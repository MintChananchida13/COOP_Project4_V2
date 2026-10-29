"""Standalone PDF-only card crop tester v8: skip crop for multi-page PDFs; geometry/edges only.

Conservative by design: ambiguous candidates fall back to the full page.
"""
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


SUPPORTED_EXTENSIONS = {'.pdf'}


def load_pages(path, dpi):
    import pymupdf
    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc, 1):
            pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), alpha=False, colorspace=pymupdf.csRGB)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
            yield i, cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def ruled_form_evidence(img, cfg):
    """Reject pages with a long shared outer frame and multiple full-width rules.

    No OCR/content inspection. Conservative whole-page guard for structured forms.
    """
    H, W = img.shape[:2]
    scale = min(1.0, 1200 / max(H, W))
    w, h = max(1, round(W * scale)), max(1, round(H * scale))
    small = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA) if scale < 1 else img
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, round(w * 0.20)), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, round(h * 0.20))))
    hor = cv2.morphologyEx(binary, cv2.MORPH_OPEN, hk)
    ver = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vk)
    hc, _ = cv2.findContours(hor, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    vc, _ = cv2.findContours(ver, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    horizontal = [(x, y, ww, hh) for c in hc for x, y, ww, hh in [cv2.boundingRect(c)] if ww >= cfg['form_rule_width'] * w]
    vertical = [(x, y, ww, hh) for c in vc for x, y, ww, hh in [cv2.boundingRect(c)] if hh >= cfg['form_wall_height'] * h]
    # Require two separated long walls, plus several wide horizontal rules in their span.
    walls = sorted(vertical, key=lambda b: b[0])
    frame = None
    for i, a in enumerate(walls):
        for b in walls[i+1:]:
            if b[0] - a[0] < cfg['form_wall_separation'] * w:
                continue
            top = max(a[1], b[1]); bottom = min(a[1]+a[3], b[1]+b[3])
            if bottom - top < cfg['form_wall_overlap'] * h:
                continue
            frame = (a, b, top, bottom)
            break
        if frame:
            break
    count = 0
    if frame:
        a, b, top, bottom = frame
        ys = sorted([y for x, y, ww, hh in horizontal
                     if top-5 <= y <= bottom+5 and x <= a[0] + w*0.06 and x+ww >= b[0] - w*0.06])
        unique = []
        for y in ys:
            if not unique or y - unique[-1] > max(4, round(h * 0.01)):
                unique.append(y)
        count = len(unique)
    return {'reject': bool(frame and count >= cfg['form_min_rules']),
            'long_horizontal_rules': len(horizontal), 'long_vertical_walls': len(vertical),
            'rules_between_shared_walls': count, 'frame_found': bool(frame)}


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


def hough_boxes(edges, cfg):
    """Find axis-aligned boxes from faint/broken Hough segments.

    Requires two distinct horizontal and two distinct vertical line positions;
    this is safer than inventing missing borders from only two lines.
    """
    H, W = edges.shape
    minimum = max(15, round(min(H, W) * cfg['hough_min_line_fraction']))
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=cfg['hough_votes'],
                            minLineLength=minimum, maxLineGap=max(5, round(min(H, W) * 0.025)))
    if lines is None:
        return []
    horizontal, vertical = [], []
    # OpenCV may return (N,1,4), (N,4), or (4,) depending on build.
    segments = np.asarray(lines).reshape(-1, 4)
    for line in segments:
        x1, y1, x2, y2 = map(int, line)
        dx, dy = x2-x1, y2-y1
        if abs(dy) <= max(3, abs(dx)*0.09) and abs(dx) >= minimum:
            horizontal.append((round((y1+y2)/2), min(x1,x2), max(x1,x2)))
        elif abs(dx) <= max(3, abs(dy)*0.09) and abs(dy) >= minimum:
            vertical.append((round((x1+x2)/2), min(y1,y2), max(y1,y2)))

    def cluster(segments, tolerance):
        groups = []
        for item in sorted(segments):
            if groups and abs(item[0] - groups[-1][-1][0]) <= tolerance:
                groups[-1].append(item)
            else:
                groups.append([item])
        out = []
        for group in groups:
            positions = [v[0] for v in group]
            out.append((int(np.median(positions)), min(v[1] for v in group),
                        max(v[2] for v in group)))
        return sorted(out, key=lambda v: v[2]-v[1], reverse=True)[:cfg['hough_max_lines']]

    hs = cluster(horizontal, max(3, round(H*0.006)))
    vs = cluster(vertical, max(3, round(W*0.006)))
    boxes = []
    for i, a in enumerate(hs):
        for b in hs[i+1:]:
            top, bottom = sorted((a[0], b[0]))
            if bottom-top < 15:
                continue
            for j, c in enumerate(vs):
                for d in vs[j+1:]:
                    left, right = sorted((c[0], d[0]))
                    if right-left < 25:
                        continue
                    w, h = right-left, bottom-top
                    ratio = w/h
                    area = w*h/(W*H)
                    if not (cfg['min_area'] <= area <= cfg['max_area']):
                        continue
                    if not (cfg['min_aspect'] <= ratio <= cfg['max_aspect']):
                        continue
                    # Each line must at least overlap the proposed rectangle.
                    overlaps = [max(0, min(right, q[2])-max(left, q[1]))/w for q in (a,b)]
                    overlaps += [max(0, min(bottom, q[2])-max(top, q[1]))/h for q in (c,d)]
                    if sum(v >= 0.18 for v in overlaps) < 3:
                        continue
                    boxes.append((left, top, w, h))
                    if len(boxes) >= cfg['hough_max_boxes']:
                        return boxes
    return boxes


def structural_frame_evidence(edges, box, cfg):
    """Geometry-only evidence that a rectangle belongs to a larger ruled form.

    Inspect extensions of its vertical borders and horizontal rules connected
    to those borders. Text/checkboxes inside the rectangle are not examined.
    """
    H, W = edges.shape
    x, y, w, h = map(int, box)
    band = max(2, round(min(H, W) * 0.004))
    reach = max(12, round(h * cfg['structure_probe_fraction']))
    def vertical_occupancy(xx, ya, yb):
        a, b = max(0, ya), min(H, yb)
        region = edges[a:b, max(0, xx-band):min(W, xx+band+1)]
        return float(np.mean(np.any(region > 0, axis=1))) if region.size else 0.0
    # Evidence of border lines continuing OUTSIDE the candidate on both sides.
    extensions = []
    for xx in (x, x+w-1):
        above = vertical_occupancy(xx, y-reach, y-band)
        below = vertical_occupancy(xx, y+h+band, y+h+reach)
        extensions.append({'above':round(above,3),'below':round(below,3)})
    long_extensions = sum(v >= cfg['structure_extension_support'] for e in extensions for v in e.values())
    # A form cell typically shares one or both vertical borders with the next cell.
    both_sides_extended = all(max(e.values()) >= cfg['structure_extension_support'] for e in extensions)
    # Horizontal divider lines at both the candidate top and bottom, plus continuing
    # vertical walls, indicate a bounded SECTION of a larger form.
    sides = side_support(edges, x, y, w, h, band)
    section = (both_sides_extended and
               sides[0] >= cfg['structure_horizontal_support'] and
               sides[1] >= cfg['structure_horizontal_support'])
    reject = (section or long_extensions >= cfg['structure_min_extensions'] or
              (w*h/(W*H) >= cfg['structure_large_area'] and
               any(v >= cfg['structure_strong_extension'] for e in extensions for v in e.values())))
    return {'reject':bool(reject), 'extended_segments':long_extensions,
            'vertical_extensions':extensions, 'section_pattern':bool(section)}



def table_structure_evidence(binary, box, cfg):
    """Detect repeated interior ruling lines and their intersections, not text content."""
    x, y, w, h = map(int, box)
    if w < 35 or h < 25:
        return {'reject': False, 'horizontal_rules': 0, 'vertical_rules': 0, 'intersections': 0}
    roi = binary[y:y+h, x:x+w]
    # Long morphology kernels suppress characters and short strokes.
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(12, round(w*cfg['table_min_rule_fraction'])), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(12, round(h*cfg['table_min_rule_fraction']))))
    hor = cv2.morphologyEx(roi, cv2.MORPH_OPEN, hk)
    ver = cv2.morphologyEx(roi, cv2.MORPH_OPEN, vk)
    margin_y = max(3, round(h*0.04))
    margin_x = max(3, round(w*0.04))
    def bands(mask, axis, margin):
        values = np.count_nonzero(mask, axis=axis)
        length = mask.shape[1-axis]
        indices = np.where(values >= length*cfg['table_min_rule_fraction'])[0]
        indices = [int(i) for i in indices if margin < i < len(values)-margin]
        groups=[]
        for i in indices:
            if not groups or i-groups[-1][-1] > 3:
                groups.append([i])
            else:
                groups[-1].append(i)
        return [round(float(np.median(g))) for g in groups]
    hs = bands(hor, 1, margin_y)
    vs = bands(ver, 0, margin_x)
    intersections = 0
    for yy in hs:
        for xx in vs:
            patch_h = hor[max(0,yy-3):min(h,yy+4),max(0,xx-3):min(w,xx+4)]
            patch_v = ver[max(0,yy-3):min(h,yy+4),max(0,xx-3):min(w,xx+4)]
            if np.any(patch_h) and np.any(patch_v):
                intersections += 1
    # Require a repeated grid, not just a few passport security/MRZ rules.
    # The whole-page parent-grid detector remains a separate safeguard.
    reject = ((len(hs) >= 3 and len(vs) >= 2 and intersections >= 6)
              or (len(hs) >= 5 and len(vs) >= 1 and intersections >= 4))
    return {'reject': bool(reject), 'horizontal_rules':len(hs), 'vertical_rules':len(vs),
            'intersections':intersections, 'rule': 'repeated_grid_v7'}

