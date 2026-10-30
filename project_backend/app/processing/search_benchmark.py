"""Opt-in request-local Template Search measurements; never runs extra models."""
from contextvars import ContextVar
from functools import wraps
from typing import Any
import threading
import time
from collections import OrderedDict

_capture: ContextVar[dict | None] = ContextVar("search_benchmark", default=None)
_spans: OrderedDict[str, dict] = OrderedDict()
_spans_lock = threading.Lock()


def record_span(trace_id: str, kind: str, started: float, ended: float):
    if ended < started:
        return
    with _spans_lock:
        state = _spans.setdefault(trace_id, {"search": [], "post": [], "updated": 0.0})
        state[kind].append((started, ended))
        state["updated"] = time.monotonic()
        _spans.move_to_end(trace_id)
        while len(_spans) > 256:
            _spans.popitem(last=False)


def span_metrics(trace_id: str, expected_post_requests: int):
    with _spans_lock:
        state = _spans.get(trace_id)
        if not state:
            return {"trace_id": trace_id, "backend_search_ms": None, "backend_post_search_ms": None,
                    "backend_total_active_ms": None, "post_requests_observed": 0}
        search = list(state["search"])
        post = list(state["post"])

    def union(intervals):
        merged = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return round(sum(end - start for start, end in merged) * 1000, 2)

    complete = len(post) == expected_post_requests and len(search) == 1
    return {"trace_id": trace_id, "backend_search_ms": union(search) if len(search) == 1 else None,
            "backend_post_search_ms": union(post) if complete else None,
            "backend_total_active_ms": union(search + post) if complete else None,
            "post_requests_observed": len(post), "post_requests_expected": expected_post_requests}


def begin(trace_id: str):
    state = {"trace_id": trace_id, "model_calls": [], "timing": {}}
    return state, _capture.set(state)


def end(token):
    _capture.reset(token)


def capture_result(result: dict):
    state = _capture.get()
    if state is not None:
        debug = result.get("debug") or {}
        state["timing"] = debug.get("timing") or {}
        state["mode"] = debug.get("verification_strategy")


def record_result_processing(elapsed_ms: float):
    state = _capture.get()
    if state is not None:
        state["result_processing_ms"] = round(elapsed_ms, 2)


def instrument_runtime(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        state = _capture.get()
        if state is None:
            return function(*args, **kwargs)
        collector = kwargs.get("timing")
        if not isinstance(collector, dict):
            collector = {}
            kwargs["timing"] = collector
        try:
            return function(*args, **kwargs)
        finally:
            state["model_calls"].append(dict(collector))
    return wrapped


def metrics(state: dict, elapsed_ms: float) -> dict:
    from app.processing.model_timing import summarize_model_calls, duration
    calls = state["model_calls"]
    summary = summarize_model_calls(calls)
    timing = state.get("timing") or {}
    output = {"trace_id": state["trace_id"], "backend_runtime_ms": round(elapsed_ms, 2),
              "template_search_ms": timing.get("template_matching_ms"), "result_processing_ms": state.get("result_processing_ms"),
              "processing_mode": state.get("mode"), "model_calls": summary["model_calls"],
              "model_intervals_overlap": summary["model_intervals_overlap"]}
    queues = [item.get("queue_wait_ms") for item in summary["model_calls"]]
    output["model_queue_wait_ms"] = (round(sum(queues), 2) if queues and summary["model_intervals_overlap"] is False
                                      and all(duration(item) for item in queues) else None)
    if output["template_search_ms"] is None:
        breakdown = timing.get("benchmark") or {}
        output["template_search_ms"] = ((breakdown.get("steps") or {}).get("matching") or {}).get("elapsed_ms")
    for label, kind in (("layout", "layout"), ("text_detection", "text_detection"), ("text_recognition", "text_recognition"), ("siglip", "image_verification")):
        selected = [call for call in summary["model_calls"] if call.get("kind") == kind]
        shared = any(call.get("kind") != kind and isinstance(call.get("model_timings"), dict) and kind in call["model_timings"] for call in summary["model_calls"])
        for name, field in (("queue_wait", "queue_wait_ms"), ("inference", "inference_ms"), ("total", "service_total_runtime_ms")):
            values = [call.get(field) for call in selected]
            output[f"{label}_{name}_ms"] = round(sum(values), 2) if not shared and values and all(duration(value) for value in values) else None
        if kind in summary["inferences"]:
            output[f"{label}_inference_ms"] = summary["inferences"][kind]
    return output
