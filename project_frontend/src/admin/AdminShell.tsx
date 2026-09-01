"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ReactNode, useEffect, useState } from "react";
import { LogOut } from "lucide-react";
import { StatusBadge } from "../shared/ui";
import { AuthSession, clearAuthSession, readAuthSession } from "../auth/session";
import { preloadAdminLists } from "./adminApi";

const navItems = [
  { href: "/admin", label: "ภาพรวม" },
  { href: "/admin/requests", label: "คำขอ Template" },
  { href: "/admin/templates", label: "คลัง Template" },
  { href: "/admin/detection-lab", label: "ทดสอบการค้นหา", badge: "DEV" },
];

export default function AdminShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [session, setSession] = useState<AuthSession | null>(null);

  useEffect(() => {
    setSession(readAuthSession());
    preloadAdminLists();
  }, []);

  const handleLogout = () => {
    clearAuthSession();
    router.replace("/login");
  };

  return (
    <main className="min-h-screen bg-slate-50">
      <div className="border-b border-slate-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-6 py-5 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <p className="ui-caption font-semibold text-blue-600">ระบบผู้ดูแล</p>
            <h1 className="ui-page-title mt-1 text-slate-950">จัดการ Template เอกสาร</h1>
            <p className="ui-body mt-1 text-slate-500">
              ตรวจคำขอ สร้าง Template ตรวจความพร้อม และทดสอบการค้นหาเอกสารก่อนนำไปใช้งานจริง
            </p>
            {session && (
              <div className="mt-3 flex flex-wrap items-center gap-2 text-xs font-bold text-slate-500">
                <span>{session.email}</span>
                <StatusBadge status="admin" tone="primary" />
              </div>
            )}
          </div>
          <div className="flex flex-col gap-3 lg:items-end">
            <nav className="flex flex-wrap gap-2">
              {navItems.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={pathname === item.href ? "page" : undefined}
                  className={`ui-button-text rounded-xl border px-4 py-2 transition-colors ${
                    pathname === item.href
                      ? "border-blue-200 bg-blue-50 text-blue-700"
                      : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50 hover:text-blue-700"
                  }`}
                >
                  {item.label}
                  {"badge" in item && item.badge && (
                    <span className="ml-2">
                      <StatusBadge status={item.badge} tone="warning" />
                    </span>
                  )}
                </Link>
              ))}
            </nav>
            <button
              type="button"
              onClick={handleLogout}
              className="inline-flex w-fit items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-black text-slate-600 hover:bg-slate-50"
            >
              <LogOut size={14} />
              ออกจากระบบ
            </button>
          </div>
        </div>
      </div>
      <div className="mx-auto max-w-7xl space-y-6 px-6 py-6">{children}</div>
    </main>
  );
}
