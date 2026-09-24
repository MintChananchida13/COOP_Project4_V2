"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Eye, Search } from "lucide-react";
import { ActionButton, EmptyState, InlineState, PageHeader, cardClassName } from "../shared/ui";
import { formatProcessingLogDateTime, processingLogsMock, ProcessingLog } from "./processingLogsMock";

const detectionLabel = (log: ProcessingLog) => (log.templateDetection.matched ? "Matched" : "No Match");
const statusLabel = (status: ProcessingLog["status"]) => (status === "completed" ? "สำเร็จ" : "ล้มเหลว");
const cleanupCutoffDates: Record<string, string> = {
  "30": "25/08/2569",
  "60": "26/07/2569",
  "90": "26/06/2569",
  "180": "28/03/2569",
};
const cleanupCounts: Record<string, number> = {
  "30": 12,
  "60": 8,
  "90": 5,
  "180": 2,
};

const logBadgeClass = (tone: "success" | "warning" | "danger") =>
  tone === "success"
    ? "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-100"
    : tone === "warning"
      ? "bg-amber-50 text-amber-700 ring-1 ring-amber-100"
      : "bg-red-50 text-red-700 ring-1 ring-red-100";

function LogBadge({ label, tone }: { label: string; tone: "success" | "warning" | "danger" }) {
  return <span className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-black ${logBadgeClass(tone)}`}>{label}</span>;
}

export default function AdminProcessingLogsPage() {
  const [search, setSearch] = useState("");
  const [templateFilter, setTemplateFilter] = useState("all");
  const [detectionFilter, setDetectionFilter] = useState("all");
  const [dateFilter, setDateFilter] = useState("all");
  const [isCleanupOpen, setIsCleanupOpen] = useState(false);
  const [cleanupAge, setCleanupAge] = useState("30");
  const [cleanupMessage, setCleanupMessage] = useState("");
  const logs = processingLogsMock;

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

  return (
    <section className="space-y-4">
      <PageHeader
        title="Processing Logs"
        description="ประวัติการประมวลผลเอกสารทั้งหมด"
        actions={
          <>
            <button
              type="button"
              onClick={() => setIsCleanupOpen(true)}
              className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-black text-slate-700 hover:bg-slate-50"
            >
              จัดการ Log
            </button>
            <ActionButton href="/admin">กลับ Dashboard</ActionButton>
          </>
        }
      />

      {cleanupMessage && <InlineState tone="info" message={cleanupMessage} />}

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
          <table className="min-w-[860px] w-full text-left">
            <thead className="border-b border-slate-200 bg-slate-50 text-xs font-black text-slate-500">
              <tr>
                <th className="px-4 py-3">วันที่/เวลา</th>
                <th className="px-4 py-3">เอกสาร</th>
                <th className="px-4 py-3">ผู้ใช้งาน</th>
                <th className="px-4 py-3">Template</th>
                <th className="px-4 py-3">Detection</th>
                <th className="px-4 py-3">สถานะ</th>
                <th className="w-36 px-4 py-3 text-right">จัดการ</th>
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
                    <LogBadge label={detectionLabel(log)} tone={log.templateDetection.matched ? "success" : "warning"} />
                  </td>
                  <td className="px-4 py-3">
                    <LogBadge label={statusLabel(log.status)} tone={log.status === "completed" ? "success" : "danger"} />
                  </td>
                  <td className="px-4 py-3">
                    <Link
                      href={`/admin/logs/${log.id}`}
                      className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-xs font-black text-slate-700 hover:bg-slate-50"
                    >
                      <Eye size={14} />
                      ดูรายละเอียด
                    </Link>
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

      {isCleanupOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/35 px-4">
          <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-5 shadow-xl">
            <h2 className="text-base font-black text-slate-950">จัดการ Processing Logs</h2>
            <p className="mt-2 text-sm font-semibold text-slate-500">
              ใช้สำหรับลบ Log เก่าตามอายุของข้อมูล รอบนี้เป็น UI Mock เท่านั้น
            </p>
            <label className="mt-4 block">
              <span className="text-xs font-black text-slate-700">ลบ Log ที่มีอายุมากกว่า</span>
              <select
                value={cleanupAge}
                onChange={(event) => setCleanupAge(event.target.value)}
                className="mt-2 h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-800 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
              >
                <option value="30">30 วัน</option>
                <option value="60">60 วัน</option>
                <option value="90">90 วัน</option>
                <option value="180">180 วัน</option>
              </select>
            </label>
            <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5">
              <p className="text-sm font-black text-slate-800">พบ Log ที่เข้าเงื่อนไข {cleanupCounts[cleanupAge]} รายการ</p>
              <p className="mt-1 text-xs font-semibold text-slate-500">ข้อมูลก่อนวันที่ {cleanupCutoffDates[cleanupAge]}</p>
              <p className="mt-3 text-xs font-semibold text-slate-500">
                เมื่อเชื่อมระบบจริง การลบจะรวมข้อมูล Processing Log และภาพเอกสารที่เกี่ยวข้องบน Server
              </p>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setIsCleanupOpen(false)}
                className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-black text-slate-700 hover:bg-slate-50"
              >
                ยกเลิก
              </button>
              <button
                type="button"
                onClick={() => {
                  setIsCleanupOpen(false);
                  setCleanupMessage(`Mock UI: ยังไม่มีการลบข้อมูลจริงสำหรับ Log ที่เก่ากว่า ${cleanupAge} วัน`);
                }}
                className="rounded-xl border border-red-200 bg-white px-4 py-2 text-sm font-black text-red-600 hover:bg-red-50"
              >
                ลบ Log ตามเงื่อนไข
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
