"use client";

import { ChangeEvent, useEffect, useRef, useState } from "react";
import {
  ADMIN_API_BASE_URL,
  detectTemplateDev,
  fetchTemplateBundle,
  type DetectionCandidate,
  type DetectionDevResult,
} from "./adminApi";
import RoiLayer from "../shared/workspace/RoiLayer";
import { WorkspaceRoi } from "../shared/workspace/RoiBox";
import WorkspaceCanvas from "../shared/workspace/WorkspaceCanvas";
import { DEFAULT_WORKSPACE_IMAGE_METRICS, ratioToImageBox, WorkspaceImageMetrics } from "../shared/workspace/roiGeometry";
import { TemplateField } from "../types/ocr";

const formatScore = (score?: number | null) => (typeof score === "number" && score !== null ? score.toFixed(4) : "N/A");
const candidateFinalScore = (candidate?: DetectionCandidate | null) =>
  typeof candidate?.finalScore === "number" ? candidate.finalScore : typeof candidate?.score === "number" ? candidate.score : 0;
const candidateLayoutScore = (candidate?: DetectionCandidate | null) =>
  typeof candidate?.layoutScore === "number"
    ? candidate.layoutScore
    : typeof candidate?.retrievalScore === "number"
      ? candidate.retrievalScore
      : 0;
const formatScoreDelta = (score?: number | null) => (typeof score === "number" && score !== null ? score.toFixed(4) : "N/A");
const separationQuality = (delta: number | null) => {
  if (delta === null) return { label: "N/A", className: "bg-slate-100 text-slate-600" };
  if (delta >= 0.25) return { label: "แยกได้ชัดเจนมาก", className: "bg-emerald-100 text-emerald-700" };
  if (delta >= 0.15) return { label: "แยกได้ดี", className: "bg-green-100 text-green-700" };
  if (delta >= 0.08) return { label: "ใกล้เคียง ต้องตรวจเพิ่ม", className: "bg-amber-100 text-amber-700" };
  return { label: "เสี่ยงสับสน", className: "bg-red-100 text-red-700" };
};
const readText = (value: unknown) => (typeof value === "string" && value.trim() ? value : "N/A");
const readValue = (value: unknown) => (typeof value === "number" || typeof value === "boolean" ? String(value) : readText(value));
const readScore = (value: unknown) => (typeof value === "number" ? formatScore(value) : "N/A");
const isImageVerificationField = (field: Record<string, unknown>) =>
  field.anchor_type === "image" ||
  field.verification_method === "image_feature" ||
  field.match_type === "image_feature";
const readPreviewValue = (field: Record<string, unknown>, keys: string[]) => {
  for (const key of keys) {
    const value = field[key];
    if (typeof value === "string" && value.trim()) return previewSrc(value);
  }
  return "";
};
const renderVerificationExpected = (field: Record<string, unknown>) => {
  if (!isImageVerificationField(field)) return readText(field.expected_text);
  return (
    <div className="space-y-1 text-xs font-semibold text-slate-700">
      <div>{readText(field.image_category_label || field.expected_text)}</div>
      <div className="text-[10px] text-slate-400">{readText(field.image_category_prompt || field.normalized_expected)}</div>
    </div>
  );
};
const renderVerificationActual = (field: Record<string, unknown>) => {
  if (!isImageVerificationField(field)) return readText(field.actual_text);
  const src = readPreviewValue(field, ["current_crop_preview_data_url", "current_crop_preview_url"]);
  const predictedLabel = field.predicted_image_category_label || field.actual_text;
  if (!src) return <span className="text-slate-400">{readText(predictedLabel) || "Test image unavailable"}</span>;
  return (
    <div className="w-36 rounded-lg border border-sky-100 bg-sky-50 p-1.5">
      <img src={src} alt="Actual anchor crop" className="h-20 w-full rounded bg-white object-contain" />
      <div className="mt-1 text-[9px] font-black uppercase text-sky-700">SigLIP Prediction</div>
      <div className="mt-0.5 text-[10px] font-bold text-slate-700">{readText(predictedLabel)}</div>
    </div>
  );
};
const DebugMetric = ({ label, value }: { label: string; value: string }) => (
  <div className="rounded-lg border border-slate-100 bg-slate-50 px-2.5 py-2">
    <div className="text-[9px] font-black uppercase tracking-wider text-slate-400">{label}</div>
    <div className="mt-1 break-words text-[11px] font-bold text-slate-700">{value}</div>
  </div>
);
const PipelineImageCard = ({
  step,
  title,
  description,
  src,
  status,
}: {
  step: number;
  title: string;
  description: string;
  src?: string;
  status?: string;
}) => (
  <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
    <div className="flex items-start justify-between gap-2 border-b border-slate-100 bg-slate-50 px-3 py-2">
      <div>
        <div className="text-[10px] font-black uppercase tracking-wider text-slate-400">Step {step}</div>
        <div className="mt-0.5 text-xs font-black text-slate-800">{title}</div>
      </div>
      {status && <span className="shrink-0 rounded-full bg-indigo-50 px-2 py-1 text-[9px] font-black uppercase text-indigo-700">{status}</span>}
    </div>
    <div className="p-3">
      {src ? (
        <img src={src} alt={title} className="h-32 w-full rounded-lg bg-slate-50 object-contain" />
      ) : (
        <div className="flex h-32 items-center justify-center rounded-lg bg-slate-50 text-center text-[11px] font-bold text-slate-400">
          Image not available
        </div>
      )}
      <p className="mt-2 min-h-8 text-[11px] font-semibold leading-4 text-slate-500">{description}</p>
    </div>
  </div>
);
const verificationFields = (candidate?: DetectionCandidate | null) => {
  const verification = candidate?.verification || {};
  const fields = verification.verification_details || verification.checked_fields;
  return Array.isArray(fields) ? (fields as Record<string, unknown>[]) : [];
};
const previewSrc = (value?: string | null) => {
  if (!value) return "";
  if (value.startsWith("data:") || value.startsWith("blob:") || value.startsWith("http")) return value;
  if (value.startsWith("/")) return `${ADMIN_API_BASE_URL}${value}`;
  return value;
};
const alignmentOf = (candidate?: DetectionCandidate | null) => (candidate?.alignment || {}) as Record<string, unknown>;
const alignmentDebugOf = (candidate?: DetectionCandidate | null) =>
  (candidate?.alignmentDebug || alignmentOf(candidate).alignment_debug || {}) as Record<string, unknown>;
