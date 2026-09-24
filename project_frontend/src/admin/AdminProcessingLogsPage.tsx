"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Eye, Search, Trash2 } from "lucide-react";
import { ActionButton, EmptyState, PageHeader, StatusBadge, cardClassName } from "../shared/ui";
import { formatProcessingLogDateTime, processingLogsMock, ProcessingLog } from "./processingLogsMock";

const detectionLabel = (log: ProcessingLog) => (log.templateDetection.matched ? "Matched" : "No Match");
const detectionTone = (log: ProcessingLog) => (log.templateDetection.matched ? "success" : "warning");
const statusLabel = (status: ProcessingLog["status"]) => (status === "completed" ? "สำเร็จ" : "ล้มเหลว");

export default function AdminProcessingLogsPage() {
  const [logs, setLogs] = useState(processingLogsMock);
  const [search, setSearch] = useState("");
  const [templateFilter, setTemplateFilter] = useState("all");
  const [detectionFilter, setDetectionFilter] = useState("all");
  const [dateFilter, setDateFilter] = useState("all");
  const [deleteTarget, setDeleteTarget] = useState<ProcessingLog | null>(null);

  const templateOptions = useMemo(
    () =>
      Array.from(
        new Set(logs.map((log) => log.templateDetection.selectedTemplate).filter((template): template is string => Boolean(template)))
      ),
    [logs]
  );

  const filteredLogs = logs.filter((log) => {
    const matchesSearch = log.documentName.toLowerCase().includes(search.trim().toLowerCase());
    const matchesTemplate = templateFilter === "all" || log.templateDetection.selectedTemplate === templateFilter;
    const matchesDetection =
      detectionFilter === "all" ||
      (detectionFilter === "matched" && log.templateDetection.matched) ||
      (detectionFilter === "no_match" && !log.templateDetection.matched);
    const logDate = log.createdAt.slice(0, 10);
    const today = "2026-09-24";
    const matchesDate =
      dateFilter === "all" ||
      (dateFilter === "today" && logDate === today) ||
      (dateFilter === "older" && logDate < today);
    return matchesSearch && matchesTemplate && matchesDetection && matchesDate;
  });

  const confirmDelete = () => {
    if (!deleteTarget) return;
    setLogs((current) => current.filter((log) => log.id !== deleteTarget.id));
    setDeleteTarget(null);
  };

  return (
    <section className="space-y-4">
      <PageHeader
        title="Processing Logs"
        description="ประวัติการประมวลผลเอกสารทั้งหมด"
        actions={<ActionButton href="/admin">กลับ Dashboard</ActionButton>}
      />

      <section className={`${cardClassName} p-4`}>
        <div className="grid gap-3 lg:grid-cols-[minmax(16rem,1.4fr)_minmax(10rem,0.8fr)_minmax(10rem,0.7fr)_minmax(9rem,0.6fr)]">
          <label className="relative block">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={16} />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="ค้นหาชื่อเอกสาร"
              className="h-11 w-full rounded-xl border border-slate-200 bg-white pl-9 pr-3 text-sm font-semibold text-slate-800 outline-none transition-colors placeholder:text-slate-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
            />
          </label>
          <select
            value={templateFilter}
            onChange={(event) => setTemplateFilter(event.target.value)}
            className="h-11 rounded-xl border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-800 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
          >
            <option value="all">Template ทั้งหมด</option>
            {templateOptions.map((template) => (
              <option key={template} value={template}>
                {template}
              </option>
            ))}
          </select>
          <select
            value={detectionFilter}
            onChange={(event) => setDetectionFilter(event.target.value)}
            className="h-11 rounded-xl border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-800 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
          >
            <option value="all">Detection ทั้งหมด</option>
            <option value="matched">Matched</option>
            <option value="no_match">No Match</option>
          </select>
          <select
            value={dateFilter}
            onChange={(event) => setDateFilter(event.target.value)}
            className="h-11 rounded-xl border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-800 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
          >
            <option value="all">วันที่ทั้งหมด</option>
            <option value="today">วันนี้</option>
            <option value="older">ก่อนวันนี้</option>
          </select>
        </div>
      </section>

      <section className={`${cardClassName} overflow-hidden`}>
        <div className="overflow-x-auto">
          <table className="min-w-[920px] w-full text-left">
            <thead className="border-b border-slate-200 bg-slate-50 text-xs font-black text-slate-500">
              <tr>
                <th className="px-4 py-3">วันที่/เวลา</th>
                <th className="px-4 py-3">เอกสาร</th>
                <th className="px-4 py-3">ผู้ใช้งาน</th>
                <th className="px-4 py-3">Template</th>
                <th className="px-4 py-3">Detection</th>
                <th className="px-4 py-3">สถานะ</th>
                <th className="px-4 py-3 text-right">จัดการ</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-sm">
              {filteredLogs.map((log) => (
                <tr key={log.id} className="hover:bg-slate-50">
                  <td className="whitespace-nowrap px-4 py-3 font-bold text-slate-600">{formatProcessingLogDateTime(log.createdAt)}</td>
                  <td className="px-4 py-3 font-black text-slate-900">{log.documentName}</td>
                  <td className="px-4 py-3 font-semibold text-slate-600">{log.user}</td>
                  <td className="px-4 py-3 font-semibold text-slate-600">{log.templateDetection.selectedTemplate || "-"}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={detectionLabel(log)} tone={detectionTone(log)} />
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={statusLabel(log.status)} tone={log.status === "completed" ? "success" : "danger"} />
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-2">
                      <Link
                        href={`/admin/logs/${log.id}`}
                        className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-xs font-black text-slate-700 hover:bg-slate-50"
                      >
                        <Eye size={14} />
                        ดู
                      </Link>
                      <button
                        type="button"
                        onClick={() => setDeleteTarget(log)}
                        className="inline-flex h-9 items-center gap-2 rounded-lg border border-red-200 bg-white px-3 text-xs font-black text-red-600 hover:bg-red-50"
                      >
                        <Trash2 size={14} />
                        ลบ
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {filteredLogs.length === 0 && (
          <div className="p-4">
            <EmptyState title="ไม่พบ Processing Log" message="ลองปรับคำค้นหาหรือตัวกรองอีกครั้ง" />
          </div>
        )}
      </section>

      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/35 px-4">
          <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-5 shadow-xl">
            <h2 className="text-base font-black text-slate-950">ลบ Processing Log?</h2>
            <p className="mt-2 text-sm font-semibold text-slate-500">
              การดำเนินการนี้จะลบ Log และข้อมูลที่เกี่ยวข้อง เฉพาะ Mock UI เท่านั้นในรอบนี้
            </p>
            <p className="mt-3 rounded-xl bg-slate-50 px-3 py-2 text-sm font-black text-slate-800">{deleteTarget.documentName}</p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-black text-slate-700 hover:bg-slate-50"
              >
                ยกเลิก
              </button>
              <button
                type="button"
                onClick={confirmDelete}
                className="rounded-xl bg-red-600 px-4 py-2 text-sm font-black text-white hover:bg-red-700"
              >
                ลบ Log
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
