"use client";

import { useMemo, useState } from "react";
import BaseWorkspace, { WorkspacePage } from "./BaseWorkspace";
import RoiCheckboxPanel from "./RoiCheckboxPanel";
import RoiLayer from "./RoiLayer";
import { WorkspaceRoi } from "./RoiBox";
import WorkspaceCanvas from "./WorkspaceCanvas";

interface WorkspaceTemplateViewerProps {
  pages: WorkspacePage[];
  currentPage: number;
  onPageChange: (pageIndex: number) => void;
  fields: WorkspaceRoi[];
  onRunOCRSelected?: (fieldIds: number[]) => void;
}

export default function WorkspaceTemplateViewer({
  pages,
  currentPage,
  onPageChange,
  fields,
  onRunOCRSelected,
}: WorkspaceTemplateViewerProps) {
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [checkedIds, setCheckedIds] = useState<Set<number>>(
    () => new Set(fields.filter((field) => field.enabled !== false).map((field) => field.id))
  );

  const currentImage = pages[currentPage]?.src || "";
  const selectedFieldIds = useMemo(() => Array.from(checkedIds), [checkedIds]);

  const handleCheckedChange = (fieldId: number, checked: boolean) => {
    setCheckedIds((prev) => {
      const next = new Set(prev);
      if (checked) {
        next.add(fieldId);
      } else {
        next.delete(fieldId);
      }
      return next;
    });
  };

  return (
    <BaseWorkspace
      pages={pages}
      currentPage={currentPage}
      onPageChange={onPageChange}
      title="Template Viewer"
      actions={
        <button
          type="button"
          disabled={selectedFieldIds.length === 0}
          onClick={() => onRunOCRSelected?.(selectedFieldIds)}
          className="px-4 py-2 rounded-lg bg-blue-600 text-white text-xs font-black disabled:bg-slate-300"
        >
          OCR Selected Fields
        </button>
      }
    >
      <div className="flex gap-5 h-[620px] items-stretch">
        <WorkspaceCanvas imageSrc={currentImage} className="h-full">
          <RoiLayer
            rois={fields}
            currentPage={currentPage}
            selectedId={selectedId}
            checkedIds={checkedIds}
            readonly
            showLabels
            onSelect={setSelectedId}
          />
        </WorkspaceCanvas>
        <RoiCheckboxPanel
          fields={fields}
          currentPage={currentPage}
          checkedIds={checkedIds}
          onCheckedChange={handleCheckedChange}
          onSelect={setSelectedId}
        />
      </div>
    </BaseWorkspace>
  );
}
