"use client";

import { WorkspacePage } from "./BaseWorkspace";

interface PageNavigatorProps {
  pages: WorkspacePage[];
  currentPage: number;
  onPageChange: (pageIndex: number) => void;
  disabled?: boolean;
}

export default function PageNavigator({ pages, currentPage, onPageChange, disabled = false }: PageNavigatorProps) {
  const pageCount = pages.length;

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        disabled={disabled || currentPage <= 0}
        onClick={() => onPageChange(currentPage - 1)}
        className="px-3 py-2 rounded-lg border border-slate-200 bg-white text-xs font-bold text-slate-600 disabled:opacity-35 hover:bg-slate-50"
      >
        Prev
      </button>
      <div className="text-xs font-black text-slate-700 min-w-20 text-center">
        {pageCount === 0 ? "No pages" : `${currentPage + 1} / ${pageCount}`}
      </div>
      <button
        type="button"
        disabled={disabled || currentPage >= pageCount - 1}
        onClick={() => onPageChange(currentPage + 1)}
        className="px-3 py-2 rounded-lg border border-slate-200 bg-white text-xs font-bold text-slate-600 disabled:opacity-35 hover:bg-slate-50"
      >
        Next
      </button>
    </div>
  );
}
