export type RoiDataType = 'string' | 'text' | 'number' | 'date' | 'address' | 'currency' | 'table' | 'image';

export interface ROI {
  id: number;
  fieldName: string;
  x: number;
  y: number;
  width: number;
  height: number;
  text?: string;
  confidence?: number;
  pageIndex?: number;
  type?: 'text' | 'table' | 'image';
  dataType?: RoiDataType;
  extractionMethod?: 'ocr_text' | 'ocr_table' | 'paddle_thai_ocr' | 'table_recognition_v2' | 'extract_image';
  roiMode?: 'fix' | 'flexible';
  expectedContent?: 'text' | null;
  isResolvedBlock?: boolean;
  parentRoiId?: number;
  layoutType?: string;
  role?: 'data_extraction';
  weight?: number;
  points?: { x: number; y: number }[];
  enabled?: boolean;
}

export interface OCRResult {
  id: number;
  roiId?: number;
  fieldName: string;
  bbox: number[];
  extractedText: string;
  originalText?: string;
  confidence: number;
  saved_path?: string;
  type?: 'text' | 'table' | 'image';
  dataType?: RoiDataType;
  role?: 'data_extraction';
  weight?: number;
  points?: { x: number; y: number }[];
  tableRows?: string[][];
  tableMergedCells?: TableMergedCell[];
  tableStructured?: StructuredTableResult;
  tableSections?: TableSectionResult[];
  tableHtml?: string;
  tableDebug?: Record<string, unknown>;
  tableExport?: TableExportConfig;
  resolvedBlocks?: ResolvedLayoutBlock[];
}

export interface ResolvedLayoutBlock {
  index?: number;
  text?: string | null;
  confidence?: number | null;
  bbox?: { x?: number; y?: number; width?: number; height?: number } | null;
  roi?: { x_ratio?: number; y_ratio?: number; width_ratio?: number; height_ratio?: number } | null;
  type?: 'text' | 'table' | 'image' | string | null;
  data_type?: 'text' | 'table' | 'image' | string | null;
  dataType?: 'text' | 'table' | 'image' | string | null;
  extraction_method?: string | null;
  extractionMethod?: string | null;
  layout_type?: string | null;
  layoutType?: string | null;
}

export type TableExportMode = 'structure' | 'key_value';

export interface TableExportConfig {
  mode: TableExportMode;
  selectedColumns?: number[];
  selectedRows?: number[];
  includeDataRows?: boolean;
  includeSummary?: boolean;
  showRowNumber?: boolean;
}

export interface StructuredTableCell {
  row: number;
  col: number;
  text: string;
  bbox?: number[];
  regionId?: string;
  rowSpan?: number;
  colSpan?: number;
  ocrText?: string;
  groundTruth?: string;
  hidden?: boolean;
}

export interface StructuredTableResult {
  columns?: string[];
  rows?: string[][];
  cells?: StructuredTableCell[];
  bbox?: number[];
  rowSpans?: number[];
  colWidths?: number[];
  headerRowCount?: number;
}

export interface TableSectionResult {
  regionId: string;
  type?: string;
  bbox?: { x?: number; y?: number; width?: number; height?: number } | number[];
  confidence?: number;
  columns?: Array<{ col?: number; label?: string } | string>;
  rows?: string[][];
  cells?: StructuredTableCell[];
  tableStructured?: StructuredTableResult;
  tableHtml?: string | null;
  text?: string | null;
  reconstruction?: Record<string, unknown>;
}

export interface TableMergedCell {
  id: string;
  row: number;
  col: number;
  rowSpan: number;
  colSpan: number;
  originalCells?: string[][];
}

export type TemplateRequestMode = 'image_only' | 'image_with_roi';

export interface RoiRatio {
  pageNumber: number;
  xRatio: number;
  yRatio: number;
  widthRatio: number;
  heightRatio: number;
}

export interface RequestedField {
  id: string;
  fieldName: string;
  displayLabel: string;
  roi: RoiRatio;
  dataType?: RoiDataType;
  extractionMethod?: string;
  userNote?: string;
}

export interface TemplateRequestDraft {
  requestTitle: string;
  documentType?: string;
  requestMode: TemplateRequestMode;
  pageCount: number;
  userNote?: string;
  requestedFields: RequestedField[];
}

export type TemplateStatus =
  | 'draft'
  | 'validated'
  | 'embedding_pending'
  | 'active'
  | 'nonactive'
  | 'pending_review'
  | 'embedding_generated'
  | 'testing'
  | 'approved'
  | 'rejected'
  | 'disabled';

export interface Template {
  id: string;
  name: string;
  documentType?: string;
  category?: string;
  status: TemplateStatus;
  version: number;
  templateGroupId?: string;
  versionNumber?: number;
  baseTemplateId?: string;
  description?: string;
  sharedFields?: string[];
  creationType?: 'new_template' | 'new_version' | string;
  detectionMode?: 'all_pages' | 'main_page' | string;
  mainPageNumber?: number;
  pageCount: number;
  similarityThreshold: number;
  finalConfidenceThreshold: number;
  layoutWeight: number;
  textAnchorWeight: number;
  imageAnchorWeight: number;
  rejectionReason?: string;
  previewImageUrl?: string;
  createdAt?: string;
  updatedAt?: string;
}

export interface TemplatePage {
  id: string;
  templateId: string;
  pageNumber: number;
  pageName?: string;
  sampleImageUrl?: string;
  normalizedImageUrl?: string;
  layoutSignatureJson?: string;
  similarityThreshold: number;
  finalConfidenceThreshold: number;
}

export interface TemplateField {
  id: string;
  templateId: string;
  templatePageId: string;
  pageNumber: number;
  fieldName: string;
  displayLabel: string;
  roi: RoiRatio;
  dataType?: RoiDataType;
  userSelectable: boolean;
  defaultSelected: boolean;
  useForVerification: boolean;
  expectedText?: string;
  matchType?: string;
  requiredForVerification: boolean;
  extractionMethod: string;
  roiMode?: 'fix' | 'flexible';
  expectedContent?: 'text' | null;
  roiPadding?: number;
  verificationWeight?: number;
  imageCategory?: string | string[];
  sortOrder: number;
}

export interface IgnoreRegion {
  id: string;
  templateId: string;
  templatePageId: string;
  pageNumber: number;
  fieldName: string;
  roi: RoiRatio;
}

export type TemplateRequestStatus = 'draft' | 'submitted' | 'in_review' | 'converted' | 'rejected';

export interface TemplateRequestPage {
  id: string;
  templateRequestId: string;
  pageNumber: number;
  sampleImageUrl?: string;
  sourceFileId?: string;
  sourceFileName?: string;
  imageSource?: 'user_request' | 'admin_upload';
  reviewStatus?: 'pending' | 'approved' | 'rejected';
  isCanonical?: boolean;
  layoutSignatureJson?: string;
}

export interface AdminTemplateRequest {
  id: string;
  requestTitle: string;
  documentType?: string;
  requestMode: TemplateRequestMode;
  status: TemplateRequestStatus;
  userNote?: string;
  adminNote?: string;
  convertedTemplateId?: string;
  pageCount: number;
  pages: TemplateRequestPage[];
  requestedFields: RequestedField[];
  createdAt?: string;
  updatedAt?: string;
}
