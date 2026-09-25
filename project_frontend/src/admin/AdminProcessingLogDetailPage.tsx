"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, CheckCircle, ChevronLeft, ChevronRight, Download, FileText, Image as ImageIcon, Table } from "lucide-react";
import { ActionButton, EmptyState, InlineState, PageHeader, cardClassName } from "../shared/ui";
import { authHeaders } from "../auth/session";
import {
  ADMIN_API_BASE_URL,
  fetchProcessingLog,
  formatProcessingLogDateTime,
  type ProcessingLog,
  type ProcessingLogField,
} from "./adminApi";

type FieldKind = "text" | "table" | "image";
type ImageRenderMetrics = {
  offsetX: number;
  offsetY: number;
  width: number;
  height: number;
  naturalWidth: number;
  naturalHeight: number;
};
type RenderedRoiBox = {
  left: number;
  top: number;
  width: number;
  height: number;
  points?: { x: number; y: number }[];
};

const formatSeconds = (ms?: number | null) => (typeof ms === "number" && Number.isFinite(ms) ? `${(ms / 1000).toFixed(2)} s` : "-");
const formatScore = (value: number | null) => (value === null || value === undefined ? "-" : value.toFixed(2));
const safeExportFilename = (value: string) => value.replace(/[^a-z0-9._-]+/gi, "-").replace(/^-+|-+$/g, "") || "processing-log";
const downloadJsonFile = (filename: string, payload: unknown) => {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
};
const backendPreviewSrc = (value?: string | null) => {
  const text = String(value || "").trim();
  if (!text) return "";
  if (/^(https?:|data:|blob:)/i.test(text)) return text;
  return `${ADMIN_API_BASE_URL}${text.startsWith("/") ? text : `/${text}`}`;
};
const normalizeFieldKind = (fieldType: string): FieldKind => {
  const type = fieldType.toLowerCase();
  if (type.includes("table")) return "table";
  if (type.includes("image")) return "image";
  return "text";
};
const fieldKindLabel = (kind: FieldKind) => (kind === "table" ? "ตาราง" : kind === "image" ? "รูปภาพ" : "ข้อความ");
const fieldKindIcon = (kind: FieldKind) => {
  if (kind === "table") return <Table size={13} className="text-indigo-500" />;
  if (kind === "image") return <ImageIcon size={13} className="text-sky-500" />;
  return <FileText size={13} className="text-slate-500" />;
};
const supportsOcrGroundTruthComparison = (fieldType: string) => {
  const kind = normalizeFieldKind(fieldType);
  return kind === "text" || kind === "table";
};
const LEGACY_PROCESSING_LOG_ROI_REFERENCE_WIDTH = 750;
const roiPointValue = (value: unknown) => {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
};
const renderedRoiBox = (
  roi: ProcessingLogField["roi"],
  metrics: ImageRenderMetrics,
  page?: ProcessingLog["sourcePages"][number]
): RenderedRoiBox | null => {
  if (!metrics.width || !metrics.height || !metrics.naturalWidth || !metrics.naturalHeight) return null;
  if (![roi.x, roi.y, roi.width, roi.height].every((value) => Number.isFinite(value))) return null;
  const referenceWidth =
    roi.coordinateUnit === "ratio"
      ? 1
      : roi.roiReferenceWidth ||
        page?.roiReferenceWidth ||
        roi.roiDisplayReferenceWidth ||
        page?.roiDisplayReferenceWidth ||
        LEGACY_PROCESSING_LOG_ROI_REFERENCE_WIDTH;
  const referenceHeight =
    roi.coordinateUnit === "ratio"
      ? 1
      : roi.roiReferenceHeight ||
        page?.roiReferenceHeight ||
        roi.roiDisplayReferenceHeight ||
        page?.roiDisplayReferenceHeight ||
        (metrics.naturalHeight / Math.max(metrics.naturalWidth, 1)) * LEGACY_PROCESSING_LOG_ROI_REFERENCE_WIDTH;
  if (!referenceWidth || !referenceHeight) return null;
  const scaleX = metrics.width / referenceWidth;
  const scaleY = metrics.height / referenceHeight;
  const left = metrics.offsetX + roi.x * scaleX;
  const top = metrics.offsetY + roi.y * scaleY;
  const width = roi.width * scaleX;
  const height = roi.height * scaleY;
  if (width <= 0 || height <= 0) return null;
  const points = Array.isArray(roi.points)
    ? roi.points
        .map((point) => {
          if (!point || typeof point !== "object" || Array.isArray(point)) return null;
          const rawPoint = point as Record<string, unknown>;
          const x = roiPointValue(rawPoint.x ?? rawPoint.xRatio ?? rawPoint.x_ratio);
          const y = roiPointValue(rawPoint.y ?? rawPoint.yRatio ?? rawPoint.y_ratio);
          if (x === null || y === null) return null;
          return {
            x: metrics.offsetX + x * scaleX,
            y: metrics.offsetY + y * scaleY,
          };
        })
        .filter((point): point is { x: number; y: number } => point !== null)
    : [];
  return { left, top, width, height, points: points.length > 2 ? points : undefined };
};
const roiOverlayClassName = (isSelected: boolean, hasPoints: boolean) =>
  `absolute cursor-pointer border text-left transition-all duration-300 ${
    hasPoints
      ? "border-transparent bg-transparent shadow-none"
      : isSelected
        ? "z-30 border-orange-500 bg-orange-500/15 shadow-lg ring-4 ring-orange-500/20"
        : "z-10 border-slate-300 bg-slate-100/5 hover:border-slate-400 hover:bg-slate-100/10"
  }`;
