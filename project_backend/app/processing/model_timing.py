from typing import Any
import math


def layout_runtime_from_breakdown(breakdown: Any) -> float | None:
    if not isinstance(breakdown, list):
        return None
    values = []
    for item in breakdown:
        if not isinstance(item, dict):
            continue
        analysis = item.get("analyze_layout_signature")
        if isinstance(analysis, dict):
            value = analysis.get("layout_runtime_ms")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(float(value))
    return round(sum(values), 2) if values else None


def backend_runtime_from_union(total_ms: float, union_ms: float | None, overlap: bool | None) -> float | None:
    if union_ms is None or overlap is not False or union_ms > total_ms:
        return None
    return round(total_ms - union_ms, 2)


def summarize_model_calls(value: Any) -> dict[str, Any]:
    calls: dict[str, dict[str, Any]] = {}

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            call_id = item.get("model_call_id")
            if isinstance(call_id, str) and call_id:
                if call_id not in calls or (
                    "model_call_start_monotonic" not in calls[call_id]
                    and "model_call_start_monotonic" in item
                ):
                    calls[call_id] = item
            for nested in item.values():
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    waits: dict[str, float] = {}
    inferences: dict[str, float] = {}
    missing_inferences: set[str] = set()
    intervals: list[tuple[float, float]] = []
    incomplete = False
    for call in calls.values():
        kind = str(call.get("model_kind") or "")
        inference = (call.get("gateway_timing") or {}).get("inference_ms")
        if isinstance(inference, (int, float)) and not isinstance(inference, bool) and math.isfinite(inference) and inference >= 0:
            inferences[kind] = inferences.get(kind, 0.0) + float(inference)
        else:
            missing_inferences.add(kind)
        wait = call.get("http_request_ms")
        start = call.get("model_call_start_monotonic")
        end = call.get("model_call_end_monotonic")
        if isinstance(wait, (int, float)) and not isinstance(wait, bool):
            waits[kind] = waits.get(kind, 0.0) + float(wait)
        if all(isinstance(point, (int, float)) and not isinstance(point, bool) for point in (start, end)) and end >= start:
            intervals.append((float(start), float(end)))
        else:
            incomplete = True

    union_ms = None
    wall_ms = None
    overlap = None
    reason = None
    if incomplete or not intervals:
        reason = "incomplete_model_call_intervals" if incomplete else "no_model_call_intervals"
    else:
        merged: list[tuple[float, float]] = []
        overlap = False
        for start, end in sorted(intervals):
            if merged and start < merged[-1][1]:
                overlap = True
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        union_ms = round(sum(end - start for start, end in merged) * 1000, 2)
        wall_ms = round((max(end for _, end in intervals) - min(start for start, _ in intervals)) * 1000, 2)
    return {
        "waits": {kind: round(wait, 2) for kind, wait in waits.items()},
        "inferences": {kind: None if kind in missing_inferences else round(inferences[kind], 2)
                       for kind in inferences.keys() | missing_inferences},
        "model_wait_union_ms": union_ms,
        "model_wall_clock_ms": wall_ms,
        "model_intervals_overlap": overlap,
        "model_timing_missing_reason": reason,
        "model_call_count": len(calls),
        "model_calls": [
            {
                "call_id": call_id,
                "kind": call.get("model_kind"),
                "http_request_ms": call.get("http_request_ms"),
                "model_load_ms": (call.get("gateway_timing") or {}).get("model_load_ms"),
                "queue_wait_ms": (call.get("gateway_timing") or {}).get("queue_wait_ms"),
                "inference_lock_wait_ms": (call.get("gateway_timing") or {}).get("inference_lock_wait_ms"),
                "inference_ms": (call.get("gateway_timing") or {}).get("inference_ms"),
                "serialization_ms": (call.get("gateway_timing") or {}).get("serialization_ms"),
                "service_total_runtime_ms": (call.get("gateway_timing") or {}).get("total_runtime_ms"),
            }
            for call_id, call in calls.items()
        ],
    }
