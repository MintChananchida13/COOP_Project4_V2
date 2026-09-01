"use client";

import { RoiRatio } from "../../types/ocr";

export interface WorkspaceImageMetrics {
  imageOffsetX: number;
  imageOffsetY: number;
  imageWidth: number;
  imageHeight: number;
  naturalWidth: number;
  naturalHeight: number;
}

export const DEFAULT_WORKSPACE_IMAGE_METRICS: WorkspaceImageMetrics = {
  imageOffsetX: 0,
  imageOffsetY: 0,
  imageWidth: 750,
  imageHeight: 1000,
  naturalWidth: 750,
  naturalHeight: 1000,
};

export const ratioToImageBox = (roi: RoiRatio, metrics: WorkspaceImageMetrics) => ({
  x: metrics.imageOffsetX + roi.xRatio * metrics.imageWidth,
  y: metrics.imageOffsetY + roi.yRatio * metrics.imageHeight,
  width: roi.widthRatio * metrics.imageWidth,
  height: roi.heightRatio * metrics.imageHeight,
});
