"use client";

import { ArrowLeft, GripVertical, Loader2, ScanSearch } from "lucide-react";
import { DragEvent, SetStateAction, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { WorkspacePage } from "../../shared/workspace/BaseWorkspace";
import { authHeaders } from "../../auth/session";
import WorkspaceCustomEditor from "../../shared/workspace/WorkspaceCustomEditor";
import { DEFAULT_WORKSPACE_IMAGE_METRICS, ratioToImageBox, WorkspaceImageMetrics } from "../../shared/workspace/roiGeometry";
import { IgnoreRegion, ROI, RoiRatio, TemplateField } from "../../types/ocr";
import {
  ImageVerificationCategory,
  createImageVerificationCategory,
  deleteImageVerificationCategory,
  listImageVerificationCategories,
  TemplateStepTestItem,
  TemplateStepTestResult,
  testTemplateExtractionFields,
  testTemplateVerificationAnchors,
  updateImageVerificationCategory,
  ADMIN_API_BASE_URL,
} from "../adminApi";
import TemplateFieldBasicForm from "./TemplateFieldBasicForm";

interface WorkspaceTemplateEditorProps {
  templateId: string;
  pages: WorkspacePage[];
  currentPage: number;
  onPageChange: (pageIndex: number) => void;
  fields: TemplateField[];
  ignoreRegions: IgnoreRegion[];
  onAddField: (roi?: RoiRatio, defaults?: Partial<TemplateField>) => void;
  onUpdateField: (fieldId: string, patch: Partial<TemplateField>) => void;
  onReorderFields: (orderedFieldIds: string[]) => void;
  onReplacePageExtractionFields: (pageNumber: number, fields: { roi: RoiRatio; defaults: Partial<TemplateField> }[]) => void;
  onReplaceExtractionFieldsForPages: (items: { pageNumber: number; fields: { roi: RoiRatio; defaults: Partial<TemplateField> }[] }[]) => void;
  onDeleteField: (fieldId: string) => void;
  onAddIgnoreRegion: (roi?: RoiRatio) => void;
  onUpdateIgnoreRegion: (regionId: string, patch: Partial<IgnoreRegion>) => void;
  onDeleteIgnoreRegion: (regionId: string) => void;
  onGenerateEmbedding: () => void;
  onRunTestMode: () => void;
  onBeforeRunTest?: () => Promise<void>;
  testModeLabel?: string;
  onBackToAdjust?: () => void;
}

type EditorStep = "extraction_fields" | "verification_anchors";
type EditorMode = "extraction_fields" | "verification_anchors" | "ignore_regions";
type AdminRoi = ROI & {
  sourceId?: string;
  workspaceKind: EditorMode;
  pageIndex?: number;
};

interface LayoutDetectedRegion {
  field_name?: string;
  type?: "text" | "table" | "image";
  data_type?: "text" | "table" | "image";
  extraction_method?: string;
  confidence?: number;
  roi?: {
    page_number?: number;
    x_ratio?: number;
    y_ratio?: number;
    width_ratio?: number;
    height_ratio?: number;
  };
}

interface LayoutDetectedPage {
  page_index: number;
  page_number: number;
  image_width: number;
  image_height: number;
  regions: LayoutDetectedRegion[];
  message?: string | null;
}

interface LayoutAnalysisResponse {
  success?: boolean;
  pages?: LayoutDetectedPage[];
  detail?: string;
  error?: string;
}

const inputClass =
  "w-full rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[11px] font-semibold text-slate-700 outline-none focus:border-indigo-500";

const fieldImageCategories = (value?: string | string[]) =>
  (Array.isArray(value) ? value : value ? [value] : [])
    .map((item) => String(item || "").trim())
    .filter(Boolean);

const normalizeTableRows = (rows?: unknown): string[][] | null => {
  if (!Array.isArray(rows) || rows.length === 0) return null;
  const normalized = rows
    .filter((row): row is unknown[] => Array.isArray(row))
    .map((row) => row.map((cell) => String(cell ?? "").trim()));
  return normalized.some((row) => row.some(Boolean)) ? normalized : null;
};

const parseMarkdownTable = (value?: string | null): string[][] | null => {
  if (!value || !value.includes("|")) return null;
  const rows = value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.includes("|"))
    .map((line) => line.replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim()))
    .filter((row) => !row.every((cell) => /^:?-{3,}:?$/.test(cell)));
  return normalizeTableRows(rows);
};

const parseHtmlTable = (value?: string | null): string[][] | null => {
  if (!value || typeof DOMParser === "undefined") return null;
  try {
    const doc = new DOMParser().parseFromString(value, "text/html");
    const rows = Array.from(doc.querySelectorAll("tr")).map((row) =>
      Array.from(row.querySelectorAll("th,td")).map((cell) => cell.textContent?.trim() || "")
    );
    return normalizeTableRows(rows);
  } catch {
    return null;
  }
};

type StructuredPreviewCell = {
  row: number;
  col: number;
  text: string;
  rowSpan: number;
  colSpan: number;
  hidden: boolean;
};

const asPreviewNumber = (value: unknown, fallback = 0) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
};

const getStructuredPreviewCells = (structured?: TemplateStepTestItem["tableStructured"] | null): StructuredPreviewCell[] => {
  if (!Array.isArray(structured?.cells)) return [];
  return structured.cells
    .map((cell) => ({
      row: Math.max(0, asPreviewNumber(cell.row)),
      col: Math.max(0, asPreviewNumber(cell.col)),
      text: String(cell.groundTruth ?? cell.text ?? cell.ocrText ?? ""),
      rowSpan: Math.max(1, asPreviewNumber(cell.rowSpan, 1)),
      colSpan: Math.max(1, asPreviewNumber(cell.colSpan, 1)),
      hidden: Boolean(cell.hidden),
    }))
    .sort((a, b) => a.row - b.row || a.col - b.col);
};

const getStructuredPreviewRows = (structured?: TemplateStepTestItem["tableStructured"] | null): string[][] | null => {
  if (!Array.isArray(structured?.rows)) return null;
  const rows = structured.rows
    .filter((row) => Array.isArray(row))
    .map((row) => row.map((cell) => String(cell ?? "")));
  return rows.length > 0 ? rows : null;
};

const getTableRowsFromTestItem = (item: TemplateStepTestItem): string[][] | null =>
  normalizeTableRows(item.tableStructured?.rows) ||
  normalizeTableRows(item.tableRows) ||
  parseHtmlTable(item.tableHtml) ||
  parseMarkdownTable(item.ocrText) ||
  parseMarkdownTable(item.actualText);

const isTableTestItem = (item: TemplateStepTestItem) =>
  item.dataType === "table" ||
  item.extractionMethod === "table_recognition_v2" ||
  item.extractionMethod === "ocr_table" ||
  Boolean(item.tableStructured?.rows?.length || item.tableRows?.length || item.tableHtml || getTableRowsFromTestItem(item));

const isImageTestItem = (item: TemplateStepTestItem) =>
  item.dataType === "image" ||
  item.extractionMethod === "extract_image" ||
  item.anchorType === "image" ||
  Boolean(item.imageCategory || item.imageCategoryLabel || item.predictedImageCategoryLabel);

const testItemTypeLabel = (item: TemplateStepTestItem) => {
  if (isTableTestItem(item)) return "Table";
  if (isImageTestItem(item)) return "Image";
  return "Text";
};

const getVerificationItemScore = (item: TemplateStepTestItem) =>
  item.anchorType === "image"
    ? item.evidenceScore ?? item.fieldScore ?? item.score ?? 0
    : item.textMatchScore ?? item.fieldScore ?? item.score ?? 0;

const stableNumericId = (value: string) =>
  Math.abs(value.split("").reduce((hash, char) => (hash * 31 + char.charCodeAt(0)) | 0, 7));

const clampRatio = (value: number) => Math.min(1, Math.max(0, value));

const isAnchor = (field: TemplateField) => field.useForVerification;

const fieldToRoiType = (field: TemplateField): ROI["type"] => {
  if (isAnchor(field) && field.dataType === "table") return "text";
  if (field.dataType === "table") return "table";
  if (field.dataType === "image") return "image";
  return "text";
};

const roiTypeToFieldPatch = (type?: ROI["type"]): Partial<TemplateField> => {
  if (type === "table") return { dataType: "table", extractionMethod: "table_recognition_v2", roiMode: "fix", expectedContent: null };
  if (type === "image") return { dataType: "image", extractionMethod: "extract_image", roiMode: "fix", expectedContent: null };
  return { dataType: "text", extractionMethod: "paddle_thai_ocr" };
};

const roiTypeToAnchorFieldPatch = (type?: ROI["type"]): Partial<TemplateField> => {
  if (type === "image") return { dataType: "image", extractionMethod: "extract_image", expectedText: "", imageCategory: undefined };
  return { dataType: "text", extractionMethod: "ocr_text" };
};


const roiToRatio = (roi: ROI, pageNumber: number, metrics: WorkspaceImageMetrics): RoiRatio => ({
  pageNumber,
  xRatio: clampRatio((roi.x - metrics.imageOffsetX) / Math.max(metrics.imageWidth, 1)),
  yRatio: clampRatio((roi.y - metrics.imageOffsetY) / Math.max(metrics.imageHeight, 1)),
  widthRatio: clampRatio(roi.width / Math.max(metrics.imageWidth, 1)),
  heightRatio: clampRatio(roi.height / Math.max(metrics.imageHeight, 1)),
});

const normalizeLayoutRegionType = (region: LayoutDetectedRegion): "text" | "table" | "image" => {
  if (region.type === "table" || region.data_type === "table") return "table";
  if (region.type === "image" || region.data_type === "image") return "image";
  return "text";
};

