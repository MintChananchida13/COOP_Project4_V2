"use client";

import { ArrowLeft, CheckCircle2, Cpu, FileText, Image as ImageIcon, Table } from "lucide-react";
import { useRef, useState } from "react";
import { ROI } from "../../types/ocr";
import WorkspaceCustomEditor, { WorkspaceCustomEditorProps } from "../../shared/workspace/WorkspaceCustomEditor";
import { InlineState } from "../../shared/ui";

interface MatchedTemplateInfo {
  id: string;
  name: string;
  confidence?: number | null;
  decisionReason?: string | null;
  alignmentStatus?: string | null;
}

interface MatchedTemplateWorkspaceZoneProps extends WorkspaceCustomEditorProps {
  matchedTemplate: MatchedTemplateInfo;
  onSwitchToCustom: () => void;
}

const isTableRoi = (roi: ROI) =>
  roi.type === "table" || roi.extractionMethod === "ocr_table" || roi.extractionMethod === "table_recognition_v2";

const typeLabel = (roi: ROI) => {
  if (roi.type === "table" || roi.extractionMethod === "ocr_table") return "ตาราง";
  if (roi.type === "image" || roi.extractionMethod === "extract_image") return "รูปภาพ";
  return "ข้อความ";
};

const typeIcon = (roi: ROI) => {
  if (isTableRoi(roi)) return <Table size={13} />;
  if (roi.type === "image" || roi.extractionMethod === "extract_image") return <ImageIcon size={13} />;
  return <FileText size={13} />;
};

const readableTypeLabel = (roi: ROI) => {
  if (isTableRoi(roi)) return "ตาราง";
  if (roi.type === "image" || roi.extractionMethod === "extract_image") return "รูปภาพ";
  return "ข้อความ";
};