const alignmentStatus = (candidate?: DetectionCandidate | null) => {
  const nestedStatus = alignmentOf(candidate).alignment_status;
  if (candidate?.alignmentStatus) return candidate.alignmentStatus;
  if (
    nestedStatus === "skipped" ||
    nestedStatus === "aligned" ||
    nestedStatus === "fallback" ||
    nestedStatus === "failed"
  ) {
    return nestedStatus;
  }
  return "failed";
};
const alignmentLabel = (candidate?: DetectionCandidate | null) => {
  if (!candidate) return "N/A";
  const status = alignmentStatus(candidate);
  if (status === "skipped") return "Skipped";
  if (status === "aligned") return "Success";
  if (status === "fallback") return "Fallback";
  if (status === "failed") return "Failed";
  if (candidate.alignmentFallbackUsed) return "Fallback";
  if (candidate.alignmentPassed) return "Aligned";
  return "Failed";
};
const alignmentBadgeClass = (candidate?: DetectionCandidate | null) => {
  const status = alignmentStatus(candidate);
  if (status === "aligned") return "bg-emerald-100 text-emerald-700";
  if (status === "skipped") return "bg-slate-100 text-slate-700";
  if (status === "fallback") return "bg-amber-100 text-amber-700";
  return "bg-red-100 text-red-700";
};
const readBool = (value: unknown) => (value === true ? "true" : value === false ? "false" : "N/A");
const readRatioNumber = (roi: Record<string, unknown>, snakeKey: string, camelKey: string) => {
  const snakeValue = roi[snakeKey];
  if (typeof snakeValue === "number") return snakeValue;
  const camelValue = roi[camelKey];
  if (typeof camelValue === "number") return camelValue;
  return null;
};
const roiRatioFromRecord = (roi: Record<string, unknown> | null | undefined) => {
  if (!roi) return null;
  const xRatio = readRatioNumber(roi, "x_ratio", "xRatio");
  const yRatio = readRatioNumber(roi, "y_ratio", "yRatio");
  const widthRatio = readRatioNumber(roi, "width_ratio", "widthRatio");
  const heightRatio = readRatioNumber(roi, "height_ratio", "heightRatio");
  const pageNumber = readRatioNumber(roi, "page_number", "pageNumber") || 1;
  if (xRatio === null || yRatio === null || widthRatio === null || heightRatio === null || widthRatio <= 0 || heightRatio <= 0) return null;
  return { pageNumber, xRatio, yRatio, widthRatio, heightRatio };
};
const stableNumericId = (value: string) =>
  Math.abs(value.split("").reduce((hash, char) => (hash * 31 + char.charCodeAt(0)) | 0, 11));
const isPdfFile = (file?: File | null) => {
  if (!file) return false;
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
};

