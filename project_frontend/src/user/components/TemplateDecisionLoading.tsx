"use client";

import { useSyncExternalStore } from "react";

// One instance per workspace. Only the loading component subscribes, so changing
// the message does not schedule a render of HomeWorkspace.
export function createTemplateDecisionStatus() {
  let status = "";
  const listeners = new Set<() => void>();
  return {
    getSnapshot: () => status,
    getServerSnapshot: () => "",
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
    set(nextStatus: string) {
      if (status === nextStatus) return;
      status = nextStatus;
      listeners.forEach(listener => listener());
    },
  };
}

export default function TemplateDecisionLoading({ status }: {
  status: ReturnType<typeof createTemplateDecisionStatus>;
}) {
  const templateDecisionStatus = useSyncExternalStore(
    status.subscribe, status.getSnapshot, status.getServerSnapshot,
  );
  return (
    <div data-detection-loading="true" className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/45 px-4 backdrop-blur-sm">
      <section className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 text-center shadow-2xl">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-indigo-50">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-indigo-200 border-t-indigo-600" />
        </div>
        <h2 className="mt-5 text-base font-black text-slate-950">กำลังค้นหา Template</h2>
        <p className="mt-2 text-sm font-semibold leading-relaxed text-slate-500">
          ระบบกำลังวิเคราะห์เอกสารและเลือก Template ที่เหมาะสมที่สุด
        </p>
        <div className="mt-4 rounded-xl border border-slate-100 bg-slate-50 px-4 py-3 text-xs font-bold text-slate-700">
          {templateDecisionStatus || "กำลังประมวลผล..."}
        </div>
      </section>
    </div>
  );
}
