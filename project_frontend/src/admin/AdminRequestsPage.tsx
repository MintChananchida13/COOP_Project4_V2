"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AdminTemplateRequest } from "../types/ocr";
import { EmptyState, InlineState, LoadingState, StatusBadge, cardClassName } from "../shared/ui";
import { fetchTemplateRequests } from "./adminApi";

type RequestFilter = "pending" | "rejected";
type LoadStatus = "loading" | "loaded" | "error";

const formatDate = (value?: string) => {
  if (!value) return "ยังไม่มีวันที่ส่ง";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("th-TH", { dateStyle: "medium", timeStyle: "short" });
};

const filterOptions: { value: RequestFilter; label: string }[] = [
  { value: "pending", label: "รอตรวจสอบ" },
  { value: "rejected", label: "ปฏิเสธ" },
];

export default function AdminRequestsPage() {
  const [requests, setRequests] = useState<AdminTemplateRequest[]>([]);
  const [loadStatus, setLoadStatus] = useState<LoadStatus>("loading");
  const [filter, setFilter] = useState<RequestFilter>("pending");

  useEffect(() => {
    let cancelled = false;

    const loadRequests = async () => {
      setLoadStatus("loading");
      try {
        const persistedRequests = await fetchTemplateRequests();
        if (cancelled) return;
        setRequests(persistedRequests);
        setLoadStatus("loaded");
      } catch (error) {
        console.warn("Template requests load failed.", error);
        if (cancelled) return;
        setRequests([]);
        setLoadStatus("error");
      }
    };

    loadRequests();

    return () => {
      cancelled = true;
    };
  }, []);

  const counts: Record<RequestFilter, number> = {
    pending: requests.filter((request) => request.status === "submitted" || request.status === "in_review").length,
    rejected: requests.filter((request) => request.status === "rejected").length,
  };

  const filteredRequests = requests.filter((request) => {
    if (filter === "pending") return request.status === "submitted" || request.status === "in_review";
    return request.status === filter;
  });

  return (
    <section className="space-y-4">
      <div className={`${cardClassName} p-4`}>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h2 className="text-sm font-black uppercase tracking-wide text-slate-800">คำขอ Template</h2>
            <p className="mt-1 text-xs font-semibold text-slate-500">
              ตรวจคำขอสร้าง Template ที่ผู้ใช้ส่งเข้ามา และแปลงเป็น Template เมื่อพร้อมใช้งาน
            </p>
          </div>

          <div className="grid gap-2 sm:grid-cols-2">
            {filterOptions.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => setFilter(option.value)}
                className={`rounded-xl border px-3 py-2 text-xs font-black transition-colors ${
                  filter === option.value
                    ? "border-indigo-500 bg-indigo-600 text-white shadow-sm"
                    : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
                }`}
              >
                {option.label}
                <span className={`ml-2 rounded-full px-1.5 py-0.5 text-[10px] ${
                  filter === option.value ? "bg-white/20 text-white" : "bg-slate-100 text-slate-500"
                }`}>
                  {counts[option.value]}
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>

      {loadStatus === "loading" && <LoadingState message="กำลังโหลดคำขอจาก Backend..." />}
      {loadStatus === "error" && (
        <InlineState tone="warning" message="โหลดคำขอ Template จาก Backend ไม่สำเร็จ กรุณาตรวจการเชื่อมต่อแล้วลองใหม่" />
      )}

      {loadStatus === "loaded" && requests.length === 0 && (
        <EmptyState title="ยังไม่มีคำขอ Template" message="เมื่อผู้ใช้ส่งคำขอสร้าง Template รายการจะแสดงที่นี่" />
      )}

      {loadStatus === "loaded" && requests.length > 0 && filteredRequests.length === 0 && (
        <EmptyState title="ไม่พบคำขอในสถานะนี้" message="ลองเปลี่ยนตัวกรองด้านบนเพื่อดูรายการคำขอที่มีอยู่" />
      )}

      {loadStatus === "loaded" && filteredRequests.length > 0 && (
        <div className="grid gap-3">
          {filteredRequests.map((request) => (
            <article key={request.id} className={`${cardClassName} p-4`}>
              <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                <div className="min-w-0">
                  <h3 className="truncate text-base font-black text-slate-900">{request.requestTitle}</h3>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <StatusBadge status={request.requestMode} tone="primary" />
                    <StatusBadge status={request.status} />
                    <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[10px] font-black uppercase text-slate-600">
                      {request.documentType || "ไม่ระบุประเภท"}
                    </span>
                  </div>
                  <p className="mt-2 text-xs font-semibold text-slate-500">
                    {request.pageCount} หน้า | ส่งเมื่อ: {formatDate(request.createdAt)}
                  </p>
                </div>

                <div className="flex flex-wrap gap-2">
                  {request.status === "converted" && request.convertedTemplateId && (
                    <Link
                      href={`/admin/templates/${request.convertedTemplateId}/edit`}
                      className="inline-flex w-fit rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2 text-xs font-black text-emerald-700 hover:bg-emerald-100"
                    >
                      เปิด Template
                    </Link>
                  )}
                  <Link
                    href={`/admin/requests/${request.id}`}
                    className="inline-flex w-fit rounded-xl bg-indigo-600 px-4 py-2 text-xs font-black text-white hover:bg-indigo-700"
                  >
                    ตรวจคำขอ
                  </Link>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
