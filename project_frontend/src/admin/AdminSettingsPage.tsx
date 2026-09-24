"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, Loader2, Pencil, Plus, Save, Settings, X } from "lucide-react";
import {
  fetchOcrModelSettings,
  fetchVerificationStrategy,
  OcrModelConfig,
  OcrModelKind,
  OcrModelSettings,
  saveOcrModel,
  updateActiveOcrModels,
  updateVerificationStrategy,
  VerificationStrategy,
} from "./adminApi";
import { InlineState, LoadingState } from "../shared/ui";

const verificationStrategyOptions: { value: VerificationStrategy; label: string; description: string }[] = [
  {
    value: "standard",
    label: "แบบมาตรฐาน (Standard)",
    description: "ใช้คะแนนรวมจาก Layout, Text Anchor และ Image Anchor ตามค่าน้ำหนักของ Template แล้วเทียบกับเกณฑ์ความมั่นใจ โดย Anchor ที่บังคับต้องผ่านเสมอ",
  },
  {
    value: "strict",
    label: "แบบเข้มงวด (Strict)",
    description: "ต้องผ่านเกณฑ์ Layout ก่อน จากนั้นตรวจ Text Anchor และ Image Anchor ทีละรายการ หาก Anchor ใดไม่ผ่าน ระบบจะไม่เลือก Template นั้นโดยไม่ใช้คะแนนรวมเป็นตัวตัดสินสุดท้าย",
  },
];

const emptyModelSettings: OcrModelSettings = {
  active: { text_detection: "", text_recognition: "" },
  models: { text_detection: [], text_recognition: [] },
};

const modelKindLabels: Record<OcrModelKind, string> = {
  text_detection: "Text Detection",
  text_recognition: "Text Recognition",
};

type ModelDraft = Omit<OcrModelConfig, "id"> & { id?: string };

const emptyModelDraft: ModelDraft = {
  displayName: "",
  singleApiPath: "",
  batchApiPath: "",
};

interface MaintenanceDraft {
  startDate: string;
  startTime: string;
  expectedEndDate: string;
  expectedEndTime: string;
  message: string;
}

interface ScheduledMaintenance {
  status: "scheduled";
  scheduledStartAt: string;
  expectedEndAt: string;
  message: string;
}

const emptyMaintenanceDraft: MaintenanceDraft = {
  startDate: "",
  startTime: "",
  expectedEndDate: "",
  expectedEndTime: "",
  message: "",
};

const modelFormPlaceholders: Record<OcrModelKind, Record<keyof Omit<ModelDraft, "id">, string>> = {
  text_detection: {
    displayName: "PP-OCRv6 Medium",
    singleApiPath: "/api/v1/text-detections?version=v6",
    batchApiPath: "/api/v1/text-detection-batches?version=v6",
  },
  text_recognition: {
    displayName: "Thai PP-OCRv5 Mobile",
    singleApiPath: "/api/v1/text-recognitions",
    batchApiPath: "/api/v1/text-recognition-batches",
  },
};

const combineDateTime = (date: string, time: string) => (date && time ? `${date}T${time}` : "");

