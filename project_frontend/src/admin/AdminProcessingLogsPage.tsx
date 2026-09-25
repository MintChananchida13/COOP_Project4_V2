"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Eye, Search, Trash2 } from "lucide-react";
import { ActionButton, EmptyState, InlineState, PageHeader, cardClassName } from "../shared/ui";
import { deleteProcessingLog, fetchProcessingLogs, formatProcessingLogDateTime, type ProcessingLog } from "./adminApi";

const detectionLabel = (log: ProcessingLog) => (log.templateDetection.matched ? "Matched" : "No Match");
const statusLabel = (status: ProcessingLog["status"]) => (status === "completed" ? "สำเร็จ" : "ล้มเหลว");
const cleanupAges = [30, 60, 90, 180] as const;
const cleanupCutoffDate = (ageDays: string) => {
  const days = Number(ageDays);
  const cutoff = new Date();
  cutoff.setHours(0, 0, 0, 0);
  cutoff.setDate(cutoff.getDate() - (Number.isFinite(days) ? days : 30));
  return cutoff;
};
const logCreatedDate = (log: ProcessingLog) => {
  const date = new Date(log.createdAt);
  return Number.isNaN(date.getTime()) ? null : date;
};
const isLogOlderThan = (log: ProcessingLog, ageDays: string) => {
  const createdAt = logCreatedDate(log);
  return Boolean(createdAt && createdAt < cleanupCutoffDate(ageDays));
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
  const [isCleanupRunning, setIsCleanupRunning] = useState(false);
  const [logs, setLogs] = useState<ProcessingLog[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setLoadError("");
    fetchProcessingLogs()
      .then((items) => {
        if (!cancelled) setLogs(items);
      })
      .catch((error) => {
        if (!cancelled) setLoadError(error instanceof Error ? error.message : "Processing logs load failed.");
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
    const today = new Date().toISOString().slice(0, 10);
    const matchesDate =
      dateFilter === "all" ||
      (dateFilter === "today" && logDate === today) ||
      (dateFilter === "older" && logDate < today);
    return matchesSearch && matchesTemplate && matchesDetection && matchesDate;
  });
  const cleanupTargets = logs.filter((log) => isLogOlderThan(log, cleanupAge));
  const cleanupCutoff = cleanupCutoffDate(cleanupAge);

  const handleCleanupLogs = async () => {
    if (cleanupTargets.length === 0 || isCleanupRunning) return;
    const confirmed = window.confirm(`ยืนยันลบ Processing Log ${cleanupTargets.length} รายการที่เก่ากว่า ${cleanupAge} วัน?`);
    if (!confirmed) return;
    setIsCleanupRunning(true);
    setCleanupMessage("");
    try {
      const targetIds = cleanupTargets.map((log) => log.id);
      await Promise.all(targetIds.map((id) => deleteProcessingLog(id)));
      setLogs((previous) => previous.filter((log) => !targetIds.includes(log.id)));
      setCleanupMessage(`ลบ Processing Log สำเร็จ ${targetIds.length} รายการ`);
      setIsCleanupOpen(false);
    } catch (error) {
      setCleanupMessage(error instanceof Error ? error.message : "ลบ Processing Log ไม่สำเร็จ");
    } finally {
      setIsCleanupRunning(false);
    }
  };

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
      {loadError && <InlineState tone="danger" message={loadError} />}

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
              {!isLoading && filteredLogs.map((log) => (
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
        {isLoading && (
          <div className="p-4">
            <InlineState tone="info" message="กำลังโหลด Processing Logs..." />
          </div>
        )}
        {!isLoading && !loadError && filteredLogs.length === 0 && (
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
              ใช้สำหรับลบ Log เก่าตามอายุของข้อมูล รวมถึงไฟล์ภาพเอกสารที่ผูกกับ Log นั้นบน Server
            </p>
            <label className="mt-4 block">
              <span className="text-xs font-black text-slate-700">ลบ Log ที่มีอายุมากกว่า</span>
              <select
                value={cleanupAge}
                onChange={(event) => setCleanupAge(event.target.value)}
                className="mt-2 h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-800 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
              >
                {cleanupAges.map((age) => (
                  <option key={age} value={String(age)}>
                    {age} วัน
                  </option>
                ))}
              </select>
            </label>
            <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5">
              <p className="text-sm font-black text-slate-800">พบ Log ที่เข้าเงื่อนไข {cleanupTargets.length} รายการ</p>
              <p className="mt-1 text-xs font-semibold text-slate-500">ข้อมูลก่อนวันที่ {formatProcessingLogDateTime(cleanupCutoff.toISOString())}</p>
              <p className="mt-3 text-xs font-semibold text-slate-500">
                การลบจะเรียก API จริงและลบ storage ของ Processing Log ตาม backend lifecycle ปัจจุบัน
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
                onClick={handleCleanupLogs}
                disabled={cleanupTargets.length === 0 || isCleanupRunning}
                className="inline-flex items-center gap-2 rounded-xl border border-red-200 bg-white px-4 py-2 text-sm font-black text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-45"
              >
                <Trash2 size={14} />
                {isCleanupRunning ? "กำลังลบ..." : "ลบ Log ตามเงื่อนไข"}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
