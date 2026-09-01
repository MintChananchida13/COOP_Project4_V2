"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Loader2, XCircle } from "lucide-react";
import RoiLayer from "../shared/workspace/RoiLayer";
import { WorkspaceRoi } from "../shared/workspace/RoiBox";
import WorkspaceCanvas from "../shared/workspace/WorkspaceCanvas";
import { DEFAULT_WORKSPACE_IMAGE_METRICS, ratioToImageBox, WorkspaceImageMetrics } from "../shared/workspace/roiGeometry";
import { authHeaders } from "../auth/session";
import { IgnoreRegion, Template, TemplateField, TemplatePage } from "../types/ocr";
import {
  ADMIN_API_BASE_URL,
  PrepublishCandidate,
  PrepublishDetectionTestResult,
  PrepublishLayoutSignaturePage,
  PrepublishSimulationResult,
  confirmTemplatePublish,
  fetchTemplateBundle,
  runPrepublishDetectionTest,
  runPrepublishSimulation,
  updateTemplateApi,
} from "./adminApi";

const samplePage =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='750' height='1000' viewBox='0 0 750 1000'%3E%3Crect width='750' height='1000' fill='%23ffffff'/%3E%3Crect x='70' y='70' width='610' height='90' rx='8' fill='%23e2e8f0'/%3E%3Crect x='70' y='210' width='270' height='34' rx='5' fill='%23cbd5e1'/%3E%3Crect x='70' y='275' width='610' height='22' rx='4' fill='%23e2e8f0'/%3E%3Crect x='70' y='325' width='610' height='22' rx='4' fill='%23e2e8f0'/%3E%3Crect x='70' y='420' width='610' height='220' rx='8' fill='%23f1f5f9' stroke='%23cbd5e1'/%3E%3Ctext x='375' y='910' text-anchor='middle' font-family='Arial' font-size='24' fill='%2364758b'%3ETemplate Sample Page%3C/text%3E%3C/svg%3E";

interface OcrPreviewResult {
  id: string;
  pageNumber: number;
  fieldName: string;
  displayLabel: string;
  extractionMethod: string;
  ocrText: string;
  confidence?: number;
  roiPreviewUrl?: string;
  expectedText?: string;
  verificationStatus?: "pass" | "fail" | "not_configured";
  passed?: boolean;
}

const DEFAULT_FINAL_CONFIDENCE_THRESHOLD = 0.75;
const DEFAULT_MATCHING_WEIGHTS = {
  layoutWeight: 0.4,
  textAnchorWeight: 0.3,
  imageAnchorWeight: 0.3,
};
const MIN_DUAL_ANCHOR_WEIGHT = 0.2;

const stableNumericId = (value: string) =>
  Math.abs(value.split("").reduce((hash, char) => (hash * 31 + char.charCodeAt(0)) | 0, 7));

const clampUnit = (value: number) => Math.max(0, Math.min(1, value));

const readMatchingWeights = (weights: {
  layoutWeight?: number | null;
  textAnchorWeight?: number | null;
  imageAnchorWeight?: number | null;
}) => ({
  layoutWeight: clampUnit(Number.isFinite(weights.layoutWeight) ? Number(weights.layoutWeight) : DEFAULT_MATCHING_WEIGHTS.layoutWeight),
  textAnchorWeight: clampUnit(Number.isFinite(weights.textAnchorWeight) ? Number(weights.textAnchorWeight) : DEFAULT_MATCHING_WEIGHTS.textAnchorWeight),
  imageAnchorWeight: clampUnit(Number.isFinite(weights.imageAnchorWeight) ? Number(weights.imageAnchorWeight) : DEFAULT_MATCHING_WEIGHTS.imageAnchorWeight),
});

const normalizeMatchingWeights = (weights: {
  layoutWeight?: number | null;
  textAnchorWeight?: number | null;
  imageAnchorWeight?: number | null;
}) => {
  const raw = readMatchingWeights(weights);
  const total = raw.layoutWeight + raw.textAnchorWeight + raw.imageAnchorWeight;
  if (total <= 0) return DEFAULT_MATCHING_WEIGHTS;
  return {
    layoutWeight: Number((raw.layoutWeight / total).toFixed(4)),
    textAnchorWeight: Number((raw.textAnchorWeight / total).toFixed(4)),
    imageAnchorWeight: Number((raw.imageAnchorWeight / total).toFixed(4)),
  };
};

const formatWeightPercent = (value: number) => `${Math.round(value * 100)}%`;

const roundWeight = (value: number) => Number(clampUnit(value).toFixed(4));

const calculateMatchingWeights = ({
  layoutWeight,
  textAnchorCount,
  imageAnchorCount,
  preferredTextWeight,
}: {
  layoutWeight: number;
  textAnchorCount: number;
  imageAnchorCount: number;
  preferredTextWeight?: number;
}) => {
  const hasText = textAnchorCount > 0;
  const hasImage = imageAnchorCount > 0;
  if (!hasText && !hasImage) {
    return { layoutWeight: 1, textAnchorWeight: 0, imageAnchorWeight: 0 };
  }

  const layout = roundWeight(Math.max(0.3, Math.min(0.5, layoutWeight)));
  const remaining = roundWeight(1 - layout);
  if (hasText && hasImage) {
    const text = preferredTextWeight === undefined
      ? roundWeight(remaining / 2)
      : roundWeight(Math.max(MIN_DUAL_ANCHOR_WEIGHT, Math.min(remaining - MIN_DUAL_ANCHOR_WEIGHT, preferredTextWeight)));
    return {
      layoutWeight: layout,
      textAnchorWeight: text,
      imageAnchorWeight: roundWeight(remaining - text),
    };
  }
  if (hasText) return { layoutWeight: layout, textAnchorWeight: remaining, imageAnchorWeight: 0 };
  return { layoutWeight: layout, textAnchorWeight: 0, imageAnchorWeight: remaining };
};

const fieldToRoi = (field: TemplateField, metrics: WorkspaceImageMetrics): WorkspaceRoi & { kind: string; pageNumber: number } => {
  const box = ratioToImageBox(field.roi, metrics);
  const isAnchor = field.useForVerification;
  return {
    id: stableNumericId(`${isAnchor ? "anchor" : "field"}:${field.id}`),
    fieldName: field.displayLabel || field.fieldName,
    x: box.x,
    y: box.y,
    width: box.width,
    height: box.height,
    pageIndex: field.pageNumber - 1,
    pageNumber: field.pageNumber,
    kind: isAnchor ? "verification_anchor" : "extraction_field",
    type: field.dataType === "table" ? "table" : field.dataType === "image" ? "image" : "text",
  };
};

const loadImage = (src: string) =>
  new Promise<HTMLImageElement>((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = reject;
    image.src = src;
  });

const cropFieldPreview = async (imageSrc: string, field: TemplateField) => {
  const image = await loadImage(imageSrc);
  const canvas = document.createElement("canvas");
  const x = Math.max(0, field.roi.xRatio * image.naturalWidth);
  const y = Math.max(0, field.roi.yRatio * image.naturalHeight);
  const width = Math.max(1, field.roi.widthRatio * image.naturalWidth);
  const height = Math.max(1, field.roi.heightRatio * image.naturalHeight);
  canvas.width = Math.round(width);
  canvas.height = Math.round(height);
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(image, x, y, width, height, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.92);
};

const evaluateVerification = (field: TemplateField, ocrText: string): OcrPreviewResult["verificationStatus"] => {
  if (!field.useForVerification) return undefined;
  if (!field.expectedText) return "not_configured";
  return ocrText.toLowerCase().includes(field.expectedText.toLowerCase()) ? "pass" : "fail";
};

const formatPrepublishScore = (value?: number | null) => (typeof value === "number" ? value.toFixed(2) : "N/A");

const readPrepublishValue = (record: Record<string, unknown>, keys: string[]) => {
  for (const key of keys) {
    const value = record[key];
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return null;
};

const isImageVerificationRecord = (record: Record<string, unknown>) => {
  const typeText = String(readPrepublishValue(record, ["anchor_type", "verification_method", "match_type", "type"]) || "").toLowerCase();
  return typeText.includes("image");
};

const readVerificationRecordScore = (record: Record<string, unknown>) => {
  const value = isImageVerificationRecord(record)
    ? readPrepublishValue(record, ["evidence_score", "image_category_score", "field_score", "score"])
    : readPrepublishValue(record, ["text_match_score", "field_score", "score", "similarity_score", "text_similarity_score"]);
  const numericValue = Number(value);
  return Number.isFinite(numericValue) ? numericValue : 0;
};

function DraftSummaryCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-xl border border-slate-100 bg-slate-50 p-3">
      <div className="text-[10px] font-black uppercase tracking-wider text-slate-400">{label}</div>
      <div className="mt-1 text-lg font-black text-slate-900">{value}</div>
    </div>
  );
}

function DraftOverviewMetric({ label, value, tone = "slate" }: { label: string; value: string | number; tone?: "slate" | "indigo" | "orange" | "emerald" }) {
  const toneClass = {
    slate: "border-slate-200 bg-slate-50 text-slate-900",
    indigo: "border-indigo-100 bg-indigo-50 text-indigo-950",
    orange: "border-orange-100 bg-orange-50 text-orange-950",
    emerald: "border-emerald-100 bg-emerald-50 text-emerald-950",
  }[tone];
  const labelClass = {
    slate: "text-slate-500",
    indigo: "text-indigo-600",
    orange: "text-orange-700",
    emerald: "text-emerald-700",
  }[tone];
  return (
    <div className={`rounded-lg border px-3 py-2.5 ${toneClass}`}>
      <div className={`text-[9px] font-black uppercase tracking-wider ${labelClass}`}>{label}</div>
      <div className="mt-1 truncate text-sm font-black">{value}</div>
    </div>
  );
}

