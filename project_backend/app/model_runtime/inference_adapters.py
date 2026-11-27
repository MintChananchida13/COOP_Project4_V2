"""Backend inference response adapters. No HTTP, model loading or business scoring."""
import math
import json
import re
from numbers import Real
from typing import Any


class TextResponseContractError(ValueError):
    pass


# Payload containers confirmed by the existing adapters and Gateway contracts.
# Deliberately do not recurse through meta, model_selection, timing or input images.
_WRAPPERS = ('data', 'result', 'results', 'raw_output', 'predictions', 'raw_predictions', 'res')


def _mapping(value):
    if isinstance(value, dict):
        return value
    result = getattr(value, 'json', None)
    if callable(result):
        result = result()
    return result if isinstance(result, dict) else None


def _sequence(value):
    if hasattr(value, 'tolist'):
        value = value.tolist()
    return value if isinstance(value, (list, tuple)) else None


def _number(value, path):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise TextResponseContractError(f'{path}: expected a finite numeric score/coordinate')
    return float(value)


def _model(node, inherited):
    selection = node.get('model_selection')
    selected = selection.get('model_name') if isinstance(selection, dict) else None
    return selected or node.get('model') or inherited


def _payload(node, path):
    if node.get('success') is False or node.get('error'):
        raise TextResponseContractError(f'{path}: model reported an unsuccessful result')
    keys = [key for key in _WRAPPERS if key in node and node[key] is not None]
    if len(keys) != 1:
        reason = 'ambiguous payload containers' if keys else 'no recognized DET/REC result fields'
        raise TextResponseContractError(f'{path}: {reason}')
    key = keys[0]
    return node[key], f'{path}.{key}'


def _is_leaf(node, kind):
    if kind == 'det':
        return 'dt_polys' in node or 'dt_scores' in node
    return 'rec_text' in node or 'rec_score' in node


def _leaf(node, kind, path, model):
    if node.get('success') is False or node.get('error'):
        raise TextResponseContractError(f'{path}: model reported an unsuccessful result')
    if kind == 'det':
        polygons, scores = _sequence(node.get('dt_polys')), _sequence(node.get('dt_scores'))
        if polygons is None or scores is None or len(polygons) != len(scores):
            raise TextResponseContractError(f'{path}: dt_polys and dt_scores must be equally sized arrays')
        normalized = []
        for index, polygon in enumerate(polygons):
            points = _sequence(polygon)
            if points is None or len(points) < 3:
                raise TextResponseContractError(f'{path}.dt_polys[{index}]: invalid polygon')
            converted = []
            for point in points:
                coordinates = _sequence(point)
                if coordinates is None or len(coordinates) != 2:
                    raise TextResponseContractError(f'{path}.dt_polys[{index}]: invalid point')
                converted.append([_number(v, f'{path}.dt_polys[{index}]') for v in coordinates])
            normalized.append(converted)
        result = {'dt_polys': normalized,
                  'dt_scores': [_number(score, f'{path}.dt_scores') for score in scores]}
    else:
        text_key = 'rec_text' if 'rec_text' in node else next(
            (key for key in ('text', 'label') if key in node), None)
        score_key = 'rec_score' if 'rec_score' in node else next(
            (key for key in ('confidence', 'score', 'prob') if key in node), None)
        if text_key is None or not isinstance(node[text_key], str) or score_key is None:
            raise TextResponseContractError(f'{path}: rec_text and rec_score are required')
        result = {'rec_text': node[text_key], 'rec_score': _number(node[score_key], f'{path}.{score_key}')}
    if model:
        result['model'] = model
    return result


def normalize_single(value: Any, kind: str, *, model=None, path='$') -> dict:
    if kind not in {'det', 'rec'}:
        raise ValueError('Only DET and REC contracts are supported')
    for _ in range(32):
        node = _mapping(value)
        if node is not None:
            model = _model(node, model)
            if _is_leaf(node, kind):
                return _leaf(node, kind, path, model)
            # Preserve the old REC adapter's explicit text/confidence aliases,
            # but never collect text/score from unrelated nested metadata.
            if kind == 'rec' and not any(key in node for key in _WRAPPERS):
                text_key = next((key for key in ('text', 'label') if isinstance(node.get(key), str)), None)
                score_key = next((key for key in ('confidence', 'score', 'prob') if key in node), None)
                if text_key and score_key:
                    return _leaf({'rec_text': node[text_key], 'rec_score': node[score_key]}, kind, path, model)
            value, path = _payload(node, path)
            continue
        items = _sequence(value)
        if items is None or len(items) != 1:
            raise TextResponseContractError(f'{path}: expected exactly one image result')
        value, path = items[0], f'{path}[0]'
    raise TextResponseContractError('DET/REC response nesting exceeds the supported depth')


