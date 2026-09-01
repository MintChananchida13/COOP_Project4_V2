"use client";

import { useEffect, useState } from "react";
import { FileText, Image as ImageIcon, Search, Square, Table } from "lucide-react";
import { RoiDataType, TemplateField } from "../../types/ocr";
import { defaultExtractionMethodForDataType } from "../../shared/workspace/extractionMethods";

interface TemplateFieldBasicFormProps {
  field: TemplateField;
  onUpdate: (fieldId: string, patch: Partial<TemplateField>) => void;
  onDelete: (fieldId: string) => void;
  compact?: boolean;
  onSave?: () => void;
}

const inputClass =
  "w-full rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[11px] font-semibold text-slate-700 outline-none focus:border-indigo-500";

const roiTypes = [
  { label: "ข้อความ", value: "text" as const, icon: FileText },
  { label: "ตาราง", value: "table" as const, icon: Table },
  { label: "รูปภาพ", value: "image" as const, icon: ImageIcon },
];

export default function TemplateFieldBasicForm({ field, onUpdate, onDelete, compact = false, onSave }: TemplateFieldBasicFormProps) {
  const [fieldNameDraft, setFieldNameDraft] = useState(field.fieldName);
  const selectedDataType = field.dataType === "string" || !field.dataType ? "text" : field.dataType;
  const selectedRoiType = selectedDataType === "table" || selectedDataType === "image" ? selectedDataType : "text";
  const selectedRoiMode = field.roiMode === "flexible" && selectedRoiType === "text" ? "flexible" : "fix";

  useEffect(() => {
    setFieldNameDraft(field.fieldName);
  }, [field.id, field.fieldName]);

  const commitFieldName = () => {
    if (fieldNameDraft === field.fieldName) return;
    onUpdate(field.id, { fieldName: fieldNameDraft, displayLabel: fieldNameDraft });
  };

  const updateDataType = (dataType: RoiDataType) => {
    const nextMode = dataType === "text" ? selectedRoiMode : "fix";
    onUpdate(field.id, {
      dataType,
      extractionMethod: defaultExtractionMethodForDataType(dataType),
      roiMode: nextMode,
      expectedContent: nextMode === "flexible" ? "text" : null,
    });
  };

  const updateRoiMode = (roiMode: "fix" | "flexible") => {
    const dataType = roiMode === "flexible" ? "text" : selectedRoiType;
    onUpdate(field.id, {
      roiMode,
      expectedContent: roiMode === "flexible" ? "text" : null,
      dataType,
      extractionMethod: defaultExtractionMethodForDataType(dataType),
    });
  };

  const handleSave = () => {
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
    onSave?.();
  };

  return (
    <section className={compact ? "space-y-2 rounded-lg border border-indigo-100 bg-white p-2" : "space-y-3 rounded-xl border border-indigo-200 bg-indigo-50/40 p-3"}>
      <h3 className="text-xs font-black uppercase tracking-wider text-indigo-800">{compact ? "Field ที่เลือก" : "Template Field"}</h3>

      <label className="block space-y-1">
        <span className="text-[9px] font-black uppercase text-slate-400">ชื่อ Field</span>
        <input
          className={inputClass}
          value={fieldNameDraft}
          onChange={(event) => setFieldNameDraft(event.target.value)}
          onBlur={commitFieldName}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.currentTarget.blur();
            }
          }}
        />
      </label>

      <div className="space-y-1">
        <span className="text-[9px] font-black uppercase text-slate-400">ประเภทข้อมูล</span>
        <div className="grid grid-cols-3 gap-1">
          {roiTypes.map(({ label, value, icon: Icon }) => (
            <button
              key={value}
              type="button"
              onClick={() => updateDataType(value)}
              className={`flex items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-[10px] font-black transition-all ${
                selectedRoiType === value
                  ? "bg-indigo-600 text-white shadow-sm shadow-indigo-500/20"
                  : "bg-white text-slate-500 ring-1 ring-slate-200 hover:bg-slate-50 hover:text-slate-700"
              }`}
            >
              <Icon size={11} />
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-1.5">
        <span className="text-[9px] font-black uppercase text-slate-400">รูปแบบ ROI</span>
        <div className="grid grid-cols-2 gap-1">
          <button
            type="button"
            onClick={() => updateRoiMode("fix")}
            className={`flex items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-[10px] font-black transition-all ${
              selectedRoiMode === "fix"
                ? "bg-slate-800 text-white shadow-sm shadow-slate-500/20"
                : "bg-white text-slate-500 ring-1 ring-slate-200 hover:bg-slate-50 hover:text-slate-700"
            }`}
          >
            <Square size={11} />
            Fix
          </button>
          <button
            type="button"
            onClick={() => updateRoiMode("flexible")}
            className={`flex items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-[10px] font-black transition-all ${
              selectedRoiMode === "flexible"
                ? "bg-sky-600 text-white shadow-sm shadow-sky-500/20"
                : "bg-white text-slate-500 ring-1 ring-slate-200 hover:bg-slate-50 hover:text-slate-700"
            }`}
          >
            <Search size={11} />
            Flexible
          </button>
        </div>
        {selectedRoiMode === "flexible" ? (
          <div className="rounded-lg border border-sky-200 bg-sky-50 px-2.5 py-2 text-[10px] font-semibold leading-relaxed text-sky-800">
            กรอบนี้คือพื้นที่ค้นหา ไม่ใช่กรอบ OCR โดยตรง ระบบจะหา Text Content ภายในพื้นที่นี้ก่อนแล้วค่อย OCR เป็นบล็อกตามลำดับ
          </div>
        ) : (
          <div className="rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-[10px] font-semibold leading-relaxed text-slate-500">
            ใช้ตำแหน่ง ROI เดิมหลัง align เอกสาร แล้วส่งเข้า OCR ตามประเภทข้อมูล
          </div>
        )}
      </div>

      <div className={compact ? "grid grid-cols-1 gap-2" : "grid grid-cols-2 gap-2"}>
        <button type="button" onClick={handleSave} className="rounded-lg bg-indigo-600 px-3 py-2 text-xs font-black text-white">
          บันทึก Field
        </button>
        <button type="button" onClick={() => onDelete(field.id)} className={`${compact ? "hidden" : ""} rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs font-black text-red-700`}>
          ลบ Field
        </button>
      </div>
    </section>
  );
}