const layoutRegionToDetectedField = (
  region: LayoutDetectedRegion,
  pageNumber: number,
  fieldNumber: number
): { roi: RoiRatio; defaults: Partial<TemplateField> } | null => {
  const roi = region.roi;
  if (!roi) return null;

  const xRatio = Number(roi.x_ratio);
  const yRatio = Number(roi.y_ratio);
  const widthRatio = Number(roi.width_ratio);
  const heightRatio = Number(roi.height_ratio);
  if (![xRatio, yRatio, widthRatio, heightRatio].every(Number.isFinite)) return null;

  const safeRoi = {
    pageNumber,
    xRatio: clampRatio(xRatio),
    yRatio: clampRatio(yRatio),
    widthRatio: clampRatio(widthRatio),
    heightRatio: clampRatio(heightRatio),
  };
  if (safeRoi.widthRatio <= 0 || safeRoi.heightRatio <= 0) return null;

  const dataType = normalizeLayoutRegionType(region);
  const fieldName = `field_${fieldNumber}`;
  return {
    roi: safeRoi,
    defaults: {
      fieldName,
      displayLabel: fieldName,
      dataType,
      extractionMethod: dataType === "image" ? "extract_image" : dataType === "table" ? "table_recognition_v2" : "paddle_thai_ocr",
      userSelectable: true,
      defaultSelected: true,
      useForVerification: false,
      requiredForVerification: false,
      roiPadding: 0,
      sortOrder: fieldNumber,
    },
  };
};

const fieldToRoi = (field: TemplateField, metrics: WorkspaceImageMetrics): AdminRoi => {
  const box = ratioToImageBox(field.roi, metrics);
  const anchor = isAnchor(field);
  return {
    id: stableNumericId(`${anchor ? "anchor" : "field"}:${field.id}`),
    sourceId: field.id,
    workspaceKind: anchor ? "verification_anchors" : "extraction_fields",
    fieldName: field.fieldName,
    x: box.x,
    y: box.y,
    width: box.width,
    height: box.height,
    pageIndex: field.pageNumber - 1,
    type: fieldToRoiType(field),
    roiMode: field.roiMode === "flexible" ? "flexible" : "fix",
    expectedContent: field.roiMode === "flexible" ? "text" : null,
  };
};

const ignoreToRoi = (region: IgnoreRegion, metrics: WorkspaceImageMetrics): AdminRoi => {
  const box = ratioToImageBox(region.roi, metrics);
  return {
    id: stableNumericId(`ignore:${region.id}`),
    sourceId: region.id,
    workspaceKind: "ignore_regions",
    fieldName: `Ignore: ${region.fieldName}`,
    x: box.x,
    y: box.y,
    width: box.width,
    height: box.height,
    pageIndex: region.pageNumber - 1,
    type: "text",
  };
};