def normalize_batch(value: Any, kind: str, expected_count: int) -> list[dict]:
    """Preserve API list order and per-image boundaries; never pad or drop items."""
    path, model = '$', None
    for _ in range(32):
        node = _mapping(value)
        if node is not None:
            model = _model(node, model)
            # Some confirmed one-image batch responses contain a single leaf.
            if _is_leaf(node, kind):
                if expected_count != 1:
                    raise TextResponseContractError('DET/REC batch has one result for multiple input images')
                return [normalize_single(node, kind, model=model, path=path)]
            value, path = _payload(node, path)
            continue
        items = _sequence(value)
        if items is None or len(items) != expected_count:
            raise TextResponseContractError(f'{path}: batch result count does not match input image count ({expected_count})')
        return [normalize_single(item, kind, model=model, path=f'{path}[{index}]')
                for index, item in enumerate(items)]
    raise TextResponseContractError('DET/REC response nesting exceeds the supported depth')


class InferenceAdapterError(ValueError):
    pass


def adapt_typhoon(value: Any) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("results"), list) or not value["results"]:
        raise InferenceAdapterError("Invalid Typhoon OCR response")
    texts = []
    for result in value["results"]:
        if not isinstance(result, dict) or result.get("success") is not True:
            raise InferenceAdapterError("Typhoon OCR failed to process a page")
        try:
            content = result["message"]["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise InferenceAdapterError("Invalid Typhoon OCR page response") from None
        if not isinstance(content, str):
            raise InferenceAdapterError("Invalid Typhoon OCR text")
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", content.strip(), re.IGNORECASE | re.DOTALL)
        json_content = fenced.group(1) if fenced else content
        try:
            parsed = json.loads(json_content)
            if isinstance(parsed, dict) and isinstance(parsed.get("natural_text"), str):
                content = parsed["natural_text"]
            elif isinstance(parsed, list) and all(isinstance(item, dict) and isinstance(item.get("text"), str) for item in parsed):
                content = "\n".join(item["text"] for item in parsed)
        except (ValueError, TypeError):
            pass
        texts.append(content)
    return {"text": "\n".join(texts), "confidence": None}


def adapt_layout(output: Any, *, expected_count: int | None = None):
    """Validate raw Layout predictions, retaining model fields and image order."""
    if expected_count is not None:
        if not isinstance(output, list) or len(output) != expected_count:
            raise InferenceAdapterError("Layout runtime must return exactly one result per input image.")
        return [adapt_layout(item) for item in output]
    if not isinstance(output, dict) or output.get("error") is not None:
        raise InferenceAdapterError("Layout runtime returned an invalid prediction.")
    prediction = output.get("res", output)
    if not isinstance(prediction, dict) or not isinstance(prediction.get("boxes"), list):
        raise InferenceAdapterError("Layout prediction must contain a boxes list.")
    return prediction


def adapt_text_detection(output: Any, *, expected_count: int | None = None):
    return normalize_single(output, 'det') if expected_count is None else normalize_batch(output, 'det', expected_count)


def count_detected_text_boxes(output: Any, *, remaining_depth: int = 32) -> int | None:
    """Count DET polygons without copying coordinates or reading unrelated boxes."""
    if remaining_depth < 1:
        return None
    if isinstance(output, dict):
        if output.get("error") or output.get("success") is False:
            return None
        polygons = output.get("dt_polys")
        if isinstance(polygons, list):
            return len(polygons)
        payloads = [output[key] for key in _WRAPPERS if output.get(key) is not None]
        if len(payloads) == 1:
            return count_detected_text_boxes(payloads[0], remaining_depth=remaining_depth - 1)
    elif isinstance(output, list) and output:
        counts = [count_detected_text_boxes(item, remaining_depth=remaining_depth - 1) for item in output]
        if all(count is not None for count in counts):
            return sum(counts)
    return None


def adapt_text_recognition(output: Any, *, expected_count: int | None = None):
    return normalize_single(output, 'rec') if expected_count is None else normalize_batch(output, 'rec', expected_count)


def adapt_table(output: Any, *, expected_count: int | None = None) -> Any:
    """Unwrap the current table transport without changing table contents."""
    if expected_count is not None:
        if (not isinstance(output, list) or len(output) != expected_count
                or any(not isinstance(item, dict) or item.get("error") is not None for item in output)):
            raise InferenceAdapterError("Table result count or prediction is invalid")
        for item in output:
            prediction = item.get("res", item)
            if not isinstance(prediction, dict) or prediction.get("error") is not None:
                raise InferenceAdapterError("Invalid Table prediction")
        return output
    for _ in range(32):
        if not isinstance(output, dict):
            return output
        if output.get("error") is not None or output.get("success") is False:
            raise InferenceAdapterError("Table runtime returned an unsuccessful response.")
        if isinstance(output.get("data"), dict):
            output = output["data"]
            continue
        for key in ("raw_output", "output", "result"):
            if key in output:
                return output[key]
        return output
    raise InferenceAdapterError("Table response nesting exceeds the supported depth")


def adapt_table_structure(output: Any) -> Any:
    return adapt_table(output)


def number(value: Any, field: str, low=None, high=None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    try:
        parsed = float(value)
    except OverflowError as error:
        raise ValueError(f"{field} must be a finite number") from error
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be a finite number")
    if (low is not None and parsed < low) or (high is not None and parsed > high):
        raise ValueError(f"{field} is out of range")
    return parsed


def _text(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing/invalid {key}")
    return value


def _object(data: Any) -> dict:
    if not isinstance(data, dict) or data.get("error") is not None:
        raise ValueError("Invalid SigLIP response or upstream error")
    return data


def adapt_siglip(data: Any, categories: list[dict]) -> list[float]:
    data = _object(data)
    if data.get("categories") != [item["prompt"] for item in categories]:
        raise ValueError("SigLIP category/prompt order does not match request")
    if "logits_per_image" in data:
        logits = data["logits_per_image"]
    else:
        if data.get("contract_version") != "leaf-inference-v1" or data.get("kind") != "siglip":
            raise ValueError("Unsupported SigLIP leaf contract")
        raw = data.get("raw_output")
        if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
            raise ValueError("Expected one raw_output item per image")
        logits = raw[0].get("logits_per_image")
    if not isinstance(logits, list) or len(logits) != len(categories):
        raise ValueError("SigLIP logit/category count mismatch")
    _text(data, "device")
    return [number(value, "logits_per_image") for value in logits]



def adapt_siglip_response(output: Any, meta: Any) -> list[dict[str, Any]]:
    """Combine transport metadata with raw logits on the Backend only."""
    if not isinstance(output, list) or not output or not isinstance(meta, dict):
        raise InferenceAdapterError("Invalid SigLIP raw response")
    prompts = meta.get("categories")
    if not isinstance(prompts, list) or not prompts or any(not isinstance(p, str) or not p.strip() for p in prompts):
        raise InferenceAdapterError("Invalid SigLIP prompt metadata")
    device = _text(meta, "device")
    categories = [{"prompt": prompt} for prompt in prompts]
    result = []
    for item in output:
        if not isinstance(item, dict) or item.get("error") is not None:
            raise InferenceAdapterError("Invalid SigLIP raw prediction")
        adapted = {**item, "categories": list(prompts), "device": device,
                   "model_name": meta.get("model", ""), "model_version": meta.get("model_version", "")}
        adapted["logits_per_image"] = adapt_siglip(adapted, categories)
        result.append(adapted)
    return result


def adapt_openthai(value: Any) -> dict:
    """Validate OpenThai single-image OCR without fabricating confidence."""
    if not isinstance(value, dict) or value.get("error") or value.get("success") is False:
        raise InferenceAdapterError("Invalid OpenThai response")
    if isinstance(value.get("data"), dict):
        value = value["data"]
    text, lines = value.get("text"), value.get("lines")
    if not isinstance(text, str) or not isinstance(lines, list):
        raise InferenceAdapterError("OpenThai text and lines are required")
    scores = []
    for item in lines:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise InferenceAdapterError("Invalid OpenThai line")
        confidence = item.get("confidence")
        if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, Real) or not math.isfinite(confidence) or not 0 <= confidence <= 1):
            raise InferenceAdapterError("Invalid OpenThai line confidence")
        if confidence is not None and item["text"].strip():
            scores.append(float(confidence))
    confidence = value.get("confidence")
    if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, Real) or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise InferenceAdapterError("Invalid OpenThai confidence")
    return {"text": text, "confidence": float(confidence) if confidence is not None else (sum(scores) / len(scores) if scores else None)}
