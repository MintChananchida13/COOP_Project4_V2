"use client";

import { useEffect, useMemo, useState } from "react";
import { CheckCircle2 } from "lucide-react";
import { OCRResult, ROI, RequestedField, RoiDataType, TemplateRequestMode } from "../../types/ocr";
import { authHeaders } from "../../auth/session";
import { defaultExtractionMethodForDataType } from "../../shared/workspace/extractionMethods";

type PageAwareRoi = ROI & { pageIndex?: number };

interface TemplateRequestPanelProps {
  imagesList: string[];
  sourceFileId?: string;
  sourceFileName?: string;
  rois: PageAwareRoi[];
  ocrResults?: (OCRResult & { pageIndex?: number })[];
  isOpen: boolean;
  onClose: () => void;
}

type RequestStatus = "idle" | "submitting" | "submitted" | "mock_submitted" | "error";

interface ImageSize {
  width: number;
  height: number;
}

interface PdfJsLib {
  GlobalWorkerOptions: { workerSrc: string };
  getDocument: (options: { data: ArrayBuffer }) => {
    promise: Promise<{
      numPages: number;
      getPage: (pageNumber: number) => Promise<{
        getViewport: (options: { scale: number }) => { width: number; height: number };
        render: (options: { canvasContext: CanvasRenderingContext2D; viewport: { width: number; height: number } }) => {
          promise: Promise<void>;
        };
      }>;
    }>;
  };
}

declare global {
  interface Window {
    pdfjsLib?: PdfJsLib;
  }
}

interface TemplateRequestPageResponse {
  id: string;
  page_number: number;
}

interface TemplateRequestCreateResponse {
  id: string;
  pages?: TemplateRequestPageResponse[];
}

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const WORKSPACE_RENDERED_WIDTH = 750;

interface RequestImageItem {
  src: string;
  sourceFileId: string;
  sourceFileName: string;
}

