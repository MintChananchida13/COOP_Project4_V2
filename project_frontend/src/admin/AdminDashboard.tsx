"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowUpRight, BadgeCheck, CircleX, FileClock, FilePenLine } from "lucide-react";
import { EmptyState, StatusBadge } from "../shared/ui";
import { fetchAdminDashboard, fetchProcessingLogs, formatProcessingLogDateTime, type ProcessingLog } from "./adminApi";
import { AdminDashboardSummary } from "./adminTypes";

const formatDateTime = (value?: string) => {
  if (!value) return "ไม่พบเวลาอัปเดต";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("th-TH", { dateStyle: "medium", timeStyle: "short" });
};

const formatTemplateDashboardTitle = (template: AdminDashboardSummary["latestTemplates"][number]) => {
  const name = template.templateGroupName || template.versionName || template.name || "แม่แบบ";
  const versionNumber = Number(template.versionNumber || template.version || 1);
  if (!Number.isFinite(versionNumber) || versionNumber <= 0) return name;
  return `${name} - ฉบับที่ ${versionNumber}`;
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

export default function AdminDashboard() {
  const [loadStatus, setLoadStatus] = useState<"loading" | "loaded" | "error">("loading");
  const [dashboard, setDashboard] = useState<AdminDashboardSummary>({
    pendingRequests: 0,
    draftTemplates: 0,
    activeTemplates: 0,
    rejectedRequests: 0,
    templateCount: 0,
    latestRequests: [],
    latestTemplates: [],
  });
  const [recentProcessingLogs, setRecentProcessingLogs] = useState<ProcessingLog[]>([]);

  useEffect(() => {
    let cancelled = false;
    const loadDashboard = async () => {
      setLoadStatus("loading");
      try {
        const [nextDashboard, nextProcessingLogs] = await Promise.all([
          fetchAdminDashboard(),
          fetchProcessingLogs(),
        ]);
        if (cancelled) return;

        setDashboard(nextDashboard);
        setRecentProcessingLogs(nextProcessingLogs.slice(0, 5));
        setLoadStatus("loaded");
      } catch (error) {
        console.warn("Admin dashboard load failed.", error);
        if (cancelled) return;
        setDashboard({
          pendingRequests: 0,
          draftTemplates: 0,
          activeTemplates: 0,
          rejectedRequests: 0,
          templateCount: 0,
          latestRequests: [],
          latestTemplates: [],
        });
        setRecentProcessingLogs([]);
        setLoadStatus("error");
      }
    };
    loadDashboard();
    return () => {
      cancelled = true;
    };
  }, []);

  const stats = [
    ["คำขอที่รอดำเนินการ", dashboard.pendingRequests, FileClock, "bg-amber-50 text-amber-700"],
    ["Template ที่เผยแพร่แล้ว", dashboard.activeTemplates, BadgeCheck, "bg-emerald-50 text-emerald-700"],
    ["Template ฉบับร่าง", dashboard.draftTemplates, FilePenLine, "bg-sky-50 text-sky-700"],
    ["คำขอที่ถูกปฏิเสธ", dashboard.rejectedRequests, CircleX, "bg-red-50 text-red-700"],
  ] as const;

  const recentRequests = dashboard.latestRequests;
  const recentTemplates = dashboard.latestTemplates;

  if (loadStatus === "loading") {
    return (
      <section className="space-y-4">
        <div className="border-b border-slate-200 pb-4">
          <div>
            <h1 className="text-xl font-black tracking-tight text-slate-950">Admin Dashboard</h1>
            <p className="mt-1 text-sm font-semibold text-slate-700">กำลังโหลดข้อมูลล่าสุดจากฐานข้อมูล</p>
          </div>
        </div>
        <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm font-semibold text-slate-700 shadow-sm">
          กำลังโหลดข้อมูลแดชบอร์ด...
        </div>
      </section>
    );
  }

  return (
    <section className="space-y-4">
      <div className="border-b border-slate-200 pb-4">
        <div>
          <h1 className="text-xl font-black tracking-tight text-slate-950">Admin Dashboard</h1>
          <p className="mt-1 text-sm font-semibold text-slate-700">ภาพรวมคำขอ Template และประวัติการประมวลผลล่าสุดของระบบ OCR</p>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {stats.map(([label, value, Icon, tone]) => (
          <div key={label} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-xs font-bold text-slate-700">{label}</p>
                <p className="mt-1 text-2xl font-black tracking-tight text-slate-950">{value}</p>
              </div>
              <div className={`rounded-lg p-2 ${tone}`}>
                <Icon size={16} />
              </div>
            </div>
          </div>
        ))}
      </div>

      {loadStatus === "error" && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm font-bold text-amber-700 shadow-sm">
          โหลดข้อมูลแดชบอร์ดไม่สำเร็จ กรุณาตรวจสอบการเชื่อมต่อระบบหลังบ้านแล้วลองใหม่
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <DashboardList
          title="คำขอสร้าง Template ที่อัปเดตล่าสุด"
          href="/admin/requests"
          items={recentRequests.map((request) => ({
            id: request.id,
            title: request.requestTitle,
            meta: `${request.pageCount} หน้า · ${formatDateTime(request.updatedAt || request.createdAt)}`,
            status: request.status,
            tone: "amber",
          }))}
          emptyText="ยังไม่มีคำขอ"
        />

        <DashboardList
          title="Template ที่อัปเดตล่าสุด"
          href="/admin/templates"
          items={recentTemplates.map((template) => ({
            id: template.id,
            title: formatTemplateDashboardTitle(template),
            meta: `${template.pageCount} หน้า · ${formatDateTime(template.updatedAt || template.createdAt)}`,
            status: template.status,
            tone: "indigo",
            editHref: `/admin/templates/${template.id}/edit`,
          }))}
          emptyText="ยังไม่มีแม่แบบ"
        />
      </div>

      <RecentProcessingLogs logs={recentProcessingLogs} />
    </section>
  );
}

function RecentProcessingLogs({ logs }: { logs: ProcessingLog[] }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h2 className="text-sm font-black tracking-tight text-slate-950">ประวัติการประมวลผลล่าสุด</h2>
        </div>
        <Link href="/admin/logs" className="inline-flex shrink-0 items-center gap-1 text-xs font-black text-slate-700 hover:text-slate-950">
          ดูประวัติทั้งหมด
          <ArrowUpRight size={12} />
        </Link>
      </div>

      <div className="overflow-x-auto">
        <table className="min-w-[760px] w-full text-left">
          <thead className="border-b border-slate-100 text-xs font-black text-slate-700">
            <tr>
              <th className="py-2 pr-3">เวลา</th>
              <th className="px-3 py-2">เอกสาร</th>
              <th className="px-3 py-2">แม่แบบ</th>
              <th className="px-3 py-2">ผลการตรวจจับ</th>
              <th className="py-2 pl-3">สถานะ</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 text-sm">
            {logs.map((log) => (
              <tr key={log.id}>
                <td className="whitespace-nowrap py-3 pr-3 text-xs font-bold text-slate-700">
                  {formatProcessingLogDateTime(log.createdAt)}
                </td>
                <td className="px-3 py-3 font-black text-slate-900">{log.documentName}</td>
                <td className="px-3 py-3 font-semibold text-slate-700">{log.templateDetection.selectedTemplate || "-"}</td>
                <td className="px-3 py-3">
                  <LogBadge label={log.templateDetection.matched ? "พบ Template" : "ไม่พบ Template"} tone={log.templateDetection.matched ? "success" : "warning"} />
                </td>
                <td className="py-3 pl-3">
                  <LogBadge label={log.status === "completed" ? "สำเร็จ" : "ล้มเหลว"} tone={log.status === "completed" ? "success" : "danger"} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DashboardList({
  title,
  href,
  items,
  emptyText,
}: {
  title: string;
  href: string;
  items: {
    id: string;
    title: string;
    meta: string;
    status: string;
    tone: "amber" | "indigo";
    editHref?: string;
  }[];
  emptyText: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-black tracking-tight text-slate-950">{title}</h2>
        </div>
        <Link href={href} className="inline-flex shrink-0 items-center gap-1 text-xs font-black text-slate-700 hover:text-slate-950">
          ดูทั้งหมด
          <ArrowUpRight size={12} />
        </Link>
      </div>

      <div className="divide-y divide-slate-100">
        {items.length === 0 ? (
          <EmptyState title={emptyText} message="ข้อมูลจะแสดงที่นี่เมื่อโหลดจากระบบหลังบ้านสำเร็จ" />
        ) : (
          items.map((item) => (
            <div key={item.id} className="flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
              <div className="min-w-0">
                <p className="truncate text-sm font-black text-slate-900">{item.title}</p>
                <p className="mt-1 truncate text-[11px] font-semibold text-slate-700">{item.meta}</p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <StatusBadge status={item.status} tone={item.tone === "amber" ? "warning" : "primary"} />
                {item.editHref && (
                  <Link href={item.editHref} className="rounded-md border border-slate-200 bg-white px-2 py-1 text-[10px] font-black text-slate-700 hover:bg-slate-50">
                    แก้ไข
                  </Link>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