const roiLabelClassName = (isSelected: boolean) =>
  `absolute -top-5 left-0 z-40 max-w-[12rem] truncate rounded border px-1.5 py-0.5 text-[9px] font-sans shadow transition-all ${
    isSelected
      ? "border-orange-600 bg-orange-600 font-extrabold text-white"
      : "border-slate-300 bg-white font-semibold text-slate-500"
  }`;
const logBadgeClass = (tone: "success" | "warning" | "danger") =>
  tone === "success"
    ? "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-100"
    : tone === "warning"
      ? "bg-amber-50 text-amber-700 ring-1 ring-amber-100"
      : "bg-red-50 text-red-700 ring-1 ring-red-100";

function LogBadge({ label, tone }: { label: string; tone: "success" | "warning" | "danger" }) {
  return <span className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-black ${logBadgeClass(tone)}`}>{label}</span>;
}

const buildProcessingLogExport = (log: ProcessingLog) => {
  const groundTruthByField = new Map(log.groundTruth.map((field) => [field.fieldId, field.value]));
  const fields = log.ocrOriginal.map((field) => {
    const kind = normalizeFieldKind(field.fieldType);
    const groundTruth = groundTruthByField.get(field.fieldId) ?? "";
    const supportsComparison = supportsOcrGroundTruthComparison(field.fieldType);
    return {
      fieldId: field.fieldId,
      fieldName: field.fieldName,
      fieldType: field.fieldType,
      fieldKind: kind,
      pageNumber: field.pageNumber,
      roiMode: field.roiMode,
      roi: field.roi,
      ocrOriginal: field.value,
      groundTruth,
      confidence: field.confidence ?? null,
      ocrDiffersFromGroundTruth: supportsComparison ? field.value !== groundTruth : null,
    };
  });
  return {
    exportedAt: new Date().toISOString(),
    exportFormat: "processing_log_detail_json_v1",
    generalInformation: {
      id: log.id,
      processingRunId: log.processingRunId ?? null,
      documentName: log.documentName,
      user: log.user,
      userEmail: log.userEmail ?? null,
      status: log.status,
      createdAt: log.createdAt,
      updatedAt: log.updatedAt ?? null,
      pageCount: log.pageCount,
    },
    timing: {
      detectionProcessingTimeMs: log.detectionProcessingTimeMs,
      ocrProcessingTimeMs: log.ocrProcessingTimeMs,
    },
    templateDetection: log.templateDetection,
    documentPages: log.sourcePages,
    matchedTemplate: log.matchedTemplate ?? null,
    roiSnapshot: log.roiSnapshot ?? [],
    fields,
    groundTruth: log.groundTruth,
    processingResultsSummary: log.processingResultsSummary ?? null,
    metadata: log.metadata ?? {},
    raw: log,
  };
};

export default function AdminProcessingLogDetailPage({ logId }: { logId: string }) {
  const [log, setLog] = useState<ProcessingLog | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const [showRoi, setShowRoi] = useState(true);
  const [showRoiLabels, setShowRoiLabels] = useState(true);
  const [selectedFieldId, setSelectedFieldId] = useState<string | null>(null);
  const [infoMessage, setInfoMessage] = useState("");

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setLoadError("");
    fetchProcessingLog(logId)
      .then((item) => {
        if (!cancelled) {
          setLog(item);
          setCurrentPage(1);
          setSelectedFieldId(null);
        }
      })
      .catch((error) => {
        if (!cancelled) setLoadError(error instanceof Error ? error.message : "Processing log load failed.");
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [logId]);

  if (isLoading) {
    return (
      <section className="space-y-4">
        <PageHeader title="Processing Log Detail" description={logId} actions={<ActionButton href="/admin/logs">กลับไป Processing Logs</ActionButton>} />
        <InlineState tone="info" message="กำลังโหลด Processing Log..." />
      </section>
    );
  }

  if (loadError) {
    return (
      <section className="space-y-4">
        <PageHeader title="Processing Log Detail" description={logId} actions={<ActionButton href="/admin/logs">กลับไป Processing Logs</ActionButton>} />
        <InlineState tone="danger" message={loadError} />
      </section>
    );
  }

  if (!log) {
    return (
      <section className="space-y-4">
        <PageHeader title="Processing Log Detail" description={logId} actions={<ActionButton href="/admin/logs">กลับไป Processing Logs</ActionButton>} />
        <EmptyState title="ไม่พบ Processing Log" message="ไม่พบ Log ID นี้จาก backend" />
      </section>
    );
  }

  const pageCount = Math.max(log.pageCount, 1);
  const pageFields = log.ocrOriginal.filter((field) => field.pageNumber === currentPage);
  const groundTruthByField = new Map(log.groundTruth.map((field) => [field.fieldId, field.value]));
  const currentPageKinds = pageFields.reduce(
    (acc, field) => {
      acc[normalizeFieldKind(field.fieldType)] += 1;
      return acc;
    },
    { text: 0, table: 0, image: 0 } as Record<FieldKind, number>
  );
  const changedOnPage = pageFields.filter(
    (field) => supportsOcrGroundTruthComparison(field.fieldType) && (groundTruthByField.get(field.fieldId) || "") !== field.value
  ).length;

  const setPage = (page: number) => {
    setCurrentPage(page);
    setSelectedFieldId(null);
  };

  const selectField = (field: ProcessingLogField) => {
    setSelectedFieldId(field.fieldId);
    setCurrentPage(field.pageNumber);
  };

  const handleExportLog = () => {
    const payload = buildProcessingLogExport(log);
    downloadJsonFile(`${safeExportFilename(`processing-log-${log.id}`)}.json`, payload);
    setInfoMessage(`Exported ${log.id} as JSON.`);
  };

  return (
    <section className="space-y-3">
      <div className="flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <Link href="/admin/logs" className="inline-flex items-center gap-2 text-xs font-black text-slate-500 hover:text-slate-950">
            <ArrowLeft size={14} />
            กลับไป Processing Logs
          </Link>
          <div className="mt-2">
            <h1 className="text-xl font-black tracking-tight text-slate-950">Processing Log Detail</h1>
            <p className="mt-1 text-sm font-semibold text-slate-500">
              {log.documentName} · {log.id}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={handleExportLog}
          className="inline-flex w-fit items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-black text-slate-700 hover:bg-slate-50"
        >
          <Download size={15} />
          Export Log
        </button>
      </div>

      {infoMessage && <InlineState tone="info" message={infoMessage} />}

      <section className={`${cardClassName} p-3`}>
        <h2 className="text-sm font-black text-slate-950">ข้อมูลทั่วไป</h2>
        <div className="mt-3 grid gap-x-4 gap-y-2 sm:grid-cols-2 xl:grid-cols-4">
          <InfoPair label="ชื่อเอกสาร" value={log.documentName} />
          <InfoPair label="ผู้ใช้งาน" value={log.user} />
          <InfoPair label="วันที่/เวลา" value={formatProcessingLogDateTime(log.createdAt)} />
          <InfoPair
            label="สถานะ"
            value={<LogBadge label={log.status === "completed" ? "สำเร็จ" : "ล้มเหลว"} tone={log.status === "completed" ? "success" : "danger"} />}
          />
          <InfoPair label="จำนวนหน้า" value={`${log.pageCount} หน้า`} />
          <InfoPair label="Detection Time" value={formatSeconds(log.detectionProcessingTimeMs)} />
          <InfoPair label="OCR Time" value={formatSeconds(log.ocrProcessingTimeMs)} />
          <InfoPair label="Log ID" value={log.id} />
        </div>
      </section>

      <section className={`${cardClassName} p-3`}>
        <h2 className="text-sm font-black text-slate-950">Template Detection</h2>
        <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-5">
          <InfoTile label="ผลการตรวจจับ" value={log.templateDetection.matched ? "Matched" : "No Match"} />
          <InfoTile label="Template" value={log.templateDetection.selectedTemplate || "-"} />
          <InfoTile label="Template Version" value={log.templateDetection.templateVersion || "-"} />
          <InfoTile label="Detection Mode" value={log.templateDetection.detectionMode} />
          <InfoTile label="Verification Mode" value={log.templateDetection.verificationMode} />
        </div>

        <div className="mt-4 grid gap-3 xl:grid-cols-2">
          <div>
            <h3 className="text-xs font-black text-slate-900">Retrieval Candidates</h3>
            <div className="mt-2 overflow-hidden rounded-xl border border-slate-200">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50 text-xs font-black text-slate-500">
                  <tr>
                    <th className="px-2.5 py-1.5">Template</th>
                    <th className="px-2.5 py-1.5">Layout Score</th>
                    <th className="px-2.5 py-1.5">Result</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {log.templateDetection.candidates.map((candidate) => (
                    <tr key={`${candidate.template}-${candidate.layoutScore}`}>
                      <td className="px-2.5 py-1.5 font-bold text-slate-800">{candidate.template}</td>
                      <td className="px-2.5 py-1.5 font-semibold text-slate-600">{candidate.layoutScore.toFixed(2)}</td>
                      <td className="px-2.5 py-1.5 font-semibold text-slate-600">{candidate.result}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div>
            <h3 className="text-xs font-black text-slate-900">Verification Result</h3>
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              <InfoTile label="Layout Score" value={formatScore(log.templateDetection.verification.layoutScore)} />
              <InfoTile label="Text Anchor Score" value={formatScore(log.templateDetection.verification.textAnchorScore)} />
              <InfoTile label="Image Anchor Score" value={formatScore(log.templateDetection.verification.imageAnchorScore)} />
              <InfoTile label="Final Score" value={formatScore(log.templateDetection.verification.finalScore)} />
              <InfoTile
                label="Passed"
                value={
                  log.templateDetection.verification.passed === null
                    ? "-"
                    : log.templateDetection.verification.passed
                      ? "Passed"
                      : "Failed"
                }
              />
            </div>
          </div>
        </div>
      </section>

      <section className={`${cardClassName} overflow-hidden`}>
        <div className="border-b border-slate-200 bg-slate-50/60 px-3 py-2.5">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-sm font-black text-slate-950">Document + Processing Results</h2>
              <p className="text-xs font-semibold text-slate-500">Read-only historical view · Page {currentPage} / {pageCount}</p>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <button
                type="button"
                onClick={() => setPage(Math.max(1, currentPage - 1))}
                disabled={currentPage <= 1}
                className="h-8 w-8 rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-35"
                aria-label="หน้าก่อนหน้า"
              >
                <ChevronLeft size={15} className="mx-auto" />
              </button>
              {Array.from({ length: pageCount }, (_, index) => (
                <button
                  key={index}
                  type="button"
                  onClick={() => setPage(index + 1)}
                  className={`h-8 min-w-8 rounded-lg border px-2 text-xs font-black ${
                    currentPage === index + 1
                      ? "border-blue-500 bg-blue-600 text-white"
                      : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
                  }`}
                >
                  {index + 1}
                </button>
              ))}
              <button
                type="button"
                onClick={() => setPage(Math.min(pageCount, currentPage + 1))}
                disabled={currentPage >= pageCount}
                className="h-8 w-8 rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-35"
                aria-label="หน้าถัดไป"
              >
                <ChevronRight size={15} className="mx-auto" />
              </button>
            </div>
          </div>
        </div>

        <div className="grid items-stretch gap-0 xl:h-[46rem] xl:grid-cols-[minmax(22rem,0.92fr)_minmax(0,1.08fr)]">
          <div className="flex min-h-[34rem] flex-col border-b border-slate-200 bg-[#edf2f7] p-3 xl:h-full xl:border-b-0 xl:border-r">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="text-xs font-black text-slate-800">ภาพเอกสาร</h3>
                <p className="text-[10px] font-bold text-slate-500">Document Pages ({pageCount} pages)</p>
              </div>
              <label className="inline-flex h-8 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-[11px] font-black text-slate-700">
                <input type="checkbox" checked={showRoi} onChange={(event) => setShowRoi(event.target.checked)} className="h-3.5 w-3.5 rounded border-slate-300" />
                แสดง ROI
              </label>
            </div>

            <div className="-mt-1 mb-2 flex justify-end">
              <label className="inline-flex h-8 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-[11px] font-black text-slate-700">
                <input
                  type="checkbox"
                  checked={showRoiLabels}
                  onChange={(event) => setShowRoiLabels(event.target.checked)}
                  disabled={!showRoi}
                  className="h-3.5 w-3.5 rounded border-slate-300 disabled:opacity-40"
                />
                ชื่อ­ Field
              </label>
            </div>

            <div className="flex flex-1 items-center justify-center py-2">
              <div className="w-full max-w-[30rem] rounded-xl border border-slate-200 bg-slate-100 p-2">
                <div className="relative aspect-[3/4] overflow-hidden rounded-xl border border-slate-200 bg-white shadow-inner">
                <ProcessingLogDocumentPage
                  log={log}
                  pageNumber={currentPage}
                  roiFields={showRoi ? pageFields : []}
                  showRoiLabels={showRoiLabels}
                  selectedFieldId={selectedFieldId}
                  onSelectField={selectField}
                />
                </div>
              </div>
            </div>
          </div>

          <div className="flex min-h-[34rem] flex-col bg-slate-50/40 xl:h-full xl:min-h-0 xl:overflow-hidden">
            <div className="border-b border-slate-200 bg-white px-3 py-2.5">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <CheckCircle size={15} className="text-blue-600" />
                  <h3 className="text-xs font-black text-slate-800">ผลลัพธ์ของหน้านี้</h3>
                </div>
                <span className="rounded-full bg-slate-100 px-2 py-1 text-[10px] font-bold text-slate-500">Read-only</span>
              </div>
              <div className="mt-2 grid grid-cols-2 gap-2 lg:grid-cols-5">
                <SummaryTile label="ทั้งหมด" value={pageFields.length} helper={`หน้า ${currentPage}/${pageCount}`} />
                <SummaryTile label="ข้อความ" value={currentPageKinds.text} helper="Text" />
                <SummaryTile label="ตาราง" value={currentPageKinds.table} helper="Table" tone="indigo" />
                <SummaryTile label="รูปภาพ" value={currentPageKinds.image} helper="Image" tone="sky" />
                <SummaryTile label="ต่างจาก GT" value={changedOnPage} helper="OCR ≠ GT" tone="amber" />
              </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-3">
              {pageFields.length === 0 ? (
                <EmptyState title="ไม่มีผลลัพธ์ในหน้านี้" message="Log นี้ไม่มี field สำหรับหน้าที่เลือก" />
              ) : (
                <div className="space-y-3">
                  {pageFields.map((field) => (
                    <ReadOnlyFieldResult
                      key={field.fieldId}
                      field={field}
                      groundTruth={groundTruthByField.get(field.fieldId) || "-"}
                      selected={selectedFieldId === field.fieldId}
                      onSelect={() => selectField(field)}
                    />
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </section>
    </section>
  );
}

function ProcessingLogDocumentPage({
  log,
  pageNumber,
  roiFields,
  showRoiLabels,
  selectedFieldId,
  onSelectField,
}: {
  log: ProcessingLog;
  pageNumber: number;
  roiFields: ProcessingLogField[];
  showRoiLabels: boolean;
  selectedFieldId: string | null;
  onSelectField: (field: ProcessingLogField) => void;
}) {
  const page = log.sourcePages.find((item) => item.pageNumber === pageNumber);
  const previewUrl = page?.processingReference
    ? page.processingPreviewUrl || `/admin/processing-logs/${log.id}/pages/${pageNumber}?kind=processing`
    : page?.sourcePreviewUrl || page?.previewUrl || `/admin/processing-logs/${log.id}/pages/${pageNumber}?kind=source`;
  const imageSrc = backendPreviewSrc(previewUrl);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const [objectUrl, setObjectUrl] = useState("");
  const [imageError, setImageError] = useState("");
  const [imageMetrics, setImageMetrics] = useState<ImageRenderMetrics | null>(null);
  const updateImageMetrics = useCallback(() => {
    const container = containerRef.current;
    const image = imageRef.current;
    if (!container || !image || !image.naturalWidth || !image.naturalHeight) {
      setImageMetrics(null);
      return;
    }
    const containerRect = container.getBoundingClientRect();
    const naturalRatio = image.naturalWidth / image.naturalHeight;
    const containerRatio = containerRect.width / Math.max(containerRect.height, 1);
    const renderedHeight = containerRatio > naturalRatio ? containerRect.height : containerRect.width / naturalRatio;
    const renderedWidth = containerRatio > naturalRatio ? renderedHeight * naturalRatio : containerRect.width;
    setImageMetrics({
      offsetX: (containerRect.width - renderedWidth) / 2,
      offsetY: (containerRect.height - renderedHeight) / 2,
      width: renderedWidth,
      height: renderedHeight,
      naturalWidth: image.naturalWidth,
      naturalHeight: image.naturalHeight,
    });
  }, []);
  useEffect(() => {
    if (!imageSrc) {
      setObjectUrl("");
      setImageError("");
      setImageMetrics(null);
      return;
    }
    if (/^(data:|blob:)/i.test(imageSrc)) {
      setObjectUrl(imageSrc);
      setImageError("");
      setImageMetrics(null);
      return;
    }
    let cancelled = false;
    let nextObjectUrl = "";
    setObjectUrl("");
    setImageError("");
    fetch(imageSrc, { headers: authHeaders(), credentials: "include" })
      .then((response) => {
        if (!response.ok) throw new Error(`Preview image failed with ${response.status}`);
        return response.blob();
      })
      .then((blob) => {
        if (cancelled) return;
        nextObjectUrl = URL.createObjectURL(blob);
        setObjectUrl(nextObjectUrl);
      })
      .catch((error) => {
        if (!cancelled) setImageError(error instanceof Error ? error.message : "Preview image load failed.");
      });
    return () => {
      cancelled = true;
      if (nextObjectUrl) URL.revokeObjectURL(nextObjectUrl);
    };
  }, [imageSrc]);
  useEffect(() => {
    updateImageMetrics();
    window.addEventListener("resize", updateImageMetrics);
    return () => window.removeEventListener("resize", updateImageMetrics);
  }, [objectUrl, updateImageMetrics]);

  const overlay = imageMetrics
    ? roiFields
        .map((field) => ({ field, box: renderedRoiBox(field.roi, imageMetrics, page) }))
        .filter((item): item is { field: ProcessingLogField; box: RenderedRoiBox } => item.box !== null)
    : [];

  if (objectUrl) {
    return (
      <div ref={containerRef} className="absolute inset-0">
        <img
          ref={imageRef}
          src={objectUrl}
          alt={`${log.documentName} page ${pageNumber}`}
          className="absolute inset-0 h-full w-full object-contain"
          onLoad={updateImageMetrics}
        />
        {overlay.map(({ field, box }) => {
          const isSelected = selectedFieldId === field.fieldId;
          const hasPoints = Boolean(box.points?.length);
          return (
          <button
            key={field.fieldId}
            type="button"
            onClick={() => onSelectField(field)}
            className={roiOverlayClassName(isSelected, hasPoints)}
            style={{
              left: `${box.left}px`,
              top: `${box.top}px`,
              width: `${box.width}px`,
              height: `${box.height}px`,
            }}
          >
            {box.points && (
              <svg className="absolute inset-0 z-10 h-full w-full overflow-visible">
                <polygon
                  points={box.points.map((point) => `${point.x - box.left},${point.y - box.top}`).join(" ")}
                  fill={isSelected ? "rgba(249, 115, 22, 0.16)" : "rgba(148, 163, 184, 0.05)"}
                  stroke={isSelected ? "#f97316" : "#94a3b8"}
                  strokeWidth="2"
                  strokeDasharray={isSelected ? "0" : "3,3"}
                />
              </svg>
            )}
            {showRoiLabels && isSelected && <span className={roiLabelClassName(isSelected)}>{field.fieldName}</span>}
          </button>
          );
        })}
      </div>
    );
  }
  if (imageError) {
    return (
      <div className="absolute inset-0 flex items-center justify-center p-6 text-center text-xs font-bold text-red-600">
        {imageError}
      </div>
    );
  }
  if (imageSrc) {
    return (
      <div className="absolute inset-0 flex items-center justify-center p-6 text-center text-xs font-bold text-slate-500">
        กำลังโหลดภาพเอกสาร...
      </div>
    );
  }
  return (
    <div className="absolute inset-0 p-[8%]">
      <div className="flex h-full flex-col">
        <div className="flex items-start justify-between border-b border-slate-200 pb-3">
          <div>
            <div className="h-3 w-24 rounded bg-blue-200" />
            <div className="mt-3 h-2 w-40 rounded bg-slate-200" />
          </div>
          <div className="space-y-2">
            <div className="h-2 w-24 rounded bg-slate-200" />
            <div className="h-2 w-20 rounded bg-slate-200" />
          </div>
        </div>
        <div className="mt-6 space-y-2.5">
          {Array.from({ length: pageNumber === 1 ? 10 : 7 }, (_, index) => (
            <div
              key={index}
              className="h-2 rounded bg-slate-200"
              style={{ width: `${index % 3 === 0 ? 88 : index % 3 === 1 ? 72 : 94}%` }}
            />
          ))}
        </div>
        <div className="mt-auto border-t border-slate-100 pt-3 text-[10px] font-black text-slate-300">
          {log.documentName} · Page {pageNumber}
        </div>
      </div>
    </div>
  );
}

function ReadOnlyFieldResult({
  field,
  groundTruth,
  selected,
  onSelect,
}: {
  field: ProcessingLogField;
  groundTruth: string;
  selected: boolean;
  onSelect: () => void;
}) {
  const kind = normalizeFieldKind(field.fieldType);
  const isDifferent = supportsOcrGroundTruthComparison(field.fieldType) && field.value !== groundTruth;
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full rounded-xl border bg-white p-3 text-left transition-colors ${
        selected ? "border-blue-200 bg-blue-50/60 ring-1 ring-blue-100" : "border-slate-200 hover:bg-white hover:ring-1 hover:ring-slate-200"
      }`}
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="truncate text-sm font-black text-slate-900">{field.fieldName}</p>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] font-bold uppercase text-slate-400">
            {fieldKindIcon(kind)}
            <span>ประเภท: {fieldKindLabel(kind)}</span>
            <span>Page {field.pageNumber}</span>
            <span>{field.roiMode === "fixed" ? "Fixed" : "Flexible"}</span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {typeof field.confidence === "number" && (
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-black tabular-nums text-slate-600">
              {(field.confidence * 100).toFixed(1)}%
            </span>
          )}
          {isDifferent && <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-black text-amber-700">OCR ≠ GT</span>}
        </div>
      </div>

      <div className="mt-3 grid gap-2 lg:grid-cols-2">
        <ReadOnlyValue label={kind === "image" ? "Image Result" : kind === "table" ? "Original table result" : "ข้อความจาก OCR"} value={field.value} kind={kind} />
        <ReadOnlyValue label="Ground Truth" value={groundTruth} kind={kind} mutedHighlight={isDifferent} />
      </div>
    </button>
  );
}