const createSourceFileId = () => `source_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

const isPdfFile = (file: File) =>
  file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");

const clampRatio = (value: number) => Math.min(1, Math.max(0, value));

const roiTypeToDataType = (roi: PageAwareRoi): RoiDataType => {
  if (roi.type === "table") return "table";
  if (roi.type === "image") return "image";
  if (roi.type === "text") return "text";
  if (roi.dataType) return roi.dataType;
  return "text";
};

const loadImageSize = (src: string): Promise<ImageSize> =>
  new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve({ width: img.naturalWidth || WORKSPACE_RENDERED_WIDTH, height: img.naturalHeight || WORKSPACE_RENDERED_WIDTH });
    img.onerror = reject;
    img.src = src;
  });

const loadPdfEngine = (): Promise<PdfJsLib> =>
  new Promise((resolve, reject) => {
    if (window.pdfjsLib) {
      resolve(window.pdfjsLib);
      return;
    }

    const script = document.createElement("script");
    script.src = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js";
    script.onload = () => {
      const pdfjs = window.pdfjsLib;
      if (!pdfjs) {
        reject(new Error("ไม่สามารถโหลด PDF.js ได้"));
        return;
      }
      pdfjs.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
      resolve(pdfjs);
    };
    script.onerror = () => reject(new Error("โหลด PDF.js ไม่สำเร็จ"));
    document.head.appendChild(script);
  });

const convertPdfToImages = async (file: File): Promise<string[]> => {
  const pdfjsLib = await loadPdfEngine();
  const loadingTask = pdfjsLib.getDocument({ data: await file.arrayBuffer() });
  const pdf = await loadingTask.promise;
  const imageUrls: string[] = [];

  for (let pageNumber = 1; pageNumber <= pdf.numPages; pageNumber += 1) {
    const page = await pdf.getPage(pageNumber);
    const viewport = page.getViewport({ scale: 2 });
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    if (!ctx) continue;
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    await page.render({ canvasContext: ctx, viewport }).promise;
    imageUrls.push(canvas.toDataURL("image/jpeg", 0.95));
  }

  return imageUrls;
};

const fileToDataUrl = (file: File) =>
  new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });

const toRequestedField = (
  roi: PageAwareRoi,
  renderedHeight: number,
  index: number,
  fieldNameOverride?: string
): RequestedField => {
  const pageIndex = roi.pageIndex !== undefined ? Number(roi.pageIndex) : 0;
  const dataType = roiTypeToDataType(roi);
  const extractionMethod = roi.extractionMethod || defaultExtractionMethodForDataType(dataType);
  const fieldName = fieldNameOverride?.trim() || roi.fieldName || `field_${index + 1}`;
  return {
    id: `requested_field_${roi.id}`,
    fieldName,
    displayLabel: fieldName,
    dataType,
    extractionMethod,
    roi: {
      pageNumber: pageIndex + 1,
      xRatio: clampRatio(roi.x / WORKSPACE_RENDERED_WIDTH),
      yRatio: clampRatio(roi.y / renderedHeight),
      widthRatio: clampRatio(roi.width / WORKSPACE_RENDERED_WIDTH),
      heightRatio: clampRatio(roi.height / renderedHeight),
    },
  };
};

export default function TemplateRequestPanel({
  imagesList,
  sourceFileId: initialSourceFileId,
  sourceFileName: initialSourceFileName,
  rois,
  ocrResults = [],
  isOpen,
  onClose,
}: TemplateRequestPanelProps) {
  const [requestImages, setRequestImages] = useState<string[]>([]);
  const [requestImageItems, setRequestImageItems] = useState<RequestImageItem[]>([]);
  const [requestTitle, setRequestTitle] = useState("");
  const [userNote, setUserNote] = useState("");
  const [requestMode, setRequestMode] = useState<TemplateRequestMode>("image_only");
  const [status, setStatus] = useState<RequestStatus>("idle");
  const [statusMessage, setStatusMessage] = useState("");
  const [submittedRequestId, setSubmittedRequestId] = useState("");

  const enabledRois = useMemo(() => rois.filter((roi) => roi.enabled !== false), [rois]);
  const fieldsByPage = useMemo(() => {
    return enabledRois.reduce<Record<number, number>>((acc, roi) => {
      const pageNumber = (roi.pageIndex !== undefined ? Number(roi.pageIndex) : 0) + 1;
      acc[pageNumber] = (acc[pageNumber] || 0) + 1;
      return acc;
    }, {});
  }, [enabledRois]);

  const imageGroups = useMemo(() => {
    const groups = new Map<string, { sourceFileId: string; sourceFileName: string; items: Array<RequestImageItem & { index: number }> }>();
    requestImageItems.forEach((item, index) => {
      const group = groups.get(item.sourceFileId) || {
        sourceFileId: item.sourceFileId,
        sourceFileName: item.sourceFileName || "Source file",
        items: [],
      };
      group.items.push({ ...item, index });
      groups.set(item.sourceFileId, group);
    });
    return Array.from(groups.values());
  }, [requestImageItems]);

  useEffect(() => {
    if (isOpen) {
      const sourceFileId = initialSourceFileId || createSourceFileId();
      const sourceFileName = "ไฟล์ต้นทาง";
      const items = imagesList.map(src => ({ src, sourceFileId, sourceFileName: initialSourceFileName || sourceFileName }));
      setRequestImageItems(items);
      setRequestImages(imagesList);
    }
  }, [imagesList, initialSourceFileId, initialSourceFileName, isOpen]);

  const canSubmit = requestImageItems.length > 0 && requestTitle.trim().length > 0 && status !== "submitting";

  const handleAddImages = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const items = await Promise.all(
      Array.from(files)
        .filter((file) => file.type.startsWith("image/"))
        .map(
          (file) =>
            new Promise<RequestImageItem>((resolve, reject) => {
              const reader = new FileReader();
              reader.onload = () => resolve({
                src: String(reader.result || ""),
                sourceFileId: createSourceFileId(),
                sourceFileName: file.name || "รูปภาพที่เพิ่มใหม่",
              });
              reader.onerror = reject;
              reader.readAsDataURL(file);
            })
        )
    );
    setRequestImageItems((current) => [...current, ...items.filter(item => item.src)]);
    setRequestImages((current) => [...current, ...items.map(item => item.src).filter(Boolean)]);
  };

  const handleAddFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setStatusMessage("");

    const nextItems: RequestImageItem[] = [];
    const acceptedFiles = Array.from(files).filter((file) => file.type.startsWith("image/") || isPdfFile(file));

    for (const [fileIndex, file] of acceptedFiles.entries()) {
      const sourceFileId = createSourceFileId();
      const sourceFileName = file.name || `ไฟล์ที่เพิ่ม ${fileIndex + 1}`;
      const pageImages = isPdfFile(file) ? await convertPdfToImages(file) : [await fileToDataUrl(file)];

      pageImages.filter(Boolean).forEach((src) => {
        nextItems.push({ src, sourceFileId, sourceFileName });
      });
    }

    setRequestImageItems((current) => [...current, ...nextItems]);
    setRequestImages((current) => [...current, ...nextItems.map((item) => item.src)]);
  };

  const buildRequestedFields = async () => {
    const submitImages = requestImageItems.length > 0 ? requestImageItems.map((item) => item.src) : imagesList;
    const imageSizes = await Promise.all(submitImages.map((src) => loadImageSize(src)));
    return enabledRois.map((roi, index) => {
      const pageIndex = roi.pageIndex !== undefined ? Number(roi.pageIndex) : 0;
      const imageSize = imageSizes[pageIndex] || imageSizes[0] || { width: WORKSPACE_RENDERED_WIDTH, height: WORKSPACE_RENDERED_WIDTH };
      const renderedHeight = imageSize.width > 0 ? (imageSize.height / imageSize.width) * WORKSPACE_RENDERED_WIDTH : WORKSPACE_RENDERED_WIDTH;
      const resultByRoiId = ocrResults.find((result) => result.roiId === roi.id);
      const resultByPageOrder = ocrResults.filter((result) => (result.pageIndex !== undefined ? Number(result.pageIndex) : 0) === pageIndex)[
        enabledRois.filter((item) => (item.pageIndex !== undefined ? Number(item.pageIndex) : 0) === pageIndex).findIndex((item) => item.id === roi.id)
      ];
      return toRequestedField(roi, renderedHeight, index, resultByRoiId?.fieldName || resultByPageOrder?.fieldName);
    });
  };

  const submitWithBackend = async (requestedFields: RequestedField[]) => {
    const createResponse = await fetch(`${API_BASE_URL}/template-requests`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        request_title: requestTitle.trim(),
        sample_file_url: requestImageItems[0]?.src || null,
        request_mode: requestMode,
        page_count: requestImageItems.length,
        user_note: userNote.trim() || null,
        pages: requestImageItems.map((item, index) => ({
          page_number: index + 1,
          original_image_url: item.src,
          normalized_image_url: item.src,
          source_file_id: item.sourceFileId,
          source_file_name: item.sourceFileName,
        })),
      }),
    });

    if (!createResponse.ok) {
      throw new Error(`สร้างคำขอ Template ไม่สำเร็จ (${createResponse.status})`);
    }

    const createJson = await createResponse.json();
    const createdRequest = createJson?.data as TemplateRequestCreateResponse | undefined;
    const requestId = createdRequest?.id;
    if (!requestId) {
      throw new Error("Backend ไม่ได้ส่งรหัสคำขอกลับมา");
    }

    if (requestMode === "image_with_roi") {
      await Promise.all(
        requestedFields.map((field) => {
          const requestedFieldPayload = {
            template_request_page_id:
              createdRequest?.pages?.find((page) => page.page_number === field.roi.pageNumber)?.id ||
              `template_request_page_${field.roi.pageNumber}`,
            page_number: field.roi.pageNumber,
            field_name: field.fieldName,
            display_label: field.displayLabel,
            data_type: field.dataType || "text",
            extraction_method: field.extractionMethod || defaultExtractionMethodForDataType(field.dataType),
            roi: {
              page_number: field.roi.pageNumber,
              x_ratio: field.roi.xRatio,
              y_ratio: field.roi.yRatio,
              width_ratio: field.roi.widthRatio,
              height_ratio: field.roi.heightRatio,
            },
            user_note: field.userNote || null,
          };
          console.info("Template request requested-field payload", requestedFieldPayload);

          return fetch(`${API_BASE_URL}/template-requests/${requestId}/requested-fields`, {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(requestedFieldPayload),
          });
        })
      );
    }

    const submitResponse = await fetch(`${API_BASE_URL}/template-requests/${requestId}/submit`, {
      method: "POST",
      headers: authHeaders(),
    });

    if (!submitResponse.ok) {
      throw new Error(`ส่งคำขอ Template ไม่สำเร็จ (${submitResponse.status})`);
    }

    return requestId;
  };

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setStatus("submitting");
    setStatusMessage("");

    try {
      const requestedFields = requestMode === "image_with_roi" ? await buildRequestedFields() : [];
      const requestId = await submitWithBackend(requestedFields);
      setSubmittedRequestId(requestId);
      setStatus("submitted");
      setStatusMessage(`ส่งคำขอเรียบร้อยแล้ว: ${requestId}`);
    } catch (error) {
      console.error("Template request submission failed.", error);
      setStatus("error");
      setStatusMessage(error instanceof Error ? error.message : "ส่งคำขอ Template ไม่สำเร็จ");
    }
  };

  const resetAndClose = () => {
    setRequestTitle("");
    setUserNote("");
    setRequestMode("image_only");
    setStatus("idle");
    setStatusMessage("");
    setSubmittedRequestId("");
    const sourceFileId = initialSourceFileId || createSourceFileId();
    const sourceFileName = requestTitle.trim() || "ไฟล์ต้นทาง";
    const items = imagesList.map(src => ({ src, sourceFileId, sourceFileName: initialSourceFileName || sourceFileName }));
    setRequestImageItems(items);
    setRequestImages(imagesList);
    onClose();
  };

  return (
    <div className={`fixed inset-0 z-50 ${isOpen ? "pointer-events-auto" : "pointer-events-none"}`} aria-hidden={!isOpen}>
      <div
        className={`absolute inset-0 bg-slate-950/30 transition-opacity ${isOpen ? "opacity-100" : "opacity-0"}`}
        onClick={status === "submitting" ? undefined : onClose}
      />
      <aside
        className={`absolute right-0 top-0 h-full w-full max-w-md bg-white shadow-2xl transition-transform duration-200 ${
          isOpen ? "translate-x-0" : "translate-x-full"
        }`}
      >
        <div className="flex h-full flex-col">
          <div className="border-b border-slate-200 px-5 py-4 flex items-start justify-between gap-4">
            <div>
              <h2 className="text-sm font-black text-slate-800 uppercase tracking-wide">ส่งคำขอสร้าง Template</h2>
              <p className="text-xs font-semibold text-slate-500">
                ส่งภาพเอกสารและ ROI ให้ผู้ดูแลตรวจสอบและสร้าง Template กลาง
              </p>
            </div>
            <button
              type="button"
              onClick={status === "submitted" ? resetAndClose : onClose}
              disabled={status === "submitting"}
              className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-black text-slate-500 hover:bg-slate-50"
            >
              ปิด
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-5 space-y-4">
            <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-bold text-slate-500">
              {requestImages.length} หน้า
              {requestMode === "image_with_roi" && `, ROI ที่ส่ง ${enabledRois.length} รายการ`}
            </div>

            <div className="rounded-xl border border-slate-200 bg-white p-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-[10px] font-black uppercase tracking-wider text-slate-400">
                    Document Pages
                  </div>
                  <p className="mt-1 text-xs font-semibold text-slate-500">
                    เพิ่มรูปเอกสารตัวอย่างหลายภาพก่อนส่งให้ผู้ดูแลตรวจสอบ
                  </p>
                </div>

              </div>

              <div className="mt-3 space-y-3">
                {imageGroups.map((group) => (
                  <div key={group.sourceFileId} className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-xs font-black text-slate-800">{group.sourceFileName}</p>
                        <p className="mt-0.5 text-[10px] font-bold text-slate-400">{group.items.length} pages</p>
                      </div>
                      <span className="rounded-full bg-white px-2.5 py-1 text-[10px] font-black text-slate-500">Document Group</span>
                    </div>
                    <div className="mt-3 grid grid-cols-2 gap-2">
                      {group.items.map((item) => (
                        <div key={`${item.sourceFileId}_${item.index}`} className="rounded-xl border border-slate-200 bg-white p-2">
                          <div className="aspect-[4/3] overflow-hidden rounded-lg bg-slate-50">
                            <img src={item.src} alt={`Document page ${item.index + 1}`} className="h-full w-full object-contain" />
                          </div>
                          <div className="mt-2 flex items-center justify-between gap-2">
                            <span className="text-[10px] font-black text-slate-500">Page {item.index + 1}</span>
                            <button
                              type="button"
                              onClick={() => {
                                setRequestImageItems((current) => current.filter((_, itemIndex) => itemIndex !== item.index));
                                setRequestImages((current) => current.filter((_, itemIndex) => itemIndex !== item.index));
                              }}
                              disabled={status === "submitting" || requestImageItems.length <= 1}
                              className="rounded-lg border border-red-100 bg-white px-2 py-1 text-[10px] font-black text-red-600 disabled:text-slate-300"
                            >
                              Remove
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>

              <div className="hidden">
                {requestImages.map((src, index) => (
                  <div key={`${src.slice(0, 32)}_${index}`} className="rounded-xl border border-slate-200 bg-slate-50 p-2">
                    <div className="aspect-[4/3] overflow-hidden rounded-lg bg-white">
                      <img src={src} alt={`Document page ${index + 1}`} className="h-full w-full object-contain" />
                    </div>
                    <div className="mt-2 flex items-center justify-between gap-2">
                      <span className="text-[10px] font-black text-slate-500">Page {index + 1}</span>
                      <button
                        type="button"
                        onClick={() => setRequestImages((current) => current.filter((_, itemIndex) => itemIndex !== index))}
                        disabled={status === "submitting" || requestImages.length <= 1}
                        className="rounded-lg border border-red-100 bg-white px-2 py-1 text-[10px] font-black text-red-600 disabled:text-slate-300"
                      >
                        Remove
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="space-y-1">
              <span className="text-[10px] font-black uppercase tracking-wider text-slate-400">รูปแบบการส่งคำขอ</span>
              <div className="grid grid-cols-2 rounded-xl border border-slate-200 bg-slate-50 p-1">
                <button
                  type="button"
                  onClick={() => setRequestMode("image_only")}
                  className={`px-3 py-2 rounded-lg text-xs font-black transition-all ${
                    requestMode === "image_only" ? "bg-white text-indigo-700 shadow-sm" : "text-slate-500"
                  }`}
                >
                  ส่งภาพเท่านั้น
                </button>
                <button
                  type="button"
                  onClick={() => setRequestMode("image_with_roi")}
                  className={`px-3 py-2 rounded-lg text-xs font-black transition-all ${
                    requestMode === "image_with_roi" ? "bg-white text-indigo-700 shadow-sm" : "text-slate-500"
                  }`}
                >
                  ส่งภาพพร้อม ROI
                </button>
              </div>
            </div>

            <label className="block space-y-1">
              <span className="text-[10px] font-black uppercase tracking-wider text-slate-400">ชื่อคำขอ</span>
              <input
                type="text"
                value={requestTitle}
                onChange={(event) => setRequestTitle(event.target.value)}
                placeholder="เช่น Template ใบกำกับภาษีรูปแบบใหม่"
                className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-xs font-semibold text-slate-800 outline-none focus:border-indigo-500 focus:bg-white"
              />
            </label>

            <label className="block space-y-1">
              <span className="text-[10px] font-black uppercase tracking-wider text-slate-400">หมายเหตุถึงผู้ดูแล</span>
              <textarea
                value={userNote}
                onChange={(event) => setUserNote(event.target.value)}
                rows={4}
                placeholder="ระบุรายละเอียดเพิ่มเติมให้ผู้ดูแลตรวจสอบ (ไม่บังคับ)"
                className="w-full resize-none rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-xs font-semibold text-slate-800 outline-none focus:border-indigo-500 focus:bg-white"
              />
            </label>

            {requestMode === "image_with_roi" && (
              <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                <div className="text-[10px] font-black uppercase tracking-wider text-slate-400">ROI ที่ส่งตามหน้าเอกสาร</div>
                <div className="mt-2 flex flex-wrap gap-2">
                  {Object.keys(fieldsByPage).length === 0 ? (
                    <span className="text-xs font-semibold text-slate-500">ยังไม่มี ROI สำหรับส่งให้ผู้ดูแล</span>
                  ) : (
                    Object.entries(fieldsByPage).map(([pageNumber, count]) => (
                      <span key={pageNumber} className="rounded-lg bg-white border border-slate-200 px-2.5 py-1 text-xs font-bold text-slate-700">
                        หน้า {pageNumber}: {count} ROI
                      </span>
                    ))
                  )}
                </div>
              </div>
            )}

            <div className={`text-xs font-bold ${
              status === "error" ? "text-red-600" : status === "submitted" || status === "mock_submitted" ? "text-emerald-600" : "text-slate-500"
            }`}>
              {statusMessage || "สถานะคำขอ: ยังไม่ได้ส่ง"}
            </div>
          </div>

          <div className="border-t border-slate-200 p-5">
            <button
              type="button"
              disabled={!canSubmit}
              onClick={handleSubmit}
              className="ui-stable-action w-full px-5 py-3 rounded-xl bg-indigo-600 text-white text-xs font-black uppercase tracking-wider shadow-sm disabled:bg-slate-300 disabled:text-slate-500"
            >
              {status === "submitting" ? "กำลังส่ง..." : "ส่งคำขอ"}
            </button>
          </div>
        </div>

        {status === "submitted" && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-slate-950/35 px-5">
            <div className="w-full max-w-sm rounded-3xl border border-emerald-100 bg-white p-6 text-center shadow-2xl">
              <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-emerald-50 text-emerald-600">
                <CheckCircle2 size={30} strokeWidth={2.2} />
              </div>
              <h3 className="mt-4 text-lg font-black text-slate-900">ส่งคำขอเรียบร้อยแล้ว</h3>
              <p className="mt-2 text-sm font-medium leading-6 text-slate-600">
                ระบบส่ง Template Request ให้ผู้ดูแลตรวจสอบแล้ว
              </p>
              {submittedRequestId && (
                <div className="mt-4 rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-bold text-slate-500">
                  Request ID: <span className="font-black text-slate-700">{submittedRequestId}</span>
                </div>
              )}
              <button
                type="button"
                onClick={resetAndClose}
                className="ui-stable-action mt-5 w-full rounded-xl bg-emerald-600 px-5 py-3 text-xs font-black uppercase tracking-wider text-white shadow-sm hover:bg-emerald-700"
              >
                Done
              </button>
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}
