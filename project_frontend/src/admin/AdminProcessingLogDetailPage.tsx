"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ArrowLeft, ChevronLeft, ChevronRight, Download, Trash2 } from "lucide-react";
import { ActionButton, EmptyState, InlineState, PageHeader, StatusBadge, cardClassName } from "../shared/ui";
import { formatProcessingLogDateTime, getProcessingLogById, ProcessingLogField } from "./processingLogsMock";

const formatSeconds = (ms: number) => `${(ms / 1000).toFixed(2)} s`;
const formatScore = (value: number | null) => (value === null || value === undefined ? "-" : value.toFixed(2));

export default function AdminProcessingLogDetailPage({ logId }: { logId: string }) {
  const log = getProcessingLogById(logId);
  const [currentPage, setCurrentPage] = useState(1);
  const [showRoi, setShowRoi] = useState(true);
  const [selectedFieldId, setSelectedFieldId] = useState<string | null>(null);
  const [mockMessage, setMockMessage] = useState("");
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const selectedField = useMemo(
    () => log?.ocrOriginal.find((field) => field.fieldId === selectedFieldId) || null,
    [log, selectedFieldId]
  );

  if (!log) {
    return (
      <section className="space-y-4">
        <PageHeader title="Processing Log Detail" description={logId} actions={<ActionButton href="/admin/logs">กลับไป Processing Logs</ActionButton>} />
        <EmptyState title="ไม่พบ Processing Log" message="Mock data ไม่มี Log ID นี้" />
      </section>
    );
  }

  const pageFields = log.ocrOriginal.filter((field) => field.pageNumber === currentPage);
  const visibleFields = selectedField && selectedField.pageNumber === currentPage ? [selectedField] : pageFields;
  const groundTruthByField = new Map(log.groundTruth.map((field) => [field.fieldId, field.value]));

  const selectField = (field: ProcessingLogField) => {
    setSelectedFieldId(field.fieldId);
    setCurrentPage(field.pageNumber);
  };

  const pageCount = Math.max(log.pageCount, 1);

  return (
    <section className="space-y-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <Link href="/admin/logs" className="inline-flex items-center gap-2 text-xs font-black text-slate-500 hover:text-slate-950">
            <ArrowLeft size={14} />
            กลับไป Processing Logs
          </Link>
          <div className="mt-3">
            <h1 className="text-xl font-black tracking-tight text-slate-950">Processing Log Detail</h1>
            <p className="mt-1 text-sm font-semibold text-slate-500">
              {log.documentName} · {log.id}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setMockMessage("Export Log เป็น Mock UI เท่านั้น ยังไม่มีการสร้างไฟล์จริง")}
            className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-black text-slate-700 hover:bg-slate-50"
          >
            <Download size={15} />
            Export Log
          </button>
          <button
            type="button"
            onClick={() => setShowDeleteConfirm(true)}
            className="inline-flex items-center gap-2 rounded-xl border border-red-200 bg-white px-4 py-2.5 text-sm font-black text-red-600 hover:bg-red-50"
          >
            <Trash2 size={15} />
            ลบ Log
          </button>
        </div>
      </div>

      {mockMessage && <InlineState tone="info" message={mockMessage} />}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(20rem,0.85fr)]">
        <section className={`${cardClassName} p-4`}>
          <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-sm font-black text-slate-950">เอกสารต้นฉบับ</h2>
              <p className="text-xs font-semibold text-slate-500">Page {currentPage} / {pageCount}</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                disabled={currentPage <= 1}
                onClick={() => {
                  setCurrentPage((page) => Math.max(1, page - 1));
                  setSelectedFieldId(null);
                }}
                className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-300"
                aria-label="หน้าก่อนหน้า"
              >
                <ChevronLeft size={15} />
              </button>
              <button
                type="button"
                disabled={currentPage >= pageCount}
                onClick={() => {
                  setCurrentPage((page) => Math.min(pageCount, page + 1));
                  setSelectedFieldId(null);
                }}
                className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-300"
                aria-label="หน้าถัดไป"
              >
                <ChevronRight size={15} />
              </button>
              <label className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-xs font-black text-slate-700">
                <input type="checkbox" checked={showRoi} onChange={(event) => setShowRoi(event.target.checked)} className="h-4 w-4 rounded border-slate-300" />
                แสดง ROI
              </label>
            </div>
          </div>

          <div className="mx-auto max-w-[34rem] rounded-2xl border border-slate-200 bg-slate-100 p-3">
            <div className="relative aspect-[3/4] overflow-hidden rounded-xl border border-slate-200 bg-white shadow-inner">
              <MockDocumentPage pageNumber={currentPage} documentName={log.documentName} />
              {showRoi &&
                visibleFields.map((field) => (
                  <button
                    key={field.fieldId}
                    type="button"
                    onClick={() => selectField(field)}
                    className={`absolute rounded-md border-2 text-left transition-colors ${
                      selectedFieldId === field.fieldId
                        ? "border-blue-600 bg-blue-500/15 shadow-[0_0_0_3px_rgba(37,99,235,0.16)]"
                        : "border-emerald-500 bg-emerald-400/10"
                    }`}
                    style={{
                      left: `${field.roi.x * 100}%`,
                      top: `${field.roi.y * 100}%`,
                      width: `${field.roi.width * 100}%`,
                      height: `${field.roi.height * 100}%`,
                    }}
                  >
                    <span className="absolute -top-6 left-0 max-w-[12rem] truncate rounded-md bg-slate-950 px-2 py-1 text-[10px] font-black text-white">
                      {field.fieldName}
                    </span>
                  </button>
                ))}
            </div>
          </div>
        </section>

        <section className={`${cardClassName} p-4`}>
          <h2 className="text-sm font-black text-slate-950">ข้อมูลทั่วไป</h2>
          <div className="mt-4 grid gap-3">
            <InfoItem label="ชื่อเอกสาร" value={log.documentName} />
            <InfoItem label="ผู้ใช้งาน" value={log.user} />
            <InfoItem label="วันที่/เวลา" value={formatProcessingLogDateTime(log.createdAt)} />
            <InfoItem label="จำนวนหน้า" value={`${log.pageCount} หน้า`} />
            <InfoItem
              label="สถานะ"
              value={<StatusBadge status={log.status === "completed" ? "สำเร็จ" : "ล้มเหลว"} tone={log.status === "completed" ? "success" : "danger"} />}
            />
            <InfoItem label="Detection Processing Time" value={formatSeconds(log.detectionProcessingTimeMs)} />
            <InfoItem label="OCR Processing Time" value={formatSeconds(log.ocrProcessingTimeMs)} />
            <InfoItem label="Log ID" value={log.id} />
          </div>
        </section>
      </div>

      <section className={`${cardClassName} p-4`}>
        <h2 className="text-sm font-black text-slate-950">Template Detection</h2>
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-5">
          <InfoTile label="ผลการตรวจจับ" value={log.templateDetection.matched ? "Matched" : "No Match"} />
          <InfoTile label="Template" value={log.templateDetection.selectedTemplate || "-"} />
          <InfoTile label="Template Version" value={log.templateDetection.templateVersion || "-"} />
          <InfoTile label="Detection Mode" value={log.templateDetection.detectionMode} />
          <InfoTile label="Verification Mode" value={log.templateDetection.verificationMode} />
        </div>

        <div className="mt-5 grid gap-4 xl:grid-cols-2">
          <div>
            <h3 className="text-xs font-black text-slate-900">Retrieval Candidates</h3>
            <div className="mt-2 overflow-hidden rounded-xl border border-slate-200">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50 text-xs font-black text-slate-500">
                  <tr>
                    <th className="px-3 py-2">Template</th>
                    <th className="px-3 py-2">Layout Score</th>
                    <th className="px-3 py-2">Result</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {log.templateDetection.candidates.map((candidate) => (
                    <tr key={`${candidate.template}-${candidate.layoutScore}`}>
                      <td className="px-3 py-2 font-bold text-slate-800">{candidate.template}</td>
                      <td className="px-3 py-2 font-semibold text-slate-600">{candidate.layoutScore.toFixed(2)}</td>
                      <td className="px-3 py-2 font-semibold text-slate-600">{candidate.result}</td>
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

      <div className="grid gap-4 xl:grid-cols-2">
        <section className={`${cardClassName} p-4`}>
          <h2 className="text-sm font-black text-slate-950">OCR Original Result</h2>
          <div className="mt-3 space-y-2">
            {log.ocrOriginal.length === 0 ? (
              <EmptyState title="ไม่มี OCR Result" message="Mock log นี้ไม่มี field ที่อ่านได้" />
            ) : (
              log.ocrOriginal.map((field) => (
                <button
                  key={field.fieldId}
                  type="button"
                  onClick={() => selectField(field)}
                  className={`w-full rounded-xl border p-3 text-left transition-colors ${
                    selectedFieldId === field.fieldId
                      ? "border-blue-200 bg-blue-50"
                      : "border-slate-200 bg-white hover:bg-slate-50"
                  }`}
                >
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                    <div>
                      <p className="text-sm font-black text-slate-900">{field.fieldName}</p>
                      <p className="mt-1 text-[11px] font-bold uppercase text-slate-400">
                        {field.fieldType} · Page {field.pageNumber} · {field.roiMode === "fixed" ? "Fixed" : "Flexible"}
                      </p>
                    </div>
                    {typeof field.confidence === "number" && (
                      <span className="text-xs font-black text-slate-500">{Math.round(field.confidence * 100)}%</span>
                    )}
                  </div>
                  <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-sm font-semibold text-slate-700">{field.value || "-"}</p>
                </button>
              ))
            )}
          </div>
        </section>

        <section className={`${cardClassName} p-4`}>
          <h2 className="text-sm font-black text-slate-950">Ground Truth</h2>
          <div className="mt-3 space-y-2">
            {log.ocrOriginal.length === 0 ? (
              <EmptyState title="ไม่มี Ground Truth" message="Mock log นี้ไม่มีข้อมูล Ground Truth" />
            ) : (
              log.ocrOriginal.map((field) => (
                <div key={field.fieldId} className="rounded-xl border border-slate-200 bg-white p-3">
                  <p className="text-sm font-black text-slate-900">{field.fieldName}</p>
                  <p className="mt-2 rounded-lg bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-800">
                    {groundTruthByField.get(field.fieldId) || "-"}
                  </p>
                </div>
              ))
            )}
          </div>
        </section>
      </div>

      {showDeleteConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/35 px-4">
          <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-5 shadow-xl">
            <h2 className="text-base font-black text-slate-950">ลบ Processing Log?</h2>
            <p className="mt-2 text-sm font-semibold text-slate-500">
              การดำเนินการนี้เป็น Mock UI เท่านั้น และจะกลับไปหน้า Processing Logs หลังยืนยัน
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowDeleteConfirm(false)}
                className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-black text-slate-700 hover:bg-slate-50"
              >
                ยกเลิก
              </button>
              <Link href="/admin/logs" className="rounded-xl bg-red-600 px-4 py-2 text-sm font-black text-white hover:bg-red-700">
                ลบ Log
              </Link>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function MockDocumentPage({ pageNumber, documentName }: { pageNumber: number; documentName: string }) {
  return (
    <div className="absolute inset-0 p-[8%]">
      <div className="flex h-full flex-col">
        <div className="flex items-start justify-between border-b border-slate-200 pb-4">
          <div>
            <div className="h-3 w-24 rounded bg-blue-200" />
            <div className="mt-3 h-2 w-40 rounded bg-slate-200" />
          </div>
          <div className="space-y-2">
            <div className="h-2 w-24 rounded bg-slate-200" />
            <div className="h-2 w-20 rounded bg-slate-200" />
          </div>
        </div>
        <div className="mt-8 space-y-3">
          {Array.from({ length: pageNumber === 1 ? 10 : 7 }, (_, index) => (
            <div
              key={index}
              className="h-2 rounded bg-slate-200"
              style={{ width: `${index % 3 === 0 ? 88 : index % 3 === 1 ? 72 : 94}%` }}
            />
          ))}
        </div>
        <div className="mt-auto border-t border-slate-100 pt-4 text-[10px] font-black text-slate-300">
          {documentName} · Page {pageNumber}
        </div>
      </div>
    </div>
  );
}

function InfoItem({ label, value }: { label: string; value: string | React.ReactNode }) {
  return (
    <div className="grid gap-1 rounded-xl bg-slate-50 px-3 py-2 sm:grid-cols-[9rem_minmax(0,1fr)]">
      <span className="text-xs font-black text-slate-400">{label}</span>
      <span className="min-w-0 text-sm font-black text-slate-800">{value}</span>
    </div>
  );
}

function InfoTile({ label, value }: { label: string; value: string | React.ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-3 py-2">
      <p className="text-[11px] font-black text-slate-400">{label}</p>
      <p className="mt-1 text-sm font-black text-slate-900">{value}</p>
    </div>
  );
}
