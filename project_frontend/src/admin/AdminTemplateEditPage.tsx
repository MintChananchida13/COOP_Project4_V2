"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import WorkspaceTemplateEditor from "./workspace/WorkspaceTemplateEditorV2";
import AdjustZone from "../user/components/AdjustZone";
import {
  createIgnoreRegionApi,
  createTemplateFieldApi,
  createTemplatePageApi,
  deleteIgnoreRegionApi,
  deleteTemplateFieldApi,
  deleteTemplatePageApi,
  fetchTemplateBundle,
  updateIgnoreRegionApi,
  updateTemplateApi,
  updateTemplateFieldApi,
  updateTemplatePageApi,
} from "./adminApi";
import type { IgnoreRegion, RoiRatio, Template, TemplateField, TemplatePage } from "../types/ocr";

type LoadStatus = "loading" | "loaded" | "error";
type AdminEditorStage = "adjust" | "roi" | "decision";

const samplePage =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='750' height='1000' viewBox='0 0 750 1000'%3E%3Crect width='750' height='1000' fill='%23ffffff'/%3E%3Crect x='70' y='70' width='610' height='90' rx='8' fill='%23e2e8f0'/%3E%3Crect x='70' y='210' width='270' height='34' rx='5' fill='%23cbd5e1'/%3E%3Crect x='70' y='275' width='610' height='22' rx='4' fill='%23e2e8f0'/%3E%3Crect x='70' y='325' width='610' height='22' rx='4' fill='%23e2e8f0'/%3E%3Crect x='70' y='420' width='610' height='220' rx='8' fill='%23f1f5f9' stroke='%23cbd5e1'/%3E%3Ctext x='375' y='910' text-anchor='middle' font-family='Sarabun' font-size='24' fill='%2364758b'%3ETemplate Sample Page%3C/text%3E%3C/svg%3E";

interface AdminAdjustPageConfig {
  rotation: number;
  brightness: number;
  contrast: number;
  sharpness: number;
  perspectiveV: number;
  perspectiveH: number;
  flipH: boolean;
  flipV: boolean;
  cropBox: {
    x: number;
    y: number;
    width: number;
    height: number;
    renderedWidth?: number;
    renderedHeight?: number;
  } | null;
  cropCorners: { x: number; y: number }[] | null;
  isCropActive: boolean;
  isCropped: boolean;
  croppedLocalUrl: string | null;
}

interface AutoDetectedTemplateField {
  roi: RoiRatio;
  defaults: Partial<TemplateField>;
}

type TemplateBundle = { template: Template; pages: TemplatePage[]; fields: TemplateField[]; ignoreRegions: IgnoreRegion[] };

const defaultRoi = (pageNumber: number): RoiRatio => ({
  pageNumber,
  xRatio: 0.1,
  yRatio: 0.2,
  widthRatio: 0.32,
  heightRatio: 0.06,
});

const DEFAULT_FINAL_CONFIDENCE_THRESHOLD = 0.75;

const defaultAdjustPageConfig = (): AdminAdjustPageConfig => ({
  rotation: 0,
  brightness: 100,
  contrast: 100,
  sharpness: 0,
  perspectiveV: 0,
  perspectiveH: 0,
  flipH: false,
  flipV: false,
  cropBox: null,
  cropCorners: null,
  isCropActive: false,
  isCropped: false,
  croppedLocalUrl: null,
});

