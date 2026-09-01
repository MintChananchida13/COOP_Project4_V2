"use client";

import { ROI } from "../../types/ocr";

export type RoiBoxMode = "readonly" | "editable";

export interface WorkspaceRoi extends ROI {
  pageIndex?: number;
  checked?: boolean;
  dimmed?: boolean;
}

interface RoiBoxProps {
  roi: WorkspaceRoi;
  selected?: boolean;
  checked?: boolean;
  dimmed?: boolean;
  readonly?: boolean;
  editable?: boolean;
  showLabel?: boolean;
  onSelect?: (id: number) => void;
}

export default function RoiBox({
  roi,
  selected = false,
  checked = true,
  dimmed = false,
  readonly = true,
  editable = false,
  showLabel = true,
  onSelect,
}: RoiBoxProps) {
  const hasPoints = roi.points && roi.points.length > 0;
  const roiKind = (roi as WorkspaceRoi & { kind?: string }).kind;
  const isIgnoreRegion = roiKind === "ignore_region";
  const isVerificationAnchor = roiKind === "verification_anchor";
  const isDimmed = !selected && (dimmed || !checked);
  const boxClass = selected
    ? isVerificationAnchor
      ? "border-orange-600 bg-orange-400/25 ring-4 ring-orange-300/45 shadow-[0_0_0_1px_rgba(234,88,12,0.35)]"
      : "border-sky-500 bg-sky-400/25 ring-4 ring-sky-300/45 shadow-[0_0_0_1px_rgba(14,165,233,0.35)]"
    : checked
      ? isVerificationAnchor
        ? "border-orange-600 bg-orange-400/20 ring-2 ring-orange-500/20"
        : isIgnoreRegion
        ? "border-amber-600 bg-amber-400/20 ring-2 ring-amber-500/20"
        : "border-indigo-600 bg-indigo-500/15 ring-2 ring-indigo-500/20"
      : "border-slate-400 bg-slate-400/5";
  const labelClass = selected
    ? isVerificationAnchor
      ? "bg-orange-600 border-orange-600 text-white"
      : "bg-sky-600 border-sky-600 text-white"
    : checked
      ? isVerificationAnchor
        ? "bg-orange-600 border-orange-600 text-white"
        : isIgnoreRegion
        ? "bg-amber-600 border-amber-600 text-white"
        : "bg-indigo-600 border-indigo-600 text-white"
      : "bg-white border-slate-200 text-slate-500";

  return (
    <button
      type="button"
      onClick={() => onSelect?.(roi.id)}
      className={`absolute text-left transition-all ${readonly && !editable ? "cursor-default" : "cursor-pointer"} ${
        isDimmed ? "opacity-25" : "opacity-100"
      } ${selected ? "z-40" : checked ? "z-30" : "z-20"}`}
      style={{
        left: roi.x,
        top: roi.y,
        width: roi.width,
        height: roi.height,
      }}
      aria-label={roi.fieldName}
    >
      <div
        className={`relative w-full h-full ${hasPoints ? "" : `border ${boxClass} ${readonly ? "border-dashed" : "border-solid"}`}`}
      >
        {hasPoints && (
          <svg className="absolute inset-0 w-full h-full pointer-events-none overflow-visible">
            <polygon
              points={roi.points?.map((p) => `${p.x - roi.x},${p.y - roi.y}`).join(" ")}
              fill={
                selected
                  ? isVerificationAnchor
                    ? "rgba(249, 115, 22, 0.24)"
                    : "rgba(14, 165, 233, 0.24)"
                  : checked
                    ? isVerificationAnchor
                      ? "rgba(249, 115, 22, 0.18)"
                      : "rgba(79, 70, 229, 0.18)"
                    : "rgba(100, 116, 139, 0.08)"
              }
              stroke={selected ? (isVerificationAnchor ? "#ea580c" : "#0284c7") : checked ? (isVerificationAnchor ? "#f97316" : "#4f46e5") : "#64748b"}
              strokeWidth="2"
              strokeDasharray={readonly ? "4,3" : "0"}
            />
          </svg>
        )}
        {showLabel && (
          <span
            className={`absolute -top-5 left-0 max-w-[220px] truncate px-1.5 py-0.5 text-[9px] font-bold rounded border shadow-sm ${labelClass}`}
          >
            {roi.fieldName || "Unnamed"}
          </span>
        )}
      </div>
    </button>
  );
}