export default function WorkspaceTemplateEditorV2({
  templateId,
  pages,
  currentPage,
  onPageChange,
  fields,
  ignoreRegions,
  onAddField,
  onUpdateField,
  onReorderFields,
  onReplacePageExtractionFields,
  onReplaceExtractionFieldsForPages,
  onDeleteField,
  onAddIgnoreRegion,
  onUpdateIgnoreRegion,
  onDeleteIgnoreRegion,
  onBeforeRunTest,
  onRunTestMode,
  testModeLabel = "Test Mode",
  onBackToAdjust,
}: WorkspaceTemplateEditorProps) {
  const [step, setStep] = useState<EditorStep>("extraction_fields");
  const [mode, setMode] = useState<EditorMode>("extraction_fields");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [imageMetrics, setImageMetrics] = useState<WorkspaceImageMetrics>(DEFAULT_WORKSPACE_IMAGE_METRICS);
  const [testStatus, setTestStatus] = useState("");
  const [testResult, setTestResult] = useState<TemplateStepTestResult | null>(null);
  const [testResultKind, setTestResultKind] = useState<"extraction" | "verification" | null>(null);
  const [testError, setTestError] = useState("");
  const [testAction, setTestAction] = useState<"extraction" | "verification" | null>(null);
  const [autoDetectStatus, setAutoDetectStatus] = useState("");
  const [autoDetectError, setAutoDetectError] = useState("");
  const [isAutoDetecting, setIsAutoDetecting] = useState(false);
  const [draggingFieldId, setDraggingFieldId] = useState<string | null>(null);
  const [imageCategories, setImageCategories] = useState<ImageVerificationCategory[]>([]);
  const [categoryError, setCategoryError] = useState("");
  const [categoryManagerOpen, setCategoryManagerOpen] = useState(false);
  const [categoryDrafts, setCategoryDrafts] = useState<Record<string, ImageVerificationCategory>>({});
  const [newCategory, setNewCategory] = useState<ImageVerificationCategory>({
    value: "",
    label: "",
    prompt: "",
    matchThreshold: 0.7,
    marginThreshold: 0.05,
    evidenceTemperature: 1,
    enabled: true,
  });
  const pendingRoiRef = useRef<{ mode: EditorMode; roi: RoiRatio } | null>(null);
  const currentPageNumber = currentPage + 1;
  const selectedPage = pages[currentPage];

  const reloadImageCategories = useCallback(async () => {
    try {
      setCategoryError("");
      const categories = await listImageVerificationCategories(false);
      setImageCategories(categories);
      setCategoryDrafts(Object.fromEntries(categories.map((category) => [category.value, category])));
    } catch (error) {
      setCategoryError(error instanceof Error ? error.message : "โหลดประเภทภาพไม่สำเร็จ");
    }
  }, []);

  useEffect(() => {
    const timerId = window.setTimeout(() => {
      void reloadImageCategories();
    }, 0);
    return () => window.clearTimeout(timerId);
  }, [reloadImageCategories]);

  const orderedFields = useMemo(
    () =>
      [...fields].sort(
        (left, right) =>
          left.pageNumber - right.pageNumber ||
          (left.sortOrder ?? 0) - (right.sortOrder ?? 0) ||
          left.fieldName.localeCompare(right.fieldName)
      ),
    [fields]
  );
  const activeImageCategories = useMemo(() => imageCategories.filter((category) => category.enabled), [imageCategories]);
  const extractionFields = orderedFields.filter((field) => !isAnchor(field));
  const verificationAnchors = orderedFields.filter(isAnchor);
  const currentPageExtractionFields = extractionFields.filter((field) => field.pageNumber === currentPageNumber);
  const currentPageAnchors = verificationAnchors.filter((field) => field.pageNumber === currentPageNumber);

  const extractionRois = useMemo(() => extractionFields.map((field) => fieldToRoi(field, imageMetrics)), [extractionFields, imageMetrics]);
  const anchorRois = useMemo(() => verificationAnchors.map((field) => fieldToRoi(field, imageMetrics)), [verificationAnchors, imageMetrics]);
  const ignoreRois = useMemo(() => ignoreRegions.map((region) => ignoreToRoi(region, imageMetrics)), [ignoreRegions, imageMetrics]);
  const activeRois = step === "verification_anchors" ? anchorRois : mode === "ignore_regions" ? ignoreRois : extractionRois;

  const selectedRoi = [...extractionRois, ...anchorRois, ...ignoreRois].find((roi) => roi.id === selectedId);
  const selectedField = selectedRoi?.workspaceKind === "extraction_fields" || selectedRoi?.workspaceKind === "verification_anchors"
    ? fields.find((field) => field.id === selectedRoi.sourceId)
    : null;
  const selectedIgnoreRegion = selectedRoi?.workspaceKind === "ignore_regions"
    ? ignoreRegions.find((region) => region.id === selectedRoi.sourceId)
    : null;
  const selectedAnchor = selectedField && isAnchor(selectedField) ? selectedField : null;
  const [anchorNameDraft, setAnchorNameDraft] = useState(selectedAnchor?.fieldName || "");
  const textAnchorsMissingExpected = verificationAnchors.filter(
    (anchor) => anchor.dataType !== "image" && !String(anchor.expectedText || "").trim()
  );
  const imageAnchorsMissingCategory = verificationAnchors.filter(
    (anchor) => anchor.dataType === "image" && fieldImageCategories(anchor.imageCategory).length === 0
  );
  const verificationAnchorsReady =
    verificationAnchors.length > 0 &&
    textAnchorsMissingExpected.length === 0 &&
    imageAnchorsMissingCategory.length === 0;
  const verificationBlockedMessage =
    verificationAnchors.length === 0
      ? "ต้องสร้าง Verification Anchor อย่างน้อย 1 รายการก่อนเข้าสู่ Test Mode"
      : textAnchorsMissingExpected.length > 0
        ? `กรุณากรอก Expected Text ให้ครบ (${textAnchorsMissingExpected.length} รายการ)`
        : imageAnchorsMissingCategory.length > 0
          ? `กรุณาเลือกประเภทภาพให้ Anchor รูปภาพให้ครบ (${imageAnchorsMissingCategory.length} รายการ)`
        : "";
  const extractionTestSignature = useMemo(
    () =>
      extractionFields
        .map((field) => `${field.id}:${field.pageNumber}:${field.fieldName}:${field.dataType}:${field.extractionMethod}:${field.roi.xRatio}:${field.roi.yRatio}:${field.roi.widthRatio}:${field.roi.heightRatio}`)
        .join("|"),
    [extractionFields]
  );
  const verificationTestSignature = useMemo(
    () =>
      verificationAnchors
        .map((field) => `${field.id}:${field.pageNumber}:${field.fieldName}:${field.dataType}:${field.extractionMethod}:${field.expectedText || ""}:${field.imageCategory || ""}:${field.roi.xRatio}:${field.roi.yRatio}:${field.roi.widthRatio}:${field.roi.heightRatio}`)
        .join("|"),
    [verificationAnchors]
  );
  const extractionTestPassed =
    testResultKind === "extraction" &&
    Boolean(testResult?.testedCount) &&
    testResult?.failedCount === 0 &&
    (testResult.fields || []).every((item) => item.passed);
  const verificationTestPassed =
    testResultKind === "verification" &&
    Boolean(testResult?.testedCount) &&
    testResult?.failedCount === 0 &&
    (testResult.anchors || []).every((item) => item.passed);

  useEffect(() => {
    setAnchorNameDraft(selectedAnchor?.fieldName || "");
  }, [selectedAnchor?.id, selectedAnchor?.fieldName]);

  useEffect(() => {
    if (testResultKind === "extraction") {
      setTestResult(null);
      setTestResultKind(null);
      setTestStatus("");
      setTestError("");
    }
  }, [extractionTestSignature]);

  useEffect(() => {
    if (testResultKind === "verification") {
      setTestResult(null);
      setTestResultKind(null);
      setTestStatus("");
      setTestError("");
    }
  }, [verificationTestSignature]);

  const commitAnchorName = () => {
    if (!selectedAnchor || anchorNameDraft === selectedAnchor.fieldName) return;
    onUpdateField(selectedAnchor.id, { fieldName: anchorNameDraft, displayLabel: anchorNameDraft });
  };

  const selectField = (field: TemplateField, toggle = true) => {
    const nextId = stableNumericId(`${isAnchor(field) ? "anchor" : "field"}:${field.id}`);
    setSelectedId((previous) => (toggle && previous === nextId ? null : nextId));
    if (field.pageNumber - 1 !== currentPage) onPageChange(field.pageNumber - 1);
  };

  const setSelectedRoiId = (value: number | null | ((previous: number | null) => number | null)) => {
    setSelectedId((previous) => {
      const next = typeof value === "function" ? value(previous) : value;
      const roi = [...extractionRois, ...anchorRois, ...ignoreRois].find((item) => item.id === next);
      if ((roi?.workspaceKind === "extraction_fields" || roi?.workspaceKind === "verification_anchors") && roi.sourceId) {
        const field = fields.find((item) => item.id === roi.sourceId);
        if (field && field.pageNumber - 1 !== currentPage) onPageChange(field.pageNumber - 1);
      }
      return next;
    });
  };

  const persistRois = (nextRois: SetStateAction<(ROI & { pageIndex?: number })[]>) => {
    const resolved = (typeof nextRois === "function" ? nextRois(activeRois) : nextRois) as AdminRoi[];
    const previousById = new Map(activeRois.map((roi) => [roi.id, roi]));

    resolved.forEach((roi) => {
      const previous = previousById.get(roi.id);
      if (!previous) {
        const ratio = roiToRatio(roi, currentPageNumber, imageMetrics);
        const optimisticFieldId = `local_field_from_roi_${roi.id}`;
        pendingRoiRef.current = { mode, roi: ratio };
        if (mode === "verification_anchors") {
          const index = verificationAnchors.length + 1;
          onAddField(ratio, {
            id: optimisticFieldId,
            fieldName: `verification_${index}`,
            displayLabel: `Verification ${index}`,
            dataType: "text",
            userSelectable: false,
            defaultSelected: false,
            useForVerification: true,
            requiredForVerification: false,
            extractionMethod: "ocr_text",
            roiPadding: 0,
            verificationWeight: 1,
            expectedText: "",
            matchType: "contains",
          });
          setSelectedId(stableNumericId(`anchor:${optimisticFieldId}`));
        } else if (mode === "ignore_regions") {
          onAddIgnoreRegion(ratio);
        } else {
          onAddField(ratio, {
            id: optimisticFieldId,
            fieldName: roi.fieldName,
            displayLabel: roi.fieldName,
            userSelectable: true,
            defaultSelected: true,
            useForVerification: false,
            requiredForVerification: false,
            extractionMethod: "paddle_thai_ocr",
            roiMode: "fix",
            expectedContent: null,
            roiPadding: 0,
          });
          setSelectedId(stableNumericId(`field:${optimisticFieldId}`));
        }
        return;
      }

      if (previous.x !== roi.x || previous.y !== roi.y || previous.width !== roi.width || previous.height !== roi.height) {
        const ratio = roiToRatio(roi, (roi.pageIndex ?? currentPage) + 1, imageMetrics);
        if ((previous.workspaceKind === "extraction_fields" || previous.workspaceKind === "verification_anchors") && previous.sourceId) {
          onUpdateField(previous.sourceId, { roi: ratio });
        }
        if (previous.workspaceKind === "ignore_regions" && previous.sourceId) {
          onUpdateIgnoreRegion(previous.sourceId, { roi: ratio });
        }
      }

      if ((previous.workspaceKind === "extraction_fields" || previous.workspaceKind === "verification_anchors") && previous.sourceId) {
        if (previous.fieldName !== roi.fieldName) {
          onUpdateField(previous.sourceId, { fieldName: roi.fieldName, displayLabel: roi.fieldName });
        }
        if (previous.type !== roi.type) {
          onUpdateField(previous.sourceId, previous.workspaceKind === "verification_anchors" ? roiTypeToAnchorFieldPatch(roi.type) : roiTypeToFieldPatch(roi.type));
        }
      }
    });

  };

  const anchorMethod = (anchor: TemplateField) => anchor.dataType === "image" ? "image_feature" : "ocr_text";

  const updateAnchorMethod = (anchor: TemplateField, value: string) => {
    if (value === "image_feature") {
      onUpdateField(anchor.id, { dataType: "image", extractionMethod: "extract_image", expectedText: "", imageCategory: anchor.imageCategory });
    } else {
      onUpdateField(anchor.id, { dataType: "text", extractionMethod: "ocr_text" });
    }
  };

  const renderAnchorSettings = (anchor: TemplateField) => {
    const method = anchorMethod(anchor);
    return (
      <section className="space-y-3 border-t border-amber-100 bg-amber-50/60 p-3">
        <h3 className="text-xs font-black uppercase tracking-wider text-amber-900">การตั้งค่า ROI</h3>
        <label className="space-y-1 block">
          <span className="text-[9px] font-black uppercase text-slate-400">ชื่อ field</span>
          <input
            className={inputClass}
            value={anchorNameDraft}
            onChange={(event) => setAnchorNameDraft(event.target.value)}
            onBlur={commitAnchorName}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.currentTarget.blur();
              }
            }}
          />
        </label>
        <label className="space-y-1 block">
          <span className="text-[9px] font-black uppercase text-slate-400">วิธีตรวจสอบ</span>
          <select className={inputClass} value={method} onChange={(event) => updateAnchorMethod(anchor, event.target.value)}>
            <option value="ocr_text">ข้อความที่ OCR อ่านได้</option>
            <option value="image_feature">ประเภทของรูปภาพ</option>
          </select>
        </label>
        {method === "image_feature" && (() => {
          const selectedCategories = fieldImageCategories(anchor.imageCategory);
          const invalidCategories = selectedCategories.filter(
            (value) => !activeImageCategories.some((category) => category.value === value)
          );
          return (
            <div className="space-y-2 rounded-xl border border-amber-100 bg-amber-50/40 p-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[9px] font-black uppercase text-slate-400">ประเภทภาพ</span>
                <button
                  type="button"
                  className="rounded-lg border border-amber-200 bg-white px-2.5 py-1.5 text-[10px] font-black text-amber-800"
                  onClick={() => setCategoryManagerOpen(true)}
                >
                  จัดการประเภทภาพ
                </button>
              </div>
              {selectedCategories.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {selectedCategories.map((value) => {
                    const category = activeImageCategories.find((option) => option.value === value);
                    return (
                      <span key={value} className="rounded-full bg-amber-600 px-2 py-0.5 text-[9px] font-black text-white">
                        {category?.label || value}
                      </span>
                    );
                  })}
                </div>
              )}
              <div className="max-h-36 space-y-1 overflow-y-auto rounded-lg border border-amber-100 bg-white p-2">
                {activeImageCategories.length === 0 ? (
                  <p className="text-[10px] font-semibold text-slate-500">ยังไม่มีประเภทภาพที่เปิดใช้งาน</p>
                ) : (
                  activeImageCategories.map((option) => {
                    const checked = selectedCategories.includes(option.value);
                    const nextCategories = checked
                      ? selectedCategories.filter((value) => value !== option.value)
                      : [...selectedCategories, option.value];
                    return (
                      <label key={option.value} className="flex items-start gap-2 rounded-lg px-2 py-1 text-[10px] font-bold text-slate-700 hover:bg-amber-50">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => onUpdateField(anchor.id, { imageCategory: nextCategories })}
                          className="mt-0.5"
                        />
                        <span>
                          <span className="block text-slate-800">{option.label}</span>
                          <span className="block font-semibold text-slate-400">{option.value}</span>
                        </span>
                      </label>
                    );
                  })
                )}
              </div>
              <p className="text-[10px] font-semibold leading-relaxed text-amber-800">
                เลือกได้มากกว่า 1 ประเภท ถ้าตรวจพบตรงกับประเภทใดประเภทหนึ่ง จะถือว่าผ่าน
              </p>
              {invalidCategories.length > 0 && (
                <p className="rounded-lg bg-red-50 px-2 py-1 text-[10px] font-semibold text-red-700">
                  ประเภทภาพนี้ไม่พบหรือถูกปิดใช้งาน: {invalidCategories.join(", ")}
                </p>
              )}
              {categoryError && <p className="text-[10px] font-semibold text-red-600">{categoryError}</p>}
            </div>
          );
        })()}
        {method === "ocr_text" && (
          <label className="space-y-1 block">
            <span className="text-[9px] font-black uppercase text-slate-400">Expected Text</span>
            <input
              required
              className={`${inputClass} ${!String(anchor.expectedText || "").trim() ? "border-red-300 bg-red-50/50 focus:border-red-500" : ""}`}
              value={anchor.expectedText || ""}
              onChange={(event) => onUpdateField(anchor.id, { expectedText: event.target.value })}
            />
          </label>
        )}
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={() => {
              commitAnchorName();
              setSelectedId(null);
            }}
            className="rounded-lg bg-amber-600 px-3 py-2 text-xs font-black text-white"
          >
            บันทึก
          </button>
          <button type="button" onClick={() => onDeleteField(anchor.id)} className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs font-black text-red-700">
            ลบ ROI
          </button>
        </div>
      </section>
    );
  };

  const reorderFieldsByDrag = (items: TemplateField[], draggedFieldId: string, targetFieldId: string) => {
    if (draggedFieldId === targetFieldId) return;
    const fromIndex = items.findIndex((field) => field.id === draggedFieldId);
    const toIndex = items.findIndex((field) => field.id === targetFieldId);
    if (fromIndex < 0 || toIndex < 0) return;

    const nextOrder = [...items];
    const [draggedField] = nextOrder.splice(fromIndex, 1);
    nextOrder.splice(toIndex, 0, draggedField);
    onReorderFields(nextOrder.map((item) => item.id));
    selectField(draggedField, false);
  };

  const handleFieldDragStart = (event: DragEvent<HTMLDivElement>, fieldId: string) => {
    setDraggingFieldId(fieldId);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", fieldId);
  };

  const handleFieldDrop = (event: DragEvent<HTMLDivElement>, targetField: TemplateField, items: TemplateField[]) => {
    event.preventDefault();
    const draggedFieldId = event.dataTransfer.getData("text/plain") || draggingFieldId;
    setDraggingFieldId(null);
    if (!draggedFieldId) return;
    reorderFieldsByDrag(items, draggedFieldId, targetField.id);
  };

  const saveCategoryDraft = async (value: string) => {
    const draft = categoryDrafts[value];
    if (!draft) return;
    try {
      setCategoryError("");
      await updateImageVerificationCategory(value, draft);
      await reloadImageCategories();
    } catch (error) {
      setCategoryError(error instanceof Error ? error.message : "บันทึประเภทภาพไม่สำเร็จ");
    }
  };

  const addCategoryDraft = async () => {
    try {
      setCategoryError("");
      await createImageVerificationCategory(newCategory);
      setNewCategory({
        value: "",
        label: "",
        prompt: "",
        matchThreshold: 0.7,
        marginThreshold: 0.05,
        evidenceTemperature: 1,
        enabled: true,
      });
      await reloadImageCategories();
    } catch (error) {
      setCategoryError(error instanceof Error ? error.message : "เพิ่มประเภทภาพไม่สำเร็จ");
    }
  };

  const deleteCategoryDraft = async (value: string) => {
    const category = imageCategories.find((item) => item.value === value);
    const label = category?.label || value;
    if (!window.confirm(`ลบคำแทน "${label}" หรือไม่?`)) return;
    try {
      setCategoryError("");
      await deleteImageVerificationCategory(value);
      fields.forEach((field) => {
        const nextCategories = fieldImageCategories(field.imageCategory).filter((item) => item !== value);
        if (nextCategories.length !== fieldImageCategories(field.imageCategory).length) {
          onUpdateField(field.id, { imageCategory: nextCategories });
        }
      });
      setCategoryDrafts((current) => {
        const next = { ...current };
        delete next[value];
        return next;
      });
      await reloadImageCategories();
    } catch (error) {
      setCategoryError(error instanceof Error ? error.message : "ลบประเภทภาพไม่สำเร็จ");
    }
  };

  const handleAutoDetectExtractionRoi = async () => {
    const pagesToAnalyze = pages
      .map((page, index) => ({ page, index }))
      .filter(({ page }) => Boolean(page.src));
    if (pagesToAnalyze.length === 0 || isAutoDetecting) return;

    setStep("extraction_fields");
    setMode("extraction_fields");
    setSelectedId(null);
    setAutoDetectStatus("");
    setAutoDetectError("");
    setIsAutoDetecting(true);

    try {
      const response = await fetch(`${ADMIN_API_BASE_URL}/api/layout/analyze`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          auto_roi_mode: "text_line",
          images: pagesToAnalyze.map(({ page, index }) => ({
            page_index: index,
            image: page.src,
          })),
        }),
      });
      const responseText = await response.text();
      let result: LayoutAnalysisResponse = {};
      try {
        result = responseText ? (JSON.parse(responseText) as LayoutAnalysisResponse) : {};
      } catch {
        result = { error: responseText || response.statusText };
      }

      if (!response.ok || !result.success) {
        if (response.status === 404) {
          throw new Error("ไม่พบ endpoint /api/layout/analyze กรุณา restart backend");
        }
        throw new Error(result.detail || result.error || "สร้าง ROI อัตโนมัติไม่สำเร็จ");
      }

      const detectedItems = pagesToAnalyze.map(({ index }) => {
        const pageNumber = index + 1;
        const detectedPage = (result.pages || []).find((page) => Number(page.page_index) === index);
        const detectedFields = (detectedPage?.regions || [])
          .map((region, regionIndex) => layoutRegionToDetectedField(region, pageNumber, regionIndex + 1))
          .filter((item): item is { roi: RoiRatio; defaults: Partial<TemplateField> } => item !== null);
        return { pageNumber, fields: detectedFields };
      });
      const totalDetectedFields = detectedItems.reduce((sum, item) => sum + item.fields.length, 0);

      onReplaceExtractionFieldsForPages(detectedItems);
      setAutoDetectStatus(
        totalDetectedFields > 0
          ? `สร้าง ROI อัตโนมัติ ${totalDetectedFields} รายการ จาก ${pagesToAnalyze.length} หน้า และลบ Extraction ROI เดิมของทุกหน้าแล้ว`
          : `ไม่พบ Text, Table หรือ Image Region ใน ${pagesToAnalyze.length} หน้า ระบบลบ Extraction ROI เดิมของทุกหน้าแล้ว`
      );
    } catch (error) {
      console.error("Admin auto ROI detection failed.", error);
      setAutoDetectError(error instanceof Error ? error.message : "สร้าง ROI อัตโนมัติไม่สำเร็จ");
    } finally {
      setIsAutoDetecting(false);
    }
  };
  const clearStepTest = () => {
    setTestResult(null);
    setTestResultKind(null);
    setTestStatus("");
    setTestError("");
  };

  const runStepTest = async (kind: "extraction" | "verification") => {
    if (kind === "verification" && !verificationAnchorsReady) {
      setTestError(verificationBlockedMessage || "Verification anchors are not ready.");
      return;
    }
    setTestAction(kind);
    setTestError("");
    setTestStatus(kind === "extraction" ? "Testing extraction fields..." : "Testing verification anchors...");
    setTestResult(null);
    try {
      if (onBeforeRunTest) {
        setTestStatus("Saving latest ROI and field settings...");
        await onBeforeRunTest();
        setTestStatus(kind === "extraction" ? "Testing extraction fields..." : "Testing verification anchors...");
      }
      const result =
        kind === "extraction"
          ? await testTemplateExtractionFields(templateId)
          : await testTemplateVerificationAnchors(templateId);
      setTestResult(result);
      setTestResultKind(kind);
      setTestStatus(`${kind === "extraction" ? "Extraction" : "Verification"} test complete: ${result.passedCount}/${result.testedCount} passed.`);
    } catch (error) {
      setTestError(error instanceof Error ? error.message : "Step test failed.");
      setTestStatus("");
    } finally {
      setTestAction(null);
    }
  };

  const renderStructuredTable = (
    structured: TemplateStepTestItem["tableStructured"] | null | undefined,
    fallbackRows: string[][] | null,
    title = "ผลลัพธ์ตาราง"
  ) => {
    const cells = getStructuredPreviewCells(structured);
    const sourceRows = getStructuredPreviewRows(structured) || fallbackRows || [];
    const headerRowCount = Math.max(1, Number(structured?.headerRowCount ?? 1));
    const maxRowFromCells = cells.reduce((max, cell) => Math.max(max, cell.row + cell.rowSpan), 0);
    const maxColFromCells = cells.reduce((max, cell) => Math.max(max, cell.col + cell.colSpan), 0);
    const rowCount = Math.max(sourceRows.length, maxRowFromCells);
    const colCount = Math.max(1, sourceRows.reduce((max, row) => Math.max(max, row.length), 0), maxColFromCells);
    const cellByPosition = new Map(cells.map((cell) => [`${cell.row}:${cell.col}`, cell]));
    const hiddenPositions = new Set<string>();

    cells.forEach((cell) => {
      if (cell.hidden) {
        hiddenPositions.add(`${cell.row}:${cell.col}`);
        return;
      }
      for (let rowOffset = 0; rowOffset < cell.rowSpan; rowOffset += 1) {
        for (let colOffset = 0; colOffset < cell.colSpan; colOffset += 1) {
          if (rowOffset !== 0 || colOffset !== 0) {
            hiddenPositions.add(`${cell.row + rowOffset}:${cell.col + colOffset}`);
          }
        }
      }
    });

    return (
      <div className="mt-3 overflow-hidden rounded-lg border border-slate-200 bg-white">
        <div className="border-b border-slate-100 bg-slate-50 px-2.5 py-1.5 text-[9px] font-black uppercase text-slate-500">
          {title}
        </div>
        <div className="max-h-48 overflow-auto">
          <table className="min-w-full border-collapse text-left text-[11px] font-semibold text-slate-700">
            <tbody>
              {Array.from({ length: rowCount }).map((_, rowIndex) => (
                <tr key={`table-row-${title}-${rowIndex}`} className={rowIndex < headerRowCount ? "bg-slate-50 font-black text-slate-800" : "bg-white"}>
                  {Array.from({ length: colCount }).map((__, colIndex) => {
                    if (hiddenPositions.has(`${rowIndex}:${colIndex}`)) return null;
                    const structuredCell = cellByPosition.get(`${rowIndex}:${colIndex}`);
                    const cellText = structuredCell?.text ?? sourceRows[rowIndex]?.[colIndex] ?? "";
                    const isMerged = Boolean(structuredCell && (structuredCell.rowSpan > 1 || structuredCell.colSpan > 1));
                    const isHeader = rowIndex < headerRowCount;
                    return (
                      <td
                        key={`table-cell-${title}-${rowIndex}-${colIndex}`}
                        rowSpan={structuredCell?.rowSpan}
                        colSpan={structuredCell?.colSpan}
                        className={`border border-slate-200 px-2 py-1.5 ${
                          isHeader || isMerged ? "text-center align-middle" : "text-left align-top"
                        }`}
                      >
                        {cellText || <span className="text-slate-300">-</span>}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  };

  const renderTablePreview = (item: TemplateStepTestItem) => {
    if (Array.isArray(item.tableSections) && item.tableSections.length > 0) {
      const renderedSections = item.tableSections
        .map((section, index) => {
          const structured = section.tableStructured || {
            rows: Array.isArray(section.rows) ? section.rows : undefined,
            cells: Array.isArray(section.cells) ? section.cells : undefined,
          };
          const rows = getStructuredPreviewRows(structured) || normalizeTableRows(section.rows);
          const hasStructuredCells = getStructuredPreviewCells(structured).length > 0;
          if (!hasStructuredCells && (!rows || rows.length === 0) && !section.text) return null;
          if (!hasStructuredCells && (!rows || rows.length === 0) && section.text) {
            return (
              <div key={section.regionId || `section-${index}`} className="mt-3 rounded-lg border border-slate-200 bg-white p-3 text-[11px] font-semibold leading-relaxed text-slate-700">
                {section.text}
              </div>
            );
          }
          return (
            <div key={section.regionId || `section-${index}`}>
              {renderStructuredTable(structured, rows, `ผลลัพธ์ตาราง ${index + 1}`)}
            </div>
          );
        })
        .filter(Boolean);
      if (renderedSections.length > 0) return <>{renderedSections}</>;
    }

    const rows = getTableRowsFromTestItem(item);
    const tableDebugStatus = typeof item.tableDebug?.status === "string" ? item.tableDebug.status : null;
    if (!rows || rows.length === 0) {
      return (
        <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] font-bold text-amber-800">
          ไม่พบโครงสร้างตารางจากผลทดสอบ{tableDebugStatus ? ` (${tableDebugStatus})` : ""}
        </div>
      );
    }

    return renderStructuredTable(item.tableStructured, rows);
  };

  const renderRoiCropPreview = (item: TemplateStepTestItem) => {
    const previewSrc =
      item.cropPreviewDataUrl ||
      item.currentCropPreviewDataUrl ||
      item.cropPreviewUrl ||
      item.currentCropPreviewUrl;
    const referenceSrc = item.referenceCropPreviewDataUrl || item.referenceCropPreviewUrl;
    const currentSrc = item.currentCropPreviewDataUrl || item.currentCropPreviewUrl || previewSrc;
    const previewItems = referenceSrc
      ? [
          { label: "ROI ต้นฉบับ", src: referenceSrc },
          { label: "ROI ปัจจุบัน", src: currentSrc },
        ]
      : [{ label: "ROI ที่ตรวจจับได้", src: currentSrc }];
    return (
      <div className="rounded-lg border border-slate-100 bg-white p-2">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[9px] font-black uppercase text-slate-400">{referenceSrc ? "Verification ROI" : "ROI Preview"}</span>
          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[8px] font-black uppercase text-slate-500">
            {testItemTypeLabel(item)}
          </span>
        </div>
        <div className={`mt-2 grid gap-2 ${referenceSrc ? "sm:grid-cols-2" : ""}`}>
          {previewItems.map((preview) => (
            <div key={preview.label}>
              {referenceSrc && <div className="mb-1 text-[8px] font-black uppercase text-slate-400">{preview.label}</div>}
              {preview.src ? (
                <img src={preview.src} alt="" className="h-28 w-full rounded-md bg-white object-contain ring-1 ring-slate-100" />
              ) : (
                <div className="flex h-28 items-center justify-center rounded-md bg-slate-50 text-[10px] font-semibold text-slate-400">
                  ไม่มีภาพ ROI
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    );
  };

  const renderImageFieldPreview = (item: TemplateStepTestItem) => {
    if (step === "extraction_fields") return null;
    return (
      <div className="rounded-lg border border-slate-100 bg-white p-2">
        <div className="text-[9px] font-black uppercase text-slate-400">ผลลัพธ์ประเภทรูปภาพ</div>
        <div className="mt-1 rounded-lg bg-sky-50 px-2 py-1.5 text-sm font-black text-sky-700">
          {item.predictedImageCategoryLabel || item.actualText || "-"}
        </div>
      </div>
    );
  };

  const renderFlexibleTextPreview = (item: TemplateStepTestItem) => {
    if (item.roiMode !== "flexible") return null;
    const blocks = item.resolvedBlocks || [];
    const tableBlocks = blocks.filter((block) => (block.dataType || block.type) === "table");
    const nonTableBlocks = blocks.filter((block) => (block.dataType || block.type) !== "table");
    return (
      <div className="space-y-3">
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.65fr)]">
          <div className="rounded-lg border border-slate-100 bg-white p-2">
            <div className="text-[9px] font-black uppercase text-slate-400">ภาพพรีวิวที่ PP-DocLayoutV3 หาองค์ประกอบได้</div>
            {item.flexibleOverlayPreviewDataUrl ? (
              <img src={item.flexibleOverlayPreviewDataUrl} alt="" className="mt-2 max-h-[520px] w-full rounded-md bg-white object-contain ring-1 ring-slate-100" />
            ) : (
              <div className="mt-2 flex h-80 items-center justify-center rounded-md bg-slate-50 text-[10px] font-semibold text-slate-400">
                ไม่มีภาพพรีวิวจาก PP-DocLayoutV3
              </div>
            )}
          </div>

          <div className="min-h-0 space-y-2 rounded-lg border border-slate-100 bg-white p-2">
            <div className="flex items-center justify-between gap-2">
              <div className="text-[9px] font-black uppercase text-slate-400">ROI ย่อยและผลตาม Type</div>
              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[8px] font-black uppercase text-slate-500">
                {nonTableBlocks.length} รายการ
              </span>
            </div>
            {nonTableBlocks.length > 0 ? (
              <div className="max-h-[520px] space-y-2 overflow-y-auto pr-1">
                {nonTableBlocks.map((block, index) => (
                  <div key={`flexible-block-${item.fieldId || item.fieldName}-${index}`} className="rounded-lg border border-slate-100 bg-slate-50 p-2">
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-[9px] font-black uppercase text-slate-400">ROI ย่อย #{index + 1}</div>
                      <div className="flex flex-wrap justify-end gap-1">
                        <span className="rounded bg-white px-1.5 py-0.5 text-[8px] font-black uppercase text-slate-500">
                          {block.dataType || block.type || "text"}
                        </span>
                        <span className="rounded bg-sky-100 px-1.5 py-0.5 text-[8px] font-black uppercase text-sky-700">
                          PP-DocLayoutV3
                        </span>
                      </div>
                    </div>
                    <div className="mt-2 grid gap-2 sm:grid-cols-[112px_1fr] lg:grid-cols-1 xl:grid-cols-[112px_1fr]">
                      {block.cropPreviewDataUrl ? (
                        <img src={block.cropPreviewDataUrl} alt="" className="h-24 w-full rounded-md bg-white object-contain ring-1 ring-slate-100" />
                      ) : (
                        <div className="flex h-24 items-center justify-center rounded-md bg-white text-[10px] font-semibold text-slate-400 ring-1 ring-slate-100">
                          ไม่มีภาพกรอบ
                        </div>
                      )}
                      <div className="min-w-0">
                        <div className="text-[9px] font-black uppercase text-slate-400">ข้อมูลที่ดึงได้</div>
                        <div className="mt-1 max-h-24 overflow-y-auto rounded bg-white px-2 py-1.5 text-[11px] font-semibold leading-5 text-slate-700 ring-1 ring-slate-100">
                          <p className="whitespace-pre-wrap break-words">{block.text || "-"}</p>
                        </div>
                        {block.ocrError && (
                          <div className="mt-1 rounded bg-red-50 px-2 py-1 text-[10px] font-semibold text-red-700">
                            OCR error: {block.ocrError}
                          </div>
                        )}
                        {block.confidence !== null && block.confidence !== undefined && (
                          <div className="mt-1 text-[9px] font-black uppercase text-slate-400">Conf {block.confidence.toFixed(2)}</div>
                        )}
                        <div className="mt-1 text-[9px] font-black uppercase text-slate-400">
                          Method {block.extractionMethod || "paddle_thai_ocr"}
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] font-bold text-amber-800">
                ยังไม่พบ ROI ย่อยประเภทข้อความหรือรูปภาพภายใน Search Boundary
              </div>
            )}
          </div>
        </div>

        {tableBlocks.length > 0 && (
          <div className="space-y-3 rounded-lg border border-slate-100 bg-white p-3">
            <div className="flex items-center justify-between gap-2">
              <div className="text-[9px] font-black uppercase text-slate-400">ตารางที่ PP-DocLayoutV3 แยกได้</div>
              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[8px] font-black uppercase text-slate-500">
                {tableBlocks.length} ตาราง
              </span>
            </div>
            {tableBlocks.map((block, index) => (
              <div key={`flexible-table-block-${item.fieldId || item.fieldName}-${index}`} className="rounded-lg border border-slate-100 bg-slate-50 p-2">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <div className="text-[9px] font-black uppercase text-slate-400">ตาราง #{index + 1}</div>
                  <span className="rounded bg-white px-1.5 py-0.5 text-[8px] font-black uppercase text-slate-500">
                    {block.extractionMethod || "table_recognition_v2"}
                  </span>
                </div>
                {block.cropPreviewDataUrl && (
                  <img src={block.cropPreviewDataUrl} alt="" className="mb-2 max-h-44 w-full rounded-md bg-white object-contain ring-1 ring-slate-100" />
                )}
                {renderStructuredTable(block.tableStructured, block.tableRows || null, `ผลลัพธ์ตาราง ${index + 1}`)}
              </div>
            ))}
          </div>
        )}
      </div>
    );
  };

  const renderTextVerificationPreview = (item: TemplateStepTestItem) => (
    <div className="rounded-lg border border-slate-100 bg-white p-2 font-semibold text-slate-600">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[9px] font-black uppercase text-slate-400">การตรวจสอบข้อความ</div>
        <span className={`rounded px-1.5 py-0.5 text-[8px] font-black uppercase ${item.passed ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700"}`}>
          {item.passed ? "Matched" : "Not Matched"}
        </span>
      </div>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        <div>
          <div className="text-[9px] font-black uppercase text-slate-400">ค่าที่กำหนด</div>
          <div className="mt-1 rounded bg-slate-50 px-2 py-1.5 text-[11px] text-slate-700">{item.expectedText || "-"}</div>
        </div>
        <div>
          <div className="text-[9px] font-black uppercase text-slate-400">ค่าที่ตรวจพบ</div>
          <div className="mt-1 rounded bg-slate-50 px-2 py-1.5 text-[11px] text-slate-700">{item.ocrText || item.actualText || "-"}</div>
        </div>
      </div>
    </div>
  );

  const renderTestResults = (items: TemplateStepTestResult["fields"] | TemplateStepTestResult["anchors"]) => (
    <div className="mt-4 space-y-3">
      {items && items.length > 0 && (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {items.map((item, index) => (
            <div
              key={`${item.fieldId || item.anchorId || index}-test-result`}
              className={`rounded-xl border border-slate-100 bg-slate-50 p-3 text-xs ${isTableTestItem(item) || item.roiMode === "flexible" ? "md:col-span-2 xl:col-span-3" : ""}`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate font-black text-slate-800">{item.displayLabel || item.fieldName || "Field"}</div>
                  <div className="mt-0.5 text-[9px] font-bold uppercase text-slate-400">
                    Page {item.pageNumber ?? "N/A"} · {testItemTypeLabel(item)} {item.anchorType === "image" ? "Feature" : item.anchorType === "text" ? "OCR" : ""}
                  </div>
                </div>
                <span className={`rounded-full px-2 py-0.5 text-[9px] font-black uppercase ${item.passed ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700"}`}>
                  {item.passed ? "PASS" : "FAIL"}
                </span>
              </div>
              <div className={`mt-3 grid gap-3 ${
                item.roiMode === "flexible"
                  ? "grid-cols-1"
                  : isTableTestItem(item)
                  ? "lg:grid-cols-[220px_1fr]"
                  : "sm:grid-cols-[180px_1fr]"
              }`}>
                {item.roiMode !== "flexible" && renderRoiCropPreview(item)}
                <div className="min-w-0">
                  {isImageTestItem(item) && renderImageFieldPreview(item)}
                  {isTableTestItem(item) && renderTablePreview(item)}
                  {renderFlexibleTextPreview(item)}
                  {item.anchorType === "text" && renderTextVerificationPreview(item)}
                  {item.roiMode !== "flexible" && !isTableTestItem(item) && !isImageTestItem(item) && item.anchorType !== "text" && (item.ocrText || item.actualText || item.expectedText) && (
                    <div className="min-w-0 rounded-lg border border-slate-100 bg-white p-2 font-semibold text-slate-600">
                      <div className="text-[9px] font-black uppercase text-slate-400">ข้อความที่อ่านได้</div>
                      <div className="mt-2 max-h-44 min-w-0 space-y-1 overflow-y-auto rounded bg-slate-50 p-2 text-[11px] leading-5 text-slate-700">
                        {(item.ocrText || item.actualText) && <p className="whitespace-pre-wrap break-words">{item.ocrText || item.actualText}</p>}
                      </div>
                    </div>
                  )}
                </div>
              </div>
              <div className="mt-2 flex flex-wrap gap-1 text-[9px] font-black uppercase">
                {item.anchorType !== "image" && item.confidence !== null && item.confidence !== undefined && (
                  <span className="rounded bg-slate-200 px-1.5 py-0.5 text-slate-600">Conf {item.confidence.toFixed(2)}</span>
                )}
                {item.anchorType !== "image" && ((item.textMatchScore !== null && item.textMatchScore !== undefined) || (item.fieldScore !== null && item.fieldScore !== undefined) || (item.score !== null && item.score !== undefined)) ? (
                  <span className="rounded bg-slate-200 px-1.5 py-0.5 text-slate-600">
                    Score {getVerificationItemScore(item).toFixed(2)}
                  </span>
                ) : null}
                {item.anchorType === "image" && item.evidenceScore !== null && item.evidenceScore !== undefined && (
                  <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-700">
                    Score {getVerificationItemScore(item).toFixed(0)}
                  </span>
                )}
                {item.anchorType !== "image" && item.rawPairScore !== null && item.rawPairScore !== undefined && (
                  <span className="rounded bg-slate-200 px-1.5 py-0.5 text-slate-600">
                    Pair {item.rawPairScore.toFixed(2)}
                  </span>
                )}
                {item.relativePercentage !== null && item.relativePercentage !== undefined && (
                  <span className="rounded bg-slate-200 px-1.5 py-0.5 text-slate-600">
                    Relative {item.relativePercentage.toFixed(1)}%
                  </span>
                )}
                {item.anchorType !== "image" && item.imageCategoryLabel && (
                  <span className="rounded bg-slate-200 px-1.5 py-0.5 text-slate-600">
                    Expected {item.imageCategoryLabel}
                  </span>
                )}
                {item.predictedImageCategoryLabel && (
                  <span className="rounded bg-sky-100 px-1.5 py-0.5 text-sky-700">
                    Predicted {item.predictedImageCategoryLabel}
                  </span>
                )}
                {item.siglipTargetRank !== null && item.siglipTargetRank !== undefined && (
                  <span className="rounded bg-indigo-100 px-1.5 py-0.5 text-indigo-700">Rank {item.siglipTargetRank}</span>
                )}
                {item.failureReason && !item.passed && (
                  <span className="rounded bg-red-100 px-1.5 py-0.5 text-red-700">{item.failureReason}</span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );

  return (
    <>
    <div className="mx-auto max-w-7xl space-y-4 pb-20">
      <WorkspaceCustomEditor
        previewUrl={selectedPage?.src || ""}
        image={selectedPage?.src || null}
        brightness={100}
        contrast={100}
        rotation={0}
        rois={activeRois}
        setRois={persistRois}
        selectedId={selectedId}
        setSelectedId={setSelectedRoiId}
        onBackToAdjust={onBackToAdjust || (() => {})}
        deleteROI={(id) => {
          const roi = activeRois.find((item) => item.id === id);
          if (!roi?.sourceId) return;
          if (roi.workspaceKind === "ignore_regions") onDeleteIgnoreRegion(roi.sourceId);
          else onDeleteField(roi.sourceId);
        }}
        isLoading={false}
        onRunOCR={() => {}}
        onRunFullPageOCR={async () => {}}
        currentIndex={currentPage}
        imagesList={pages.map((page) => page.src)}
        onIndexChange={onPageChange}
        hideOcrActions
        hideStepProgress
        rootClassName="max-w-7xl mx-auto space-y-3"
        onImageMetricsChange={setImageMetrics}
        getRoiBadges={(roi) => {
          const adminRoi = roi as AdminRoi;
          if (adminRoi.workspaceKind !== "extraction_fields") return [];
          return [];
        }}
        allowedRoiTypes={step === "verification_anchors" ? ["text", "image"] : ["text", "table", "image"]}
        getRoiClassName={(roi, selected) => {
          const adminRoi = roi as AdminRoi;
          const isAnchorRoi = adminRoi.workspaceKind === "verification_anchors";
          const isIgnore = adminRoi.workspaceKind === "ignore_regions";
          const isFlexible = adminRoi.workspaceKind === "extraction_fields" && adminRoi.roiMode === "flexible";
          if (isAnchorRoi || isIgnore) {
            return `rnd-box-item border transition-shadow pointer-events-auto ${
              selected
                ? "border-amber-700 bg-amber-400/30 shadow-lg z-30 ring-4 ring-amber-300/45"
                : "border-amber-500 bg-amber-400/10 hover:bg-amber-400/15 z-20"
            }`;
          }
          if (isFlexible) {
            return `rnd-box-item border-2 border-dashed transition-shadow pointer-events-auto ${
              selected
                ? "border-cyan-700 bg-cyan-300/20 shadow-lg z-30 ring-4 ring-cyan-300/45"
                : "border-cyan-500 bg-cyan-300/10 hover:bg-cyan-300/15 z-20"
            }`;
          }
          return `rnd-box-item border transition-shadow pointer-events-auto ${
            selected
              ? "border-sky-600 bg-sky-400/25 shadow-lg z-30 ring-4 ring-sky-300/45"
              : "border-indigo-400/80 bg-indigo-50/5 hover:border-indigo-500 hover:bg-indigo-50/10 z-20"
          }`;
        }}
        getRoiLabelClassName={(roi, selected) => {
          const adminRoi = roi as AdminRoi;
          const amber = adminRoi.workspaceKind === "verification_anchors" || adminRoi.workspaceKind === "ignore_regions";
          const flexible = adminRoi.workspaceKind === "extraction_fields" && adminRoi.roiMode === "flexible";
          return `absolute -top-5 left-0 px-1.5 py-0.5 text-[9px] font-sans rounded shadow border flex items-center gap-1.5 pointer-events-auto cursor-pointer ${
            selected
              ? amber
                ? "bg-amber-700 border-amber-700 text-white font-extrabold"
                : flexible
                  ? "bg-cyan-700 border-cyan-700 text-white font-extrabold"
                  : "bg-sky-600 border-sky-600 text-white font-extrabold"
              : amber
                ? "bg-white border-amber-200 text-amber-700 font-bold"
                : flexible
                  ? "bg-white border-cyan-200 text-cyan-700 font-bold"
                  : "bg-white border-indigo-200 text-indigo-700 font-bold"
          }`;
        }}
        getRoiLabelText={(roi) => {
          const adminRoi = roi as AdminRoi;
          const name = roi.fieldName || "(Unnamed)";
          return adminRoi.workspaceKind === "extraction_fields" && adminRoi.roiMode === "flexible"
            ? `${name} · Flexible Search Area`
            : name;
        }}
        rightPanelRenderer={({ currentPageRois: panelRois }) => (
          <div className="flex h-full min-h-0 flex-col">
            <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
            {step === "extraction_fields" ? (
              <>
                {onBackToAdjust && (
                  <button
                    type="button"
                    onClick={onBackToAdjust}
                    className="flex w-full items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-xs font-black text-slate-700 shadow-sm hover:bg-slate-50"
                  >
                    <ArrowLeft size={14} />
                    กลับไป 2.0 ปรับภาพ
                  </button>
                )}
                <section className="flex min-h-0 flex-1 flex-col space-y-2 rounded-xl border border-slate-200 bg-slate-50 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <h3 className="text-xs font-black uppercase tracking-wider text-slate-700">ROI ทุกหน้า</h3>
                    <span className="rounded-full bg-white px-2 py-0.5 text-[10px] font-black text-slate-500">{panelRois.length}</span>
                  </div>
                  {mode === "extraction_fields" && (
                    <div className="space-y-2 rounded-lg border border-indigo-100 bg-white p-2.5">
                      <button
                        type="button"
                        onClick={handleAutoDetectExtractionRoi}
                        disabled={isAutoDetecting || !pages.some((page) => page.src)}
                        className="flex w-full items-center justify-center gap-2 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs font-black text-indigo-700 shadow-sm hover:bg-indigo-100 disabled:border-slate-200 disabled:bg-slate-100 disabled:text-slate-400"
                      >
                        {isAutoDetecting ? <Loader2 size={14} className="animate-spin" /> : <ScanSearch size={14} />}
                        {isAutoDetecting ? "กำลังตีกรอบ ROI ทุกหน้า..." : "ตีกรอบ ROI อัตโนมัติทุกหน้า"}
                      </button>
                      <p className="text-[10px] font-semibold leading-relaxed text-slate-500">
                        วิเคราะห์ Layout ทุกหน้าด้วย PP-DocLayoutV3 แล้วสร้าง Extraction ROI ใหม่ โดยลบ Extraction ROI เดิมของทุกหน้าก่อนทุกครั้ง
                      </p>
                      {autoDetectStatus && (
                        <p className="rounded-lg bg-emerald-50 px-2.5 py-2 text-[10px] font-bold leading-relaxed text-emerald-700">
                          {autoDetectStatus}
                        </p>
                      )}
                      {autoDetectError && (
                        <p className="rounded-lg bg-red-50 px-2.5 py-2 text-[10px] font-bold leading-relaxed text-red-700">
                          {autoDetectError}
                        </p>
                      )}
                    </div>
                  )}
                  <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-1">
                    {panelRois.length === 0 ? (
                      <p className="text-xs font-semibold text-slate-400">No ROI on this page.</p>
                    ) : panelRois.map((roi, index) => {
                      const sourceField = currentPageExtractionFields.find((field) => field.id === (roi as AdminRoi).sourceId);
                      const isSelected = selectedId === roi.id;
                      return (
                        <div
                          key={roi.id}
                          draggable={Boolean(sourceField)}
                          onDragStart={(event) => sourceField && handleFieldDragStart(event, sourceField.id)}
                          onDragOver={(event) => {
                            if (sourceField && draggingFieldId && draggingFieldId !== sourceField.id) event.preventDefault();
                          }}
                          onDrop={(event) => sourceField && handleFieldDrop(event, sourceField, currentPageExtractionFields)}
                          onDragEnd={() => setDraggingFieldId(null)}
                          className={`rounded-lg border bg-white ${
                            draggingFieldId === sourceField?.id
                              ? "border-indigo-400 opacity-60"
                              : isSelected
                                ? "border-indigo-300 shadow-sm"
                                : "border-slate-200 hover:bg-slate-50"
                          }`}
                        >
                          <div className="flex items-center gap-1.5 px-2 py-2 text-[11px] font-bold">
                            {sourceField && (
                              <span className="inline-flex h-7 w-6 shrink-0 cursor-grab items-center justify-center rounded-md text-slate-400 active:cursor-grabbing" title="ลากเพื่อจัดลำดับ">
                                <GripVertical size={14} />
                              </span>
                            )}
                            <button type="button" onClick={() => setSelectedId(isSelected ? null : roi.id)} className={`min-w-0 flex-1 truncate text-left ${isSelected ? "text-indigo-800" : "text-slate-600"}`}>
                              <span className="mr-1 text-slate-400">{index + 1}.</span>
                              {sourceField?.displayLabel || sourceField?.fieldName || roi.fieldName}
                            </button>
                            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[9px] font-black uppercase text-slate-500">
                              {sourceField?.dataType || roi.type || "text"}
                            </span>
                          </div>
                          {isSelected && sourceField && (
                            <div className="border-t border-indigo-100 p-2">
                              <TemplateFieldBasicForm
                                field={sourceField}
                                onUpdate={onUpdateField}
                                onDelete={onDeleteField}
                                compact
                                onSave={() => setSelectedId(null)}
                              />
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </section>
              </>
            ) : (
              <>
                <section className="rounded-xl border border-slate-200 bg-white p-3">
                  <button
                    type="button"
                    onClick={() => {
                      setStep("extraction_fields");
                      setMode("extraction_fields");
                      setSelectedId(null);
                      clearStepTest();
                    }}
                    className="w-full rounded-xl border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs font-black text-indigo-700 hover:bg-indigo-100"
                  >
                    ย้อนกลับไป 2.1 Workspace ROI
                  </button>
                </section>
                <section className="space-y-2 rounded-xl border border-amber-200 bg-amber-50/70 p-3">
                  <h3 className="text-xs font-black uppercase tracking-wider text-amber-900">Verification ROI</h3>
                  <p className="text-[10px] font-semibold leading-relaxed text-amber-800">
                    กำหนด ROI ของข้อความหรือภาพที่ใช้สำหรับยืนยัน Template เท่านั้น
                  </p>
                </section>
                <section className="flex min-h-0 flex-1 flex-col space-y-2 rounded-xl border border-slate-200 bg-slate-50 p-3">
                  <h3 className="text-xs font-black uppercase tracking-wider text-slate-700">Page {currentPage + 1} ROI</h3>
                  <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-1">
                    {currentPageAnchors.length === 0 ? (
                      <p className="text-xs font-semibold text-slate-400">วาด ROI เพื่อกำหนดพื้นที่สำหรับยืนยัน Template</p>
                    ) : currentPageAnchors.map((anchor) => {
                      const isSelected = selectedAnchor?.id === anchor.id;
                      return (
                        <div
                          key={anchor.id}
                          draggable
                          onDragStart={(event) => handleFieldDragStart(event, anchor.id)}
                          onDragOver={(event) => {
                            if (draggingFieldId && draggingFieldId !== anchor.id) event.preventDefault();
                          }}
                          onDrop={(event) => handleFieldDrop(event, anchor, currentPageAnchors)}
                          onDragEnd={() => setDraggingFieldId(null)}
                          className={`overflow-hidden rounded-lg border bg-white text-[11px] font-bold ${
                            draggingFieldId === anchor.id
                              ? "border-amber-400 opacity-60"
                              : isSelected
                                ? "border-amber-500 bg-amber-100 text-amber-900"
                                : "border-slate-200 text-slate-600 hover:bg-slate-50"
                          }`}
                        >
                          <div className="flex items-center gap-1.5 px-2 py-2">
                            <span className="inline-flex h-7 w-6 shrink-0 cursor-grab items-center justify-center rounded-md text-slate-400 active:cursor-grabbing" title="ลากเพื่อจัดลำดับ">
                              <GripVertical size={14} />
                            </span>
                            <button type="button" onClick={() => selectField(anchor)} className="min-w-0 flex-1 text-left">
                              <div className="truncate">{anchor.displayLabel || anchor.fieldName}</div>
                              <div className="mt-0.5 flex flex-wrap items-center gap-1 text-[9px] uppercase tracking-wide text-amber-700">
                                <span>{anchorMethod(anchor) === "image_feature" ? "Image" : "OCR Text"}</span>
                                {anchorMethod(anchor) === "image_feature" && fieldImageCategories(anchor.imageCategory).map((value) => {
                                  const category = activeImageCategories.find((option) => option.value === value);
                                  return (
                                    <span key={value} className="rounded bg-amber-100 px-1 py-0.5 text-amber-800">
                                      {category?.label || value}
                                    </span>
                                  );
                                })}
                                {anchorMethod(anchor) === "image_feature" && fieldImageCategories(anchor.imageCategory).length === 0 && (
                                  <span className="rounded bg-red-100 px-1 py-0.5 text-red-700">ต้องเลือกประเภทภาพ</span>
                                )}
                                {anchorMethod(anchor) === "ocr_text" && !String(anchor.expectedText || "").trim() && (
                                  <span className="rounded bg-red-100 px-1 py-0.5 text-red-700">ค่าที่ใช้ยืนยัน</span>
                                )}
                              </div>
                            </button>
                          </div>
                          {isSelected && renderAnchorSettings(anchor)}
                        </div>
                      );
                    })}
                  </div>
                </section>
                {!selectedAnchor && (
                  <p className="rounded-xl bg-slate-50 p-3 text-xs font-semibold text-slate-500">กรุณาวาดหรือเลือก ROI สำหรับยืนยัน Template ก่อน</p>
                )}
              </>
            )}
            </div>
          </div>
        )}
      />

      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h3 className="text-xs font-black uppercase tracking-wider text-slate-700">
              {step === "verification_anchors" ? "ผลลัพธ์การทดสอบ Verification" : "ผลลัพธ์การทดสอบ Extraction"}
            </h3>
            <p className="mt-1 text-[11px] font-semibold text-slate-500">
              {step === "verification_anchors"
                ? "ทดสอบ OCR และ Image Anchor สำหรับ Verification ROI ที่กำหนดไว้ใน Template"
                : "ทดสอบ OCR สำหรับ Extraction ROI เพื่อยืนยันว่าแต่ละ ROI อ่านข้อมูลได้"}
            </p>
          </div>
          <div className="flex gap-2">
            {step === "verification_anchors" ? (
              <button
                type="button"
                onClick={() => runStepTest("verification")}
                disabled={testAction !== null || !verificationAnchorsReady}
                className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-2 text-xs font-black text-amber-800 disabled:border-slate-200 disabled:bg-slate-100 disabled:text-slate-400"
              >
                {testAction === "verification" ? "Testing..." : "Test Verification"}
              </button>
            ) : (
              <button
                type="button"
                onClick={() => runStepTest("extraction")}
                disabled={testAction !== null || extractionFields.length === 0}
                className="rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-2 text-xs font-black text-indigo-700 disabled:border-slate-200 disabled:bg-slate-100 disabled:text-slate-400"
              >
                {testAction === "extraction" ? "Testing..." : "Test Extraction"}
              </button>
            )}
          </div>
        </div>
        {testStatus && <p className="mt-3 text-xs font-bold text-slate-600">{testStatus}</p>}
        {testError && <p className="mt-3 rounded-xl bg-red-50 p-3 text-xs font-bold text-red-700">{testError}</p>}
        {testResult && (
          <div className="mt-3 flex flex-wrap gap-2 text-[10px] font-black uppercase">
            <span className="rounded-full bg-slate-100 px-2.5 py-1 text-slate-600">{testResult.testedCount} tested</span>
            <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-emerald-700">{testResult.passedCount} passed</span>
            <span className="rounded-full bg-red-100 px-2.5 py-1 text-red-700">{testResult.failedCount} failed</span>
            {testResult.score !== null && testResult.score !== undefined && (
              <span className="rounded-full bg-indigo-100 px-2.5 py-1 text-indigo-700">Score {testResult.score.toFixed(2)}</span>
            )}
          </div>
        )}
        {step === "verification_anchors"
          ? testResult?.anchors && renderTestResults(testResult.anchors)
          : testResult?.fields && renderTestResults(testResult.fields)}
        {!testResult && !testStatus && !testError && (
          <p className="mt-4 rounded-xl bg-slate-50 p-4 text-xs font-semibold text-slate-500">
            No step test results yet.
          </p>
        )}
        {step === "verification_anchors" && verificationBlockedMessage && (
          <p className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-bold text-amber-800">
            {verificationBlockedMessage}
          </p>
        )}
        {step === "extraction_fields" && (
          <div className="mt-4 flex flex-col items-end gap-2 border-t border-slate-100 pt-4">
            {!extractionTestPassed && (
              <p className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-800">
                ต้องกด Test Extraction และผลต้อง PASS ทุก ROI ก่อนเข้าสู่ Verification
              </p>
            )}
            <button
              type="button"
              onClick={() => {
                if (!extractionTestPassed) return;
                setStep("verification_anchors");
                setMode("verification_anchors");
                setSelectedId(null);
                clearStepTest();
              }}
              disabled={!extractionTestPassed}
              className="rounded-xl bg-amber-600 px-5 py-2.5 text-xs font-black text-white hover:bg-amber-700 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:text-slate-500"
            >
              Next : Verification Anchors
            </button>
          </div>
        )}
        {step === "verification_anchors" && (
          <div className="mt-4 flex flex-col items-end gap-2 border-t border-slate-100 pt-4">
            {!verificationTestPassed && (
              <p className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-800">
                ต้องกด Test Verification และผลต้อง PASS ทุก ROI ก่อนเข้าสู่ Test Mode
              </p>
            )}
            <button
              type="button"
              onClick={onRunTestMode}
              disabled={!verificationAnchorsReady || !verificationTestPassed}
              className="rounded-xl bg-slate-900 px-5 py-2.5 text-xs font-black text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:text-slate-500"
            >
              {testModeLabel}
            </button>
          </div>
        )}
      </section>
    </div>
    {categoryManagerOpen && (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4">
        <section className="flex max-h-[86vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl">
          <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4">
            <div>
              <h2 className="text-sm font-black text-slate-900">จัดการประเภทภาพ</h2>
              <p className="mt-1 text-xs font-semibold text-slate-500">
                เพิ่มหรือแก้ไขคำแทนสำหรับ Image Verification
              </p>
            </div>
            <button
              type="button"
              onClick={() => setCategoryManagerOpen(false)}
              className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-black text-slate-700 hover:bg-slate-50"
            >
              ปิด
            </button>
          </div>

          <div className="grid min-h-0 flex-1 gap-4 overflow-y-auto p-5 lg:grid-cols-[minmax(0,1fr)_280px]">
            <div className="space-y-3">
              <div className="text-[10px] font-black uppercase tracking-wider text-slate-400">คำแทนที่มีอยู่</div>
              {imageCategories.length === 0 ? (
                <p className="rounded-xl bg-slate-50 p-4 text-xs font-semibold text-slate-500">
                  ยังไม่มีประเภทภาพ
                </p>
              ) : (
                imageCategories.map((category) => {
                  const draft = categoryDrafts[category.value] || category;
                  return (
                    <div key={category.value} className="space-y-2 rounded-xl border border-slate-200 bg-slate-50 p-3">
                      <div className="flex items-center justify-between gap-3">
                        <span className="truncate text-xs font-black text-slate-800">{category.value}</span>
                        <label className="flex items-center gap-1.5 text-[10px] font-bold text-slate-500">
                          <input
                            type="checkbox"
                            checked={draft.enabled}
                            onChange={(event) =>
                              setCategoryDrafts((current) => ({
                                ...current,
                                [category.value]: { ...draft, enabled: event.target.checked },
                              }))
                            }
                          />
                          เปิดใช้
                        </label>
                      </div>
                      <input
                        className={inputClass}
                        placeholder="ชื่อที่แสดง"
                        value={draft.label}
                        onChange={(event) => setCategoryDrafts((current) => ({ ...current, [category.value]: { ...draft, label: event.target.value } }))}
                      />
                      <textarea
                        className={`${inputClass} min-h-20 resize-none`}
                        placeholder="Prompt ภาษาอังกฤษ"
                        value={draft.prompt}
                        onChange={(event) => setCategoryDrafts((current) => ({ ...current, [category.value]: { ...draft, prompt: event.target.value } }))}
                      />
                      <div className="flex justify-end gap-2">
                        <button
                          type="button"
                          className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[10px] font-black text-red-700 hover:bg-red-100"
                          onClick={() => deleteCategoryDraft(category.value)}
                        >
                          ลบ
                        </button>
                        <button
                          type="button"
                          className="rounded-lg bg-slate-900 px-3 py-2 text-[10px] font-black text-white hover:bg-slate-800"
                          onClick={() => saveCategoryDraft(category.value)}
                        >
                          บันทึก
                        </button>
                      </div>
                    </div>
                  );
                })
              )}
            </div>

            <div className="space-y-2 rounded-xl border border-dashed border-amber-200 bg-amber-50/50 p-3">
              <div className="text-[10px] font-black uppercase tracking-wider text-amber-800">เพิ่มคำแทนใหม่</div>
              <input className={inputClass} placeholder="value เช่น document_logo" value={newCategory.value} onChange={(event) => setNewCategory((current) => ({ ...current, value: event.target.value }))} />
              <input className={inputClass} placeholder="ชื่อที่แสดง" value={newCategory.label} onChange={(event) => setNewCategory((current) => ({ ...current, label: event.target.value }))} />
              <textarea className={`${inputClass} min-h-24 resize-none`} placeholder="English prompt" value={newCategory.prompt} onChange={(event) => setNewCategory((current) => ({ ...current, prompt: event.target.value }))} />
              <label className="flex items-center gap-2 text-[10px] font-bold text-slate-600">
                <input
                  type="checkbox"
                  checked={newCategory.enabled}
                  onChange={(event) => setNewCategory((current) => ({ ...current, enabled: event.target.checked }))}
                />
                เปิดใช้
              </label>
              <button type="button" className="w-full rounded-lg bg-amber-600 px-3 py-2 text-[10px] font-black text-white hover:bg-amber-700" onClick={addCategoryDraft}>
                เพิ่มคำแทน
              </button>
              {categoryError && <p className="rounded-lg bg-red-50 px-2 py-1 text-[10px] font-semibold text-red-700">{categoryError}</p>}
            </div>
          </div>
        </section>
      </div>
    )}
    </>
  );
}

