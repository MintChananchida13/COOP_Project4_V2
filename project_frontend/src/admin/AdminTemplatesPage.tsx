"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Check, ChevronDown, ChevronRight, FileImage, Folder, Loader2, Pencil, Plus, Search, UploadCloud, X } from "lucide-react";
import { Template, TemplateStatus } from "../types/ocr";
import {
  addTemplateRequestImage,
  createTemplateRequest,
  deleteTemplateApi,
  fetchTemplates,
  updateTemplateApi,
  updateTemplateStatus,
} from "./adminApi";
import { AdminStatusFilter } from "./adminTypes";
import { ActionButton, EmptyState, InlineState, LoadingState, StatusBadge, cardClassName } from "../shared/ui";

const statusFilterOptions: { value: AdminStatusFilter; label: string }[] = [
  { value: "all", label: "ทั้งหมด" },
  { value: "draft", label: "ฉบับร่าง" },
  { value: "active", label: "ใช้งานอยู่" },
  { value: "nonactive", label: "ไม่ใช้งาน" },
];

const manageableStatuses: TemplateStatus[] = ["active", "nonactive", "disabled"];

interface PdfJsLib {
  GlobalWorkerOptions: { workerSrc: string };
  getDocument: (options: { data: ArrayBuffer }) => {
    promise: Promise<{
      numPages: number;
      getPage: (pageNumber: number) => Promise<{
        getViewport: (options: { scale: number }) => { width: number; height: number };
        render: (options: { canvasContext: CanvasRenderingContext2D; viewport: { width: number; height: number } }) => { promise: Promise<void> };
      }>;
    }>;
  };
}

declare global {
  interface Window {
    pdfjsLib?: PdfJsLib;
  }
}

const isPdfFile = (file: File) =>
  file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");

const fileToDataUrl = (file: File) =>
  new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("อ่านไฟล์ไม่สำเร็จ"));
    reader.readAsDataURL(file);
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
        reject(new Error("โหลดตัวอ่าน PDF ไม่สำเร็จ"));
        return;
      }
      pdfjs.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
      resolve(pdfjs);
    };
    script.onerror = () => reject(new Error("โหลดตัวอ่าน PDF ไม่สำเร็จ"));
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
    if (!ctx) throw new Error("ไม่สามารถเตรียมภาพจาก PDF ได้");
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    await page.render({ canvasContext: ctx, viewport }).promise;
    imageUrls.push(canvas.toDataURL("image/png"));
  }
  return imageUrls;
};

const statusSelectLabel = (status: TemplateStatus) => {
  if (status === "active") return "ใช้งานอยู่";
  if (status === "nonactive" || status === "disabled") return "ปิดใช้งานชั่วคราว";
  if (status === "draft") return "ฉบับร่าง";
  if (status === "embedding_pending") return "กำลังเตรียมเผยแพร่";
  if (status === "validated") return "ตรวจสอบแล้ว";
  return status.replaceAll("_", " ");
};

const detectionModeLabel = (value?: string) => {
  if (value === "main_page") return "main page";
  if (value === "all_pages") return "all pages";
  return value || "all pages";
};

const stripRepeatedNamePrefix = (value: string, prefix: string) => {
  let nextValue = value.trim();
  const cleanPrefix = prefix.trim();
  const prefixText = `${cleanPrefix} - `;
  while (cleanPrefix && nextValue.startsWith(prefixText)) {
    nextValue = nextValue.slice(prefixText.length).trim();
  }
  return nextValue;
};

const collapseRepeatedName = (value: string) => {
  const parts = value.split(" - ").map((part) => part.trim()).filter(Boolean);
  if (parts.length > 1 && parts.every((part) => part === parts[0])) {
    return parts[0];
  }
  return value.trim();
};

