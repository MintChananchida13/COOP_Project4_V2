"use client";

import React, { useState, useRef, useEffect, useMemo } from 'react';
import { Square, Trash2, Move, Hand, X, ArrowLeft, ZoomIn, ZoomOut, Maximize2, Cpu, FileText, Table, Image as ImageIcon, PenTool, ChevronUp, ChevronDown, Eye, EyeOff, Undo2, Redo2, Loader2, ScanSearch } from 'lucide-react';
import { Rnd } from "react-rnd";
import { ROI } from '../../types/ocr';
import { WorkspaceImageMetrics } from './roiGeometry';
import { ADMIN_API_BASE_URL } from "@/admin/adminApi";
import { authHeaders } from "@/auth/session";

const renderTypeIcon = (type?: 'text' | 'table' | 'image', size = 11) => {
  if (type === 'table') return <Table size={size} className="shrink-0" />;
  if (type === 'image') return <ImageIcon size={size} className="shrink-0" />;
  return <FileText size={size} className="shrink-0" />;
};

const roiTypePatch = (type: 'text' | 'table' | 'image'): Partial<ROI> => ({
  type,
  dataType: type,
  extractionMethod: type === 'image' ? 'extract_image' : type === 'table' ? 'table_recognition_v2' : 'paddle_thai_ocr',
});

const createWorkspaceRoiId = () => {
  const randomPart = Math.floor(Math.random() * 1_000_000);
  return Date.now() * 1000 + randomPart;
};

const normalizeRoiMetadata = (roi: ROI): ROI => {
  const type =
    roi.type === 'table' || roi.dataType === 'table' || roi.extractionMethod === 'ocr_table' || roi.extractionMethod === 'table_recognition_v2'
      ? 'table'
      : roi.type === 'image' || roi.dataType === 'image' || roi.extractionMethod === 'extract_image'
        ? 'image'
        : 'text';

  return {
    ...roi,
    ...roiTypePatch(type),
  };
};

interface LayoutDetectedRegion {
  field_name?: string;
  type?: "text" | "table" | "image";
  data_type?: "text" | "table" | "image";
  extraction_method?: "paddle_thai_ocr" | "table_recognition_v2" | "extract_image" | "ocr_text" | "ocr_table";
  confidence?: number;
  auto_roi_group?: {
    mode?: "text_line";
    line_count?: number;
  } | null;
  roi?: {
    page_number?: number;
    x_ratio?: number;
    y_ratio?: number;
    width_ratio?: number;
    height_ratio?: number;
  };
}

interface LayoutDetectedPage {
  page_index: number;
  page_number: number;
  image_width: number;
  image_height: number;
  regions: LayoutDetectedRegion[];
  message?: string | null;
}

interface LayoutAnalysisResponse {
  success?: boolean;
  pages?: LayoutDetectedPage[];
  detail?: string;
  error?: string;
}

const WORKSPACE_ZOOM_STEPS = [0.25, 0.33, 0.5, 0.67, 0.75, 0.85, 1.0, 1.1, 1.2, 1.33, 1.5, 1.75, 2.0, 2.5, 3.0];
const WORKSPACE_DEFAULT_ZOOM_INDEX = WORKSPACE_ZOOM_STEPS.findIndex((step) => step === 1.0);
const WORKSPACE_LOCKED_HEIGHT_CLASS = "h-[calc(100vh-220px)] min-h-[640px]";
const WORKSPACE_MAX_FIT_ZOOM = 3.0;

export interface WorkspaceCustomEditorProps {
  previewUrl: string;
  image: string | null;
  brightness: number;
  contrast: number;
  rotation: number;
  rois: (ROI & { pageIndex?: number })[]; 
  setRois: React.Dispatch<React.SetStateAction<(ROI & { pageIndex?: number })[]>>;
  selectedId: number | null;
  setSelectedId: React.Dispatch<React.SetStateAction<number | null>>;
  onBackToAdjust: () => void;
  deleteROI: (id: number) => void;
  isLoading: boolean;
  onRunOCR: (scaleX: number, scaleY: number) => void;
  onRunFullPageOCR: () => Promise<void>;
  ocrProgress?: {
    currentPage: number;
    totalPages: number;
    completedPages?: number;
  } | null;
  currentIndex: number;
  imagesList: string[]; 
  onIndexChange: (index: number) => void;
  hideOcrActions?: boolean;
  readOnly?: boolean;
  hideStepProgress?: boolean;
  hideRightPanel?: boolean;
  hideFooter?: boolean;
  hideFooterActions?: boolean;
  hideDrawTools?: boolean;
  lockRoiMetadata?: boolean;
  workspaceHeightClassName?: string;
  rootClassName?: string;
  centerCanvas?: boolean;
  fitImageToViewport?: boolean;
  imageFrameClassName?: string;
  layoutVariant?: "default" | "user";
  rightPanelClassName?: string;
  rightPanelTopContent?: React.ReactNode;
  rightPanelRenderer?: (api: {
    currentPageRois: (ROI & { pageIndex?: number })[];
    selectedId: number | null;
    setSelectedId: React.Dispatch<React.SetStateAction<number | null>>;
    updateROI: (id: number, fields: Partial<ROI>) => void;
    deleteROI: (id: number) => void;
    moveROI: (index: number, direction: 'up' | 'down') => void;
    triggerOCRProcessing: () => void;
  }) => React.ReactNode;
  toolbarExtra?: React.ReactNode;
  canvasActionRenderer?: (api: {
    currentPageRois: (ROI & { pageIndex?: number })[];
    selectedId: number | null;
    triggerOCRProcessing: () => void;
  }) => React.ReactNode;
  getRoiClassName?: (roi: ROI & { pageIndex?: number }, selected: boolean, activeTool: 'pan' | 'box' | 'polygon') => string;
  getRoiLabelClassName?: (roi: ROI & { pageIndex?: number }, selected: boolean) => string;
  getRoiLabelText?: (roi: ROI & { pageIndex?: number }) => string;
  getRoiBadges?: (roi: ROI & { pageIndex?: number }) => string[];
  allowedRoiTypes?: Array<"text" | "table" | "image">;
  onImageMetricsChange?: (metrics: WorkspaceImageMetrics) => void;
  onCanvasRoiSelect?: (roiId: number) => void;
}