export default function MatchedTemplateWorkspaceZone({
  matchedTemplate,
  onSwitchToCustom,
  ...props
}: MatchedTemplateWorkspaceZoneProps) {
  const [fieldQuery, setFieldQuery] = useState("");
  const rightPanelScrollRef = useRef<HTMLDivElement | null>(null);
  const fieldItemRefs = useRef<Map<number, HTMLLabelElement>>(new Map());

  const scrollRightPanelToField = (roiId: number) => {
    setFieldQuery("");
    window.setTimeout(() => {
      const container = rightPanelScrollRef.current;
      const target = fieldItemRefs.current.get(roiId);
      if (!container || !target) return;

      const containerRect = container.getBoundingClientRect();
      const targetRect = target.getBoundingClientRect();
      const relativeTop = targetRect.top - containerRect.top + container.scrollTop;
      const top = relativeTop - Math.max(0, (container.clientHeight - targetRect.height) / 2);
      container.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
    }, 50);
  };

  const hasResolvedChildren = (roi: ROI) =>
    roi.roiMode === "flexible" &&
    props.rois.some(
      (candidate) =>
        candidate.isResolvedBlock &&
        candidate.parentRoiId === roi.id &&
        (candidate.pageIndex ?? 0) === (roi.pageIndex ?? 0)
    );
  const visibleRois = props.rois.filter((roi) => !hasResolvedChildren(roi));
  const selectedVisibleId = visibleRois.some((roi) => roi.id === props.selectedId) ? props.selectedId : null;

  return (
    <WorkspaceCustomEditor
      {...props}
      rois={visibleRois}
      selectedId={selectedVisibleId}
      onCanvasRoiSelect={scrollRightPanelToField}
      readOnly={false}
      hideOcrActions
      hideDrawTools
      hideFooterActions
      getRoiBadges={() => []}
      getRoiClassName={(roi, selected) => {
        if (hasResolvedChildren(roi)) {
          return "hidden pointer-events-none";
        }
        if (roi.isResolvedBlock) {
          return `rnd-box-item border transition-shadow pointer-events-auto ${
            selected
              ? "border-emerald-600 bg-emerald-500/10 shadow-md z-30 ring-2 ring-emerald-500/20"
              : "border-emerald-400/90 bg-emerald-50/10 hover:border-emerald-500 z-20"
          }`;
        }
        if (roi.roiMode === "flexible") {
          return `rnd-box-item border transition-shadow pointer-events-auto ${
            selected
              ? "border-emerald-600 bg-emerald-500/10 shadow-md z-30 ring-2 ring-emerald-500/20"
              : "border-emerald-400/90 bg-emerald-50/10 hover:border-emerald-500 z-20"
          }`;
        }
        const disabled = roi.enabled === false;
        return `rnd-box-item border transition-shadow ${
          disabled
            ? "border-slate-300 bg-slate-200/20 opacity-40"
            : selected
              ? "border-emerald-600 bg-emerald-500/10 shadow-md z-30 ring-2 ring-emerald-500/20"
              : "border-emerald-400/90 bg-emerald-50/10 hover:border-emerald-500 z-20"
        }`;
      }}
      getRoiLabelClassName={(roi, selected) =>
        `${hasResolvedChildren(roi) ? "hidden" : ""} absolute -top-5 left-0 px-1.5 py-0.5 text-[9px] font-sans rounded shadow border flex items-center gap-1.5 pointer-events-auto cursor-pointer ${
          roi.isResolvedBlock
            ? selected
              ? "bg-emerald-600 border-emerald-600 text-white font-extrabold"
              : "bg-white border-emerald-200 text-emerald-700 font-bold"
            : roi.roiMode === "flexible"
              ? selected
                ? "bg-emerald-600 border-emerald-600 text-white font-extrabold"
                : "bg-white border-emerald-200 text-emerald-700 font-bold"
              : roi.enabled === false
            ? "bg-slate-100 border-slate-200 text-slate-400"
            : selected
              ? "bg-emerald-600 border-emerald-600 text-white font-extrabold"
              : "bg-white border-emerald-200 text-emerald-700 font-bold"
        }`
      }
      getRoiLabelText={(roi) => {
        if (roi.isResolvedBlock) return `${roi.fieldName || "Resolved ROI"}`;
        return roi.roiMode === "flexible" ? `${roi.fieldName || "(Unnamed)"} · Flexible Search Area` : roi.fieldName || "(Unnamed)";
      }}
      rightPanelRenderer={({ currentPageRois, selectedId, setSelectedId, updateROI, triggerOCRProcessing }) => {
        const selectablePageRois = currentPageRois.filter((roi) => !hasResolvedChildren(roi));
        const enabledCount = selectablePageRois.filter((roi) => roi.enabled !== false).length;
        const filteredRois = currentPageRois.filter((roi) =>
          !hasResolvedChildren(roi) &&
          `${roi.fieldName} ${roi.type || ""} ${roi.extractionMethod || ""}`.toLowerCase().includes(fieldQuery.trim().toLowerCase())
        );

        return (
          <div className="flex h-full min-h-0 flex-col">
            <div ref={rightPanelScrollRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
            <div className="grid grid-cols-1 gap-2">
              <button
                type="button"
                onClick={props.onBackToAdjust}
                className="ui-button-text inline-flex min-w-0 items-center justify-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-slate-700 transition-colors hover:bg-slate-50"
              >
                <ArrowLeft size={14} />
                กลับไปปรับกรอบ
              </button>

              <button
                type="button"
                onClick={onSwitchToCustom}
                className="ui-button-text min-w-0 rounded-xl border border-blue-200 bg-blue-50 px-3 py-2.5 text-blue-700 shadow-sm transition-colors hover:bg-blue-100"
              >
                ไปหน้า OCR แบบกำหนดเอง
              </button>
            </div>

            <section className="rounded-2xl border border-emerald-100 bg-emerald-50 p-3">
              <div className="flex items-start gap-2.5">
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white text-emerald-600 shadow-sm ring-1 ring-emerald-100">
                  <CheckCircle2 size={18} />
                </div>
                <div className="min-w-0">
                  <h3 className="ui-label text-emerald-800">พบ Template ที่ตรงกัน</h3>
                  <p className="ui-card-title mt-1 truncate text-emerald-950">{matchedTemplate.name}</p>
                  <p className="ui-caption ui-tabular mt-1 text-emerald-700">
                    {matchedTemplate.confidence !== undefined && matchedTemplate.confidence !== null
                      ? `ความมั่นใจ ${(matchedTemplate.confidence * 100).toFixed(1)}%`
                      : "ยังไม่มีค่าความมั่นใจ"}
                    
                  </p>
                  <div className="mt-3 rounded-xl border border-emerald-100 bg-white/75 px-3 py-2">
                    <p className="ui-caption break-words font-semibold text-emerald-800">
                      ใช้ภาพที่จัดแนวเข้ากับ Template และใช้ ROI ต้นฉบับของ Template
                    </p>
                  </div>
                </div>
              </div>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="ui-card-title text-slate-800">เลือกข้อมูลที่ต้องการอ่าน</h3>
                  <p className="ui-body mt-1 text-slate-500">
                    เลือก Field ที่ต้องการอ่าน สามารถขยับและปรับขนาดกรอบได้ แต่ไม่สามารถเปลี่ยนชื่อหรือประเภท Field
                  </p>
                </div>
                <span className="ui-caption ui-tabular shrink-0 rounded-full bg-slate-100 px-2.5 py-1 font-semibold text-slate-600">
                  {enabledCount}/{selectablePageRois.length}
                </span>
              </div>

              <div className="mt-4 grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => selectablePageRois.forEach((roi) => updateROI(roi.id, { enabled: true }))}
                  className="ui-button-text rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-slate-600 transition-colors hover:bg-white"
                >
                  เลือกทั้งหมด
                </button>
                <button
                  type="button"
                  onClick={() => selectablePageRois.forEach((roi) => updateROI(roi.id, { enabled: false }))}
                  className="ui-button-text rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-slate-600 transition-colors hover:bg-white"
                >
                  ยกเลิกทั้งหมด
                </button>
              </div>

              <div className="mt-3">
                <input
                  type="search"
                  value={fieldQuery}
                  onChange={(event) => setFieldQuery(event.target.value)}
                  placeholder="ค้นหาชื่อข้อมูล..."
                  className="ui-label w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-slate-700 placeholder:text-slate-400 focus:border-blue-400 focus:bg-white focus:outline-none"
                  aria-label="Search template fields"
                />
              </div>

              <div className="mt-3 max-h-[320px] space-y-2 overflow-y-auto pr-1">
                {currentPageRois.length === 0 ? (
                  <p className="ui-body rounded-xl bg-slate-50 p-3 text-slate-500">ไม่พบ Field สำหรับหน้านี้</p>
                ) : filteredRois.length === 0 ? (
                  <p className="ui-body rounded-xl bg-slate-50 p-3 text-slate-500">ไม่พบ Field ที่ตรงกับคำค้นหา</p>
                ) : (
                  filteredRois.map((roi) => {
                    const checked = roi.enabled !== false;
                    const selected = selectedId === roi.id;
                    return (
                      <label
                        key={`${roi.pageIndex ?? 0}-${roi.id}`}
                        ref={(el) => {
                          if (el) fieldItemRefs.current.set(roi.id, el);
                          else fieldItemRefs.current.delete(roi.id);
                        }}
                        className={`ui-label flex cursor-pointer items-center gap-3 rounded-xl border p-3 transition-all ${
                          selected
                            ? "border-emerald-300 bg-emerald-50 text-emerald-950"
                            : checked
                              ? "border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
                              : "border-slate-200 bg-slate-50 text-slate-400"
                        }`}
                        onClick={() => setSelectedId(roi.id)}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(event) => updateROI(roi.id, { enabled: event.target.checked })}
                          className="h-4 w-4 rounded border-slate-300 text-emerald-600 focus:ring-emerald-500"
                        />
                        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-500">
                          {typeIcon(roi)}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate font-semibold">{roi.fieldName || "(Unnamed)"}</span>
                          <span className="ui-caption mt-0.5 block text-slate-400">{readableTypeLabel(roi)}</span>
                        </span>
                      </label>
                    );
                  })
                )}
              </div>
            </section>

            </div>

            <div className="space-y-3 border-t border-slate-200 bg-white p-4">
              {enabledCount === 0 && <InlineState tone="warning" message="เลือก Field อย่างน้อย 1 รายการก่อนเริ่ม OCR" />}
              <button
                type="button"
                disabled={props.isLoading || enabledCount === 0}
                onClick={triggerOCRProcessing}
                className="ui-button-text ui-stable-action-lg flex w-full items-center justify-center gap-2 rounded-xl bg-[#0052cc] px-6 py-3.5 text-white shadow-md transition-all hover:bg-[#0043a4] disabled:bg-slate-400 disabled:text-white/80"
              >
                <Cpu size={14} className={props.isLoading ? "animate-spin" : ""} />
                {props.isLoading ? "กำลังอ่านข้อมูล..." : `อ่านข้อมูลที่เลือก`}
              </button>
            </div>
          </div>
        );
      }}
    />
  );
}