function MatchingWeightsPanel({
  matchingWeights,
  effectiveMatchingWeights,
  textAnchorCount,
  imageAnchorCount,
  onLayoutChange,
  onTextChange,
  onImageChange,
  onWeightBlur,
  onUseRecommended,
}: {
  matchingWeights: typeof DEFAULT_MATCHING_WEIGHTS;
  effectiveMatchingWeights: typeof DEFAULT_MATCHING_WEIGHTS;
  textAnchorCount: number;
  imageAnchorCount: number;
  onLayoutChange: (value: number) => void;
  onTextChange: (value: number) => void;
  onImageChange: (value: number) => void;
  onWeightBlur: () => void;
  onUseRecommended: () => void;
}) {
  const hasText = textAnchorCount > 0;
  const hasImage = imageAnchorCount > 0;
  const hasAnyAnchor = hasText || hasImage;
  const remaining = Math.round((1 - effectiveMatchingWeights.layoutWeight) * 100);
  const layoutPercent = Math.round(effectiveMatchingWeights.layoutWeight * 100);
  const textPercent = Math.round(effectiveMatchingWeights.textAnchorWeight * 100);
  const imagePercent = Math.round(effectiveMatchingWeights.imageAnchorWeight * 100);
  const textImageLocked = hasText && hasImage;
  const dualAnchorMinPercent = textImageLocked ? MIN_DUAL_ANCHOR_WEIGHT * 100 : 0;
  const dualAnchorMaxPercent = textImageLocked ? remaining - dualAnchorMinPercent : remaining;
  const layoutOptions = [30, 35, 40, 45, 50];

  return (
    <div className="mt-3 rounded-xl border border-slate-200 bg-white p-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h4 className="text-[11px] font-black uppercase tracking-wider text-slate-700">กำหนดค่าน้ำหนัก</h4>
          <p className="mt-1 text-[11px] font-semibold leading-relaxed text-slate-500">
            กำหนดน้ำหนัก Layout แล้วระบบจะคำนวณส่วนที่เหลือให้ Text/Image Anchors อัตโนมัติ
          </p>
        </div>
        <button
          type="button"
          onClick={onUseRecommended}
          className="ui-stable-action rounded-xl border border-slate-200 bg-white px-3 py-2 text-[10px] font-black text-slate-700 hover:border-indigo-200 hover:text-indigo-700"
        >
          ใช้ค่าแนะนำ
        </button>
      </div>

      <div className="mt-3 rounded-lg border border-slate-100 bg-slate-50 p-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <label className="block min-w-36">
            <span className="block text-[10px] font-black uppercase tracking-wider text-slate-500">Layout</span>
            <select
              value={layoutPercent}
              disabled={!hasAnyAnchor}
              onChange={(event) => onLayoutChange(Number(event.target.value) / 100)}
              onBlur={onWeightBlur}
              className="mt-2 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-black text-slate-800 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400"
            >
              {hasAnyAnchor ? layoutOptions.map((value) => <option key={value} value={value}>{value}%</option>) : <option value={100}>100%</option>}
            </select>
          </label>
          <input
            type="range"
            min={hasAnyAnchor ? 30 : 100}
            max={hasAnyAnchor ? 50 : 100}
            step={5}
            value={layoutPercent}
            disabled={!hasAnyAnchor}
            onChange={(event) => onLayoutChange(Number(event.target.value) / 100)}
            onMouseUp={onWeightBlur}
            onTouchEnd={onWeightBlur}
            className="w-full accent-indigo-600 disabled:opacity-50"
          />
        </div>
        <p className="mt-2 text-[10px] font-semibold text-slate-500">
          กำหนดน้ำหนัก Layout ได้ระหว่าง 30–50% โดยปรับครั้งละ 5% ส่วนที่เหลือจะถูกแบ่งให้ Text และ Image Anchors {remaining}%.
        </p>
      </div>

      <div className="mt-3 grid gap-2 md:grid-cols-2">
        <label className="block rounded-lg border border-slate-100 bg-slate-50 p-3">
          <span className="block text-[10px] font-black uppercase tracking-wider text-slate-500">Text</span>
          <input
            type="range"
            min={dualAnchorMinPercent}
            max={dualAnchorMaxPercent}
            step="1"
            value={textPercent}
            disabled={!hasText || !textImageLocked}
            onChange={(event) => onTextChange(Number(event.target.value) / 100)}
            onMouseUp={onWeightBlur}
            onTouchEnd={onWeightBlur}
            className="mt-3 w-full accent-emerald-600 disabled:opacity-50"
          />
          <div className="mt-2 flex items-center gap-2">
            <input
              type="number"
              min={dualAnchorMinPercent}
              max={dualAnchorMaxPercent}
              step="1"
              value={textPercent}
              disabled={!hasText || !textImageLocked}
              onChange={(event) => onTextChange(Number(event.target.value) / 100)}
              onBlur={onWeightBlur}
              className="w-24 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm font-black text-slate-800 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400"
            />
            <span className="text-sm font-black text-slate-800">%</span>
          </div>
          <span className="mt-1 block text-[10px] font-semibold text-slate-500">
            {!hasText ? "ไม่มี Text Anchor" : textImageLocked ? "เมื่อปรับ Text ระบบจะปรับ Image อัตโนมัติ" : "ใช้ค่าน้ำหนักที่เหลือทั้งหมด"}
          </span>
        </label>

        <label className="block rounded-lg border border-slate-100 bg-slate-50 p-3">
          <span className="block text-[10px] font-black uppercase tracking-wider text-slate-500">Image</span>
          <input
            type="range"
            min={dualAnchorMinPercent}
            max={dualAnchorMaxPercent}
            step="1"
            value={imagePercent}
            disabled={!hasImage || !textImageLocked}
            onChange={(event) => onImageChange(Number(event.target.value) / 100)}
            onMouseUp={onWeightBlur}
            onTouchEnd={onWeightBlur}
            className="mt-3 w-full accent-sky-600 disabled:opacity-50"
          />
          <div className="mt-2 flex items-center gap-2">
            <input
              type="number"
              min={dualAnchorMinPercent}
              max={dualAnchorMaxPercent}
              step="1"
              value={imagePercent}
              disabled={!hasImage || !textImageLocked}
              onChange={(event) => onImageChange(Number(event.target.value) / 100)}
              onBlur={onWeightBlur}
              className="w-24 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm font-black text-slate-800 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400"
            />
            <span className="text-sm font-black text-slate-800">%</span>
          </div>
          <span className="mt-1 block text-[10px] font-semibold text-slate-500">
            {!hasImage ? "ไม่มี Image Anchor" : textImageLocked ? "เมื่อปรับ Image ระบบจะปรับ Text อัตโนมัติ" : "ใช้ค่าน้ำหนักที่เหลือทั้งหมด"}
          </span>
        </label>
      </div>

      <div className="mt-3 rounded-lg bg-indigo-50 px-3 py-2 text-[11px] font-bold text-indigo-800">
        ค่าน้ำหนักปัจจุบัน : Layout {formatWeightPercent(effectiveMatchingWeights.layoutWeight)} / Text {formatWeightPercent(effectiveMatchingWeights.textAnchorWeight)} / Image {formatWeightPercent(effectiveMatchingWeights.imageAnchorWeight)}
        {!hasAnyAnchor && <span className="block text-[10px] text-indigo-600">No Text/Image Anchor. Layout is 100%.</span>}
        {hasText && hasImage && <span className="block text-[10px] text-indigo-600">Text และ Image รวมกันต้องเท่ากับค่าน้ำหนักที่เหลือ และแต่ละส่วนต้องไม่น้อยกว่า 20%</span>}
      </div>
    </div>
  );
}

function DraftSectionHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div>
      <h3 className="text-xs font-black uppercase tracking-wider text-slate-700">{title}</h3>
      {subtitle && <p className="mt-1 text-[11px] font-semibold text-slate-500">{subtitle}</p>}
    </div>
  );
}

function DraftStatusPill({ passed, label }: { passed: boolean; label?: string }) {
  return (
    <span className={`rounded-full px-2.5 py-1 text-[10px] font-black uppercase ${passed ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700"}`}>
      {label || (passed ? "PASS" : "FAIL")}
    </span>
  );
}