const formatMaintenanceDateTime = (value: string) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  const datePart = new Intl.DateTimeFormat("th-TH", {
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(date);
  const timePart = new Intl.DateTimeFormat("th-TH", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
  return `${datePart} เวลา ${timePart} น.`;
};

export default function AdminSettingsPage() {
  const [savedStrategy, setSavedStrategy] = useState<VerificationStrategy>("standard");
  const [draftStrategy, setDraftStrategy] = useState<VerificationStrategy>("standard");
  const [modelSettings, setModelSettings] = useState<OcrModelSettings>(emptyModelSettings);
  const [draftDetectionModelId, setDraftDetectionModelId] = useState("");
  const [draftRecognitionModelId, setDraftRecognitionModelId] = useState("");
  const [loadStatus, setLoadStatus] = useState<"loading" | "loaded" | "error">("loading");
  const [strategySaveStatus, setStrategySaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [modelSaveStatus, setModelSaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [strategyFeedback, setStrategyFeedback] = useState("");
  const [modelFeedback, setModelFeedback] = useState("");
  const [isModelManagerOpen, setIsModelManagerOpen] = useState(false);
  const [activeManagerTab, setActiveManagerTab] = useState<OcrModelKind>("text_detection");
  const [editingModel, setEditingModel] = useState<{ kind: OcrModelKind; draft: ModelDraft } | null>(null);
  const [modelFormStatus, setModelFormStatus] = useState<"idle" | "saving" | "error">("idle");
  const [modelFormError, setModelFormError] = useState("");
  const [maintenanceDraft, setMaintenanceDraft] = useState<MaintenanceDraft>(emptyMaintenanceDraft);
  const [scheduledMaintenance, setScheduledMaintenance] = useState<ScheduledMaintenance | null>(null);
  const [isMaintenanceEditing, setIsMaintenanceEditing] = useState(true);
  const [maintenanceError, setMaintenanceError] = useState("");

  useEffect(() => {
    let cancelled = false;
    const loadSettings = async () => {
      setLoadStatus("loading");
      setStrategyFeedback("");
      setModelFeedback("");
      try {
        const [strategy, ocrSettings] = await Promise.all([fetchVerificationStrategy(), fetchOcrModelSettings()]);
        if (cancelled) return;
        setSavedStrategy(strategy);
        setDraftStrategy(strategy);
        setModelSettings(ocrSettings);
        setDraftDetectionModelId(ocrSettings.active.text_detection);
        setDraftRecognitionModelId(ocrSettings.active.text_recognition);
        setLoadStatus("loaded");
      } catch (error) {
        console.warn("Settings load failed.", error);
        if (cancelled) return;
        setLoadStatus("error");
        setStrategyFeedback(error instanceof Error ? error.message : "โหลดการตั้งค่าระบบไม่สำเร็จ");
      }
    };
    loadSettings();
    return () => {
      cancelled = true;
    };
  }, []);

  const hasStrategyChanges = draftStrategy !== savedStrategy;
  const hasModelChanges =
    draftDetectionModelId !== modelSettings.active.text_detection ||
    draftRecognitionModelId !== modelSettings.active.text_recognition;
  const hasUnsavedChanges = hasStrategyChanges || hasModelChanges;
  const selectedOption = useMemo(
    () => verificationStrategyOptions.find((option) => option.value === draftStrategy) || verificationStrategyOptions[0],
    [draftStrategy]
  );

  useEffect(() => {
    if (!hasUnsavedChanges) return;
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [hasUnsavedChanges]);

  const handleSaveStrategy = async () => {
    if (!hasStrategyChanges || strategySaveStatus === "saving") return;
    setStrategySaveStatus("saving");
    setStrategyFeedback("");
    try {
      const persistedStrategy = await updateVerificationStrategy(draftStrategy);
      setSavedStrategy(persistedStrategy);
      setDraftStrategy(persistedStrategy);
      setStrategySaveStatus("saved");
      setStrategyFeedback("บันทึกการตั้งค่าเรียบร้อยแล้ว");
      window.setTimeout(() => setStrategySaveStatus((current) => (current === "saved" ? "idle" : current)), 1800);
    } catch (error) {
      console.warn("Verification strategy update failed.", error);
      setStrategySaveStatus("error");
      setStrategyFeedback(error instanceof Error ? error.message : "บันทึกรูปแบบการตรวจสอบ Template ไม่สำเร็จ");
    }
  };

  const handleSaveModels = async () => {
    if (!hasModelChanges || modelSaveStatus === "saving") return;
    setModelSaveStatus("saving");
    setModelFeedback("");
    try {
      const persistedSettings = await updateActiveOcrModels({
        textDetectionModelId: draftDetectionModelId,
        textRecognitionModelId: draftRecognitionModelId,
      });
      setModelSettings(persistedSettings);
      setDraftDetectionModelId(persistedSettings.active.text_detection);
      setDraftRecognitionModelId(persistedSettings.active.text_recognition);
      setModelSaveStatus("saved");
      setModelFeedback("บันทึกการตั้งค่าโมเดลเรียบร้อยแล้ว");
      window.setTimeout(() => setModelSaveStatus((current) => (current === "saved" ? "idle" : current)), 1800);
    } catch (error) {
      console.warn("OCR model settings update failed.", error);
      setModelSaveStatus("error");
      setModelFeedback(error instanceof Error ? error.message : "บันทึกการตั้งค่าโมเดลไม่สำเร็จ");
    }
  };

  const openAddModel = (kind: OcrModelKind) => {
    setEditingModel({ kind, draft: emptyModelDraft });
    setModelFormStatus("idle");
    setModelFormError("");
  };

  const openEditModel = (kind: OcrModelKind, model: OcrModelConfig) => {
    setEditingModel({ kind, draft: { ...model } });
    setModelFormStatus("idle");
    setModelFormError("");
  };

  const handleSaveModelForm = async () => {
    if (!editingModel || modelFormStatus === "saving") return;
    setModelFormStatus("saving");
    setModelFormError("");
    try {
      const persistedSettings = await saveOcrModel(editingModel.kind, editingModel.draft);
      setModelSettings(persistedSettings);
      setDraftDetectionModelId((current) => current || persistedSettings.active.text_detection);
      setDraftRecognitionModelId((current) => current || persistedSettings.active.text_recognition);
      setEditingModel(null);
      setModelFormStatus("idle");
    } catch (error) {
      console.warn("OCR model save failed.", error);
      setModelFormStatus("error");
      setModelFormError(error instanceof Error ? error.message : "บันทึกโมเดลไม่สำเร็จ");
    }
  };

  const handleScheduleMaintenance = () => {
    const scheduledStartAt = combineDateTime(maintenanceDraft.startDate, maintenanceDraft.startTime);
    const expectedEndAt = combineDateTime(maintenanceDraft.expectedEndDate, maintenanceDraft.expectedEndTime);
    const message = maintenanceDraft.message.trim();

    if (!scheduledStartAt) {
      setMaintenanceError("กรุณาเลือกวันที่และเวลาเริ่มปิดปรับปรุง");
      return;
    }
    if (!expectedEndAt) {
      setMaintenanceError("กรุณาเลือกวันที่และเวลาที่คาดว่าจะเปิดให้บริการ");
      return;
    }
    if (new Date(expectedEndAt).getTime() <= new Date(scheduledStartAt).getTime()) {
      setMaintenanceError("เวลาที่คาดว่าจะเปิดให้บริการต้องอยู่หลังเวลาเริ่มปิดปรับปรุง");
      return;
    }
    if (!message) {
      setMaintenanceError("กรุณากรอกข้อความแจ้งผู้ใช้งาน");
      return;
    }

    setScheduledMaintenance({
      status: "scheduled",
      scheduledStartAt,
      expectedEndAt,
      message,
    });
    setMaintenanceDraft((current) => ({ ...current, message }));
    setIsMaintenanceEditing(false);
    setMaintenanceError("");
  };

  const handleEditMaintenance = () => {
    if (scheduledMaintenance) {
      const [startDate, startTime = ""] = scheduledMaintenance.scheduledStartAt.split("T");
      const [expectedEndDate, expectedEndTime = ""] = scheduledMaintenance.expectedEndAt.split("T");
      setMaintenanceDraft({
        startDate,
        startTime,
        expectedEndDate,
        expectedEndTime,
        message: scheduledMaintenance.message,
      });
    }
    setIsMaintenanceEditing(true);
    setMaintenanceError("");
  };

  const handleCancelMaintenance = () => {
    setScheduledMaintenance(null);
    setMaintenanceDraft(emptyMaintenanceDraft);
    setIsMaintenanceEditing(true);
    setMaintenanceError("");
  };

  const renderModelSelect = (
    kind: OcrModelKind,
    title: string,
    description: string,
    value: string,
    onChange: (value: string) => void
  ) => (
    <label className="block space-y-2">
      <span className="block text-xs font-black text-slate-900">{title}</span>
      <span className="block text-xs font-semibold text-slate-500">{description}</span>
      <select
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
          setModelSaveStatus("idle");
          setModelFeedback("");
        }}
        className="h-11 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-800 outline-none transition-colors focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
      >
        {modelSettings.models[kind].map((model) => (
          <option key={model.id} value={model.id}>{model.displayName}</option>
        ))}
      </select>
    </label>
  );

  const renderModelManager = () => {
    const models = modelSettings.models[activeManagerTab];
    const activeModelId = modelSettings.active[activeManagerTab];
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4">
        <div className="flex max-h-[88vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl">
          <div className="flex items-start justify-between gap-3 border-b border-slate-100 p-5">
            <div>
              <h2 className="text-base font-black text-slate-900">จัดการโมเดล OCR</h2>
              <p className="mt-1 text-xs font-semibold text-slate-500">เพิ่มหรือแก้ไข API Path ของโมเดลที่ Gateway ใช้งาน</p>
            </div>
            <button type="button" onClick={() => { setIsModelManagerOpen(false); setEditingModel(null); }} className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-slate-200 text-slate-500 hover:bg-slate-50" aria-label="ปิด">
              <X size={16} />
            </button>
          </div>
          <div className="flex flex-wrap gap-2 border-b border-slate-100 px-5 py-3">
            {(["text_detection", "text_recognition"] as OcrModelKind[]).map((kind) => (
              <button key={kind} type="button" onClick={() => { setActiveManagerTab(kind); setEditingModel(null); }} className={`h-9 rounded-xl border px-3 text-xs font-black transition-colors ${activeManagerTab === kind ? "border-indigo-500 bg-indigo-600 text-white" : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"}`}>
                {modelKindLabels[kind]}
              </button>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-5">
            {editingModel ? (
              <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
                <h3 className="text-sm font-black text-slate-900">{editingModel.draft.id ? "แก้ไขโมเดล" : "เพิ่มโมเดล"} {modelKindLabels[editingModel.kind]}</h3>
                <div className="mt-4 grid gap-3">
                  {[
                    ["Display Name", "displayName"],
                    ["Single API Path", "singleApiPath"],
                    ["Batch API Path", "batchApiPath"],
                  ].map(([label, key]) => (
                    <label key={key} className="block space-y-1.5">
                      <span className="text-[11px] font-black uppercase tracking-wide text-slate-500">{label}</span>
                      <input
                        type="text"
                        value={String(editingModel.draft[key as keyof ModelDraft] || "")}
                        placeholder={modelFormPlaceholders[editingModel.kind][key as keyof Omit<ModelDraft, "id">]}
                        onChange={(event) => setEditingModel((current) => current ? { ...current, draft: { ...current.draft, [key]: event.target.value } } : current)}
                        className="h-11 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-800 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                      />
                    </label>
                  ))}
                </div>
                {modelFormStatus === "error" && modelFormError && <div className="mt-4"><InlineState tone="danger" message={modelFormError} /></div>}
                <div className="mt-4 flex justify-end gap-2">
                  <button type="button" onClick={() => setEditingModel(null)} className="h-10 rounded-xl border border-slate-200 bg-white px-4 text-xs font-black text-slate-700 hover:bg-slate-50">ยกเลิก</button>
                  <button type="button" onClick={() => void handleSaveModelForm()} disabled={modelFormStatus === "saving"} className="inline-flex h-10 items-center gap-2 rounded-xl bg-indigo-600 px-4 text-xs font-black text-white hover:bg-indigo-700 disabled:bg-slate-300">
                    {modelFormStatus === "saving" && <Loader2 size={14} className="animate-spin" />}
                    {editingModel.draft.id ? "บันทึกโมเดล" : "เพิ่มโมเดล"}
                  </button>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="flex justify-end">
                  <button type="button" onClick={() => openAddModel(activeManagerTab)} className="inline-flex h-10 items-center gap-2 rounded-xl bg-indigo-600 px-3 text-xs font-black text-white hover:bg-indigo-700">
                    <Plus size={14} />
                    เพิ่มโมเดล
                  </button>
                </div>
                {models.map((model) => (
                  <div key={model.id} className="rounded-xl border border-slate-200 bg-white p-4">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="text-sm font-black text-slate-900">{model.displayName}</h3>
                          {model.id === activeModelId && <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-black text-emerald-700">ใช้งานอยู่</span>}
                        </div>
                        <div className="mt-3 grid gap-2 text-xs font-semibold text-slate-500">
                          <div><span className="font-black text-slate-700">Single API Path</span><br />{model.singleApiPath}</div>
                          <div><span className="font-black text-slate-700">Batch API Path</span><br />{model.batchApiPath}</div>
                        </div>
                      </div>
                      <button type="button" onClick={() => openEditModel(activeManagerTab, model)} className="inline-flex h-9 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-xs font-black text-slate-600 hover:bg-slate-50">
                        <Pencil size={14} />
                        แก้ไข
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  };

  const renderMaintenanceSettings = () => {
    const isScheduled = scheduledMaintenance?.status === "scheduled";
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
          <div className="max-w-3xl">
            <h3 className="text-sm font-black text-slate-900">การปิดปรับปรุงระบบ</h3>
            <p className="mt-1 text-xs font-semibold leading-5 text-slate-500">
              กำหนดช่วงเวลาสำหรับปรับปรุงระบบและแจ้งเตือนผู้ใช้งานล่วงหน้า
            </p>
          </div>
          <div
            className={`inline-flex w-fit items-center gap-2 rounded-full px-3 py-1 text-[11px] font-black ${
              isScheduled ? "bg-amber-50 text-amber-700" : "bg-emerald-50 text-emerald-700"
            }`}
          >
            <span className={`h-2 w-2 rounded-full ${isScheduled ? "bg-amber-500" : "bg-emerald-500"}`} />
            {isScheduled ? "มีกำหนดปิดปรับปรุง" : "เปิดให้บริการตามปกติ"}
          </div>
        </div>

        {isScheduled && !isMaintenanceEditing ? (
          <div className="mt-5 space-y-4">
            <div className="grid gap-3 rounded-xl border border-slate-200 bg-slate-50 p-4 text-xs font-semibold text-slate-600 sm:grid-cols-2">
              <div>
                <p className="font-black text-slate-800">เริ่ม:</p>
                <p className="mt-1">{formatMaintenanceDateTime(scheduledMaintenance.scheduledStartAt)}</p>
              </div>
              <div>
                <p className="font-black text-slate-800">คาดว่าจะเปิด:</p>
                <p className="mt-1">{formatMaintenanceDateTime(scheduledMaintenance.expectedEndAt)}</p>
              </div>
              <div className="sm:col-span-2">
                <p className="font-black text-slate-800">ข้อความ:</p>
                <p className="mt-1 leading-5">{scheduledMaintenance.message}</p>
              </div>
            </div>
            <div className="flex flex-col justify-end gap-2 sm:flex-row">
              <button
                type="button"
                onClick={handleEditMaintenance}
                className="inline-flex h-10 items-center justify-center rounded-xl border border-slate-200 bg-white px-4 text-xs font-black text-slate-700 hover:bg-slate-50"
              >
                แก้ไขกำหนดการ
              </button>
              <button
                type="button"
                onClick={handleCancelMaintenance}
                className="inline-flex h-10 items-center justify-center rounded-xl border border-red-200 bg-white px-4 text-xs font-black text-red-700 hover:bg-red-50"
              >
                ยกเลิกกำหนดการ
              </button>
            </div>
          </div>
        ) : (
          <div className="mt-5 space-y-4">
            <div>
              <p className="text-xs font-black text-slate-900">สถานะระบบ</p>
              <div className="mt-2 inline-flex items-center gap-2 rounded-full bg-emerald-50 px-3 py-1 text-[11px] font-black text-emerald-700">
                <span className="h-2 w-2 rounded-full bg-emerald-500" />
                เปิดให้บริการตามปกติ
              </div>
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <div>
                <p className="text-xs font-black text-slate-900">วันที่และเวลาเริ่มปิดปรับปรุง</p>
                <div className="mt-2 grid gap-2 sm:grid-cols-[minmax(0,1fr)_8rem]">
                  <input
                    type="date"
                    value={maintenanceDraft.startDate}
                    onChange={(event) => {
                      setMaintenanceDraft((current) => ({ ...current, startDate: event.target.value }));
                      setMaintenanceError("");
                    }}
                    className="h-11 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-800 outline-none transition-colors focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                  />
                  <input
                    type="time"
                    value={maintenanceDraft.startTime}
                    onChange={(event) => {
                      setMaintenanceDraft((current) => ({ ...current, startTime: event.target.value }));
                      setMaintenanceError("");
                    }}
                    className="h-11 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-800 outline-none transition-colors focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                  />
                </div>
              </div>

              <div>
                <p className="text-xs font-black text-slate-900">วันที่และเวลาที่คาดว่าจะเปิดให้บริการ</p>
                <div className="mt-2 grid gap-2 sm:grid-cols-[minmax(0,1fr)_8rem]">
                  <input
                    type="date"
                    value={maintenanceDraft.expectedEndDate}
                    onChange={(event) => {
                      setMaintenanceDraft((current) => ({ ...current, expectedEndDate: event.target.value }));
                      setMaintenanceError("");
                    }}
                    className="h-11 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-800 outline-none transition-colors focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                  />
                  <input
                    type="time"
                    value={maintenanceDraft.expectedEndTime}
                    onChange={(event) => {
                      setMaintenanceDraft((current) => ({ ...current, expectedEndTime: event.target.value }));
                      setMaintenanceError("");
                    }}
                    className="h-11 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-800 outline-none transition-colors focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                  />
                </div>
              </div>
            </div>

            <label className="block space-y-2">
              <span className="block text-xs font-black text-slate-900">ข้อความแจ้งผู้ใช้งาน</span>
              <textarea
                value={maintenanceDraft.message}
                onChange={(event) => {
                  setMaintenanceDraft((current) => ({ ...current, message: event.target.value }));
                  setMaintenanceError("");
                }}
                placeholder="อัปเดต Template และปรับปรุงระบบ"
                rows={3}
                className="w-full resize-none rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm font-semibold text-slate-800 outline-none transition-colors placeholder:text-slate-400 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
              />
              <span className="block text-xs font-semibold text-slate-500">ข้อความนี้จะแสดงให้ผู้ใช้งานเห็นในการแจ้งเตือน</span>
            </label>

            {maintenanceError && <InlineState tone="danger" message={maintenanceError} />}

            <div className="flex justify-end">
              <button
                type="button"
                onClick={handleScheduleMaintenance}
                className="inline-flex h-11 items-center justify-center rounded-xl bg-indigo-600 px-4 text-xs font-black text-white shadow-sm transition-colors hover:bg-indigo-700"
              >
                กำหนดเวลาปิดปรับปรุง
              </button>
            </div>
          </div>
        )}
      </div>
    );
  };

  return (
    <section className="space-y-5">
      <div>
        <p className="ui-caption font-semibold text-blue-600">ตั้งค่าระบบ</p>
        <h2 className="ui-page-title mt-1 text-slate-950">ตั้งค่าระบบ</h2>
        <p className="ui-body mt-1 text-slate-500">กำหนดค่าที่มีผลกับการทำงานส่วนกลางของระบบผู้ดูแล</p>
      </div>
      {hasUnsavedChanges && (
        <InlineState
          tone="warning"
          message="มีการเปลี่ยนแปลงที่ยังไม่ได้บันทึก กรุณากดบันทึกในส่วนที่แก้ไขก่อนออกจากหน้านี้"
        />
      )}
      {loadStatus === "loading" && <LoadingState message="กำลังโหลดการตั้งค่าระบบ..." />}
      {loadStatus === "error" && <InlineState tone="danger" message={strategyFeedback || "โหลดการตั้งค่าระบบไม่สำเร็จ"} />}
      {loadStatus === "loaded" && (
        <>
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
              <div className="max-w-3xl">
                <h3 className="text-sm font-black text-slate-900">การตรวจสอบ Template</h3>
                <p className="mt-1 text-xs font-semibold leading-5 text-slate-500">เลือกรูปแบบการตรวจสอบที่ใช้กับ Template ทั้งหมดในระบบ</p>
              </div>
              <div className="rounded-full bg-slate-50 px-3 py-1 text-[11px] font-black text-slate-500">ปัจจุบัน: {savedStrategy === "strict" ? "Strict" : "Standard"}</div>
            </div>
            <div className="mt-5 grid gap-3">
              {verificationStrategyOptions.map((option) => {
                const checked = draftStrategy === option.value;
                return (
                  <label key={option.value} className={`flex cursor-pointer gap-3 rounded-xl border p-4 transition-colors ${checked ? "border-indigo-300 bg-indigo-50" : "border-slate-200 bg-white hover:bg-slate-50"}`}>
                    <input type="radio" name="verificationStrategy" value={option.value} checked={checked} onChange={() => { setDraftStrategy(option.value); setStrategySaveStatus("idle"); setStrategyFeedback(""); }} className="mt-1" />
                    <span className="min-w-0">
                      <span className="block text-sm font-black text-slate-900">{option.label}</span>
                      <span className="mt-1 block text-xs font-semibold leading-5 text-slate-500">{option.description}</span>
                    </span>
                  </label>
                );
              })}
            </div>
            <p className="mt-4 text-xs font-semibold leading-5 text-slate-500">{selectedOption.description}</p>
            {strategySaveStatus === "saved" && strategyFeedback && <div className="mt-4"><InlineState tone="success" message={strategyFeedback} /></div>}
            {strategySaveStatus === "error" && strategyFeedback && <div className="mt-4"><InlineState tone="danger" message={strategyFeedback} /></div>}
            <div className="mt-5 flex justify-end">
              <button type="button" onClick={() => void handleSaveStrategy()} disabled={!hasStrategyChanges || strategySaveStatus === "saving"} className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-indigo-600 px-4 text-xs font-black text-white shadow-sm transition-colors hover:bg-indigo-700 disabled:bg-slate-300 disabled:text-slate-500">
                {strategySaveStatus === "saving" ? <Loader2 size={15} className="animate-spin" /> : strategySaveStatus === "saved" ? <Check size={15} /> : <Save size={15} />}
                {strategySaveStatus === "saving" ? "กำลังบันทึก..." : "บันทึกการตั้งค่า"}
              </button>
            </div>
          </div>

          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h3 className="text-sm font-black text-slate-900">การตั้งค่าโมเดล OCR</h3>
                <p className="mt-1 text-xs font-semibold leading-5 text-slate-500">เลือกโมเดลที่ระบบใช้ในการประมวลผล OCR</p>
              </div>
              <button type="button" onClick={() => setIsModelManagerOpen(true)} className="inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-xs font-black text-slate-700 hover:bg-slate-50">
                <Settings size={14} />
                จัดการโมเดล
              </button>
            </div>
            <div className="mt-5 grid gap-4 lg:grid-cols-2">
              {renderModelSelect("text_detection", "Text Detection Model", "โมเดลสำหรับตรวจหาตำแหน่งข้อความ", draftDetectionModelId, setDraftDetectionModelId)}
              {renderModelSelect("text_recognition", "Text Recognition Model", "โมเดลสำหรับอ่านข้อความ", draftRecognitionModelId, setDraftRecognitionModelId)}
            </div>
            {modelSaveStatus === "saved" && modelFeedback && <div className="mt-4"><InlineState tone="success" message={modelFeedback} /></div>}
            {modelSaveStatus === "error" && modelFeedback && <div className="mt-4"><InlineState tone="danger" message={modelFeedback} /></div>}
            <div className="mt-5 flex justify-end">
              <button type="button" onClick={() => void handleSaveModels()} disabled={!hasModelChanges || modelSaveStatus === "saving"} className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-indigo-600 px-4 text-xs font-black text-white shadow-sm transition-colors hover:bg-indigo-700 disabled:bg-slate-300 disabled:text-slate-500">
                {modelSaveStatus === "saving" ? <Loader2 size={15} className="animate-spin" /> : modelSaveStatus === "saved" ? <Check size={15} /> : <Save size={15} />}
                {modelSaveStatus === "saving" ? "กำลังบันทึก..." : "บันทึกการตั้งค่าโมเดล"}
              </button>
            </div>
          </div>

          {renderMaintenanceSettings()}
        </>
      )}
      {isModelManagerOpen && renderModelManager()}
    </section>
  );
}
