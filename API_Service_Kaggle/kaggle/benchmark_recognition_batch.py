"""Compare single-image and batched recognition on the same local image crops."""

import argparse
import json
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.models import manager, resolve_ocr
from app.serialization import json_safe


def result_summary(item):
    result = item.get("res", item)
    return {"text": result.get("rec_text"), "score": result.get("rec_score")}


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", help="Paths to text ROI images")
    parser.add_argument("--version", default="v5")
    parser.add_argument("--model-id", default="default")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if args.batch_size < 1 or args.repeat < 1:
        parser.error("batch size and repeat must be positive")
    images = []
    for path in args.images:
        image = cv2.imread(path)
        if image is None:
            parser.error(f"Unable to read image: {path}")
        images.append(image)
    entry = resolve_ocr("rec", args.version, args.model_id)
    model, load_ms = manager.get_ocr("rec", entry)
    list(model.predict(images[0]))  # warm up before measuring
    singles_ms, batches_ms = [], []
    for _ in range(args.repeat):
        started = time.perf_counter()
        singles = [json_safe(list(model.predict(image)))[0] for image in images]
        singles_ms.append((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        batches = []
        for index in range(0, len(images), args.batch_size):
            chunk = images[index:index + args.batch_size]
            batches.extend(json_safe(list(model.predict(chunk, batch_size=len(chunk)))))
        batches_ms.append((time.perf_counter() - started) * 1000)
        if len(batches) != len(images):
            raise RuntimeError("Batch result count differs from input count")
    comparisons = []
    for path, single, batch in zip(args.images, singles, batches):
        before, after = result_summary(single), result_summary(batch)
        comparisons.append({"image": path, "same_text": before["text"] == after["text"],
                            "single_score": before["score"], "batch_score": after["score"]})
    print(json.dumps({"model": entry["model_name"], "model_id": args.model_id, "model_load_ms": load_ms,
                      "batch_size": args.batch_size, "single_ms": singles_ms, "batch_ms": batches_ms,
                      "comparisons": comparisons}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run()