function DraftCandidateCard({
  candidate,
  open,
  onToggle,
}: {
  candidate: PrepublishCandidate;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white">
      <button type="button" onClick={onToggle} className="flex w-full items-start gap-3 px-4 py-3 text-left">
        {open ? <ChevronDown size={16} className="mt-0.5 text-slate-400" /> : <ChevronRight size={16} className="mt-0.5 text-slate-400" />}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-black text-slate-900">#{candidate.rank}</span>
            <span className="text-xs font-black text-slate-900">{candidate.templateName || candidate.templateId}</span>
            {candidate.isCurrentDraft && <span className="rounded-full bg-indigo-100 px-2 py-0.5 text-[9px] font-black uppercase text-indigo-700">Draft ปัจจุบัน</span>}
            <DraftStatusPill passed={candidate.finalPassed} label={candidate.decision || (candidate.finalPassed ? "PASS" : "REVIEW")} />
          </div>
          <div className="mt-2 grid gap-2 text-[10px] font-bold text-slate-500 sm:grid-cols-3 xl:grid-cols-6">
            <span>Layout {formatPrepublishScore(candidate.globalScore)}</span>
            <span>Image {formatPrepublishScore(candidate.imageAnchorScore)}</span>
            <span>Text {formatPrepublishScore(candidate.textAnchorScore)}</span>
            <span>Verification {formatPrepublishScore(candidate.verificationScore)}</span>
            <span>คะแนนรวม {formatPrepublishScore(candidate.finalScore)}</span>
          </div>
        </div>
      </button>
      {open && (
        <div className="border-t border-slate-100 p-4">
          {candidate.verificationDetails && candidate.verificationDetails.length > 0 && (
            <div className="mt-4 rounded-xl bg-slate-50 p-3">
              <h4 className="text-[10px] font-black uppercase tracking-wider text-slate-500">Verification Checklist</h4>
              <div className="mt-2 overflow-x-auto rounded-lg border border-slate-200 bg-white">
                <table className="min-w-full divide-y divide-slate-100 text-[11px]">
                  <thead className="bg-slate-50 text-[9px] font-black uppercase tracking-wider text-slate-500">
                    <tr>
                      <th className="px-3 py-2 text-left">Anchor</th>
                      <th className="px-3 py-2 text-left">ประเภท</th>
                      <th className="px-3 py-2 text-left">คะแนน</th>
                      <th className="px-3 py-2 text-left">ผลการตรวจสอบ</th>
                      <th className="px-3 py-2 text-left">ค่าที่กำหนด</th>
                      <th className="px-3 py-2 text-left">ค่าที่ตรวจพบ</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {candidate.verificationDetails.map((detail, index) => {
                      const required = Boolean(readPrepublishValue(detail, ["required", "required_for_verification"]));
                      const passed = Boolean(readPrepublishValue(detail, ["passed", "final_passed"]));
                      return (
                        <tr key={`${candidate.templateId}-check-${index}`} className={required && !passed ? "bg-red-50" : undefined}>
                          <td className="px-3 py-2 font-black text-slate-900">
                            {String(readPrepublishValue(detail, ["field_name", "anchor_name", "name", "display_label"]) || `Anchor ${index + 1}`)}
                          </td>
                          <td className="px-3 py-2 font-semibold text-slate-600">
                            {String(readPrepublishValue(detail, ["anchor_type", "verification_method", "match_type"]) || "verification")}
                          </td>
                          <td className="px-3 py-2 font-black text-slate-900">
                            {formatPrepublishScore(readVerificationRecordScore(detail))}
                          </td>
                          <td className="px-3 py-2">
                            <DraftStatusPill passed={passed} label={passed ? "PASS" : "FAIL"} />
                          </td>
                          <td className="max-w-[180px] truncate px-3 py-2 font-semibold text-slate-600">
                            {String(readPrepublishValue(detail, ["expected_text"]) || "N/A")}
                          </td>
                          <td className="max-w-[180px] truncate px-3 py-2 font-semibold text-slate-600">
                            {String(readPrepublishValue(detail, ["actual_text", "ocr_text"]) || "N/A")}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {candidate.verificationDetails.some((detail) => Boolean(readPrepublishValue(detail, ["required", "required_for_verification"])) && !Boolean(readPrepublishValue(detail, ["passed", "final_passed"]))) && (
                <p className="mt-3 rounded-lg bg-red-50 p-3 text-xs font-bold text-red-700">
                  Candidate นี้ถูกปฏิเสธด้วย required_verification_failed เพราะมี Required Verification Anchor อย่างน้อย 1 รายการที่ FAIL.
                </p>
              )}
              <h4 className="mt-4 text-[10px] font-black uppercase tracking-wider text-slate-500">ROI Preview</h4>
              <div className="mt-2 grid gap-2 md:grid-cols-2">
                {candidate.verificationDetails.map((detail, index) => (
                  <div key={`${candidate.templateId}-detail-${index}`} className="rounded-lg bg-white p-3 text-xs font-semibold text-slate-600">
                    <div className="font-black text-slate-900">
                      {String(readPrepublishValue(detail, ["field_name", "anchor_name", "name", "display_label"]) || `Detail ${index + 1}`)}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-2 text-[10px]">
                      <span>{String(readPrepublishValue(detail, ["anchor_type", "verification_method", "match_type"]) || "verification")}</span>
                      <span>Score {formatPrepublishScore(readVerificationRecordScore(detail))}</span>
                      <span>Weight {String(readPrepublishValue(detail, ["weight", "verification_weight"]) || "N/A")}</span>
                      <span>{String(readPrepublishValue(detail, ["status", "decision", "failure_reason"]) || "N/A")}</span>
                    </div>
                    {(readPrepublishValue(detail, ["expected_text"]) || readPrepublishValue(detail, ["actual_text", "ocr_text"])) && (
                      <div className="mt-2 grid gap-2 text-[10px] md:grid-cols-2">
                        <p className="rounded bg-slate-50 p-2">ค่าที่กำหนด: {String(readPrepublishValue(detail, ["expected_text"]) || "N/A")}</p>
                        <p className="rounded bg-slate-50 p-2">ค่าที่ตรวจพบ: {String(readPrepublishValue(detail, ["actual_text", "ocr_text"]) || "N/A")}</p>
                      </div>
                    )}
                    {readPrepublishValue(detail, ["current_crop_preview_data_url", "current_crop_preview_url"]) && (
                      <div className="mt-3">
                        <div className="rounded border border-slate-100 bg-slate-50 p-2">
                          <div className="text-[9px] font-black uppercase text-slate-400">ทดสอบ ROI</div>
                          <img
                            src={String(readPrepublishValue(detail, ["current_crop_preview_data_url", "current_crop_preview_url"]))}
                            alt=""
                            className="mt-2 h-28 w-full rounded object-contain"
                          />
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function AdminTemplateTestPage({ templateId }: { templateId: string }) {
  const [template, setTemplate] = useState<Template | null>(null);
  const [pages, setPages] = useState<TemplatePage[]>([]);
  const [fields, setFields] = useState<TemplateField[]>([]);
  const [currentPage, setCurrentPage] = useState(0);
  const [selectedFieldId, setSelectedFieldId] = useState<string | null>(null);
  const [imageMetrics, setImageMetrics] = useState<WorkspaceImageMetrics>(DEFAULT_WORKSPACE_IMAGE_METRICS);
  const [loadStatus, setLoadStatus] = useState<"loading" | "loaded" | "error">("loading");
  const [ocrResults, setOcrResults] = useState<OcrPreviewResult[]>([]);
  const [anchorPreviewResults, setAnchorPreviewResults] = useState<OcrPreviewResult[]>([]);
  const [ocrStatus, setOcrStatus] = useState("");
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [simulation, setSimulation] = useState<PrepublishSimulationResult | null>(null);
  const [simulationAction, setSimulationAction] = useState<"run" | "confirm" | null>(null);
  const [simulationError, setSimulationError] = useState("");
  const [publishConfirmed, setPublishConfirmed] = useState(false);
  const [showPublishSuccessDialog, setShowPublishSuccessDialog] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [validationStep, setValidationStep] = useState(1);
  const [stepOneConfirmed, setStepOneConfirmed] = useState(false);
  const [testDocumentFile, setTestDocumentFile] = useState<File | null>(null);
  const [testDocumentPreviewUrl, setTestDocumentPreviewUrl] = useState<string | null>(null);
  const [detectionTest, setDetectionTest] = useState<PrepublishDetectionTestResult | null>(null);
  const [detectionTestAction, setDetectionTestAction] = useState(false);
  const [detectionTestError, setDetectionTestError] = useState("");
  const [expandedDetectionCandidates, setExpandedDetectionCandidates] = useState<Record<string, boolean>>({});
  const autoSimulationStartedRef = useRef(false);

  const applyTemplateBundle = (bundle: Awaited<ReturnType<typeof fetchTemplateBundle>>, scope: string) => {
    const nextPages = Array.isArray(bundle?.pages) ? bundle.pages : [];
    const nextFields = Array.isArray(bundle?.fields) ? bundle.fields : [];
    if (process.env.NODE_ENV === "development") {
      console.debug("[prepublish:pages]", scope, {
        hasTemplate: Boolean(bundle?.template),
        pagesCount: nextPages.length,
        fieldsCount: nextFields.length,
      });
    }
    setTemplate(bundle.template);
    setPages(nextPages);
    setFields(nextFields);
  };

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoadStatus("loading");
      try {
        const bundle = await fetchTemplateBundle(templateId);
        if (cancelled) return;
        applyTemplateBundle(bundle, "initial-load");
        setStepOneConfirmed(false);
        autoSimulationStartedRef.current = false;
        setLoadStatus("loaded");
      } catch (error) {
        console.warn("Template pre-publish load failed.", error);
        if (cancelled) return;
        setTemplate(null);
        setPages([]);
        setFields([]);
        setStepOneConfirmed(false);
        autoSimulationStartedRef.current = false;
        setLoadStatus("error");
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [templateId]);

  useEffect(() => {
    return () => {
      if (testDocumentPreviewUrl) URL.revokeObjectURL(testDocumentPreviewUrl);
    };
  }, [testDocumentPreviewUrl]);

  const safePages = pages.length > 0 ? pages : [{ id: "empty", templateId, pageNumber: 1, sampleImageUrl: samplePage, similarityThreshold: 0.75, finalConfidenceThreshold: DEFAULT_FINAL_CONFIDENCE_THRESHOLD }];
  const safeCurrentPage = Math.min(currentPage, Math.max(safePages.length - 1, 0));
  const currentPageNumber = safePages[safeCurrentPage]?.pageNumber || safeCurrentPage + 1;
  const currentPageImage = safePages[safeCurrentPage]?.normalizedImageUrl || safePages[safeCurrentPage]?.sampleImageUrl || samplePage;
  const extractionFields = fields.filter((field) => !field.useForVerification);
  const verificationAnchors = fields.filter((field) => field.useForVerification);
  const textAnchors = verificationAnchors.filter((field) => field.dataType !== "image");
  const imageAnchors = verificationAnchors.filter((field) => field.dataType === "image");
  const currentPageFields = extractionFields.filter((field) => field.pageNumber === currentPageNumber);
  const currentPageAnchors = verificationAnchors.filter((field) => field.pageNumber === currentPageNumber);
  const selectedField = selectedFieldId ? fields.find((field) => field.id === selectedFieldId) : null;
  const selectedRoiId = selectedFieldId
    ? stableNumericId(`${selectedField?.useForVerification ? "anchor" : "field"}:${selectedFieldId}`)
    : null;
  const rois = useMemo(() => fields.map((field) => fieldToRoi(field, imageMetrics)), [fields, imageMetrics]);
  const resultsByPage = ocrResults.reduce<Record<number, OcrPreviewResult[]>>((acc, result) => {
    acc[result.pageNumber] = [...(acc[result.pageNumber] || []), result];
    return acc;
  }, {});
  const anchorPreviewsByPage = anchorPreviewResults.reduce<Record<number, OcrPreviewResult[]>>((acc, result) => {
    acc[result.pageNumber] = [...(acc[result.pageNumber] || []), result];
    return acc;
  }, {});
  const ocrPreviewPassed = Boolean(extractionFields.length > 0 || verificationAnchors.length > 0);
  const finalConfidenceThreshold = typeof template?.finalConfidenceThreshold === "number" && Number.isFinite(template.finalConfidenceThreshold)
    ? template.finalConfidenceThreshold
    : DEFAULT_FINAL_CONFIDENCE_THRESHOLD;
  const matchingWeights = useMemo(() => {
    const configured = readMatchingWeights({
      layoutWeight: template?.layoutWeight,
      textAnchorWeight: template?.textAnchorWeight,
      imageAnchorWeight: template?.imageAnchorWeight,
    });
    return calculateMatchingWeights({
      layoutWeight: configured.layoutWeight,
      textAnchorCount: textAnchors.length,
      imageAnchorCount: imageAnchors.length,
      preferredTextWeight: configured.textAnchorWeight,
    });
  }, [imageAnchors.length, template?.imageAnchorWeight, template?.layoutWeight, template?.textAnchorWeight, textAnchors.length]);
  const effectiveMatchingWeights = matchingWeights;
  const rawLayoutSignaturePages: PrepublishLayoutSignaturePage[] =
    simulation?.layoutSignaturePages?.length
      ? simulation.layoutSignaturePages
      : simulation?.temporaryEmbedding?.layoutSignaturePages?.length
        ? simulation.temporaryEmbedding?.layoutSignaturePages || []
        : safePages.map((page) => ({
            templatePageId: page.id,
            templateLayoutReferenceId: null,
            pageNumber: page.pageNumber,
            status: simulationAction === "run" ? "running" : "pending",
            engine: "layout_signature",
            version: null,
            modelName: null,
            labelCount: null,
            imageUrl: page.normalizedImageUrl || page.sampleImageUrl || samplePage,
            imageSource: "template_page",
            isCanonical: true,
            referenceRole: "main",
            persisted: false,
            reason: null,
          }));
  const layoutSignaturePages: PrepublishLayoutSignaturePage[] = rawLayoutSignaturePages.map((signaturePage) => {
    const templatePage = safePages.find(
      (page) => page.id === signaturePage.templatePageId || page.pageNumber === signaturePage.pageNumber
    );
    return {
      ...signaturePage,
      imageUrl: signaturePage.imageUrl || templatePage?.normalizedImageUrl || templatePage?.sampleImageUrl || null,
    };
  });
  const generatedLayoutReferenceCount = layoutSignaturePages.filter((page) => String(page.status || "").toLowerCase() === "generated").length;
  const layoutReferencesGenerated = layoutSignaturePages.length > 0 && generatedLayoutReferenceCount === layoutSignaturePages.length;
  const simulationPassed = Boolean(simulation?.separationAnalysis?.simulationPassed);
  const stepTwoCompleted = Boolean(simulationPassed && layoutReferencesGenerated);
  const detectionTestPassed = Boolean(detectionTest?.passed && detectionTest.draftTemplateRank === 1);
  const publishPrerequisitesMet = Boolean(stepTwoCompleted && detectionTestPassed);
  const overallReady = publishPrerequisitesMet;
  const canRunDetectionTest = Boolean(stepTwoCompleted && simulationAction === null && testDocumentFile && !detectionTestAction);
  const canConfirmPublish = publishPrerequisitesMet && simulationAction === null && template?.status !== "active";
  const validationSteps = [
    { step: 1, label: "ตรวจสอบ ROI และ OCR", enabled: true, done: ocrPreviewPassed },
    { step: 2, label: "สร้างข้อมูลอ้างอิง Template", enabled: stepOneConfirmed, done: stepTwoCompleted },
    { step: 3, label: "ทดสอบเอกสารใหม่", enabled: stepTwoCompleted, done: Boolean(detectionTest) },
    { step: 4, label: "ตรวจสอบก่อนเผยแพร่", enabled: Boolean(detectionTest), done: overallReady },
  ];
  const goToValidationStep = (step: number) => {
    const target = validationSteps.find((item) => item.step === step);
    if (!target?.enabled) return;
    setValidationStep(step);
  };

  const runPreviewOcr = async () => {
    setIsPreviewing(true);
    setOcrStatus("Running OCR on extraction fields...");
    setOcrResults([]);
    setAnchorPreviewResults([]);

    try {
      const nextResults: OcrPreviewResult[] = [];
      for (const field of extractionFields) {
        const page = safePages.find((item) => item.pageNumber === field.pageNumber);
        const imageSrc = page?.normalizedImageUrl || page?.sampleImageUrl || samplePage;
        const roiPreviewUrl = await cropFieldPreview(imageSrc, field);

        if (field.extractionMethod === "extract_image") {
          nextResults.push({
            id: field.id,
            pageNumber: field.pageNumber,
            fieldName: field.fieldName,
            displayLabel: field.displayLabel,
            extractionMethod: field.extractionMethod,
            ocrText: "(image crop ready)",
            roiPreviewUrl: roiPreviewUrl || undefined,
            passed: Boolean(roiPreviewUrl),
          });
          continue;
        }

        const response = await fetch(`${ADMIN_API_BASE_URL}/api/ai/process`, {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({
            image: roiPreviewUrl,
            rois: [{ fieldName: field.fieldName, x: 0, y: 0, width: 9999, height: 9999 }],
          }),
        });
        const json = await response.json();
        const result = json?.extracted_data?.[0];
        const ocrText = result?.text || "";
        nextResults.push({
          id: field.id,
          pageNumber: field.pageNumber,
          fieldName: field.fieldName,
          displayLabel: field.displayLabel,
          extractionMethod: field.extractionMethod,
          ocrText,
          confidence: typeof result?.confidence === "number" ? result.confidence : undefined,
          roiPreviewUrl: roiPreviewUrl || undefined,
          passed: ocrText.trim().length > 0,
        });
      }

      setOcrResults(nextResults);
      const nextAnchorPreviews: OcrPreviewResult[] = [];
      for (const anchor of verificationAnchors) {
        const page = safePages.find((item) => item.pageNumber === anchor.pageNumber);
        const imageSrc = page?.normalizedImageUrl || page?.sampleImageUrl || samplePage;
        const roiPreviewUrl = await cropFieldPreview(imageSrc, anchor);
        const isImageAnchor = anchor.dataType === "image";
        nextAnchorPreviews.push({
          id: anchor.id,
          pageNumber: anchor.pageNumber,
          fieldName: anchor.fieldName,
          displayLabel: anchor.displayLabel,
          extractionMethod: isImageAnchor ? "image_feature" : "ocr_text",
          ocrText: isImageAnchor ? "(image anchor crop ready)" : anchor.expectedText || "",
          roiPreviewUrl: roiPreviewUrl || undefined,
          expectedText: anchor.expectedText || undefined,
          verificationStatus: isImageAnchor || anchor.expectedText?.trim() ? "pass" : "not_configured",
          passed: Boolean(roiPreviewUrl && (isImageAnchor || anchor.expectedText?.trim())),
        });
      }
      setAnchorPreviewResults(nextAnchorPreviews);
      setOcrStatus(`OCR preview complete for ${nextResults.length} extraction fields and ${nextAnchorPreviews.length} verification anchors.`);
    } catch (error) {
      console.error(error);
      setOcrStatus("OCR preview failed. Check the OCR backend and image data.");
    } finally {
      setIsPreviewing(false);
    }
  };

  const handleRunPrepublishSimulation = async () => {
    setSimulationAction("run");
    setSimulationError("");
    setStatusMessage("");
    setPublishConfirmed(false);
    try {
      const result = await runPrepublishSimulation(templateId);
      const refreshedBundle = await fetchTemplateBundle(templateId);
      applyTemplateBundle(refreshedBundle, "post-simulation-transition");
      setSimulation({ ...result, template: refreshedBundle.template });
      setStatusMessage("Temporary layout signature simulation completed. Review candidate ranking and readiness before publishing.");
    } catch (error) {
      console.warn("Pre-publish simulation failed.", error);
      setSimulationError(error instanceof Error ? error.message : "Pre-publish simulation failed.");
    } finally {
      setSimulationAction(null);
    }
  };

  const handleTestDocumentChange = (file: File | null) => {
    if (testDocumentPreviewUrl) URL.revokeObjectURL(testDocumentPreviewUrl);
    setTestDocumentFile(file);
    setDetectionTest(null);
    setDetectionTestError("");
    if (file && (file.type.startsWith("image/") || file.type === "application/pdf")) {
      setTestDocumentPreviewUrl(URL.createObjectURL(file));
    } else {
      setTestDocumentPreviewUrl(null);
    }
  };

  const handleRunDetectionTest = async () => {
    if (!testDocumentFile) return;
    setDetectionTestAction(true);
    setDetectionTestError("");
    setStatusMessage("");
    try {
      const result = await runPrepublishDetectionTest(templateId, testDocumentFile);
      setDetectionTest(result);
      setStatusMessage("การทดสอบตรวจจับเอกสารเสร็จสิ้น ตรวจสอบผลการจัดอันดับก่อนเผยแพร่");
    } catch (error) {
      console.warn("การทดสอบ Draft Template ด้วยเอกสารใหม่ไม่สำเร็จ", error);
      setDetectionTestError(error instanceof Error ? error.message : "ไม่สามารถทดสอบด้วยเอกสารใหม่ได้");
    } finally {
      setDetectionTestAction(false);
    }
  };

  const handleConfirmPublish = async () => {
    setSimulationAction("confirm");
    setSimulationError("");
    setStatusMessage("");
    try {
      const result = await confirmTemplatePublish(templateId);
      setTemplate(result.template);
      setPublishConfirmed(true);
      setShowPublishSuccessDialog(true);
      setStatusMessage("Publish สำเร็จ Template พร้อมใช้งานแล้ว");
    } catch (error) {
      console.warn("Template publish failed.", error);
      setSimulationError(error instanceof Error ? error.message : "Template publish failed.");
    } finally {
      setSimulationAction(null);
    }
  };

  const persistFinalConfidenceThreshold = async () => {
    if (!template) return;
    const nextThreshold = Math.max(0, Math.min(1, finalConfidenceThreshold));
    setStatusMessage("");
    setSimulationError("");
    try {
      const bundle = await updateTemplateApi(templateId, { finalConfidenceThreshold: nextThreshold });
      applyTemplateBundle(bundle, "final-threshold-save");
      setStatusMessage("Final confidence threshold saved.");
    } catch (error) {
      console.warn("Final confidence threshold save failed.", error);
      setSimulationError(error instanceof Error ? error.message : "Final confidence threshold save failed.");
    }
  };

  const updateMatchingWeightsDraft = (weights: typeof DEFAULT_MATCHING_WEIGHTS) => {
    setTemplate((current) => current ? { ...current, ...weights } : current);
  };

  const updateLayoutWeightDraft = (value: number) => {
    updateMatchingWeightsDraft(calculateMatchingWeights({
      layoutWeight: Number.isFinite(value) ? value : DEFAULT_MATCHING_WEIGHTS.layoutWeight,
      textAnchorCount: textAnchors.length,
      imageAnchorCount: imageAnchors.length,
    }));
  };

  const updateTextWeightDraft = (value: number) => {
    updateMatchingWeightsDraft(calculateMatchingWeights({
      layoutWeight: matchingWeights.layoutWeight,
      textAnchorCount: textAnchors.length,
      imageAnchorCount: imageAnchors.length,
      preferredTextWeight: value,
    }));
  };

  const updateImageWeightDraft = (value: number) => {
    const remaining = 1 - matchingWeights.layoutWeight;
    updateMatchingWeightsDraft(calculateMatchingWeights({
      layoutWeight: matchingWeights.layoutWeight,
      textAnchorCount: textAnchors.length,
      imageAnchorCount: imageAnchors.length,
      preferredTextWeight: Math.max(0, remaining - value),
    }));
  };

  const persistMatchingWeights = async (weights = matchingWeights) => {
    if (!template) return;
    const configured = calculateMatchingWeights({
      layoutWeight: weights.layoutWeight,
      textAnchorCount: textAnchors.length,
      imageAnchorCount: imageAnchors.length,
      preferredTextWeight: weights.textAnchorWeight,
    });
    const nextWeights = configured;
    setStatusMessage("");
    setSimulationError("");
    try {
      const bundle = await updateTemplateApi(templateId, nextWeights);
      applyTemplateBundle(bundle, "matching-weights-save");
      setStatusMessage("Matching weights saved.");
    } catch (error) {
      console.warn("Matching weights save failed.", error);
      setSimulationError(error instanceof Error ? error.message : "Matching weights save failed.");
    }
  };

  const applyRecommendedMatchingWeights = async () => {
    const recommended = calculateMatchingWeights({
      layoutWeight: DEFAULT_MATCHING_WEIGHTS.layoutWeight,
      textAnchorCount: textAnchors.length,
      imageAnchorCount: imageAnchors.length,
    });
    updateMatchingWeightsDraft(recommended);
    await persistMatchingWeights(recommended);
  };

  useEffect(() => {
    if (validationStep !== 2 || autoSimulationStartedRef.current || simulation || simulationAction !== null || !ocrPreviewPassed || !stepOneConfirmed) return;
    autoSimulationStartedRef.current = true;
    void handleRunPrepublishSimulation();
  }, [ocrPreviewPassed, simulation, simulationAction, stepOneConfirmed, validationStep]);

  useEffect(() => {
    if (validationStep === 2 && stepTwoCompleted && simulationAction === null) {
      setValidationStep(3);
    }
  }, [simulationAction, stepTwoCompleted, validationStep]);

  if (loadStatus === "loading") {
    return <section className="rounded-xl border border-slate-200 bg-white p-6 text-sm font-semibold text-slate-500 shadow-sm">Loading draft validation...</section>;
  }

  if (!template) {
    return (
      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-lg font-black text-slate-900">Template not found</h2>
        <Link href="/admin/templates" className="mt-4 inline-flex rounded-xl bg-indigo-600 px-4 py-2 text-xs font-black text-white">
          Back to Templates
        </Link>
      </section>
    );
  }

  return (
    <section className="space-y-4">
      <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h2 className="text-lg font-black text-slate-900">ตรวจสอบ Template ก่อนเผยแพร่</h2>
            <p className="mt-1 text-xs font-semibold text-slate-500">
              ทดสอบ Template ฉบับร่างก่อนเผยแพร่ โดยไม่กระทบกับ Template ที่เผยแพร่แล้ว
            </p>
            {statusMessage && <p className="mt-2 text-xs font-bold text-emerald-600">{statusMessage}</p>}
            {simulationError && <p className="mt-2 text-xs font-bold text-red-600">{simulationError}</p>}
          </div>
          <Link href={`/admin/templates/${templateId}/edit`} className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-black text-slate-700">
            ย้อนกลับไปแก้ไข Template
          </Link>
        </div>
        <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {validationSteps.map(({ step, label, enabled, done }) => (
            <button
              key={step}
              type="button"
              onClick={() => goToValidationStep(step)}
              disabled={!enabled}
              className={`rounded-xl border px-3 py-2 text-left text-[11px] font-black transition-colors ${
                validationStep === step
                  ? "border-indigo-500 bg-indigo-600 text-white"
                  : enabled
                    ? done
                      ? "border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-white"
                      : "border-slate-200 bg-slate-50 text-slate-600 hover:bg-white"
                    : "cursor-not-allowed border-slate-200 bg-slate-100 text-slate-400"
              }`}
            >
              <span className="block text-[9px] uppercase opacity-75">ขั้นตอนที่ {step}</span>
              <span className="block">{label}</span>
              <span className="mt-1 block text-[9px] uppercase opacity-70">{done ? "Done" : enabled ? "Ready" : "Locked"}</span>
            </button>
          ))}
        </div>
      </div>

      {validationStep === 1 && (
      <>
      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <DraftSectionHeader title="Draft Template Summary" subtitle="ตรวจสอบข้อมูลหลักของ Draft Template ก่อนทดสอบ OCR และ Simulation." />
          <span className="w-fit rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-[10px] font-black uppercase tracking-wide text-slate-700">
            {template.status}
          </span>
        </div>
        <div className="mt-4 rounded-xl border border-slate-100 bg-white p-3">
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            <DraftOverviewMetric label="Template Name" value={template.name} />
            <DraftOverviewMetric label="Status" value={simulation?.draftSummary?.status || template.status} tone="emerald" />
            <DraftOverviewMetric label="Pages" value={simulation?.draftSummary?.pageCount ?? safePages.length} />
            <DraftOverviewMetric label="Extraction Fields" value={simulation?.draftSummary?.extractionFieldCount ?? extractionFields.length} tone="indigo" />
            <DraftOverviewMetric label="Text Anchors" value={simulation?.draftSummary?.textAnchorCount ?? textAnchors.length} tone="orange" />
            <DraftOverviewMetric label="Image Anchors" value={simulation?.draftSummary?.imageAnchorCount ?? imageAnchors.length} tone="orange" />
          </div>
        </div>
        <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <h4 className="text-[11px] font-black uppercase tracking-wider text-slate-700">การตั้งค่าการตัดสินใจ</h4>
              <p className="mt-1 text-[11px] font-semibold text-slate-500">
                ใช้เป็นเกณฑ์ตัดสิน Final Score ในขั้น New Document Test และตอน Publish
              </p>
            </div>
            <label className="block w-full max-w-xs space-y-1">
              <span className="text-[10px] font-black uppercase tracking-wider text-slate-500">เกณฑ์คะแนนรวม</span>
              <input
                type="number"
                min="0"
                max="1"
                step="0.01"
                value={finalConfidenceThreshold}
                onChange={(event) => {
                  const value = Number(event.target.value);
                  setTemplate((current) => current ? { ...current, finalConfidenceThreshold: Number.isFinite(value) ? value : DEFAULT_FINAL_CONFIDENCE_THRESHOLD } : current);
                }}
                onBlur={persistFinalConfidenceThreshold}
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-black text-slate-800"
              />
              <span className="block text-[10px] font-semibold text-slate-500">ค่าเริ่มต้นที่แนะนำ: 0.75</span>
            </label>
          </div>
        </div>
        <MatchingWeightsPanel
          matchingWeights={matchingWeights}
          effectiveMatchingWeights={effectiveMatchingWeights}
          textAnchorCount={textAnchors.length}
          imageAnchorCount={imageAnchors.length}
          onLayoutChange={updateLayoutWeightDraft}
          onTextChange={updateTextWeightDraft}
          onImageChange={updateImageWeightDraft}
          onWeightBlur={() => persistMatchingWeights()}
          onUseRecommended={applyRecommendedMatchingWeights}
        />
        <div className="mt-4 flex justify-end">
          <button
            type="button"
            onClick={() => {
              autoSimulationStartedRef.current = false;
              setSimulation(null);
              setSimulationError("");
              setStepOneConfirmed(true);
              setValidationStep(2);
            }}
            disabled={!ocrPreviewPassed}
            className="ui-stable-action-lg rounded-xl bg-indigo-600 px-4 py-2 text-xs font-black text-white shadow-sm disabled:bg-slate-300 disabled:text-slate-500"
          >
            ยืนยัน
          </button>
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <DraftSectionHeader title="ตัวอย่าง ROI" subtitle="แสดงตำแหน่ง ROI และข้อมูล Preview จาก Template ปัจจุบัน" />
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {safePages.map((page, index) => (
            <button
              key={page.id}
              type="button"
              onClick={() => setCurrentPage(index)}
              className={`rounded-lg px-3 py-1.5 text-[10px] font-black ${safeCurrentPage === index ? "bg-indigo-600 text-white" : "border border-slate-200 bg-white text-slate-600"}`}
            >
              Page {page.pageNumber}
            </button>
          ))}
        </div>
        <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
          <WorkspaceCanvas imageSrc={currentPageImage} className="h-[560px]" onImageMetricsChange={setImageMetrics}>
            <RoiLayer
              rois={rois}
              currentPage={safeCurrentPage}
              selectedId={selectedRoiId}
              readonly
              showLabels
              onSelect={(id) => {
                const field = fields.find((item) => stableNumericId(`${item.useForVerification ? "anchor" : "field"}:${item.id}`) === id);
                if (field) setSelectedFieldId(field.id);
              }}
            />
          </WorkspaceCanvas>
          <aside className="space-y-3">
            <div className="rounded-xl border border-indigo-200 bg-indigo-50/70 p-3">
              <div className="flex items-center justify-between gap-2">
                <h4 className="text-xs font-black text-indigo-950">Page {currentPageNumber} Extraction Fields</h4>
              </div>
              <div className="mt-3 max-h-64 space-y-2 overflow-y-auto pr-1">
                {currentPageFields.length === 0 ? (
                  <p className="rounded-lg bg-white p-3 text-xs font-semibold text-indigo-500">No extraction fields on this page.</p>
                ) : (
                  currentPageFields.map((field) => (
                    <button
                      key={field.id}
                      type="button"
                      onClick={() => setSelectedFieldId(field.id)}
                      className={`w-full rounded-lg border p-3 text-left text-xs transition-colors ${
                        selectedFieldId === field.id
                          ? "border-indigo-500 bg-white text-indigo-950 shadow-sm ring-2 ring-indigo-200"
                          : "border-indigo-100 bg-white/85 text-indigo-900 hover:border-indigo-300"
                      }`}
                    >
                      <div className="font-black">{field.displayLabel}</div>
                      <div className="mt-1 text-[10px] font-bold text-indigo-500">{field.fieldName}</div>
                      <div className="mt-2 w-fit rounded-full bg-indigo-100 px-2 py-0.5 text-[9px] font-black uppercase text-indigo-700">{field.extractionMethod}</div>
                    </button>
                  ))
                )}
              </div>
            </div>
            <div className="rounded-xl border border-orange-200 bg-orange-50/80 p-3">
              <div className="flex items-center justify-between gap-2">
                <h4 className="text-xs font-black text-orange-950">Page {currentPageNumber} Verification Anchors</h4>
              </div>
              <div className="mt-3 space-y-2">
                {currentPageAnchors.length === 0 ? (
                  <p className="text-xs font-semibold text-orange-700">No anchors on this page.</p>
                ) : (
                  currentPageAnchors.map((anchor) => (
                    <button
                      key={anchor.id}
                      type="button"
                      onClick={() => setSelectedFieldId(anchor.id)}
                      className={`w-full rounded-lg border p-3 text-left text-xs transition-colors ${
                        selectedFieldId === anchor.id
                          ? "border-orange-500 bg-white text-orange-950 shadow-sm ring-2 ring-orange-200"
                          : "border-orange-100 bg-white/80 text-orange-900 hover:border-orange-300"
                      }`}
                    >
                      <div className="font-black">{anchor.displayLabel}</div>
                      <div className="mt-2 w-fit rounded-full bg-orange-100 px-2 py-0.5 text-[9px] font-black uppercase text-orange-700">
                        {anchor.dataType === "image" ? "Image Anchor" : "Text Anchor"}
                      </div>
                    </button>
                  ))
                )}
              </div>
            </div>
          </aside>
        </div>
        {false && ocrStatus && <p className="mt-3 text-xs font-bold text-slate-600">{ocrStatus}</p>}
        {false && (ocrResults.length > 0 || anchorPreviewResults.length > 0) && (
          <div className="mt-4 rounded-xl border border-orange-200 bg-orange-50/70 p-3">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h4 className="text-xs font-black text-orange-950">Verification Anchors Preview</h4>
                <p className="mt-1 text-[11px] font-semibold text-orange-700">
                  แสดง ROI ที่ใช้ยืนยัน Template เท่านั้น ไม่ใช่ข้อมูลที่จะส่งออกให้ผู้ใช้
                </p>
              </div>
              <span className="w-fit rounded-full bg-orange-600 px-2.5 py-1 text-[10px] font-black uppercase text-white">
                {verificationAnchors.length} Anchors
              </span>
            </div>
            <div className="mt-3 space-y-3">
              {verificationAnchors.length === 0 ? (
                <p className="rounded-lg bg-white p-3 text-xs font-semibold text-orange-600">ยังไม่มี Verification Anchors สำหรับ Template นี้</p>
              ) : (
                safePages.map((page) => {
                  const pageAnchors = verificationAnchors.filter((anchor) => anchor.pageNumber === page.pageNumber);
                  const pagePreviews = anchorPreviewsByPage[page.pageNumber] || [];
                  if (pageAnchors.length === 0) return null;
                  return (
                    <div key={`anchor-preview-${page.id}`} className="rounded-xl border border-orange-100 bg-white p-3">
                      <h5 className="text-[11px] font-black uppercase tracking-wider text-orange-800">Page {page.pageNumber}</h5>
                      <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                        {pageAnchors.map((anchor) => {
                          const preview = pagePreviews.find((item) => item.id === anchor.id);
                          const isImageAnchor = anchor.dataType === "image";
                          const anchorPassed = Boolean(preview?.passed);
                          const imageCategories = Array.isArray(anchor.imageCategory)
                            ? anchor.imageCategory.filter(Boolean)
                            : anchor.imageCategory
                              ? [anchor.imageCategory]
                              : [];
                          return (
                            <button
                              key={anchor.id}
                              type="button"
                              onClick={() => setSelectedFieldId(anchor.id)}
                              className={`rounded-xl border p-3 text-left text-xs transition-colors ${
                                selectedFieldId === anchor.id
                                  ? "border-orange-500 bg-orange-50 text-orange-950 ring-2 ring-orange-200"
                                  : "border-orange-100 bg-white text-slate-800 hover:border-orange-300"
                              }`}
                            >
                              <div className="flex gap-3">
                                {preview?.roiPreviewUrl ? (
                                  <img src={preview.roiPreviewUrl} alt="" className="h-16 w-24 rounded-lg border border-orange-100 bg-white object-contain" />
                                ) : (
                                  <div className="flex h-16 w-24 items-center justify-center rounded-lg border border-dashed border-orange-200 bg-orange-50 text-[10px] font-bold text-orange-400">
                                    No preview
                                  </div>
                                )}
                                <div className="min-w-0 flex-1">
                                  <div className="flex items-start justify-between gap-2">
                                    <div className="min-w-0">
                                      <div className="truncate font-black text-slate-900">{anchor.displayLabel}</div>
                                      <div className="mt-0.5 text-[10px] font-bold text-slate-500">{anchor.fieldName}</div>
                                    </div>
                                    <DraftStatusPill passed={anchorPassed} label={anchorPassed ? "PASS" : "FAIL"} />
                                  </div>
                                  <div className="mt-2 flex flex-wrap gap-1">
                                    <span className="rounded-full bg-orange-100 px-2 py-0.5 text-[9px] font-black uppercase text-orange-700">
                                      Type: {isImageAnchor ? "Image" : "Text"}
                                    </span>
                                    {isImageAnchor && imageCategories.length > 0 && (
                                      <span className="rounded-full bg-indigo-100 px-2 py-0.5 text-[9px] font-black uppercase text-indigo-700">
                                        Category: {imageCategories.join(", ")}
                                      </span>
                                    )}
                                    {!isImageAnchor && (
                                      <span className="rounded-full bg-sky-100 px-2 py-0.5 text-[9px] font-black uppercase text-sky-700">
                                        Method: OCR Text
                                      </span>
                                    )}
                                    <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[9px] font-black uppercase text-slate-600">
                                      Weight {anchor.verificationWeight ?? 1}
                                    </span>
                                    {anchor.requiredForVerification && (
                                      <span className="rounded-full bg-red-100 px-2 py-0.5 text-[9px] font-black uppercase text-red-700">Required</span>
                                    )}
                                  </div>
                                </div>
                              </div>
                              {!isImageAnchor && (
                                <div className="mt-3 rounded-lg bg-orange-50 p-2 text-[11px] font-semibold text-orange-900">
                                  Expected: {anchor.expectedText || "ยังไม่ได้กำหนด Expected Text"}
                                </div>
                              )}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}
        <div className="mt-4 space-y-4">
          {ocrResults.length === 0 ? (
            <p className="rounded-xl bg-slate-50 p-4 text-xs font-semibold text-slate-500">ขั้นนี้เป็นการตรวจ Preview ตำแหน่ง ROI เท่านั้น ระบบจะไม่รัน OCR ใหม่</p>
          ) : (
            Object.entries(resultsByPage).map(([pageNumber, pageResults]) => (
              <div key={pageNumber} className="rounded-xl border border-slate-200 p-3">
                <h4 className="text-xs font-black text-slate-800">Page {pageNumber}</h4>
                <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {pageResults.map((result) => (
                    <div key={result.id} className="rounded-xl bg-slate-50 p-3 text-xs">
                      <div className="flex gap-3">
                        {result.roiPreviewUrl && <img src={result.roiPreviewUrl} alt="" className="h-16 w-24 rounded-lg border border-slate-200 bg-white object-contain" />}
                        <div className="min-w-0 flex-1">
                          <div className="font-black text-slate-900">{result.displayLabel}</div>
                          <div className="mt-0.5 text-[10px] font-bold text-slate-500">{result.fieldName}</div>
                          <div className="mt-1 flex flex-wrap gap-1">
                            <DraftStatusPill passed={Boolean(result.passed)} />
                            <span className="rounded-full bg-slate-200 px-2 py-1 text-[10px] font-black uppercase text-slate-600">
                              Confidence {result.confidence !== undefined ? result.confidence.toFixed(2) : "N/A"}
                            </span>
                          </div>
                        </div>
                      </div>
                      <div className="mt-3 rounded-lg bg-white p-2 font-semibold text-slate-700">{result.ocrText || "(empty)"}</div>
                    </div>
                  ))}
                </div>
              </div>
            ))
          )}
        </div>
      </section>
      </>
      )}

      {validationStep === 2 && (
      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <DraftSectionHeader
            title="สร้างข้อมูลอ้างอิง Template"
            subtitle="สร้างและทดสอบ Layout Signature จาก Main และ Reference ทั้งหมด หลังจาก ROI & OCR Preview ผ่านแล้วเท่านั้น"
          />
          <span className={`rounded-full px-3 py-1 text-[10px] font-black uppercase ${
            simulationAction === "run"
              ? "bg-indigo-100 text-indigo-700"
              : stepTwoCompleted
                ? "bg-emerald-100 text-emerald-700"
                : simulation
                  ? "bg-amber-100 text-amber-700"
                  : "bg-slate-200 text-slate-600"
          }`}>
            {simulationAction === "run" ? "Running" : stepTwoCompleted ? "Completed" : simulation ? "Incomplete" : "Waiting"}
          </span>
        </div>
        {!ocrPreviewPassed && (
          <p className="mt-4 rounded-xl bg-amber-50 p-3 text-xs font-bold text-amber-700">
            ต้อง Preview OCR ใน Step 1 ให้ผ่านก่อนจึงจะเริ่ม Simulation ได้
          </p>
        )}
        <div className="mt-4 rounded-xl border border-slate-100 bg-white p-3">
          {simulationAction === "run" && (
            <div className="mb-4 rounded-xl border border-indigo-100 bg-indigo-50 p-4">
              <div className="flex items-center gap-3">
                <Loader2 size={20} className="shrink-0 animate-spin text-indigo-700" />
                <div>
                  <div className="text-sm font-black text-indigo-950">กำลังสร้าง Layout Simulation</div>
                  <p className="mt-1 text-xs font-semibold text-indigo-700">
                    ระบบกำลังสร้าง Layout Signature ชั่วคราวและตรวจความพร้อมก่อนทดสอบเอกสารใหม่
                  </p>
                </div>
              </div>
            </div>
          )}
          <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h4 className="text-[11px] font-black uppercase tracking-wider text-slate-700">Layout Reference Images</h4>
              <p className="mt-1 text-[10px] font-semibold text-slate-500">
                ภาพแต่ละหน้าจะถูกแปลงเป็น Layout Signature ชั่วคราวเพื่อใช้ทดสอบก่อน Publish
              </p>
            </div>
            <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[10px] font-black uppercase text-slate-600">
              {generatedLayoutReferenceCount}/{layoutSignaturePages.length} generated
            </span>
          </div>
          <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {layoutSignaturePages.map((page) => {
              const status = page.status || "pending";
              const isGenerated = status === "generated";
              const isRunning = status === "running";
              const isFailed = status === "failed";
              const isReferenceOnly = page.referenceRole === "reference_only" || page.isCanonical === false;
              return (
                <div key={`${page.templateLayoutReferenceId || page.templatePageId || "page"}-${page.pageNumber}`} className="rounded-xl border border-slate-200 bg-slate-50 p-2.5">
                  <div className="flex items-start gap-3">
                    <div className="h-20 w-16 shrink-0 overflow-hidden rounded-lg border border-slate-200 bg-white">
                      {page.imageUrl ? (
                        <img src={page.imageUrl} alt={`Page ${page.pageNumber}`} className="h-full w-full object-contain" />
                      ) : (
                        <div className="flex h-full w-full items-center justify-center text-[10px] font-bold text-slate-400">No Image</div>
                      )}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2">
                        <div className="text-xs font-black text-slate-900">Page {page.pageNumber}</div>
                        <div className="flex shrink-0 flex-wrap justify-end gap-1">
                          <span
                            className={`rounded-full px-2 py-0.5 text-[9px] font-black uppercase ${
                              isReferenceOnly ? "bg-amber-100 text-amber-700" : "bg-emerald-100 text-emerald-700"
                            }`}
                          >
                            {isReferenceOnly ? "Reference Only" : "Main"}
                          </span>
                          <span
                            className={`rounded-full px-2 py-0.5 text-[9px] font-black uppercase ${
                              isGenerated
                                ? "bg-emerald-100 text-emerald-700"
                                : isRunning
                                  ? "bg-indigo-100 text-indigo-700"
                                  : isFailed
                                    ? "bg-red-100 text-red-700"
                                    : "bg-slate-200 text-slate-600"
                            }`}
                          >
                            {isGenerated ? "Generated" : isRunning ? "Running" : isFailed ? "Failed" : "Pending"}
                          </span>
                        </div>
                      </div>
                      <div className="mt-2 space-y-1 text-[10px] font-semibold text-slate-500">
                        {page.reason && <p className="text-red-600">Reason: {page.reason}</p>}
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
        {simulation?.temporaryEmbedding && (
          <div className="mt-4 rounded-xl border border-slate-100 bg-white p-3">
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
              <DraftOverviewMetric label="Generated" value={simulation.temporaryEmbedding?.generatedAt || "N/A"} />
            </div>
          </div>
        )}
      </section>
      )}

      {validationStep === 3 && (
      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <DraftSectionHeader
            title="ทดสอบการจับคู่ Template กับเอกสารใหม่"
            subtitle="อัปโหลดเอกสารใหม่เพื่อทดสอบว่า Draft Template นี้ถูกเลือกได้ถูกต้องก่อน Publish."
          />
          <button
            type="button"
            onClick={handleRunDetectionTest}
            disabled={!canRunDetectionTest}
            className="rounded-xl bg-indigo-600 px-4 py-2 text-xs font-black text-white shadow-sm disabled:bg-slate-300 disabled:text-slate-500"
          >
            {detectionTestAction ? "Running..." : "Run Detection Test"}
          </button>
        </div>

        <div className="mt-4 grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
          <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
            <label className="block text-[10px] font-black uppercase tracking-wider text-slate-700">เอกสารทดสอบ</label>
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp,application/pdf"
              onChange={(event) => handleTestDocumentChange(event.target.files?.[0] || null)}
              className="mt-3 block w-full text-[11px] font-semibold text-slate-600 file:mr-3 file:rounded-lg file:border-0 file:bg-indigo-600 file:px-3 file:py-2 file:text-[11px] file:font-black file:text-white"
            />
            {testDocumentFile && (
              <div className="mt-3 rounded-lg bg-white p-2.5 text-[11px] font-semibold text-slate-600">
                <div className="truncate font-black text-slate-900">{testDocumentFile.name}</div>
                <div className="mt-1">{Math.round(testDocumentFile.size / 1024)} KB</div>
              </div>
            )}
            {testDocumentPreviewUrl && testDocumentFile?.type === "application/pdf" ? (
              <object
                data={`${testDocumentPreviewUrl}#page=1&toolbar=0&navpanes=0&scrollbar=0`}
                type="application/pdf"
                className="mt-3 h-56 w-full rounded-lg border border-slate-200 bg-white"
                aria-label="PDF first page preview"
              >
                <div className="flex h-56 w-full items-center justify-center rounded-lg bg-white p-4 text-center text-[11px] font-semibold text-slate-500">
                  เลือก PDF แล้ว ระบบจะแสดงพรีวิวจาก backend หลังทดสอบ
                </div>
              </object>
            ) : testDocumentPreviewUrl ? (
              <img src={testDocumentPreviewUrl} alt="" className="mt-3 max-h-44 w-full rounded-lg border border-slate-200 bg-white object-contain" />
            ) : (
              <div className="mt-3 rounded-lg border border-dashed border-slate-300 bg-white p-5 text-center text-[11px] font-semibold text-slate-500">
                PNG, JPEG, WebP หรือ PDF
              </div>
            )}
            {!stepTwoCompleted && (
              <p className="mt-3 rounded-lg bg-amber-50 p-2.5 text-[11px] font-bold text-amber-700">
                ต้อง Run Simulation ให้ผ่านก่อนจึงจะทดสอบเอกสารใหม่ได้
              </p>
            )}
          </div>

          <div className="space-y-3">
            {detectionTestError && <p className="rounded-xl bg-red-50 p-3 text-xs font-black text-red-700">{detectionTestError}</p>}
            {!detectionTest ? (
              <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 text-xs font-semibold text-slate-500">
                No new document detection test has been run yet.
              </div>
            ) : (
              <div className="rounded-xl border border-slate-100 bg-white p-3">
                <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                  <DraftOverviewMetric label="ผลการจับคู่" value={detectionTest.matched ? "ตรวจพบ" : "ตรวจไม่พบ"} tone={detectionTest.matched ? "emerald" : "slate"} />
                  <DraftOverviewMetric label="Template ที่ตรวจพบ" value={detectionTest.selectedTemplate?.templateName || detectionTest.selectedTemplate?.templateId || "N/A"} />
                  <DraftOverviewMetric label="ประเภทของ Template" value={detectionTest.selectedTemplateType || "N/A"} />
                  <DraftOverviewMetric label="คะแนนความมั่นใจในการจับคู่" value={formatPrepublishScore(detectionTest.finalConfidence)} tone="indigo" />
                  <DraftOverviewMetric label="เหตุผลที่เลือก Template" value={detectionTest.decisionReason || "N/A"} />
                  <DraftOverviewMetric label="ลำดับของ Template ใน Draft" value={detectionTest.draftTemplateRank ?? "N/A"} />
                  <DraftOverviewMetric label="สถานะการทดสอบ" value={detectionTest.passed ? "ผ่าน" : detectionTest.warning ? "คำเตือน" : "ไม่ผ่าน"} tone={detectionTest.passed ? "emerald" : detectionTest.warning ? "orange" : "slate"} />
                </div>
              </div>
            )}
          </div>
        </div>
      </section>
      )}

      {validationStep === 3 && detectionTest && (
      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <DraftSectionHeader title="ผลการจัดอันดับ Template" subtitle="แสดงผลหลังจากทดสอบการตรวจจับ โดยสามารถขยายเพื่อดูรายละเอียดของแต่ละ Template ได้" />
        <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200">
          <table className="min-w-full divide-y divide-slate-200 text-xs">
            <thead className="bg-slate-50 text-[10px] font-black uppercase tracking-wider text-slate-500">
              <tr>
                <th className="px-3 py-2 text-left">อันดับ</th>
                <th className="px-3 py-2 text-left">Template</th>
                <th className="px-3 py-2 text-left">แหล่งข้อมูล</th>
                <th className="px-3 py-2 text-left">คะแนนรวม</th>
                <th className="px-3 py-2 text-left">Layout</th>
                <th className="px-3 py-2 text-left">Verification</th>
                <th className="px-3 py-2 text-left">Text Anchor</th>
                <th className="px-3 py-2 text-left">Image Anchor</th>
                <th className="px-3 py-2 text-left">ผลการประเมิน</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {(detectionTest?.candidates || []).length === 0 ? (
                <tr>
                  <td colSpan={9} className="px-3 py-6 text-center font-semibold text-slate-500">
                    Run Detection Test to see unified candidates.
                  </td>
                </tr>
              ) : (
                (detectionTest?.candidates || []).slice(0, 5).map((candidate) => (
                  <tr key={`${candidate.templateId}-${candidate.rank}-test`} className={candidate.isCurrentDraft ? "bg-indigo-50" : undefined}>
                    <td className="px-3 py-2 font-black text-slate-900">#{candidate.rank}</td>
                    <td className="px-3 py-2 font-bold text-slate-800">{candidate.templateName || candidate.templateId}</td>
                    <td className="px-3 py-2 font-semibold text-slate-600">
                      <div>{candidate.sourceLabel || (candidate.isCurrentDraft ? "Template ฉบับร่าง" : "Template ที่เผยแพร่")}</div>
                      {candidate.isCurrentDraft && candidate.layoutReferenceCount ? (
                        <div className="mt-1 text-[10px] font-black uppercase text-indigo-500">
                          {candidate.layoutReferenceCount} Layout References
                        </div>
                      ) : null}
                    </td>
                    <td className="px-3 py-2 font-black text-slate-900">{formatPrepublishScore(candidate.finalScore)}</td>
                    <td className="px-3 py-2">{formatPrepublishScore(candidate.globalScore)}</td>
                    <td className="px-3 py-2">{formatPrepublishScore(candidate.verificationScore)}</td>
                    <td className="px-3 py-2">{formatPrepublishScore(candidate.textAnchorScore)}</td>
                    <td className="px-3 py-2">{formatPrepublishScore(candidate.imageAnchorScore)}</td>
                    <td className="px-3 py-2">
                      <DraftStatusPill passed={candidate.finalPassed} label={candidate.decision || (candidate.finalPassed ? "PASS" : "FAIL")} />
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <div className="mt-4 space-y-3">
          {(detectionTest?.candidates || [])
            .slice(0, 5)
            .map((candidate) => {
              const key = `${candidate.templateId}-${candidate.rank}-detail`;

              return (
                <DraftCandidateCard
                  key={key}
                  candidate={candidate}
                  open={Boolean(expandedDetectionCandidates[key])}
                  onToggle={() =>
                    setExpandedDetectionCandidates((prev) => ({
                      ...prev,
                      [key]: !prev[key],
                    }))
                  }
                />
              );
            })}
        </div>
      </section>
      )}

      {validationStep === 2 && simulation && (
      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <DraftSectionHeader title="7. Verification Anchor Results" subtitle="Text anchors use OCR comparison. Image anchors use temporary image-feature similarity when available." />
        <div className="mt-4 grid gap-3 lg:grid-cols-2">
          {(simulation?.verificationAnchorResults || []).length > 0 ? (
            (simulation?.verificationAnchorResults || []).map((anchor, index) => {
              const anchorType = String(readPrepublishValue(anchor, ["anchor_type", "type", "verification_method"]) || "text");
              const passed = Boolean(readPrepublishValue(anchor, ["passed", "final_passed"]));
              return (
                <div key={`anchor-result-${index}`} className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-xs">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <div className="font-black text-slate-900">{String(readPrepublishValue(anchor, ["anchor_name", "field_name", "name", "display_label"]) || `Anchor ${index + 1}`)}</div>
                      <div className="mt-1 text-[10px] font-black uppercase text-slate-400">{anchorType}</div>
                    </div>
                    <DraftStatusPill passed={passed} />
                  </div>
                  {anchorType === "image" ? (
                    <div className="mt-3 grid gap-3 sm:grid-cols-2">
                      <div className="rounded-lg bg-white p-3">
                        <div className="text-[10px] font-black uppercase text-slate-400">Reference Preview</div>
                        {readPrepublishValue(anchor, ["reference_crop_preview_data_url", "reference_crop_preview_url"]) ? (
                          <img
                            src={String(readPrepublishValue(anchor, ["reference_crop_preview_data_url", "reference_crop_preview_url"]))}
                            alt=""
                            className="mt-2 h-28 w-full rounded-lg object-contain"
                          />
                        ) : (
                          <div className="mt-2 text-xs font-semibold text-slate-500">Preview unavailable</div>
                        )}
                      </div>
                      <div className="rounded-lg bg-white p-3">
                        <div className="text-[10px] font-black uppercase text-slate-400">Test Preview</div>
                        {readPrepublishValue(anchor, ["current_crop_preview_data_url", "current_crop_preview_url"]) ? (
                          <img
                            src={String(readPrepublishValue(anchor, ["current_crop_preview_data_url", "current_crop_preview_url"]))}
                            alt=""
                            className="mt-2 h-28 w-full rounded-lg object-contain"
                          />
                        ) : (
                          <div className="mt-2 text-xs font-semibold text-slate-500">Preview unavailable</div>
                        )}
                      </div>
                    </div>
                  ) : (
                    <div className="mt-3 grid gap-2 sm:grid-cols-2">
                      <p className="rounded-lg bg-white p-3 font-semibold text-slate-700">Expected: {String(readPrepublishValue(anchor, ["expected_text", "expectedText"]) || "N/A")}</p>
                      <p className="rounded-lg bg-white p-3 font-semibold text-slate-700">OCR: {String(readPrepublishValue(anchor, ["actual_text", "ocr_text", "actualText"]) || "N/A")}</p>
                    </div>
                  )}
                  <div className="mt-3 flex flex-wrap gap-2 text-[10px] font-black uppercase text-slate-500">
                    <span className="rounded-full bg-white px-2 py-1">Score {formatPrepublishScore(readVerificationRecordScore(anchor))}</span>
                    {readPrepublishValue(anchor, ["image_category_label", "image_category"]) && (
                      <span className="rounded-full bg-white px-2 py-1">{String(readPrepublishValue(anchor, ["image_category_label", "image_category"]))}</span>
                    )}
                    <span className="rounded-full bg-white px-2 py-1">Weight {String(readPrepublishValue(anchor, ["weight", "verification_weight"]) || "N/A")}</span>
                  </div>
                </div>
              );
            })
          ) : (
            <p className="rounded-xl bg-slate-50 p-4 text-xs font-semibold text-slate-500 lg:col-span-2">Run Simulation to see verification anchor results.</p>
          )}
        </div>
      </section>
      )}

      {validationStep === 4 && (
      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <DraftSectionHeader title="Publish Review" subtitle="ตรวจสอบความพร้อมครั้งสุดท้ายก่อนเปิดใช้งาน Template" />
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          {[
            ["ROI พร้อมใช้งาน", ocrPreviewPassed],
            ["Layout Simulation", stepTwoCompleted],
            ["ทดสอบเอกสารใหม่", detectionTestPassed],
          ].map(([label, passed]) => (
            <div key={String(label)} className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50 p-3">
              <span className="text-xs font-black text-slate-800">{String(label)}</span>
              <DraftStatusPill passed={Boolean(passed)} label={Boolean(passed) ? "PASS" : "WAIT"} />
            </div>
          ))}
        </div>
        <div className="mt-4 flex flex-col gap-3 rounded-xl border border-slate-200 bg-slate-50 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h4 className="text-sm font-black text-slate-900">{overallReady ? "พร้อมเผยแพร่" : "ยังเผยแพร่ไม่ได้"}</h4>
            <p className="mt-1 text-xs font-semibold text-slate-500">
              {overallReady ? "เมื่อตกลง ระบบจะบันทึก Layout Signature จริงและเปิดใช้งาน Template" : "ต้องให้ Layout Simulation และ New Document Test ผ่านก่อน"}
            </p>
          </div>
          <button
            type="button"
            onClick={handleConfirmPublish}
            disabled={!canConfirmPublish}
            className="ui-stable-action-lg rounded-xl bg-emerald-600 px-4 py-2 text-xs font-black text-white disabled:bg-slate-300 disabled:text-slate-500"
          >
            {template.status === "active" ? "เผยแพร่แล้ว" : simulationAction === "confirm" ? "กำลังเผยแพร่..." : "เผยแพร่ Template"}
          </button>
        </div>
      </section>
      )}

      {showPublishSuccessDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 px-4">
          <div className="w-full max-w-md rounded-2xl border border-emerald-100 bg-white p-6 text-center shadow-2xl">
            <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-emerald-100 text-emerald-700">
              <CheckCircle2 size={30} />
            </div>
            <h3 className="mt-4 text-lg font-black text-slate-950">เผยแพร่ Template สำเร็จ</h3>
            <p className="mt-2 text-sm font-semibold text-slate-500">
              Template นี้ถูกเปิดใช้งานแล้ว ผู้ใช้สามารถค้นหาและใช้งานได้ตามกระบวนการจริง
            </p>
            <div className="mt-5 flex justify-center gap-2">
              <button
                type="button"
                onClick={() => setShowPublishSuccessDialog(false)}
                className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-black text-slate-700"
              >
                ปิด
              </button>
              <Link href="/admin/templates" className="rounded-xl bg-emerald-600 px-4 py-2 text-xs font-black text-white">
                ไปคลัง Template
              </Link>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