function ReadOnlyValue({ label, value, kind, mutedHighlight }: { label: string; value: string; kind: FieldKind; mutedHighlight?: boolean }) {
  if (kind === "image") {
    return (
      <div className="min-w-0">
        <p className="mb-1 text-[10px] font-black uppercase tracking-wider text-slate-400">{label}</p>
        <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-3 py-4 text-center text-xs font-bold text-slate-500">
          {value || "Image field ไม่มีข้อความ OCR"}
        </div>
      </div>
    );
  }
  if (kind === "table") {
    const rows = value
      .split(/\r?\n/)
      .map((row) => row.split("|").map((cell) => cell.trim()).filter(Boolean))
      .filter((row) => row.length > 0);
    return (
      <div className="min-w-0">
        <p className="mb-1 text-[10px] font-black uppercase tracking-wider text-slate-400">{label}</p>
        <div className={`overflow-x-auto rounded-xl border ${mutedHighlight ? "border-amber-100 bg-amber-50/40" : "border-slate-200 bg-slate-50"}`}>
          {rows.length > 0 ? (
            <table className="min-w-full text-left text-xs">
              <tbody className="divide-y divide-slate-200">
                {rows.map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {row.map((cell, cellIndex) => (
                      <td key={`${rowIndex}-${cellIndex}`} className="px-2 py-1.5 font-semibold text-slate-700">
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="px-3 py-2 text-xs font-semibold text-slate-500">{value || "-"}</p>
          )}
        </div>
      </div>
    );
  }
  return (
    <div className="min-w-0">
      <p className="mb-1 text-[10px] font-black uppercase tracking-wider text-slate-400">{label}</p>
      <div
        className={`min-h-10 rounded-xl border px-3 py-2 text-xs font-semibold leading-relaxed text-slate-700 ${
          mutedHighlight ? "border-amber-100 bg-amber-50/40" : "border-slate-200 bg-slate-50"
        }`}
        style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
      >
        {value || <span className="italic text-slate-400">(ไม่มีข้อมูล)</span>}
      </div>
    </div>
  );
}

function InfoPair({ label, value }: { label: string; value: string | React.ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] font-black text-slate-400">{label}</p>
      <div className="mt-0.5 min-w-0 truncate text-sm font-black text-slate-800">{value}</div>
    </div>
  );
}

function InfoTile({ label, value }: { label: string; value: string | React.ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5">
      <p className="text-[11px] font-black text-slate-400">{label}</p>
      <p className="text-sm font-black text-slate-900">{value}</p>
    </div>
  );
}

function SummaryTile({
  label,
  value,
  helper,
  tone = "slate",
}: {
  label: string;
  value: number;
  helper: string;
  tone?: "slate" | "indigo" | "sky" | "amber";
}) {
  const toneClass =
    tone === "indigo"
      ? "border-indigo-100 bg-indigo-50/60 text-indigo-900"
      : tone === "sky"
        ? "border-sky-100 bg-sky-50/70 text-sky-900"
        : tone === "amber"
          ? "border-amber-100 bg-amber-50/70 text-amber-900"
          : "border-slate-200 bg-white text-slate-900";
  return (
    <div className={`rounded-xl border p-2 text-left ${toneClass}`}>
      <p className="text-[10px] font-black uppercase tracking-wider text-slate-400">{label}</p>
      <p className="mt-0.5 text-base font-black tabular-nums">{value}</p>
      <p className="text-[9px] font-bold text-slate-400">{helper}</p>
    </div>
  );
}
