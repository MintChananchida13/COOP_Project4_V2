"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { LogOut } from "lucide-react";
import AdjustZone from "../user/components/AdjustZone";
import WorkspaceZone from "../user/components/WorkspaceZone";
import MatchedTemplateWorkspaceZone from "../user/components/MatchedTemplateWorkspaceZone";
import GroundTruthEditorZone from "../user/components/GroundTruthEditorZone";
import TemplateRequestPanel from "../user/components/TemplateRequestPanel";
import { ROI, OCRResult, StructuredTableResult, TableExportConfig, TemplateField } from "../types/ocr";
import {
  ADMIN_API_BASE_URL,
  detectTemplateDev,
  fetchTemplateBundle,
  type DetectionDevResult,
} from "../admin/adminApi";
import AuthGate from "../auth/AuthGate";
import { AuthSession, authHeaders, clearAuthSession, readAuthSession } from "../auth/session";

interface PageConfig {
  rotation: number;
  brightness: number;
  contrast: number;
  sharpness: number;
  perspectiveV: number;
  perspectiveH: number;
  flipH: boolean;
  flipV: boolean;
  cropBox: {
    x: number;
    y: number;
    width: number;
    height: number;
    renderedWidth?: number;
    renderedHeight?: number;
  } | null;
  cropCorners: { x: number; y: number }[] | null;
  isCropActive: boolean;
  isCropped: boolean;
  croppedLocalUrl: string | null;
}

interface TemplateDetectionNotice {
  title: string;
  message: string;
  detail?: string;
}

type NoticeTone = "success" | "warning" | "danger" | "info";
type ExportFormat = "word" | "excel" | "json" | "images";
type ExportContentOptions = {
  text: boolean;
  tables: boolean;
  images: boolean;
};
type ExportDisplayOptions = {
  showFieldNames: boolean;
  showDocumentTitle: boolean;
};
type ImageFieldCrop = {
  resultId: number;
  fieldName: string;
  filename: string;
  dataUrl: string;
  page: number;
  width: number;
  height: number;
};

const USER_FLOW_STEPS = [
  {
    key: "upload",
    title: "อัปโหลดเอกสาร",
    description: "เลือกไฟล์ภาพหรือ PDF ครั้งละ 1 ไฟล์",
    note: "PDF หนึ่งไฟล์รองรับหลายหน้า ระบบจะแปลงเป็นภาพก่อนทำงาน",
  },
  {
    key: "adjust",
    title: "ตรวจสอบขอบเอกสาร",
    description: "ปรับกรอบให้พอดีกับขอบเอกสาร",
    note: "กรอบที่แม่นยำช่วยให้การค้นหา Template และ OCR ดีขึ้น",
  },
  {
    key: "studio",
    title: "เลือกข้อมูลที่ต้องอ่าน",
    description: "ใช้ ROI จาก Template หรือกำหนด ROI เอง",
    note: "เลือกเฉพาะ Field ที่ต้องการ เพื่อลดเวลาและลดข้อมูลเกินจำเป็น",
  },
  {
    key: "editor",
    title: "ตรวจผลและส่งออก",
    description: "แก้ไขผล OCR แล้ว Export ด้วยค่าล่าสุด",
    note: "ผลลัพธ์ที่ส่งออกจะใช้ค่าที่ผู้ใช้แก้ไขล่าสุด",
  },
] as const;

const USER_STEP_ACTIONS: Record<(typeof USER_FLOW_STEPS)[number]["key"], string[]> = {
  upload: [
    "คลิกพื้นที่อัปโหลดหรือวางไฟล์เอกสาร 1 ไฟล์ลงในช่องอัปโหลด",
    "ตรวจสอบว่าไฟล์เป็นภาพหรือ PDF ที่อ่านได้ชัดเจน",
    "หากเป็น PDF หลายหน้า ระบบจะเตรียมภาพให้ทีละหน้า",
  ],
  adjust: [
    "ตรวจกรอบเอกสารที่ระบบจับให้อัตโนมัติ",
    "ลากมุมหรือขอบกรอบให้ครอบเฉพาะตัวเอกสาร",
    "กดยืนยันเมื่อภาพตรงและพร้อมค้นหา Template",
  ],
  studio: [
    "เลือก Field หรือวาด ROI เฉพาะข้อมูลที่ต้องการอ่าน",
    "ใช้ Auto ROI เมื่อต้องการให้ระบบช่วยสร้างกรอบเริ่มต้น",
    "กดอ่านข้อมูลที่เลือกเพื่อเข้าสู่หน้าตรวจผล",
  ],
  editor: [
    "ตรวจข้อความ ตาราง และรูปภาพที่ OCR อ่านได้",
    "แก้ไขค่าที่ไม่ถูกต้อง ระบบจะอัปเดตให้อัตโนมัติ",
    "กด Export แล้วเลือกรูปแบบไฟล์ที่ต้องการ",
  ],
};

const NoTemplateDetectionCard = ({
  notice,
}: {
  notice: TemplateDetectionNotice;
}) => {
  const isRuntimeUnavailable = [
    notice.title,
    notice.message,
    notice.detail,
  ]
    .filter(Boolean)
    .some((text) =>
      String(text).toLowerCase().includes("runtime unavailable")
    );

  const heading = isRuntimeUnavailable
    ? "ไม่สามารถตรวจสอบ Template ได้"
    : "ไม่พบ Template ที่ตรงกัน";

  const title = isRuntimeUnavailable
    ? "ระบบตรวจจับ Template ไม่พร้อมใช้งาน"
    : notice.title || "ตรวจจับ Template ไม่สำเร็จ";

  const message = isRuntimeUnavailable
    ? "ขณะนี้ไม่สามารถเชื่อมต่อระบบประมวลผลได้ กรุณาลองใหม่อีกครั้ง หรือดำเนินการด้วย Custom OCR"
    : notice.message || "ไม่พบ Template ที่ตรงกับเอกสารนี้";

  return (
    <section className="rounded-2xl border border-amber-100 bg-amber-50 p-4">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white text-amber-600 shadow-sm ring-1 ring-amber-100">
          <svg
            className="h-[18px] w-[18px]"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M12 9v4m0 4h.01M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"
            />
          </svg>
        </div>

        <div className="min-w-0">
          <h3 className="ui-label text-amber-800">{heading}</h3>

          <p className="ui-card-title mt-1 text-amber-950">
            {title}
          </p>

          <p className="ui-caption mt-1 break-words text-amber-700">
            {message}
          </p>

          <div className="mt-3 rounded-xl border border-amber-100 bg-white/75 px-3 py-2">
            <p className="ui-caption break-words font-semibold text-amber-800">
              สามารถดำเนินการต่อด้วย Custom OCR
            </p>

            <p className="ui-caption mt-0.5 break-words text-amber-700">
              กำหนดกรอบ ROI ด้วยตนเอง หรือใช้ Auto ROI เพื่อช่วยตรวจจับพื้นที่ข้อมูล
            </p>
          </div>
        </div>
      </div>
    </section>
  );
};

const UploadZone = dynamic(() => import("../user/components/UploadZone"), {
  ssr: false,
  loading: () => (
    <div className="w-full max-w-3xl mx-auto py-12 px-4 text-center text-slate-500 font-medium text-xs">
      กำลังเตรียมส่วนประกอบการอัปโหลด...
    </div>
  ),
});

const cropRoiToImage = (
  imgEl: HTMLImageElement,
  roi: { x: number; y: number; width: number; height: number; points?: { x: number; y: number }[] },
  scaleX: number,
  scaleY: number
): string | null => {
  if (!imgEl || !imgEl.complete || imgEl.naturalWidth === 0) return null;

  const realX = roi.x * scaleX;
  const realY = roi.y * scaleY;
  const realW = roi.width * scaleX;
  const realH = roi.height * scaleY;

  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(realW));
  canvas.height = Math.max(1, Math.round(realH));
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;

  const polygonPoints = roi.points && roi.points.length > 2 ? roi.points : null;
  const hasPolygonMask = Boolean(polygonPoints);
  if (!hasPolygonMask) {
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }

  ctx.save();
  if (polygonPoints) {
    ctx.beginPath();
    polygonPoints.forEach((p, idx) => {
      const px = p.x * scaleX - realX;
      const py = p.y * scaleY - realY;
      if (idx === 0) {
        ctx.moveTo(px, py);
      } else {
        ctx.lineTo(px, py);
      }
    });
    ctx.closePath();
    ctx.clip();
  }

  ctx.drawImage(
    imgEl,
    Math.max(0, realX),
    Math.max(0, realY),
    Math.max(1, realW),
    Math.max(1, realH),
    0,
    0,
    Math.max(1, realW),
    Math.max(1, realH)
  );
  ctx.restore();

  return polygonPoints ? canvas.toDataURL("image/png") : canvas.toDataURL("image/jpeg", 0.95);
};

async function dataUrlToFile(dataUrl: string, filename: string) {
  const response = await fetch(dataUrl);
  const blob = await response.blob();
  return new File([blob], filename, { type: blob.type || "image/jpeg" });
}

function blobToDataUrl(blob: Blob) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("Unable to read image blob"));
    reader.readAsDataURL(blob);
  });
}

async function imageUrlToCanvasSafeSrc(src: string) {
  if (!src || src.startsWith("data:") || src.startsWith("blob:")) return src;
  const response = await fetch(src, { mode: "cors" });
  if (!response.ok) throw new Error(`Unable to load extraction image: ${response.status}`);
  return blobToDataUrl(await response.blob());
}

async function analyzeLayoutForUserImage(imageDataUrl: string) {
  const response = await fetch(`${ADMIN_API_BASE_URL}/api/layout/analyze`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      auto_roi_mode: "text_line",
      context: "flexible",
      images: [{ page_index: 0, image: imageDataUrl }],
    }),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(data?.detail || data?.message || "Layout analysis failed.");
  const regions = data?.pages?.[0]?.regions;
  return Array.isArray(regions) ? (regions as Record<string, any>[]) : [];
}

function backendPreviewSrc(value?: string | null) {
  if (!value) return "";
  if (value.startsWith("data:") || value.startsWith("blob:") || value.startsWith("http")) return value;
  if (value.startsWith("/")) return `${ADMIN_API_BASE_URL}${value}`;
  return value;
}

function stableNumericId(value: string) {
  return Math.abs(value.split("").reduce((hash, char) => (hash * 31 + char.charCodeAt(0)) | 0, 7));
}

function loadImageElement(src: string) {
  return new Promise<HTMLImageElement>((resolve, reject) => {
    const imageObj = new Image();
    if (!src.startsWith("data:") && !src.startsWith("blob:")) {
      imageObj.crossOrigin = "anonymous";
    }
    imageObj.onload = () => resolve(imageObj);
    imageObj.onerror = reject;
    imageObj.src = src;
  });
}

async function templateFieldsToWorkspaceRois(
  fields: TemplateField[],
  imageList: string[],
  detection?: DetectionDevResult | null,
  templateId?: string,
  options?: { templatePageToImageIndex?: Record<number, number> }
) {
  const pageImages = await Promise.all(imageList.map((src) => loadImageElement(src).catch(() => null)));
  const extractionFields = fields
    .filter((field) => !field.useForVerification)
    .sort(compareTemplateFieldsForWorkspace);
  const workspaceRois: (ROI & { pageIndex?: number; roiCoordinateSource?: string })[] = [];

  for (const field of extractionFields) {
    const roi = field.roi;
    const pageIndex = Math.max(0, options?.templatePageToImageIndex?.[roi.pageNumber] ?? roi.pageNumber - 1);
    const pageImage = pageImages[pageIndex];
    const displayWidth = 750;
    const displayHeight = pageImage?.naturalWidth
      ? (pageImage.naturalHeight / pageImage.naturalWidth) * displayWidth
      : 1000;
    const type = getWorkspaceRoiType(field);

    workspaceRois.push({
      id: stableNumericId(`template-field:${field.id}`),
      fieldName: field.displayLabel || field.fieldName,
      x: roi.xRatio * displayWidth,
      y: roi.yRatio * displayHeight,
      width: roi.widthRatio * displayWidth,
      height: roi.heightRatio * displayHeight,
      pageIndex,
      type,
      dataType: field.dataType || type,
      extractionMethod: getWorkspaceExtractionMethod(field),
      roiMode: field.roiMode === "flexible" ? "flexible" : "fix",
      expectedContent: field.roiMode === "flexible" ? "text" : null,
      role: "data_extraction",
      enabled: field.defaultSelected !== false,
      roiCoordinateSource: "template_roi",
    });
  }

  return workspaceRois;
}

function roiFromLayoutBlock(
  block: Record<string, any>,
  renderedWidth: number,
  renderedHeight: number,
  scaleX: number,
  scaleY: number
) {
  const bbox = block.bbox && typeof block.bbox === "object" ? block.bbox : null;
  const roi = block.roi && typeof block.roi === "object" ? block.roi : null;
  if (bbox) {
    return {
      x: Number(bbox.x || 0) / Math.max(scaleX, 1e-6),
      y: Number(bbox.y || 0) / Math.max(scaleY, 1e-6),
      width: Number(bbox.width || 0) / Math.max(scaleX, 1e-6),
      height: Number(bbox.height || 0) / Math.max(scaleY, 1e-6),
    };
  }
  if (roi) {
    return {
      x: Number(roi.x_ratio || 0) * renderedWidth,
      y: Number(roi.y_ratio || 0) * renderedHeight,
      width: Number(roi.width_ratio || 0) * renderedWidth,
      height: Number(roi.height_ratio || 0) * renderedHeight,
    };
  }
  return null;
}

function isCountableWorkspaceField(roi: ROI & { pageIndex?: number }) {
  return roi.enabled !== false && !(roi.roiMode === "flexible" && !roi.isResolvedBlock);
}

async function buildWholePageAutoRois(
  sourceImages: string[],
  existingRois: (ROI & { pageIndex?: number })[],
  excludedPageIndexes: Set<number>
): Promise<(ROI & { pageIndex?: number; roiCoordinateSource?: string })[]> {
  const autoRois: (ROI & { pageIndex?: number; roiCoordinateSource?: string })[] = [];
  let nextFieldNumber = existingRois.filter(isCountableWorkspaceField).length + 1;
  for (const [pageIndex, sourceImage] of sourceImages.entries()) {
    if (excludedPageIndexes.has(pageIndex) || !sourceImage) continue;
    let img: HTMLImageElement;
    try {
      img = await loadImageElement(sourceImage);
    } catch (error) {
      console.warn("Unable to load page image for whole-page auto ROI.", error);
      continue;
    }
    const renderedWidth = 750;
    const renderedHeight = (img.naturalHeight / img.naturalWidth) * renderedWidth;
    const scaleX = img.naturalWidth / renderedWidth;
    const scaleY = img.naturalHeight / renderedHeight;
    let regions: Record<string, any>[] = [];
    try {
      regions = await analyzeLayoutForUserImage(sourceImage);
    } catch (error) {
      console.warn("Whole-page auto ROI analysis failed.", error);
      continue;
    }
    regions.forEach((block, index) => {
      const rect = roiFromLayoutBlock(block, renderedWidth, renderedHeight, scaleX, scaleY);
      if (!rect || ![rect.x, rect.y, rect.width, rect.height].every(Number.isFinite) || rect.width <= 1 || rect.height <= 1) {
        return;
      }
      const type = normalizeResolvedBlockType(block);
      autoRois.push({
        id: stableNumericId(`matched-template-extra-page:${pageIndex}:${index}:${rect.x}:${rect.y}:${rect.width}:${rect.height}`),
        fieldName: `field_${nextFieldNumber}`,
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height,
        pageIndex,
        type,
        dataType: type,
        extractionMethod: type === "table" ? "table_recognition_v2" : type === "image" ? "extract_image" : "paddle_thai_ocr",
        roiMode: "fix",
        enabled: true,
        role: "data_extraction",
        isResolvedBlock: true,
        roiCoordinateSource: "whole_page_auto_roi",
        layoutType: String(block.layout_type || block.layoutType || type),
      });
      nextFieldNumber += 1;
    });
  }
  return autoRois;
}

function compareTemplateFieldsForWorkspace(left: TemplateField, right: TemplateField) {
  return (
    left.pageNumber - right.pageNumber ||
    (left.sortOrder ?? 0) - (right.sortOrder ?? 0) ||
    left.fieldName.localeCompare(right.fieldName)
  );
}

function getWorkspaceRoiType(field: TemplateField): "text" | "table" | "image" {
  if (field.extractionMethod === "ocr_table" || field.extractionMethod === "table_recognition_v2" || field.dataType === "table") {
    return "table";
  }
  if (field.extractionMethod === "extract_image" || field.dataType === "image") {
    return "image";
  }
  return "text";
}

function getWorkspaceExtractionMethod(field: TemplateField) {
  if (
    field.extractionMethod === "ocr_table" ||
    field.extractionMethod === "table_recognition_v2" ||
    field.extractionMethod === "paddle_thai_ocr" ||
    field.extractionMethod === "extract_image"
  ) {
    return field.extractionMethod;
  }
  if (field.dataType === "table") return "table_recognition_v2";
  if (field.dataType === "image") return "extract_image";
  return "paddle_thai_ocr";
}

function getRoiFieldType(roi: ROI): "text" | "table" | "image" {
  if (roi.type === "table" || roi.dataType === "table" || roi.extractionMethod === "ocr_table" || roi.extractionMethod === "table_recognition_v2") {
    return "table";
  }
  if (roi.type === "image" || roi.dataType === "image" || roi.extractionMethod === "extract_image") {
    return "image";
  }
  return "text";
}

function getRoiExtractionMethod(roi: ROI) {
  const roiType = getRoiFieldType(roi);
  if (
    roi.extractionMethod === "ocr_table" ||
    roi.extractionMethod === "table_recognition_v2" ||
    roi.extractionMethod === "paddle_thai_ocr" ||
    roi.extractionMethod === "extract_image"
  ) {
    return roi.extractionMethod;
  }
  return roiType === "image" ? "extract_image" : roiType === "table" ? "table_recognition_v2" : "paddle_thai_ocr";
}

async function buildTemplateCanvasImages(
  sourceImages: string[],
  detection: DetectionDevResult,
  templateId: string,
  options?: { keepOriginalImages?: boolean }
) {
  if (options?.keepOriginalImages) return [...sourceImages];
  const pages = detection.pages || [];
  return Promise.all(sourceImages.map(async (sourceImage, pageIndex) => {
    const page = pages.find((item) => item.pageIndex === pageIndex + 1);
    const pageCandidate =
      page?.candidates?.find((candidate) => candidate.templateId === templateId) ||
      (page?.bestCandidate?.templateId === templateId ? page.bestCandidate : null);
    const extractionSrc = backendPreviewSrc(pageCandidate?.alignedImagePreviewUrl || pageCandidate?.extractionImagePreviewUrl);
    if (!extractionSrc) return sourceImage;
    try {
      return await imageUrlToCanvasSafeSrc(extractionSrc);
    } catch (error) {
      console.warn("Unable to convert extraction image to canvas-safe data URL.", error);
      return sourceImage;
    }
  }));
}

function devTemplateFlowLog(message: string, details?: Record<string, unknown>) {
  if (process.env.NODE_ENV !== "production") {
    console.debug(`[Template detection flow] ${message}`, details || {});
  }
}

function contextualTemplateError(context: string, error: unknown) {
  const message = error instanceof Error ? error.message : String(error || "Unknown error");
  return new Error(`${context}: ${message}`, { cause: error });
}