export default function AdminTemplateEditPage({ templateId }: { templateId: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const pendingDetectionMode = searchParams.get("detectionMode") === "main_page" ? "main_page" : searchParams.get("detectionMode") === "all_pages" ? "all_pages" : undefined;
  const pendingMainPageNumber = Math.max(1, Number(searchParams.get("mainPageNumber") || 1));
  const [selectedTemplate, setSelectedTemplate] = useState<Template | null>(null);
  const [selectedTemplatePages, setSelectedTemplatePages] = useState<TemplatePage[]>([]);
  const [selectedTemplateFields, setSelectedTemplateFields] = useState<TemplateField[]>([]);
  const [selectedIgnoreRegions, setSelectedIgnoreRegions] = useState<IgnoreRegion[]>([]);
  const [loadStatus, setLoadStatus] = useState<LoadStatus>("loading");
  const [saveStatus, setSaveStatus] = useState("");
  const [showUpdateSuccessDialog, setShowUpdateSuccessDialog] = useState(false);
  const [currentPage, setCurrentPage] = useState(0);
  const [editorStage, setEditorStage] = useState<AdminEditorStage>("adjust");
  const [adjustPageConfigs, setAdjustPageConfigs] = useState<AdminAdjustPageConfig[]>([]);
  const localFieldSequenceRef = useRef(0);
  const fieldUpdateSequenceRef = useRef(new Map<string, number>());
  const pendingLocalFieldPatchesRef = useRef(new Map<string, Partial<TemplateField>>());
  const dirtyFieldPatchesRef = useRef(new Map<string, Partial<TemplateField>>());
  const pendingDeletedFieldIdsRef = useRef(new Set<string>());
  const cancelledLocalFieldIdsRef = useRef(new Set<string>());
  const selectedTemplateFieldsRef = useRef<TemplateField[]>([]);

  const applyBundle = (bundle: TemplateBundle) => {
    setSelectedTemplate({
      ...bundle.template,
      detectionMode: pendingDetectionMode || bundle.template.detectionMode,
      mainPageNumber: pendingDetectionMode ? pendingMainPageNumber : bundle.template.mainPageNumber,
    });
    setSelectedTemplatePages(bundle.pages);
    const mergedBundleFields = bundle.fields
      .filter((field) => !pendingDeletedFieldIdsRef.current.has(field.id))
      .map((field) => ({ ...field, ...(dirtyFieldPatchesRef.current.get(field.id) || {}) }));
    const bundleFieldIds = new Set(mergedBundleFields.map((field) => field.id));
    const pendingLocalFields = selectedTemplateFieldsRef.current.filter(
      (field) =>
        field.id.startsWith("local_field_") &&
        !bundleFieldIds.has(field.id) &&
        pendingLocalFieldPatchesRef.current.has(field.id)
    );
    const nextFields = pendingLocalFields.length > 0 ? [...mergedBundleFields, ...pendingLocalFields] : mergedBundleFields;
    selectedTemplateFieldsRef.current = nextFields;
    setSelectedTemplateFields(nextFields);
    setSelectedIgnoreRegions(bundle.ignoreRegions);
    return nextFields;
  };

  const registerPendingLocalFields = (fieldsToRegister: TemplateField[]) => {
    fieldsToRegister.forEach((field) => {
      if (!field.id.startsWith("local_field_")) return;
      cancelledLocalFieldIdsRef.current.delete(field.id);
      pendingLocalFieldPatchesRef.current.set(field.id, {
        ...(pendingLocalFieldPatchesRef.current.get(field.id) || {}),
        ...field,
      });
      dirtyFieldPatchesRef.current.delete(field.id);
    });
  };

  const clearPendingLocalFields = (fieldsToClear: TemplateField[]) => {
    fieldsToClear.forEach((field) => {
      if (!field.id.startsWith("local_field_")) return;
      pendingLocalFieldPatchesRef.current.delete(field.id);
      dirtyFieldPatchesRef.current.delete(field.id);
      cancelledLocalFieldIdsRef.current.delete(field.id);
    });
  };

  const markPendingDeletedFields = (fieldsToDelete: TemplateField[]) => {
    fieldsToDelete.forEach((field) => {
      if (!field.id.startsWith("local_field_")) {
        pendingDeletedFieldIdsRef.current.add(field.id);
      } else {
        cancelledLocalFieldIdsRef.current.add(field.id);
      }
      pendingLocalFieldPatchesRef.current.delete(field.id);
      dirtyFieldPatchesRef.current.delete(field.id);
    });
  };

  useEffect(() => {
    selectedTemplateFieldsRef.current = selectedTemplateFields;
  }, [selectedTemplateFields]);

  const flushFieldDrafts = async () => {
    if (!canPersistToBackend) {
      setLocalOnly("Field changes saved locally.");
      return selectedTemplateFieldsRef.current;
    }

    let latestBundle: TemplateBundle | null = null;
    const deletedIds = Array.from(pendingDeletedFieldIdsRef.current).filter((fieldId) => !fieldId.startsWith("local_field_"));
    for (const fieldId of deletedIds) {
      latestBundle = await deleteTemplateFieldApi(templateId, fieldId);
      pendingDeletedFieldIdsRef.current.delete(fieldId);
      dirtyFieldPatchesRef.current.delete(fieldId);
      pendingLocalFieldPatchesRef.current.delete(fieldId);
    }

    let currentFields = selectedTemplateFieldsRef.current;
    const localFields = currentFields.filter(
      (field) => field.id.startsWith("local_field_") && !cancelledLocalFieldIdsRef.current.has(field.id)
    );
    for (const localField of localFields) {
      const localPatch = pendingLocalFieldPatchesRef.current.get(localField.id) || {};
      let fieldToCreate = { ...localField, ...localPatch };
      let persistedIdsBeforeCreate = new Set(currentFields.filter((field) => !field.id.startsWith("local_field_")).map((field) => field.id));
      latestBundle = await createTemplateFieldApi(templateId, fieldToCreate);
      const savedField =
        latestBundle.fields.find(
          (field) =>
            !persistedIdsBeforeCreate.has(field.id) &&
            field.templatePageId === fieldToCreate.templatePageId &&
            field.pageNumber === fieldToCreate.pageNumber &&
            field.useForVerification === fieldToCreate.useForVerification
        ) ||
        latestBundle.fields.find(
          (field) =>
            field.templatePageId === fieldToCreate.templatePageId &&
            field.pageNumber === fieldToCreate.pageNumber &&
            field.fieldName === fieldToCreate.fieldName &&
            field.sortOrder === fieldToCreate.sortOrder
        ) ||
        latestBundle.fields.find(
          (field) =>
            field.templatePageId === fieldToCreate.templatePageId &&
            field.pageNumber === fieldToCreate.pageNumber &&
            field.fieldName === fieldToCreate.fieldName
        );
      if (savedField) {
        const preservedField = {
          ...savedField,
          ...fieldToCreate,
          id: savedField.id,
          templateId: savedField.templateId,
          templatePageId: savedField.templatePageId,
        };
        currentFields = currentFields.map((field) => (field.id === localField.id ? preservedField : field));
        setSelectedTemplateFields(currentFields);
        selectedTemplateFieldsRef.current = currentFields;
        pendingLocalFieldPatchesRef.current.delete(localField.id);
        dirtyFieldPatchesRef.current.delete(localField.id);
        cancelledLocalFieldIdsRef.current.delete(localField.id);
      }
    }

    const dirtyEntries = Array.from(dirtyFieldPatchesRef.current.entries()).filter(([fieldId]) => !fieldId.startsWith("local_field_"));
    for (const [fieldId, patch] of dirtyEntries) {
      latestBundle = await updateTemplateFieldApi(templateId, fieldId, patch);
      dirtyFieldPatchesRef.current.delete(fieldId);
    }

    if (latestBundle) {
      if (latestBundle.template.pendingUpdate) {
        setSelectedTemplate((current) => (current ? { ...current, pendingUpdate: latestBundle.template.pendingUpdate } : current));
        pendingDeletedFieldIdsRef.current.clear();
        dirtyFieldPatchesRef.current.clear();
        pendingLocalFieldPatchesRef.current.clear();
        setSaved("Field ROI changes saved as pending maintenance update.");
      } else {
        applyBundle(latestBundle);
        setSaved("Field changes saved.");
      }
    }
    return selectedTemplateFieldsRef.current;
  };

  useEffect(() => {
    let cancelled = false;

    const loadTemplate = async () => {
      setLoadStatus("loading");
      try {
        const bundle = await fetchTemplateBundle(templateId);
        if (cancelled) return;
        applyBundle(bundle);
        setLoadStatus("loaded");
      } catch (error) {
        console.warn("Template editor load failed.", error);
        if (cancelled) return;
        setSelectedTemplate(null);
        setSelectedTemplatePages([]);
        setSelectedTemplateFields([]);
        setSelectedIgnoreRegions([]);
        setLoadStatus("error");
      }
    };

    loadTemplate();
    return () => {
      cancelled = true;
    };
  }, [templateId]);

  const imageList = useMemo(
    () => selectedTemplatePages.map((page) => page.normalizedImageUrl || page.sampleImageUrl || samplePage),
    [selectedTemplatePages]
  );
  const adjustPageIndices = useMemo(() => {
    if (selectedTemplate?.detectionMode !== "main_page") return selectedTemplatePages.map((_, index) => index);
    const firstPageIndex = selectedTemplatePages.findIndex((page) => page.pageNumber === 1);
    return selectedTemplatePages.length ? [firstPageIndex >= 0 ? firstPageIndex : 0] : [];
  }, [selectedTemplate?.detectionMode, selectedTemplatePages]);
  const adjustImages = useMemo(() => adjustPageIndices.map((index) => imageList[index]), [adjustPageIndices, imageList]);
  const visibleAdjustConfigs = adjustPageIndices.map((index) => adjustPageConfigs[index] || defaultAdjustPageConfig());
  const setVisibleAdjustConfigs = useCallback<React.Dispatch<React.SetStateAction<AdminAdjustPageConfig[]>>>((action) => {
    setAdjustPageConfigs((current) => {
      const visible = adjustPageIndices.map((index) => current[index] || defaultAdjustPageConfig());
      const updated = typeof action === "function" ? action(visible) : action;
      const next = [...current];
      adjustPageIndices.forEach((index, visibleIndex) => { if (updated[visibleIndex]) next[index] = updated[visibleIndex]; });
      return next;
    });
  }, [adjustPageIndices]);

  const workspacePages = adjustPageIndices.map((index) => ({
    ...selectedTemplatePages[index],
  })).map((page) => ({
    id: page.id,
    src: page.normalizedImageUrl || page.sampleImageUrl || samplePage,
    label: page.pageName || `Page ${page.pageNumber}`,
  }));
  const workspacePageIds = new Set(workspacePages.map((page) => page.id));
  const workspaceFields = selectedTemplateFields.filter((field) => workspacePageIds.has(field.templatePageId));
  const workspaceIgnoreRegions = selectedIgnoreRegions.filter((region) => workspacePageIds.has(region.templatePageId));

  useEffect(() => {
    setAdjustPageConfigs((current) => {
      if (current.length === selectedTemplatePages.length) return current;
      return selectedTemplatePages.map((_, index) => current[index] || defaultAdjustPageConfig());
    });
  }, [selectedTemplatePages.length]);

  const safeCurrentPage = Math.min(currentPage, Math.max(selectedTemplatePages.length - 1, 0));
  const currentTemplatePage = selectedTemplatePages[safeCurrentPage];
  const extractionFieldCount = workspaceFields.filter((field) => !field.useForVerification).length;
  const verificationAnchorCount = workspaceFields.filter((field) => field.useForVerification).length;
  const isUpdatingPublishedTemplate = selectedTemplate?.status === "active";
  const processSteps = [
    {
      id: "adjust",
      label: "2.0 ปรับแต่งภาพ",
      description: "ตรวจภาพและ Crop เอกสาร",
      status: editorStage === "roi" || editorStage === "decision" ? "done" : "active",
    },
    {
      id: "roi",
      label: "2.1 กำหนด Extraction ROI",
      description: "วาดพื้นที่ข้อมูลสำหรับ OCR และทดสอบ OCR",
      status: editorStage === "roi" ? "active" : editorStage === "decision" ? "done" : "next",
    },
    {
      id: "verification",
      label: "2.2 กำหนด Verification ROI",
      description: "เลือกจุดอ้างอิงสำหรับยืนยัน Template",
      status: editorStage === "roi" && verificationAnchorCount > 0 ? "active" : editorStage === "decision" ? "done" : "next",
    },
    {
      id: "decision",
      label: "2.3 สรุปและอัปเดต Template",
      description: "ตรวจสอบจำนวน ROI ก่อนอัปเดต",
      status: editorStage === "decision" ? "active" : "next",
    },
  ] as const;

  const setSaved = (message: string) => setSaveStatus(message);
  const setLocalOnly = (message: string) => setSaveStatus(`${message} Backend unavailable; kept local edit.`);
  const canPersistToBackend = loadStatus === "loaded";

  const handleConfirmAdjustedImages = async (finalImages: string[]) => {
    if (finalImages.length === 0) return;

    const previousPages = selectedTemplatePages;
    const adjustedByIndex = new Map(adjustPageIndices.map((pageIndex, index) => [pageIndex, finalImages[index]]));
    const nextPages = selectedTemplatePages.map((page, index) => adjustedByIndex.get(index)
      ? { ...page, normalizedImageUrl: adjustedByIndex.get(index)! }
      : page);
    setSelectedTemplatePages(nextPages);
    setCurrentPage(0);
    setEditorStage("roi");

    if (!canPersistToBackend) {
      setLocalOnly("Adjusted images saved locally.");
      return;
    }

    try {
      let latestBundle: Awaited<ReturnType<typeof updateTemplatePageApi>> | null = null;
      for (const page of adjustPageIndices.map((index) => nextPages[index])) {
        if (!page.id.startsWith("local_page_")) {
          latestBundle = await updateTemplatePageApi(templateId, page.id, {
            normalizedImageUrl: page.normalizedImageUrl,
          });
        }
      }
      if (latestBundle?.template.pendingUpdate) {
        setSelectedTemplate((current) => (current ? { ...current, pendingUpdate: latestBundle.template.pendingUpdate } : current));
        setSelectedTemplatePages(nextPages);
        setSaved("Adjusted images saved as pending maintenance update. Workspace ROI uses the local adjusted image.");
      } else {
        if (latestBundle) applyBundle(latestBundle);
        setSaved("Adjusted images saved. Workspace ROI is ready.");
      }
    } catch (error) {
      console.warn("Adjusted image save failed.", error);
      setSelectedTemplatePages(nextPages.length > 0 ? nextPages : previousPages);
      setLocalOnly("Adjusted images saved locally.");
    }
  };

  const persistTemplatePatch = async (patch: Partial<Template>) => {
    if (!selectedTemplate) return;
    setSelectedTemplate({ ...selectedTemplate, ...patch });
    if (!canPersistToBackend) {
      setLocalOnly("Template saved locally.");
      return;
    }
    try {
      const bundle = await updateTemplateApi(templateId, patch);
      applyBundle(bundle);
      setSaved(bundle.template.pendingUpdate ? "Template changes saved as pending maintenance update." : "Template saved.");
    } catch (error) {
      console.warn("Template save failed.", error);
      setLocalOnly("Template saved locally.");
    }
  };

  const handleAddPage = () => {
    const nextPageNumber = selectedTemplatePages.length + 1;
    const optimisticPage: TemplatePage = {
      id: `local_page_${Date.now()}`,
      templateId,
      pageNumber: nextPageNumber,
      pageName: `Page ${nextPageNumber}`,
      sampleImageUrl: samplePage,
      normalizedImageUrl: samplePage,
      similarityThreshold: selectedTemplate?.similarityThreshold ?? 0.5,
      finalConfidenceThreshold: selectedTemplate?.finalConfidenceThreshold ?? DEFAULT_FINAL_CONFIDENCE_THRESHOLD,
    };
    setSelectedTemplatePages((prev) => [...prev, optimisticPage]);

    if (!canPersistToBackend) {
      setLocalOnly("Page added locally.");
      return;
    }

    createTemplatePageApi(templateId, nextPageNumber, samplePage)
      .then((bundle) => {
        applyBundle(bundle);
        setSaved("Page saved.");
      })
      .catch((error) => {
        console.warn("Page create failed.", error);
        setLocalOnly("Page added locally.");
      });
  };

  const handleUpdatePage = (pageId: string, patch: Partial<TemplatePage>) => {
    setSelectedTemplatePages((prev) => prev.map((page) => (page.id === pageId ? { ...page, ...patch } : page)));
    if (!canPersistToBackend || pageId.startsWith("local_page_")) {
      setLocalOnly("Page saved locally.");
      return;
    }

    updateTemplatePageApi(templateId, pageId, patch)
      .then((bundle) => {
        applyBundle(bundle);
        setSaved("Page saved.");
      })
      .catch((error) => {
        console.warn("Page update failed.", error);
        setLocalOnly("Page saved locally.");
      });
  };

  const handleRemovePage = (pageId: string) => {
    const previousPages = selectedTemplatePages;
    const previousFields = selectedTemplateFields;
    const previousIgnoreRegions = selectedIgnoreRegions;
    setSelectedTemplatePages((prev) => prev.filter((page) => page.id !== pageId));
    setSelectedTemplateFields((prev) => prev.filter((field) => field.templatePageId !== pageId));
    setSelectedIgnoreRegions((prev) => prev.filter((region) => region.templatePageId !== pageId));
    setCurrentPage(0);

    if (!canPersistToBackend || pageId.startsWith("local_page_")) {
      setLocalOnly("Page removed locally.");
      return;
    }

    deleteTemplatePageApi(templateId, pageId)
      .then((bundle) => {
        applyBundle(bundle);
        setSaved("Page removed.");
      })
      .catch((error) => {
        console.warn("Page delete failed.", error);
        if (!pageId.startsWith("local_page_")) {
          setSelectedTemplatePages(previousPages);
          setSelectedTemplateFields(previousFields);
          setSelectedIgnoreRegions(previousIgnoreRegions);
        }
        setLocalOnly("Page removal could not be persisted.");
      });
  };

  const handleAddField = (roi?: RoiRatio, defaults?: Partial<TemplateField>) => {
    if (!currentTemplatePage) return;
    const nextIndex = selectedTemplateFields.filter((field) => field.templatePageId === currentTemplatePage.id && !field.useForVerification).length + 1;
    const nextRoi = roi || defaultRoi(currentTemplatePage.pageNumber);
    localFieldSequenceRef.current += 1;
    const optimisticId = defaults?.id || `local_field_${Date.now()}_${localFieldSequenceRef.current}`;
    const fieldName = defaults?.useForVerification
      ? defaults?.fieldName || `verification_${selectedTemplateFields.filter((field) => field.useForVerification).length + 1}`
      : defaults?.fieldName || `field_${nextIndex}`;
    const optimisticField: TemplateField = {
      id: optimisticId,
      templateId,
      templatePageId: currentTemplatePage.id,
      pageNumber: currentTemplatePage.pageNumber,
      fieldName,
      displayLabel: defaults?.displayLabel || fieldName || `Field ${nextIndex}`,
      roi: nextRoi,
      dataType: defaults?.dataType || "text",
      userSelectable: defaults?.userSelectable ?? true,
      defaultSelected: defaults?.defaultSelected ?? true,
      useForVerification: defaults?.useForVerification ?? false,
      expectedText: defaults?.expectedText || "",
      requiredForVerification: defaults?.requiredForVerification ?? false,
      extractionMethod:
        defaults?.extractionMethod ||
        (defaults?.dataType === "image" ? "extract_image" : defaults?.dataType === "table" ? "table_recognition_v2" : "paddle_thai_ocr"),
      roiPadding: defaults?.roiPadding ?? 0,
      verificationWeight: defaults?.verificationWeight ?? 1,
      sortOrder: nextIndex,
    };
    selectedTemplateFieldsRef.current = [...selectedTemplateFieldsRef.current, optimisticField];
    setSelectedTemplateFields((prev) => [...prev, optimisticField]);
    registerPendingLocalFields([optimisticField]);
    setSaved("Field added to draft.");
  };

  const handleUpdateField = (fieldId: string, patch: Partial<TemplateField>) => {
    const requestSequence = (fieldUpdateSequenceRef.current.get(fieldId) || 0) + 1;
    fieldUpdateSequenceRef.current.set(fieldId, requestSequence);
    selectedTemplateFieldsRef.current = selectedTemplateFieldsRef.current.map((field) => (field.id === fieldId ? { ...field, ...patch } : field));
    setSelectedTemplateFields((prev) => prev.map((field) => (field.id === fieldId ? { ...field, ...patch } : field)));
    if (fieldId.startsWith("local_field_")) {
      pendingLocalFieldPatchesRef.current.set(fieldId, {
        ...(pendingLocalFieldPatchesRef.current.get(fieldId) || {}),
        ...patch,
      });
    } else {
      dirtyFieldPatchesRef.current.set(fieldId, {
        ...(dirtyFieldPatchesRef.current.get(fieldId) || {}),
        ...patch,
      });
    }
    setSaved("Field changes saved to draft.");
  };

  const handleReorderFields = (orderedFieldIds: string[]) => {
    const orderMap = new Map(orderedFieldIds.map((fieldId, index) => [fieldId, index + 1]));
    const previousFields = selectedTemplateFields;
    const changedFields = previousFields
      .filter((field) => orderMap.has(field.id) && field.sortOrder !== orderMap.get(field.id))
      .map((field) => ({ ...field, sortOrder: orderMap.get(field.id) || field.sortOrder || 0 }));

    if (changedFields.length === 0) return;

    setSelectedTemplateFields((prev) =>
      prev.map((field) => {
        const nextSortOrder = orderMap.get(field.id);
        return nextSortOrder ? { ...field, sortOrder: nextSortOrder } : field;
      })
    );
    selectedTemplateFieldsRef.current = selectedTemplateFieldsRef.current.map((field) => {
      const nextSortOrder = orderMap.get(field.id);
      return nextSortOrder ? { ...field, sortOrder: nextSortOrder } : field;
    });

    changedFields.forEach((field) => {
      const patch = { sortOrder: field.sortOrder };
      if (field.id.startsWith("local_field_")) {
        pendingLocalFieldPatchesRef.current.set(field.id, {
          ...(pendingLocalFieldPatchesRef.current.get(field.id) || {}),
          ...patch,
        });
      } else {
        dirtyFieldPatchesRef.current.set(field.id, {
          ...(dirtyFieldPatchesRef.current.get(field.id) || {}),
          ...patch,
        });
      }
    });
    setSaved("Field order saved to draft.");
  };

  const handleDeleteField = (fieldId: string) => {
    selectedTemplateFieldsRef.current = selectedTemplateFieldsRef.current.filter((field) => field.id !== fieldId);
    setSelectedTemplateFields((prev) => prev.filter((field) => field.id !== fieldId));
    pendingLocalFieldPatchesRef.current.delete(fieldId);
    dirtyFieldPatchesRef.current.delete(fieldId);
    if (fieldId.startsWith("local_field_")) {
      cancelledLocalFieldIdsRef.current.add(fieldId);
    }
    if (!fieldId.startsWith("local_field_")) {
      pendingDeletedFieldIdsRef.current.add(fieldId);
    }
    setSaved("Field deleted from draft.");
  };

  const handleEnterTemplateTestMode = () => {
    void (async () => {
      await flushFieldDrafts();
      if (selectedTemplate?.status === "active") {
        setEditorStage("decision");
        setSaveStatus("");
        return;
      }
      window.dispatchEvent(new Event("admin-route-transition-start"));
      router.push(`/admin/templates/${templateId}/test`);
    })();
  };

  const handleConfirmTemplateUpdate = () => {
    void (async () => {
      await flushFieldDrafts();
      if (!selectedTemplate) return;
      const templateDraft = selectedTemplate;
      const patch: Partial<Template> = {
        detectionMode: templateDraft.detectionMode,
        mainPageNumber: templateDraft.mainPageNumber,
      };
      setSelectedTemplate((current) => (current ? { ...current, ...patch } : current));
      let savedBundle: TemplateBundle | null = null;
      if (canPersistToBackend) {
        try {
          const bundle = await updateTemplateApi(templateId, patch);
          savedBundle = bundle;
          applyBundle(bundle);
        } catch (error) {
          console.warn("Template update settings save failed.", error);
          setLocalOnly("Template update settings kept locally.");
          return;
        }
      }
      setSaved(savedBundle?.template.pendingUpdate ? "Template changes saved as pending maintenance update." : "Template update saved.");
      setShowUpdateSuccessDialog(true);
    })();
  };

  const handleReplacePageExtractionFields = (pageNumber: number, detectedFields: AutoDetectedTemplateField[]) => {
    const targetPage = selectedTemplatePages.find((page) => page.pageNumber === pageNumber);
    if (!targetPage) return;

    const previousFields = selectedTemplateFields;
    const fieldsToDelete = previousFields.filter((field) => field.pageNumber === pageNumber && !field.useForVerification);
    const remainingFields = previousFields.filter((field) => !(field.pageNumber === pageNumber && !field.useForVerification));

    const optimisticFields = detectedFields.map(({ roi, defaults }, index) => {
      localFieldSequenceRef.current += 1;
      const fieldNumber = index + 1;
      const fieldName = defaults.fieldName || `field_${fieldNumber}`;
      return {
        id: `local_field_${Date.now()}_${localFieldSequenceRef.current}`,
        templateId,
        templatePageId: targetPage.id,
        pageNumber,
        fieldName,
        displayLabel: defaults.displayLabel && defaults.fieldName === fieldName ? defaults.displayLabel : fieldName,
        roi,
        dataType: defaults.dataType || "text",
        userSelectable: defaults.userSelectable ?? true,
        defaultSelected: defaults.defaultSelected ?? true,
        useForVerification: false,
        expectedText: "",
        requiredForVerification: false,
        extractionMethod:
          defaults.extractionMethod ||
          (defaults.dataType === "image" ? "extract_image" : defaults.dataType === "table" ? "table_recognition_v2" : "paddle_thai_ocr"),
        roiPadding: defaults.roiPadding ?? 0,
        verificationWeight: defaults.verificationWeight ?? 1,
        sortOrder: fieldNumber,
      } satisfies TemplateField;
    });

    registerPendingLocalFields(optimisticFields);
    markPendingDeletedFields(fieldsToDelete);
    setSelectedTemplateFields([...remainingFields, ...optimisticFields]);
    selectedTemplateFieldsRef.current = [...remainingFields, ...optimisticFields];

    setSaved(`Auto ROI replaced ${fieldsToDelete.length} old fields with ${optimisticFields.length} fields.`);
  };

  const handleReplaceExtractionFieldsForPages = (
    items: { pageNumber: number; fields: AutoDetectedTemplateField[] }[]
  ) => {
    const pageNumbers = new Set(items.map((item) => item.pageNumber));
    const templatePageByNumber = new Map(selectedTemplatePages.map((page) => [page.pageNumber, page]));
    const previousFields = selectedTemplateFields;
    const fieldsToDelete = previousFields.filter((field) => pageNumbers.has(field.pageNumber) && !field.useForVerification);
    const remainingFields = previousFields.filter((field) => !(pageNumbers.has(field.pageNumber) && !field.useForVerification));
    const optimisticFields = items.flatMap(({ pageNumber, fields: detectedFields }) => {
      const targetPage = templatePageByNumber.get(pageNumber);
      if (!targetPage) return [];

      return detectedFields.map(({ roi, defaults }, index) => {
        localFieldSequenceRef.current += 1;
        const fieldNumber = index + 1;
        const fieldName = defaults.fieldName || `field_${pageNumber}_${fieldNumber}`;
        return {
          id: `local_field_${Date.now()}_${localFieldSequenceRef.current}`,
          templateId,
          templatePageId: targetPage.id,
          pageNumber,
          fieldName,
          displayLabel: defaults.displayLabel && defaults.fieldName === fieldName ? defaults.displayLabel : fieldName,
          roi,
          dataType: defaults.dataType || "text",
          userSelectable: defaults.userSelectable ?? true,
          defaultSelected: defaults.defaultSelected ?? true,
          useForVerification: false,
          expectedText: "",
          requiredForVerification: false,
          extractionMethod:
            defaults.extractionMethod ||
            (defaults.dataType === "image" ? "extract_image" : defaults.dataType === "table" ? "table_recognition_v2" : "paddle_thai_ocr"),
          roiPadding: defaults.roiPadding ?? 0,
          verificationWeight: defaults.verificationWeight ?? 1,
          sortOrder: fieldNumber,
        } satisfies TemplateField;
      });
    });

    registerPendingLocalFields(optimisticFields);
    markPendingDeletedFields(fieldsToDelete);
    setSelectedTemplateFields([...remainingFields, ...optimisticFields]);
    selectedTemplateFieldsRef.current = [...remainingFields, ...optimisticFields];

    setSaved(`Auto ROI replaced ${fieldsToDelete.length} old fields with ${optimisticFields.length} fields across ${items.length} pages.`);
  };

  const handleAddIgnoreRegion = (roi?: RoiRatio) => {
    if (!currentTemplatePage) return;
    const nextIndex = selectedIgnoreRegions.length + 1;
    const nextRoi = roi || {
      pageNumber: currentTemplatePage.pageNumber,
      xRatio: 0.5,
      yRatio: 0.25,
      widthRatio: 0.22,
      heightRatio: 0.08,
    };
    const optimisticRegion: IgnoreRegion = {
      id: `local_ignore_${Date.now()}`,
      templateId,
      templatePageId: currentTemplatePage.id,
      pageNumber: currentTemplatePage.pageNumber,
      fieldName: `ignore_region_${nextIndex}`,
      roi: nextRoi,
    };
    setSelectedIgnoreRegions((prev) => [...prev, optimisticRegion]);

    if (!canPersistToBackend) {
      setLocalOnly("Ignore region added locally.");
      return;
    }

    createIgnoreRegionApi(templateId, optimisticRegion)
      .then((bundle) => {
        applyBundle(bundle);
        setSaved("Ignore region saved.");
      })
      .catch((error) => {
        console.warn("Ignore region create failed.", error);
        setLocalOnly("Ignore region added locally.");
      });
  };

  const handleUpdateIgnoreRegion = (regionId: string, patch: Partial<IgnoreRegion>) => {
    setSelectedIgnoreRegions((prev) => prev.map((region) => (region.id === regionId ? { ...region, ...patch } : region)));
    if (!canPersistToBackend || regionId.startsWith("local_ignore_")) {
      setLocalOnly("Ignore region saved locally.");
      return;
    }

    updateIgnoreRegionApi(templateId, regionId, patch)
      .then((bundle) => {
        applyBundle(bundle);
        setSaved("Ignore region saved.");
      })
      .catch((error) => {
        console.warn("Ignore region update failed.", error);
        setLocalOnly("Ignore region saved locally.");
      });
  };

  const handleDeleteIgnoreRegion = (regionId: string) => {
    const previousRegions = selectedIgnoreRegions;
    setSelectedIgnoreRegions((prev) => prev.filter((region) => region.id !== regionId));
    if (!canPersistToBackend || regionId.startsWith("local_ignore_")) {
      setLocalOnly("Ignore region deleted locally.");
      return;
    }

    deleteIgnoreRegionApi(templateId, regionId)
      .then((bundle) => {
        applyBundle(bundle);
        setSaved("Ignore region deleted.");
      })
      .catch((error) => {
        console.warn("Ignore region delete failed.", error);
        setSelectedIgnoreRegions(previousRegions);
        setLocalOnly("Ignore region delete could not be persisted.");
      });
  };

  if (loadStatus === "loading") {
    return (
      <section className="rounded-xl border border-slate-200 bg-white p-6 text-sm font-semibold text-slate-500 shadow-sm">
        Loading template editor...
      </section>
    );
  }

  if (!selectedTemplate) {
    return (
      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-lg font-black text-slate-900">Template not found</h2>
        <Link href="/admin/templates" className="mt-4 inline-flex rounded-xl bg-indigo-600 px-4 py-2 text-xs font-black text-white">
          Back to Templates
        </Link>
      </section>
    );
  }

  return (
    <section className="space-y-4">
      {saveStatus && <p className="rounded-xl bg-emerald-50 px-3 py-2 text-xs font-bold text-emerald-700">{saveStatus}</p>}

      {selectedTemplatePages.length > 0 && (
        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <h2 className="text-sm font-black uppercase tracking-wide text-slate-800">กระบวนการเตรียม Template</h2>
              <p className="mt-1 text-xs font-semibold text-slate-500">
                ทำตามลำดับจากปรับภาพ วาด ROI ทดสอบ OCR แล้วตั้ง Verification Anchors ก่อนเข้าสู่ Test Mode
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <span className="rounded-full bg-sky-50 px-3 py-2 text-[11px] font-black text-sky-700">
                Pages: {workspacePages.length}
              </span>
              <span className="rounded-full bg-indigo-50 px-3 py-2 text-[11px] font-black text-indigo-700">
                ROI: {extractionFieldCount}
              </span>
              <span className="rounded-full bg-amber-50 px-3 py-2 text-[11px] font-black text-amber-700">
                Anchors: {verificationAnchorCount}
              </span>
            </div>
          </div>

          <div className={`mt-4 grid gap-2 ${isUpdatingPublishedTemplate ? "md:grid-cols-4" : "md:grid-cols-3"}`}>
            {processSteps.filter((item) => isUpdatingPublishedTemplate || item.id !== "decision").map((item) => {
                const isActive = item.status === "active";
                const isDone = item.status === "done";
                return (
                  <div
                    key={item.id}
                    aria-current={isActive ? "step" : undefined}
                    className={`rounded-xl border px-4 py-3 text-left ${
                      isActive
                        ? "border-indigo-300 bg-indigo-50 text-indigo-800"
                        : isDone
                          ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                          : "border-slate-200 bg-slate-50 text-slate-600"
                    }`}
                  >
                    <span className="block text-xs font-black leading-snug">{item.label}</span>
                    <span className="mt-1 block text-[11px] font-semibold leading-snug opacity-75">{item.description}</span>
                  </div>
                );
            })}
          </div>
        </div>
      )}

      {selectedTemplatePages.length > 0 && currentTemplatePage && (
        <div className="space-y-4">
          {editorStage === "adjust" ? (
            <AdjustZone
              imagesList={adjustImages}
              currentIndex={Math.max(0, adjustPageIndices.indexOf(safeCurrentPage))}
              onIndexChange={(index) => setCurrentPage(adjustPageIndices[index] ?? 0)}
              pagesConfig={visibleAdjustConfigs}
              setPagesConfig={setVisibleAdjustConfigs}
              onBatchConfirm={(finalImages) => {
                void handleConfirmAdjustedImages(finalImages);
              }}
              confirmLabel="ยืนยันเอกสารและดำเนินการต่อ"
            />
          ) : editorStage === "roi" ? (
            <WorkspaceTemplateEditor
              templateId={templateId}
              pages={workspacePages}
              currentPage={Math.max(0, adjustPageIndices.indexOf(safeCurrentPage))}
              onPageChange={(index) => setCurrentPage(adjustPageIndices[index] ?? 0)}
              fields={workspaceFields}
              ignoreRegions={workspaceIgnoreRegions}
              onAddField={handleAddField}
              onUpdateField={handleUpdateField}
              onReorderFields={handleReorderFields}
              onReplacePageExtractionFields={handleReplacePageExtractionFields}
              onReplaceExtractionFieldsForPages={handleReplaceExtractionFieldsForPages}
              onDeleteField={handleDeleteField}
              onAddIgnoreRegion={handleAddIgnoreRegion}
              onUpdateIgnoreRegion={handleUpdateIgnoreRegion}
              onDeleteIgnoreRegion={handleDeleteIgnoreRegion}
              onGenerateEmbedding={handleEnterTemplateTestMode}
              onRunTestMode={handleEnterTemplateTestMode}
              onBeforeRunTest={flushFieldDrafts}
              testModeLabel={selectedTemplate?.status === "active" ? "สรุปและอัปเดต" : "Test Mode"}
              onBackToAdjust={() => setEditorStage("adjust")}
            />
          ) : (
            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <p className="text-[11px] font-black uppercase tracking-wider text-indigo-600">2.3</p>
                  <h2 className="mt-1 text-xl font-black text-slate-950">สรุปการแก้ไข Template</h2>
                  <p className="mt-2 text-sm font-semibold text-slate-500">ตรวจสอบจำนวน ROI ก่อนอัปเดต Template</p>
                </div>
                <button type="button" onClick={() => setEditorStage("roi")} className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-black text-slate-600 hover:bg-slate-50">
                  กลับไปแก้ ROI
                </button>
              </div>
              <div className="mt-5 grid gap-3 sm:grid-cols-2">
                <div className="rounded-xl border border-indigo-100 bg-indigo-50 p-4">
                  <p className="text-xs font-bold text-indigo-700">Extraction ROI</p>
                  <p className="mt-1 text-2xl font-black text-indigo-950">{extractionFieldCount}</p>
                </div>
                <div className="rounded-xl border border-amber-100 bg-amber-50 p-4">
                  <p className="text-xs font-bold text-amber-700">Verification ROI</p>
                  <p className="mt-1 text-2xl font-black text-amber-950">{verificationAnchorCount}</p>
                </div>
              </div>
              <div className="mt-5 flex justify-end">
                <button type="button" onClick={handleConfirmTemplateUpdate} className="rounded-xl bg-emerald-600 px-5 py-3 text-xs font-black text-white shadow-sm hover:bg-emerald-700">
                  อัปเดต Template
                </button>
              </div>
            </section>
          )}
        </div>
      )}

      {showUpdateSuccessDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 px-4">
          <div className="w-full max-w-md rounded-2xl border border-emerald-100 bg-white p-6 text-center shadow-2xl">
            <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-emerald-100 text-2xl font-black text-emerald-700">
              ✓
            </div>
            <h3 className="mt-4 text-lg font-black text-slate-950">อัปเดต Template สำเร็จ</h3>
            <p className="mt-2 text-sm font-semibold leading-6 text-slate-500">
              ระบบบันทึกการเปลี่ยนแปลงล่าสุดของ Template แล้ว และจะพากลับไปยังคลัง Template
            </p>
            <button
              type="button"
              onClick={() => {
                setShowUpdateSuccessDialog(false);
                window.dispatchEvent(new Event("admin-route-transition-start"));
                router.push("/admin/templates");
              }}
              className="mt-5 rounded-xl bg-emerald-600 px-5 py-2.5 text-xs font-black text-white shadow-sm hover:bg-emerald-700"
            >
              ตกลง
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
