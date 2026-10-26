from typing import Any
import math


def duration(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


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
            if duration(value):
                values.append(float(value))
    return round(sum(values), 2) if values else None


def backend_runtime_from_union(total_ms: float, union_ms: float | None, overlap: bool | None) -> float | None:
    if not duration(total_ms) or not duration(union_ms) or overlap is None or union_ms > total_ms:
        return None
    return round(total_ms - union_ms, 2)


def summarize_model_calls(value: Any) -> dict[str, Any]:
    calls: dict[str, dict[str, Any]] = {}

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            call_id = item.get("model_call_id") or item.get("call_id")
            if isinstance(call_id, str) and call_id:
                normalized = dict(item)
                normalized["model_kind"] = item.get("model_kind") or item.get("kind")
                if "gateway_timing" not in normalized:
                    normalized["gateway_timing"] = {key: item.get(key) for key in (
                        "model_load_ms", "queue_wait_ms", "inference_lock_wait_ms", "inference_ms",
                        "serialization_ms", "model_timings",
                    )}
                    normalized["gateway_timing"]["total_runtime_ms"] = item.get("service_total_runtime_ms")
                previous = calls.setdefault(call_id, {})
                for key, field in normalized.items():
                    if key == "gateway_timing" and isinstance(field, dict):
                        merged = dict(previous.get(key) or {})
                        for timing_key, timing_value in field.items():
                            if merged.get(timing_key) is None:
                                merged[timing_key] = timing_value
                            elif timing_value is not None and merged[timing_key] != timing_value:
                                previous["conflicting_record"] = True
                        previous[key] = merged
                    elif previous.get(key) is None:
                        previous[key] = field
                    elif key in ("model_kind", "model_call_start_monotonic", "model_call_end_monotonic") and field is not None and previous[key] != field:
                        previous["conflicting_record"] = True
            for nested in item.values():
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    waits: dict[str, float] = {}
    inferences: dict[str, float] = {}
    missing_inferences: set[str] = set()
    missing_waits: set[str] = set()
    intervals: list[tuple[float, float]] = []
    incomplete = False
    for call in calls.values():
        kind = str(call.get("model_kind") or "")
        gateway = call.get("gateway_timing") or {}
        components = gateway.get("model_timings")
        measured = components if isinstance(components, dict) and components else {kind: gateway}
        for component_kind, component in measured.items():
            inference = component.get("inference_ms") if isinstance(component, dict) else None
            aliases = {"layout": "layout_runtime_ms", "text_detection": "text_det_inference_ms",
                       "text_recognition": "text_rec_inference_ms", "table": "table_runtime_ms",
                       "image_verification": "image_siglip_runtime_ms"}
            if isinstance(component, dict) and "inference_ms" not in component:
                inference = component.get(aliases.get(component_kind, ""))
                if component_kind == "image_verification" and "image_siglip_runtime_ms" not in component:
                    inference = component.get("siglip_inference_ms")
            if duration(inference) and not call.get("conflicting_record"):
                inferences[component_kind] = inferences.get(component_kind, 0.0) + float(inference)
            else:
                missing_inferences.add(component_kind)
            if component_kind != kind:
                missing_waits.add(component_kind)
        wait = call.get("http_request_ms")
        start = call.get("model_call_start_monotonic")
        end = call.get("model_call_end_monotonic")
        if duration(wait) and not call.get("conflicting_record"):
            waits[kind] = waits.get(kind, 0.0) + float(wait)
        else:
            missing_waits.add(kind)
        if all(duration(point) for point in (start, end)) and end >= start and not call.get("conflicting_record"):
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
    if reason is None and missing_inferences:
        reason = "incomplete_model_inference_measurements"
    if reason is None and missing_waits:
        reason = "incomplete_or_shared_model_wait_measurements"
    return {
        "waits": {kind: None if kind in missing_waits else round(waits[kind], 2)
                  for kind in waits.keys() | missing_waits},
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
                "endpoint": call.get("endpoint") or call.get("endpoint_path"),
                "model_call_start_monotonic": call.get("model_call_start_monotonic"),
                "model_call_end_monotonic": call.get("model_call_end_monotonic"),
                "model_timings": (call.get("gateway_timing") or {}).get("model_timings"),
                "conflicting_record": call.get("conflicting_record", False),
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