export default function AdminDetectionLabPage() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [result, setResult] = useState<DetectionDevResult | null>(null);
  const [pageIndex, setPageIndex] = useState(0);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState("");
  const [roiPreviewImageMetrics, setRoiPreviewImageMetrics] = useState<WorkspaceImageMetrics>(DEFAULT_WORKSPACE_IMAGE_METRICS);
  const [selectedPreviewRoiId, setSelectedPreviewRoiId] = useState<number | null>(null);
  const [workspaceTemplateId, setWorkspaceTemplateId] = useState<string | null>(null);
  const [workspaceTemplateFields, setWorkspaceTemplateFields] = useState<TemplateField[]>([]);
  const [workspaceTemplateError, setWorkspaceTemplateError] = useState("");
  useEffect(() => {
    if (!file || isPdfFile(file)) {
      setPreviewUrl("");
      return;
    }
    const nextUrl = URL.createObjectURL(file);
    setPreviewUrl(nextUrl);
    return () => URL.revokeObjectURL(nextUrl);
  }, [file]);

  const pages = result?.pages || [];
  const currentPage = pages[pageIndex] || pages[0] || null;
  const bestCandidate = result?.bestCandidate || null;
  const visibleCandidates = currentPage?.candidates.length ? currentPage.candidates : result?.candidates || [];
  const sortedVisibleCandidates = [...visibleCandidates].sort((left, right) => candidateFinalScore(right) - candidateFinalScore(left));
  const topFinalScore = sortedVisibleCandidates.length > 0 ? candidateFinalScore(sortedVisibleCandidates[0]) : null;
  const secondFinalScore = sortedVisibleCandidates.length > 1 ? candidateFinalScore(sortedVisibleCandidates[1]) : null;
  const finalScoreGap = topFinalScore !== null && secondFinalScore !== null ? topFinalScore - secondFinalScore : null;
  const finalScoreGapQuality = separationQuality(finalScoreGap);
  const topLayoutScore = sortedVisibleCandidates.length > 0 ? candidateLayoutScore(sortedVisibleCandidates[0]) : null;
  const secondLayoutScore = sortedVisibleCandidates.length > 1 ? candidateLayoutScore(sortedVisibleCandidates[1]) : null;
  const layoutScoreGap = topLayoutScore !== null && secondLayoutScore !== null ? topLayoutScore - secondLayoutScore : null;
  const layoutScoreGapQuality = separationQuality(layoutScoreGap);
  const finalTemplateId = bestCandidate?.templateId || currentPage?.bestCandidate?.templateId || null;
  const pageDisplayCandidate =
    currentPage?.bestCandidate ||
    sortedVisibleCandidates.find((candidate) => candidate.finalPassed) ||
    sortedVisibleCandidates[0] ||
    (currentPage && finalTemplateId
      ? currentPage.candidates.find((candidate) => candidate.templateId === finalTemplateId) || null
      : null) ||
    bestCandidate ||
    null;
  const verificationCandidate = pageDisplayCandidate;
  const alignmentCandidate = pageDisplayCandidate;
  const candidateTemplateId = alignmentCandidate?.templateId || null;

  useEffect(() => {
    let cancelled = false;
    setWorkspaceTemplateError("");
    setSelectedPreviewRoiId(null);

    if (!candidateTemplateId) {
      setWorkspaceTemplateId(null);
      setWorkspaceTemplateFields([]);
      return;
    }

    setWorkspaceTemplateId(candidateTemplateId);
    fetchTemplateBundle(candidateTemplateId)
      .then((bundle) => {
        if (cancelled) return;
        setWorkspaceTemplateFields(bundle.fields || []);
      })
      .catch((err) => {
        if (cancelled) return;
        console.warn("Unable to load template bundle for detection lab workspace preview.", err);
        setWorkspaceTemplateFields([]);
        setWorkspaceTemplateError(err instanceof Error ? err.message : "Unable to load template ROI bundle.");
      });

    return () => {
      cancelled = true;
    };
  }, [candidateTemplateId]);

  const currentAlignment = alignmentOf(alignmentCandidate);
  const currentAlignmentDebug = alignmentDebugOf(alignmentCandidate);
  const currentAlignmentStatus = alignmentStatus(alignmentCandidate);
  const verificationSourceUsed =
    alignmentCandidate?.verificationSourceUsed ||
    (currentAlignmentDebug.verification_source_used === "aligned" || currentAlignmentDebug.verification_source_used === "normalized"
      ? currentAlignmentDebug.verification_source_used
      : null);
  const alignmentReason =
    alignmentCandidate?.alignmentReason ||
    (currentAlignmentDebug.alignment_reason as string | undefined) ||
    (currentAlignmentDebug.reason as string | undefined);
  const alignmentPrecheck =
    currentAlignmentDebug.precheck && typeof currentAlignmentDebug.precheck === "object"
      ? (currentAlignmentDebug.precheck as Record<string, unknown>)
      : null;
  const orbExecuted = currentAlignmentDebug.orb_executed === true;
  const homographyFound = currentAlignmentDebug.homography_found === true || currentAlignment.homography_found === true;
  const alignedImagePreviewUrl = previewSrc(alignmentCandidate?.alignedImagePreviewUrl || (currentAlignment.aligned_image_preview_url as string | null | undefined));
  const originalImagePreviewUrl = previewSrc(
    currentPage?.originalImagePreviewUrl ||
      (currentPage?.debug?.original_image_preview_url as string | undefined) ||
      currentPage?.imagePreviewDataUrl ||
      previewUrl
  );
  const normalizedImagePreviewUrl = previewSrc(
    currentPage?.normalizedImagePreviewUrl || (currentPage?.debug?.normalized_image_preview_url as string | undefined) || currentPage?.imagePreviewDataUrl
  );
  const extractionImagePreviewUrl = previewSrc(alignmentCandidate?.extractionImagePreviewUrl);
  const extractionTest = alignmentCandidate?.extractionTest || null;
  const userFlowWorkspaceImageUrl = alignedImagePreviewUrl || extractionImagePreviewUrl || originalImagePreviewUrl;
  const effectivePreviewUrl =
    currentAlignmentStatus === "aligned" && alignedImagePreviewUrl ? alignedImagePreviewUrl : normalizedImagePreviewUrl || currentPage?.imagePreviewDataUrl || "";
  const effectivePreviewLabel = currentAlignmentStatus === "aligned" && alignedImagePreviewUrl ? "Aligned Image" : "Normalized Image";
  const effectivePreviewBadge =
    currentAlignmentStatus === "aligned"
      ? "Aligned image used for verification"
      : currentAlignmentStatus === "skipped"
        ? "Alignment skipped: normalized image used"
        : currentAlignmentStatus === "fallback"
          ? "Alignment fallback: normalized image used"
          : currentAlignmentStatus === "failed"
            ? "Alignment failed: normalized image used"
            : "Pipeline image";
  const roiPreviewImageUrl = userFlowWorkspaceImageUrl;
  const roiPreviewLabel = "Template Workspace Image";
  const roiPreviewBadge = alignedImagePreviewUrl
    ? "Aligned/template canvas with original Template ROI"
    : extractionImagePreviewUrl
      ? "Fallback image with original Template ROI"
      : "Source image fallback with original Template ROI";
  const currentVerificationFields = verificationFields(verificationCandidate);
  const isPdf = isPdfFile(file);
  const sourceType = typeof result?.debug?.source_type === "string" ? result.debug.source_type : isPdf ? "pdf" : "image";
  const convertedPageCount = typeof result?.debug?.converted_page_count === "number" ? result.debug.converted_page_count : 0;
  const inputPageCount = typeof result?.debug?.input_page_count === "number" ? result.debug.input_page_count : pages.length;
  const pipelineImageSteps = [
    {
      title: sourceType === "pdf" ? "PDF Page Image" : "Uploaded Image",
      description:
        sourceType === "pdf"
          ? "The uploaded PDF page is converted to an image before detection."
          : "The original uploaded image used as the detection input.",
      src: originalImagePreviewUrl,
      status: sourceType === "pdf" ? "converted" : "input",
    },
    {
      title: "Normalized Image",
      description: "The image after the normalization stage. Current normalization may preserve the original image when correction is bypassed.",
      src: normalizedImagePreviewUrl,
      status: "preprocess",
    },
    {
      title: "Template Alignment",
      description:
        currentAlignmentStatus === "aligned"
          ? "ORB alignment produced a warped image and this image was used for verification."
          : currentAlignmentStatus === "skipped"
            ? "Alignment was skipped because geometry already matched the template tolerance."
          : currentAlignmentStatus === "fallback"
              ? alignmentReason === "aligned_verification_worse_than_normalized"
                ? "Alignment produced a warped image, but OCR verification was better on the normalized image. The aligned image was not used."
                : "Alignment was attempted but the normalized image was safer, so fallback was used."
              : "Alignment failed or was unavailable, so the normalized image was used.",
      src: alignedImagePreviewUrl || normalizedImagePreviewUrl,
      status: alignmentLabel(alignmentCandidate),
    },
    {
      title: "Final ROI/OCR Image",
      description: "The final image follows the same source selection used by the user workspace.",
      src: userFlowWorkspaceImageUrl,
      status: verificationSourceUsed === "aligned" ? "aligned" : "normalized",
    },
  ];
  const currentPageNumber = currentPage?.pageIndex || 1;
  const matchedTemplatePageNumber =
    typeof alignmentCandidate?.templatePageNumber === "number"
      ? alignmentCandidate.templatePageNumber
      : typeof alignmentCandidate?.metadata?.matched_layout_reference_page_number === "number"
        ? alignmentCandidate.metadata.matched_layout_reference_page_number
        : currentPageNumber;
  const bundleTemplateRoiFields =
    workspaceTemplateId === candidateTemplateId
      ? workspaceTemplateFields
          .filter((field) => !field.useForVerification && field.pageNumber === matchedTemplatePageNumber)
          .sort(
            (left, right) =>
              left.pageNumber - right.pageNumber ||
              (left.sortOrder ?? 0) - (right.sortOrder ?? 0) ||
              left.fieldName.localeCompare(right.fieldName)
          )
          .map((field) => ({
            fieldId: field.id,
            fieldName: field.fieldName,
            displayLabel: field.displayLabel,
            pageNumber: field.pageNumber,
            dataType: field.dataType || "text",
            extractionMethod:
              field.extractionMethod === "ocr_table" ||
              field.extractionMethod === "table_recognition_v2" ||
              field.extractionMethod === "paddle_thai_ocr" ||
              field.extractionMethod === "extract_image"
                ? field.extractionMethod
                : field.dataType === "table"
                  ? "table_recognition_v2"
                  : "paddle_thai_ocr",
            source: "template_bundle",
            roi: {
              page_number: field.roi.pageNumber,
              x_ratio: field.roi.xRatio,
              y_ratio: field.roi.yRatio,
              width_ratio: field.roi.widthRatio,
              height_ratio: field.roi.heightRatio,
            },
          }))
      : [];
  const templateRoiFields =
    bundleTemplateRoiFields.length > 0
      ? bundleTemplateRoiFields
      : alignmentCandidate?.templateRois || [];
  const roiSourceLabel =
    bundleTemplateRoiFields.length > 0
      ? "Original Template ROI from Template Bundle"
      : "Original Template ROI from Detection response";
  const roiPreviewItems = templateRoiFields
    .map((field, index) => {
      const ratio = roiRatioFromRecord(field.roi || null);
      if (!ratio) return null;
      const box = ratioToImageBox(ratio, roiPreviewImageMetrics);
      return {
        key: `${field.fieldId || field.fieldName || "admin-template-roi"}-${index}`,
        label: field.displayLabel || field.fieldName || `Field ${index + 1}`,
        fieldName: field.fieldName || "",
        dataType: field.dataType || "text",
        extractionMethod: field.extractionMethod || (field.dataType === "table" ? "table_recognition_v2" : "paddle_thai_ocr"),
        source: roiSourceLabel,
        projection: "admin_template_roi",
        id: stableNumericId(`detection-template-roi:${field.fieldId || field.fieldName || index}`),
        roi: field.roi as Record<string, unknown> | null | undefined,
        workspaceRoi: {
          id: stableNumericId(`detection-template-roi:${field.fieldId || field.fieldName || index}`),
          fieldName: field.displayLabel || field.fieldName || `Field ${index + 1}`,
          x: box.x,
          y: box.y,
          width: box.width,
          height: box.height,
          pageIndex: Math.max(0, currentPageNumber - 1),
          type: field.dataType === "table" ? "table" : field.dataType === "image" ? "image" : "text",
          kind: "extraction_field",
          enabled: true,
        } as WorkspaceRoi & { kind: string },
      };
    })
    .filter((field): field is NonNullable<typeof field> => Boolean(field));
  const roiPreviewRois = roiPreviewItems.map((item) => item.workspaceRoi);

  useEffect(() => {
    if (!alignmentCandidate || !roiPreviewImageUrl) return;
    console.debug("[Admin Detection Lab] ROI coordinate check", {
      templateId: alignmentCandidate.templateId,
      templateName: alignmentCandidate.templateName,
      roiCoordinateSpace: alignmentCandidate.roiCoordinateSpace,
      roiSourceLabel,
      extractionImagePreviewUrl: alignmentCandidate.extractionImagePreviewUrl,
      alignedImagePreviewUrl: alignmentCandidate.alignedImagePreviewUrl,
      workspaceImageUrl: roiPreviewImageUrl,
      backendCoordinateDebug: alignmentCandidate.coordinateDebug,
      imageMetrics: roiPreviewImageMetrics,
      firstRoi: roiPreviewRois[0]
        ? {
            fieldName: roiPreviewRois[0].fieldName,
            pageIndex: roiPreviewRois[0].pageIndex,
            x: roiPreviewRois[0].x,
            y: roiPreviewRois[0].y,
            width: roiPreviewRois[0].width,
            height: roiPreviewRois[0].height,
          }
        : null,
    });
  }, [alignmentCandidate, roiPreviewImageUrl, roiSourceLabel, roiPreviewImageMetrics, roiPreviewRois]);

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const nextFile = event.target.files?.[0] || null;
    setError("");
    setResult(null);
    setPageIndex(0);
    setSelectedPreviewRoiId(null);
    setWorkspaceTemplateId(null);
    setWorkspaceTemplateFields([]);
    setWorkspaceTemplateError("");
    if (!nextFile) {
      setFile(null);
      return;
    }
    const isSupported =
      nextFile.type === "application/pdf" ||
      nextFile.name.toLowerCase().endsWith(".pdf") ||
      nextFile.type === "image/png" ||
      nextFile.type === "image/jpeg" ||
      nextFile.type === "image/webp";
    if (!isSupported) {
      setFile(null);
      setError("Please choose a PNG, JPEG, WebP, or PDF file.");
      return;
    }
    setFile(nextFile);
  };

  const handleChooseNewDocument = () => {
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
      fileInputRef.current.click();
    }
  };

  const runDetection = async () => {
    if (!file) {
      setError("Please select an image or PDF first.");
      return;
    }
    setIsRunning(true);
    setError("");
    setResult(null);
    setPageIndex(0);
    try {
      setResult(await detectTemplateDev(file));
    } catch (err) {
      console.warn("Detection lab failed.", err);
      setError(err instanceof Error ? err.message : "ค้นหา Template ไม่สำเร็จ");
    } finally {
      setIsRunning(false);
    }
  };

  const extractionDetailsPanel = (
    <details>
      <summary className="cursor-pointer text-xs font-black uppercase tracking-wider text-slate-700">
        Extraction Details (Test Extraction)
      </summary>
      <div className="mt-3 grid gap-2 text-xs font-semibold text-slate-600 sm:grid-cols-2 lg:grid-cols-4">
        <DebugMetric label="Candidate" value={readText(alignmentCandidate?.templateName)} />
        <DebugMetric label="Image Used" value={roiPreviewLabel} />
        <DebugMetric label="ROI Source" value={roiSourceLabel} />
        <DebugMetric
          label="Coordinate Space"
          value={readText(extractionTest?.roiCoordinateSpace || alignmentCandidate?.roiCoordinateSpace || "template_canvas")}
        />
      </div>
      {extractionTest ? (
        <>
          <div className="mt-3 flex flex-wrap gap-2 text-[10px] font-black uppercase">
            <span className="rounded-full bg-slate-100 px-2.5 py-1 text-slate-600">{extractionTest.testedCount} tested</span>
            <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-emerald-700">{extractionTest.passedCount} passed</span>
            <span className="rounded-full bg-red-100 px-2.5 py-1 text-red-700">{extractionTest.failedCount} failed</span>
          </div>
          <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {(extractionTest.fields || []).map((field, index) => (
              <div key={`${field.fieldId || index}-detection-extraction`} className="rounded-xl border border-slate-100 bg-slate-50 p-3 text-xs">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate font-black text-slate-800">{field.displayLabel || field.fieldName || "Field"}</div>
                    <div className="mt-0.5 text-[9px] font-bold uppercase text-slate-400">
                      Page {field.pageNumber ?? "N/A"} - {field.dataType || "text"} - {field.extractionMethod || "paddle_thai_ocr"}
                    </div>
                  </div>
                  <span className={`rounded-full px-2 py-0.5 text-[9px] font-black uppercase ${field.passed ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700"}`}>
                    {field.passed ? "PASS" : "FAIL"}
                  </span>
                </div>
                <div className="mt-2 rounded-lg border border-slate-100 bg-white p-2">
                  <div className="text-[9px] font-black uppercase text-slate-400">OCR Result</div>
                  <p className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-words text-xs font-semibold leading-5 text-slate-700">
                    {field.ocrText || field.actualText || "(no text)"}
                  </p>
                </div>
                <div className="mt-2 flex flex-wrap gap-1 text-[9px] font-black uppercase">
                  {field.confidence !== null && field.confidence !== undefined && (
                    <span className="rounded bg-slate-200 px-1.5 py-0.5 text-slate-600">Conf {field.confidence.toFixed(2)}</span>
                  )}
                  {field.roiSource && <span className="rounded bg-slate-200 px-1.5 py-0.5 text-slate-600">{field.roiSource}</span>}
                  {field.failureReason && <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-700">{field.failureReason}</span>}
                </div>
              </div>
            ))}
          </div>
        </>
      ) : (
        <p className="mt-3 rounded-xl bg-slate-50 p-3 text-xs font-semibold text-slate-500">
          Extraction test result is not available for this candidate.
        </p>
      )}
    </details>
  );

  return (
    <section className="space-y-4">
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-xl font-black text-slate-900">ห้องทดสอบการค้นหา Template</h2>
              <span className="rounded-full bg-amber-100 px-2.5 py-1 text-[10px] font-black uppercase text-amber-700">DEV</span>
              <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[10px] font-black uppercase text-slate-600">Pipeline จริง</span>
            </div>
            <p className="mt-2 text-sm font-semibold text-slate-500">
              อัปโหลดภาพหรือ PDF หลายหน้าเพื่อทดสอบการค้นหา Template ที่เผยแพร่แล้ว ระบบจะแปลง PDF เป็นภาพก่อนเริ่มตรวจจับ
            </p>
          </div>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-4">
        {[
          ["1", "อัปโหลดเอกสาร", "รองรับไฟล์ภาพและ PDF"],
          ["2", "ค้นหา Template", "ตรวจสอบและค้นหา Template ที่ตรงกับเอกสาร"],
          ["3", "ตรวจสอบแต่ละหน้า", "ตรวจสอบและจับคู่ Template ที่เหมาะสมสำหรับแต่ละหน้า"],
          ["4", "แสดง ROI และผลลัพธ์", "แสดง ROI/Extraction จาก Template ที่ match"],
        ].map(([step, title, note]) => (
          <div key={step} className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
            <div className="flex items-center gap-2">
              <span className="flex h-6 w-6 items-center justify-center rounded-full bg-indigo-600 text-[10px] font-black text-white">{step}</span>
              <h3 className="text-xs font-black text-slate-900">{title}</h3>
            </div>
            <p className="mt-2 text-[11px] font-semibold leading-5 text-slate-500">{note}</p>
          </div>
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-[380px_minmax(0,1fr)]">
        <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <h3 className="text-xs font-black uppercase tracking-wider text-slate-700">เอกสารสำหรับทดสอบ</h3>
          <label className="mt-3 flex cursor-pointer flex-col rounded-xl border border-dashed border-slate-300 bg-slate-50 px-4 py-5 text-xs font-bold text-slate-600 hover:bg-white">
            <input ref={fileInputRef} type="file" accept="image/png,image/jpeg,image/webp,application/pdf" onChange={handleFileChange} className="sr-only" />
            <span className="text-sm font-black text-slate-800">เลือกไฟล์ PNG, JPEG, WebP หรือ PDF หลายหน้า</span>
            <span className="mt-1">{file ? `${file.name} (${Math.round(file.size / 1024)} KB)` : "ยังไม่ได้เลือกไฟล์"}</span>
            {file && (
              <span className="mt-2 rounded-lg bg-white px-2 py-1 text-[10px] font-black uppercase text-slate-500">
                {isPdf ? "PDF จะถูกแปลงเป็นภาพ" : "ไฟล์ภาพเดี่ยว"}
              </span>
            )}
          </label>

          <button
            type="button"
            onClick={runDetection}
            disabled={!file || isRunning}
            className="ui-stable-action-lg mt-4 w-full rounded-xl bg-indigo-600 px-4 py-3 text-sm font-black text-white disabled:bg-slate-300 disabled:text-slate-500"
          >
            {isRunning ? "กำลังค้นหา Template..." : "เริ่มค้นหา Template"}
          </button>
          {error && <p className="mt-3 rounded-xl bg-red-50 p-3 text-xs font-bold text-red-700">{error}</p>}

          <div className="mt-4">
            {previewUrl ? (
              <div className="overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
                <img src={previewUrl} alt="Detection lab upload preview" className="max-h-[380px] w-full object-contain" />
              </div>
            ) : file?.type === "application/pdf" ? (
              <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-xs font-semibold text-slate-500">
                เลือกไฟล์ PDF แล้ว ระบบจะแสดงภาพแต่ละหน้าหลังเริ่มค้นหา Template
              </div>
            ) : (
              <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-xs font-semibold text-slate-500">ยังไม่มีภาพตัวอย่าง</div>
            )}
          </div>
        </section>

        <section className="space-y-4">
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <h3 className="text-xs font-black uppercase tracking-wider text-slate-700">ผลการค้นหา Template ตามกระบวนการผู้ใช้</h3>
            {!result ? (
              <p className="mt-3 rounded-xl bg-slate-50 p-4 text-xs font-semibold text-slate-500">กดเริ่มค้นหาเพื่อดู Template ที่ใกล้เคียงที่สุด</p>
            ) : (
              <div className="mt-3 space-y-3 text-xs font-semibold text-slate-700">
                <div className="flex flex-wrap gap-2 text-[10px] font-black uppercase">
                  <span className={`rounded-full px-2.5 py-1 ${result.matched ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700"}`}>
                    {result.matched ? "พบ Template" : "ไม่พบ Template ที่ผ่านเกณฑ์"}
                  </span>
                  <span className="rounded-full bg-slate-100 px-2.5 py-1 text-slate-600">เกณฑ์ {result.threshold}</span>
                  <span className="rounded-full bg-slate-100 px-2.5 py-1 text-slate-600">{inputPageCount || pages.length || 1} หน้า</span>
                  <span className="rounded-full bg-slate-100 px-2.5 py-1 text-slate-600">{sourceType === "pdf" ? "แปลง PDF เป็นภาพแล้ว" : "ไฟล์ภาพ"}</span>
                  <span className="rounded-full bg-slate-100 px-2.5 py-1 text-slate-600">{result.version}</span>
                </div>
                {sourceType === "pdf" && (
                  <p className="rounded-xl bg-sky-50 p-3 font-bold text-sky-700">
                    แปลง PDF จำนวน {convertedPageCount || pages.length} หน้าเป็นภาพสำหรับใช้ค้นหา Template แล้ว
                  </p>
                )}

                {bestCandidate ? (
                  <div className="rounded-xl border border-indigo-100 bg-indigo-50 p-3 text-indigo-900">
                    <div className="text-[10px] font-black uppercase tracking-wider text-indigo-700">Template ที่เหมาะสมที่สุด</div>
                    <div className="mt-2 grid gap-1 sm:grid-cols-2">
                      <p>ชื่อ Template: {bestCandidate.templateName || "N/A"}</p>
                      <p>Template ID: {bestCandidate.templateId || "N/A"}</p>
                      <p>คะแนนรวม: {formatScore(bestCandidate.finalScore ?? bestCandidate.score)}</p>
                      <p>คะแนน Layout: {formatScore(bestCandidate.layoutScore ?? bestCandidate.retrievalScore)}</p>
                      <p>คะแนน Anchor: {formatScore(bestCandidate.verificationScore)}</p>
                      <p>คะแนน Text Anchor: {formatScore(bestCandidate.textAnchorScore)}</p>
                      <p>คะแนน Image Anchor: {formatScore(bestCandidate.imageAnchorScore)}</p>
                      <p>หน้าที่ตรงกัน: {bestCandidate.matchedPages ?? "N/A"}</p>
                      <p>เหตุผลการตัดสินใจ: {bestCandidate.decisionReason || "N/A"}</p>
                      <p>สถานะ: {bestCandidate.templateStatus || "N/A"}</p>
                      <p>Signature ID: {bestCandidate.vectorId || "N/A"}</p>
                      <p>จำนวนหน้า: {bestCandidate.pageCount ?? "N/A"}</p>
                      <p>จำนวน Field: {bestCandidate.fieldCount ?? "N/A"}</p>
                      <p>Model: {bestCandidate.modelName || "N/A"}</p>
                      <p>Retrieval: {bestCandidate.retrievalEngine || bestCandidate.vectorStoreEngine || "N/A"}</p>
                      <p>เกณฑ์คะแนนรวม: {formatScore(bestCandidate.finalConfidenceThreshold)}</p>
                      <p>สถานะ Alignment: {alignmentLabel(bestCandidate)}</p>
                      <p>คะแนน Alignment: {formatScore(bestCandidate.alignmentScore)}</p>
                    </div>
                  </div>
                ) : result.message ? (
                  <p className="rounded-xl bg-amber-50 p-3 font-bold text-amber-700">{result.message}</p>
                ) : (
                  <p className="rounded-xl bg-amber-50 p-3 font-bold text-amber-700">ไม่มี Template ที่ผ่านเกณฑ์คะแนน</p>
                )}

                {!result.matched && result.candidates.length > 0 && (
                  <p className="rounded-xl bg-amber-50 p-3 font-bold text-amber-700">ไม่มี Template ที่ผ่านเกณฑ์คะแนน</p>
                )}
                {result.candidates.length === 0 && (
                  <p className="rounded-xl bg-amber-50 p-3 font-bold text-amber-700">
                    ยังไม่มี Template ที่ Active และมี Layout Signature กรุณาตรวจสอบและเผยแพร่ Template อย่างน้อย 1 รายการก่อน
                  </p>
                )}
              </div>
            )}
          </div>

          {pages.length > 0 && (
            <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <div>
                  <h3 className="text-xs font-black uppercase tracking-wider text-slate-700">Page Results</h3>
                  <p className="mt-1 text-xs font-semibold text-slate-500">
                    {sourceType === "pdf" ? "PDF pages converted to images" : "Uploaded image"} · Page {currentPage?.pageIndex || 1} of {pages.length}
                    {alignmentCandidate ? ` · Template page ${matchedTemplatePageNumber}` : ""}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {pages.map((page, index) => (
                    <button
                      key={`detection-lab-page-${page.pageIndex}`}
                      type="button"
                      onClick={() => setPageIndex(index)}
                      className={`rounded-lg px-3 py-1.5 text-[10px] font-black ${
                        pageIndex === index ? "bg-indigo-600 text-white" : "border border-slate-200 bg-white text-slate-600"
                      }`}
                    >
                      Page {page.pageIndex}
                    </button>
                  ))}
                </div>
              </div>

              <div className="mt-3">
                <div>
                  {false && <section className="mb-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                    <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
                      <div>
                        <h3 className="text-xs font-black uppercase tracking-wider text-slate-700">Image Processing Sequence</h3>
                        <p className="mt-1 text-xs font-semibold text-slate-500">
                          Images are shown in the order used by the detection pipeline, ending with the final ROI/OCR view.
                        </p>
                      </div>
                      <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[10px] font-black uppercase text-slate-600">
                        Page {currentPage?.pageIndex || 1}
                      </span>
                    </div>
                    <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                      {pipelineImageSteps.map((item, index) => (
                        <PipelineImageCard
                          key={`pipeline-image-${index}`}
                          step={index + 1}
                          title={item.title}
                          description={item.description}
                          src={item.src}
                          status={item.status}
                        />
                      ))}
                    </div>
                  </section>}

                  {roiPreviewImageUrl && (
                    <div className="overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
                      <div className="flex flex-col gap-3 border-b border-slate-200 bg-white p-3 lg:flex-row lg:items-center lg:justify-between">
                        <div>
                          <div className="text-[10px] font-black uppercase tracking-wider text-slate-500">{roiPreviewLabel}</div>
                          <div className="mt-1 text-xs font-semibold text-slate-600">{roiPreviewBadge}</div>
                          {workspaceTemplateError && (
                            <div className="mt-1 text-xs font-bold text-amber-700">
                              Template bundle could not be loaded, so this preview is using detection response ROI fallback.
                            </div>
                          )}
                        </div>
                        <span className="w-fit rounded-full bg-indigo-50 px-3 py-1 text-[10px] font-black uppercase text-indigo-700">
                          Admin ROI {roiPreviewRois.length}
                        </span>
                      </div>
                      <div className="bg-white p-4">
                        <WorkspaceCanvas
                          imageSrc={roiPreviewImageUrl}
                          className="h-[560px]"
                          onImageMetricsChange={setRoiPreviewImageMetrics}
                        >
                          <RoiLayer
                            rois={roiPreviewRois}
                            currentPage={Math.max(0, (currentPage?.pageIndex || 1) - 1)}
                            selectedId={selectedPreviewRoiId}
                            readonly
                            showLabels
                            onSelect={setSelectedPreviewRoiId}
                          />
                        </WorkspaceCanvas>
                      </div>
                    </div>
                  )}
                  {currentPage && (
                    <div className="mt-3 rounded-xl border border-slate-200 p-3 text-xs font-semibold text-slate-700">
                      Page {currentPage.pageIndex}: {currentPage.matched ? "Matched" : "No match"}
                      {currentPage.bestCandidate ? `, final score ${formatScore(currentPage.bestCandidate.finalScore ?? currentPage.bestCandidate.score)}` : ""}
                    </div>
                  )}
                  {false && <details className="mt-3 rounded-xl border border-slate-200 bg-white p-3">
                    <summary className="cursor-pointer text-[10px] font-black uppercase tracking-wider text-slate-600">
                      Alignment Debug
                    </summary>
                    {!alignmentCandidate ? (
                      <p className="mt-3 text-xs font-semibold text-slate-500">No candidate selected for alignment debug.</p>
                    ) : (
                      <div className="mt-3 grid gap-2 text-xs font-semibold text-slate-700 sm:grid-cols-2">
                        <p>Candidate: {alignmentCandidate.templateName || "N/A"}</p>
                        <p>
                          Alignment Status:{" "}
                          <span className={`rounded-full px-2 py-0.5 text-[10px] font-black uppercase ${alignmentBadgeClass(alignmentCandidate)}`}>
                            {alignmentLabel(alignmentCandidate)}
                          </span>
                        </p>
                        <p>Verification Source Used: {readText(verificationSourceUsed)}</p>
                        <p>
                          Normalized Verification:{" "}
                          {readScore(
                            currentAlignmentDebug.normalized_verification_score ??
                              currentAlignmentDebug.before_alignment_verification ??
                              alignmentCandidate.normalizedVerificationScore ??
                            alignmentCandidate.beforeAlignmentVerification
                          )}
                        </p>
                        <p>Reason: {readText(alignmentReason)}</p>
                        {currentAlignmentStatus === "skipped" && (
                          <div className="rounded-lg bg-slate-50 p-3 font-bold text-slate-700 sm:col-span-2">
                            <p>Alignment Status: Skipped</p>
                            <p className="mt-2 font-semibold">
                              The uploaded document already matches the template geometry within the acceptable tolerance.
                              OCR verification was performed using the normalized image. No geometric correction was required.
                            </p>
                          </div>
                        )}
                        {orbExecuted && (
                          <>
                            <p>Method: {readText(currentAlignmentDebug.method)}</p>
                            <p>ORB Executed: true</p>
                            <p>Raw Matches: {readValue(currentAlignmentDebug.raw_matches)}</p>
                            <p>Good Matches: {readValue(currentAlignmentDebug.good_matches)}</p>
                            <p>Query Keypoints: {readValue(currentAlignmentDebug.query_keypoints)}</p>
                            <p>Template Keypoints: {readValue(currentAlignmentDebug.template_keypoints)}</p>
                            <p>Inliers: {readValue(currentAlignmentDebug.inliers)}</p>
                            <p>Outliers: {readValue(currentAlignmentDebug.outliers)}</p>
                            <p>Inlier Ratio: {readScore(currentAlignmentDebug.inlier_ratio)}</p>
                            {homographyFound && (
                              <>
                                <p>Homography Found: true</p>
                                <p>Warp Applied: {readBool(currentAlignmentDebug.warp_applied)}</p>
                                <p>Reprojection Error (px): {readScore(currentAlignmentDebug.reprojection_error_px ?? currentAlignmentDebug.reprojection_error)}</p>
                              </>
                            )}
                            {currentAlignmentStatus === "aligned" && (
                              <>
                                <p>Alignment Score: {readScore(currentAlignmentDebug.alignment_score ?? alignmentCandidate.alignmentScore)}</p>
                                <p>
                                  Aligned Verification:{" "}
                                  {readScore(
                                    currentAlignmentDebug.aligned_verification_score ??
                                      currentAlignmentDebug.after_alignment_verification ??
                                      alignmentCandidate.alignedVerificationScore ??
                                      alignmentCandidate.afterAlignmentVerification
                                  )}
                                </p>
                              </>
                            )}
                          </>
                        )}
                        {currentAlignmentStatus === "fallback" && (
                          <p className="rounded-lg bg-amber-50 p-2 font-bold text-amber-700 sm:col-span-2">
                            {alignmentReason === "aligned_verification_worse_than_normalized"
                              ? "Fallback: alignment completed, but normalized OCR verification was better. The warped image was not used."
                              : "Fallback: alignment was attempted, but it could not produce a usable transformed image. OCR verification used the normalized image."}
                          </p>
                        )}
                        {currentAlignmentStatus === "failed" && (
                          <p className="rounded-lg bg-red-50 p-2 font-bold text-red-700 sm:col-span-2">
                            Failed: alignment encountered an unexpected error. OCR verification used the normalized image.
                          </p>
                        )}
                        {currentAlignmentStatus === "failed" && <p className="sm:col-span-2">error: {readText(currentAlignment.error)}</p>}
                        {alignmentPrecheck && (
                          <details className="rounded-lg border border-slate-200 bg-slate-50 p-2 sm:col-span-2">
                            <summary className="cursor-pointer text-[10px] font-black uppercase tracking-wider text-slate-600">
                              Alignment Pre-check
                            </summary>
                            <pre className="mt-2 max-h-40 overflow-auto rounded-lg bg-slate-900 p-3 text-[10px] font-semibold text-slate-100">
                              {JSON.stringify(alignmentPrecheck, null, 2)}
                            </pre>
                          </details>
                        )}
                        {homographyFound && Array.isArray(currentAlignment.homography) && (
                          <details className="rounded-lg border border-slate-200 bg-slate-50 p-2 sm:col-span-2">
                            <summary className="cursor-pointer text-[10px] font-black uppercase tracking-wider text-slate-600">
                              Homography Matrix
                            </summary>
                            <pre className="mt-2 max-h-40 overflow-auto rounded-lg bg-slate-900 p-3 text-[10px] font-semibold text-slate-100">
                              {JSON.stringify(currentAlignment.homography, null, 2)}
                            </pre>
                          </details>
                        )}
                      </div>
                    )}
                  </details>}
                </div>

                <div className="hidden">
                  {pages.map((page, index) => (
                    <button
                      key={`detection-page-thumb-${page.pageIndex}`}
                      type="button"
                      onClick={() => setPageIndex(index)}
                      className={`w-full rounded-lg border p-2 text-left ${
                        pageIndex === index ? "border-indigo-400 bg-white shadow-sm" : "border-slate-200 bg-white/70"
                      }`}
                    >
                      {page.imagePreviewDataUrl && (
                        <img src={page.imagePreviewDataUrl} alt={`Page ${page.pageIndex} thumbnail`} className="h-28 w-full rounded-md object-contain" />
                      )}
                      <div className="mt-2 flex items-center justify-between text-[10px] font-black uppercase">
                        <span className="text-slate-700">Page {page.pageIndex}</span>
                        <span className={page.matched ? "text-emerald-600" : "text-slate-400"}>{page.matched ? "Matched" : "No match"}</span>
                      </div>
                      <p className="mt-1 truncate text-[10px] font-semibold text-slate-500">
                        {page.bestCandidate?.templateName || "No candidate"}
                        {page.bestCandidate ? ` · ${formatScore(page.bestCandidate.score)}` : ""}
                      </p>
                    </button>
                  ))}
                </div>
              </div>
            </section>
          )}

          {result && (
            <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              {extractionDetailsPanel}
            </section>
          )}

          {result && (
            <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <details>
                <summary className="cursor-pointer text-xs font-black uppercase tracking-wider text-slate-700">
                  Verification Details
                </summary>
                {!verificationCandidate ? (
                  <p className="mt-3 rounded-xl bg-slate-50 p-3 text-xs font-semibold text-slate-500">No candidate available for verification details.</p>
                ) : currentVerificationFields.length === 0 ? (
                  <p className="mt-3 rounded-xl bg-slate-50 p-3 text-xs font-semibold text-slate-500">
                    {readText(verificationCandidate.verification?.status)}. No verification fields were checked.
                  </p>
                ) : (
                  <div className="mt-3 space-y-2">
                    <div className="rounded-xl bg-slate-50 p-3 text-xs font-semibold text-slate-600">
                      Candidate: {verificationCandidate.templateName || "N/A"} · Decision: {verificationCandidate.decisionPath || verificationCandidate.decisionReason || "N/A"}
                    </div>
                    <div className="overflow-hidden rounded-xl border border-slate-200">
                      <table className="w-full text-left text-xs">
                        <thead className="bg-slate-50 text-[10px] font-black uppercase tracking-wider text-slate-500">
                          <tr>
                            <th className="px-3 py-2">Field</th>
                            <th className="px-3 py-2">Expected</th>
                            <th className="px-3 py-2">Actual</th>
                            <th className="px-3 py-2">Match</th>
                            <th className="px-3 py-2">Score</th>
                            <th className="px-3 py-2">Reason</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100 font-semibold text-slate-700">
                          {currentVerificationFields.map((field, index) => (
                            <tr key={`${readText(field.field_id)}-${index}`}>
                              <td className="px-3 py-2">{readText(field.field_name)}</td>
                              <td className="px-3 py-2 align-top">{renderVerificationExpected(field)}</td>
                              <td className="px-3 py-2 align-top">{renderVerificationActual(field)}</td>
                              <td className="px-3 py-2">{readText(field.match_type)}</td>
                              <td className="px-3 py-2">{readScore(field.field_score ?? field.score)}</td>
                              <td className="px-3 py-2">{readText(field.failure_reason || field.error)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <details className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                      <summary className="cursor-pointer text-[10px] font-black uppercase tracking-wider text-slate-600">
                        Show Debug Details
                      </summary>
                      <div className="mt-3 grid gap-3 md:grid-cols-2">
                        {currentVerificationFields.map((field, index) => (
                          <div key={`verification-debug-${readText(field.field_id)}-${index}`} className="rounded-xl border border-slate-200 bg-white p-3 text-xs font-semibold text-slate-600">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <div>
                                <div className="font-black text-slate-900">{readText(field.field_name)}</div>
                                <div className="mt-0.5 text-[10px] font-bold uppercase tracking-wide text-slate-400">
                                  {isImageVerificationField(field) ? "Image Anchor" : "Text Anchor"}
                                </div>
                              </div>
                              <span className="rounded-full bg-indigo-100 px-2 py-1 text-[10px] font-black uppercase text-indigo-700">
                                Final input {readScore(field.field_score ?? field.score)}
                              </span>
                            </div>
                            {isImageVerificationField(field) && (
                              <div className="mt-3">
                                <div className="mb-1 text-[9px] font-black uppercase tracking-wider text-sky-700">Current Crop / SigLIP Prediction</div>
                                {renderVerificationActual(field)}
                              </div>
                            )}
                            {isImageVerificationField(field) ? (
                              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                                <DebugMetric label="Score" value={readScore(field.evidence_score ?? field.field_score ?? field.score)} />
                                <DebugMetric label="Pair Score (debug)" value={readScore(field.raw_pair_score)} />
                                <DebugMetric label="Relative Score (debug)" value={`${readScore(field.relative_percentage)}%`} />
                                <DebugMetric label="Expected Category" value={readText(field.image_category_label || field.image_category)} />
                                <DebugMetric label="Predicted Category" value={readText(field.predicted_image_category_label || field.actual_text)} />
                                <DebugMetric label="Target Rank" value={readValue(field.siglip_target_rank)} />
                                <DebugMetric label="Status" value={readText(field.status)} />
                                <DebugMetric label="Scoring" value={readText(field.scoring_version)} />
                                <DebugMetric label="Reason" value={readText(field.failure_reason || field.error)} />
                              </div>
                            ) : (
                              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                                <DebugMetric label="Normalized Expected" value={readText(field.normalized_expected)} />
                                <DebugMetric label="Normalized Actual" value={readText(field.normalized_actual)} />
                                <DebugMetric label="Text Similarity" value={readScore(field.text_similarity_score)} />
                                <DebugMetric label="OCR Confidence" value={readScore(field.ocr_confidence)} />
                                <DebugMetric label="Threshold" value={readScore(field.verification_threshold)} />
                                <DebugMetric label="Field Score" value={readScore(field.field_score ?? field.score)} />
                                <DebugMetric label="Reason" value={readText(field.failure_reason || field.error)} />
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </details>
                  </div>
                )}
              </details>
            </section>
          )}

          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h3 className="text-xs font-black uppercase tracking-wider text-slate-700">Candidates</h3>
                <p className="mt-1 text-[11px] font-semibold text-slate-500">เรียงตามคะแนน Final จากมากไปน้อย</p>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-right">
                  <div className="text-[9px] font-black uppercase tracking-wider text-slate-400">Final Diff</div>
                  <div className="mt-0.5 text-sm font-black tabular-nums text-slate-900">{formatScoreDelta(finalScoreGap)}</div>
                  <span className={`mt-1 inline-flex rounded-full px-2 py-0.5 text-[10px] font-black ${finalScoreGapQuality.className}`}>
                    {finalScoreGapQuality.label}
                  </span>
                </div>
                <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-right">
                  <div className="text-[9px] font-black uppercase tracking-wider text-slate-400">Layout Diff</div>
                  <div className="mt-0.5 text-sm font-black tabular-nums text-slate-900">{formatScoreDelta(layoutScoreGap)}</div>
                  <span className={`mt-1 inline-flex rounded-full px-2 py-0.5 text-[10px] font-black ${layoutScoreGapQuality.className}`}>
                    {layoutScoreGapQuality.label}
                  </span>
                </div>
              </div>
            </div>
            <div className="mt-3 overflow-hidden rounded-xl border border-slate-200">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50 text-[10px] font-black uppercase tracking-wider text-slate-500">
                  <tr>
                    <th className="px-3 py-2">Rank</th>
                    <th className="px-3 py-2">Template Name</th>
                    <th className="px-3 py-2">Final</th>
                    <th className="px-3 py-2">Layout</th>
                    <th className="px-3 py-2">Verification</th>
                    <th className="px-3 py-2">Final Diff</th>
                    <th className="px-3 py-2">Layout Diff</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 font-semibold text-slate-700">
                  {sortedVisibleCandidates.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="px-3 py-4 text-center text-slate-500">
                        No candidates to display.
                      </td>
                    </tr>
                  ) : (
                    sortedVisibleCandidates.map((candidate, index) => (
                      <tr key={`${candidate.vectorId || "candidate"}-${index}`}>
                        <td className="px-3 py-2">{index + 1}</td>
                        <td className="px-3 py-2">{candidate.templateName || "N/A"}</td>
                        <td className="px-3 py-2 font-black text-slate-900">{formatScore(candidateFinalScore(candidate))}</td>
                        <td className="px-3 py-2">{formatScore(candidateLayoutScore(candidate))}</td>
                        <td className="px-3 py-2">
                          {formatScore(candidate.verificationScore)}
                        </td>
                        <td className="px-3 py-2 tabular-nums">
                          {index === 0 ? "Top" : formatScoreDelta((topFinalScore ?? 0) - candidateFinalScore(candidate))}
                        </td>
                        <td className="px-3 py-2 tabular-nums">
                          {index === 0 ? "Top" : formatScoreDelta((topLayoutScore ?? 0) - candidateLayoutScore(candidate))}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </section>
      </div>
    </section>
  );
}
