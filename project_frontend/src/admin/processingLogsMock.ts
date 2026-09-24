"use client";

export type ProcessingLogStatus = "completed" | "failed";
export type ProcessingDetectionResult = "matched" | "no_match";
export type ProcessingRoiMode = "fixed" | "flexible";

export interface ProcessingLogRoi {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface ProcessingLogField {
  fieldId: string;
  fieldName: string;
  fieldType: string;
  pageNumber: number;
  roiMode: ProcessingRoiMode;
  roi: ProcessingLogRoi;
  value: string;
  confidence?: number;
}

export interface ProcessingGroundTruthField {
  fieldId: string;
  value: string;
}

export interface ProcessingLog {
  id: string;
  documentName: string;
  user: string;
  createdAt: string;
  pageCount: number;
  status: ProcessingLogStatus;
  detectionProcessingTimeMs: number;
  ocrProcessingTimeMs: number;
  sourcePages: { pageNumber: number; imageUrl?: string }[];
  templateDetection: {
    matched: boolean;
    selectedTemplate: string | null;
    templateVersion: string | null;
    detectionMode: string;
    verificationMode: string;
    candidates: { template: string; layoutScore: number; result: string }[];
    verification: {
      layoutScore: number | null;
      textAnchorScore: number | null;
      imageAnchorScore: number | null;
      finalScore: number | null;
      passed: boolean | null;
    };
  };
  ocrOriginal: ProcessingLogField[];
  groundTruth: ProcessingGroundTruthField[];
}

export const processingLogsMock: ProcessingLog[] = [
  {
    id: "log_20260924_1432",
    documentName: "document_01.pdf",
    user: "user@ocr.com",
    createdAt: "2026-09-24T14:32:00+07:00",
    pageCount: 2,
    status: "completed",
    detectionProcessingTimeMs: 2310,
    ocrProcessingTimeMs: 8420,
    sourcePages: [{ pageNumber: 1 }, { pageNumber: 2 }],
    templateDetection: {
      matched: true,
      selectedTemplate: "หนังสือราชการ",
      templateVersion: "v1",
      detectionMode: "all_pages",
      verificationMode: "Strict",
      candidates: [
        { template: "หนังสือราชการ", layoutScore: 0.91, result: "Selected" },
        { template: "ใบเสนอราคา", layoutScore: 0.72, result: "-" },
        { template: "ใบเสร็จ", layoutScore: 0.63, result: "-" },
      ],
      verification: {
        layoutScore: 0.91,
        textAnchorScore: 0.88,
        imageAnchorScore: 0.76,
        finalScore: 0.87,
        passed: true,
      },
    },
    ocrOriginal: [
      {
        fieldId: "field_001",
        fieldName: "เลขที่เอกสาร",
        fieldType: "Text",
        pageNumber: 1,
        roiMode: "fixed",
        roi: { x: 0.58, y: 0.13, width: 0.28, height: 0.06 },
        value: "ABCI23",
        confidence: 0.92,
      },
      {
        fieldId: "field_002",
        fieldName: "วันที่",
        fieldType: "Text",
        pageNumber: 1,
        roiMode: "fixed",
        roi: { x: 0.58, y: 0.21, width: 0.25, height: 0.05 },
        value: "24/09/69",
        confidence: 0.95,
      },
      {
        fieldId: "field_003",
        fieldName: "ชื่อบริษัท",
        fieldType: "Text",
        pageNumber: 1,
        roiMode: "flexible",
        roi: { x: 0.16, y: 0.38, width: 0.58, height: 0.08 },
        value: "บริษัท ทดสอบ",
        confidence: 0.89,
      },
      {
        fieldId: "field_004",
        fieldName: "หมายเหตุ",
        fieldType: "Text",
        pageNumber: 2,
        roiMode: "flexible",
        roi: { x: 0.14, y: 0.24, width: 0.7, height: 0.12 },
        value: "ส่งเอกสารเพิ่มเติมภายใน 7 วัน",
        confidence: 0.86,
      },
    ],
    groundTruth: [
      { fieldId: "field_001", value: "ABC123" },
      { fieldId: "field_002", value: "24/09/2569" },
      { fieldId: "field_003", value: "บริษัท ทดสอบ" },
      { fieldId: "field_004", value: "ส่งเอกสารเพิ่มเติมภายใน 7 วัน" },
    ],
  },
  {
    id: "log_20260924_1420",
    documentName: "document_02.pdf",
    user: "user@ocr.com",
    createdAt: "2026-09-24T14:20:00+07:00",
    pageCount: 1,
    status: "completed",
    detectionProcessingTimeMs: 1790,
    ocrProcessingTimeMs: 5380,
    sourcePages: [{ pageNumber: 1 }],
    templateDetection: {
      matched: false,
      selectedTemplate: null,
      templateVersion: null,
      detectionMode: "all_pages",
      verificationMode: "Standard",
      candidates: [
        { template: "หนังสือราชการ", layoutScore: 0.54, result: "-" },
        { template: "ใบเสนอราคา", layoutScore: 0.49, result: "-" },
      ],
      verification: {
        layoutScore: 0.54,
        textAnchorScore: null,
        imageAnchorScore: null,
        finalScore: 0.42,
        passed: false,
      },
    },
    ocrOriginal: [
      {
        fieldId: "auto_001",
        fieldName: "ข้อความที่พบ",
        fieldType: "Text",
        pageNumber: 1,
        roiMode: "flexible",
        roi: { x: 0.18, y: 0.2, width: 0.62, height: 0.14 },
        value: "ไม่พบ Template ที่ตรงกัน",
        confidence: 0.81,
      },
    ],
    groundTruth: [{ fieldId: "auto_001", value: "ไม่พบ Template ที่ตรงกัน" }],
  },
  {
    id: "log_20260924_1405",
    documentName: "invoice_2026_09.pdf",
    user: "reviewer@ocr.com",
    createdAt: "2026-09-24T14:05:00+07:00",
    pageCount: 1,
    status: "completed",
    detectionProcessingTimeMs: 2160,
    ocrProcessingTimeMs: 6210,
    sourcePages: [{ pageNumber: 1 }],
    templateDetection: {
      matched: true,
      selectedTemplate: "ใบเสนอราคา",
      templateVersion: "v2",
      detectionMode: "main_page",
      verificationMode: "Strict",
      candidates: [
        { template: "ใบเสนอราคา", layoutScore: 0.89, result: "Selected" },
        { template: "ใบเสร็จ", layoutScore: 0.71, result: "-" },
      ],
      verification: {
        layoutScore: 0.89,
        textAnchorScore: 0.84,
        imageAnchorScore: null,
        finalScore: 0.86,
        passed: true,
      },
    },
    ocrOriginal: [
      {
        fieldId: "invoice_001",
        fieldName: "เลขที่ใบเสนอราคา",
        fieldType: "Text",
        pageNumber: 1,
        roiMode: "fixed",
        roi: { x: 0.57, y: 0.12, width: 0.29, height: 0.06 },
        value: "QT-0924-01",
      },
      {
        fieldId: "invoice_002",
        fieldName: "ยอดรวม",
        fieldType: "Text",
        pageNumber: 1,
        roiMode: "fixed",
        roi: { x: 0.61, y: 0.75, width: 0.23, height: 0.06 },
        value: "12,500.00",
      },
    ],
    groundTruth: [
      { fieldId: "invoice_001", value: "QT-0924-01" },
      { fieldId: "invoice_002", value: "12,500.00" },
    ],
  },
  {
    id: "log_20260924_1348",
    documentName: "receipt_scan.png",
    user: "user@ocr.com",
    createdAt: "2026-09-24T13:48:00+07:00",
    pageCount: 1,
    status: "failed",
    detectionProcessingTimeMs: 1280,
    ocrProcessingTimeMs: 0,
    sourcePages: [{ pageNumber: 1 }],
    templateDetection: {
      matched: false,
      selectedTemplate: null,
      templateVersion: null,
      detectionMode: "all_pages",
      verificationMode: "Standard",
      candidates: [{ template: "ใบเสร็จ", layoutScore: 0.58, result: "-" }],
      verification: {
        layoutScore: 0.58,
        textAnchorScore: null,
        imageAnchorScore: null,
        finalScore: null,
        passed: null,
      },
    },
    ocrOriginal: [],
    groundTruth: [],
  },
  {
    id: "log_20260924_1324",
    documentName: "memo_internal.pdf",
    user: "staff@ocr.com",
    createdAt: "2026-09-24T13:24:00+07:00",
    pageCount: 3,
    status: "completed",
    detectionProcessingTimeMs: 2470,
    ocrProcessingTimeMs: 9100,
    sourcePages: [{ pageNumber: 1 }, { pageNumber: 2 }, { pageNumber: 3 }],
    templateDetection: {
      matched: true,
      selectedTemplate: "บันทึกข้อความ",
      templateVersion: "v1",
      detectionMode: "all_pages",
      verificationMode: "Standard",
      candidates: [
        { template: "บันทึกข้อความ", layoutScore: 0.86, result: "Selected" },
        { template: "หนังสือราชการ", layoutScore: 0.66, result: "-" },
      ],
      verification: {
        layoutScore: 0.86,
        textAnchorScore: 0.82,
        imageAnchorScore: null,
        finalScore: 0.84,
        passed: true,
      },
    },
    ocrOriginal: [
      {
        fieldId: "memo_001",
        fieldName: "เรื่อง",
        fieldType: "Text",
        pageNumber: 1,
        roiMode: "flexible",
        roi: { x: 0.18, y: 0.3, width: 0.64, height: 0.07 },
        value: "ขออนุมัติดำเนินการ",
      },
    ],
    groundTruth: [{ fieldId: "memo_001", value: "ขออนุมัติดำเนินการ" }],
  },
];

export const getProcessingLogById = (logId: string) => processingLogsMock.find((log) => log.id === logId);

export const formatProcessingLogDateTime = (value: string) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const datePart = new Intl.DateTimeFormat("th-TH", {
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
  }).format(date);
  const timePart = new Intl.DateTimeFormat("th-TH", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
  return `${datePart} ${timePart}`;
};
