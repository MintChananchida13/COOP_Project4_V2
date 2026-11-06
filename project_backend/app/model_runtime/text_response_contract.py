"""DET/REC response normalization only; no model/version routing or inference."""
import math
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
