"use client";

import React, { useState } from "react";
import { convertPdfToImages } from "../../shared/pdfImages";
import { FileImage, FileText, Loader2, Upload } from "lucide-react";

interface UploadZoneProps {
  onUploadSuccess: (urls: string[], sourceFileName?: string, sourceFileType?: "pdf" | "image", sourceFile?: File) => void;
}

export default function UploadZone({ onUploadSuccess }: UploadZoneProps) {
  const [isProcessing, setIsProcessing] = useState(false);

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    if (files.length > 1) {
      alert("อัปโหลดได้ครั้งละ 1 ไฟล์เท่านั้น กรุณาเลือกไฟล์เดียว");
      e.target.value = "";
      return;
    }

    setIsProcessing(true);
    const file = files[0];
    let preparedImages: string[] = [];

    try {
      const sourceFileType = file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf") ? "pdf" : "image";
      if (sourceFileType === "pdf") {
        preparedImages = await convertPdfToImages(file);
      } else {
        const base64 = await new Promise<string>((resolve) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result || ""));
          reader.readAsDataURL(file);
        });
        preparedImages = [base64];
      }

      if (preparedImages.length > 0) {
        onUploadSuccess(preparedImages, file.name, sourceFileType, file);
      }
    } catch (error) {
      alert("เกิดข้อผิดพลาดระหว่างเตรียมไฟล์เอกสาร กรุณาลองใหม่อีกครั้ง");
      console.error(error);
    } finally {
      setIsProcessing(false);
      e.target.value = "";
    }
  };

  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-8">
      <div className="mb-5 text-center">
        <p className="ui-caption font-semibold text-blue-600">นำเข้าเอกสาร</p>
        <h2 className="ui-section-title mt-1 text-slate-950">อัปโหลดเอกสารสำหรับ OCR</h2>
        <p className="ui-body mx-auto mt-2 max-w-2xl text-slate-500">
          รองรับไฟล์ภาพและ PDF หลายหน้า หลังอัปโหลดระบบจะเริ่มตรวจขอบเขตเอกสาร ค้นหา Template และอ่านข้อมูล
        </p>
      </div>

      <div
        className={`group relative flex min-h-[360px] flex-col items-center justify-center rounded-3xl border bg-white p-10 text-center shadow-sm transition-all duration-300 ${
          isProcessing
            ? "border-blue-200 bg-blue-50/40"
            : "border-slate-200 hover:border-blue-300 hover:shadow-lg hover:shadow-slate-200/70"
        }`}
      >
        {isProcessing ? (
          <div className="flex flex-col items-center justify-center space-y-4">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-white text-blue-600 shadow-sm ring-1 ring-blue-100">
              <Loader2 className="h-7 w-7 animate-spin" />
            </div>
            <div>
              <p className="ui-card-title text-slate-800">กำลังเตรียมไฟล์เอกสาร</p>
              <p className="ui-body mt-1 text-slate-500">
                หากเป็น PDF ระบบจะแปลงแต่ละหน้าเป็นภาพก่อนดำเนินการต่อ
              </p>
            </div>
          </div>
        ) : (
          <>
            <div className="pointer-events-none absolute inset-0 rounded-3xl bg-gradient-to-b from-blue-50/0 to-blue-50/60 opacity-0 transition-opacity duration-300 group-hover:opacity-100" />
            <input
              type="file"
              accept="image/*,application/pdf"
              className="absolute inset-0 z-10 cursor-pointer opacity-0"
              aria-label="เลือกไฟล์เอกสาร"
              onChange={handleFileChange}
            />

            <div className="relative mb-6 flex h-16 w-16 items-center justify-center rounded-2xl border border-slate-200 bg-slate-50 text-slate-500 shadow-sm transition-all duration-300 group-hover:border-blue-200 group-hover:bg-blue-50 group-hover:text-blue-600">
              <Upload className="h-8 w-8" strokeWidth={2.2} />
            </div>

            <div className="relative z-20 space-y-2">
              <h3 className="ui-card-title text-slate-900">วางไฟล์เอกสารที่นี่ หรือคลิกเพื่อเลือกไฟล์</h3>
              <p className="ui-body mx-auto max-w-md text-slate-500">
                เลือกได้ครั้งละ 1 ไฟล์ รองรับไฟล์ภาพหรือ PDF หลายหน้า ระบบจะเตรียมไฟล์ก่อนเข้าสู่ขั้นตอนตรวจขอบเขตเอกสาร
              </p>
            </div>

            <div className="relative z-20 mt-8 flex flex-wrap justify-center gap-2">
              {[
                { label: "PDF", Icon: FileText },
                { label: "JPG", Icon: FileImage },
                { label: "PNG", Icon: FileImage },
                { label: "WEBP", Icon: FileImage },
              ].map(({ label, Icon }) => (
                <span
                  key={label}
                  className="ui-caption inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-slate-50 px-3 py-1 font-semibold text-slate-500"
                >
                  <Icon size={12} />
                  {label}
                </span>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