def global_grid_evidence(binary, box, cfg):
    """Reject table subregions using rules detected across the WHOLE page.

    A cell can contain no interior intersections, so looking only inside its
    candidate bounding box is insufficient. Require an actual multi-row,
    multi-column grid with crossings, not merely internal passport markings.
    """
    H, W = binary.shape
    x, y, w, h = map(int, box)
    # Keep long straight strokes, removing small text/texture.
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(18, round(W * cfg['grid_kernel_fraction'])), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(18, round(H * cfg['grid_kernel_fraction']))))
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, hk)
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vk)
    hs, vs = [], []
    for mask, direction in ((horizontal, 'h'), (vertical, 'v')):
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in cnts:
            a, b, cw, ch = cv2.boundingRect(cnt)
            if direction == 'h' and cw >= max(30, w * cfg['grid_rule_fraction']) and cw > 4*ch:
                hs.append((a, b + ch//2, a+cw))
            if direction == 'v' and ch >= max(30, h * cfg['grid_rule_fraction']) and ch > 4*cw:
                vs.append((a+cw//2, b, b+ch))
    # The candidate may be a single cell or section. Search a surrounding
    # region to recognize a parent grid; don't rely on its interior alone.
    ex = round(w * cfg['grid_context_fraction'])
    ey = round(h * cfg['grid_context_fraction'])
    x0, x1 = max(0, x-ex), min(W, x+w+ex)
    y0, y1 = max(0, y-ey), min(H, y+h+ey)
    hlines = [(a,b,c) for a,b,c in hs if y0 <= b <= y1 and c >= x0 and a <= x1]
    vlines = [(a,b,c) for a,b,c in vs if x0 <= a <= x1 and c >= y0 and b <= y1]
    crossing_points = []
    for xa, yy, xb in hlines:
        for xx, ya, yb in vlines:
            if xa-3 <= xx <= xb+3 and ya-3 <= yy <= yb+3:
                crossing_points.append((xx, yy))
    distinct_x = len(set(round(xx/6) for xx,yy in crossing_points))
    distinct_y = len(set(round(yy/6) for xx,yy in crossing_points))
    grid = (distinct_x >= cfg['grid_min_columns'] and
            distinct_y >= cfg['grid_min_rows'] and
            len(crossing_points) >= cfg['grid_min_crossings'])
    # Only reject a candidate belonging to the detected grid region.
    if crossing_points:
        gx0 = min(xx for xx, yy in crossing_points)
        gx1 = max(xx for xx, yy in crossing_points)
        gy0 = min(yy for xx, yy in crossing_points)
        gy1 = max(yy for xx, yy in crossing_points)
        overlap = max(0, min(x+w, gx1)-max(x,gx0))*max(0,min(y+h,gy1)-max(y,gy0)) / max(1,w*h)
    else:
        overlap = 0.0
    reject = grid and overlap >= cfg['grid_min_overlap']
    return {'reject':bool(reject), 'crossings':len(crossing_points),
            'distinct_x':distinct_x,'distinct_y':distinct_y,
            'grid_overlap':round(overlap,4)}


def detect(img, cfg):
    started = time.perf_counter()
    H, W = img.shape[:2]
    scale = min(1.0, cfg['max_detection_dim']/max(H,W))
    dw, dh = max(1, round(W*scale)), max(1, round(H*scale))
    small = cv2.resize(img, (dw,dh), interpolation=cv2.INTER_AREA) if scale < 1 else img
    sx, sy = W/dw, H/dh
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5,5), 0)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8)).apply(gray)
    normal_edges = cv2.Canny(gray, cfg['canny_low'], cfg['canny_high'])
    faint_edges = cv2.Canny(clahe, cfg['faint_canny_low'], cfg['faint_canny_high'])
    candidates, kernels_tried = [], []
    structural_edges = cv2.bitwise_or(normal_edges, faint_edges)
    band = max(2, round(min(dw,dh)*0.004))
    table_binary = cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)[1]

    def consider(box, source, edges, polygon_vertices, rectangularity, kernel):
        x,y,w,h = map(int,box)
        if w < 25 or h < 15 or x < 0 or y < 0 or x+w > dw or y+h > dh:
            return
        aspect, area_ratio = w/h, w*h/(dw*dh)
        if not (cfg['min_area'] <= area_ratio <= cfg['max_area']):
            return
        if not (cfg['min_aspect'] <= aspect <= cfg['max_aspect']):
            return
        sides = side_support(edges,x,y,w,h,band)
        edge_support = sum(sides)/4
        faint = source.startswith('hough') or source.startswith('faint')
        side_threshold = cfg['faint_strong_side_threshold'] if faint else cfg['strong_side_threshold']
        strong = sum(v >= side_threshold for v in sides)
        required_sides = cfg['faint_min_strong_sides'] if faint else cfg['min_strong_sides']
        required_support = cfg['faint_min_edge_support'] if faint else cfg['min_edge_support']
        margin_x, margin_y = cfg['page_border_margin']*dw, cfg['page_border_margin']*dh
        near_border = sum((x <= margin_x, y <= margin_y, dw-(x+w) <= margin_x, dh-(y+h) <= margin_y))
        structure = structural_frame_evidence(structural_edges, (x,y,w,h), cfg)
        table = table_structure_evidence(table_binary, (x,y,w,h), cfg)
        grid = global_grid_evidence(table_binary, (x,y,w,h), cfg)
        reasons = []
        if table['reject']:
            reasons.append('table_structure')
        if grid['reject']:
            reasons.append('parent_grid_structure')
        if structure['reject']:
            reasons.append('connected_form_structure')
        if near_border >= cfg['reject_border_sides']:
            reasons.append('page_border_frame')
        if edge_support < required_support:
            reasons.append('edge_support')
        if strong < required_sides:
            reasons.append('strong_edge_sides')
        if not faint and rectangularity < cfg['min_rectangularity']:
            reasons.append('rectangularity')
        if not faint and polygon_vertices != 4:
            reasons.append('not_quadrilateral')
        # Two strong sides alone are not enough: require a third weak supporting side.
        if faint and strong == 2 and sum(v >= cfg['faint_third_side_support'] for v in sides) < 3:
            reasons.append('insufficient_third_side_evidence')
        score = 0.35*edge_support + 0.25*rectangularity + 0.25*min(1.0, 1-abs(aspect-1.58)/0.5) + 0.15*strong/4
        ox, oy = round(x*sx), round(y*sy)
        ow, oh = min(W-ox,round((x+w)*sx)-ox), min(H-oy,round((y+h)*sy)-oy)
        candidates.append({
            'bbox':[ox,oy,ow,oh], 'source':source, 'kernel_size':kernel,
            'aspect_ratio':round(aspect,4), 'area_ratio':round(area_ratio,4),
            'rectangularity':round(rectangularity,4), 'edge_support':round(edge_support,4),
            'edge_side_support':[round(v,4) for v in sides],
            'strong_edge_side_count':strong, 'polygon_vertices':polygon_vertices,
            'near_page_border_sides':near_border, 'structure':structure, 'table':table, 'parent_grid':grid, 'score':round(score,4),
            'accepted':not reasons, 'rejection_reasons':reasons,
        })

    for source, edges, sizes in [('normal_contour',normal_edges,cfg['kernel_sizes']),
                                 ('faint_contour',faint_edges,cfg['kernel_sizes'])]:
        for kernel_size in sizes:
            kernels_tried.append(f'{source}:{kernel_size}')
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT,(kernel_size,kernel_size))
            connected = cv2.morphologyEx(edges,cv2.MORPH_CLOSE,kernel,iterations=cfg['close_iterations'])
            contours,_ = cv2.findContours(connected,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
            for cnt in contours:
                x,y,w,h = cv2.boundingRect(cnt)
                if w < 25 or h < 15:
                    continue
                area = cv2.contourArea(cnt)
                if area <= 0:
                    continue
                perimeter = cv2.arcLength(cnt,True)
                poly = cv2.approxPolyDP(cnt,0.025*perimeter,True)
                consider((x,y,w,h),source,edges,len(poly),min(1.0,area/(w*h)),kernel_size)

    # Only search the faint Hough path if no contour candidate has passed.
    if not any(c['accepted'] for c in candidates):
        for box in hough_boxes(faint_edges,cfg):
            consider(box,'hough_faint',faint_edges,4,1.0,None)

    # Collapse near-identical boxes across kernels and methods.
    candidates.sort(key=lambda c:(c['accepted'],c['score']),reverse=True)
    unique=[]
    for c in candidates:
        x,y,w,h=c['bbox']
        if any(max(abs(x-q['bbox'][0])/max(w,1),abs(y-q['bbox'][1])/max(h,1),
                   abs(w-q['bbox'][2])/max(w,1),abs(h-q['bbox'][3])/max(h,1)) < 0.035 for q in unique):
            continue
        unique.append(c)
    selected=next((c for c in unique if c['accepted'] and c['score'] >= cfg['min_score']),None)
    return selected,unique[:cfg['debug_candidates']],{
        'original_size':[W,H], 'detection_size':[dw,dh],
        'candidate_count':len(candidates), 'unique_candidate_count':len(unique),
        'kernels_tried':kernels_tried,
        'card_detection_ms':round((time.perf_counter()-started)*1000,2),
    }


def process(path,output,dpi,cfg):
    import pymupdf
    output.mkdir(parents=True,exist_ok=True)
    # Count pages before running any detection. Multi-page PDFs bypass all crop logic.
    with pymupdf.open(path) as doc:
        total_pages = len(doc)
    for page_num,img in load_pages(path,dpi):
        started=time.perf_counter()
        if total_pages > 1:
            selected, candidates = None, []
            form = {'reject': False, 'skipped': 'multipage_pdf'}
            meta = {'candidate_count': 0, 'card_detection_ms': 0.0,
                    'skipped_detection': 'multipage_pdf'}
        else:
            form = ruled_form_evidence(img, cfg)
            if form['reject']:
                selected, candidates, meta = None, [], {'candidate_count': 0, 'card_detection_ms': 0.0,
                    'skipped_detection': 'ruled_form_page'}
            else:
                selected,candidates,meta=detect(img,cfg)
        meta['ruled_form_evidence'] = form
        H,W=img.shape[:2]
        overlay=img.copy()
        for c in reversed(candidates):
            x,y,w,h=c['bbox']
            color=(0,180,0) if c['accepted'] else (0,0,220)
            cv2.rectangle(overlay,(x,y),(x+w,y+h),color,max(2,round(W/1200)))
        crop_box=None
        if selected:
            x,y,w,h=selected['bbox']
            px,py=round(w*cfg['padding']),round(h*cfg['padding'])
            x0,y0=max(0,x-px),max(0,y-py)
            x1,y1=min(W,x+w+px),min(H,y+h+py)
            result=img[y0:y1,x0:x1]
            crop_box=[x0,y0,x1-x0,y1-y0]
            source='physical_card_crop'
        else:
            result=img
            source='full_page_multipage' if total_pages > 1 else 'full_page'
        stem=f'{path.stem}_page_{page_num:03d}'
        save_image(output/f'{stem}_result.png',result)
        save_image(output/f'{stem}_overlay.png',overlay)
        debug={'input':str(path),'page':page_num,'total_pages':total_pages,'page_size':[W,H],
               'final_matching_path_source':source,'padded_crop_box':crop_box,
               'selected_candidate':selected,'crop_rejection_reason':None if selected else ('multipage_pdf' if total_pages > 1 else ('ruled_form_page' if form['reject'] else 'no_candidate_passed_guards')),
               'detection':meta,'top_candidates':candidates,'thresholds':cfg}
        (output/f'{stem}_debug.json').write_text(json.dumps(debug,indent=2,ensure_ascii=False),encoding='utf-8')
        print(f'Page {page_num}: {source}; crop={crop_box}; candidates={meta["candidate_count"]}; detection_ms={meta["card_detection_ms"]}; total_ms={round((time.perf_counter()-started)*1000,2)}')


def main():
    parser=argparse.ArgumentParser(description='Standalone PDF-only card crop tester v7 (geometry only)')
    parser.add_argument('input',type=Path)
    parser.add_argument('--output',type=Path,default=Path('crop_output_v3'))
    parser.add_argument('--dpi',type=int,default=200)
    parser.add_argument('--max-detection-dim',type=int,default=1600)
    parser.add_argument('--kernel-sizes',type=int,nargs='+',default=[5,9,15])
    parser.add_argument('--close-iterations',type=int,default=2)
    parser.add_argument('--canny-low',type=int,default=35)
    parser.add_argument('--canny-high',type=int,default=110)
    parser.add_argument('--faint-canny-low',type=int,default=15)
    parser.add_argument('--faint-canny-high',type=int,default=55)
    parser.add_argument('--min-aspect',type=float,default=1.35)
    parser.add_argument('--max-aspect',type=float,default=1.85)
    parser.add_argument('--min-area',type=float,default=0.05)
    parser.add_argument('--max-area',type=float,default=0.70)
    parser.add_argument('--min-rectangularity',type=float,default=0.70)
    parser.add_argument('--min-edge-support',type=float,default=0.60)
    parser.add_argument('--min-strong-sides',type=int,default=3)
    parser.add_argument('--strong-side-threshold',type=float,default=0.50)
    parser.add_argument('--faint-min-edge-support',type=float,default=0.30)
    parser.add_argument('--faint-min-strong-sides',type=int,default=2)
    parser.add_argument('--faint-strong-side-threshold',type=float,default=0.25)
    parser.add_argument('--faint-third-side-support',type=float,default=0.15)
    parser.add_argument('--page-border-margin',type=float,default=0.03)
    parser.add_argument('--reject-border-sides',type=int,default=3)
    parser.add_argument('--hough-votes',type=int,default=35)
    parser.add_argument('--hough-min-line-fraction',type=float,default=0.06)
    parser.add_argument('--hough-max-lines',type=int,default=16)
    parser.add_argument('--hough-max-boxes',type=int,default=250)
    parser.add_argument('--structure-probe-fraction',type=float,default=0.18)
    parser.add_argument('--structure-extension-support',type=float,default=0.48)
    parser.add_argument('--structure-horizontal-support',type=float,default=0.55)
    parser.add_argument('--structure-large-area',type=float,default=0.20)
    parser.add_argument('--structure-strong-extension',type=float,default=0.85)
    parser.add_argument('--structure-min-extensions',type=int,default=2)
    parser.add_argument('--table-min-rule-fraction',type=float,default=0.28)
    parser.add_argument('--table-min-horizontal',type=int,default=2)
    parser.add_argument('--table-min-vertical',type=int,default=1)
    parser.add_argument('--table-min-intersections',type=int,default=2)
    parser.add_argument('--table-many-horizontal',type=int,default=4)
    parser.add_argument('--grid-kernel-fraction',type=float,default=0.035)
    parser.add_argument('--grid-rule-fraction',type=float,default=0.35)
    parser.add_argument('--grid-context-fraction',type=float,default=0.80)
    parser.add_argument('--grid-min-columns',type=int,default=2)
    parser.add_argument('--grid-min-rows',type=int,default=3)
    parser.add_argument('--grid-min-crossings',type=int,default=6)
    parser.add_argument('--grid-min-overlap',type=float,default=0.12)
    parser.add_argument('--min-score',type=float,default=0.50)
    parser.add_argument('--padding',type=float,default=0.0)
    parser.add_argument('--debug-candidates',type=int,default=30)
    parser.add_argument('--form-rule-width',type=float,default=0.65)
    parser.add_argument('--form-wall-height',type=float,default=0.30)
    parser.add_argument('--form-wall-separation',type=float,default=0.65)
    parser.add_argument('--form-wall-overlap',type=float,default=0.30)
    parser.add_argument('--form-min-rules',type=int,default=4)
    args=parser.parse_args()
    if not args.input.exists():
        parser.error(f'Input path not found: {args.input}')
    if args.max_detection_dim<100 or any(k<1 or k%2==0 for k in args.kernel_sizes):
        parser.error('max-detection-dim must be >=100; kernel sizes must be positive odd integers')
    cfg=vars(args).copy()
    for k in ('input','output','dpi'):
        cfg.pop(k)
    paths = (sorted(p for p in args.input.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS)
             if args.input.is_dir() else [args.input])
    if not paths or any(p.suffix.lower() not in SUPPORTED_EXTENSIONS for p in paths):
        parser.error('Provide a PDF file or a folder containing PDF files')
    for path in paths:
        # Separate per-source directories to avoid overwriting same-stem files.
        target = args.output / f'{path.stem}_pdf'
        try:
            process(path, target, args.dpi, cfg)
        except Exception as exc:
            print(f'ERROR {path}: {exc}')
            if len(paths) == 1:
                raise


if __name__=='__main__':
    main()
