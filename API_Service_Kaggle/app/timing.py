def build_timing(name, queue_wait_ms, model_load_ms, lock_wait_ms, inference_ms, serialization_ms, total_runtime_ms, model_timings=None):
    timing = {
        "queue_wait_ms": queue_wait_ms,
        "model_load_ms": model_load_ms,
        "inference_lock_wait_ms": lock_wait_ms,
        "inference_ms": inference_ms,
        "serialization_ms": serialization_ms,
        "total_runtime_ms": total_runtime_ms,
    }
    timing[{
        "layout": "layout_runtime_ms",
        "det": "text_det_inference_ms",
        "rec": "text_rec_inference_ms",
        "table": "table_runtime_ms",
        "siglip": "image_siglip_runtime_ms",
    }[name]] = inference_ms
    if model_timings:
        timing["model_timings"] = model_timings
        if name == "layout":
            timing["layout_runtime_ms"] = model_timings["layout"]["inference_ms"]
    if name == "layout":
        timing["inference_including_load_and_adaptation"] = round(
            model_load_ms + lock_wait_ms + inference_ms + serialization_ms, 2
        )
    return timing
