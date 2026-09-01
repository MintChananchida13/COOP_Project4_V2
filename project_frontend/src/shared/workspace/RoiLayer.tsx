"use client";

import RoiBox, { WorkspaceRoi } from "./RoiBox";

interface RoiLayerProps {
  rois: WorkspaceRoi[];
  currentPage: number;
  selectedId?: number | null;
  checkedIds?: Set<number>;
  readonly?: boolean;
  editable?: boolean;
  showLabels?: boolean;
  onSelect?: (id: number) => void;
}

export default function RoiLayer({
  rois,
  currentPage,
  selectedId = null,
  checkedIds,
  readonly = true,
  editable = false,
  showLabels = true,
  onSelect,
}: RoiLayerProps) {
  const pageRois = rois.filter((roi) => (roi.pageIndex !== undefined ? Number(roi.pageIndex) : 0) === currentPage);

  return (
    <div className="absolute inset-0 pointer-events-auto">
      {pageRois.map((roi, index) => {
        const checked = checkedIds ? checkedIds.has(roi.id) : roi.enabled !== false;
        const roiWithOptionalContext = roi as WorkspaceRoi & {
          kind?: string;
          pageNumber?: number;
          page_number?: number;
        };
        const compositeKey = `${roiWithOptionalContext.kind ?? roi.type ?? "roi"}-${
          roiWithOptionalContext.pageNumber ?? roiWithOptionalContext.page_number ?? currentPage + 1
        }-${roi.id}-${index}`;
        return (
          <RoiBox
            key={compositeKey}
            roi={roi}
            selected={selectedId === roi.id}
            checked={checked}
            dimmed={!checked}
            readonly={readonly}
            editable={editable}
            showLabel={showLabels}
            onSelect={onSelect}
          />
        );
      })}
    </div>
  );
}