const downloadTextFile = (filename: string, content: string, mimeType = "application/json") => {
  const blob = new Blob([content], { type: `${mimeType};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
};

const downloadBlobFile = (filename: string, blob: Blob) => {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
};

const escapeHtml = (value: unknown) =>
  String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

const safeFilename = (value: string) =>
  (value || "image")
    .trim()
    .replace(/[\\/:*?"<>|]+/g, "_")
    .replace(/\s+/g, "_")
    .slice(0, 80) || "image";

const dataUrlToBytes = (dataUrl: string) => {
  const [, encoded = ""] = dataUrl.split(",", 2);
  const binary = atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
};

const dataUrlMimeType = (dataUrl: string) => {
  const match = /^data:([^;,]+)[;,]/.exec(dataUrl);
  return match?.[1] || "image/jpeg";
};

const dataUrlBase64 = (dataUrl: string) => dataUrl.split(",", 2)[1] || "";

const sanitizeXmlText = (value: unknown) =>
  String(value ?? "")
    // XML 1.0 rejects most control characters. OCR output can contain them and
    // Excel will refuse to open the workbook if they are written into inlineStr.
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, "");

const xmlEscape = (value: unknown) =>
  sanitizeXmlText(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");

const spreadsheetColumnName = (index: number) => {
  let value = index + 1;
  let name = "";
  while (value > 0) {
    const remainder = (value - 1) % 26;
    name = String.fromCharCode(65 + remainder) + name;
    value = Math.floor((value - 1) / 26);
  }
  return name;
};

const xlsxCell = (rowIndex: number, colIndex: number, value: unknown, style = 1) =>
  `<c r="${spreadsheetColumnName(colIndex)}${rowIndex + 1}" t="inlineStr" s="${style}"><is><t>${xmlEscape(value)}</t></is></c>`;

const xlsxRow = (rowIndex: number, values: unknown[], style = 1) =>
  `<row r="${rowIndex + 1}">${values.map((value, colIndex) => xlsxCell(rowIndex, colIndex, value, style)).join("")}</row>`;

const EXCEL_IMAGE_MAX_WIDTH_PX = 180;
const EXCEL_IMAGE_MAX_HEIGHT_PX = 120;
const EXCEL_EMUS_PER_PIXEL = 9525;

const fitExcelImageSize = (width: number, height: number) => {
  const safeWidth = Math.max(1, width || EXCEL_IMAGE_MAX_WIDTH_PX);
  const safeHeight = Math.max(1, height || EXCEL_IMAGE_MAX_HEIGHT_PX);
  const scale = Math.min(EXCEL_IMAGE_MAX_WIDTH_PX / safeWidth, EXCEL_IMAGE_MAX_HEIGHT_PX / safeHeight);
  return {
    width: Math.max(1, Math.round(safeWidth * scale)),
    height: Math.max(1, Math.round(safeHeight * scale)),
  };
};

const crc32 = (bytes: Uint8Array) => {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
};

const writeUint16 = (target: number[], value: number) => {
  target.push(value & 0xff, (value >>> 8) & 0xff);
};

const writeUint32 = (target: number[], value: number) => {
  target.push(value & 0xff, (value >>> 8) & 0xff, (value >>> 16) & 0xff, (value >>> 24) & 0xff);
};

const createZipBlob = (files: { name: string; bytes: Uint8Array }[]) => {
  const encoder = new TextEncoder();
  const output: number[] = [];
  const centralDirectory: number[] = [];

  files.forEach((file) => {
    const nameBytes = encoder.encode(file.name);
    const checksum = crc32(file.bytes);
    const localHeaderOffset = output.length;

    writeUint32(output, 0x04034b50);
    writeUint16(output, 20);
    writeUint16(output, 0);
    writeUint16(output, 0);
    writeUint16(output, 0);
    writeUint16(output, 0);
    writeUint32(output, checksum);
    writeUint32(output, file.bytes.length);
    writeUint32(output, file.bytes.length);
    writeUint16(output, nameBytes.length);
    writeUint16(output, 0);
    output.push(...nameBytes, ...file.bytes);

    writeUint32(centralDirectory, 0x02014b50);
    writeUint16(centralDirectory, 20);
    writeUint16(centralDirectory, 20);
    writeUint16(centralDirectory, 0);
    writeUint16(centralDirectory, 0);
    writeUint16(centralDirectory, 0);
    writeUint16(centralDirectory, 0);
    writeUint32(centralDirectory, checksum);
    writeUint32(centralDirectory, file.bytes.length);
    writeUint32(centralDirectory, file.bytes.length);
    writeUint16(centralDirectory, nameBytes.length);
    writeUint16(centralDirectory, 0);
    writeUint16(centralDirectory, 0);
    writeUint16(centralDirectory, 0);
    writeUint16(centralDirectory, 0);
    writeUint32(centralDirectory, 0);
    writeUint32(centralDirectory, localHeaderOffset);
    centralDirectory.push(...nameBytes);
  });

  const centralDirectoryOffset = output.length;
  output.push(...centralDirectory);
  writeUint32(output, 0x06054b50);
  writeUint16(output, 0);
  writeUint16(output, 0);
  writeUint16(output, files.length);
  writeUint16(output, files.length);
  writeUint32(output, centralDirectory.length);
  writeUint32(output, centralDirectoryOffset);
  writeUint16(output, 0);
  return new Blob([new Uint8Array(output)], { type: "application/zip" });
};

const parseExportTable = (value: string): string[][] | null => {
  const trimmed = value.trim();
  if (!trimmed || /^\(?no\s+text\s+found\s+in\s+roi\)?$/i.test(trimmed)) return null;

  try {
    const parsed = JSON.parse(trimmed);
    if (Array.isArray(parsed) && parsed.every((row) => Array.isArray(row))) {
      return parsed.map((row) => row.map((cell) => String(cell ?? "")));
    }
    if (Array.isArray(parsed) && parsed.every((row) => row && typeof row === "object" && !Array.isArray(row))) {
      const keys = Array.from(new Set(parsed.flatMap((row) => Object.keys(row))));
      return [keys, ...parsed.map((row) => keys.map((key) => String(row[key] ?? "")))];
    }
  } catch {
    // Continue with markdown/plain-text parsing.
  }

  const markdownRows = trimmed
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.includes("|"))
    .map((line) => line.replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim()))
    .filter((row) => !row.every((cell) => /^:?-{3,}:?$/.test(cell)));
  if (markdownRows.length >= 2) return markdownRows;

  const plainRows = trimmed
    .split(/\r?\n/)
    .map((line) => line.trim().replace(/\s+/g, " "))
    .filter(Boolean)
    .map((line) => line.split(/\s{2,}/).map((cell) => cell.trim()).filter(Boolean));
  return plainRows.length >= 2 && plainRows.some((row) => row.length > 1) ? plainRows : null;
};

const tableRowsToObjects = (rows: string[][]) => {
  const [header = [], ...bodyRows] = rows;
  const headers = header.map((cell, index) => cell || `column_${index + 1}`);
  return bodyRows.map((row) =>
    Object.fromEntries(headers.map((headerName, index) => [headerName, row[index] ?? ""]))
  );
};

const normalizeHeaderPart = (value: unknown) => String(value ?? "").replace(/\s+/g, " ").trim();

const getEffectiveHeaderRowCount = (rows: string[][], structured?: StructuredTableResult | null) => {
  const baseHeaderRowCount = Math.max(1, Number(structured?.headerRowCount ?? 1));
  const cells = (structured?.cells || []).filter((cell) => !cell.hidden);
  let effectiveHeaderRowCount = Math.min(rows.length || 1, baseHeaderRowCount);

  for (let rowIndex = 0; rowIndex < effectiveHeaderRowCount; rowIndex += 1) {
    const parentCells = cells.filter((cell) => cell.row === rowIndex && (cell.colSpan ?? 1) > 1);
    if (parentCells.length === 0) continue;

    const nextRowIndex = rowIndex + 1;
    if (nextRowIndex >= rows.length) continue;

    const parentRanges = parentCells.map((cell) => {
      const startCol = Math.max(0, Number(cell.col ?? 0));
      return {
        startCol,
        endCol: startCol + Math.max(1, Number(cell.colSpan ?? 1)),
      };
    });
    const isInsideParentRange = (colIndex: number) =>
      parentRanges.some((range) => colIndex >= range.startCol && colIndex < range.endCol);
    const childTextCount = parentRanges.reduce((count, range) => {
      const childCells = cells.filter((cell) => {
        if (cell.row !== nextRowIndex) return false;
        const col = Math.max(0, Number(cell.col ?? 0));
        return col >= range.startCol && col < range.endCol && hasMeaningfulTableText(cell.groundTruth ?? cell.text ?? cell.ocrText);
      });
      const rowChildTexts = (rows[nextRowIndex] || [])
        .slice(range.startCol, range.endCol)
        .filter(hasMeaningfulTableText);
      return count + Math.max(childCells.length, rowChildTexts.length);
    }, 0);
    const outsideTextCount = (rows[nextRowIndex] || []).filter((cell, colIndex) =>
      !isInsideParentRange(colIndex) && hasMeaningfulTableText(cell)
    ).length;

    if (childTextCount >= 2 && outsideTextCount === 0) {
      effectiveHeaderRowCount = Math.max(effectiveHeaderRowCount, nextRowIndex + 1);
    }
  }

  return effectiveHeaderRowCount;
};

const resolveTableHeaderKeys = (rows: string[][], structured?: StructuredTableResult | null) => {
  const headerRowCount = getEffectiveHeaderRowCount(rows, structured);
  const columnCount = Math.max(...rows.map((row) => row.length), 1);
  const headerGrid: string[][] = Array.from({ length: headerRowCount }, () => Array(columnCount).fill(""));

  if (structured?.cells?.length) {
    structured.cells
      .filter((cell) => !cell.hidden && cell.row < headerRowCount)
      .forEach((cell) => {
        const text = normalizeHeaderPart(cell.groundTruth ?? cell.text ?? cell.ocrText);
        if (!text) return;
        const startRow = Math.max(0, Number(cell.row ?? 0));
        const startCol = Math.max(0, Number(cell.col ?? 0));
        const rowSpan = Math.max(1, Number(cell.rowSpan ?? 1));
        const colSpan = Math.max(1, Number(cell.colSpan ?? 1));
        for (let rowOffset = 0; rowOffset < rowSpan; rowOffset += 1) {
          const targetRow = startRow + rowOffset;
          if (targetRow >= headerRowCount) continue;
          for (let colOffset = 0; colOffset < colSpan; colOffset += 1) {
            const targetCol = startCol + colOffset;
            if (targetCol < columnCount) headerGrid[targetRow][targetCol] = text;
          }
        }
      });
  }

  for (let rowIndex = 0; rowIndex < headerRowCount; rowIndex += 1) {
    const row = rows[rowIndex] || [];
    for (let colIndex = 0; colIndex < columnCount; colIndex += 1) {
      if (!headerGrid[rowIndex][colIndex]) {
        headerGrid[rowIndex][colIndex] = normalizeHeaderPart(row[colIndex]);
      }
    }
  }

  return Array.from({ length: columnCount }, (_, colIndex) => {
    const parts = headerGrid
      .map((row) => row[colIndex])
      .filter(Boolean)
      .filter((part, index, array) => array.indexOf(part) === index);
    return parts.join(" ") || `Column ${colIndex + 1}`;
  });
};

const getTableExportConfigForRows = (
  result: OCRResult & { pageIndex?: number },
  rows: string[][],
  structured?: StructuredTableResult | null
): TableExportConfig & { selectedColumns: number[]; selectedRows?: number[]; includeDataRows: boolean; includeSummary: boolean; showRowNumber: boolean } => {
  const headerCount = resolveTableHeaderKeys(rows, structured).length;
  const allColumns = Array.from({ length: headerCount }, (_, index) => index);
  const rawSelected = Array.isArray(result.tableExport?.selectedColumns)
    ? result.tableExport.selectedColumns
    : allColumns;
  return {
    mode: result.tableExport?.mode === "key_value" ? "key_value" : "structure",
    selectedColumns: rawSelected.filter((index) => index >= 0 && index < headerCount),
    selectedRows: Array.isArray(result.tableExport?.selectedRows) ? result.tableExport.selectedRows : undefined,
    includeDataRows: result.tableExport?.includeDataRows !== false,
    includeSummary: result.tableExport?.includeSummary !== false,
    showRowNumber: result.tableExport?.showRowNumber !== false,
  };
};

const normalizedResultRoiId = (value: unknown) => {
  if (value === undefined || value === null) return "";
  const text = String(value).trim();
  return text && text !== "NaN" ? text : "";
};

const getResultPageIndex = (result: OCRResult & { pageIndex?: number }) =>
  Number.isFinite(Number(result.pageIndex)) ? Number(result.pageIndex) : 0;

const getRoiPageIndex = (roi: ROI) =>
  Number.isFinite(Number(roi.pageIndex)) ? Number(roi.pageIndex) : 0;

const resultBelongsToRoi = (result: OCRResult & { pageIndex?: number }, roi: ROI) => {
  const resultRoiId = normalizedResultRoiId(result.roiId);
  return Boolean(resultRoiId) && normalizedResultRoiId(roi.id) === resultRoiId;
};

const findRoiForOcrResult = (rois: ROI[], result: OCRResult & { pageIndex?: number }) => {
  const resultPageIndex = getResultPageIndex(result);
  const pageRois = rois.filter((roi) => getRoiPageIndex(roi) === resultPageIndex);
  const hasResultPage = result.pageIndex !== undefined && Number.isFinite(Number(result.pageIndex));
  return pageRois.find((roi) => resultBelongsToRoi(result, roi))
    || (!hasResultPage ? rois.find((roi) => resultBelongsToRoi(result, roi)) : undefined)
    || pageRois.find((roi) => roi.fieldName === result.fieldName);
};

const normalizeResolvedBlockType = (block: Record<string, any>): "text" | "table" | "image" => {
  const rawType = String(block.data_type || block.dataType || block.type || "").toLowerCase();
  if (rawType === "table") return "table";
  if (rawType === "image") return "image";
  return "text";
};

const createResolvedBlockDisplayRois = (
  parentRoi: ROI & { pageIndex?: number },
  blocks: Record<string, any>[] | undefined | null,
  scaleX: number,
  scaleY: number,
  pageIndex: number,
  startingFieldNumber = 1
): (ROI & { pageIndex?: number })[] => {
  if (!Array.isArray(blocks) || parentRoi.roiMode !== "flexible") return [];
  const resolvedRois: (ROI & { pageIndex?: number })[] = [];
  blocks.forEach((block, index) => {
      const type = normalizeResolvedBlockType(block);
      let localX = 0;
      let localY = 0;
      let localWidth = 0;
      let localHeight = 0;
      const bbox = block.bbox && typeof block.bbox === "object" ? block.bbox : null;
      const roi = block.roi && typeof block.roi === "object" ? block.roi : null;
      if (bbox) {
        localX = Number(bbox.x || 0) / Math.max(scaleX, 1e-6);
        localY = Number(bbox.y || 0) / Math.max(scaleY, 1e-6);
        localWidth = Number(bbox.width || 0) / Math.max(scaleX, 1e-6);
        localHeight = Number(bbox.height || 0) / Math.max(scaleY, 1e-6);
      } else if (roi) {
        localX = Number(roi.x_ratio || 0) * parentRoi.width;
        localY = Number(roi.y_ratio || 0) * parentRoi.height;
        localWidth = Number(roi.width_ratio || 0) * parentRoi.width;
        localHeight = Number(roi.height_ratio || 0) * parentRoi.height;
      }
      if (![localX, localY, localWidth, localHeight].every(Number.isFinite) || localWidth <= 1 || localHeight <= 1) {
        return;
      }
      resolvedRois.push({
        id: Number(`${Math.abs(parentRoi.id)}${index + 1}`.slice(0, 12)) || Date.now() + index,
        fieldName: `field_${startingFieldNumber + resolvedRois.length}`,
        x: parentRoi.x + localX,
        y: parentRoi.y + localY,
        width: localWidth,
        height: localHeight,
        pageIndex,
        type,
        dataType: type,
        extractionMethod: type === "table" ? "table_recognition_v2" : type === "image" ? "extract_image" : "paddle_thai_ocr",
        roiMode: "fix",
        parentRoiId: parentRoi.id,
        isResolvedBlock: true,
        enabled: true,
        layoutType: String(block.layout_type || block.layoutType || type),
      });
    });
  return resolvedRois;
};

async function buildFlexibleResolvedDisplayRois(
  sourceImages: string[],
  sourceRois: (ROI & { pageIndex?: number })[]
): Promise<(ROI & { pageIndex?: number })[]> {
  const byPageImage = new Map<number, HTMLImageElement>();
  const resolved: (ROI & { pageIndex?: number })[] = [];
  let nextFieldNumber = sourceRois.filter(isCountableWorkspaceField).length + 1;
  for (const roi of sourceRois) {
    if (roi.roiMode !== "flexible") continue;
    const pageIndex = Number(roi.pageIndex ?? 0);
    const sourceImage = sourceImages[pageIndex];
    if (!sourceImage) continue;
    let img = byPageImage.get(pageIndex);
    if (!img) {
      img = await loadImageElement(sourceImage);
      byPageImage.set(pageIndex, img);
    }
    const renderedWidth = 750;
    const renderedHeight = (img.naturalHeight / img.naturalWidth) * renderedWidth;
    const scaleX = img.naturalWidth / renderedWidth;
    const scaleY = img.naturalHeight / renderedHeight;
    const boundaryCrop = cropRoiToImage(img, roi, scaleX, scaleY);
    if (!boundaryCrop) continue;
    const regions = await analyzeLayoutForUserImage(boundaryCrop);
    const blocks = regions.length > 0
      ? regions
      : [
          {
            type: "text",
            data_type: "text",
            extraction_method: "paddle_thai_ocr",
            roi: { x_ratio: 0, y_ratio: 0, width_ratio: 1, height_ratio: 1 },
          },
        ];
    const nextResolved = createResolvedBlockDisplayRois(roi, blocks, scaleX, scaleY, pageIndex, nextFieldNumber);
    resolved.push(...nextResolved);
    nextFieldNumber += nextResolved.length;
  }
  return resolved;
}

type TableRowKind = "header" | "data" | "summary" | "empty";
type TableSummaryRegion = {
  detected: boolean;
  rows: Set<number>;
  columns: Set<number>;
  pairs: { row: number; key: string; value: string }[];
};

const hasMeaningfulTableText = (value: unknown) => /[A-Za-z0-9ก-๙]/.test(String(value ?? "").trim());

const medianNumber = (values: number[]) => {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.floor(sorted.length / 2)] || 0;
};

const classifyTableRowsForKeyValue = (
  rows: string[][],
  structured?: StructuredTableResult | null
): TableRowKind[] => {
  const headerRowCount = getEffectiveHeaderRowCount(rows, structured);
  const populatedCounts = rows.map((row) => row.filter(hasMeaningfulTableText).length);
  const bodyCounts = populatedCounts.slice(headerRowCount).filter((count) => count > 0);
  const typicalDataCount = medianNumber(bodyCounts);
  const maxColumns = Math.max(...rows.map((row) => row.length), 1);
  const spannedRows = new Set(
    (structured?.cells || [])
      .filter((cell) => !cell.hidden && ((cell.colSpan ?? 1) > 1 || (cell.rowSpan ?? 1) > 1))
      .map((cell) => cell.row)
  );
  const spanScoreByRow = new Map<number, number>();
  (structured?.cells || [])
    .filter((cell) => !cell.hidden)
    .forEach((cell) => {
      const spanScore = Math.max(1, cell.colSpan ?? 1) * Math.max(1, cell.rowSpan ?? 1) - 1;
      if (spanScore > 0) {
        spanScoreByRow.set(cell.row, (spanScoreByRow.get(cell.row) || 0) + spanScore);
      }
    });
  const lastDataIndex = rows.length - 1;

  return rows.map((row, rowIndex) => {
    if (rowIndex < headerRowCount) return "header";
    const populated = populatedCounts[rowIndex] || 0;
    if (populated === 0) return "empty";

    const populatedIndexes = row
      .map((cell, index) => (hasMeaningfulTableText(cell) ? index : -1))
      .filter((index) => index >= 0);
    const isNearEnd = rowIndex >= Math.max(headerRowCount, Math.floor(rows.length * 0.65)) || rowIndex >= lastDataIndex - 2;
    const isSparse = typicalDataCount >= 3 && populated <= Math.max(1, Math.floor(typicalDataCount * 0.6));
    const hasSpan = spannedRows.has(rowIndex);
    const looksLabelValue =
      populated >= 2 &&
      populated <= Math.max(2, Math.ceil(maxColumns * 0.45)) &&
      populatedIndexes.length >= 2 &&
      populatedIndexes[populatedIndexes.length - 1] - populatedIndexes[0] >= Math.max(1, Math.floor(maxColumns * 0.45));
    const differsFromData = typicalDataCount > 0 && Math.abs(populated - typicalDataCount) >= Math.max(2, Math.ceil(typicalDataCount * 0.4));
    const evidence = [isNearEnd, isSparse || differsFromData, hasSpan, looksLabelValue].filter(Boolean).length;
    return evidence >= 2 ? "summary" : "data";
  });
};

const detectTableSummaryRegion = (
  rows: string[][],
  structured?: StructuredTableResult | null
): TableSummaryRegion => {
  const emptyResult: TableSummaryRegion = { detected: false, rows: new Set(), columns: new Set(), pairs: [] };
  const headerRowCount = getEffectiveHeaderRowCount(rows, structured);
  const maxColumns = Math.max(...rows.map((row) => row.length), 1);
  if (rows.length <= headerRowCount + 1 || maxColumns < 2) return emptyResult;

  const populatedCounts = rows.map((row) => row.filter(hasMeaningfulTableText).length);
  const spannedRows = new Set(
    (structured?.cells || [])
      .filter((cell) => !cell.hidden && ((cell.colSpan ?? 1) > 1 || (cell.rowSpan ?? 1) > 1))
      .map((cell) => cell.row)
  );
  const spanScoreByRow = new Map<number, number>();
  (structured?.cells || [])
    .filter((cell) => !cell.hidden)
    .forEach((cell) => {
      const spanScore = Math.max(1, cell.colSpan ?? 1) * Math.max(1, cell.rowSpan ?? 1) - 1;
      if (spanScore > 0) {
        spanScoreByRow.set(cell.row, (spanScoreByRow.get(cell.row) || 0) + spanScore);
      }
    });

  const transitionStarts = rows
    .map((_, rowIndex) => rowIndex)
    .filter((rowIndex) => {
      if (rowIndex <= headerRowCount || populatedCounts[rowIndex] === 0) return false;
      const previousDataCounts = populatedCounts
        .slice(headerRowCount, rowIndex)
        .filter((count) => count > 0)
        .slice(-5);
      const previousTypicalCount = medianNumber(previousDataCounts);
      const currentCount = populatedCounts[rowIndex];
      return (
        previousTypicalCount >= 3 &&
        currentCount <= Math.max(2, Math.floor(previousTypicalCount * 0.6)) &&
        previousTypicalCount - currentCount >= 2
      );
    });

  const candidates = transitionStarts.flatMap((startRow) => {
    const previousDataCounts = populatedCounts
      .slice(headerRowCount, startRow)
      .filter((count) => count > 0)
      .slice(-5);
    const previousTypicalCount = medianNumber(previousDataCounts);
    const previousSpanScores = Array.from({ length: startRow - headerRowCount }, (_, index) => headerRowCount + index)
      .filter((rowIndex) => populatedCounts[rowIndex] > 0)
      .slice(-5)
      .map((rowIndex) => spanScoreByRow.get(rowIndex) || 0);
    const previousTypicalSpanScore = medianNumber(previousSpanScores);
    const sparseLimit = Math.max(2, Math.floor(previousTypicalCount * 0.65));
    const candidateRows: number[] = [];
    for (let rowIndex = startRow; rowIndex < rows.length; rowIndex += 1) {
      const populated = populatedCounts[rowIndex];
      if (populated === 0) continue;
      if (populated > sparseLimit && candidateRows.length > 0) break;
      if (populated <= sparseLimit) candidateRows.push(rowIndex);
    }

    return Array.from({ length: Math.max(0, maxColumns - 1) }, (_, labelCol) =>
      Array.from({ length: maxColumns - labelCol - 1 }, (_, offset) => {
        const valueCol = labelCol + offset + 1;
        const pairs = candidateRows
          .map((rowIndex) => ({
            row: rowIndex,
            key: String(rows[rowIndex]?.[labelCol] ?? "").trim(),
            value: String(rows[rowIndex]?.[valueCol] ?? "").trim(),
          }))
          .filter((pair) => hasMeaningfulTableText(pair.key) && hasMeaningfulTableText(pair.value));
        const transitionEvidence = pairs.length > 0;
        const nearTailEvidence = startRow >= Math.max(headerRowCount, Math.floor(rows.length * 0.55)) || startRow >= rows.length - 4;
        const labelValueEvidence = pairs.length >= 1;
        const continuityEvidence = pairs.length >= 2;
        const spanEvidence = pairs.some((pair) => spannedRows.has(pair.row));
        const spanStructureChangeEvidence = pairs.some((pair) => (spanScoreByRow.get(pair.row) || 0) > previousTypicalSpanScore);
        const stableColumnsEvidence = pairs.every((pair) => hasMeaningfulTableText(rows[pair.row]?.[labelCol]) && hasMeaningfulTableText(rows[pair.row]?.[valueCol]));
        const compactRegionEvidence = pairs.every((pair) => {
          const populatedIndexes = rows[pair.row]
            .map((cell, index) => (hasMeaningfulTableText(cell) ? index : -1))
            .filter((index) => index >= 0);
          const regionIndexes = populatedIndexes.filter((index) => index >= labelCol && index <= valueCol);
          const leftIndexes = populatedIndexes.filter((index) => index < labelCol);
          return regionIndexes.length >= 2 && leftIndexes.length <= 1;
        });
        const evidence = [
          transitionEvidence,
          nearTailEvidence,
          labelValueEvidence,
          continuityEvidence,
          spanEvidence,
          spanStructureChangeEvidence,
          stableColumnsEvidence,
          compactRegionEvidence,
        ].filter(Boolean).length;
        return { labelCol, valueCol, pairs, evidence, startRow, previousTypicalCount };
      })
    ).flat();
  });

  const best = candidates
    .filter((candidate) => candidate.pairs.length >= 1 && candidate.evidence >= 4)
    .sort((left, right) =>
      right.evidence - left.evidence ||
      right.pairs.length - left.pairs.length ||
      right.previousTypicalCount - left.previousTypicalCount ||
      right.valueCol - left.valueCol ||
      right.labelCol - left.labelCol
    )[0];
  if (!best) return emptyResult;

  return {
    detected: true,
    rows: new Set(best.pairs.map((pair) => pair.row)),
    columns: new Set(Array.from({ length: best.valueCol - best.labelCol + 1 }, (_, index) => best.labelCol + index)),
    pairs: best.pairs,
  };
};

const tableRowsToKeyValueRecords = (
  rows: string[][],
  selectedColumns: number[],
  structured?: StructuredTableResult | null,
  includeDataRows = true,
  selectedRows?: number[]
) => {
  if (!includeDataRows) return [];
  const headers = resolveTableHeaderKeys(rows, structured);
  const headerRowCount = getEffectiveHeaderRowCount(rows, structured);
  const rowKinds = classifyTableRowsForKeyValue(rows, structured);
  const summaryRegion = detectTableSummaryRegion(rows, structured);
  const selectedRowSet = Array.isArray(selectedRows) ? new Set(selectedRows) : null;
  return rows.slice(headerRowCount).map((row, rowOffset) => {
    const rowIndex = headerRowCount + rowOffset;
    return {
      rowIndex,
      row: rowIndex + 1,
      rowType: summaryRegion.rows.has(rowIndex) ? "summary" : rowKinds[rowIndex],
      values: Object.fromEntries(
        selectedColumns.map((columnIndex) => [
          headers[columnIndex] || `Column ${columnIndex + 1}`,
          row[columnIndex] ?? "",
        ])
      ),
    };
  }).filter((record) => record.rowType === "data" && (!selectedRowSet || selectedRowSet.has(record.rowIndex)));
};

const getTableKeyValueDataRows = (rows: string[][], structured?: StructuredTableResult | null) =>
  tableRowsToKeyValueRecords(
    rows,
    resolveTableHeaderKeys(rows, structured).map((_, index) => index),
    structured,
    true
  ).map((record) => ({ rowIndex: record.rowIndex, label: `แถวที่ ${record.row}` }));

const tableRowsToSummaryKeyValuePairs = (rows: string[][], structured?: StructuredTableResult | null) =>
  detectTableSummaryRegion(rows, structured).pairs.map((pair) => ({
    row: pair.row,
    key: pair.key,
    value: pair.value,
  }));

const tableRowsToKeyValueDisplayRows = (
  rows: string[][],
  selectedColumns: number[],
  structured?: StructuredTableResult | null,
  includeDataRows = true,
  includeSummary = true,
  showRowNumber = true,
  selectedRows?: number[]
) => {
  const records = tableRowsToKeyValueRecords(rows, selectedColumns, structured, includeDataRows, selectedRows);
  const summaryPairs = includeSummary ? tableRowsToSummaryKeyValuePairs(rows, structured) : [];
  const dataRows = records.flatMap((record, recordIndex) => {
    const valueRows = Object.entries(record.values).map(([key, value]) => [key, String(value ?? "")]);
    return [
      ...(showRowNumber ? [["แถวที่", String(recordIndex + 1)]] : []),
      ...valueRows,
      ["", ""],
    ];
  });
  const summaryRows = summaryPairs.length > 0
    ? [["ส่วนสรุป", ""], ...summaryPairs.map((pair) => [pair.key, pair.value])]
    : [];
  return [...dataRows, ...summaryRows];
};

const rowsFromStructuredCells = (structured?: StructuredTableResult | null): string[][] | null => {
  const cells = structured?.cells;
  if (!Array.isArray(cells) || cells.length === 0) return null;
  const maxRow = Math.max(...cells.map(cell => Number(cell.row ?? 0) + Math.max(1, Number(cell.rowSpan ?? 1)) - 1));
  const maxCol = Math.max(...cells.map(cell => Number(cell.col ?? 0) + Math.max(1, Number(cell.colSpan ?? 1)) - 1));
  if (!Number.isFinite(maxRow) || !Number.isFinite(maxCol) || maxRow < 0 || maxCol < 0) return null;
  const rows = Array.from({ length: maxRow + 1 }, () => Array(maxCol + 1).fill(""));
  for (const cell of cells) {
    if (cell.hidden) continue;
    const row = Math.max(0, Number(cell.row ?? 0));
    const col = Math.max(0, Number(cell.col ?? 0));
    rows[row][col] = String(cell.groundTruth ?? cell.text ?? cell.ocrText ?? "");
  }
  return rows;
};

const tableRowsToMarkdown = (rows: string[][]) => {
  if (!rows.length) return "";
  const columnCount = Math.max(...rows.map((row) => row.length), 1);
  const normalizedRows = rows.map((row) => [...row.map((cell) => String(cell ?? "")), ...Array(columnCount - row.length).fill("")]);
  const [header = [], ...bodyRows] = normalizedRows;
  const safeHeader = header.map((cell, index) => cell || `Column ${index + 1}`);
  const formatRow = (row: string[]) => `| ${row.map((cell) => cell.replace(/\|/g, "/")).join(" | ")} |`;
  return [formatRow(safeHeader), formatRow(Array(columnCount).fill("---")), ...bodyRows.map(formatRow)].join("\n");
};

const createEmptyStructuredTable = (): StructuredTableResult => ({
  rows: [["Column 1"], [""]],
  cells: [
    { row: 0, col: 0, text: "Column 1", rowSpan: 1, colSpan: 1, ocrText: "Column 1", groundTruth: "Column 1" },
    { row: 1, col: 0, text: "", rowSpan: 1, colSpan: 1, ocrText: "", groundTruth: "" },
  ],
  headerRowCount: 1,
});

const structuredTableFromRows = (
  rows: string[][],
  sourceStructured?: StructuredTableResult | null
): StructuredTableResult => {
  const columnCount = Math.max(...rows.map((row) => row.length), 1);
  const normalizedRows = rows.map((row) => [
    ...row.map((cell) => String(cell ?? "")),
    ...Array(columnCount - row.length).fill(""),
  ]);
  const sourceCells = new Map((sourceStructured?.cells || []).map((cell) => [`${cell.row}:${cell.col}`, cell]));
  return {
    ...sourceStructured,
    rows: normalizedRows,
    cells: normalizedRows.flatMap((row, rowIndex) =>
      row.map((text, colIndex) => {
        const sourceCell = sourceCells.get(`${rowIndex}:${colIndex}`);
        return {
          row: rowIndex,
          col: colIndex,
          text,
          rowSpan: sourceCell?.rowSpan ?? 1,
          colSpan: sourceCell?.colSpan ?? 1,
          bbox: sourceCell?.bbox,
          ocrText: sourceCell?.ocrText ?? sourceCell?.text ?? text,
          groundTruth: sourceCell?.groundTruth ?? text,
          hidden: sourceCell?.hidden ?? false,
        };
      })
    ),
    headerRowCount: sourceStructured?.headerRowCount ?? 1,
  };
};

const parseHtmlTableRows = (value?: string): string[][] | null => parseHtmlTableStructured(value)?.rows || null;

const parseHtmlTableStructured = (value?: string): StructuredTableResult | null => {
  if (!value || !value.toLowerCase().includes("<table")) return null;
  try {
    const doc = new DOMParser().parseFromString(value, "text/html");
    const rows: string[][] = [];
    const cells: NonNullable<StructuredTableResult["cells"]> = [];
    const occupied = new Set<string>();
    const rowElements = Array.from(doc.querySelectorAll("tr"));

    rowElements.forEach((row, rowIndex) => {
      rows[rowIndex] = rows[rowIndex] || [];
      let colIndex = 0;
      Array.from(row.querySelectorAll("th,td")).forEach((cell) => {
        while (occupied.has(`${rowIndex}:${colIndex}`)) colIndex += 1;
        const text = (cell.textContent || "").replace(/\s+/g, " ").trim();
        const colSpan = Math.max(1, Number(cell.getAttribute("colspan") || 1));
        const rowSpan = Math.max(1, Number(cell.getAttribute("rowspan") || 1));
        rows[rowIndex][colIndex] = text;
        cells.push({
          row: rowIndex,
          col: colIndex,
          text,
          rowSpan,
          colSpan,
          ocrText: text,
          groundTruth: text,
          hidden: false,
        });

        for (let rowOffset = 0; rowOffset < rowSpan; rowOffset += 1) {
          const targetRow = rowIndex + rowOffset;
          rows[targetRow] = rows[targetRow] || [];
          for (let colOffset = 0; colOffset < colSpan; colOffset += 1) {
            const targetCol = colIndex + colOffset;
            rows[targetRow][targetCol] = rows[targetRow][targetCol] ?? "";
            occupied.add(`${targetRow}:${targetCol}`);
            if (rowOffset !== 0 || colOffset !== 0) {
              cells.push({
                row: targetRow,
                col: targetCol,
                text: "",
                rowSpan: 1,
                colSpan: 1,
                ocrText: "",
                groundTruth: "",
                hidden: true,
              });
            }
          }
        }
        colIndex += colSpan;
      });
    });

    const rowCountFromCells = cells.reduce(
      (max, cell) => Math.max(max, Number(cell.row ?? 0) + Math.max(1, Number(cell.rowSpan ?? 1))),
      0
    );
    const columnCountFromCells = cells.reduce(
      (max, cell) => Math.max(max, Number(cell.col ?? 0) + Math.max(1, Number(cell.colSpan ?? 1))),
      0
    );
    const usefulRows = Array.from({ length: Math.max(rows.length, rowCountFromCells) }, (_, index) => rows[index] || []);
    if (usefulRows.length === 0 && cells.length === 0) return null;
    const columnCount = Math.max(...usefulRows.map((row) => row.length), columnCountFromCells, 1);
    return {
      rows: usefulRows.map((row) => [...row.map((cell) => String(cell ?? "")), ...Array(columnCount - row.length).fill("")]),
      cells,
      headerRowCount: Math.max(1, doc.querySelectorAll("thead tr").length || 1),
    };
  } catch {
    return null;
  }
};

const assignExportField = (fields: Record<string, unknown>, name: string, value: unknown) => {
  if (!(name in fields)) {
    fields[name] = value;
    return;
  }
  fields[name] = Array.isArray(fields[name]) ? [...fields[name], value] : [fields[name], value];
};

const wait = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms));

async function runAiProcessJob(payload: Record<string, unknown>) {
  const response = await fetch(`${ADMIN_API_BASE_URL}/api/ai/process`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ ...payload, async_mode: true }),
  });
  const created = await response.json();
  if (!response.ok || !created.success) {
    throw new Error(created?.detail || created?.error || "สร้าง OCR Job ไม่สำเร็จ");
  }
  if (!created.job_id) {
    return created;
  }

  for (;;) {
    await wait(2500);
    const pollResponse = await fetch(`${ADMIN_API_BASE_URL}/api/ai/jobs/${created.job_id}`, {
      headers: authHeaders(),
    });
    const job = await pollResponse.json();
    if (!pollResponse.ok || !job.success) {
      throw new Error(job?.detail || job?.error || "ตรวจสถานะ OCR Job ไม่สำเร็จ");
    }
    if (job.status === "completed") {
      return job.result;
    }
    if (job.status === "failed") {
      throw new Error(job.error || "OCR Job ล้มเหลว");
    }
  }
}

function HomeWorkspace() {
  const router = useRouter();
  const [authSession, setAuthSession] = useState<AuthSession | null>(null);
  const [currentStep, setCurrentStep] = useState<"upload" | "adjust" | "studio" | "editor">("upload");
  const [imagesList, setImagesList] = useState<string[]>([]);
  const [originalImagesList, setOriginalImagesList] = useState<string[]>([]);
  const [uploadedSourceFileId, setUploadedSourceFileId] = useState<string>("");
  const [uploadedSourceFileName, setUploadedSourceFileName] = useState<string>("");
  const [uploadedSourceFileType, setUploadedSourceFileType] = useState<"pdf" | "image" | "">("");
  const [uploadedSourceFile, setUploadedSourceFile] = useState<File | null>(null);

  const [currentIndex, setCurrentIndex] = useState<number>(0);
  const [pagesConfig, setPagesConfig] = useState<PageConfig[]>([]);
  const [previewUrl, setPreviewUrl] = useState<string>("");
  const [image, setImage] = useState<string | null>(null);

  const [rois, setRois] = useState<(ROI & { pageIndex?: number })[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [ocrResults, setOcrResults] = useState<(OCRResult & { pageIndex?: number })[]>([]);
  const ocrRunIdRef = useRef(0);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isTemplateRequestOpen, setIsTemplateRequestOpen] = useState<boolean>(false);
  const [ocrProgress, setOcrProgress] = useState<{ currentPage: number; totalPages: number; completedPages?: number } | null>(null);
  const [classificationStatus, setClassificationStatus] = useState<string>("");
  const [templateDetectionNotice, setTemplateDetectionNotice] = useState<TemplateDetectionNotice | null>(null);
  const [, setOperationNotice] = useState<{ tone: NoticeTone; title: string; message: string } | null>(null);
  const [isTemplateDecisionOpen, setIsTemplateDecisionOpen] = useState<boolean>(false);
  const [templateDecisionStatus, setTemplateDecisionStatus] = useState<string>("");
  const [exportJson, setExportJson] = useState<string>("");
  const [exportPreviewJsonText, setExportPreviewJsonText] = useState<string>("");
  const [exportText, setExportText] = useState<string>("");
  const [copyStatus, setCopyStatus] = useState<string>("");
  const [isExportMenuOpen, setIsExportMenuOpen] = useState<boolean>(false);
  const [exportFormat, setExportFormat] = useState<ExportFormat>("word");
  const [exportContent, setExportContent] = useState<ExportContentOptions>({ text: true, tables: true, images: true });
  const [exportOptions, setExportOptions] = useState<ExportDisplayOptions>({ showFieldNames: true, showDocumentTitle: true });
  const [openTableExportDropdown, setOpenTableExportDropdown] = useState<string | null>(null);
  const [excelExportMode, setExcelExportMode] = useState<"fields" | "tables" | "fields_tables">("fields_tables");
  const [textPreviewCopyStatus, setTextPreviewCopyStatus] = useState<string>("");
  const [matchedTemplate, setMatchedTemplate] = useState<{
    id: string;
    name: string;
    confidence?: number | null;
    decisionReason?: string | null;
    alignmentStatus?: string | null;
  } | null>(null);
  const tableExportDropdownRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const session = readAuthSession();
    if (session?.role === "admin") {
      router.replace("/admin");
      return;
    }
    setAuthSession(session);
  }, [router]);

  const handleLogout = () => {
    clearAuthSession();
    router.replace("/login");
  };

  useEffect(() => {
    if (!openTableExportDropdown) return;
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (tableExportDropdownRef.current?.contains(event.target as Node)) return;
      setOpenTableExportDropdown(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpenTableExportDropdown(null);
    };
    document.addEventListener("mousedown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [openTableExportDropdown]);

  const handleUploadSuccess = (urls: string[], sourceFileName?: string, sourceFileType?: "pdf" | "image", sourceFile?: File) => {
    setUploadedSourceFileId(`user_file_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`);
    setUploadedSourceFileName(sourceFileName || "ไฟล์ต้นทาง");
    setUploadedSourceFileType(sourceFileType || (sourceFileName?.toLowerCase().endsWith(".pdf") ? "pdf" : "image"));
    setUploadedSourceFile(sourceFile || null);
    setImagesList(urls);
    setOriginalImagesList([...urls]);
    setCurrentIndex(0);
    setPreviewUrl(urls[0] || "");
    setImage(urls[0] || null);
    setRois([]);
    setSelectedId(null);
    setOcrResults([]);
    setClassificationStatus("");
    setTemplateDetectionNotice(null);
    setOperationNotice(null);
    setIsTemplateDecisionOpen(false);
    setTemplateDecisionStatus("");
    setMatchedTemplate(null);
    setPagesConfig(
      urls.map(() => ({
        rotation: 0,
        brightness: 100,
        contrast: 100,
        sharpness: 0,
        perspectiveV: 0,
        perspectiveH: 0,
        flipH: false,
        flipV: false,
        cropBox: null,
        cropCorners: null,
        isCropActive: false,
        isCropped: false,
        croppedLocalUrl: null,
      }))
    );
    setCurrentStep("adjust");
  };

  const handleClearAndUploadNew = () => {
    setImagesList([]);
    setOriginalImagesList([]);
    setUploadedSourceFileId("");
    setUploadedSourceFileName("");
    setUploadedSourceFileType("");
    setUploadedSourceFile(null);
    setCurrentIndex(0);
    setPagesConfig([]);
    setPreviewUrl("");
    setImage(null);
    setRois([]);
    setSelectedId(null);
    setOcrResults([]);
    setClassificationStatus("");
    setTemplateDetectionNotice(null);
    setOperationNotice(null);
    setIsTemplateDecisionOpen(false);
    setTemplateDecisionStatus("");
    setMatchedTemplate(null);
    setTemplateDetectionNotice(null);
    setCurrentStep("upload");
  };

  const handleBatchConfirm = async (finalProcessedImages: string[]) => {
    setImagesList(finalProcessedImages);
    setPreviewUrl(finalProcessedImages[currentIndex] || finalProcessedImages[0] || "");
    setImage(finalProcessedImages[currentIndex] || finalProcessedImages[0] || null);
    setRois([]);
    setSelectedId(null);
    setOcrResults([]);
    setMatchedTemplate(null);
    setOperationNotice(null);
    setCurrentStep("studio");
    setIsTemplateDecisionOpen(true);
    setTemplateDecisionStatus("กำลังเตรียมภาพที่ยืนยันขอบเขตแล้ว");
    setClassificationStatus("กำลังแยกประเภทเอกสารจากภาพที่ยืนยันขอบเขตแล้ว...");

    const firstImage = finalProcessedImages[0];
    if (!firstImage) {
      setClassificationStatus("ไม่พบภาพสำหรับแยกประเภทเอกสาร ระบบเปิด Custom OCR ให้ใช้งานต่อ");
      setTemplateDetectionNotice({
        title: "ไม่พบภาพสำหรับแยกประเภทเอกสาร",
        message: "ระบบไม่สามารถเริ่มค้นหา Template ได้ เพราะไม่มีภาพที่ยืนยันขอบเขตแล้ว",
        detail: "โปรดกลับไปตรวจสอบภาพ หรือใช้งาน Custom OCR ต่อ",
      });
      setIsTemplateDecisionOpen(false);
      setTemplateDecisionStatus("");
      return;
    }

    try {
      setTemplateDecisionStatus("กำลังค้นหา Template ที่ใกล้เคียงที่สุด");
      let detection: DetectionDevResult;
      try {
        const shouldUseOriginalPdfForDetection =
          uploadedSourceFileType === "pdf" &&
          uploadedSourceFile &&
          (uploadedSourceFile.type === "application/pdf" || uploadedSourceFile.name.toLowerCase().endsWith(".pdf"));
        const detectionInput = shouldUseOriginalPdfForDetection
          ? uploadedSourceFile
          : await Promise.all(finalProcessedImages.map((src, index) => dataUrlToFile(src, `confirmed-document-page-${index + 1}.jpg`)));
        detection = await detectTemplateDev(Array.isArray(detectionInput) && detectionInput.length === 1 ? detectionInput[0] : detectionInput);
        devTemplateFlowLog("detection completed", {
          matched: detection.matched,
          candidateCount: detection.candidates.length,
          pageCount: detection.pages.length,
          bestTemplateId: detection.bestCandidate?.templateId ?? null,
          usedOriginalPdf: shouldUseOriginalPdfForDetection,
        });
      } catch (error) {
        throw contextualTemplateError("Template detection mapping failed", error);
      }

      const templateId = detection.bestCandidate?.templateId;

      if (!detection.matched || !templateId) {
        setClassificationStatus("ไม่พบ Template ที่มั่นใจพอ ระบบเปิด Custom OCR ให้ใช้งานต่อ");
        setTemplateDetectionNotice({
          title: "ไม่พบ Template ที่มั่นใจพอ",
          message: detection.message || "คะแนนการจับคู่ยังไม่ผ่านเกณฑ์ที่กำหนด",
          detail: "ไม่โหลด ROI จาก Template ใด ๆ",
        });
        return;
      }

      setTemplateDecisionStatus("พบ Template แล้ว กำลังโหลดโครงสร้าง ROI");
      setTemplateDetectionNotice(null);
      let bundle: Awaited<ReturnType<typeof fetchTemplateBundle>>;
      try {
        bundle = await fetchTemplateBundle(templateId);
        devTemplateFlowLog("bundle loaded", {
          templateId: bundle.template.id,
          fieldCount: bundle.fields.length,
          pageCount: bundle.pages.length,
        });
      } catch (error) {
        throw contextualTemplateError("Template bundle loading failed", error);
      }

      const keepOriginalPdfPages =
        uploadedSourceFileType === "pdf" || uploadedSourceFileName.toLowerCase().endsWith(".pdf");
      setTemplateDecisionStatus(
        keepOriginalPdfPages
          ? "พบ Template แล้ว กำลังเตรียมกรอบ OCR บนหน้า PDF เดิม"
          : "กำลังจัดภาพให้ตรงกับ Template และเตรียมกรอบ OCR"
      );
      let templateCanvasImages: string[];
      try {
        templateCanvasImages = await buildTemplateCanvasImages(finalProcessedImages, detection, templateId, {
          keepOriginalImages: keepOriginalPdfPages,
        });
        devTemplateFlowLog("canvas images prepared", {
          pageCount: templateCanvasImages.length,
          replacedCount: templateCanvasImages.filter((src, index) => src !== finalProcessedImages[index]).length,
          keptOriginalPdfPages: keepOriginalPdfPages,
        });
      } catch (error) {
        throw contextualTemplateError("Template canvas preparation failed", error);
      }

      setImagesList(templateCanvasImages);
      setPreviewUrl(templateCanvasImages[currentIndex] || templateCanvasImages[0] || "");
      setImage(templateCanvasImages[currentIndex] || templateCanvasImages[0] || null);

      let detectedRois: (ROI & { pageIndex?: number; roiCoordinateSource?: string })[];
      try {
        const isMainPageDetection = bundle.template.detectionMode === "main_page";
        const matchedQueryPageIndex = Math.max(
          0,
          Number(
            detection.bestCandidate?.pageIndex ??
            detection.pages.find((page) => page.bestCandidate?.templateId === templateId || page.matched)?.pageIndex ??
            1
          ) - 1
        );
        detectedRois = await templateFieldsToWorkspaceRois(bundle.fields, templateCanvasImages, detection, templateId, {
          templatePageToImageIndex: isMainPageDetection ? { 1: matchedQueryPageIndex } : undefined,
        });
        const flexibleResolvedRois = await buildFlexibleResolvedDisplayRois(templateCanvasImages, detectedRois);
        if (flexibleResolvedRois.length > 0) {
          const resolvedParentIds = new Set(flexibleResolvedRois.map((roi) => roi.parentRoiId).filter((id): id is number => typeof id === "number"));
          detectedRois = [
            ...detectedRois.map((roi) => resolvedParentIds.has(roi.id) ? { ...roi, enabled: false } : roi),
            ...flexibleResolvedRois,
          ];
        }
        if (isMainPageDetection && templateCanvasImages.length > 1) {
          const extraPageAutoRois = await buildWholePageAutoRois(
            templateCanvasImages,
            detectedRois,
            new Set([matchedQueryPageIndex])
          );
          detectedRois = [...detectedRois, ...extraPageAutoRois];
        }
        devTemplateFlowLog("ROIs mapped", {
          templateId,
          roiCount: detectedRois.length,
          flexibleResolvedRoiCount: flexibleResolvedRois.length,
          firstRoi: detectedRois[0]
            ? {
                fieldName: detectedRois[0].fieldName,
                pageIndex: detectedRois[0].pageIndex,
                x: detectedRois[0].x,
                y: detectedRois[0].y,
                width: detectedRois[0].width,
                height: detectedRois[0].height,
              }
            : null,
        });
      } catch (error) {
        throw contextualTemplateError("Template ROI mapping failed", error);
      }

      setMatchedTemplate({
        id: bundle.template.id,
        name: bundle.template.name,
        confidence: detection.bestCandidate?.finalScore ?? detection.bestCandidate?.score ?? null,
        decisionReason: detection.bestCandidate?.decisionReason ?? null,
        alignmentStatus: detection.bestCandidate?.alignmentStatus ?? null,
      });

      if (detectedRois.length === 0) {
        setClassificationStatus(`ตรวจพบ Template: ${bundle.template.name} แต่ยังไม่มี Extraction ROI ให้ใช้งาน`);
        return;
      }

      setRois(detectedRois);
      setSelectedId(detectedRois[0]?.id ?? null);
      setClassificationStatus(`ตรวจพบ Template: ${bundle.template.name} และโหลด ROI สำหรับ OCR แล้ว`);
    } catch (error) {
      console.warn("Document classification after boundary confirmation failed.", error);
      setClassificationStatus("ตรวจจับ Template ไม่สำเร็จ ระบบเปิด Custom OCR ให้ใช้งานต่อ");
      setTemplateDetectionNotice({
        title: "ตรวจจับ Template ไม่สำเร็จ",
        message: error instanceof Error ? error.message : "ระบบค้นหา Template ไม่สำเร็จ",
        detail: "ระบบเปิด Custom OCR ให้ใช้งานต่อ",
      });
    } finally {
      setIsTemplateDecisionOpen(false);
      setTemplateDecisionStatus("");
    }
  };

  const handleRunOCR = async () => {
    const runId = ocrRunIdRef.current + 1;
    ocrRunIdRef.current = runId;
    const activeRois = rois.filter((roi) => roi.enabled !== false);
    if (activeRois.length === 0) {
      setOperationNotice({
        tone: "warning",
        title: "ยังไม่มีข้อมูลให้ OCR",
        message: "กรุณาเลือกหรือเปิดใช้งาน ROI อย่างน้อย 1 กล่องก่อนอ่านข้อมูล",
      });
      return;
    }

    setIsLoading(true);
    setOcrResults([]);
    setOperationNotice(null);
    setOcrProgress({ currentPage: 0, totalPages: imagesList.length, completedPages: 0 });

    try {
      const combinedResults: (OCRResult & { pageIndex?: number })[] = [];

      for (let pageIdx = 0; pageIdx < imagesList.length; pageIdx += 1) {
        if (ocrRunIdRef.current !== runId) return;
        setOcrProgress({ currentPage: pageIdx + 1, totalPages: imagesList.length, completedPages: pageIdx });
        const pageRois = rois.filter(
          (roi) => roi.enabled !== false && (roi.pageIndex !== undefined ? Number(roi.pageIndex) : 0) === pageIdx
        );

        if (pageRois.length === 0) {
          setOcrProgress({ currentPage: pageIdx + 1, totalPages: imagesList.length, completedPages: pageIdx + 1 });
          continue;
        }

        const currentImgUrl = imagesList[pageIdx];
        const img = await loadImageElement(currentImgUrl);

        const renderedWidth = 750;
        const renderedHeight = (img.naturalHeight / img.naturalWidth) * renderedWidth;

        const scaleX = img.naturalWidth / renderedWidth;
        const scaleY = img.naturalHeight / renderedHeight;

        const roiPromises = pageRois.map(async (roi, rIdx) => {
          const croppedBase64 = cropRoiToImage(img, roi, scaleX, scaleY);
          if (!croppedBase64) return null;
          const roiFieldType = getRoiFieldType(roi);
          const roiExtractionMethod = getRoiExtractionMethod(roi);
          const isTableRoi = roiFieldType === "table";
          const createTablePlaceholderResult = (message: string): OCRResult & { pageIndex?: number } => {
            const emptyStructured = createEmptyStructuredTable();
            return {
              id: Date.now() + pageIdx * 100000 + rIdx + Math.floor(Math.random() * 1000000),
              roiId: roi.id,
              fieldName: roi.fieldName,
              bbox: [],
              extractedText: JSON.stringify(emptyStructured, null, 2),
              originalText: message,
              confidence: 0,
              saved_path: "",
              pageIndex: pageIdx,
              type: "table",
              dataType: "table",
              role: roi.role || "data_extraction",
              weight: roi.weight !== undefined ? roi.weight : 1.0,
              points: roi.points,
              tableRows: emptyStructured.rows,
              tableStructured: emptyStructured,
              tableDebug: { status: "table_placeholder", message },
            };
          };

          try {
            const aiData = await runAiProcessJob({
              image: croppedBase64,
              rois: [
                {
                  fieldName: roi.fieldName,
                  roiId: roi.id,
                  x: 0,
                  y: 0,
                  width: roi.width * scaleX,
                  height: roi.height * scaleY,
                  type: roiFieldType,
                  extractionMethod: roiExtractionMethod,
                  roiMode: roi.roiMode === "flexible" ? "flexible" : "fix",
                  expectedContent: roi.roiMode === "flexible" ? "text" : null,
                },
              ],
            });
            if (aiData.success && aiData.extracted_data.length > 0) {
              const resItem = aiData.extracted_data[0];
              const parsedHtmlStructured = parseHtmlTableStructured(typeof resItem.table_html === "string" ? resItem.table_html : undefined);
              const responseStructured =
                resItem.table_structured && typeof resItem.table_structured === "object"
                  ? (resItem.table_structured as StructuredTableResult)
                  : resItem.tableStructured && typeof resItem.tableStructured === "object"
                    ? (resItem.tableStructured as StructuredTableResult)
                    : parsedHtmlStructured || undefined;
              const responseRowsFromCells = rowsFromStructuredCells(responseStructured);
              const responseTableRows = Array.isArray(resItem.table_rows)
                ? resItem.table_rows.map((row: unknown) => (Array.isArray(row) ? row.map((cell) => String(cell ?? "")) : []))
                : null;
              const rawTableRows =
                responseRowsFromCells ||
                responseStructured?.rows ||
                responseTableRows ||
                parseHtmlTableRows(typeof resItem.table_html === "string" ? resItem.table_html : undefined);
              const finalTableStructured = isTableRoi
                ? responseStructured?.cells?.length
                  ? { ...responseStructured, rows: rawTableRows || responseStructured.rows || [] }
                  : rawTableRows
                    ? structuredTableFromRows(rawTableRows, responseStructured)
                    : responseStructured || createEmptyStructuredTable()
                : responseStructured;
              const finalTableRows = isTableRoi ? rawTableRows || finalTableStructured?.rows || [["Column 1"], [""]] : rawTableRows;
              const tableMarkdown = rawTableRows && rawTableRows.length > 0 ? tableRowsToMarkdown(rawTableRows) : "";
              const extractedText = String(resItem.text || tableMarkdown || "");
              const responseRoiId = Number(resItem.roiId ?? roi.id);
              const resolvedBlocks = Array.isArray(resItem.resolved_blocks)
                ? (resItem.resolved_blocks as Record<string, any>[])
                : Array.isArray(resItem.resolvedBlocks)
                  ? (resItem.resolvedBlocks as Record<string, any>[])
                  : [];
              const result = {
                id: Date.now() + pageIdx * 100000 + rIdx + Math.floor(Math.random() * 1000000),
                roiId: Number.isFinite(responseRoiId) ? responseRoiId : roi.id,
                fieldName: roi.fieldName,
                bbox: [],
                extractedText,
                originalText: extractedText,
                confidence: resItem.confidence,
                saved_path: resItem.saved_path || "",
                pageIndex: pageIdx,
                type: roiFieldType,
                dataType: roiFieldType,
                role: roi.role || "data_extraction",
                weight: roi.weight !== undefined ? roi.weight : 1.0,
                points: roi.points,
                tableRows: finalTableRows || undefined,
                tableStructured: finalTableStructured,
                tableHtml: typeof resItem.table_html === "string" ? resItem.table_html : undefined,
                tableDebug: resItem.table_debug && typeof resItem.table_debug === "object" ? resItem.table_debug : undefined,
                resolvedBlocks,
              };
              if (roi.roiMode === "flexible" && resolvedBlocks.length > 0) {
                const blockRois = createResolvedBlockDisplayRois(roi, resolvedBlocks, scaleX, scaleY, pageIdx);
                if (blockRois.length > 0) {
                  setRois((previous) => [
                    ...previous.filter((item) => !(item.isResolvedBlock && item.parentRoiId === roi.id)),
                    ...blockRois,
                  ]);
                }
              }
              return result;
            }
            if (isTableRoi) {
              return createTablePlaceholderResult(aiData?.detail || aiData?.error || "Table Recognition did not return table data.");
            }
          } catch (innerErr) {
            console.error(`Error processing ROI ${roi.fieldName}:`, innerErr);
            if (isTableRoi) {
              return createTablePlaceholderResult(innerErr instanceof Error ? innerErr.message : "Table Recognition failed.");
            }
          }
          return null;
        });

        const roiResults = await Promise.all(roiPromises);
        if (ocrRunIdRef.current !== runId) return;
        combinedResults.push(...(roiResults.filter((r) => r !== null) as (OCRResult & { pageIndex?: number })[]));
        setOcrProgress({ currentPage: pageIdx + 1, totalPages: imagesList.length, completedPages: pageIdx + 1 });
      }

      if (combinedResults.length > 0) {
        if (ocrRunIdRef.current !== runId) return;
        setOcrResults(combinedResults);
        setCurrentIndex(0);
        setCurrentStep("editor");
      } else {
        setOperationNotice({
          tone: "warning",
          title: "ไม่พบผล OCR",
          message: "ระบบอ่านข้อมูลจาก ROI ที่เลือกไม่ได้ กรุณาตรวจสอบตำแหน่ง ROI หรือสถานะ OCR engine",
        });
      }
    } catch (err) {
      console.error(err);
      setOperationNotice({
        tone: "danger",
        title: "อ่านข้อมูลไม่สำเร็จ",
        message: err instanceof Error ? err.message : "เกิดข้อผิดพลาดในการประมวลผล OCR",
      });
    } finally {
      if (ocrRunIdRef.current === runId) {
        setIsLoading(false);
        setOcrProgress(null);
      }
    }
  };

  const handleRunFullPageOCR = async () => {
    const runId = ocrRunIdRef.current + 1;
    ocrRunIdRef.current = runId;
    setIsLoading(true);
    setOcrResults([]);
    setOperationNotice(null);
    setOcrProgress({ currentPage: 0, totalPages: imagesList.length, completedPages: 0 });

    try {
      const allRoisFromOcr: (ROI & { pageIndex?: number })[] = [];
      const allOcrResults: (OCRResult & { pageIndex?: number })[] = [];

      for (let pageIdx = 0; pageIdx < imagesList.length; pageIdx += 1) {
        if (ocrRunIdRef.current !== runId) return;
        setOcrProgress({ currentPage: pageIdx + 1, totalPages: imagesList.length, completedPages: pageIdx });

        const currentImgUrl = imagesList[pageIdx];
        const img = await loadImageElement(currentImgUrl);

        const renderedWidth = 750;
        const renderedHeight = (img.naturalHeight / img.naturalWidth) * renderedWidth;
        const scaleX = img.naturalWidth / renderedWidth;
        const scaleY = img.naturalHeight / renderedHeight;

        const aiData = await runAiProcessJob({
          image: currentImgUrl,
          rois: [],
        });
        if (ocrRunIdRef.current !== runId) return;
        if (!aiData.success || aiData.extracted_data.length === 0) {
          setOcrProgress({ currentPage: pageIdx + 1, totalPages: imagesList.length, completedPages: pageIdx + 1 });
          continue;
        }

        const generatedRoiIds = aiData.extracted_data.map((_: any, idx: number) =>
          Date.now() + pageIdx * 100000 + idx + Math.floor(Math.random() * 1000000)
        );

        const pageRoisFromOcr: (ROI & { pageIndex?: number })[] = aiData.extracted_data.map((item: any, idx: number) => {
          const rx = item.x / scaleX;
          const ry = item.y / scaleY;
          const rw = item.width / scaleX;
          const rh = item.height / scaleY;

          const pts = item.bbox
            ? item.bbox.map((pt: any) => ({
                x: pt[0] / scaleX,
                y: pt[1] / scaleY,
              }))
            : undefined;

          return {
            id: generatedRoiIds[idx],
            fieldName: item.fieldName || `line_${idx + 1}`,
            x: rx,
            y: ry,
            width: rw,
            height: rh,
            pageIndex: pageIdx,
            type: "text",
            dataType: "string",
            role: "data_extraction",
            points: pts,
          };
        });

        const pageOcrResults: (OCRResult & { pageIndex?: number })[] = aiData.extracted_data.map((item: any, idx: number) => {
          const pts = item.bbox
            ? item.bbox.map((pt: any) => ({
                x: pt[0] / scaleX,
                y: pt[1] / scaleY,
              }))
            : undefined;

          return {
            id: Date.now() + pageIdx * 100000 + idx + 1000000 + Math.floor(Math.random() * 1000000),
            roiId: generatedRoiIds[idx],
            fieldName: item.fieldName || `line_${idx + 1}`,
            bbox: [],
            extractedText: item.text,
            originalText: item.text,
            confidence: item.confidence,
            saved_path: item.saved_path || "",
            pageIndex: pageIdx,
            type: "text",
            dataType: "string",
            role: "data_extraction",
            points: pts,
          };
        });

        allRoisFromOcr.push(...pageRoisFromOcr);
        allOcrResults.push(...pageOcrResults);
        setOcrProgress({ currentPage: pageIdx + 1, totalPages: imagesList.length, completedPages: pageIdx + 1 });
      }

      if (allOcrResults.length > 0) {
        if (ocrRunIdRef.current !== runId) return;
        setRois((prev) => {
          const nonGeneratedRois = prev.filter((r) => !r.fieldName.startsWith("line_"));
          return [...nonGeneratedRois, ...allRoisFromOcr];
        });

        setOcrResults(allOcrResults);
        setCurrentIndex(0);
        setCurrentStep("editor");
      } else {
        setOperationNotice({
          tone: "warning",
          title: "ไม่พบข้อความในเอกสาร",
          message: "ระบบไม่พบข้อความจากการอ่านทั้งหน้า กรุณาตรวจสอบคุณภาพภาพหรือกำหนด ROI เอง",
        });
      }
    } catch (err) {
      console.error(err);
      setOperationNotice({
        tone: "danger",
        title: "อ่านทั้งหน้าไม่สำเร็จ",
        message: err instanceof Error ? err.message : "เกิดข้อผิดพลาดในการรัน OCR อัตโนมัติทั้งเอกสาร",
      });
    } finally {
      if (ocrRunIdRef.current === runId) {
        setIsLoading(false);
        setOcrProgress(null);
      }
    }
  };

  const getIncludedExportResults = (content: ExportContentOptions = exportContent) =>
    ocrResults.filter((result) => {
      const fieldType = getResultFieldType(result);
      if (fieldType === "table") return content.tables;
      if (fieldType === "image") return content.images;
      return content.text;
    });

  const buildExportPayload = (
    content: ExportContentOptions = exportContent,
    options: ExportDisplayOptions = exportOptions,
    imageCrops: ImageFieldCrop[] = []
  ) => {
    const imageCropByResult = new Map(imageCrops.map((crop) => [crop.resultId, crop]));
    const pages = Array.from({ length: Math.max(imagesList.length, 1) }, (_, index) => ({
      page: index + 1,
      fields: options.showFieldNames ? ({} as Record<string, unknown>) : ([] as unknown[]),
    }));

    const addField = (page: { fields: Record<string, unknown> | unknown[] }, fieldName: string, value: unknown) => {
      if (Array.isArray(page.fields)) {
        page.fields.push(value);
        return;
      }
      assignExportField(page.fields, fieldName, value);
    };

    getIncludedExportResults(content).forEach((result) => {
      const matchedRoi = findRoiForOcrResult(rois, result);
      const pageIndex = Math.max(0, result.pageIndex ?? matchedRoi?.pageIndex ?? 0);
      const page = pages[pageIndex] || pages[0];
      const fieldName = result.fieldName || matchedRoi?.fieldName || `field_${Object.keys(page.fields).length + 1}`;
      const fieldType = getResultFieldType(result);
      const rawValue = result.extractedText || "";

      if (fieldType === "table") {
        const structured = getStructuredTableForExport(result);
        const rows = structured?.rows || parseExportTable(rawValue) || [];
        const tableExportConfig = getTableExportConfigForRows(result, rows, structured);
        const resolvedHeaders = resolveTableHeaderKeys(rows, structured);
        if (tableExportConfig.mode === "key_value") {
          addField(page, fieldName, {
            mode: "key_value",
            keys: tableExportConfig.selectedColumns.map((columnIndex) => resolvedHeaders[columnIndex] || `Column ${columnIndex + 1}`),
            includeDataRows: tableExportConfig.includeDataRows,
            includeSummary: tableExportConfig.includeSummary,
            showRowNumber: tableExportConfig.showRowNumber,
            selectedRows: tableExportConfig.selectedRows,
            records: tableRowsToKeyValueRecords(rows, tableExportConfig.selectedColumns, structured, tableExportConfig.includeDataRows, tableExportConfig.selectedRows),
            summary: tableExportConfig.includeSummary ? tableRowsToSummaryKeyValuePairs(rows, structured) : [],
          });
          return;
        }

        if (structured?.cells?.length) {
          addField(page, fieldName, {
            mode: "structure",
            headerRowCount: structured.headerRowCount ?? 1,
            colWidths: structured.colWidths ?? [],
            cells: structured.cells
              .filter((cell) => !cell.hidden)
              .map((cell) => ({
                row: cell.row,
                col: cell.col,
                text: cell.text,
                rowSpan: cell.rowSpan ?? 1,
                colSpan: cell.colSpan ?? 1,
                bbox: cell.bbox,
                ocrText: cell.ocrText ?? cell.text,
                groundTruth: cell.groundTruth ?? cell.text,
              })),
          });
          return;
        }
        addField(page, fieldName, rows.length > 0 ? { mode: "structure", rows } : rawValue);
        return;
      }

      if (fieldType === "image") {
        const crop = imageCropByResult.get(result.id);
        addField(page, fieldName, {
          type: "image",
          hasImage: true,
          ...(crop
            ? {
                filename: crop.filename,
                mimeType: dataUrlMimeType(crop.dataUrl),
                base64: dataUrlBase64(crop.dataUrl),
              }
            : {}),
        });
        return;
      }

      addField(page, fieldName, rawValue);
    });

    return {
      ...(options.showDocumentTitle ? { template: matchedTemplate?.name ?? "OCR Export" } : {}),
      page_count: pages.length,
      pages,
    };
  };

  const buildExportPayloadWithImages = async (
    content: ExportContentOptions = exportContent,
    options: ExportDisplayOptions = exportOptions
  ) => buildExportPayload(content, options, await buildImageFieldCrops(content));

  const getResultFieldType = (result: OCRResult & { pageIndex?: number }) => {
    const matchedRoi = findRoiForOcrResult(rois, result);
    const markers = [
      result.type,
      result.dataType,
      matchedRoi?.type,
      matchedRoi?.dataType,
      matchedRoi?.extractionMethod,
    ].map((value) => String(value || "").toLowerCase());
    if (markers.some((value) => value === "image" || value === "extract_image")) return "image";
    if (markers.some((value) => value === "table" || value === "table_recognition_v2" || value === "ocr_table")) return "table";
    return "text";
  };

  const getStructuredTableForExport = (result: OCRResult & { pageIndex?: number }): StructuredTableResult | null => {
    if (result.tableStructured?.cells?.length) return result.tableStructured;
    const rows = Array.isArray(result.tableRows) && result.tableRows.length > 0 ? result.tableRows : parseExportTable(result.extractedText || "");
    if (!rows) return null;
    return {
      headerRowCount: 1,
      rows,
      cells: rows.flatMap((row, rowIndex) =>
        row.map((text, colIndex) => ({
          row: rowIndex,
          col: colIndex,
          text,
          rowSpan: 1,
          colSpan: 1,
          ocrText: text,
          groundTruth: text,
          hidden: false,
        }))
      ),
    };
  };

  const getTableDisplayRowsForExport = (result: OCRResult & { pageIndex?: number }) => {
    const structured = getStructuredTableForExport(result);
    const rows = structured?.rows || parseExportTable(result.extractedText || "") || [[""]];
    const tableExportConfig = getTableExportConfigForRows(result, rows, structured);
    if (tableExportConfig.mode !== "key_value") return rows;
    return tableRowsToKeyValueDisplayRows(
      rows,
      tableExportConfig.selectedColumns,
      structured,
      tableExportConfig.includeDataRows,
      tableExportConfig.includeSummary,
      tableExportConfig.showRowNumber,
      tableExportConfig.selectedRows
    );
  };

  const renderHtmlTable = (result: OCRResult & { pageIndex?: number }) => {
    const structured = getStructuredTableForExport(result);
    const rows = structured?.rows || parseExportTable(result.extractedText || "") || [];
    const tableExportConfig = getTableExportConfigForRows(result, rows, structured);
    if (tableExportConfig.mode === "key_value") {
      const records = tableRowsToKeyValueRecords(
        rows,
        tableExportConfig.selectedColumns,
        structured,
        tableExportConfig.includeDataRows,
        tableExportConfig.selectedRows
      );
      const summaryPairs = tableExportConfig.includeSummary ? tableRowsToSummaryKeyValuePairs(rows, structured) : [];
      const recordHtml = records.map((record, recordIndex) => {
        const lines = [
          ...(tableExportConfig.showRowNumber ? [`<p><strong>แถวที่</strong> : ${recordIndex + 1}</p>`] : []),
          ...Object.entries(record.values).map(([key, value]) => `<p><strong>${escapeHtml(key)}</strong> : ${escapeHtml(value)}</p>`),
        ].join("");
        return `<div class="kv-record">${lines}</div>`;
      }).join("");
      const summaryHtml = summaryPairs.length > 0
        ? `<div class="kv-summary"><h4>ส่วนสรุป</h4>${summaryPairs.map((pair) => `<p><strong>${escapeHtml(pair.key)}</strong> : ${escapeHtml(pair.value)}</p>`).join("")}</div>`
        : "";
      return recordHtml || summaryHtml ? `<div class="kv-table">${recordHtml}${summaryHtml}</div>` : "<p>-</p>";
    }
    if (!structured?.cells?.length) return `<p>${escapeHtml(result.extractedText)}</p>`;
    const headerRows = structured.headerRowCount ?? 1;
    const cellsByPosition = new Map(structured.cells.map((cell) => [`${cell.row}:${cell.col}`, cell]));
    const visibleCells = structured.cells.filter((cell) => !cell.hidden);
    const rowCount = Math.max(...visibleCells.map((cell) => cell.row + (cell.rowSpan ?? 1)), structured.rows?.length || 1);
    const colCount = Math.max(...visibleCells.map((cell) => cell.col + (cell.colSpan ?? 1)), structured.rows?.[0]?.length || 1);

    const rowsHtml = Array.from({ length: rowCount }, (_, rowIndex) => {
      const cellsHtml = Array.from({ length: colCount }, (_, colIndex) => {
        const cell = cellsByPosition.get(`${rowIndex}:${colIndex}`);
        if (!cell || cell.hidden) return "";
        const tag = rowIndex < headerRows ? "th" : "td";
        const spanAttrs = `${(cell.rowSpan ?? 1) > 1 ? ` rowspan="${cell.rowSpan}"` : ""}${(cell.colSpan ?? 1) > 1 ? ` colspan="${cell.colSpan}"` : ""}`;
        return `<${tag}${spanAttrs}>${escapeHtml(cell.groundTruth ?? cell.text)}</${tag}>`;
      }).join("");
      return `<tr>${cellsHtml}</tr>`;
    }).join("");
    return `<table>${rowsHtml}</table>`;
  };

  const buildImageFieldPreviewList = (content: ExportContentOptions = exportContent) => {
    if (!content.images) return [];
    const imageResults = getIncludedExportResults(content).filter((result) => getResultFieldType(result) === "image");
    const usedNames = new Map<string, number>();
    return imageResults.map((result) => {
      const matchedRoi = findRoiForOcrResult(rois, result);
      const baseName = safeFilename(result.fieldName || matchedRoi?.fieldName || "image");
      const nextCount = (usedNames.get(baseName) || 0) + 1;
      usedNames.set(baseName, nextCount);
      const extension = matchedRoi?.points && matchedRoi.points.length > 2 ? "png" : "jpg";
      return {
        fieldName: result.fieldName || matchedRoi?.fieldName || "Image",
        filename: `${baseName}${nextCount > 1 ? `_${nextCount}` : ""}.${extension}`,
        page: Math.max(0, result.pageIndex ?? matchedRoi?.pageIndex ?? 0) + 1,
      };
    });
  };

  const buildImageFieldCrops = async (content: ExportContentOptions = exportContent) => {
    if (!content.images) return [];
    const imageResults = getIncludedExportResults(content).filter((result) => getResultFieldType(result) === "image");
    const usedNames = new Map<string, number>();
    const crops: ImageFieldCrop[] = [];

    for (const result of imageResults) {
      const matchedRoi = findRoiForOcrResult(rois, result);
      const pageIndex = Math.max(0, result.pageIndex ?? matchedRoi?.pageIndex ?? 0);
      const sourceImage = imagesList[pageIndex] || imagesList[0] || previewUrl;
      if (!matchedRoi || !sourceImage) continue;
      const img = await loadImageElement(sourceImage);
      const displayWidth = 750;
      const displayHeight = img.naturalWidth > 0 ? (img.naturalHeight / img.naturalWidth) * displayWidth : 1000;
      const scaleX = img.naturalWidth / displayWidth;
      const scaleY = img.naturalHeight / displayHeight;
      const cropped = cropRoiToImage(img, matchedRoi, scaleX, scaleY);
      if (!cropped) continue;
      const baseName = safeFilename(result.fieldName || matchedRoi.fieldName || "image");
      const nextCount = (usedNames.get(baseName) || 0) + 1;
      usedNames.set(baseName, nextCount);
      const extension = cropped.startsWith("data:image/png") ? "png" : "jpg";
      crops.push({
        resultId: result.id,
        fieldName: result.fieldName,
        filename: `${baseName}${nextCount > 1 ? `_${nextCount}` : ""}.${extension}`,
        dataUrl: cropped,
        page: pageIndex + 1,
        width: Math.max(1, Math.round(matchedRoi.width * scaleX)),
        height: Math.max(1, Math.round(matchedRoi.height * scaleY)),
      });
    }
    return crops;
  };

  const buildWordHtml = async (
    content: ExportContentOptions = exportContent,
    options: ExportDisplayOptions = exportOptions
  ) => {
    const imageCrops = await buildImageFieldCrops(content);
    const imageByResult = new Map(imageCrops.map((crop) => [crop.resultId, crop]));
    const body = getIncludedExportResults(content).map((result) => {
      const fieldType = getResultFieldType(result);
      const heading = options.showFieldNames ? `<h2>${escapeHtml(result.fieldName)}</h2>` : "";
      if (fieldType === "table") {
        return `${heading}${renderHtmlTable(result)}`;
      }
      if (fieldType === "image") {
        const crop = imageByResult.get(result.id);
        return `${heading}${crop ? `<p><img src="${crop.dataUrl}" alt="${escapeHtml(result.fieldName)}" style="max-width:520px;height:auto"></p>` : "<p>Image field</p>"}`;
      }
      return `${heading}<p>${escapeHtml(result.extractedText)}</p>`;
    }).join("");

    const title = options.showDocumentTitle ? `<h1>${escapeHtml(matchedTemplate?.name || "OCR Export")}</h1>` : "";
    return `<!doctype html><html><head><meta charset="utf-8"><style>body{font-family:Arial,sans-serif;font-size:12pt}h1{font-size:18pt}h2{font-size:13pt;margin-top:18px}table{border-collapse:collapse;margin:8px 0 16px;width:100%}td,th{border:1px solid #999;padding:6px;vertical-align:top;white-space:pre-wrap}th{background:#f1f5f9}.kv-record{border-bottom:1px solid #e5e7eb;margin:0 0 10px;padding:0 0 8px}.kv-record p,.kv-summary p{margin:3px 0}.kv-summary{background:#f8fafc;border:1px solid #e5e7eb;margin-top:10px;padding:8px}.kv-summary h4{margin:0 0 6px;font-size:11pt}</style></head><body>${title}${body || "<p>No content selected</p>"}</body></html>`;
  };

  const buildExcelHtml = async (
    content: ExportContentOptions = exportContent,
    options: ExportDisplayOptions = exportOptions
  ) => {
    const imageCrops = await buildImageFieldCrops(content);
    const imageByResult = new Map(imageCrops.map((crop) => [crop.resultId, crop]));
    const sections: string[] = [];
    if (options.showDocumentTitle) {
      sections.push(`<h1>${escapeHtml(matchedTemplate?.name || "OCR Export")}</h1>`);
    }
    if (content.text) {
      const rows = getIncludedExportResults(content)
        .filter((result) => getResultFieldType(result) !== "table" && getResultFieldType(result) !== "image")
        .map((result) =>
          options.showFieldNames
            ? `<tr><td>${escapeHtml(result.fieldName)}</td><td>${escapeHtml(result.extractedText)}</td></tr>`
            : `<tr><td>${escapeHtml(result.extractedText)}</td></tr>`
        )
        .join("");
      const header = options.showFieldNames ? "<tr><th>Field</th><th>Ground Truth</th></tr>" : "<tr><th>Ground Truth</th></tr>";
      sections.push(`<h3>Text</h3><table>${header}${rows || "<tr><td colspan=\"2\">No text fields</td></tr>"}</table>`);
    }
    if (content.tables) {
      getIncludedExportResults(content)
        .filter((result) => getResultFieldType(result) === "table")
        .forEach((result) => {
          sections.push(`${options.showFieldNames ? `<h3>${escapeHtml(result.fieldName)}</h3>` : ""}${renderHtmlTable(result)}`);
        });
    }
    if (content.images) {
      const imageResults = getIncludedExportResults(content).filter((result) => getResultFieldType(result) === "image");
      const rows = imageResults
        .map((result) => {
          const crop = imageByResult.get(result.id);
          const fallbackPage = Math.max(0, result.pageIndex ?? 0) + 1;
          const imageCell = crop
            ? `<img src="cid:excel-image-${crop.resultId}" alt="${escapeHtml(result.fieldName)}" style="width:180px;height:auto;display:block">`
            : "Image crop unavailable";
          return `<tr>${options.showFieldNames ? `<td>${escapeHtml(result.fieldName)}</td>` : ""}<td>${imageCell}</td><td>${escapeHtml(crop?.filename || result.fieldName || "image")}</td><td>${crop?.page ?? fallbackPage}</td></tr>`;
        })
        .join("");
      const header = `<tr>${options.showFieldNames ? "<th>Field</th>" : ""}<th>Image</th><th>Filename</th><th>Page</th></tr>`;
      sections.push(`<h3>Images</h3><table>${header}${rows || `<tr><td colspan="${options.showFieldNames ? 4 : 3}">No image fields</td></tr>`}</table>`);
    }
    return `<!doctype html><html><head><meta charset="utf-8"><style>body{font-family:Arial,sans-serif}h1{font-size:18pt;text-align:left}h3{font-size:12pt;margin-top:16px;text-align:left}table{border-collapse:collapse;margin-bottom:18px}td,th{border:1px solid #999;padding:5px;vertical-align:top;white-space:pre-wrap;text-align:left;mso-number-format:"\\@";}th{background:#e2e8f0;font-weight:bold}img{display:block}.kv-record{border-bottom:1px solid #e5e7eb;margin:0 0 10px;padding:0 0 8px}.kv-record p,.kv-summary p{margin:3px 0}.kv-summary{background:#f8fafc;border:1px solid #e5e7eb;margin-top:10px;padding:8px}.kv-summary h4{margin:0 0 6px;font-size:11pt}</style></head><body>${sections.join("") || "<p>No content selected</p>"}</body></html>`;
  };

  const buildExcelXlsxBlob = async (
    content: ExportContentOptions = exportContent,
    options: ExportDisplayOptions = exportOptions
  ) => {
    const imageCrops = await buildImageFieldCrops(content);
    const textResults = getIncludedExportResults(content).filter((result) => getResultFieldType(result) !== "table" && getResultFieldType(result) !== "image");
    const tableResults = getIncludedExportResults(content).filter((result) => getResultFieldType(result) === "table");
    const imageResults = getIncludedExportResults(content).filter((result) => getResultFieldType(result) === "image");
    const imageByResult = new Map(imageCrops.map((crop) => [crop.resultId, crop]));
    const encoder = new TextEncoder();
    const files: { name: string; bytes: Uint8Array }[] = [];
    const sheetDefs: { name: string; xml: string; relXml?: string; drawingXml?: string; drawingRelsXml?: string }[] = [];

    const worksheet = (rows: string, beforeSheetData = "", afterSheetData = "") =>
      `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheetViews><sheetView workbookViewId="0"/></sheetViews><sheetFormatPr defaultRowHeight="18"/>${beforeSheetData}<sheetData>${rows}</sheetData>${afterSheetData}</worksheet>`;

    if (textResults.length > 0) {
      const textRows = [
        ...(options.showDocumentTitle ? [xlsxRow(0, [matchedTemplate?.name || "OCR Export"], 2)] : []),
        xlsxRow(options.showDocumentTitle ? 2 : 0, options.showFieldNames ? ["Field", "Ground Truth"] : ["Ground Truth"], 2),
        ...textResults.map((result, index) =>
          xlsxRow((options.showDocumentTitle ? 3 : 1) + index, options.showFieldNames ? [result.fieldName, result.extractedText] : [result.extractedText])
        ),
      ].join("");
      sheetDefs.push({
        name: "Text",
        xml: worksheet(textRows, `<cols><col min="1" max="1" width="28" customWidth="1"/><col min="2" max="2" width="64" customWidth="1"/></cols>`),
      });
    }

    if (tableResults.length > 0) {
      const tableRows: string[] = [];
      let tableRowIndex = 0;
      if (options.showDocumentTitle) {
        tableRows.push(xlsxRow(tableRowIndex, [matchedTemplate?.name || "OCR Export"], 2));
        tableRowIndex += 2;
      }
      tableResults.forEach((result) => {
        if (options.showFieldNames) {
          tableRows.push(xlsxRow(tableRowIndex, [result.fieldName], 2));
          tableRowIndex += 1;
        }
        const rows = getTableDisplayRowsForExport(result);
        rows.forEach((row, rowOffset) => {
          tableRows.push(xlsxRow(tableRowIndex, row, rowOffset === 0 ? 2 : 1));
          tableRowIndex += 1;
        });
        tableRowIndex += 1;
      });
      sheetDefs.push({
        name: "Tables",
        xml: worksheet(tableRows.join(""), `<cols><col min="1" max="20" width="22" customWidth="1"/></cols>`),
      });
    }

    if (imageResults.length > 0) {
      const imageRows: string[] = [];
      let imageRowIndex = 0;
      imageRows.push(xlsxRow(imageRowIndex, options.showFieldNames ? ["Field", "Image", "Filename", "Page"] : ["Image", "Filename", "Page"], 2));
      imageRowIndex += 1;
      imageResults.forEach((result) => {
        const crop = imageByResult.get(result.id);
        const displaySize = crop ? fitExcelImageSize(crop.width, crop.height) : null;
        const rowHeight = displaySize ? Math.ceil(displaySize.height * 0.75 + 10) : 28;
        const rowValues = options.showFieldNames
          ? [result.fieldName, crop ? "" : "Image crop unavailable", crop?.filename || result.fieldName || "image", crop?.page ?? Math.max(0, result.pageIndex ?? 0) + 1]
          : [crop ? "" : "Image crop unavailable", crop?.filename || result.fieldName || "image", crop?.page ?? Math.max(0, result.pageIndex ?? 0) + 1];
        imageRows.push(`<row r="${imageRowIndex + 1}" ht="${rowHeight}" customHeight="1">${rowValues.map((value, colIndex) => xlsxCell(imageRowIndex, colIndex, value)).join("")}</row>`);
        imageRowIndex += 1;
      });

      const imageDrawingRel = imageCrops.length > 0 ? `<drawing r:id="rId1"/>` : "";
      sheetDefs.push({
        name: "Images",
        xml: worksheet(imageRows.join(""), `<cols><col min="1" max="1" width="${options.showFieldNames ? 28 : 30}" customWidth="1"/><col min="2" max="2" width="30" customWidth="1"/><col min="3" max="4" width="24" customWidth="1"/></cols>`, imageDrawingRel),
        relXml: imageCrops.length > 0
          ? `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing1.xml"/></Relationships>`
          : undefined,
      });
    }

    if (sheetDefs.length === 0) {
      sheetDefs.push({
        name: "Export",
        xml: worksheet(xlsxRow(0, ["No content selected"], 2)),
      });
    }

    files.push(
      { name: "[Content_Types].xml", bytes: encoder.encode(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="jpg" ContentType="image/jpeg"/><Default Extension="jpeg" ContentType="image/jpeg"/><Default Extension="png" ContentType="image/png"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>${sheetDefs.map((_, index) => `<Override PartName="/xl/worksheets/sheet${index + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join("")}${imageCrops.length > 0 ? `<Override PartName="/xl/drawings/drawing1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>` : ""}</Types>`) },
      { name: "_rels/.rels", bytes: encoder.encode(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`) },
      { name: "xl/workbook.xml", bytes: encoder.encode(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>${sheetDefs.map((sheet, index) => `<sheet name="${xmlEscape(sheet.name)}" sheetId="${index + 1}" r:id="rId${index + 1}"/>`).join("")}</sheets></workbook>`) },
      { name: "xl/_rels/workbook.xml.rels", bytes: encoder.encode(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${sheetDefs.map((_, index) => `<Relationship Id="rId${index + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${index + 1}.xml"/>`).join("")}<Relationship Id="rId${sheetDefs.length + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>`) },
      { name: "xl/styles.xml", bytes: encoder.encode(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Arial"/></font><font><b/><sz val="11"/><name val="Arial"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFE2E8F0"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/></border></borders><cellXfs count="3"><xf fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="left" vertical="top" wrapText="1"/></xf><xf fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="left" vertical="top" wrapText="1"/></xf><xf fontId="1" fillId="1" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="left" vertical="top" wrapText="1"/></xf></cellXfs></styleSheet>`) }
    );
    sheetDefs.forEach((sheet, index) => {
      files.push({ name: `xl/worksheets/sheet${index + 1}.xml`, bytes: encoder.encode(sheet.xml) });
      if (sheet.relXml) {
        files.push({ name: `xl/worksheets/_rels/sheet${index + 1}.xml.rels`, bytes: encoder.encode(sheet.relXml) });
      }
    });

    if (imageCrops.length > 0) {
      files.push(
        { name: "xl/drawings/_rels/drawing1.xml.rels", bytes: encoder.encode(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${imageCrops.map((crop, index) => `<Relationship Id="rId${index + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image${index + 1}.${dataUrlMimeType(crop.dataUrl).includes("png") ? "png" : "jpg"}"/>`).join("")}</Relationships>`) },
        { name: "xl/drawings/drawing1.xml", bytes: encoder.encode(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">${imageCrops.map((crop, index) => {
          const row = imageResults.findIndex((result) => result.id === crop.resultId) + 1;
          const col = options.showFieldNames ? 1 : 0;
          const displaySize = fitExcelImageSize(crop.width, crop.height);
          const cx = displaySize.width * EXCEL_EMUS_PER_PIXEL;
          const cy = displaySize.height * EXCEL_EMUS_PER_PIXEL;
          return `<xdr:oneCellAnchor><xdr:from><xdr:col>${col}</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>${row}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from><xdr:ext cx="${cx}" cy="${cy}"/><xdr:pic><xdr:nvPicPr><xdr:cNvPr id="${index + 1}" name="${xmlEscape(crop.filename)}"/><xdr:cNvPicPr/></xdr:nvPicPr><xdr:blipFill><a:blip r:embed="rId${index + 1}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/><a:stretch><a:fillRect/></a:stretch></xdr:blipFill><xdr:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="${cx}" cy="${cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr></xdr:pic><xdr:clientData/></xdr:oneCellAnchor>`;
        }).join("")}</xdr:wsDr>`) }
      );
      imageCrops.forEach((crop, index) => {
        files.push({
          name: `xl/media/image${index + 1}.${dataUrlMimeType(crop.dataUrl).includes("png") ? "png" : "jpg"}`,
          bytes: dataUrlToBytes(crop.dataUrl),
        });
      });
    }

    return createZipBlob(files);
  };

  const openExportJson = () => {
    setCopyStatus("");
    setExportText("");
    setExportJson("กำลังเตรียม JSON พร้อมรูปภาพ...");
    void buildExportPayloadWithImages().then((payload) => {
      setExportJson(JSON.stringify(payload, null, 2));
    });
  };

  const renderPlainValue = (value: unknown, indent = ""): string => {
    if (Array.isArray(value)) {
      if (value.every((row) => row && typeof row === "object" && !Array.isArray(row))) {
        return value
          .map((row, index) => {
            const cells = Object.entries(row as Record<string, unknown>)
              .map(([key, cell]) => `${key}: ${String(cell ?? "")}`)
              .join(" | ");
            return `${indent}${index + 1}. ${cells}`;
          })
          .join("\n");
      }
      return value.map((item) => `${indent}- ${String(item ?? "")}`).join("\n");
    }

    if (value && typeof value === "object") {
      return Object.entries(value as Record<string, unknown>)
        .map(([key, child]) => `${indent}${key}: ${String(child ?? "")}`)
        .join("\n");
    }

    return `${indent}${String(value ?? "")}`;
  };

  const buildExportPlainText = () => {
    const payload = buildExportPayload();
    const lines: string[] = [];

    if (payload.template) {
      lines.push(`Template: ${payload.template}`);
    }
    lines.push(`Pages: ${payload.page_count}`);

    payload.pages.forEach((page) => {
      lines.push("");
      lines.push(`Page ${page.page}`);

      const fields = Object.entries(page.fields);
      if (fields.length === 0) {
        lines.push("- No OCR fields");
        return;
      }

      fields.forEach(([fieldName, value]) => {
        const rendered = renderPlainValue(value, "  ");
        if (rendered.includes("\n")) {
          lines.push(`${fieldName}:`);
          lines.push(rendered);
        } else {
          lines.push(`${fieldName}: ${rendered.trim()}`);
        }
      });
    });

    return lines.join("\n").trim();
  };

  const openExportText = () => {
    setCopyStatus("");
    setExportJson("");
    setExportText(buildExportPlainText());
  };

  const downloadWordExport = async () => {
    setCopyStatus("");
    setExportJson("");
    setExportText("");
    downloadTextFile(`ocr-export-${Date.now()}.doc`, await buildWordHtml(exportContent, exportOptions), "application/msword");
  };

  const downloadExcelExport = async () => {
    setCopyStatus("");
    setExportJson("");
    setExportText("");
    downloadBlobFile(`ocr-export-${Date.now()}.xlsx`, await buildExcelXlsxBlob(exportContent, exportOptions));
  };

  const downloadImageZipExport = async () => {
    setCopyStatus("");
    setExportJson("");
    setExportText("");
    const crops = await buildImageFieldCrops(exportContent);
    if (crops.length === 0) {
      setOperationNotice({
        tone: "warning",
        title: "ไม่มี Image Field",
        message: "ไม่พบ field ประเภทรูปภาพที่สามารถ crop เพื่อดาวน์โหลดได้",
      });
      return;
    }
    downloadBlobFile(
      `ocr-image-fields-${Date.now()}.zip`,
      createZipBlob(crops.map((crop) => ({ name: crop.filename, bytes: dataUrlToBytes(crop.dataUrl) })))
    );
  };

  const runExport = async (type: ExportFormat) => {
    if (type === "json") {
      const json = JSON.stringify(await buildExportPayloadWithImages(exportContent, exportOptions), null, 2);
      downloadTextFile(`ocr-export-${Date.now()}.json`, json);
      return;
    }
    if (type === "word") {
      await downloadWordExport();
      return;
    }
    if (type === "excel") {
      await downloadExcelExport();
      return;
    }
    await downloadImageZipExport();
  };

  const requestExport = (type: ExportFormat) => {
    setIsExportMenuOpen(false);
    void runExport(type);
  };

  const handleOpenDefaultExport = () => {
    setExportFormat("word");
    setExportContent({
      text: availableExportContent.text,
      tables: availableExportContent.tables,
      images: availableExportContent.images,
    });
    setIsExportMenuOpen(true);
  };

  const handleOpenExportJson = () => {
    setExportFormat("json");
    setIsExportMenuOpen(true);
  };

  const handleOpenExportWord = () => {
    setExportFormat("word");
    setIsExportMenuOpen(true);
  };

  const handleOpenExportExcel = () => {
    setExportFormat("excel");
    setIsExportMenuOpen(true);
  };

  const handleOpenExportImages = () => {
    setExportFormat("images");
    setIsExportMenuOpen(true);
  };

  const handleCopyExportJson = async () => {
    if (!exportJson) return;
    try {
      await navigator.clipboard.writeText(exportJson);
      setCopyStatus("Copied JSON to clipboard.");
    } catch {
      setCopyStatus("Copy failed. You can select and copy the JSON manually.");
    }
  };

  const handleCopyExportText = async () => {
    if (!exportText) return;
    try {
      await navigator.clipboard.writeText(exportText);
      setCopyStatus("Copied text to clipboard.");
    } catch {
      setCopyStatus("Copy failed. You can select and copy the text manually.");
    }
  };

  const currentFlowIndex = Math.max(0, USER_FLOW_STEPS.findIndex((step) => step.key === currentStep));
  const currentFlowStep = USER_FLOW_STEPS[currentFlowIndex] || USER_FLOW_STEPS[0];
  const completedStepCount = currentFlowIndex;
  const exportFormats: { key: ExportFormat; label: string }[] = [
    { key: "word", label: "Word" },
    { key: "excel", label: "Excel" },
    { key: "json", label: "JSON" },
    { key: "images", label: "Images" },
  ];
  const exportableContentCounts = useMemo(() => {
    const counts = { text: 0, tables: 0, images: 0 };
    ocrResults.forEach((result) => {
      const fieldType = getResultFieldType(result);
      if (fieldType === "table") {
        counts.tables += 1;
      } else if (fieldType === "image") {
        counts.images += 1;
      } else {
        counts.text += 1;
      }
    });
    return counts;
  }, [ocrResults, rois]);
  const availableExportContent = {
    text: exportableContentCounts.text > 0,
    tables: exportableContentCounts.tables > 0,
    images: exportableContentCounts.images > 0,
  };
  const exportContentChoiceOptions: { key: keyof ExportContentOptions; label: string }[] = [
    { key: "text", label: "Text" },
    { key: "tables", label: "Tables" },
    { key: "images", label: "Images" },
  ];
  const exportContentChoices = exportContentChoiceOptions.filter((choice) => {
    if (!availableExportContent[choice.key]) return false;
    if (exportFormat === "images") return choice.key === "images";
    return true;
  });
  const visibleExportFormats = exportFormats.filter((format) => format.key !== "images" || availableExportContent.images);
  const exportPreviewImages = buildImageFieldPreviewList(exportContent);
  const exportPreviewResults = getIncludedExportResults(exportContent);
  const hasSelectedExportContent = exportPreviewResults.length > 0;
  useEffect(() => {
    if (!availableExportContent.images && exportFormat === "images") {
      setExportFormat("word");
    }
    setExportContent((prev) => {
      const next = {
        text: exportFormat === "images" ? false : availableExportContent.text ? prev.text : false,
        tables: exportFormat === "images" ? false : availableExportContent.tables ? prev.tables : false,
        images: availableExportContent.images ? prev.images : false,
      };
      return next.text === prev.text && next.tables === prev.tables && next.images === prev.images ? prev : next;
    });
  }, [availableExportContent.text, availableExportContent.tables, availableExportContent.images, exportFormat]);
  useEffect(() => {
    let cancelled = false;
    if (!isExportMenuOpen || exportFormat !== "json") return;
    setExportPreviewJsonText("กำลังเตรียม JSON พร้อมรูปภาพ...");
    void buildExportPayloadWithImages(exportContent, exportOptions)
      .then((payload) => {
        if (!cancelled) setExportPreviewJsonText(JSON.stringify(payload, null, 2));
      })
      .catch((error) => {
        if (!cancelled) {
          setExportPreviewJsonText(
            JSON.stringify(
              {
                error: "สร้าง JSON Preview ไม่สำเร็จ",
                detail: error instanceof Error ? error.message : String(error),
              },
              null,
              2
            )
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [isExportMenuOpen, exportFormat, exportContent, exportOptions, ocrResults, rois, imagesList, previewUrl, matchedTemplate?.name]);
  const textPreviewItems = useMemo(() => {
    return ocrResults
      .filter((result) => (result.pageIndex !== undefined ? Number(result.pageIndex) : 0) === currentIndex)
      .filter((result) => getResultFieldType(result) !== "table" && getResultFieldType(result) !== "image")
      .map((result) => ({
        id: result.id,
        key: result.fieldName?.trim() || `field_${result.id}`,
        value: result.extractedText || "",
      }));
  }, [ocrResults, currentIndex, rois]);
  const textPreviewCopyText = useMemo(() => {
    return textPreviewItems.map((item) => `${item.key}: ${item.value || "-"}`).join("\n");
  }, [textPreviewItems]);

  const handleCopyTextPreview = async () => {
    if (!textPreviewCopyText) return;
    try {
      await navigator.clipboard.writeText(textPreviewCopyText);
      setTextPreviewCopyStatus("คัดลอกแล้ว");
    } catch {
      setTextPreviewCopyStatus("คัดลอกไม่สำเร็จ");
    }
    window.setTimeout(() => setTextPreviewCopyStatus(""), 1800);
  };

  const toggleExportContent = (key: keyof ExportContentOptions) => {
    setExportContent((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const toggleExportOption = (key: keyof ExportDisplayOptions) => {
    setExportOptions((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const updateTableExportConfig = (resultId: number, nextConfig: TableExportConfig) => {
    setOcrResults((previous) =>
      previous.map((result) =>
        result.id === resultId ? { ...result, tableExport: nextConfig } : result
      )
    );
  };

  const tableResultsForExport = exportPreviewResults.filter((result) => getResultFieldType(result) === "table");
  const tableExportMode =
    tableResultsForExport.length > 0 && tableResultsForExport.every((result) => {
      const structured = getStructuredTableForExport(result);
      const rows = structured?.rows || parseExportTable(result.extractedText || "") || [[""]];
      return getTableExportConfigForRows(result, rows, structured).mode === "key_value";
    })
      ? "key_value"
      : "structure";
  const showTableExportConfigPanel = exportContent.tables && tableExportMode === "key_value" && tableResultsForExport.length > 0;

  const setAllTableExportMode = (mode: TableExportConfig["mode"]) => {
    setOcrResults((previous) =>
      previous.map((result) => {
        if (getResultFieldType(result) !== "table") return result;
        const structured = getStructuredTableForExport(result);
        const rows = structured?.rows || parseExportTable(result.extractedText || "") || [[""]];
        const config = getTableExportConfigForRows(result, rows, structured);
        return { ...result, tableExport: { ...config, mode } };
      })
    );
  };

  const renderTableExportConfigPanel = () => {
    const tableResults = tableResultsForExport;
    if (!showTableExportConfigPanel) return null;

    return (
      <div ref={tableExportDropdownRef} className="space-y-3">
        {tableResults.map((result) => {
          const structured = getStructuredTableForExport(result);
          const rows = structured?.rows || parseExportTable(result.extractedText || "") || [[""]];
          const config = getTableExportConfigForRows(result, rows, structured);
          const headerColumns = resolveTableHeaderKeys(rows, structured);
          const dataRowOptions = getTableKeyValueDataRows(rows, structured);
          const selectedRowIndexes = Array.isArray(config.selectedRows) ? config.selectedRows : dataRowOptions.map((row) => row.rowIndex);
          const selectedColumnIndexes = config.selectedColumns;
          const rowDropdownKey = `${result.id}:rows`;
          const columnDropdownKey = `${result.id}:columns`;
          const rowStatus =
            selectedRowIndexes.length === dataRowOptions.length
              ? `ทุกแถว (${dataRowOptions.length})`
              : `เลือก ${selectedRowIndexes.length}/${dataRowOptions.length} แถว`;
          const columnStatus =
            selectedColumnIndexes.length === headerColumns.length
              ? `ทุกคอลัมน์ (${headerColumns.length})`
              : `เลือก ${selectedColumnIndexes.length}/${headerColumns.length} คอลัมน์`;
          const summaryPairs = tableRowsToSummaryKeyValuePairs(rows, structured);
          const patchConfig = (patch: Partial<TableExportConfig>) =>
            updateTableExportConfig(result.id, { ...config, ...patch });
          const setAllRowsSelected = (checked: boolean) => {
            patchConfig({ mode: "key_value", selectedRows: checked ? dataRowOptions.map((row) => row.rowIndex) : [] });
          };
          const setAllColumnsSelected = (checked: boolean) => {
            patchConfig({ mode: "key_value", selectedColumns: checked ? headerColumns.map((_, index) => index) : [] });
          };
          const toggleRow = (rowIndex: number) => {
            const nextRows = selectedRowIndexes.includes(rowIndex)
              ? selectedRowIndexes.filter((index) => index !== rowIndex)
              : [...selectedRowIndexes, rowIndex].sort((left, right) => left - right);
            patchConfig({ mode: "key_value", selectedRows: nextRows });
          };
          const toggleColumn = (columnIndex: number) => {
            const nextColumns = selectedColumnIndexes.includes(columnIndex)
              ? selectedColumnIndexes.filter((index) => index !== columnIndex)
              : [...selectedColumnIndexes, columnIndex].sort((left, right) => left - right);
            patchConfig({ mode: "key_value", selectedColumns: nextColumns });
          };

          return (
            <section key={result.id} className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-xs font-black text-slate-900">{result.fieldName || "Table"}</p>
                  <p className="mt-0.5 text-[10px] font-semibold text-slate-500">เลือกรูปแบบข้อมูลเฉพาะตารางนี้</p>
                </div>
                <span className="shrink-0 rounded-full bg-slate-100 px-2 py-1 text-[9px] font-black text-slate-500">
                  {rows.length} rows
                </span>
              </div>

              <div className="mt-3 space-y-3">
                  <div className="rounded-xl border border-blue-100 bg-blue-50/60 p-3">
                    <label className="flex cursor-pointer items-start gap-2">
                      <input
                        type="checkbox"
                        checked={config.includeDataRows}
                        onChange={() => patchConfig({ includeDataRows: !config.includeDataRows })}
                        className="mt-0.5 h-4 w-4 rounded border-slate-300 text-blue-600"
                      />
                      <span className="min-w-0">
                        <span className="block text-xs font-black text-blue-950">ส่วนตารางเนื้อหา</span>
                        <span className="mt-0.5 block text-[10px] font-semibold text-blue-700">ใช้ data rows สร้าง Row N -&gt; key : value</span>
                      </span>
                    </label>
                    <label className="mt-3 flex cursor-pointer items-center gap-2 rounded-lg border border-blue-100 bg-white px-2.5 py-1.5 text-[11px] font-bold text-blue-800">
                      <input
                        type="checkbox"
                        checked={config.showRowNumber}
                        onChange={() => patchConfig({ showRowNumber: !config.showRowNumber })}
                        disabled={!config.includeDataRows}
                        className="h-3.5 w-3.5 rounded border-slate-300 text-blue-600"
                      />
                      แสดงแถวที่
                    </label>
                    <div className="mt-3 grid gap-2">
                      <div className="relative">
                        <button
                          type="button"
                          disabled={!config.includeDataRows}
                          onClick={() => setOpenTableExportDropdown((current) => current === rowDropdownKey ? null : rowDropdownKey)}
                          className="flex w-full items-center justify-between gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-left text-[11px] font-black text-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          <span>Row</span>
                          <span className="truncate text-slate-500">{rowStatus}</span>
                        </button>
                        {openTableExportDropdown === rowDropdownKey && (
                          <div className="absolute left-0 right-0 top-full z-40 mt-1 max-h-56 overflow-auto rounded-lg border border-slate-200 bg-white p-2 shadow-xl">
                            <label className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-[11px] font-black text-slate-700 hover:bg-slate-50">
                              <input
                                type="checkbox"
                                checked={selectedRowIndexes.length === dataRowOptions.length}
                                onChange={(event) => setAllRowsSelected(event.target.checked)}
                                className="h-3.5 w-3.5 rounded border-slate-300 text-indigo-600"
                              />
                              เลือกทั้งหมด
                            </label>
                            <div className="my-1 border-t border-slate-100" />
                            {dataRowOptions.map((row) => (
                              <label key={`${result.id}-row-${row.rowIndex}`} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-[11px] font-semibold text-slate-600 hover:bg-slate-50">
                                <input
                                  type="checkbox"
                                  checked={selectedRowIndexes.includes(row.rowIndex)}
                                  onChange={() => toggleRow(row.rowIndex)}
                                  className="h-3.5 w-3.5 rounded border-slate-300 text-indigo-600"
                                />
                                {row.label}
                              </label>
                            ))}
                          </div>
                        )}
                      </div>

                      <div className="relative">
                        <button
                          type="button"
                          disabled={!config.includeDataRows}
                          onClick={() => setOpenTableExportDropdown((current) => current === columnDropdownKey ? null : columnDropdownKey)}
                          className="flex w-full items-center justify-between gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-left text-[11px] font-black text-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          <span>Column</span>
                          <span className="truncate text-slate-500">{columnStatus}</span>
                        </button>
                        {openTableExportDropdown === columnDropdownKey && (
                          <div className="absolute left-0 right-0 top-full z-40 mt-1 max-h-56 overflow-auto rounded-lg border border-slate-200 bg-white p-2 shadow-xl">
                            <label className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-[11px] font-black text-slate-700 hover:bg-slate-50">
                              <input
                                type="checkbox"
                                checked={selectedColumnIndexes.length === headerColumns.length}
                                onChange={(event) => setAllColumnsSelected(event.target.checked)}
                                className="h-3.5 w-3.5 rounded border-slate-300 text-indigo-600"
                              />
                              เลือกทั้งหมด
                            </label>
                            <div className="my-1 border-t border-slate-100" />
                            {headerColumns.map((header, columnIndex) => (
                              <label key={`${result.id}-export-column-${columnIndex}`} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-[11px] font-semibold text-slate-600 hover:bg-slate-50">
                                <input
                                  type="checkbox"
                                  checked={selectedColumnIndexes.includes(columnIndex)}
                                  onChange={() => toggleColumn(columnIndex)}
                                  className="h-3.5 w-3.5 rounded border-slate-300 text-indigo-600"
                                />
                                <span className="truncate">{header || `Column ${columnIndex + 1}`}</span>
                              </label>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  </div>

                  <div className="rounded-xl border border-emerald-100 bg-emerald-50/70 p-3">
                    <label className="flex cursor-pointer items-start gap-2">
                      <input
                        type="checkbox"
                        checked={config.includeSummary}
                        onChange={() => patchConfig({ includeSummary: !config.includeSummary })}
                        className="mt-0.5 h-4 w-4 rounded border-slate-300 text-emerald-600"
                      />
                      <span className="min-w-0">
                        <span className="block text-xs font-black text-emerald-950">ส่วนสรุป</span>
                        <span className="mt-0.5 block text-[10px] font-semibold text-emerald-700">ส่งออก Summary Region เป็น key : value แยกจากตารางเนื้อหา</span>
                      </span>
                    </label>
                    <div className="mt-3 rounded-lg border border-emerald-100 bg-white p-2">
                      {summaryPairs.length === 0 ? (
                        <p className="px-2 py-1 text-[11px] font-semibold text-slate-400">ไม่พบ Summary Region ที่มั่นใจพอ</p>
                      ) : !config.includeSummary ? (
                        <p className="px-2 py-1 text-[11px] font-semibold text-slate-400">ไม่ได้เลือกส่งออกส่วนสรุป</p>
                      ) : (
                        <div className="space-y-1">
                          {summaryPairs.map((pair, pairIndex) => (
                            <p key={`${result.id}-summary-${pair.row}-${pairIndex}`} className="break-words text-[11px] font-medium text-emerald-900">
                              <span className="font-bold">{pair.key}</span>
                              <span className="px-1 text-emerald-500">:</span>
                              <span>{pair.value || "-"}</span>
                            </p>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
              </div>
            </section>
          );
        })}
      </div>
    );
  };

  const renderExportPreview = () => {
    if (exportFormat === "json") {
      return (
        <pre className="h-full min-h-0 overflow-auto rounded-xl bg-slate-950 p-4 text-xs leading-relaxed text-slate-100">
          {exportPreviewJsonText || "กำลังเตรียม JSON พร้อมรูปภาพ..."}
        </pre>
      );
    }

    if (exportFormat === "images") {
      return (
        <div className="h-full min-h-0 overflow-auto rounded-xl border border-slate-200 bg-white">
          {exportPreviewImages.length === 0 ? (
            <p className="p-4 text-xs font-semibold text-slate-500">ไม่มีรูปภาพที่จะอยู่ใน ZIP</p>
          ) : (
            exportPreviewImages.map((image) => (
              <div key={`${image.filename}-${image.page}`} className="flex items-center justify-between gap-3 border-b border-slate-100 px-4 py-3 last:border-b-0">
                <div className="min-w-0">
                  <p className="truncate text-xs font-black text-slate-900">{image.filename}</p>
                  {exportOptions.showFieldNames && <p className="mt-0.5 truncate text-[11px] font-semibold text-slate-500">{image.fieldName}</p>}
                </div>
                <span className="shrink-0 rounded-full bg-slate-100 px-2 py-1 text-[10px] font-black text-slate-500">หน้า {image.page}</span>
              </div>
            ))
          )}
        </div>
      );
    }

    if (exportFormat === "excel") {
      const textCount = exportPreviewResults.filter((result) => getResultFieldType(result) !== "table" && getResultFieldType(result) !== "image").length;
      const tableResults = exportPreviewResults.filter((result) => getResultFieldType(result) === "table");
      return (
        <div className="h-full min-h-0 space-y-3 overflow-auto rounded-xl border border-slate-200 bg-white p-4">
          {exportOptions.showDocumentTitle && <h3 className="text-sm font-black text-slate-950">{matchedTemplate?.name || "OCR Export"}</h3>}
          {exportContent.text && (
            <div className="rounded-lg border border-slate-200">
              <div className="border-b border-slate-100 bg-slate-50 px-3 py-2 text-xs font-black text-slate-700">Sheet: Text Fields</div>
              <p className="px-3 py-2 text-xs font-semibold text-slate-500">{textCount} rows</p>
            </div>
          )}
          {exportContent.tables && tableResults.map((result) => (
            <div key={result.roiId || result.fieldName} className="rounded-lg border border-slate-200">
              <div className="border-b border-slate-100 bg-slate-50 px-3 py-2 text-xs font-black text-slate-700">
                Sheet: {exportOptions.showFieldNames ? result.fieldName : "Table"}
              </div>
              <div className="overflow-auto p-3 [&_.kv-record]:mb-3 [&_.kv-record]:rounded-lg [&_.kv-record]:border [&_.kv-record]:border-slate-200 [&_.kv-record]:bg-white [&_.kv-record]:p-3 [&_.kv-record]:shadow-sm [&_.kv-record_p]:my-1 [&_.kv-summary]:rounded-lg [&_.kv-summary]:border [&_.kv-summary]:border-emerald-200 [&_.kv-summary]:bg-emerald-50 [&_.kv-summary]:p-3 [&_td]:border [&_td]:border-slate-300 [&_td]:px-2.5 [&_td]:py-2 [&_th]:border [&_th]:border-slate-400 [&_th]:bg-slate-100 [&_th]:px-2.5 [&_th]:py-2" dangerouslySetInnerHTML={{ __html: renderHtmlTable(result) }} />
            </div>
          ))}
          {exportContent.images && (
            <div className="rounded-lg border border-slate-200">
              <div className="border-b border-slate-100 bg-slate-50 px-3 py-2 text-xs font-black text-slate-700">Sheet: Images</div>
              <p className="px-3 py-2 text-xs font-semibold text-slate-500">{exportPreviewImages.length} image rows</p>
            </div>
          )}
        </div>
      );
    }

    return (
      <div className="h-full min-h-0 overflow-auto rounded-xl border border-slate-200 bg-white p-5">
        {exportOptions.showDocumentTitle && <h3 className="text-lg font-black text-slate-950">{matchedTemplate?.name || "OCR Export"}</h3>}
        <div className="mt-4 space-y-4">
          {exportPreviewResults.length === 0 ? (
            <p className="text-sm font-semibold text-slate-500">ไม่มีเนื้อหาที่เลือกสำหรับ Export</p>
          ) : (
            exportPreviewResults.map((result) => {
              const fieldType = getResultFieldType(result);
              return (
                <section key={result.roiId || result.fieldName} className="border-b border-slate-100 pb-4 last:border-b-0">
                  {exportOptions.showFieldNames && <h4 className="text-xs font-black uppercase tracking-wide text-slate-500">{result.fieldName}</h4>}
                  {fieldType === "table" ? (
                    <div className="mt-2 overflow-auto [&_.kv-record]:mb-3 [&_.kv-record]:rounded-lg [&_.kv-record]:border [&_.kv-record]:border-slate-200 [&_.kv-record]:bg-white [&_.kv-record]:p-3 [&_.kv-record]:shadow-sm [&_.kv-record_p]:my-1 [&_.kv-summary]:rounded-lg [&_.kv-summary]:border [&_.kv-summary]:border-emerald-200 [&_.kv-summary]:bg-emerald-50 [&_.kv-summary]:p-3 [&_td]:border [&_td]:border-slate-300 [&_td]:px-2.5 [&_td]:py-2 [&_th]:border [&_th]:border-slate-400 [&_th]:bg-slate-100 [&_th]:px-2.5 [&_th]:py-2" dangerouslySetInnerHTML={{ __html: renderHtmlTable(result) }} />
                  ) : fieldType === "image" ? (
                    <p className="mt-2 text-sm font-semibold text-slate-600">Image crop จะถูกใส่ในเอกสาร Word และ Excel</p>
                  ) : (
                    <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-slate-700">{result.extractedText || "-"}</p>
                  )}
                </section>
              );
            })
          )}
        </div>
      </div>
    );
  };

  const renderUserWorkflowGuide = () => (
    <section className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 shadow-sm">
      <div className="grid gap-2 lg:grid-cols-[minmax(180px,260px)_1fr] lg:items-center">
        <div className="min-w-0">
          <p className="text-[10px] font-black uppercase tracking-wide text-blue-600">ขั้นตอนปัจจุบัน</p>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <h2 className="text-sm font-black text-slate-950">{currentFlowStep.title}</h2>
            <span className="hidden text-xs font-semibold text-slate-500 sm:inline">{currentFlowStep.description}</span>
          </div>
        </div>
        <div className="grid min-w-0 grid-cols-2 gap-1 sm:grid-cols-4">
          {USER_FLOW_STEPS.map((step, index) => {
            const active = index === currentFlowIndex;
            const completed = index < currentFlowIndex;
            return (
              <div
                key={step.key}
                className={`flex items-center gap-2 rounded-lg border px-2.5 py-2 transition-colors ${
                  active
                    ? "border-blue-300 bg-blue-50 text-blue-950"
                    : completed
                      ? "border-emerald-200 bg-emerald-50 text-emerald-950"
                      : "border-slate-200 bg-slate-50 text-slate-500"
                }`}
              >
                <span
                  className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-black ${
                    active
                      ? "bg-blue-600 text-white ring-4 ring-blue-100"
                      : completed
                        ? "bg-emerald-600 text-white"
                        : "bg-white text-slate-400 ring-1 ring-slate-200"
                  }`}
                >
                  {completed ? "✓" : index + 1}
                </span>
                <span className="truncate text-[11px] font-black leading-tight">{step.title}</span>
              </div>
            );
          })}
        </div>
      </div>
      <p className="mt-2 hidden border-t border-slate-100 pt-2 text-xs font-semibold leading-relaxed text-slate-500 md:block">
        หมายเหตุ: {USER_STEP_ACTIONS[currentFlowStep.key][0]}
      </p>
    </section>
  );
  const exportPreviewPayload = exportJson || exportText ? buildExportPayload() : null;
  const exportFieldCount =
    exportPreviewPayload?.pages.reduce((sum, page) => sum + Object.keys(page.fields).length, 0) ?? 0;

  return (
    <main className="min-h-screen bg-slate-50 py-6 select-none">
      <div className="container mx-auto px-6 max-w-7xl space-y-5">
        <div className="rounded-2xl border border-slate-200 bg-white px-6 py-5 shadow-sm">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <p className="ui-caption font-semibold text-blue-600">พื้นที่ทำงานเอกสารอัจฉริยะ</p>
              <h1 className="ui-page-title mt-1 text-slate-950">ระบบอ่านเอกสารด้วย OCR</h1>
              <p className="ui-body mt-1 text-slate-500">
                อัปโหลดเอกสาร ตรวจขอบเขต ค้นหา Template เลือก Field ที่ต้องการอ่าน และตรวจสอบผล OCR ก่อนนำออกใช้งาน
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {authSession && (
                <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-bold text-slate-600">
                  {authSession.email} / user
                </div>
              )}
              {imagesList.length > 0 && (
                <button
                  type="button"
                  onClick={handleClearAndUploadNew}
                  className="ui-button-text rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-slate-700 transition-colors hover:bg-slate-50"
                >
                  เอกสารใหม่
                </button>
              )}
              <button
                type="button"
                onClick={handleLogout}
                className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-xs font-black text-slate-700 transition-colors hover:bg-slate-50"
              >
                <LogOut size={14} />
                ออกจากระบบ
              </button>
            </div>
          </div>
        </div>

        {currentStep !== "upload" && renderUserWorkflowGuide()}

        <div className="hidden text-center py-2">
          <h1 className="text-2xl font-extrabold tracking-tight text-slate-900">
            Intelligent OCR Portal
          </h1>
        </div>

        <div className="hidden bg-white border border-slate-200/80 rounded-2xl px-6 py-4 flex items-center justify-between shadow-sm">
          <div className="flex items-center gap-2.5">
            <div className="w-2.5 h-2.5 rounded-full bg-indigo-600 shadow-sm shadow-indigo-600/30 animate-pulse"></div>
            <span className="text-xs font-bold tracking-wide text-slate-700 uppercase">
              Intelligent OCR Studio v1.2
              {imagesList.length > 0 && ` (Active: หน้า ${currentIndex + 1}/${imagesList.length})`}
            </span>
          </div>

          <div className="flex items-center">
            <a
              href="/admin"
              className="mr-3 flex items-center gap-1.5 text-xs font-bold px-4 py-2 bg-white border border-slate-200 text-slate-600 rounded-xl hover:bg-slate-50 hover:text-indigo-600 hover:border-indigo-200 transition-all shadow-sm active:scale-98"
            >
              Admin
            </a>
            {imagesList.length > 0 && (
              <button
                type="button"
                onClick={handleClearAndUploadNew}
                className="flex items-center gap-1.5 text-xs font-bold px-4 py-2 bg-white border border-slate-200 text-slate-600 rounded-xl hover:bg-slate-50 hover:text-indigo-600 hover:border-indigo-200 transition-all shadow-sm active:scale-98"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" />
                </svg>
                เปลี่ยนไฟล์ภาพใหม่
              </button>
            )}
          </div>
        </div>

        {currentStep === "upload" && (
          <UploadZone onUploadSuccess={handleUploadSuccess} />
        )}

        {currentStep === "adjust" && (
          <AdjustZone
            imagesList={originalImagesList.length > 0 ? originalImagesList : imagesList}
            currentIndex={currentIndex}
            onIndexChange={(nextIdx) => setCurrentIndex(nextIdx)}
            pagesConfig={pagesConfig}
            setPagesConfig={setPagesConfig}
            onBatchConfirm={handleBatchConfirm}
          />
        )}

        {currentStep === "studio" && (
          <>
            {matchedTemplate ? (
              <MatchedTemplateWorkspaceZone
                matchedTemplate={matchedTemplate}
                previewUrl={imagesList[currentIndex] || previewUrl}
                image={imagesList[currentIndex] || image}
                brightness={pagesConfig[currentIndex]?.brightness ?? 100}
                contrast={pagesConfig[currentIndex]?.contrast ?? 100}
                rotation={pagesConfig[currentIndex]?.rotation ?? 0}
                rois={rois}
                setRois={setRois}
                selectedId={selectedId}
                setSelectedId={setSelectedId}
                onBackToAdjust={() => setCurrentStep("adjust")}
                deleteROI={(id) => setRois((p) => p.filter((roi) => roi.id !== id))}
                isLoading={isLoading}
                onRunOCR={handleRunOCR}
                onRunFullPageOCR={handleRunFullPageOCR}
                ocrProgress={ocrProgress}
                currentIndex={currentIndex}
                imagesList={imagesList}
                onSwitchToCustom={() => {
                  setRois((previous) => {
                    const resolvedParentIds = new Set(
                      previous
                        .filter((roi) => roi.isResolvedBlock && typeof roi.parentRoiId === "number")
                        .map((roi) => roi.parentRoiId as number)
                    );
                    return previous.filter((roi) => !(roi.roiMode === "flexible" && resolvedParentIds.has(roi.id)));
                  });
                  setMatchedTemplate(null);
                  setTemplateDetectionNotice(null);
                  setSelectedId((current) => {
                    const selectedRoi = rois.find((roi) => roi.id === current);
                    return selectedRoi?.roiMode === "flexible" && !selectedRoi.isResolvedBlock ? null : current;
                  });
                  setClassificationStatus("เปิด Custom OCR ต่อจาก ROI ของ Template ที่ตรวจพบ สามารถเพิ่มหรือแก้ไขกรอบได้ตามต้องการ");
                }}
                onIndexChange={(nextIdx) => {
                  setCurrentIndex(nextIdx);
                  setSelectedId(null);
                }}
              />
            ) : (
              <WorkspaceZone
                previewUrl={imagesList[currentIndex] || previewUrl}
                image={imagesList[currentIndex] || image}
                brightness={pagesConfig[currentIndex]?.brightness ?? 100}
                contrast={pagesConfig[currentIndex]?.contrast ?? 100}
                rotation={pagesConfig[currentIndex]?.rotation ?? 0}
                rois={rois}
                setRois={setRois}
                selectedId={selectedId}
                setSelectedId={setSelectedId}
                onBackToAdjust={() => setCurrentStep("adjust")}
                deleteROI={(id) => setRois((p) => p.filter((roi) => roi.id !== id))}
                isLoading={isLoading}
                onRunOCR={handleRunOCR}
                onRunFullPageOCR={handleRunFullPageOCR}
                ocrProgress={ocrProgress}
                rightPanelTopContent={templateDetectionNotice ? <NoTemplateDetectionCard notice={templateDetectionNotice} /> : null}
                currentIndex={currentIndex}
                imagesList={imagesList}
                onIndexChange={(nextIdx) => {
                  setCurrentIndex(nextIdx);
                  setSelectedId(null);
                }}
              />
            )}
          </>
        )}

        {currentStep === "editor" && (
          <>
            <GroundTruthEditorZone
              previewUrl={imagesList[currentIndex] || previewUrl}
              rois={rois}
              ocrResults={ocrResults}
              setOcrResults={setOcrResults}
              onBackToStudio={() => setCurrentStep("studio")}
              imageList={imagesList}
              currentImageIndex={currentIndex}
              onImageIndexChange={(nextIdx) => setCurrentIndex(nextIdx)}
            />
            {textPreviewItems.length > 0 && (
              <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
                <div className="flex flex-col gap-2 border-b border-slate-100 bg-white px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    <h2 className="text-sm font-black text-slate-900">Text Preview</h2>
                    <p className="mt-0.5 text-xs font-semibold text-slate-500">
                      แสดงค่าข้อความแบบ Key-Value จาก Ground Truth ปัจจุบันของหน้า {currentIndex + 1}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {textPreviewCopyStatus && (
                      <span className="text-[10px] font-black text-emerald-600">{textPreviewCopyStatus}</span>
                    )}
                    <button
                      type="button"
                      onClick={handleCopyTextPreview}
                      className="rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-2 text-xs font-black text-indigo-700 hover:bg-indigo-100"
                    >
                      คัดลอก
                    </button>
                  </div>
                </div>
                <div className="max-h-56 overflow-auto bg-slate-50/60 p-3">
                  <dl className="grid grid-cols-1 gap-2 md:grid-cols-2">
                    {textPreviewItems.map((item) => (
                      <div key={item.id} className="grid gap-1 rounded-xl border border-slate-200 bg-white px-3 py-2 sm:grid-cols-[minmax(120px,0.45fr)_minmax(0,1fr)]">
                        <dt className="min-w-0 truncate text-[11px] font-black text-slate-700" title={item.key}>
                          {item.key}
                        </dt>
                        <dd className="min-w-0 whitespace-pre-wrap break-words text-xs font-semibold leading-relaxed text-slate-600" title={item.value || "-"}>
                          {item.value || <span className="text-slate-400">-</span>}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </div>
              </section>
            )}
            <section className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm space-y-4">
              <div>
                <h2 className="text-sm font-black text-slate-800 uppercase tracking-wide">Actions</h2>
                <p className="text-xs font-semibold text-slate-500">
                  ส่งออกผลลัพธ์หรือส่งคำขอให้ผู้ดูแลระบบตรวจสอบ Template
                </p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <button
                  type="button"
                  onClick={handleOpenDefaultExport}
                  className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-xs font-black text-slate-700 hover:bg-slate-50"
                >
                  Export
                </button>
                <button
                  type="button"
                  onClick={handleOpenExportWord}
                  className="hidden rounded-xl border border-slate-200 bg-white px-4 py-3 text-xs font-black text-slate-700 hover:bg-slate-50"
                >
                  Export Word
                </button>
                <div className="hidden rounded-xl border border-slate-200 bg-white p-2">
                  <select
                    value={excelExportMode}
                    onChange={(event) => setExcelExportMode(event.target.value as "fields" | "tables" | "fields_tables")}
                    className="mb-2 w-full rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5 text-[10px] font-bold text-slate-600 outline-none"
                  >
                    <option value="fields_tables">Fields + Tables</option>
                    <option value="fields">Fields</option>
                    <option value="tables">Tables</option>
                  </select>
                  <button
                    type="button"
                    onClick={handleOpenExportExcel}
                    className="w-full rounded-lg bg-emerald-600 px-3 py-2 text-xs font-black text-white hover:bg-emerald-700"
                  >
                    Export Excel
                  </button>
                </div>
                <button
                  type="button"
                  onClick={handleOpenExportJson}
                  className="hidden rounded-xl border border-slate-200 bg-white px-4 py-3 text-xs font-black text-slate-700 hover:bg-slate-50"
                >
                  ส่งออก JSON
                </button>
                <button
                  type="button"
                  onClick={handleOpenExportImages}
                  className="hidden rounded-xl border border-slate-200 bg-white px-4 py-3 text-xs font-black text-slate-700 hover:bg-slate-50"
                >
                  Export Images ZIP
                </button>
                <button
                  type="button"
                  onClick={() => setIsTemplateRequestOpen(true)}
                  className="rounded-xl bg-indigo-600 px-4 py-3 text-xs font-black text-white shadow-sm hover:bg-indigo-700"
                >
                  ส่งคำขอ Template ใหม่
                </button>
              </div>
            </section>
            <TemplateRequestPanel
              imagesList={imagesList}
              sourceFileId={uploadedSourceFileId}
              sourceFileName={uploadedSourceFileName}
              rois={rois}
              ocrResults={ocrResults}
              isOpen={isTemplateRequestOpen}
              onClose={() => setIsTemplateRequestOpen(false)}
            />
            {isExportMenuOpen && (
              <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-4">
                <section className="flex h-[min(820px,88vh)] w-[min(1420px,calc(100vw-48px))] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl">
                  <div className="flex h-[88px] shrink-0 items-start justify-between gap-3 border-b border-slate-200 px-5 py-4">
                    <div>
                      <h2 className="text-sm font-black uppercase tracking-wide text-slate-900">Export Preview</h2>
                      <p className="mt-1 text-xs font-semibold text-slate-500">
                        ตรวจตัวอย่างจากค่า OCR/Ground Truth ปัจจุบันก่อนสร้างไฟล์จริง
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => setIsExportMenuOpen(false)}
                      className="rounded-xl bg-slate-900 px-4 py-2 text-xs font-black text-white hover:bg-slate-800"
                    >
                      Close
                    </button>
                  </div>

                  <div className={`grid min-h-0 flex-1 gap-0 overflow-hidden ${
                    showTableExportConfigPanel
                      ? "lg:grid-cols-[260px_320px_minmax(0,1fr)]"
                      : "lg:grid-cols-[260px_minmax(0,1fr)]"
                  }`}>
                    <aside className="space-y-5 overflow-auto border-b border-slate-200 bg-slate-50 p-5 lg:border-b-0 lg:border-r">
                      <div>
                        <p className="text-[11px] font-black uppercase tracking-wide text-slate-500">รูปแบบไฟล์</p>
                        <div className="mt-2 grid grid-cols-2 gap-2">
                          {visibleExportFormats.map((format) => (
                            <button
                              key={format.key}
                              type="button"
                              onClick={() => setExportFormat(format.key)}
                              className={`rounded-lg border px-3 py-2 text-xs font-black ${
                                exportFormat === format.key
                                  ? "border-blue-300 bg-blue-50 text-blue-700"
                                  : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
                              }`}
                            >
                              {format.label}
                            </button>
                          ))}
                        </div>
                      </div>

                      <div>
                        <p className="text-[11px] font-black uppercase tracking-wide text-slate-500">ข้อมูลที่ต้องการส่งออก</p>
                        <div className="mt-2 space-y-2">
                          {exportContentChoices.length === 0 && (
                            <div className="rounded-lg border border-dashed border-slate-300 bg-white px-3 py-2 text-xs font-bold text-slate-500">
                              ไม่มีข้อมูลสำหรับ Export
                            </div>
                          )}
                          {exportContentChoices.map(({ key, label }) => (
                            <div key={key} className="rounded-lg border border-slate-200 bg-white px-3 py-2">
                              <label className="flex items-center gap-2 text-xs font-bold text-slate-700">
                                <input
                                  type="checkbox"
                                  checked={exportContent[key]}
                                  onChange={() => toggleExportContent(key)}
                                  className="h-4 w-4 rounded border-slate-300 text-blue-600"
                                />
                                {label}
                              </label>
                              {key === "tables" && exportContent.tables && (
                                <div className="mt-2 grid grid-cols-2 gap-2 pl-6">
                                  {(["structure", "key_value"] as const).map((mode) => (
                                    <button
                                      key={mode}
                                      type="button"
                                      onClick={() => setAllTableExportMode(mode)}
                                      className={`rounded-lg border px-2 py-1.5 text-[10px] font-black transition-colors ${
                                        tableExportMode === mode
                                          ? "border-indigo-300 bg-indigo-50 text-indigo-700"
                                          : "border-slate-200 bg-slate-50 text-slate-600 hover:bg-slate-100"
                                      }`}
                                    >
                                      {mode === "structure" ? "Structure" : "Key-Value"}
                                    </button>
                                  ))}
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>

                      <div>
                        <p className="text-[11px] font-black uppercase tracking-wide text-slate-500">ตัวเลือกการแสดงผล</p>
                        <div className="mt-2 space-y-2">
                          {[
                            ["showDocumentTitle", "แสดงชื่อเอกสาร"],
                            ["showFieldNames", "แสดงชื่อ Field"],
                          ].map(([key, label]) => (
                            <label key={key} className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-700">
                              <input
                                type="checkbox"
                                checked={exportOptions[key as keyof ExportDisplayOptions]}
                                onChange={() => toggleExportOption(key as keyof ExportDisplayOptions)}
                                className="h-4 w-4 rounded border-slate-300 text-blue-600"
                              />
                              {label}
                            </label>
                          ))}
                        </div>
                      </div>
                    </aside>

                    {showTableExportConfigPanel && (
                      <aside className="min-h-0 overflow-auto border-b border-slate-200 bg-white p-5 lg:border-b-0 lg:border-r">
                        <div className="mb-3">
                          <p className="text-[11px] font-black uppercase tracking-wide text-slate-500">Table Export</p>
                          <h3 className="mt-1 text-sm font-black text-slate-950">Key-Value Columns</h3>
                          <p className="mt-1 text-xs font-semibold text-slate-500">
                            เลือกส่วนตารางเนื้อหา ส่วนสรุป และ column ที่ต้องการ export
                          </p>
                        </div>
                        {renderTableExportConfigPanel()}
                      </aside>
                    )}

                    <div className="flex min-h-0 flex-col overflow-hidden bg-slate-100 p-4">
                      <div className="mb-3 flex items-center justify-between gap-3">
                        <div>
                          <p className="text-[11px] font-black uppercase tracking-wide text-slate-500">Preview</p>
                          <h3 className="text-sm font-black text-slate-950">{exportFormats.find((format) => format.key === exportFormat)?.label}</h3>
                        </div>
                        <span className="rounded-full bg-white px-3 py-1 text-[10px] font-black text-slate-500 shadow-sm">
                          {exportPreviewResults.length} fields
                        </span>
                      </div>
                      <div className="min-h-0 flex-1">
                        {renderExportPreview()}
                      </div>
                    </div>
                  </div>

                  <div className="flex h-[72px] shrink-0 flex-col gap-3 border-t border-slate-200 bg-white px-5 py-4 sm:flex-row sm:items-center sm:justify-end">

                    <button
                      type="button"
                      onClick={() => requestExport(exportFormat)}
                      disabled={!hasSelectedExportContent}
                      className="rounded-xl bg-indigo-600 px-5 py-2.5 text-xs font-black text-white shadow-sm hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:text-slate-500"
                    >
                      Export {exportFormats.find((format) => format.key === exportFormat)?.label}
                    </button>
                  </div>
                </section>
              </div>
            )}
            {exportJson && (
              <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-4">
                <section className="flex max-h-[86vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl">
                  <div className="flex flex-col gap-3 border-b border-slate-200 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
                    <div>
                      <h2 className="text-sm font-black uppercase tracking-wide text-slate-900">Export JSON</h2>
                      <p className="mt-1 text-xs font-semibold text-slate-500">
                        JSON แบบย่อจากผล OCR ที่ตรวจและแก้ไขแล้ว
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={handleCopyExportJson}
                        className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-black text-slate-700 hover:bg-slate-50"
                      >
                        Copy JSON
                      </button>
                      <button
                        type="button"
                        onClick={() => downloadTextFile(`ocr-export-${Date.now()}.json`, exportJson)}
                        className="rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-2 text-xs font-black text-indigo-700 hover:bg-indigo-100"
                      >
                        Download JSON
                      </button>
                      <button
                        type="button"
                        onClick={() => setExportJson("")}
                        className="rounded-xl bg-slate-900 px-4 py-2 text-xs font-black text-white hover:bg-slate-800"
                      >
                        ปิด
                      </button>
                    </div>
                  </div>
                  {copyStatus && (
                    <div className="border-b border-slate-100 bg-emerald-50 px-5 py-2 text-xs font-bold text-emerald-700">
                      {copyStatus}
                    </div>
                  )}
                  {exportPreviewPayload && (
                    <div className="grid gap-3 border-b border-slate-100 bg-slate-50 px-5 py-3 text-xs font-bold text-slate-600 sm:grid-cols-3">
                      <div>Template: <span className="text-slate-900">{exportPreviewPayload.template || "Custom OCR"}</span></div>
                      <div>Pages: <span className="tabular-nums text-slate-900">{exportPreviewPayload.page_count}</span></div>
                      <div>Fields: <span className="tabular-nums text-slate-900">{exportFieldCount}</span></div>
                    </div>
                  )}
                  <div className="min-h-0 flex-1 overflow-auto bg-slate-950 p-4">
                    <pre className="whitespace-pre-wrap break-words font-mono text-[12px] leading-relaxed text-slate-100">
                      {exportJson}
                    </pre>
                  </div>
                </section>
              </div>
            )}
            {exportText && (
              <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-4">
                <section className="flex max-h-[86vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl">
                  <div className="flex flex-col gap-3 border-b border-slate-200 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
                    <div>
                      <h2 className="text-sm font-black uppercase tracking-wide text-slate-900">Export Text</h2>
                      <p className="mt-1 text-xs font-semibold text-slate-500">
                        Plain text summary from the reviewed OCR result.
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={handleCopyExportText}
                        className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-black text-slate-700 hover:bg-slate-50"
                      >
                        Copy Text
                      </button>
                      <button
                        type="button"
                        onClick={() => downloadTextFile(`ocr-export-${Date.now()}.txt`, exportText, "text/plain")}
                        className="rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-2 text-xs font-black text-indigo-700 hover:bg-indigo-100"
                      >
                        Download TXT
                      </button>
                      <button
                        type="button"
                        onClick={() => setExportText("")}
                        className="rounded-xl bg-slate-900 px-4 py-2 text-xs font-black text-white hover:bg-slate-800"
                      >
                        Close
                      </button>
                    </div>
                  </div>
                  {copyStatus && (
                    <div className="border-b border-slate-100 bg-emerald-50 px-5 py-2 text-xs font-bold text-emerald-700">
                      {copyStatus}
                    </div>
                  )}
                  {exportPreviewPayload && (
                    <div className="grid gap-3 border-b border-slate-100 bg-slate-50 px-5 py-3 text-xs font-bold text-slate-600 sm:grid-cols-3">
                      <div>Template: <span className="text-slate-900">{exportPreviewPayload.template || "Custom OCR"}</span></div>
                      <div>Pages: <span className="tabular-nums text-slate-900">{exportPreviewPayload.page_count}</span></div>
                      <div>Fields: <span className="tabular-nums text-slate-900">{exportFieldCount}</span></div>
                    </div>
                  )}
                  <div className="min-h-0 flex-1 overflow-auto bg-slate-950 p-4">
                    <pre className="whitespace-pre-wrap break-words font-mono text-[12px] leading-relaxed text-slate-100">
                      {exportText}
                    </pre>
                  </div>
                </section>
              </div>
            )}
          </>
        )}

        {isTemplateDecisionOpen && (
          <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/45 px-4 backdrop-blur-sm">
            <section className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 text-center shadow-2xl">
              <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-indigo-50">
                <div className="h-8 w-8 animate-spin rounded-full border-4 border-indigo-200 border-t-indigo-600" />
              </div>
              <h2 className="mt-5 text-base font-black text-slate-950">กำลังค้นหา Template</h2>
              <p className="mt-2 text-sm font-semibold leading-relaxed text-slate-500">
                ระบบกำลังวิเคราะห์เอกสารและเลือก Template ที่เหมาะสมที่สุด
              </p>
              <div className="mt-4 rounded-xl border border-slate-100 bg-slate-50 px-4 py-3 text-xs font-bold text-slate-700">
                {templateDecisionStatus || "กำลังประมวลผล..."}
              </div>
            </section>
          </div>
        )}
      </div>
    </main>
  );
}

export default function Home() {
  return (
    <AuthGate requiredRole="user">
      <HomeWorkspace />
    </AuthGate>
  );
}
