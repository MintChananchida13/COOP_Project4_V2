# Table Recognition V2 memory investigation

## Evidence and scope

The reported failing document is `THAI_TABLE_081413.png`: 1998 x 546 RGBA,
112618 file bytes, 3272724 bytes for one decoded RGB/BGR pixel buffer. This
investigation does not attribute its OOM to a response size from another document.
The real Gateway response for this failing job and Render RSS logs were not
available. Consequently the exact allocation that crossed the Render limit is
not yet established, and the change does not claim every workload fits 512 MB.

Confirmed code-level memory problems:

1. `_collect_dicts` used a recursive closure that referenced itself and the
   result inventory. This cycle retained the entire raw tree after ordinary
   references were released, until cyclic GC. A weak-reference regression with
   cyclic GC disabled reproduced retention before the change and immediate
   release after breaking the closure cycle. No forced GC is added to production.
2. Runtime parsing held response bytes, decoded response text and parsed objects
   simultaneously. Request body/Request references also remained until return.
   Now the unneeded bytes/request references are dropped before parsing, and
   decoded text before diagnostics/reconstruction. JSON decoding semantics stay
   unchanged. This reduces reference lifetime; OS RSS need not fall immediately
   because Python/native allocators may keep freed pages.
3. Selected-ROI processing kept the PIL image after creating its independent BGR
   array, plus references to all prepared text crops/results during Table work.
   PIL is closed after decode; processed crop and OCR-result references are
   released as work completes. No resizing, precision changes or ROI changes.
4. FastAPI BackgroundTasks allowed heavy OCR jobs to overlap without a heavy-work
   limit. Jobs now use one dedicated heavy OCR worker per process, acquired before
   database request decoding/image decoding. Async submission still persists the
   job first and queues only its ID, allowing its HTTP request/image body to be
   released without waiting for heavy work. Nested calls are reentrant and request
   ContextVars propagate. Shutdown waits for queued work before closing DB pools.

The model, Table reconstruction decisions, embedded rules and response schema
are unchanged. No runtime calls or result caches are added. Concurrency changes
queue time/throughput; the public job status/polling protocol stays the same.

## Memory diagnostics

`OCR_MEMORY_DIAGNOSTICS=true` is the default (set false to disable logging and
sampling, without disabling the memory fixes or worker limit).

Backend stderr emits `OCR_MEMORY` records at startup before/after warmup, job
load, image decode/release, Gateway image encoding, response read/decode, JSON
parse/release, output scan, structure rules, reconstruction and result storage.
Only fixed stage names, PID, counts and memory measurements are logged; no image,
OCR text, JSON body or credentials. No new response fields are added.

- `rss_bytes`: current process resident memory.
- `process_peak_rss_bytes`: OS lifetime peak RSS/working set; not a per-stage peak.
- `sampled_peak_rss_bytes`: maximum observed RSS during this job.
- `stage_sampled_peak_rss_bytes`: maximum observed RSS while each stage was
  active. Samples occur every 100 ms and at boundaries; short spikes may be missed.
- Linux cgroup v2 `cgroup_current_bytes`, `cgroup_peak_bytes`, `cgroup_limit_bytes`:
  container measurements when available, including other processes. They are not
  sums of stage measurements; unavailable values stay null.
- `OCR_MEMORY gate ... wait_ms`: Backend worker queue wait, NOT Gateway queue or
  model inference time.

Linux uses `/proc/self/status` and cgroup metadata; Windows probes use OS process
counters. No tracemalloc, full heap dumps, document logging or extra dependencies.
There is one short-lived sampling thread only during active heavy work.

Look first at `startup_after_warmup` to establish Render baseline with its actual
dependencies, template caches and DB pool. Compare cgroup/current and process RSS
at each failing stage. Record PID: the worker limit is per process, so multiple
Uvicorn workers have separate baselines and queues. Verify the Render Start
Command uses one web worker for the 512 MB service. This change does not modify
Render settings, instance RAM or plan. Template search/other HTTP work remains
independent; total container memory still needs measurement.

## Before/after measurements

Fresh Windows processes, three repetitions, same application imports, actual
`THAI_TABLE_081413.png`, and a synthetic 5282831-byte Table response containing
merged-cell metadata and numeric test data. The response is a test fixture, not
the real Gateway output for this image. Startup cache/DB warmup and real model
inference are excluded. No real Model API calls were made.

| Median MiB | Before | After |
| --- | ---: | ---: |
| Framework/import baseline, one job | 73.09 | 73.30 |
| Peak RSS, one job | 139.37 | 130.04 |
| RSS after one job | 125.27 | 81.30 |
| Peak RSS, two simultaneous submissions | 195.48 | 133.84 |

Before: two jobs may run concurrently. After: they queue on one worker. Both
complete with the same number of mocked Runtime calls (one per document).
Gateway request-body hashes and OCR/table response hashes (excluding timings)
matched for every repetition. This verifies pixels/model input and resulting
text, structure, cells and audit remain unchanged in the controlled test.

Artifacts: `benchmark/output/table_memory_comparison.json` and per-run JSONs.
`benchmark/table_memory_probe.py` runs each mode in a fresh process; its
`--baseline-dir` contains the source snapshot taken before this change. That
snapshot is an ignored local test artifact, not production code.

```powershell
python benchmark/table_memory_probe.py --prepare
python benchmark/table_memory_probe.py --mode before --run-id 1
python benchmark/table_memory_probe.py --mode after --run-id 1
python benchmark/table_memory_probe.py --mode before --jobs 2 --run-id 1
python benchmark/table_memory_probe.py --mode after --jobs 2 --run-id 1
```

For the live conclusion, deploy Backend changes and rerun this exact document.
Use the new Render stage/cgroup logs to establish real baseline/peak and confirm
whether the OOM remains. Do not extrapolate the synthetic result into a promised
Render peak or infer unavailable response/queue/inference measurements.
