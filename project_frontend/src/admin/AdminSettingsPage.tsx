"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, Loader2, Save } from "lucide-react";
import {
  fetchVerificationStrategy,
  updateVerificationStrategy,
  VerificationStrategy,
} from "./adminApi";
import { InlineState, LoadingState } from "../shared/ui";

const verificationStrategyOptions: {
  value: VerificationStrategy;
  label: string;
  description: string;
}[] = [
  {
    value: "standard",
    label: "แบบมาตรฐาน (Standard)",
    description:
      "ใช้คะแนนรวมจาก Layout, Text Anchor และ Image Anchor ตามค่าน้ำหนักของ Template แล้วเทียบกับเกณฑ์ความมั่นใจ โดย Anchor ที่บังคับต้องผ่านเสมอ",
  },
  {
    value: "strict",
    label: "แบบเข้มงวด (Strict)",
    description:
      "ต้องผ่านเกณฑ์ Layout ก่อน จากนั้นตรวจ Text Anchor และ Image Anchor ทีละรายการ หาก Anchor ใดไม่ผ่าน ระบบจะไม่เลือก Template นั้นโดยไม่ใช้คะแนนรวมเป็นตัวตัดสินสุดท้าย",
  },
];

export default function AdminSettingsPage() {
  const [savedStrategy, setSavedStrategy] = useState<VerificationStrategy>("standard");
  const [draftStrategy, setDraftStrategy] = useState<VerificationStrategy>("standard");
  const [loadStatus, setLoadStatus] = useState<"loading" | "loaded" | "error">("loading");
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [feedbackMessage, setFeedbackMessage] = useState("");

  useEffect(() => {
    let cancelled = false;

    const loadSettings = async () => {
      setLoadStatus("loading");
      setFeedbackMessage("");
      try {
        const strategy = await fetchVerificationStrategy();
        if (cancelled) return;
        setSavedStrategy(strategy);
        setDraftStrategy(strategy);
        setLoadStatus("loaded");
      } catch (error) {
        console.warn("Verification strategy load failed.", error);
        if (cancelled) return;
        setLoadStatus("error");
        setFeedbackMessage(error instanceof Error ? error.message : "โหลดการตั้งค่าระบบไม่สำเร็จ");
      }
    };

    loadSettings();

    return () => {
      cancelled = true;
    };
  }, []);

  const hasChanges = draftStrategy !== savedStrategy;
  const selectedOption = useMemo(
    () => verificationStrategyOptions.find((option) => option.value === draftStrategy) || verificationStrategyOptions[0],
    [draftStrategy]
  );

  const handleSave = async () => {
    if (!hasChanges || saveStatus === "saving") return;

    setSaveStatus("saving");
    setFeedbackMessage("");
    try {
      const persistedStrategy = await updateVerificationStrategy(draftStrategy);
      setSavedStrategy(persistedStrategy);
      setDraftStrategy(persistedStrategy);
      setSaveStatus("saved");
      setFeedbackMessage("บันทึกการตั้งค่าเรียบร้อยแล้ว");
      window.setTimeout(() => {
        setSaveStatus((current) => (current === "saved" ? "idle" : current));
      }, 1800);
    } catch (error) {
      console.warn("Verification strategy update failed.", error);
      setSaveStatus("error");
      setFeedbackMessage(error instanceof Error ? error.message : "บันทึกรูปแบบการตรวจสอบ Template ไม่สำเร็จ");
    }
  };

  return (
    <section className="space-y-5">
      <div>
        <p className="ui-caption font-semibold text-blue-600">ตั้งค่าระบบ</p>
        <h2 className="ui-page-title mt-1 text-slate-950">ตั้งค่าระบบ</h2>
        <p className="ui-body mt-1 text-slate-500">กำหนดค่าที่มีผลกับการทำงานส่วนกลางของระบบผู้ดูแล</p>
      </div>

      {loadStatus === "loading" && <LoadingState message="กำลังโหลดการตั้งค่าระบบ..." />}

      {loadStatus === "error" && (
        <InlineState tone="danger" message={feedbackMessage || "โหลดการตั้งค่าระบบไม่สำเร็จ"} />
      )}

      {loadStatus === "loaded" && (
        <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
            <div className="max-w-3xl">
              <h3 className="text-sm font-black text-slate-900">การตรวจสอบ Template</h3>
              <p className="mt-1 text-xs font-semibold leading-5 text-slate-500">
                เลือกรูปแบบการตรวจสอบที่ใช้กับ Template ทั้งหมดในระบบ
              </p>
            </div>
            <div className="rounded-full bg-slate-50 px-3 py-1 text-[11px] font-black text-slate-500">
              ปัจจุบัน: {savedStrategy === "strict" ? "Strict" : "Standard"}
            </div>
          </div>

          <div className="mt-5 grid gap-3">
            {verificationStrategyOptions.map((option) => {
              const checked = draftStrategy === option.value;
              return (
                <label
                  key={option.value}
                  className={`flex cursor-pointer gap-3 rounded-xl border p-4 transition-colors ${
                    checked ? "border-indigo-300 bg-indigo-50" : "border-slate-200 bg-white hover:bg-slate-50"
                  }`}
                >
                  <input
                    type="radio"
                    name="verificationStrategy"
                    value={option.value}
                    checked={checked}
                    onChange={() => {
                      setDraftStrategy(option.value);
                      setSaveStatus("idle");
                      setFeedbackMessage("");
                    }}
                    className="mt-1"
                  />
                  <span className="min-w-0">
                    <span className="block text-sm font-black text-slate-900">{option.label}</span>
                    <span className="mt-1 block text-xs font-semibold leading-5 text-slate-500">{option.description}</span>
                  </span>
                </label>
              );
            })}
          </div>

          <p className="mt-4 text-xs font-semibold leading-5 text-slate-500">{selectedOption.description}</p>

          {saveStatus === "saved" && feedbackMessage && (
            <div className="mt-4">
              <InlineState tone="success" message={feedbackMessage} />
            </div>
          )}
          {saveStatus === "error" && feedbackMessage && (
            <div className="mt-4">
              <InlineState tone="danger" message={feedbackMessage} />
            </div>
          )}

          <div className="mt-5 flex justify-end">
            <button
              type="button"
              onClick={() => void handleSave()}
              disabled={!hasChanges || saveStatus === "saving"}
              className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-indigo-600 px-4 text-xs font-black text-white shadow-sm transition-colors hover:bg-indigo-700 disabled:bg-slate-300 disabled:text-slate-500"
            >
              {saveStatus === "saving" ? <Loader2 size={15} className="animate-spin" /> : saveStatus === "saved" ? <Check size={15} /> : <Save size={15} />}
              {saveStatus === "saving" ? "กำลังบันทึก..." : "บันทึกการตั้งค่า"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
