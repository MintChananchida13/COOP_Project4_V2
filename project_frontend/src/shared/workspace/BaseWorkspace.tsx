"use client";

import React from "react";
import PageNavigator from "./PageNavigator";

export interface WorkspacePage {
  id?: string | number;
  src: string;
  label?: string;
}

interface BaseWorkspaceProps {
  pages: WorkspacePage[];
  currentPage: number;
  onPageChange: (pageIndex: number) => void;
  children: React.ReactNode;
  title?: string;
  actions?: React.ReactNode;
}

export default function BaseWorkspace({
  pages,
  currentPage,
  onPageChange,
  children,
  title = "Workspace",
  actions,
}: BaseWorkspaceProps) {
  return (
    <div className="max-w-7xl mx-auto space-y-5 pb-20">
      <div className="bg-white border border-slate-200 rounded-xl px-4 py-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between shadow-sm">
        <div>
          <h2 className="text-sm font-black text-slate-800">{title}</h2>
          <p className="text-[11px] font-semibold text-slate-400">
            Page {Math.min(currentPage + 1, Math.max(pages.length, 1))} of {Math.max(pages.length, 1)}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <PageNavigator pages={pages} currentPage={currentPage} onPageChange={onPageChange} />
          {actions}
        </div>
      </div>
      {children}
    </div>
  );
}