export default function AdminTemplatesPage() {
  const router = useRouter();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [selectedStatus, setSelectedStatus] = useState<AdminStatusFilter>("all");
  const [templateSearch, setTemplateSearch] = useState("");
  const [loadStatus, setLoadStatus] = useState<"loading" | "loaded" | "error">("loading");
  const [deletingTemplateId, setDeletingTemplateId] = useState<string | null>(null);
  const [deleteConfirmTemplate, setDeleteConfirmTemplate] = useState<Template | null>(null);
  const [statusUpdatingTemplateId, setStatusUpdatingTemplateId] = useState<string | null>(null);
  const [deleteMessage, setDeleteMessage] = useState("");
  const [deleteError, setDeleteError] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [statusError, setStatusError] = useState("");
  const [editingTemplateId, setEditingTemplateId] = useState<string | null>(null);
  const [editingTemplateName, setEditingTemplateName] = useState("");
  const [renamingTemplateId, setRenamingTemplateId] = useState<string | null>(null);
  const [renameMessage, setRenameMessage] = useState("");
  const [renameError, setRenameError] = useState("");
  const [newTemplateName, setNewTemplateName] = useState("");
  const [newVersionNameSuffix, setNewVersionNameSuffix] = useState("");
  const [selectedExistingDocumentType, setSelectedExistingDocumentType] = useState("");
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [manualCreationType, setManualCreationType] = useState<"new_template" | "new_version">("new_template");
  const [expandedFolderId, setExpandedFolderId] = useState<string | null>(null);
  const [editingFolderId, setEditingFolderId] = useState<string | null>(null);
  const [editingFolderName, setEditingFolderName] = useState("");
  const editingFolderInputRef = useRef<HTMLInputElement | null>(null);
  const [renamingFolderId, setRenamingFolderId] = useState<string | null>(null);
  const [isCreatingRequest, setIsCreatingRequest] = useState(false);
  const [createRequestError, setCreateRequestError] = useState("");

  useEffect(() => {
    let cancelled = false;

    const loadTemplates = async () => {
      setLoadStatus("loading");
      try {
        const persistedTemplates = await fetchTemplates();
        if (cancelled) return;
        setTemplates(persistedTemplates);
        setLoadStatus("loaded");
      } catch (error) {
        console.warn("Templates load failed.", error);
        if (cancelled) return;
        setTemplates([]);
        setLoadStatus("error");
      }
    };

    loadTemplates();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!editingFolderId) return;
    editingFolderInputRef.current?.focus();
    editingFolderInputRef.current?.select();
  }, [editingFolderId]);

  const filteredTemplates = templates.filter((template) => {
    if (selectedStatus === "all") return true;
    if (selectedStatus === "draft") return template.status === "draft";
    if (selectedStatus === "active") return template.status === "active";
    return template.status !== "draft" && template.status !== "active";
  });

  const statusCounts: Record<AdminStatusFilter, number> = {
    all: templates.length,
    draft: templates.filter((template) => template.status === "draft").length,
    active: templates.filter((template) => template.status === "active").length,
    nonactive: templates.filter((template) => template.status !== "draft" && template.status !== "active").length,
  };

  const templateFolders = useMemo(() => {
    const groups = new Map<string, Template[]>();
    filteredTemplates.forEach((template) => {
      const groupId = template.templateGroupId || template.baseTemplateId || template.id;
      groups.set(groupId, [...(groups.get(groupId) || []), template]);
    });
    return Array.from(groups.entries()).map(([groupId, versions]) => {
      const sortedVersions = versions.sort((a, b) => (b.versionNumber || b.version) - (a.versionNumber || a.version));
      const latest = sortedVersions[0];
      const baseVersion = sortedVersions.find((template) => !template.baseTemplateId) || sortedVersions[sortedVersions.length - 1] || latest;
      const folderName = collapseRepeatedName(
        (latest?.templateGroupName || baseVersion?.templateGroupName || baseVersion?.documentType || latest?.documentType || "Template").trim()
      ) || "Template";
      const activeCount = sortedVersions.filter((template) => template.status === "active").length;
      return {
        groupId,
        name: folderName,
        documentType: folderName,
        previewImageUrl: latest?.previewImageUrl,
        pageCount: latest?.pageCount || 0,
        activeCount,
        versions: sortedVersions,
      };
    });
  }, [filteredTemplates]);

  const visibleTemplateFolders = useMemo(() => {
    const query = templateSearch.trim().toLowerCase();
    if (!query) return templateFolders;
    return templateFolders.filter((folder) => {
      const folderText = `${folder.name} ${folder.documentType}`.toLowerCase();
      const versionText = folder.versions
        .map((template) => `${template.name} ${template.documentType || ""}`)
        .join(" ")
        .toLowerCase();
      return folderText.includes(query) || versionText.includes(query);
    });
  }, [templateFolders, templateSearch]);

  const existingDocumentTypes = useMemo(
    () => templateFolders.map((folder) => folder.documentType).filter(Boolean).sort((a, b) => a.localeCompare(b)),
    [templateFolders]
  );

  const canUploadCreateReference =
    manualCreationType === "new_template"
      ? newTemplateName.trim().length > 0
      : selectedExistingDocumentType.trim().length > 0 && newVersionNameSuffix.trim().length > 0;

  const toggleTemplateFolder = (groupId: string) => {
    setExpandedFolderId((current) => (current === groupId ? null : groupId));
    setEditingFolderId(null);
    setEditingTemplateId(null);
  };

  const startRenameFolder = (folder: { groupId: string; name: string }) => {
    setEditingFolderId(folder.groupId);
    setEditingFolderName(folder.name);
    setRenameMessage("");
    setRenameError("");
  };

  const cancelRenameFolder = () => {
    setEditingFolderId(null);
    setEditingFolderName("");
  };

  const templateNameSuffix = (templateName: string, folderName: string) => {
    return stripRepeatedNamePrefix(templateName, folderName);
  };

  const templateVersionSuffix = (template: Template, folderName: string) =>
    templateNameSuffix(template.versionName || template.name, folderName) || `Version ${template.versionNumber || template.version}`;

  const templateVersionDisplayName = (template: Template, folderName: string) =>
    `${folderName.trim()} - ${templateVersionSuffix(template, folderName)}`;

  const handleRenameFolder = async (folder: { groupId: string; name: string; versions: Template[] }) => {
    const nextName = editingFolderName.trim();
    if (!nextName) {
      setRenameError("กรุณากรอกชื่อ Template");
      return;
    }
    if (nextName === folder.name) {
      cancelRenameFolder();
      return;
    }

    setRenamingFolderId(folder.groupId);
    setRenameMessage("");
    setRenameError("");
    setDeleteMessage("");
    setDeleteError("");
    setStatusMessage("");
    setStatusError("");

    try {
      await Promise.all(folder.versions.map((template) => updateTemplateApi(template.id, { name: nextName, documentType: nextName })));
      const versionIds = new Set(folder.versions.map((template) => template.id));
      setTemplates((current) =>
        current.map((template) =>
          versionIds.has(template.id)
            ? { ...template, templateGroupName: nextName, documentType: nextName }
            : template
        )
      );
      setRenameMessage(`เปลี่ยนชื่อโฟลเดอร์เป็น "${nextName}" เรียบร้อยแล้ว`);
      cancelRenameFolder();
    } catch (error) {
      console.warn("Template folder rename failed.", error);
      setRenameError(error instanceof Error ? error.message : "เปลี่ยนชื่อ Template ไม่สำเร็จ");
    } finally {
      setRenamingFolderId(null);
    }
  };

  const handleChangeTemplateStatus = async (template: Template, nextStatus: TemplateStatus) => {
    if (loadStatus !== "loaded") {
      setStatusError("ไม่สามารถเปลี่ยนสถานะ Template ตัวอย่างได้ เพราะไม่ได้โหลดจากฐานข้อมูลจริง");
      return;
    }

    const actionLabel = nextStatus === "active" ? "เปิดใช้งาน" : "ปิดใช้งานชั่วคราว";
    if (!window.confirm(`${actionLabel} Template "${template.name}"?`)) return;

    setStatusUpdatingTemplateId(template.id);
    setStatusMessage("");
    setStatusError("");
    setDeleteMessage("");
    setDeleteError("");

    try {
      const bundle = await updateTemplateStatus(template.id, nextStatus);
      setTemplates((current) => current.map((item) => (item.id === template.id ? bundle.template : item)));
      setStatusMessage(`${actionLabel} Template "${template.name}" เรียบร้อยแล้ว`);
    } catch (error) {
      console.warn("Template status update failed.", error);
      setStatusError(error instanceof Error ? error.message : "เปลี่ยนสถานะ Template ไม่สำเร็จ");
    } finally {
      setStatusUpdatingTemplateId(null);
    }
  };

  const startRenameTemplate = (template: Template, folderName: string) => {
    setEditingTemplateId(template.id);
    setEditingTemplateName(templateVersionSuffix(template, folderName));
    setRenameMessage("");
    setRenameError("");
  };

  const cancelRenameTemplate = () => {
    setEditingTemplateId(null);
    setEditingTemplateName("");
    setRenameError("");
  };

  const handleRenameTemplate = async (template: Template, folderName: string) => {
    if (loadStatus !== "loaded") {
      setRenameError("ไม่สามารถเปลี่ยนชื่อ Template ได้ เพราะยังไม่ได้โหลดข้อมูลจาก Backend");
      return;
    }

    const nextSuffix = editingTemplateName.trim();
    if (!nextSuffix) {
      setRenameError("กรุณาระบุชื่อ Template");
      return;
    }
    if (nextSuffix === templateVersionSuffix(template, folderName)) {
      cancelRenameTemplate();
      return;
    }

    setRenamingTemplateId(template.id);
    setRenameMessage("");
    setRenameError("");
    setDeleteMessage("");
    setDeleteError("");
    setStatusMessage("");
    setStatusError("");

    try {
      const bundle = await updateTemplateApi(template.id, { versionName: nextSuffix });
      setTemplates((current) => current.map((item) => (item.id === template.id ? bundle.template : item)));
      setEditingTemplateId(null);
      setEditingTemplateName("");
      setRenameMessage(`เปลี่ยนชื่อ Template เป็น "${bundle.template.name}" เรียบร้อยแล้ว`);
    } catch (error) {
      console.warn("Template rename failed.", error);
      setRenameError(error instanceof Error ? error.message : "เปลี่ยนชื่อ Template ไม่สำเร็จ");
    } finally {
      setRenamingTemplateId(null);
    }
  };

  const handleCreateTemplateRequest = async (files: FileList | null) => {
    if (!files?.length) return;
    if (loadStatus !== "loaded") {
      setCreateRequestError("ต้องเชื่อมต่อ Backend ก่อนสร้าง Template Request ใหม่");
      return;
    }

    const selectedFolder = templateFolders.find((folder) => folder.documentType === selectedExistingDocumentType);
    const selectedBaseTemplate = selectedFolder?.versions[0];
    const documentType = manualCreationType === "new_template" ? newTemplateName.trim() : selectedBaseTemplate?.documentType || selectedExistingDocumentType.trim();
    const requestTitle =
      manualCreationType === "new_template"
        ? newTemplateName.trim()
        : newVersionNameSuffix.trim();
    if (!documentType || !requestTitle) {
      setCreateRequestError(manualCreationType === "new_template" ? "กรุณากรอกชื่อ Template ก่อนอัปโหลด" : "กรุณาเลือก Template เดิมและกรอกชื่อต่อท้าย Version ก่อนอัปโหลด");
      return;
    }

    const acceptedFiles = Array.from(files).filter((file) => file.type.startsWith("image/") || isPdfFile(file));
    if (acceptedFiles.length === 0) {
      setCreateRequestError("กรุณาเลือกไฟล์รูปภาพหรือ PDF");
      return;
    }

    setIsCreatingRequest(true);
    setCreateRequestError("");
    setDeleteMessage("");
    setDeleteError("");
    setStatusMessage("");
    setStatusError("");

    try {
      const request = await createTemplateRequest({
        requestTitle,
        documentType,
        requestMode: "image_only",
        pageCount: 1,
        userNote: "สร้างโดยผู้ดูแลระบบจากหน้า Template",
        requestedBy: "admin",
      });

      for (const [fileIndex, file] of acceptedFiles.entries()) {
        const sourceFileId = `admin_template_file_${Date.now()}_${fileIndex}`;
        const sourceFileName = file.name || `ไฟล์ Template ${fileIndex + 1}`;
        const pageImages = isPdfFile(file) ? await convertPdfToImages(file) : [await fileToDataUrl(file)];
        for (const imageUrl of pageImages) {
          await addTemplateRequestImage(request.id, imageUrl, "admin_upload", sourceFileId, sourceFileName);
        }
      }

      setNewTemplateName("");
      setNewVersionNameSuffix("");
      setSelectedExistingDocumentType("");
      setIsCreateModalOpen(false);
      const baseTemplateParam = manualCreationType === "new_version" && selectedBaseTemplate?.id ? `&baseTemplateId=${encodeURIComponent(selectedBaseTemplate.id)}` : "";
      window.dispatchEvent(new Event("admin-route-transition-start"));
      router.push(`/admin/requests/${request.id}?creationType=${manualCreationType}${baseTemplateParam}`);
    } catch (error) {
      console.warn("Admin create template request failed.", error);
      setCreateRequestError(error instanceof Error ? error.message : "สร้าง Template Request ไม่สำเร็จ");
    } finally {
      setIsCreatingRequest(false);
    }
  };

  const handleDeleteTemplate = async (template: Template) => {
    if (loadStatus !== "loaded") {
      setDeleteError("ไม่สามารถลบ Template ตัวอย่างได้ เพราะไม่ได้มาจากฐานข้อมูลจริง");
      return;
    }
    const confirmed = window.confirm(
      `ลบ Template "${template.name}"?\n\nระบบจะลบ Template, หน้าเอกสาร, Field, Ignore Region และประวัติ Embedding ออกจากฐานข้อมูลถาวร การดำเนินการนี้ย้อนกลับไม่ได้`
    );
    if (!confirmed) return;

    setDeletingTemplateId(template.id);
    setDeleteMessage("");
    setDeleteError("");
    try {
      await deleteTemplateApi(template.id);
      setTemplates((current) => current.filter((item) => item.id !== template.id));
      setDeleteMessage(`ลบ Template "${template.name}" เรียบร้อยแล้ว`);
    } catch (error) {
      console.warn("Template delete failed.", error);
      setDeleteError(error instanceof Error ? error.message : "ลบ Template ไม่สำเร็จ");
    } finally {
      setDeletingTemplateId(null);
    }
  };

  const confirmDeleteTemplate = async () => {
    const template = deleteConfirmTemplate;
    if (!template) return;

    setDeletingTemplateId(template.id);
    setDeleteMessage("");
    setDeleteError("");
    try {
      await deleteTemplateApi(template.id);
      setTemplates((current) => current.filter((item) => item.id !== template.id));
      setDeleteMessage(`ลบ Template "${template.name}" เรียบร้อยแล้ว`);
      setDeleteConfirmTemplate(null);
    } catch (error) {
      console.warn("Template delete failed.", error);
      setDeleteError(error instanceof Error ? error.message : "ลบ Template ไม่สำเร็จ");
    } finally {
      setDeletingTemplateId(null);
    }
  };

  return (
    <section className="space-y-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <p className="ui-caption font-semibold text-blue-600">คลัง Template</p>
          <h2 className="ui-page-title mt-1 text-slate-950">คลัง Template</h2>
          <p className="ui-body mt-1 text-slate-500">จัดการ Template และ Version ทั้งหมด</p>
        </div>
        <button
          type="button"
          onClick={() => setIsCreateModalOpen(true)}
          disabled={loadStatus !== "loaded"}
          className="inline-flex h-11 shrink-0 items-center justify-center gap-2 rounded-xl bg-indigo-600 px-4 text-xs font-black text-white shadow-sm hover:bg-indigo-700 disabled:bg-slate-300"
        >
          <Plus size={16} />
          สร้าง Template
        </button>
      </div>

      <div className={`${cardClassName} space-y-4 p-4`}>
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 className="text-sm font-black uppercase tracking-wide text-slate-800">สถานะ Template</h2>
            <p className="mt-1 text-xs font-medium text-slate-500">เลือกดู Template ตามสถานะ</p>
          </div>
          <div className="grid w-full gap-2 sm:grid-cols-4 lg:w-auto lg:min-w-[520px]">
            {statusFilterOptions.map((status) => (
              <button
                key={status.value}
                type="button"
                onClick={() => setSelectedStatus(status.value)}
                className={`inline-flex h-10 items-center justify-between rounded-xl border px-3 text-xs font-black transition-colors ${
                  selectedStatus === status.value
                    ? "border-indigo-500 bg-indigo-600 text-white"
                    : "border-slate-200 bg-slate-100 text-slate-600 hover:bg-slate-50"
                }`}
              >
                <span>{status.label}</span>
                <span className={`ml-2 inline-flex min-w-6 justify-center rounded-full px-1.5 py-0.5 text-[10px] tabular-nums ${
                  selectedStatus === status.value ? "bg-white/20 text-white" : "bg-white text-slate-500"
                }`}>
                  {statusCounts[status.value]}
                </span>
              </button>
            ))}
          </div>
        </div>

      {loadStatus === "loading" && <LoadingState message="กำลังโหลด Template จากฐานข้อมูล..." />}
      {loadStatus === "error" && (
        <InlineState tone="warning" message="โหลดรายการ Template จาก Backend ไม่สำเร็จ กรุณาตรวจการเชื่อมต่อแล้วลองใหม่" />
      )}
      {deleteMessage && (
        <InlineState tone="success" message={deleteMessage} />
      )}
      {deleteError && (
        <InlineState tone="danger" message={deleteError} />
      )}
      {statusMessage && (
        <InlineState tone="success" message={statusMessage} />
      )}
      {statusError && (
        <InlineState tone="danger" message={statusError} />
      )}
      {renameMessage && (
        <InlineState tone="success" message={renameMessage} />
      )}
      {renameError && (
        <InlineState tone="danger" message={renameError} />
      )}

      <div>
        <label className="flex min-w-0 items-center gap-3 rounded-xl border border-slate-200 bg-slate-50 px-3 focus-within:border-indigo-500 focus-within:bg-white focus-within:ring-2 focus-within:ring-indigo-100">
          <Search size={17} className="shrink-0 text-slate-400" />
          <input
            type="search"
            value={templateSearch}
            onChange={(event) => {
              setTemplateSearch(event.target.value);
              setExpandedFolderId(null);
            }}
            placeholder="ค้นหาชื่อ Template หรือชื่อ Version"
            className="h-11 min-w-0 flex-1 bg-transparent text-sm font-semibold text-slate-800 outline-none placeholder:text-slate-400"
          />
          <span className="shrink-0 rounded-full bg-white px-2 py-1 text-[10px] font-black text-slate-500">
            {visibleTemplateFolders.length} โฟลเดอร์
          </span>
        </label>
      </div>

      <div className="space-y-2">
        {visibleTemplateFolders.map((folder) => {
          const isExpanded = expandedFolderId === folder.groupId;
          return (
            <div key={folder.groupId} className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
              <div
                role="button"
                tabIndex={0}
                onClick={() => toggleTemplateFolder(folder.groupId)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    toggleTemplateFolder(folder.groupId);
                  }
                }}
                className="flex w-full cursor-pointer items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-slate-50"
              >
                <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border border-indigo-100 bg-indigo-50 text-indigo-600">
                  <Folder size={24} strokeWidth={1.8} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                    {editingFolderId === folder.groupId ? (
                      <div
                        className="flex min-w-[180px] max-w-full flex-1 items-center gap-1.5 sm:max-w-sm"
                        onClick={(event) => event.stopPropagation()}
                        onKeyDown={(event) => event.stopPropagation()}
                      >
                        <input
                          type="text"
                          value={editingFolderName}
                          onChange={(event) => setEditingFolderName(event.target.value)}
                          onKeyDown={(event) => {
                            event.stopPropagation();
                            if (event.key === "Enter") {
                              event.preventDefault();
                              void handleRenameFolder(folder);
                            }
                            if (event.key === "Escape") {
                              event.preventDefault();
                              cancelRenameFolder();
                            }
                          }}
                          disabled={renamingFolderId === folder.groupId}
                          ref={editingFolderInputRef}
                          className="h-8 min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-2.5 text-sm font-black text-slate-900 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100 disabled:bg-slate-100 disabled:text-slate-400"
                        />
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            void handleRenameFolder(folder);
                          }}
                          disabled={renamingFolderId === folder.groupId}
                          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:bg-slate-300"
                          title="บันทึก"
                          aria-label="บันทึกชื่อ Template"
                        >
                          {renamingFolderId === folder.groupId ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                        </button>
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            cancelRenameFolder();
                          }}
                          disabled={renamingFolderId === folder.groupId}
                          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 disabled:bg-slate-100 disabled:text-slate-400"
                          title="ยกเลิก"
                          aria-label="ยกเลิกการแก้ชื่อ Template"
                        >
                          <X size={14} />
                        </button>
                      </div>
                    ) : (
                      <>
                        <h3 className="truncate text-sm font-black text-slate-900">{folder.name}</h3>
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            startRenameFolder(folder);
                          }}
                          disabled={loadStatus !== "loaded" || renamingFolderId === folder.groupId}
                          className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-700 disabled:bg-slate-100 disabled:text-slate-300"
                          title="เปลี่ยนชื่อโฟลเดอร์"
                          aria-label={`เปลี่ยนชื่อโฟลเดอร์ ${folder.name}`}
                        >
                          <Pencil size={13} />
                        </button>
                      </>
                    )}
                    <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-black text-slate-600">
                      {folder.versions.length} Version
                    </span>
                    {folder.activeCount > 0 && (
                      <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-black text-emerald-700">
                        Active {folder.activeCount}
                      </span>
                    )}
                  </div>
                  <p className="mt-0.5 text-xs font-semibold text-slate-500">
                    ชื่อ Template ปัจจุบัน · {folder.pageCount} หน้า
                  </p>
                </div>
                <div className="shrink-0 text-slate-400">
                  {isExpanded ? <ChevronDown size={20} /> : <ChevronRight size={20} />}
                </div>
              </div>

              {isExpanded && (
                <div className="border-t border-slate-100 bg-slate-50 p-2">
                  <div className="grid gap-3 lg:grid-cols-2">
                    {folder.versions.map((template) => (
                      <div key={template.id} className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
                        <div className="flex gap-3">
                          <div className="relative h-24 w-20 shrink-0 overflow-hidden rounded-lg border border-slate-200 bg-slate-100">
                            {template.previewImageUrl ? (
                              <img src={template.previewImageUrl} alt={`${template.name} template preview`} className="h-full w-full bg-white object-contain" />
                            ) : (
                              <div className="flex h-full flex-col items-center justify-center gap-1 text-slate-400">
                                <FileImage size={22} strokeWidth={1.8} />
                                <span className="text-[9px] font-bold">No preview</span>
                              </div>
                            )}
                          </div>

                          <div className="min-w-0 flex-1 space-y-2">
                            {editingTemplateId === template.id ? (
                              <div className="space-y-2">
                                <div className="flex min-w-0 overflow-hidden rounded-xl border border-slate-200 bg-white focus-within:border-indigo-500 focus-within:ring-2 focus-within:ring-indigo-100">
                                  <span className="flex max-w-[45%] shrink-0 items-center truncate border-r border-slate-200 bg-slate-50 px-3 text-xs font-black text-slate-500">
                                    {folder.name} -
                                  </span>
                                  <input
                                    type="text"
                                    value={editingTemplateName}
                                    onChange={(event) => setEditingTemplateName(event.target.value)}
                                    onKeyDown={(event) => {
                                      if (event.key === "Enter") {
                                        event.preventDefault();
                                        void handleRenameTemplate(template, folder.name);
                                      }
                                      if (event.key === "Escape") cancelRenameTemplate();
                                    }}
                                    disabled={renamingTemplateId === template.id}
                                    autoFocus
                                    className="h-10 min-w-0 flex-1 px-3 text-sm font-bold text-slate-900 outline-none"
                                  />
                                </div>
                                <div className="flex flex-wrap gap-2">
                                  <button type="button" onClick={() => void handleRenameTemplate(template, folder.name)} disabled={renamingTemplateId === template.id} className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-indigo-600 px-3 text-xs font-black text-white disabled:bg-slate-300">
                                    {renamingTemplateId === template.id ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                                    บันทึก
                                  </button>
                                  <button type="button" onClick={cancelRenameTemplate} disabled={renamingTemplateId === template.id} className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-xs font-black text-slate-600">
                                    <X size={14} />
                                    ยกเลิก
                                  </button>
                                </div>
                              </div>
                            ) : (
                              <div className="flex items-start justify-between gap-2">
                                <div className="min-w-0">
                                  <div className="truncate text-sm font-black text-slate-900">{templateVersionDisplayName(template, folder.name)}</div>
                                  <div className="mt-1 flex flex-wrap gap-1.5">
                                    <span className="inline-flex items-center justify-center rounded-full bg-indigo-50 px-2.5 py-1 text-[10px] font-black uppercase leading-none text-indigo-700">
                                      {detectionModeLabel(template.detectionMode)}
                                    </span>
                                    <StatusBadge status={template.status} />
                                  </div>
                                </div>
                                <button
                                  type="button"
                                  onClick={() => startRenameTemplate(template, folder.name)}
                                  disabled={loadStatus !== "loaded" || deletingTemplateId === template.id || statusUpdatingTemplateId === template.id || renamingTemplateId === template.id}
                                  className="inline-flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-[11px] font-black text-slate-500 hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-700 disabled:bg-slate-100 disabled:text-slate-300"
                                  title="เปลี่ยนชื่อ Template"
                                  aria-label={`เปลี่ยนชื่อ ${template.name}`}
                                >
                                  <Pencil size={14} />
                                  เปลี่ยนชื่อ
                                </button>
                              </div>
                            )}

                            <div className="rounded-xl bg-slate-50 px-3 py-2 text-xs font-semibold text-slate-500">
                              {template.documentType || "No document type"} · {template.pageCount} หน้า
                            </div>

                            <div className="flex flex-wrap gap-2">
                              <ActionButton href={`/admin/templates/${template.id}/edit`} tone="primary">แก้ไข</ActionButton>
                              <label className="min-w-[150px]">
                                <span className="sr-only">Template status</span>
                                <select
                                  value={manageableStatuses.includes(template.status) ? (template.status === "disabled" ? "nonactive" : template.status) : template.status}
                                  onChange={(event) => {
                                    const nextStatus = event.target.value as TemplateStatus;
                                    if (nextStatus !== template.status) handleChangeTemplateStatus(template, nextStatus);
                                  }}
                                  disabled={loadStatus !== "loaded" || deletingTemplateId === template.id || statusUpdatingTemplateId === template.id || !manageableStatuses.includes(template.status)}
                                  className="h-9 w-full rounded-xl border border-slate-200 bg-white px-3 text-xs font-black text-slate-700 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100 disabled:bg-slate-100 disabled:text-slate-400"
                                >
                                  {!manageableStatuses.includes(template.status) && <option value={template.status}>{statusSelectLabel(template.status)}</option>}
                                  <option value="active">ใช้งานอยู่</option>
                                  <option value="nonactive">ปิดใช้งานชั่วคราว</option>
                                </select>
                              </label>
                              <button
                                type="button"
                                onClick={() => {
                                  setDeleteConfirmTemplate(template);
                                  setDeleteMessage("");
                                  setDeleteError("");
                                }}
                                disabled={loadStatus !== "loaded" || deletingTemplateId === template.id || statusUpdatingTemplateId === template.id}
                                className="ui-stable-action-sm rounded-xl border border-red-200 bg-white px-3 py-2 text-xs font-black text-red-600 hover:bg-red-50 disabled:border-slate-200 disabled:bg-slate-100 disabled:text-slate-400"
                              >
                                {deletingTemplateId === template.id ? "กำลังลบ..." : "ลบ"}
                              </button>
                            </div>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          );
        })}
        {loadStatus === "loaded" && visibleTemplateFolders.length === 0 && (
          <EmptyState title="ไม่พบ Template" message="ไม่มี Template ที่ตรงกับสถานะที่เลือก" />
        )}
      </div>

      <div className="hidden">
        {filteredTemplates.map((template) => (
          <div key={template.id} className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            <div className="relative h-44 border-b border-slate-200 bg-slate-100">
              {template.previewImageUrl ? (
                <img
                  src={template.previewImageUrl}
                  alt={`${template.name} template preview`}
                  className="h-full w-full bg-white object-contain"
                />
              ) : (
                <div className="flex h-full flex-col items-center justify-center gap-2 text-slate-400">
                  <FileImage size={30} strokeWidth={1.8} />
                  <span className="ui-caption font-semibold">ไม่มีภาพตัวอย่าง</span>
                </div>
              )}
              <div className="absolute bottom-3 right-3 rounded-full border border-slate-200 bg-white/95 px-2.5 py-1 text-[11px] font-bold tabular-nums text-slate-600 shadow-sm">
                {template.pageCount} หน้า
              </div>
            </div>
            <div className="space-y-3 p-4">
            <div>
              {editingTemplateId === template.id ? (
                <div className="space-y-2">
                  <label className="block space-y-1">
                    <span className="text-[10px] font-black uppercase tracking-wide text-slate-500">ชื่อ Template</span>
                    <input
                      type="text"
                      value={editingTemplateName}
                      onChange={(event) => setEditingTemplateName(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          void handleRenameTemplate(template, template.documentType || template.name);
                        }
                        if (event.key === "Escape") {
                          cancelRenameTemplate();
                        }
                      }}
                      disabled={renamingTemplateId === template.id}
                      autoFocus
                      className="h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm font-bold text-slate-900 outline-none transition-colors focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100 disabled:bg-slate-100 disabled:text-slate-400"
                    />
                  </label>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => void handleRenameTemplate(template, template.documentType || template.name)}
                      disabled={renamingTemplateId === template.id}
                      className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-indigo-600 px-3 text-xs font-black text-white transition-colors hover:bg-indigo-700 disabled:bg-slate-300 disabled:text-slate-500"
                    >
                      {renamingTemplateId === template.id ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                      บันทึก
                    </button>
                    <button
                      type="button"
                      onClick={cancelRenameTemplate}
                      disabled={renamingTemplateId === template.id}
                      className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 text-xs font-black text-slate-600 transition-colors hover:bg-slate-50 disabled:bg-slate-100 disabled:text-slate-400"
                    >
                      <X size={14} />
                      ยกเลิก
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex min-h-10 items-start justify-between gap-2">
                  <div className="line-clamp-2 text-sm font-black leading-5 text-slate-900">{template.name}</div>
                  <button
                    type="button"
                    onClick={() => startRenameTemplate(template, template.documentType || template.name)}
                    disabled={loadStatus !== "loaded" || deletingTemplateId === template.id || statusUpdatingTemplateId === template.id || renamingTemplateId === template.id}
                    className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 transition-colors hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-700 disabled:bg-slate-100 disabled:text-slate-300"
                    title="เปลี่ยนชื่อ Template"
                    aria-label={`เปลี่ยนชื่อ ${template.name}`}
                  >
                    <Pencil size={14} />
                  </button>
                </div>
              )}
              <div className="mt-1 flex flex-wrap gap-1.5">
                <StatusBadge status={template.status} />
                {loadStatus === "error" && (
                  <StatusBadge status="backend error" tone="warning" />
                )}
              </div>
            </div>
            <div className="rounded-xl bg-slate-50 px-3 py-2 text-xs font-semibold text-slate-500">
              {template.documentType || "No document type"} · Template preview
            </div>
            <div className="flex flex-wrap gap-2">
              <ActionButton href={`/admin/templates/${template.id}/edit`} tone="primary">แก้ไข</ActionButton>
              <label className="min-w-[170px]">
                <span className="sr-only">Template status</span>
                <select
                  value={manageableStatuses.includes(template.status) ? (template.status === "disabled" ? "nonactive" : template.status) : template.status}
                  onChange={(event) => {
                    const nextStatus = event.target.value as TemplateStatus;
                    if (nextStatus !== template.status) {
                      handleChangeTemplateStatus(template, nextStatus);
                    }
                  }}
                  disabled={
                    loadStatus !== "loaded" ||
                    deletingTemplateId === template.id ||
                    statusUpdatingTemplateId === template.id ||
                    !manageableStatuses.includes(template.status)
                  }
                  title={!manageableStatuses.includes(template.status) ? "Template ต้องผ่านการ Publish ก่อน จึงจะเปิดหรือปิดใช้งานได้" : undefined}
                  className="h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-xs font-black text-slate-700 shadow-sm outline-none transition-colors hover:border-indigo-200 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100 disabled:bg-slate-100 disabled:text-slate-400"
                >
                  {!manageableStatuses.includes(template.status) && (
                    <option value={template.status}>{statusSelectLabel(template.status)}</option>
                  )}
                  <option value="active">ใช้งานอยู่</option>
                  <option value="nonactive">ปิดใช้งานชั่วคราว</option>
                </select>
              </label>
              <button
                type="button"
                onClick={() => {
                  setDeleteConfirmTemplate(template);
                  setDeleteMessage("");
                  setDeleteError("");
                }}
                disabled={loadStatus !== "loaded" || deletingTemplateId === template.id || statusUpdatingTemplateId === template.id}
                className="ui-stable-action-sm rounded-xl border border-red-200 bg-white px-4 py-2.5 text-xs font-black text-red-600 transition-colors hover:bg-red-50 disabled:border-slate-200 disabled:bg-slate-100 disabled:text-slate-400"
              >
                {deletingTemplateId === template.id ? "กำลังลบ..." : "ลบ"}
              </button>
            </div>
            </div>
          </div>
        ))}
        {loadStatus === "loaded" && filteredTemplates.length === 0 && (
          <div className="md:col-span-2 xl:col-span-3">
            <EmptyState title="ไม่พบ Template" message="ไม่มี Template ที่ตรงกับสถานะที่เลือก" />
          </div>
        )}
      </div>
      </div>
      {isCreateModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4">
          <div className="w-full max-w-lg rounded-2xl border border-slate-200 bg-white shadow-2xl">
            <div className="flex items-start justify-between gap-3 border-b border-slate-100 p-5">
              <div>
                <h2 className="text-base font-black text-slate-900">Select Creation Type</h2>
                <p className="mt-1 text-xs font-semibold text-slate-500">
                  เลือกวิธีสร้างก่อนอัปโหลดไฟล์อ้างอิง
                </p>
              </div>
              <button
                type="button"
                onClick={() => setIsCreateModalOpen(false)}
                className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-slate-200 text-slate-500 hover:bg-slate-50"
              >
                <X size={16} />
              </button>
            </div>

            <div className="space-y-3 p-5">
              {[
                { value: "new_template", title: "Create New Template", note: "สร้าง Template ใหม่ แล้วสร้าง Version 1" },
                { value: "new_version", title: "Add New Version", note: "เพิ่ม Version ให้ Template เดิมและให้ระบบช่วย reuse ROI ถ้า Layout ใกล้เคียง" },
              ].map((option) => (
                <label
                  key={option.value}
                  className={`flex cursor-pointer gap-3 rounded-xl border p-3 ${
                    manualCreationType === option.value ? "border-indigo-300 bg-indigo-50" : "border-slate-200 bg-slate-50"
                  }`}
                >
                  <input
                    type="radio"
                    name="manualCreationType"
                    checked={manualCreationType === option.value}
                    onChange={() => setManualCreationType(option.value as "new_template" | "new_version")}
                    className="mt-1"
                  />
                  <span>
                    <span className="block text-xs font-black text-slate-900">{option.title}</span>
                    <span className="mt-0.5 block text-[11px] font-semibold leading-5 text-slate-500">{option.note}</span>
                  </span>
                </label>
              ))}

              {manualCreationType === "new_template" ? (
                <div className="space-y-3 rounded-2xl border border-slate-100 bg-slate-50 p-3">
                  <label className="block space-y-1.5">
                    <span className="text-[11px] font-black uppercase tracking-wide text-slate-500">ชื่อ Template</span>
                    <input
                      type="text"
                      value={newTemplateName}
                      onChange={(event) => setNewTemplateName(event.target.value)}
                      placeholder="เช่น ใบสมัครสมาชิก Version หลัก"
                      className="h-11 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-800 outline-none transition-colors focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                    />
                  </label>
                </div>
              ) : (
                <div className="space-y-3 rounded-2xl border border-slate-100 bg-slate-50 p-3">
                <label className="block space-y-1.5">
                  <span className="text-[11px] font-black uppercase tracking-wide text-slate-500">Template ในระบบ</span>
                  <select
                    value={selectedExistingDocumentType}
                    onChange={(event) => setSelectedExistingDocumentType(event.target.value)}
                    className="h-11 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-800 outline-none transition-colors focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                  >
                    <option value="" disabled hidden>เลือกจากรายการ</option>
                    {existingDocumentTypes.map((documentType) => (
                      <option key={documentType} value={documentType}>
                        {documentType}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block space-y-1.5">
                  <span className="text-[11px] font-black uppercase tracking-wide text-slate-500">ชื่อต่อท้าย Version</span>
                  <div className="flex min-w-0 overflow-hidden rounded-xl border border-slate-200 bg-white focus-within:border-indigo-500 focus-within:ring-2 focus-within:ring-indigo-100">
                    <span className="flex max-w-[45%] shrink-0 items-center truncate border-r border-slate-200 bg-slate-50 px-3 text-xs font-black text-slate-500">
                      {selectedExistingDocumentType.trim() || "Template"} -
                    </span>
                    <input
                      type="text"
                      value={newVersionNameSuffix}
                      onChange={(event) => setNewVersionNameSuffix(event.target.value)}
                      placeholder="เช่น ปรับฟอร์ม 2026"
                      className="h-11 min-w-0 flex-1 px-3 text-sm font-semibold text-slate-800 outline-none"
                    />
                  </div>
                </label>
                </div>
              )}

              {createRequestError && <InlineState tone="danger" message={createRequestError} />}

              {canUploadCreateReference ? (
              <label
                className={`flex min-h-36 cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed px-4 text-center transition-colors ${
                  isCreatingRequest || loadStatus !== "loaded"
                    ? "cursor-not-allowed border-slate-200 bg-white text-slate-400"
                    : "border-indigo-200 bg-indigo-50 text-indigo-700 hover:border-indigo-400"
                }`}
              >
                {isCreatingRequest ? <Loader2 size={28} className="animate-spin" /> : <UploadCloud size={32} />}
                <span className="mt-3 text-sm font-black">{isCreatingRequest ? "กำลังเตรียม Template Request..." : "Upload Reference Image"}</span>
                <span className="mt-1 text-xs font-semibold text-slate-500">รองรับ PNG, JPG, WebP และ PDF หลายหน้า</span>
                <input
                  type="file"
                  multiple
                  accept="image/*,application/pdf"
                  disabled={isCreatingRequest || loadStatus !== "loaded"}
                  onChange={(event) => {
                    handleCreateTemplateRequest(event.target.files);
                    event.currentTarget.value = "";
                  }}
                  className="sr-only"
                />
              </label>
              ) : (
                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-4 py-6 text-center text-xs font-bold text-slate-500">
                  กรอกข้อมูลให้ครบก่อนอัปโหลดไฟล์อ้างอิง
                </div>
              )}
            </div>
          </div>
        </div>
      )}
      {deleteConfirmTemplate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4">
          <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white shadow-2xl">
            <div className="flex items-start justify-between gap-3 border-b border-slate-100 p-5">
              <div>
                <h2 className="text-base font-black text-slate-900">ลบ Template นี้หรือไม่?</h2>
                <p className="mt-1 text-xs font-semibold leading-5 text-slate-500">
                  การลบนี้จะลบ Template และข้อมูลที่เกี่ยวข้องออกจากฐานข้อมูลถาวร
                </p>
              </div>
              <button
                type="button"
                onClick={() => setDeleteConfirmTemplate(null)}
                disabled={deletingTemplateId === deleteConfirmTemplate.id}
                className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-slate-200 text-slate-500 hover:bg-slate-50 disabled:bg-slate-100 disabled:text-slate-300"
                aria-label="ปิดหน้าต่างยืนยันการลบ"
              >
                <X size={16} />
              </button>
            </div>

            <div className="space-y-4 p-5">
              <div className="rounded-xl border border-red-100 bg-red-50 px-3 py-3">
                <p className="text-xs font-black text-red-900">{deleteConfirmTemplate.name}</p>
                <p className="mt-1 text-[11px] font-semibold leading-5 text-red-700">
                  จะลบหน้าเอกสาร, Field, Ignore Region และประวัติ Embedding ที่ผูกกับ Template นี้ด้วย
                </p>
              </div>

              {deleteError && <InlineState tone="danger" message={deleteError} />}

              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setDeleteConfirmTemplate(null)}
                  disabled={deletingTemplateId === deleteConfirmTemplate.id}
                  className="inline-flex h-10 items-center justify-center rounded-xl border border-slate-200 bg-white px-4 text-xs font-black text-slate-700 hover:bg-slate-50 disabled:bg-slate-100 disabled:text-slate-400"
                >
                  ยกเลิก
                </button>
                <button
                  type="button"
                  onClick={() => void confirmDeleteTemplate()}
                  disabled={deletingTemplateId === deleteConfirmTemplate.id}
                  className="inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-red-600 px-4 text-xs font-black text-white hover:bg-red-700 disabled:bg-slate-300 disabled:text-slate-500"
                >
                  {deletingTemplateId === deleteConfirmTemplate.id && <Loader2 size={14} className="animate-spin" />}
                  {deletingTemplateId === deleteConfirmTemplate.id ? "กำลังลบ..." : "ลบ Template"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
