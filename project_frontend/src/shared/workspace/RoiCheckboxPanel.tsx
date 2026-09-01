"use client";

import { WorkspaceRoi } from "./RoiBox";

interface RoiCheckboxPanelProps {
  fields: WorkspaceRoi[];
  currentPage: number;
  checkedIds: Set<number>;
  onCheckedChange: (fieldId: number, checked: boolean) => void;
  onSelect?: (fieldId: number) => void;
}

export default function RoiCheckboxPanel({
  fields,
  currentPage,
  checkedIds,
  onCheckedChange,
  onSelect,
}: RoiCheckboxPanelProps) {
  const pageFields = fields.filter((field) => (field.pageIndex !== undefined ? Number(field.pageIndex) : 0) === currentPage);

  return (
    <aside className="w-80 shrink-0 bg-white border border-slate-200 p-4 rounded-xl shadow-sm h-full overflow-y-auto">
      <h3 className="text-xs font-black text-slate-700 uppercase tracking-wider">Template Fields</h3>
      <div className="mt-3 space-y-2">
        {pageFields.length === 0 && (
          <p className="text-xs font-semibold text-slate-400">No fields on this page.</p>
        )}
        {pageFields.map((field) => {
          const checked = checkedIds.has(field.id);
          return (
            <label
              key={field.id}
              className={`flex items-center gap-3 rounded-lg border p-2.5 text-xs font-bold cursor-pointer transition-all ${
                checked ? "border-indigo-200 bg-indigo-50 text-slate-800" : "border-slate-200 bg-white text-slate-500"
              }`}
              onMouseEnter={() => onSelect?.(field.id)}
            >
              <input
                type="checkbox"
                checked={checked}
                onChange={(event) => onCheckedChange(field.id, event.target.checked)}
                className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
              />
              <span className="min-w-0 truncate">{field.fieldName}</span>
            </label>
          );
        })}
      </div>
    </aside>
  );
}
