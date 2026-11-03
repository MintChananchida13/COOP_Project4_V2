import type { ROI } from "../../types/ocr";

export interface SelectionRect { x: number; y: number; width: number; height: number }

/** DOM rect includes both the current CSS zoom and the viewport scroll offset. */
export function canvasPoint(clientX: number, clientY: number, rect: SelectionRect,
  width: number, height: number) {
  return { x: Math.max(0, Math.min(width, (clientX - rect.x) * width / rect.width)),
    y: Math.max(0, Math.min(height, (clientY - rect.y) * height / rect.height)) };
}

export function selectionRect(start: { x: number; y: number }, end: { x: number; y: number }): SelectionRect {
  return { x: Math.min(start.x, end.x), y: Math.min(start.y, end.y),
    width: Math.abs(end.x - start.x), height: Math.abs(end.y - start.y) };
}

export function intersectsSelection(roi: SelectionRect, rect: SelectionRect): boolean {
  return roi.x < rect.x + rect.width && roi.x + roi.width > rect.x
    && roi.y < rect.y + rect.height && roi.y + roi.height > rect.y;
}

/** Clamp the whole group together so relative positions never change at image edges. */
export function groupTranslation(rois: SelectionRect[], dx: number, dy: number, width: number, height: number) {
  if (!rois.length) return { x: 0, y: 0 };
  const left = Math.min(...rois.map(roi => roi.x));
  const top = Math.min(...rois.map(roi => roi.y));
  const right = Math.max(...rois.map(roi => roi.x + roi.width));
  const bottom = Math.max(...rois.map(roi => roi.y + roi.height));
  return { x: Math.max(-left, Math.min(width - right, dx)), y: Math.max(-top, Math.min(height - bottom, dy)) };
}

export function translateRoi<T extends ROI>(roi: T, delta: { x: number; y: number }): T {
  return { ...roi, x: roi.x + delta.x, y: roi.y + delta.y,
    ...(roi.points ? { points: roi.points.map(point => ({ x: point.x + delta.x, y: point.y + delta.y })) } : {}) };
}