export default function WorkspaceCustomEditor({
  previewUrl,
  rois,
  setRois,
  selectedId,
  setSelectedId,
  onBackToAdjust,
  deleteROI,
  isLoading,
  onRunOCR,
  onRunFullPageOCR,
  ocrProgress,
  currentIndex,
  imagesList,    
  onIndexChange,
  hideOcrActions = false,
  readOnly = false,
  hideRightPanel = false,
  hideFooter = false,
  hideFooterActions = false,
  hideDrawTools = false,
  lockRoiMetadata = false,
  workspaceHeightClassName = WORKSPACE_LOCKED_HEIGHT_CLASS,
  rootClassName = "max-w-7xl mx-auto space-y-6 pb-20",
  centerCanvas = true,
  fitImageToViewport = true,
  imageFrameClassName = "w-[750px]",
  layoutVariant = "user",
  rightPanelClassName,
  rightPanelTopContent,
  rightPanelRenderer,
  toolbarExtra,
  canvasActionRenderer,
  getRoiClassName,
  getRoiLabelClassName,
  getRoiLabelText,
  getRoiBadges,
  allowedRoiTypes = ["text", "table", "image"],
  onImageMetricsChange,
  onCanvasRoiSelect,
}: WorkspaceCustomEditorProps) {
  const isUserLayout = layoutVariant === "user";
  const [activeTool, setActiveTool] = useState<'pan' | 'box' | 'polygon'>(readOnly || hideDrawTools ? 'pan' : 'box');
  const [activeDrawPoints, setActiveDrawPoints] = useState<{ x: number; y: number }[]>([]);

  // Calculate the bounding box for custom ROI points.
  const getBoundingBoxOfPoints = (points: { x: number; y: number }[]) => {
    if (points.length === 0) return { x: 0, y: 0, width: 0, height: 0 };
    const xs = points.map(p => p.x);
    const ys = points.map(p => p.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    return {
      x: minX,
      y: minY,
      width: maxX - minX,
      height: maxY - minY
    };
  };
  const [isDrawing, setIsDrawing] = useState(false);
  const [startPos, setStartPos] = useState({ x: 0, y: 0 });
  const [dragBox, setDragBox] = useState<{ x: number; y: number; w: number; h: number } | null>(null);

  // Standard zoom levels.
  const [zoomIndex, setZoomIndex] = useState<number>(WORKSPACE_DEFAULT_ZOOM_INDEX);
  const [fitZoom, setFitZoom] = useState<number | null>(null);
  const currentZoom = fitZoom ?? WORKSPACE_ZOOM_STEPS[zoomIndex];
  const [isAutoDetectingRoi, setIsAutoDetectingRoi] = useState(false);
  const [autoDetectMessage, setAutoDetectMessage] = useState("");

  const [isPanning, setIsPanning] = useState(false);
  const [panStart, setPanStart] = useState({ scrollLeft: 0, scrollTop: 0, clientX: 0, clientY: 0 });

  const imageRef = useRef<HTMLImageElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const defaultRightPanelScrollRef = useRef<HTMLDivElement | null>(null);
  const defaultRightPanelRoiRefs = useRef<Map<number, HTMLDivElement>>(new Map());

  const reportImageMetrics = React.useCallback(() => {
    if (!imageRef.current || !containerRef.current || !onImageMetricsChange) return;
    const imageElement = imageRef.current;
    const imageWidth = imageElement.clientWidth || imageElement.naturalWidth || 1;
    const imageHeight =
      imageElement.clientHeight ||
      (imageElement.naturalWidth > 0 ? (imageElement.naturalHeight / imageElement.naturalWidth) * imageWidth : 1);

    onImageMetricsChange({
      imageOffsetX: 0,
      imageOffsetY: 0,
      imageWidth,
      imageHeight,
      naturalWidth: imageElement.naturalWidth,
      naturalHeight: imageElement.naturalHeight,
    });
  }, [onImageMetricsChange]);

  const fitCurrentImageToViewport = React.useCallback(() => {
    if (!fitImageToViewport || !viewportRef.current || !containerRef.current || !imageRef.current) return;
    const viewport = viewportRef.current;
    const baseWidth = containerRef.current.offsetWidth || imageRef.current.clientWidth;
    const baseHeight = containerRef.current.offsetHeight || imageRef.current.clientHeight;
    if (!Number.isFinite(baseWidth) || !Number.isFinite(baseHeight) || baseWidth <= 0 || baseHeight <= 0) return;

    const availableWidth = Math.max(120, viewport.clientWidth - 32);
    const targetZoom = Math.min(WORKSPACE_MAX_FIT_ZOOM, availableWidth / baseWidth);
    const nextZoom = Math.max(WORKSPACE_ZOOM_STEPS[0], targetZoom);
    setFitZoom((prev) => (prev !== null && Math.abs(prev - nextZoom) < 0.001 ? prev : nextZoom));
  }, [fitImageToViewport]);

  // Toggle field labels and keep undo/redo history.
  const [showLabels, setShowLabels] = useState(true);
  const [history, setHistory] = useState<ROI[][]>([]);
  const [historyIndex, setHistoryIndex] = useState<number>(-1);
  const [draggedItemId, setDraggedItemId] = useState<number | null>(null);

  const skipHistoryRecordRef = useRef(false);
  const lastRoisRef = useRef<ROI[]>([]);

  // Track ROI history when the page changes.
  useEffect(() => {
    setHistory([rois]);
    setHistoryIndex(0);
    lastRoisRef.current = rois;
  }, [currentIndex]);

  // Track ROI history when the page changes.
  useEffect(() => {
    if (skipHistoryRecordRef.current) {
      skipHistoryRecordRef.current = false;
      lastRoisRef.current = rois;
      return;
    }
    
    // Record only real ROI changes to avoid loops.
    if (JSON.stringify(rois) !== JSON.stringify(lastRoisRef.current)) {
      const newHistory = history.slice(0, historyIndex + 1);
      setHistory([...newHistory, rois]);
      setHistoryIndex(newHistory.length);
      lastRoisRef.current = rois;
    }
  }, [rois, history, historyIndex]);

  const handleUndo = () => {
    if (historyIndex > 0) {
      const prevIndex = historyIndex - 1;
      skipHistoryRecordRef.current = true;
      setHistoryIndex(prevIndex);
      setRois(history[prevIndex]);
      setSelectedId(null);
    }
  };

  const handleRedo = () => {
    if (historyIndex < history.length - 1) {
      const nextIndex = historyIndex + 1;
      skipHistoryRecordRef.current = true;
      setHistoryIndex(nextIndex);
      setRois(history[nextIndex]);
      setSelectedId(null);
    }
  };

  const createPolygonRoi = (points: { x: number; y: number }[]) => {
    if (points.length < 3) return;
    const bbox = getBoundingBoxOfPoints(points);
    if (bbox.width <= 1 || bbox.height <= 1) return;
    const newBox = {
      id: createWorkspaceRoiId(),
      fieldName: `field_${rois.length + 1}`,
      x: bbox.x,
      y: bbox.y,
      width: bbox.width,
      height: bbox.height,
      pageIndex: currentIndex,
      type: 'text' as const,
      dataType: 'text' as const,
      extractionMethod: 'paddle_thai_ocr' as const,
      points,
    };
    setSelectedId(newBox.id);
    setRois(prev => [...prev, newBox]);
    setActiveDrawPoints([]);
  };


  useEffect(() => {
    setSelectedId(null);
  }, [currentIndex]);

      // Delete selected ROI.
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const activeEl = document.activeElement;
      const isInputActive = activeEl && (activeEl.tagName === 'INPUT' || activeEl.tagName === 'TEXTAREA');

      // Delete selected ROI.
      if (!isInputActive && (e.key === 'Delete' || e.key === 'Backspace')) {
        if (selectedId !== null) {
          e.preventDefault();
          deleteROI(selectedId);
          setSelectedId(null);
        }
      }

      // Undo.
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z' && !e.shiftKey) {
        if (isInputActive) return;
        e.preventDefault();
        handleUndo();
      }

      // Redo.
      if (
        ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') ||
        ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'z')
      ) {
        if (isInputActive) return;
        e.preventDefault();
        handleRedo();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [selectedId, handleUndo, handleRedo, deleteROI]);


  useEffect(() => {
    if (imageRef.current && previewUrl) {
      imageRef.current.src = previewUrl;
      reportImageMetrics();
    }
  }, [previewUrl, currentIndex, reportImageMetrics]);

  useEffect(() => {
    const handleResize = () => {
      fitCurrentImageToViewport();
      reportImageMetrics();
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [fitCurrentImageToViewport, onImageMetricsChange, reportImageMetrics]);

  useEffect(() => {
    if (!fitImageToViewport) return;
    window.requestAnimationFrame(() => {
      fitCurrentImageToViewport();
      reportImageMetrics();
    });
  }, [currentIndex, fitCurrentImageToViewport, fitImageToViewport, previewUrl, reportImageMetrics]);

  useEffect(() => {
    window.requestAnimationFrame(reportImageMetrics);
  }, [currentZoom, reportImageMetrics]);

  const currentPageRois = useMemo(() => {
    return rois.filter(roi => {
      const roiPage = roi.pageIndex !== undefined ? Number(roi.pageIndex) : 0;
      return roiPage === Number(currentIndex);
    });
  }, [rois, currentIndex]);

  const scrollDefaultRightPanelToRoi = (roiId: number) => {
    const container = defaultRightPanelScrollRef.current;
    const target = defaultRightPanelRoiRefs.current.get(roiId);
    if (!container || !target) return;

    const containerRect = container.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const relativeTop = targetRect.top - containerRect.top + container.scrollTop;
    const top = relativeTop - Math.max(0, (container.clientHeight - targetRect.height) / 2);
    container.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
  };

  const selectRoiFromCanvas = (roiId: number) => {
    setSelectedId(roiId);
    onCanvasRoiSelect?.(roiId);
    window.setTimeout(() => scrollDefaultRightPanelToRoi(roiId), 0);
  };

  const renderPagePagination = () => (
    <div className="w-full rounded-xl border border-slate-200 bg-[#edf2f7] px-4 py-3 text-slate-800 shadow-sm select-none">
      <div className="flex flex-col items-center justify-between gap-3 sm:flex-row">
        <div className="text-xs font-bold uppercase tracking-wider text-slate-500">
          หน้าปัจจุบัน:
          <span className="ml-1 font-mono text-sm font-bold text-slate-800">
            {currentIndex + 1} / {imagesList.length} หน้า
          </span>
        </div>

        <div className="flex w-full items-center justify-center gap-2 sm:w-auto">
          <button
            type="button"
            disabled={currentIndex === 0 || isLoading}
            onClick={() => onIndexChange(currentIndex - 1)}
            className="flex items-center justify-center rounded-xl border border-slate-200 bg-white p-2 text-slate-650 transition-all hover:bg-slate-50 disabled:opacity-30 disabled:hover:bg-white"
          >
            <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
            </svg>
          </button>

          <div className="flex max-w-[320px] items-center gap-2 overflow-x-auto py-0.5 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden">
            {imagesList.map((url, idx) => (
              <button
                key={idx}
                type="button"
                disabled={isLoading}
                onClick={() => onIndexChange(idx)}
                className={`relative h-12 w-9 shrink-0 overflow-hidden rounded-md border shadow-md transition-all ${
                  currentIndex === idx
                    ? "scale-105 border-blue-500 ring-2 ring-blue-500/50"
                    : "border-slate-250 opacity-60 hover:opacity-100"
                }`}
              >
                <img src={url} alt={`Page ${idx + 1}`} className="h-full w-full object-cover" />
              </button>
            ))}
          </div>

          <button
            type="button"
            disabled={currentIndex === imagesList.length - 1 || isLoading}
            onClick={() => onIndexChange(currentIndex + 1)}
            className="flex items-center justify-center rounded-xl border border-slate-200 bg-white p-2 text-slate-650 transition-all hover:bg-slate-50 disabled:opacity-30 disabled:hover:bg-white"
          >
            <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );

  const handleZoomIn = () => {
    const nextIndex = WORKSPACE_ZOOM_STEPS.findIndex((step) => step > currentZoom + 0.001);
    if (nextIndex >= 0) {
      setFitZoom(null);
      setZoomIndex(nextIndex);
    }
  };

  const handleZoomOut = () => {
    let nextIndex = -1;
    for (let index = WORKSPACE_ZOOM_STEPS.length - 1; index >= 0; index -= 1) {
      if (WORKSPACE_ZOOM_STEPS[index] < currentZoom - 0.001) {
        nextIndex = index;
        break;
      }
    }
    if (nextIndex >= 0) {
      setFitZoom(null);
      setZoomIndex(nextIndex);
    }
  };

  const handleDoubleClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (readOnly) return;
    if (activeTool === 'polygon' && activeDrawPoints.length >= 3) {
      e.preventDefault();
      e.stopPropagation();
      createPolygonRoi(activeDrawPoints);
    }
  };

  const handleMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!containerRef.current || !viewportRef.current) return;

    if (readOnly || activeTool === 'pan' || e.button === 1) {
      setIsPanning(true);
      setPanStart({
        scrollLeft: viewportRef.current.scrollLeft,
        scrollTop: viewportRef.current.scrollTop,
        clientX: e.clientX,
        clientY: e.clientY
      });
      return;
    }

    const isTargetBox = (e.target as HTMLElement).closest('.rnd-box-item');
    if (isTargetBox) return;

    e.preventDefault(); 
    e.stopPropagation();
    setSelectedId(null); 

    const rect = e.currentTarget.getBoundingClientRect();
    const x = (e.clientX - rect.left) / currentZoom;
    const y = (e.clientY - rect.top) / currentZoom;

    if (activeTool === 'polygon') {
      const newPoint = { x, y };
      const updatedPoints = [...activeDrawPoints, newPoint];
      
      if (updatedPoints.length >= 4) {
        const firstPoint = updatedPoints[0];
        const dist = Math.sqrt((x - firstPoint.x) ** 2 + (y - firstPoint.y) ** 2);
        if (dist < 12) {
          createPolygonRoi(updatedPoints.slice(0, -1));
          return;
        }
      }
      setActiveDrawPoints(updatedPoints);
      return;
    }

    if (activeTool === 'box') {
      setIsDrawing(true);
      setStartPos({ x, y });
      setDragBox({ x, y, w: 0, h: 0 });
    }
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (isPanning && viewportRef.current) {
      const dx = e.clientX - panStart.clientX;
      const dy = e.clientY - panStart.clientY;
      viewportRef.current.scrollLeft = panStart.scrollLeft - dx;
      viewportRef.current.scrollTop = panStart.scrollTop - dy;
      return;
    }

    if (!isDrawing || !dragBox || !containerRef.current) return;
    
    const rect = containerRef.current.getBoundingClientRect();
    const currentX = (e.clientX - rect.left) / currentZoom;
    const currentY = (e.clientY - rect.top) / currentZoom;

    setDragBox({
      x: Math.min(startPos.x, currentX),
      y: Math.min(startPos.y, currentY),
      w: Math.abs(startPos.x - currentX),
      h: Math.abs(startPos.y - currentY)
    });
  };

  const handleMouseUp = () => {
    if (isPanning) {
      setIsPanning(false);
      return;
    }

    if (!isDrawing || !dragBox) return;
    setIsDrawing(false);

    if (dragBox.w > 5 && dragBox.h > 5) {
      const newBox = {
        id: createWorkspaceRoiId(),
        fieldName: `field_${rois.length + 1}`,
        x: dragBox.x,
        y: dragBox.y,
        width: dragBox.w,
        height: dragBox.h,
        pageIndex: currentIndex,
        ...roiTypePatch('text'),
      };
      setSelectedId(newBox.id);
      setRois(prev => [...prev, newBox]);
    } else {
      setSelectedId(null);
    }
    setDragBox(null);
    if (!readOnly) setActiveTool('box');
  };

  const updateROI = (id: number, fields: Partial<ROI>) => {
    setRois(prev => prev.map(roi => {
      if (roi.id !== id) return roi;
      
      let updatedPoints = roi.points ? [...roi.points] : undefined;
      
      if (roi.points && roi.points.length > 0) {
        const oldX = roi.x;
        const oldY = roi.y;
        const oldW = roi.width;
        const oldH = roi.height;
        
        const newX = fields.x !== undefined ? fields.x : oldX;
        const newY = fields.y !== undefined ? fields.y : oldY;
        const newW = fields.width !== undefined ? fields.width : oldW;
        const newH = fields.height !== undefined ? fields.height : oldH;
        
        const dx = newX - oldX;
        const dy = newY - oldY;
        const scaleX = oldW > 0 ? newW / oldW : 1;
        const scaleY = oldH > 0 ? newH / oldH : 1;
        
        updatedPoints = roi.points.map(p => {
          const relX = p.x - oldX;
          const relY = p.y - oldY;
          return {
            x: oldX + relX * scaleX + dx,
            y: oldY + relY * scaleY + dy
          };
        });
      }
      
      return normalizeRoiMetadata({ ...roi, ...fields, points: updatedPoints });
    }));
  };

  const reorderCurrentPageRoi = (draggedRoiId: number, targetRoiId: number) => {
    if (draggedRoiId === targetRoiId) return;
    setRois(prev => {
      const currentPageItems = prev
        .map((roi, originalIdx) => ({ roi, originalIdx }))
        .filter(item => (item.roi.pageIndex !== undefined ? Number(item.roi.pageIndex) : 0) === currentIndex);
      const fromPageIndex = currentPageItems.findIndex(item => item.roi.id === draggedRoiId);
      const toPageIndex = currentPageItems.findIndex(item => item.roi.id === targetRoiId);
      if (fromPageIndex < 0 || toPageIndex < 0 || fromPageIndex === toPageIndex) return prev;
      const nextPageItems = [...currentPageItems];
      const [draggedItem] = nextPageItems.splice(fromPageIndex, 1);
      nextPageItems.splice(toPageIndex, 0, draggedItem);
      const next = [...prev];
      currentPageItems.forEach((item, index) => {
        next[item.originalIdx] = nextPageItems[index].roi;
      });
      return next;
    });
  };

  const moveROI = (index: number, direction: 'up' | 'down') => {
    const targetIndex = direction === 'up' ? index - 1 : index + 1;
    const source = currentPageRois[index];
    const target = currentPageRois[targetIndex];
    if (!source || !target) return;
    reorderCurrentPageRoi(source.id, target.id);
  };

  // Handle drag-and-drop ordering in the right panel.
  const handleDragStart = (e: React.DragEvent, roiId: number) => {
    setDraggedItemId(roiId);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', String(roiId));
  };

  const handleDragOver = (e: React.DragEvent, hoverRoiId: number) => {
    e.preventDefault();
    const draggedId = draggedItemId ?? Number(e.dataTransfer.getData('text/plain'));
    if (!Number.isFinite(draggedId) || draggedId === hoverRoiId) return;
    reorderCurrentPageRoi(draggedId, hoverRoiId);
  };

  const handleDragEnd = () => {
    setDraggedItemId(null);
  };

  const handleStyle = {
    width: "8px",
    height: "8px",
    background: "#ffffff",
    border: "1.5px solid #2563eb",
    borderRadius: "2px",
    boxShadow: "0 1px 2px rgba(0,0,0,0.2)"
  };


  const triggerOCRProcessing = () => {
    if (!imageRef.current) return;

    const scaleX = imageRef.current.naturalWidth / imageRef.current.clientWidth;
    const scaleY = imageRef.current.naturalHeight / imageRef.current.clientHeight;

    onRunOCR(scaleX, scaleY);
  };

  const layoutRegionToWorkspaceRoi = (
    region: LayoutDetectedRegion,
    page: LayoutDetectedPage,
    index: number,
    fieldNumber: number
  ): (ROI & { pageIndex?: number }) | null => {
    const roi = region.roi;
    if (!roi) return null;

    const xRatio = Number(roi.x_ratio);
    const yRatio = Number(roi.y_ratio);
    const widthRatio = Number(roi.width_ratio);
    const heightRatio = Number(roi.height_ratio);
    if (![xRatio, yRatio, widthRatio, heightRatio].every(Number.isFinite)) return null;

    const pageIndex = Number.isFinite(Number(page.page_index)) ? Number(page.page_index) : Math.max(0, Number(page.page_number || 1) - 1);
    const displayWidth = 750;
    const displayHeight = page.image_width > 0 ? (page.image_height / page.image_width) * displayWidth : 1000;
    const type = region.type === "table" || region.type === "image" ? region.type : "text";
    const extractionMethod = type === "image" ? "extract_image" : type === "table" ? "table_recognition_v2" : "paddle_thai_ocr";

    const width = widthRatio * displayWidth;
    const height = heightRatio * displayHeight;
    if (width < 4 || height < 4) return null;

    return {
      id: createWorkspaceRoiId() + pageIndex * 1000000 + index,
      fieldName: `field_${fieldNumber}`,
      x: xRatio * displayWidth,
      y: yRatio * displayHeight,
      width,
      height,
      pageIndex,
      type,
      dataType: type,
      extractionMethod,
      confidence: typeof region.confidence === "number" ? region.confidence : undefined,
      role: "data_extraction",
      enabled: true,
    };
  };

  const handleAutoDetectRoi = async () => {
    if (!previewUrl || isAutoDetectingRoi) return;

    setIsAutoDetectingRoi(true);
    setAutoDetectMessage("");

    try {
      const pagesToAnalyze = imagesList.length > 0 ? imagesList : [previewUrl];
      const response = await fetch(`${ADMIN_API_BASE_URL}/api/layout/analyze`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          auto_roi_mode: "text_line",
          images: pagesToAnalyze.map((imageSrc, pageIndex) => ({
            page_index: pageIndex,
            image: imageSrc,
          })),
        }),
      });
      const responseText = await response.text();
      let result: LayoutAnalysisResponse = {};
      try {
        result = responseText ? JSON.parse(responseText) as LayoutAnalysisResponse : {};
      } catch {
        result = { error: responseText || response.statusText };
      }

      if (!response.ok || !result?.success) {
        if (response.status === 404) {
          throw new Error("ไม่พบ endpoint /api/layout/analyze กรุณา restart backend และตรวจสอบ PaddleOCR Layout Analysis service");
        }
        throw new Error(result?.detail || result?.error || "วิเคราะห์ Layout เพื่อสร้าง ROI ไม่สำเร็จ");
      }

      let fieldNumber = 1;
      const detectedRois = (result.pages || []).flatMap((page) =>
        (page.regions || [])
          .map((region, index) => {
            const nextRoi = layoutRegionToWorkspaceRoi(region, page, index, fieldNumber);
            if (nextRoi) fieldNumber += 1;
            return nextRoi;
          })
          .filter((roi): roi is ROI & { pageIndex?: number } => roi !== null)
      );
      const processedPages = new Set((result.pages || []).map((page) => Number(page.page_index)));
      const emptyPages = (result.pages || [])
        .filter((page) => (page.regions || []).length === 0)
        .map((page) => Number(page.page_index) + 1);

      if (detectedRois.length === 0) {
        setRois((prev) =>
          prev.filter((roi) => {
            const roiPage = roi.pageIndex !== undefined ? Number(roi.pageIndex) : 0;
            return !processedPages.has(roiPage);
          })
        );
        setSelectedId(null);
        setAutoDetectMessage("PaddleOCR ไม่พบ Text, Table หรือ Image Region ที่สามารถสร้าง ROI ได้");
        return;
      }

      const removedCount = rois.filter((roi) => {
        const roiPage = roi.pageIndex !== undefined ? Number(roi.pageIndex) : 0;
        return processedPages.has(roiPage);
      }).length;
      setRois((prev) => {
        const otherPageRois = prev.filter((roi) => {
          const roiPage = roi.pageIndex !== undefined ? Number(roi.pageIndex) : 0;
          return !processedPages.has(roiPage);
        });
        return [...otherPageRois, ...detectedRois];
      });
      setSelectedId(detectedRois[0].id);
      setActiveTool("box");
      setAutoDetectMessage(
        `สร้าง ROI อัตโนมัติ ${detectedRois.length} รายการจาก ${processedPages.size} หน้า และลบ ROI เดิม ${removedCount} รายการ${emptyPages.length ? ` (ไม่พบ Region ในหน้า ${emptyPages.join(", ")})` : ""}`
      );
    } catch (error) {
      console.error("Auto ROI detection failed.", error);
      setAutoDetectMessage(error instanceof Error ? error.message : "วิเคราะห์ Layout เพื่อสร้าง ROI ไม่สำเร็จ");
    } finally {
      setIsAutoDetectingRoi(false);
    }
  };
  return (
    <div className={rootClassName}>
      {/* Main canvas row */}
      <div className={`relative grid min-h-0 ${workspaceHeightClassName} ${hideRightPanel ? "grid-cols-[64px_minmax(0,1fr)]" : "grid-cols-[64px_minmax(0,1fr)_320px] xl:grid-cols-[64px_minmax(0,1fr)_minmax(320px,360px)]"} gap-5 items-stretch overflow-hidden`}>
        
        {/* Left toolbar */}
                <div className="flex h-full flex-col items-center gap-3 rounded-xl border border-slate-200 bg-white py-4 shadow-sm select-none overflow-y-auto">
          <button 
            type="button"
            onClick={() => { setActiveTool('pan'); setSelectedId(null); setActiveDrawPoints([]); }}
            className={`p-2.5 rounded-lg transition-all ${activeTool === 'pan' ? 'bg-indigo-600 text-white shadow-md' : 'text-slate-500 hover:bg-slate-100 hover:text-indigo-600'}`}
            title="Hand Pan Tool (Hand)"
          >
            <Hand size={20} />
          </button>
          {!readOnly && !hideDrawTools && (
            <>
              <button 
                type="button"
                onClick={() => { setActiveTool('box'); setSelectedId(null); setActiveDrawPoints([]); }}
                className={`p-2.5 rounded-lg transition-all ${activeTool === 'box' ? 'bg-indigo-600 text-white shadow-md' : 'text-slate-500 hover:bg-slate-100 hover:text-indigo-600'}`}
                title="Standard Box Tool (Rectangle)"
              >
                <Square size={20} />
              </button>
              <button 
                type="button"
                onClick={() => { setActiveTool('polygon'); setSelectedId(null); setActiveDrawPoints([]); }}
                className={`p-2.5 rounded-lg transition-all ${activeTool === 'polygon' ? 'bg-indigo-600 text-white shadow-md' : 'text-slate-500 hover:bg-slate-100 hover:text-indigo-600'}`}
                title="Freeform Polygon Tool"
              >
                <PenTool size={20} />
              </button>
            </>
          )}

          {!readOnly && !hideDrawTools && <div className="w-8 h-[1px] bg-slate-200 my-2"></div>}

          {/* Undo and redo buttons */}
          {!readOnly && !hideDrawTools && (
            <>
              <button 
                type="button"
                onClick={handleUndo}
                disabled={historyIndex <= 0}
                className="p-2.5 rounded-lg text-slate-500 hover:bg-slate-100 hover:text-indigo-600 disabled:opacity-20 transition-all"
                title="ย้อนกลับ (Ctrl+Z)"
              >
                <Undo2 size={20} />
              </button>
              <button 
                type="button"
                onClick={handleRedo}
                disabled={historyIndex >= history.length - 1}
                className="p-2.5 rounded-lg text-slate-500 hover:bg-slate-100 hover:text-indigo-600 disabled:opacity-20 transition-all"
                title="ทำซ้ำ (Ctrl+Y)"
              >
                <Redo2 size={20} />
              </button>
            </>
          )}

          <div className="w-8 h-[1px] bg-slate-200 my-2"></div>

          {/* Toggle field labels */}
          <button 
            type="button"
            onClick={() => setShowLabels(prev => !prev)}
            className={`p-2.5 rounded-lg transition-all ${!showLabels ? 'bg-amber-100 text-amber-600 border border-amber-250 shadow-sm' : 'text-slate-500 hover:bg-slate-100 hover:text-indigo-600'}`}
            title={showLabels ? "ซ่อนชื่อ Field บนกรอบ ROI" : "แสดงชื่อ Field บนกรอบ ROI"}
          >
            {showLabels ? <Eye size={20} /> : <EyeOff size={20} />}
          </button>

          <div className="w-8 h-[1px] bg-slate-200 my-2"></div>

          <button 
            type="button"
            onClick={handleZoomIn}
            disabled={currentZoom >= WORKSPACE_ZOOM_STEPS[WORKSPACE_ZOOM_STEPS.length - 1] - 0.001}
            className="p-2.5 rounded-lg text-slate-500 hover:bg-slate-100 hover:text-blue-600 disabled:opacity-30 transition-all"
            title={`Zoom In (${Math.round(currentZoom * 100)}%)`}
          >
            <ZoomIn size={20} />
          </button>

          <button 
            type="button"
            onClick={handleZoomOut}
            disabled={currentZoom <= WORKSPACE_ZOOM_STEPS[0] + 0.001}
            className="p-2.5 rounded-lg text-slate-500 hover:bg-slate-100 hover:text-blue-600 disabled:opacity-30 transition-all"
            title={`Zoom Out (${Math.round(currentZoom * 100)}%)`}
          >
            <ZoomOut size={20} />
          </button>

          <button 
            type="button"
            onClick={fitCurrentImageToViewport}
            className="p-2.5 rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-600 transition-all"
            title="Fit image to width"
          >
            <Maximize2 size={16} />
          </button>

          <div className="w-8 h-[1px] bg-slate-200 my-2"></div>
          
          {!readOnly && !hideDrawTools && <button 
            type="button"
            onClick={() => { 
              setRois(prev => prev.filter(roi => {
                const roiPage = roi.pageIndex !== undefined ? Number(roi.pageIndex) : 0;
                return roiPage !== Number(currentIndex);
              })); 
              setSelectedId(null); 
            }}
            className="p-2.5 text-slate-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors"
            title="ลบ ROI ทั้งหมดในหน้านี้"
          >
            <Trash2 size={20} />
          </button>}

          {toolbarExtra}
        </div>

        {/* Center document canvas */}
        <div className="relative flex h-full min-h-0 min-w-0 flex-col overflow-hidden">
          {canvasActionRenderer && (
            <div className="pointer-events-none absolute bottom-4 right-4 z-40 flex w-fit max-w-[calc(100%-2rem)] justify-end">
              <div className="pointer-events-auto">
                {canvasActionRenderer({ currentPageRois, selectedId, triggerOCRProcessing })}
              </div>
            </div>
          )}
          <div 
            ref={viewportRef} 
            className="min-h-0 min-w-0 flex-1 bg-[#edf2f7] border border-slate-200 rounded-xl overflow-auto flex items-start justify-start p-6 shadow-inner relative"
          >
            <div 
              ref={containerRef}
              className={`relative inline-block ${centerCanvas ? "mx-auto" : ""} ${selectedId ? 'cursor-default' : (activeTool === 'box' || activeTool === 'polygon') ? 'cursor-crosshair select-none' : isPanning ? 'cursor-grabbing' : 'cursor-grab'}`} 
              style={{ 
                transform: `scale(${currentZoom})`, 
                transformOrigin: "top left",
                transition: "transform 0.1s ease-out"
              }}
              onMouseDown={handleMouseDown}
              onMouseMove={handleMouseMove}
              onMouseUp={handleMouseUp}
              onMouseLeave={handleMouseUp}
              onDoubleClick={handleDoubleClick}
            >
              <div className={`relative ${imageFrameClassName} h-auto bg-transparent`}>
                {previewUrl && (
                  <img 
                    ref={imageRef}
                    src={previewUrl} 
                    alt="Workspace" 
                    draggable="false" 
                    onLoad={() => {
                      fitCurrentImageToViewport();
                      reportImageMetrics();
                    }}
                    className="w-full h-auto block select-none pointer-events-none border border-slate-300 shadow-xl rounded bg-white"
                  />
                )}
                  
              {isDrawing && dragBox && (
                <div 
                  className="absolute border border-dashed border-indigo-500 bg-indigo-500/10 pointer-events-none z-50" 
                  style={{ left: dragBox.x, top: dragBox.y, width: dragBox.w, height: dragBox.h }} 
                />
              )}

              {/* Temporary drawing overlay for freeform polygon */}
              {activeTool === 'polygon' && activeDrawPoints.length > 0 && (
                <svg className="absolute inset-0 w-full h-full pointer-events-none z-50">
                  <polygon
                    points={activeDrawPoints.map(p => `${p.x},${p.y}`).join(' ')}
                    fill="rgba(99, 102, 241, 0.12)"
                    stroke="#4f46e5"
                    strokeWidth="1.5"
                    strokeDasharray="3,3"
                  />
                  {activeDrawPoints.map((p, idx) => (
                    <circle
                      key={idx}
                      cx={p.x}
                      cy={p.y}
                      r="4"
                      fill="#4f46e5"
                      stroke="white"
                      strokeWidth="1.2"
                    />
                  ))}
                </svg>
              )}
                          
              <div className="absolute inset-0 top-0 left-0 w-full h-full pointer-events-auto">
                {currentPageRois.map((roi, index) => {
                  const hasPoints = roi.points && roi.points.length > 0;
                  const selected = selectedId === roi.id;
                  const customRoiClassName = getRoiClassName?.(roi, selected, activeTool);
                  const roiBadges = getRoiBadges?.(roi) || [];
                  return (
                    <Rnd
                      key={`${roi.type || "roi"}-${roi.pageIndex ?? currentIndex}-${roi.id}-${index}`}
                      size={{ width: roi.width, height: roi.height }}
                      position={{ x: roi.x, y: roi.y }}
                      onMouseDown={(e) => { e.stopPropagation(); selectRoiFromCanvas(roi.id); }}
                      onDragStop={(e, d) => {
                        if (!readOnly) updateROI(roi.id, { x: d.x, y: d.y });
                      }}
                      onResizeStop={(e, dir, ref, delta, pos) => {
                        if (!readOnly) updateROI(roi.id, { width: parseInt(ref.style.width), height: parseInt(ref.style.height), ...pos });
                      }}
                      bounds="parent"
                      scale={currentZoom}
                      className={customRoiClassName || `rnd-box-item border transition-shadow ${
                        activeTool !== 'pan' && selectedId !== roi.id ? 'pointer-events-none' : 'pointer-events-auto'
                      } ${hasPoints ? 'border-transparent bg-transparent shadow-none' : (selectedId === roi.id ? "border-indigo-600 bg-indigo-600/10 shadow-md z-30 ring-2 ring-indigo-500/20" : "border-indigo-400/80 bg-indigo-50/5 hover:border-indigo-500 hover:bg-indigo-50/10 z-20")}`}
                      resizeHandleStyles={!readOnly && selectedId === roi.id ? { topLeft: handleStyle, topRight: handleStyle, bottomLeft: handleStyle, bottomRight: handleStyle, top: handleStyle, right: handleStyle, bottom: handleStyle, left: handleStyle } : {}}
                      enableResizing={!readOnly && selectedId === roi.id}
                      disableDragging={readOnly}
                    >
                      <div className={`w-full h-full relative ${selectedId === roi.id || activeTool === 'pan' ? 'pointer-events-auto' : 'pointer-events-none'}`}>
                        {/* SVG Polygon overlay for Quad/Polygon ROIs */}
                        {roi.points && roi.points.length > 0 && (
                          <svg className="absolute inset-0 w-full h-full pointer-events-none z-10 overflow-visible">
                            <polygon
                              points={roi.points.map(p => `${p.x - roi.x},${p.y - roi.y}`).join(' ')}
                              fill={selectedId === roi.id ? "rgba(79, 70, 229, 0.18)" : "rgba(99, 102, 241, 0.08)"}
                              stroke={selectedId === roi.id ? "#4f46e5" : "#818cf8"}
                              strokeWidth="2"
                              strokeDasharray={selectedId === roi.id ? "0" : "3,3"}
                            />
                          </svg>
                        )}

                        {roiBadges.length > 0 && (
                          <div className="absolute left-1 top-1 z-20 flex flex-wrap gap-1 pointer-events-none">
                            {roiBadges.map((badge) => (
                              <span
                                key={badge}
                                className={`rounded px-1.5 py-0.5 text-[8px] font-black uppercase tracking-wide shadow-sm ${
                                  badge === "ANCHOR" ? "bg-amber-500 text-white" : "bg-sky-600 text-white"
                                }`}
                              >
                                {badge}
                              </span>
                            ))}
                          </div>
                        )}

                        {showLabels && (
                          <span 
                            onMouseDown={(e) => { e.stopPropagation(); selectRoiFromCanvas(roi.id); }}
                            className={getRoiLabelClassName?.(roi, selected) || `absolute -top-5 left-0 px-1.5 py-0.5 text-[9px] font-sans rounded shadow border flex items-center gap-1.5 pointer-events-auto cursor-pointer ${selectedId === roi.id ? "bg-indigo-600 border-indigo-600 text-white font-extrabold" : "bg-white border-indigo-200 text-indigo-700 font-bold"}`}
                          >
                            {renderTypeIcon(roi.type, 10)}
                            <span>{getRoiLabelText?.(roi) || roi.fieldName || "(Unnamed)"}</span>
                          </span>
                        )}

                      {/* Floating Menu Popover */}
                      {!readOnly && !lockRoiMetadata && selectedId === roi.id && (
                        <div 
                          className="absolute top-full left-1/2 -translate-x-1/2 mt-2 w-60 bg-white border border-slate-200 rounded-xl shadow-xl p-3 z-50 text-slate-800 flex flex-col gap-2 pointer-events-auto"
                          onMouseDown={(e) => e.stopPropagation()}
                        >
                          <div className="flex flex-col gap-1">
                            <label className="text-[9px] font-bold text-slate-400 uppercase tracking-wider text-left">ชื่อ Field</label>
                            <input
                              type="text"
                              value={roi.fieldName || ""}
                              onChange={(e) => updateROI(roi.id, { fieldName: e.target.value })}
                              placeholder="e.g. invoice_no"
                              className="w-full bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-1 text-xs font-semibold focus:outline-none focus:border-indigo-500 text-slate-800 text-left"
                            />
                          </div>
                          
                          <div className="flex flex-col gap-1">
                            <label className="text-[9px] font-bold text-slate-400 uppercase tracking-wider text-left">ประเภท ROI</label>
                            <div className={`grid gap-1 ${allowedRoiTypes.length <= 2 ? "grid-cols-2" : "grid-cols-3"}`}>
                              <button
                                type="button"
                                onClick={() => updateROI(roi.id, roiTypePatch('text'))}
                                className={`py-1 rounded text-[10px] font-bold flex items-center justify-center gap-1.5 transition-all ${!allowedRoiTypes.includes("text") ? "hidden" : ""} ${
                                  (roi.type || 'text') === 'text' 
                                    ? 'bg-indigo-600 text-white shadow-sm shadow-indigo-500/20' 
                                    : 'bg-slate-100 text-slate-500 hover:bg-slate-200/80 hover:text-slate-700'
                                }`}
                              >
                                <FileText size={10} /> ข้อความ
                              </button>
                              <button
                                type="button"
                                onClick={() => updateROI(roi.id, roiTypePatch('table'))}
                                className={`py-1 rounded text-[10px] font-bold flex items-center justify-center gap-1.5 transition-all ${!allowedRoiTypes.includes("table") ? "hidden" : ""} ${
                                  roi.type === 'table' 
                                    ? 'bg-indigo-600 text-white shadow-sm shadow-indigo-500/20' 
                                    : 'bg-slate-100 text-slate-500 hover:bg-slate-200/80 hover:text-slate-700'
                                }`}
                              >
                                <Table size={10} /> ตาราง
                              </button>
                              <button
                                type="button"
                                onClick={() => updateROI(roi.id, roiTypePatch('image'))}
                                className={`py-1 rounded text-[10px] font-bold flex items-center justify-center gap-1.5 transition-all ${!allowedRoiTypes.includes("image") ? "hidden" : ""} ${
                                  roi.type === 'image' 
                                    ? 'bg-indigo-600 text-white shadow-sm shadow-indigo-500/20' 
                                    : 'bg-slate-100 text-slate-500 hover:bg-slate-200/80 hover:text-slate-700'
                                }`}
                              >
                                <ImageIcon size={10} /> รูปภาพ
                              </button>
                            </div>
                          </div>

                          <div className="flex items-center justify-between border-t border-slate-100 pt-1.5 mt-0.5">
                            <span className="text-[8px] text-slate-400">ID: #{roi.id}</span>
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                setSelectedId(null);
                              }}
                              className="px-2.5 py-0.5 bg-slate-100 hover:bg-slate-200 text-slate-650 rounded text-[9px] font-bold transition-colors border border-slate-200"
                            >
                              ปิด
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  </Rnd>
                );
              })}
              </div>
            </div>
            </div>
          </div>
          {isUserLayout && !hideFooter && (
            <div className="mt-4">
              {renderPagePagination()}
            </div>
          )}
        </div>

        {/* Right properties panel */}
        {!hideRightPanel && <div className={rightPanelClassName || "min-w-0 h-full overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm flex flex-col"}>
          {rightPanelRenderer ? (
            rightPanelRenderer({ currentPageRois, selectedId, setSelectedId, updateROI, deleteROI, moveROI, triggerOCRProcessing })
          ) : (
            <>
              <div ref={defaultRightPanelScrollRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
              {rightPanelTopContent}
              <button
                type="button"
                onClick={onBackToAdjust}
                className="w-full py-2.5 bg-slate-100 hover:bg-slate-200 text-slate-700 border border-slate-200 rounded-lg text-xs font-bold flex items-center justify-center gap-1.5 transition-all shadow-sm"
              >
                <ArrowLeft size={14} /> กลับไปหน้าปรับภาพ
              </button>

              {!hideOcrActions && (
                <section className="space-y-2 rounded-xl border border-slate-200 bg-slate-50 p-3">
                  <h3 className="text-xs font-black uppercase tracking-wider text-slate-700">ตีกรอบ ROI อัตโนมัติ</h3>
                  <button
                    type="button"
                    disabled={isLoading || isAutoDetectingRoi || !previewUrl}
                    onClick={handleAutoDetectRoi}
                    className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-indigo-200 bg-white px-3 py-2 text-xs font-black text-indigo-700 shadow-sm hover:bg-indigo-50 disabled:bg-slate-100 disabled:text-slate-400"
                  >
                    {isAutoDetectingRoi ? <Loader2 size={13} className="animate-spin" /> : <ScanSearch size={13} />}
                    {isAutoDetectingRoi ? "กำลังตรวจหา ROI..." : "ตีกรอบ ROI อัตโนมัติ"}
                  </button>
                  <p className="text-[10px] font-semibold leading-relaxed text-slate-500">
                    สแกนหน้าปัจจุบันและสร้าง ROI จากบริเวณที่ OCR อ่านได้ โดยจะลบ ROI เดิมของหน้านี้ก่อนสร้างชุดใหม่
                  </p>
                  {autoDetectMessage && (
                    <p className={`rounded-lg px-2.5 py-2 text-[10px] font-bold ${
                      autoDetectMessage.startsWith("สร้าง ROI") ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"
                    }`}>
                      {autoDetectMessage}
                    </p>
                  )}
                </section>
              )}

              <div className="space-y-2 bg-slate-50 p-3 rounded-lg border border-slate-100">
                <h3 className="text-xs font-bold text-slate-500 tracking-wider uppercase">ROI ของหน้านี้ ({currentPageRois.length})</h3>
                <div className="space-y-1.5 max-h-[440px] overflow-y-auto pr-1">
                  {currentPageRois.map((roi, idx) => (
                    <div 
                      key={roi.id} 
                      ref={(el) => {
                        if (el) defaultRightPanelRoiRefs.current.set(roi.id, el);
                        else defaultRightPanelRoiRefs.current.delete(roi.id);
                      }}
                      onClick={() => setSelectedId(roi.id)} 
                      draggable={true}
                      onDragStart={(e) => handleDragStart(e, roi.id)}
                      onDragOver={(e) => handleDragOver(e, roi.id)}
                      onDragEnd={handleDragEnd}
                      className={`flex items-center justify-between p-2 rounded border text-xs cursor-grab active:cursor-grabbing select-none transition-all ${
                        roi.enabled === false
                          ? 'opacity-50 bg-slate-50 border-slate-200'
                          : draggedItemId === roi.id 
                          ? 'opacity-40 border-dashed border-indigo-400 bg-indigo-50/50' 
                          : (selectedId === roi.id 
                              ? "bg-indigo-50 border-indigo-300 text-slate-800 font-bold shadow-xs" 
                              : "bg-white border-slate-200 text-slate-600 hover:bg-slate-50")
                      }`}
                    >
                      <div className="flex items-center gap-2 w-full mr-1.5 min-w-0">
                        <Move size={11} className="text-slate-400 shrink-0 cursor-grab" />
                        <input 
                          type="text" 
                          value={roi.fieldName} 
                          onChange={(e) => updateROI(roi.id, { fieldName: e.target.value })} 
                          className="bg-transparent border-b border-transparent focus:border-indigo-500 focus:outline-none text-[11px] text-slate-700 w-full min-w-0 cursor-text" 
                          onClick={(e) => e.stopPropagation()} 
                        />
                        <select
                          value={roi.type || 'text'}
                          onChange={(e) => updateROI(roi.id, roiTypePatch(e.target.value as 'text' | 'table' | 'image'))}
                          onClick={(e) => e.stopPropagation()}
                          className="text-[9.5px] font-bold bg-white text-slate-600 border border-slate-200 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-indigo-500 cursor-pointer shrink-0 select-none"
                        >
                          <option value="text">ข้อความ</option>
                          <option value="table">ตาราง</option>
                          <option value="image">รูปภาพ</option>
                        </select>
                      </div>
                      <button 
                        type="button"
                        onClick={(e) => { e.stopPropagation(); deleteROI(roi.id); }} 
                        className="text-slate-400 hover:text-red-500 transition-colors p-1 ml-1 shrink-0"
                        title="ลบรายการ"
                      >
                        <X size={14} />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
              </div>
              {isUserLayout && !hideOcrActions && !hideFooterActions && (
                <div className="border-t border-slate-200 bg-white p-4">
                  <button
                    type="button"
                    disabled={rois.length === 0 || isLoading}
                    onClick={triggerOCRProcessing}
                    className="ui-stable-action-lg flex w-full items-center justify-center gap-2 rounded-xl bg-[#0052cc] px-6 py-3.5 text-xs font-bold uppercase tracking-wider text-white shadow-md transition-all hover:bg-[#0043a4] disabled:bg-slate-400 disabled:text-white/80"
                  >
                    <Cpu size={14} className={isLoading ? "animate-spin" : ""} />
                    {isLoading ? "กำลังประมวลผล ROI..." : "อ่านข้อมูลที่เลือก"}
                  </button>
                </div>
              )}
            </>
          )}
        </div>}
      </div>

      {/* Footer carousel and action buttons */}
      {!hideFooter && !isUserLayout && <div className="w-full bg-[#edf2f7] text-slate-800 border border-slate-200 rounded-2xl px-6 py-4 flex flex-col sm:flex-row items-center justify-between gap-4 shadow-sm select-none">
        <div className="flex items-center gap-4">
          <div className="text-xs font-bold text-slate-500 uppercase tracking-wider">
            หน้าปัจจุบัน: <span className="text-slate-800 text-sm ml-1 font-bold">{currentIndex + 1} / {imagesList.length} หน้า</span>
          </div>
          
          <div className="flex items-center gap-2.5">
            <button
              type="button"
              disabled={currentIndex === 0 || isLoading}
              onClick={() => onIndexChange(currentIndex - 1)}
              className="p-2 bg-white text-slate-650 border border-slate-200 rounded-xl hover:bg-slate-50 disabled:opacity-30 disabled:hover:bg-white transition-all active:scale-95 flex items-center justify-center"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
              </svg>
            </button>

            {/* Thumbnails */}
            <div className="flex items-center gap-2 overflow-x-auto max-w-[320px] py-0.5 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden">
              {imagesList.map((url, idx) => (
                <button
                  key={idx}
                  type="button"
                  disabled={isLoading}
                  onClick={() => onIndexChange(idx)}
                  className={`relative w-9 h-12 rounded-md overflow-hidden border transition-all shrink-0 shadow-md ${
                    currentIndex === idx 
                      ? "border-blue-500 ring-2 ring-blue-500/50 scale-105" 
                      : "border-slate-250 opacity-60 hover:opacity-100"
                  }`}
                >
                  <img src={url} alt={`Page ${idx + 1}`} className="w-full h-full object-cover" />
                </button>
              ))}
            </div>

            <button
              type="button"
              disabled={currentIndex === imagesList.length - 1 || isLoading}
              onClick={() => onIndexChange(currentIndex + 1)}
              className="p-2 bg-white text-slate-650 border border-slate-200 rounded-xl hover:bg-slate-50 disabled:opacity-30 disabled:hover:bg-white transition-all active:scale-95 flex items-center justify-center"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
              </svg>
            </button>
          </div>
        </div>

        {!hideOcrActions && !hideFooterActions && <div className="flex items-center gap-3 w-full sm:w-auto">
          <div className="flex flex-col gap-2 w-full sm:w-auto">
            <div className="flex flex-col sm:flex-row items-center gap-3 w-full sm:w-auto">
              <button 
                type="button"
                disabled={rois.length === 0 || isLoading} 
                onClick={triggerOCRProcessing} 
                className="ui-stable-action-lg w-full sm:w-auto px-6 py-3 bg-blue-600 hover:bg-blue-700 disabled:bg-slate-300 disabled:text-slate-400 text-white rounded-xl text-xs font-black uppercase tracking-widest flex items-center justify-center gap-2 transition-all shadow-md shadow-blue-900/10 active:scale-98"
              >
                <Cpu size={14} className={isLoading ? "animate-spin text-blue-300" : "text-white"} />
                {isLoading ? "กำลังประมวลผล ROI..." : "ตรวจ ROI ที่เลือก"}
              </button>
            </div>
          </div>
        </div>}
      </div>}

      {isLoading && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/35 px-4 backdrop-blur-sm">
          <div className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-6 text-center shadow-2xl">
            <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-blue-50 text-blue-600">
              <Loader2 size={28} className="animate-spin" />
            </div>
            <h3 className="mt-4 text-sm font-black uppercase tracking-wide text-slate-800">กำลังประมวลผล</h3>
            <p className="mt-2 text-xs font-semibold leading-relaxed text-slate-500">
              ระบบกำลังอ่านข้อความจาก ROI ที่เลือก กรุณารอสักครู่
            </p>
          </div>
        </div>
      )}

    </div>
  );
}
