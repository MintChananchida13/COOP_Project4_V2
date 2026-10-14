import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np


def save_image(path, image):
    ok, encoded = cv2.imencode('.png', image)
    if not ok:
        raise RuntimeError(f'Cannot encode image: {path}')
    encoded.tofile(str(path))


def load_pages(path, dpi):
    import fitz
    with fitz.open(path) as doc:
        for i, page in enumerate(doc, 1):
            pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False, colorspace=fitz.csRGB)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
            yield i, cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def side_support(edges, x, y, w, h, band):
    height, width = edges.shape
    x0, x1 = max(0, x), min(width, x + w)
    y0, y1 = max(0, y), min(height, y + h)
    def horizontal(yy):
        region = edges[max(0, yy-band):min(height, yy+band+1), x0:x1]
        return float(np.mean(np.any(region > 0, axis=0))) if region.size else 0.0
    def vertical(xx):
        region = edges[y0:y1, max(0, xx-band):min(width, xx+band+1)]
        return float(np.mean(np.any(region > 0, axis=1))) if region.size else 0.0
    return [horizontal(y0), horizontal(y1-1), vertical(x0), vertical(x1-1)]


def detect(img, cfg):
    started = time.perf_counter()
    H, W = img.shape[:2]
    scale = min(1.0, cfg['max_detection_dim'] / max(H, W))
    dw, dh = max(1, round(W * scale)), max(1, round(H * scale))
    small = cv2.resize(img, (dw, dh), interpolation=cv2.INTER_AREA) if scale < 1 else img
    sy, sx = H / dh, W / dw
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, cfg['canny_low'], cfg['canny_high'])
    candidates = []
    seen = set()
    kernels_tried = []
    early_stopped = False
    for kernel_size in cfg['kernel_sizes']:
        kernels_tried.append(kernel_size)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
        connected = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=cfg['close_iterations'])
        contours, _ = cv2.findContours(connected, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        kernel_accepted = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if w < 25 or h < 15:
                continue
            aspect = w / h
            area_ratio = (w * h) / (dw * dh)
            # Filter tiny text contours before expensive validation.
            if area_ratio < cfg['min_area'] * 0.5 or area_ratio > min(0.95, cfg['max_area'] * 1.4):
                continue
            if aspect < cfg['min_aspect'] * 0.75 or aspect > cfg['max_aspect'] * 1.25:
                continue
            key = (round(x/5), round(y/5), round(w/5), round(h/5))
            if key in seen:
                continue
            seen.add(key)
            area = cv2.contourArea(cnt)
            if area <= 0:
                continue
            perimeter = cv2.arcLength(cnt, True)
            polygon = cv2.approxPolyDP(cnt, 0.025 * perimeter, True)
            rectangularity = min(1.0, area / (w * h))
            sides = side_support(edges, x, y, w, h, max(2, round(min(dw, dh) * 0.004)))
            edge_support = sum(sides) / 4
            strong_sides = sum(s >= cfg['strong_side_threshold'] for s in sides)
            reasons = []
            if not cfg['min_aspect'] <= aspect <= cfg['max_aspect']:
                reasons.append('aspect_ratio')
            if not cfg['min_area'] <= area_ratio <= cfg['max_area']:
                reasons.append('area_ratio')
            if rectangularity < cfg['min_rectangularity']:
                reasons.append('rectangularity')
            if edge_support < cfg['min_edge_support']:
                reasons.append('edge_support')
            if strong_sides < cfg['min_strong_sides']:
                reasons.append('strong_edge_sides')
            if len(polygon) != 4:
                reasons.append('not_quadrilateral')
            score = 0.5 * edge_support + 0.3 * rectangularity + 0.2 * strong_sides / 4
            ox, oy = round(x*sx), round(y*sy)
            ow, oh = min(W-round(x*sx), round((x+w)*sx)-ox), min(H-round(y*sy), round((y+h)*sy)-oy)
            candidate = {
                'bbox': [ox, oy, ow, oh],
                'aspect_ratio': round(aspect, 4), 'area_ratio': round(area_ratio, 4),
                'rectangularity': round(rectangularity, 4), 'edge_support': round(edge_support, 4),
                'edge_side_support': [round(v, 4) for v in sides],
                'strong_edge_side_count': strong_sides, 'polygon_vertices': len(polygon),
                'kernel_size': kernel_size, 'score': round(score, 4),
                'accepted': not reasons, 'rejection_reasons': reasons
            }
            candidates.append(candidate)
            if candidate['accepted']:
                kernel_accepted.append(candidate)
        # Optional early exit: only stop for a high-confidence candidate.
        # Disabled by default to preserve best-of-all-kernels behavior.
        if cfg['early_exit'] and any(
            c['edge_support'] >= cfg['early_exit_edge']
            and c['rectangularity'] >= cfg['early_exit_rectangularity']
            and c['strong_edge_side_count'] == 4
            for c in kernel_accepted
        ):
            early_stopped = True
            break
    candidates.sort(key=lambda c: (c['accepted'], c['score'], c['area_ratio']), reverse=True)
    selected = next((c for c in candidates if c['accepted']), None)
    return selected, candidates[:cfg['debug_candidates']], {
        'original_size': [W, H], 'detection_size': [dw, dh],
        'scale_x': round(sx, 6), 'scale_y': round(sy, 6),
        'candidate_count': len(candidates),
        'kernels_tried': kernels_tried, 'early_stopped': early_stopped,
        'card_detection_ms': round((time.perf_counter() - started) * 1000, 2),
    }


def process(path, output, dpi, cfg):
    output.mkdir(parents=True, exist_ok=True)
    for page_num, img in load_pages(path, dpi):
        page_started = time.perf_counter()
        selected, candidates, meta = detect(img, cfg)
        H, W = img.shape[:2]
        overlay = img.copy()
        for c in reversed(candidates):
            x, y, w, h = c['bbox']
            color = (0, 180, 0) if c['accepted'] else (0, 0, 220)
            cv2.rectangle(overlay, (x, y), (x+w, y+h), color, max(2, round(W/1200)))
        crop_box = None
        if selected:
            x, y, w, h = selected['bbox']
            px, py = round(w * cfg['padding']), round(h * cfg['padding'])
            x0, y0 = max(0, x-px), max(0, y-py)
            x1, y1 = min(W, x+w+px), min(H, y+h+py)
            result = img[y0:y1, x0:x1]
            crop_box = [x0, y0, x1-x0, y1-y0]
            source = 'physical_card_crop'
        else:
            result = img
            source = 'full_page'
        stem = f'{path.stem}_page_{page_num:03d}'
        save_image(output / f'{stem}_result.png', result)
        save_image(output / f'{stem}_overlay.png', overlay)
        debug = {
            'input': str(path), 'page': page_num, 'page_size': [W, H],
            'final_matching_path_source': source, 'padded_crop_box': crop_box,
            'selected_candidate': selected,
            'crop_rejection_reason': None if selected else 'no_candidate_passed_guards',
            'detection': meta, 'top_candidates': candidates, 'thresholds': cfg,
        }
        (output / f'{stem}_debug.json').write_text(json.dumps(debug, indent=2, ensure_ascii=False), encoding='utf-8')
        print(f'Page {page_num}: {source}; crop={crop_box}; candidates={meta["candidate_count"]}; kernels={meta["kernels_tried"]}; detection_ms={meta["card_detection_ms"]}; total_ms={round((time.perf_counter()-page_started)*1000,2)}')


def main():
    parser = argparse.ArgumentParser(description='Standalone PDF physical card crop tester')
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path, default=Path('crop_output'))
    parser.add_argument('--dpi', type=int, default=200)
    parser.add_argument('--max-detection-dim', type=int, default=1600)
    parser.add_argument('--kernel-sizes', type=int, nargs='+', default=[5, 9, 15])
    parser.add_argument('--close-iterations', type=int, default=2)
    parser.add_argument('--canny-low', type=int, default=35)
    parser.add_argument('--canny-high', type=int, default=110)
    parser.add_argument('--min-aspect', type=float, default=1.35)
    parser.add_argument('--max-aspect', type=float, default=1.85)
    parser.add_argument('--min-area', type=float, default=0.05)
    parser.add_argument('--max-area', type=float, default=0.50)
    parser.add_argument('--min-rectangularity', type=float, default=0.76)
    parser.add_argument('--min-edge-support', type=float, default=0.66)
    parser.add_argument('--min-strong-sides', type=int, default=3)
    parser.add_argument('--strong-side-threshold', type=float, default=0.55)
    parser.add_argument('--padding', type=float, default=0)
    parser.add_argument('--early-exit', action='store_true', help='Stop after a kernel yields a high-confidence accepted card')
    parser.add_argument('--early-exit-edge', type=float, default=0.90)
    parser.add_argument('--early-exit-rectangularity', type=float, default=0.85)
    parser.add_argument('--debug-candidates', type=int, default=30)
    args = parser.parse_args()
    if args.input.suffix.lower() != '.pdf':
        parser.error('Only PDF files are supported')
    if not args.input.is_file():
        parser.error(f'Input file not found: {args.input}')
    if args.max_detection_dim < 100 or any(k < 1 or k % 2 == 0 for k in args.kernel_sizes):
        parser.error('max-detection-dim must be >= 100; kernel sizes must be positive odd integers')
    cfg = {k: getattr(args, k) for k in (
        'max_detection_dim', 'kernel_sizes', 'close_iterations', 'canny_low', 'canny_high',
        'min_aspect', 'max_aspect', 'min_area', 'max_area', 'min_rectangularity',
        'min_edge_support', 'min_strong_sides', 'strong_side_threshold', 'padding', 'debug_candidates',
        'early_exit', 'early_exit_edge', 'early_exit_rectangularity'
    )}
    process(args.input, args.output, args.dpi, cfg)


if __name__ == '__main__':
    main()
