import {
  AdminTemplateRequest,
  IgnoreRegion,
  RoiDataType,
  Template,
  TemplateField,
  TemplatePage,
  TemplateStatus,
} from "../types/ocr";

export const ADMIN_API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const fetchWithAuth = (input: RequestInfo | URL, init: RequestInit = {}) => {
  const headers = new Headers(init.headers || {});
  return fetch(input, { ...init, headers });
};

let templateRequestListCache: AdminTemplateRequest[] | null = null;
let templateListCache: Template[] | null = null;
let templateRequestListPromise: Promise<AdminTemplateRequest[]> | null = null;
let templateListPromise: Promise<Template[]> | null = null;

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asRecordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item))) : [];
}

function cloneTemplateRequests(requests: AdminTemplateRequest[] = []) {
  return requests.map((request) => ({
    ...request,
    pages: (request.pages || []).map((page) => ({ ...page })),
    requestedFields: (request.requestedFields || []).map((field) => ({ ...field, roi: { ...field.roi } })),
  }));
}

function cloneTemplates(templates: Template[] = []) {
  return templates.map((template) => ({
    ...template,
    sharedFields: template.sharedFields ? [...template.sharedFields] : undefined,
  }));
}

function debugPrepublishPagesAccess(scope: string, value: unknown) {
  if (process.env.NODE_ENV !== "development") return;
  const record = value as Record<string, unknown> | null | undefined;
  console.debug("[prepublish:pages]", scope, {
    hasValue: Boolean(record),
    keys: record && typeof record === "object" ? Object.keys(record) : [],
    hasPagesArray: Array.isArray(record?.pages),
  });
}

const setTemplateRequestListCache = (requests: AdminTemplateRequest[]) => {
  templateRequestListCache = cloneTemplateRequests(requests);
};

const setTemplateListCache = (templates: Template[]) => {
  templateListCache = cloneTemplates(templates);
};

const upsertTemplateRequestListCache = (request: AdminTemplateRequest) => {
  if (!templateRequestListCache) return;
  const index = templateRequestListCache.findIndex((item) => item.id === request.id);
  const nextRequest = cloneTemplateRequests([request])[0];
  templateRequestListCache =
    index >= 0
      ? templateRequestListCache.map((item, itemIndex) => (itemIndex === index ? nextRequest : item))
      : [nextRequest, ...templateRequestListCache];
};

const removeTemplateRequestListCache = (requestId: string) => {
  if (!templateRequestListCache) return;
  templateRequestListCache = templateRequestListCache.filter((request) => request.id !== requestId);
};

const upsertTemplateListCache = (template: Template) => {
  if (!templateListCache) return;
  const index = templateListCache.findIndex((item) => item.id === template.id);
  const nextTemplate = cloneTemplates([template])[0];
  templateListCache =
    index >= 0
      ? templateListCache.map((item, itemIndex) => (itemIndex === index ? nextTemplate : item))
      : [nextTemplate, ...templateListCache];
};

const removeTemplateListCache = (templateId: string) => {
  if (!templateListCache) return;
  templateListCache = templateListCache.filter((template) => template.id !== templateId);
};

export const invalidateAdminListCache = (target: "templates" | "requests" | "all" = "all") => {
  if (target === "templates" || target === "all") {
    templateListCache = null;
    templateListPromise = null;
  }
  if (target === "requests" || target === "all") {
    templateRequestListCache = null;
    templateRequestListPromise = null;
  }
};

interface ApiTemplateRequestPage {
  id: string;
  template_request_id: string;
  page_number: number;
  sample_image_url?: string | null;
  source_file_id?: string | null;
  source_file_name?: string | null;
  image_source?: "user_request" | "admin_upload" | null;
  review_status?: "pending" | "approved" | "rejected" | null;
  is_canonical?: boolean | number | null;
  layout_signature_json?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

interface ApiRequestedField {
  id: string;
  field_name: string;
  display_label: string;
  data_type?: string | null;
  dataType?: string | null;
  extraction_method?: string | null;
  extractionMethod?: string | null;
  user_note?: string | null;
  roi: {
    page_number: number;
    x_ratio: number;
    y_ratio: number;
    width_ratio: number;
    height_ratio: number;
  };
}

interface ApiTemplatePage {
  id: string;
  template_id: string;
  page_number: number;
  page_name?: string | null;
  sample_image_url?: string | null;
  normalized_image_url?: string | null;
  layout_signature_json?: string | null;
  similarity_threshold?: number | null;
  final_confidence_threshold?: number | null;
}

interface ApiTemplateField {
  id: string;
  template_id: string;
  template_page_id: string;
  page_number: number;
  field_name: string;
  display_label: string;
  roi: {
    page_number: number;
    x_ratio: number;
    y_ratio: number;
    width_ratio: number;
    height_ratio: number;
  };
  data_type?: string | null;
  user_selectable: boolean;
  default_selected: boolean;
  use_for_verification: boolean;
  expected_text?: string | null;
  match_type?: string | null;
  required_for_verification: boolean;
  extraction_method: string;
  roi_mode?: "fix" | "flexible" | null;
  expected_content?: "text" | null;
  roi_padding?: number | null;
  verification_weight?: number | null;
  image_category?: string | null;
  sort_order: number;
}

interface ApiIgnoreRegion {
  id: string;
  template_id: string;
  template_page_id: string;
  page_number: number;
  field_name: string;
  roi: {
    page_number: number;
    x_ratio: number;
    y_ratio: number;
    width_ratio: number;
    height_ratio: number;
  };
}

interface ApiTemplate {
  id: string;
  name: string;
  document_type?: string | null;
  category?: string | null;
  status: string;
  version: number;
  template_group_id?: string | null;
  version_number?: number | null;
  base_template_id?: string | null;
  description?: string | null;
  shared_fields?: string[] | null;
  creation_type?: string | null;
  detection_mode?: "all_pages" | "main_page" | string | null;
  main_page_number?: number | null;
  page_count: number;
  similarity_threshold: number;
  final_confidence_threshold: number;
  layout_weight?: number | null;
  text_anchor_weight?: number | null;
  image_anchor_weight?: number | null;
  rejection_reason?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  pages?: ApiTemplatePage[];
  fields?: ApiTemplateField[];
  ignore_regions?: ApiIgnoreRegion[];
}

interface ApiEmbeddingJob {
  id: string;
  template_id: string;
  status: string;
  requested_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  error_message?: string | null;
  vector_id?: string | null;
  metadata_json?: string | null;
}

export type EmbeddingJobStatus = "queued" | "running" | "completed" | "failed";

export interface EmbeddingJob {
  id: string;
  templateId: string;
  status: EmbeddingJobStatus;
  requestedAt?: string;
  startedAt?: string | null;
  completedAt?: string | null;
  errorMessage?: string | null;
  vectorId?: string | null;
  metadataJson?: string | null;
}

export interface DetectionCandidate {
  templateId?: string | null;
  vectorId?: string | null;
  score: number;
  retrievalScore?: number | null;
  layoutScore?: number | null;
  layoutDebug?: Record<string, unknown>;
  verificationScore?: number | null;
  textAnchorScore?: number | null;
  imageAnchorScore?: number | null;
  matchingWeights?: Record<string, number>;
  effectiveMatchingWeights?: Record<string, number>;
  verificationPassed?: boolean | null;
  finalScore?: number | null;
  finalPassed?: boolean | null;
  decisionReason?: string | null;
  decisionPath?: string | null;
  requiredPassed?: boolean | null;
  requiredFailedFields?: Record<string, unknown>[];
  finalConfidenceThreshold?: number | null;
  verification?: Record<string, unknown>;
  averageScore?: number | null;
  matchedPages?: number | null;
  templateName?: string | null;
  templateStatus?: string | null;
  pageCount?: number | null;
  fieldCount?: number | null;
  modelName?: string | null;
  vectorStoreEngine?: string | null;
  retrievalEngine?: string | null;
  pageIndex?: number | null;
  queryPageIndex?: number | null;
  templatePageNumber?: number | null;
  alignmentStatus?: "skipped" | "aligned" | "fallback" | "failed" | null;
  alignment?: Record<string, unknown>;
  alignmentDebug?: Record<string, unknown>;
  alignmentScore?: number | null;
  alignmentPassed?: boolean | null;
  alignmentFallbackUsed?: boolean | null;
  alignmentReason?: string | null;
  normalizedVerificationScore?: number | null;
  alignedVerificationScore?: number | null;
  verificationSourceUsed?: "normalized" | "aligned" | null;
  beforeAlignmentVerification?: number | null;
  afterAlignmentVerification?: number | null;
  verificationImprovement?: number | null;
  alignmentMatchImagePreviewUrl?: string | null;
  alignedImagePreviewUrl?: string | null;
  normalizedImagePreviewUrl?: string | null;
  extractionImagePreviewUrl?: string | null;
  roiCoordinateSpace?: "template_canvas" | "projected" | string | null;
  templateRois?: DetectionTemplateRoi[];
  projection?: Record<string, unknown>;
  projectedFields?: DetectionProjectedField[];
  extractionTest?: TemplateStepTestResult | null;
  coordinateDebug?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

const parseImageCategoryValue = (value?: string | null): string | string[] | undefined => {
  const raw = String(value || "").trim();
  if (!raw) return undefined;
  if (raw.startsWith("[")) {
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        const values = parsed.map((item) => String(item || "").trim()).filter(Boolean);
        return values.length > 1 ? values : values[0];
      }
    } catch {
      return raw;
    }
  }
  return raw;
};

const serializeImageCategoryValue = (value?: string | string[] | null) => {
  if (Array.isArray(value)) {
    const values = value.map((item) => String(item || "").trim()).filter(Boolean);
    if (values.length === 0) return undefined;
    return values.length === 1 ? values[0] : JSON.stringify(values);
  }
  const raw = String(value || "").trim();
  return raw || undefined;
};

export interface DetectionTemplateRoi {
  fieldId?: string | null;
  fieldName?: string | null;
  displayLabel?: string | null;
  pageNumber?: number | null;
  dataType?: string | null;
  extractionMethod?: string | null;
  source?: string | null;
  roi?: Record<string, unknown>;
}

export interface DetectionProjectedField {
  fieldId?: string | null;
  fieldName?: string | null;
  displayLabel?: string | null;
  pageNumber?: number | null;
  roiMode?: "fix" | "flexible" | null;
  expectedContent?: "text" | null;
  templateRoi?: Record<string, unknown>;
  projectedRoiBeforeClip?: DetectionProjectedField["projectedRoi"];
  projectedRoi?: {
    page_number?: number | null;
    x_ratio?: number | null;
    y_ratio?: number | null;
    width_ratio?: number | null;
    height_ratio?: number | null;
  } | null;
  adaptiveRoi?: DetectionProjectedField["projectedRoi"];
  adaptiveStatus?: string | null;
  adaptiveSearchRegion?: DetectionProjectedField["projectedRoi"];
  adaptiveWordBoxes?: Record<string, unknown>[];
  adaptiveWordGroups?: Record<string, unknown>[];
  adaptiveRankedWordGroups?: Record<string, unknown>[];
  adaptiveConfidence?: number | null;
  adaptiveWordCount?: number | null;
  adaptiveCoverage?: number | null;
  adaptiveOcrConfidence?: number | null;
  adaptiveValidationResult?: Record<string, unknown>;
  adaptiveFallbackReason?: string | null;
  projectedPolygon?: number[][];
  projectedPolygonBeforeClip?: number[][];
  projectionMethod?: string | null;
  projectionValid?: boolean | null;
  projectionValidationResult?: Record<string, unknown>;
  fallbackUsed?: boolean | null;
}

export interface DetectionPageResult {
  pageIndex: number;
  matched: boolean;
  bestCandidate?: DetectionCandidate | null;
  candidates: DetectionCandidate[];
  imagePreviewDataUrl?: string | null;
  originalImagePreviewUrl?: string | null;
  normalizedImagePreviewUrl?: string | null;
  originalImagePath?: string | null;
  normalizedImagePath?: string | null;
  normalization?: Record<string, unknown>;
  debug?: Record<string, unknown>;
}

export interface DetectionDevResult {
  queryId: string;
  engine: string;
  version: string;
  threshold: number;
  matched: boolean;
  bestCandidate?: DetectionCandidate | null;
  candidates: DetectionCandidate[];
  pages: DetectionPageResult[];
  message?: string | null;
  debug?: Record<string, unknown>;
}

export interface PrepublishCandidate {
  rank: number;
  templateId: string;
  templateName?: string | null;
  templateStatus?: string | null;
  vectorId?: string | null;
  globalScore: number;
  textAnchorScore: number;
  imageAnchorScore: number;
  verificationScore: number;
  finalScore: number;
  matchingWeights?: Record<string, number>;
  effectiveMatchingWeights?: Record<string, number>;
  alignmentStatus: string;
  alignmentReason?: string | null;
  alignmentDetails?: Record<string, unknown>[];
  verificationSourceUsed?: string | null;
  decision: string;
  finalPassed: boolean;
  requiredPassed?: boolean | null;
  requiredFailedFields?: Record<string, unknown>[];
  isCurrentDraft?: boolean;
  source?: "draft" | "published" | string;
  sourceLabel?: string | null;
  pageCount?: number | null;
  fieldCount?: number | null;
  matchedLayoutReference?: Record<string, unknown> | null;
  layoutReferenceCount?: number | null;
  verification?: Record<string, unknown>;
  verificationDetails?: Record<string, unknown>[];
}

export interface PrepublishSimulationResult {
  template: Template;
  draftSummary: {
    templateName?: string | null;
    templateId: string;
    status: string;
    pageCount: number;
    extractionFieldCount: number;
    textAnchorCount: number;
    imageAnchorCount: number;
    similarityThreshold?: number | null;
    finalConfidenceThreshold?: number | null;
    layoutWeight?: number | null;
    textAnchorWeight?: number | null;
    imageAnchorWeight?: number | null;
  };
  temporaryEmbedding: {
    status: string;
    engine: string;
    version: string;
    modelName: string;
    embeddingDimension: number;
    inputCount: number;
    generatedAt?: string;
    persisted: boolean;
    note?: string;
    layoutSignaturePages?: PrepublishLayoutSignaturePage[];
  };
  layoutSignaturePages?: PrepublishLayoutSignaturePage[];
  candidates: PrepublishCandidate[];
  verificationAnchorResults: Record<string, unknown>[];
  separationAnalysis: {
    top1Score: number;
    top2Score?: number | null;
    status: "ready_to_publish" | "needs_review" | "conflict_detected" | "not_ready" | string;
    simulationPassed: boolean;
    conflictTemplates: PrepublishCandidate[];
    message?: string;
  };
}

export interface PrepublishLayoutSignaturePage {
  templatePageId?: string | null;
  templateLayoutReferenceId?: string | null;
  pageNumber: number;
  status: string;
  engine?: string | null;
  version?: string | null;
  modelName?: string | null;
  labelCount?: number | null;
  imageUrl?: string | null;
  imageSource?: string | null;
  isCanonical?: boolean;
  referenceRole?: "main" | "reference_only" | string | null;
  persisted?: boolean;
  reason?: string | null;
}

export interface PrepublishDetectionTestResult {
  testId: string;
  templateId: string;
  matched: boolean;
  selectedTemplate?: PrepublishCandidate | null;
  selectedTemplateType?: string | null;
  finalConfidence: number;
  decisionReason?: string | null;
  draftTemplateRank?: number | null;
  passed: boolean;
  warning: boolean;
  candidates: PrepublishCandidate[];
  separationResult: {
    draftTemplateRank?: number | null;
    draftFinalScore: number;
    closestPublishedTemplate?: string | null;
    closestPublishedScore?: number | null;
    conflictLevel: string;
    recommendation: string;
  };
  debug?: Record<string, unknown>;
}

export interface TemplateStepTestItem {
  fieldId?: string;
  anchorId?: string;
  fieldName?: string | null;
  displayLabel?: string | null;
  pageNumber?: number | null;
  dataType?: string | null;
  extractionMethod?: string | null;
  roiSource?: string | null;
  roi?: Record<string, unknown> | null;
  roiMode?: "fix" | "flexible" | null;
  expectedContent?: "text" | null;
  flexibleOverlayPreviewDataUrl?: string | null;
  resolvedBlocks?: Array<{
    index?: number;
    text?: string | null;
    confidence?: number | null;
    roi?: Record<string, unknown> | null;
    type?: string | null;
    dataType?: string | null;
    extractionMethod?: string | null;
    layoutType?: string | null;
    tableRows?: string[][] | null;
    tableStructured?: TemplateStepTestItem["tableStructured"];
    tableHtml?: string | null;
    source?: string | null;
    cropPreviewDataUrl?: string | null;
    ocrError?: string | null;
  }>;
  ocrText?: string | null;
  actualText?: string | null;
  expectedText?: string | null;
  confidence?: number | null;
  score?: number | null;
  fieldScore?: number | null;
  textMatchScore?: number | null;
  tableRows?: string[][] | null;
  tableStructured?: {
    rows?: string[][];
    cells?: Record<string, unknown>[];
    headerRowCount?: number;
    colWidths?: number[];
    [key: string]: unknown;
  } | null;
  tableSections?: Array<{
    regionId: string;
    rows?: string[][];
    cells?: Record<string, unknown>[];
    tableStructured?: TemplateStepTestItem["tableStructured"];
    bbox?: Record<string, unknown> | unknown[];
    columns?: unknown[];
    text?: string | null;
    [key: string]: unknown;
  }> | null;
  tableHtml?: string | null;
  tableDebug?: Record<string, unknown> | null;
  passed: boolean;
  status?: string | null;
  failureReason?: string | null;
  anchorType?: string | null;
  verificationMethod?: string | null;
  siglipSimilarityScore?: number | null;
  evidenceScore?: number | null;
  rawLogit?: number | null;
  rawPairScore?: number | null;
  relativePercentage?: number | null;
  marginThreshold?: number | null;
  scoringVersion?: string | null;
  siglipUiPercentages?: Record<string, unknown>[];
  imageCategory?: string | null;
  imageCategoryLabel?: string | null;
  imageCategoryPrompt?: string | null;
  predictedImageCategory?: string | null;
  predictedImageCategoryLabel?: string | null;
  predictedImageCategoryPrompt?: string | null;
  siglipTargetRank?: number | null;
  siglipScoreMargin?: number | null;
  referenceCropPreviewDataUrl?: string | null;
  currentCropPreviewDataUrl?: string | null;
  referenceCropPreviewUrl?: string | null;
  currentCropPreviewUrl?: string | null;
  cropPreviewDataUrl?: string | null;
  cropPreviewUrl?: string | null;
}

export interface ImageVerificationCategory {
  value: string;
  label: string;
  prompt: string;
  matchThreshold: number;
  marginThreshold: number;
  evidenceTemperature: number;
  enabled: boolean;
}

export interface TemplateStepTestResult {
  templateId: string;
  status: string;
  passed?: boolean;
  score?: number | null;
  testedCount: number;
  passedCount: number;
  failedCount: number;
  imagePreviewUrl?: string | null;
  roiCoordinateSpace?: string | null;
  fields?: TemplateStepTestItem[];
  anchors?: TemplateStepTestItem[];
}

export interface ApiTemplateRequest {
  id: string;
  request_title: string;
  document_type?: string | null;
  request_mode: "image_only" | "image_with_roi";
  status: string;
  user_note?: string | null;
  admin_note?: string | null;
  converted_template_id?: string | null;
  page_count: number;
  created_at?: string | null;
  updated_at?: string | null;
  pages?: ApiTemplateRequestPage[];
  requested_fields?: ApiRequestedField[];
}

const mapRequestStatus = (status: string): AdminTemplateRequest["status"] => {
  if (status === "converted_to_template") return "converted";
  if (status === "draft" || status === "submitted" || status === "in_review" || status === "converted" || status === "rejected") {
    return status;
  }
  return "draft";
};

const mapTemplateStatus = (status: string): TemplateStatus => {
  if (
    status === "draft" ||
    status === "validated" ||
    status === "embedding_pending" ||
    status === "active" ||
    status === "nonactive" ||
    status === "pending_review" ||
    status === "embedding_generated" ||
    status === "testing" ||
    status === "approved" ||
    status === "rejected" ||
    status === "disabled"
  ) {
    return status;
  }
  return "draft";
};

const normalizeExtractionMethod = (value?: string | null) => {
  if (value === "typhoon_ocr") return "paddle_thai_ocr";
  if (value === "ocr_table" || value === "paddle_thai_ocr" || value === "table_recognition_v2" || value === "extract_image") return value;
  return "ocr_text";
};

const mapEmbeddingJobStatus = (status: string): EmbeddingJobStatus => {
  if (status === "running" || status === "completed" || status === "failed") return status;
  return "queued";
};

export const mapApiRequest = (request: ApiTemplateRequest): AdminTemplateRequest => ({
  id: request.id,
  requestTitle: request.request_title,
  documentType: request.document_type || undefined,
  requestMode: request.request_mode,
  status: mapRequestStatus(request.status),
  userNote: request.user_note || undefined,
  adminNote: request.admin_note || undefined,
  convertedTemplateId: request.converted_template_id || undefined,
  pageCount: request.page_count,
  createdAt: request.created_at || undefined,
  updatedAt: request.updated_at || undefined,
  pages: (request.pages || []).map((page) => ({
    id: page.id,
    templateRequestId: page.template_request_id,
    pageNumber: page.page_number,
    sampleImageUrl: page.sample_image_url || undefined,
    sourceFileId: page.source_file_id || undefined,
    sourceFileName: page.source_file_name || undefined,
    imageSource: page.image_source || undefined,
    reviewStatus: page.review_status || undefined,
    isCanonical: Boolean(page.is_canonical),
    layoutSignatureJson: page.layout_signature_json || undefined,
  })),
  requestedFields: (request.requested_fields || []).map((field) => ({
    id: field.id,
    fieldName: field.field_name,
    displayLabel: field.display_label,
    dataType: (field.data_type || field.dataType || "text") as RoiDataType,
    extractionMethod: normalizeExtractionMethod(field.extraction_method || field.extractionMethod),
    userNote: field.user_note || undefined,
    roi: {
      pageNumber: field.roi.page_number,
      xRatio: field.roi.x_ratio,
      yRatio: field.roi.y_ratio,
      widthRatio: field.roi.width_ratio,
      heightRatio: field.roi.height_ratio,
    },
  })),
});

function mapApiTemplate(template: Partial<ApiTemplate> | null | undefined, fallbackId = ""): Template {
  const source = asRecord(template) as Partial<ApiTemplate>;
  const sourcePages = Array.isArray(source.pages) ? source.pages : [];
  const previewPage = sourcePages.length > 0
    ? sourcePages.find((page) => page && (page.sample_image_url || page.normalized_image_url))
    : undefined;

  return {
    id: source.id || fallbackId,
    name: source.name || source.id || fallbackId || "Template",
    documentType: source.document_type || undefined,
    category: source.category || undefined,
    status: mapTemplateStatus(source.status || "draft"),
    version: typeof source.version === "number" ? source.version : Number(source.version_number || 1),
    templateGroupId: source.template_group_id || undefined,
    versionNumber: source.version_number || source.version || 1,
    baseTemplateId: source.base_template_id || undefined,
    description: source.description || undefined,
    sharedFields: Array.isArray(source.shared_fields) ? source.shared_fields : [],
    creationType: source.creation_type || undefined,
    detectionMode: source.detection_mode || undefined,
    mainPageNumber: source.main_page_number || undefined,
    pageCount: typeof source.page_count === "number" ? source.page_count : 0,
    similarityThreshold: typeof source.similarity_threshold === "number" ? source.similarity_threshold : 0.75,
    finalConfidenceThreshold: typeof source.final_confidence_threshold === "number" ? source.final_confidence_threshold : 0.75,
    layoutWeight: typeof source.layout_weight === "number" ? source.layout_weight : 0.5,
    textAnchorWeight: typeof source.text_anchor_weight === "number" ? source.text_anchor_weight : 0.35,
    imageAnchorWeight: typeof source.image_anchor_weight === "number" ? source.image_anchor_weight : 0.15,
    rejectionReason: source.rejection_reason || undefined,
    previewImageUrl: previewPage?.sample_image_url || previewPage?.normalized_image_url || undefined,
    createdAt: source.created_at || undefined,
    updatedAt: source.updated_at || undefined,
  };
}

const mapApiEmbeddingJob = (job?: ApiEmbeddingJob | null): EmbeddingJob | null => {
  if (!job) return null;
  return {
    id: job.id,
    templateId: job.template_id,
    status: mapEmbeddingJobStatus(job.status),
    requestedAt: job.requested_at || undefined,
    startedAt: job.started_at || null,
    completedAt: job.completed_at || null,
    errorMessage: job.error_message || null,
    vectorId: job.vector_id || null,
    metadataJson: job.metadata_json || null,
  };
};

function mapProjectedField(field: Record<string, unknown>): DetectionProjectedField {
  return {
    fieldId: (field.field_id as string | null | undefined) ?? null,
    fieldName: (field.field_name as string | null | undefined) ?? null,
    displayLabel: (field.display_label as string | null | undefined) ?? null,
    pageNumber: typeof field.page_number === "number" ? field.page_number : null,
    roiMode: field.roi_mode === "flexible" ? "flexible" : "fix",
    expectedContent: field.expected_content === "text" ? "text" : null,
    templateRoi: (field.template_roi as Record<string, unknown> | undefined) || undefined,
    projectedRoiBeforeClip: (field.projected_roi_before_clip as DetectionProjectedField["projectedRoiBeforeClip"]) || null,
    projectedRoi: (field.projected_roi as DetectionProjectedField["projectedRoi"]) || null,
    adaptiveRoi: (field.adaptive_roi as DetectionProjectedField["adaptiveRoi"]) || null,
    adaptiveStatus: (field.adaptive_status as string | null | undefined) ?? null,
    adaptiveSearchRegion: (field.adaptive_search_region as DetectionProjectedField["adaptiveSearchRegion"]) || null,
    adaptiveWordBoxes: Array.isArray(field.adaptive_word_boxes) ? (field.adaptive_word_boxes as Record<string, unknown>[]) : [],
    adaptiveWordGroups: Array.isArray(field.adaptive_word_groups) ? (field.adaptive_word_groups as Record<string, unknown>[]) : [],
    adaptiveRankedWordGroups: Array.isArray(field.adaptive_ranked_word_groups) ? (field.adaptive_ranked_word_groups as Record<string, unknown>[]) : [],
    adaptiveConfidence: typeof field.adaptive_confidence === "number" ? field.adaptive_confidence : null,
    adaptiveWordCount: typeof field.adaptive_word_count === "number" ? field.adaptive_word_count : null,
    adaptiveCoverage: typeof field.adaptive_coverage === "number" ? field.adaptive_coverage : null,
    adaptiveOcrConfidence: typeof field.adaptive_ocr_confidence === "number" ? field.adaptive_ocr_confidence : null,
    adaptiveValidationResult: (field.adaptive_validation_result as Record<string, unknown> | undefined) || undefined,
    adaptiveFallbackReason: (field.adaptive_fallback_reason as string | null | undefined) ?? null,
    projectedPolygon: Array.isArray(field.projected_polygon) ? (field.projected_polygon as number[][]) : undefined,
    projectedPolygonBeforeClip: Array.isArray(field.projected_polygon_before_clip) ? (field.projected_polygon_before_clip as number[][]) : undefined,
    projectionMethod: (field.projection_method as string | null | undefined) ?? null,
    projectionValid: typeof field.projection_valid === "boolean" ? field.projection_valid : null,
    projectionValidationResult: (field.projection_validation_result as Record<string, unknown> | undefined) || undefined,
    fallbackUsed: typeof field.fallback_used === "boolean" ? field.fallback_used : null,
  };
}

function mapDetectionTemplateRoi(field: Record<string, unknown>): DetectionTemplateRoi {
  return {
    fieldId: (field.field_id as string | null | undefined) ?? null,
    fieldName: (field.field_name as string | null | undefined) ?? null,
    displayLabel: (field.display_label as string | null | undefined) ?? null,
    pageNumber: typeof field.page_number === "number" ? field.page_number : null,
    dataType: (field.data_type as string | null | undefined) ?? null,
    extractionMethod: (field.extraction_method as string | null | undefined) ?? null,
    source: (field.source as string | null | undefined) ?? null,
    roi: (field.roi as Record<string, unknown> | undefined) || undefined,
  };
}

function mapDetectionCandidate(candidate: Record<string, unknown>): DetectionCandidate {
  return {
    templateId: (candidate.template_id as string | null | undefined) ?? null,
    vectorId: (candidate.vector_id as string | null | undefined) ?? null,
    score: typeof candidate.score === "number" ? candidate.score : 0,
    retrievalScore: typeof candidate.retrieval_score === "number" ? candidate.retrieval_score : null,
    layoutScore: typeof candidate.layout_score === "number" ? candidate.layout_score : null,
    layoutDebug: (candidate.layout_debug as Record<string, unknown> | undefined) || undefined,
    verificationScore: typeof candidate.verification_score === "number" ? candidate.verification_score : null,
    textAnchorScore: typeof candidate.text_anchor_score === "number" ? candidate.text_anchor_score : null,
    imageAnchorScore: typeof candidate.image_anchor_score === "number" ? candidate.image_anchor_score : null,
    matchingWeights: (candidate.matching_weights as Record<string, number> | undefined) || undefined,
    effectiveMatchingWeights: (candidate.effective_matching_weights as Record<string, number> | undefined) || undefined,
    verificationPassed: typeof candidate.verification_passed === "boolean" ? candidate.verification_passed : null,
    finalScore: typeof candidate.final_score === "number" ? candidate.final_score : null,
    finalPassed: typeof candidate.final_passed === "boolean" ? candidate.final_passed : null,
    decisionReason: (candidate.decision_reason as string | null | undefined) ?? null,
    decisionPath: (candidate.decision_path as string | null | undefined) ?? null,
    requiredPassed: typeof candidate.required_passed === "boolean" ? candidate.required_passed : null,
    requiredFailedFields: Array.isArray(candidate.required_failed_fields)
      ? (candidate.required_failed_fields as Record<string, unknown>[])
      : [],
    finalConfidenceThreshold: typeof candidate.final_confidence_threshold === "number" ? candidate.final_confidence_threshold : null,
    verification: (candidate.verification as Record<string, unknown> | undefined) || undefined,
    averageScore: typeof candidate.average_score === "number" ? candidate.average_score : null,
    matchedPages: typeof candidate.matched_pages === "number" ? candidate.matched_pages : null,
    templateName: (candidate.template_name as string | null | undefined) ?? null,
    templateStatus: (candidate.template_status as string | null | undefined) ?? null,
    pageCount: typeof candidate.page_count === "number" ? candidate.page_count : null,
    fieldCount: typeof candidate.field_count === "number" ? candidate.field_count : null,
    modelName: (candidate.model_name as string | null | undefined) ?? null,
    vectorStoreEngine: (candidate.vector_store_engine as string | null | undefined) ?? null,
    retrievalEngine: (candidate.retrieval_engine as string | null | undefined) ?? null,
    pageIndex: typeof candidate.page_index === "number" ? candidate.page_index : null,
    queryPageIndex: typeof candidate.query_page_index === "number" ? candidate.query_page_index : null,
    templatePageNumber: typeof candidate.template_page_number === "number" ? candidate.template_page_number : null,
    alignmentStatus:
      candidate.alignment_status === "skipped" ||
      candidate.alignment_status === "fallback" ||
      candidate.alignment_status === "failed" ||
      candidate.alignment_status === "aligned"
        ? candidate.alignment_status
        : null,
    alignment: (candidate.alignment as Record<string, unknown> | undefined) || undefined,
    alignmentDebug: (candidate.alignment_debug as Record<string, unknown> | undefined) || undefined,
    alignmentScore: typeof candidate.alignment_score === "number" ? candidate.alignment_score : null,
    alignmentPassed: typeof candidate.alignment_passed === "boolean" ? candidate.alignment_passed : null,
    alignmentFallbackUsed: typeof candidate.alignment_fallback_used === "boolean" ? candidate.alignment_fallback_used : null,
    alignmentReason: (candidate.alignment_reason as string | null | undefined) ?? null,
    normalizedVerificationScore: typeof candidate.normalized_verification_score === "number" ? candidate.normalized_verification_score : null,
    alignedVerificationScore: typeof candidate.aligned_verification_score === "number" ? candidate.aligned_verification_score : null,
    verificationSourceUsed:
      candidate.verification_source_used === "normalized" || candidate.verification_source_used === "aligned"
        ? candidate.verification_source_used
        : null,
    beforeAlignmentVerification: typeof candidate.before_alignment_verification === "number" ? candidate.before_alignment_verification : null,
    afterAlignmentVerification: typeof candidate.after_alignment_verification === "number" ? candidate.after_alignment_verification : null,
    verificationImprovement: typeof candidate.verification_improvement === "number" ? candidate.verification_improvement : null,
    alignmentMatchImagePreviewUrl: (candidate.alignment_match_image_preview_url as string | null | undefined) ?? null,
    alignedImagePreviewUrl: (candidate.aligned_image_preview_url as string | null | undefined) ?? null,
    normalizedImagePreviewUrl: (candidate.normalized_image_preview_url as string | null | undefined) ?? null,
    extractionImagePreviewUrl: (candidate.extraction_image_preview_url as string | null | undefined) ?? null,
    roiCoordinateSpace: (candidate.roi_coordinate_space as string | null | undefined) ?? null,
    templateRois: Array.isArray(candidate.template_rois)
      ? (candidate.template_rois as Record<string, unknown>[]).map(mapDetectionTemplateRoi)
      : [],
    projection: (candidate.projection as Record<string, unknown> | undefined) || undefined,
    projectedFields: Array.isArray(candidate.projected_fields)
      ? (candidate.projected_fields as Record<string, unknown>[]).map(mapProjectedField)
      : [],
    extractionTest: candidate.extraction_test ? mapTemplateStepTestResult(candidate.extraction_test as Record<string, unknown>) : null,
    coordinateDebug: (candidate.coordinate_debug as Record<string, unknown> | undefined) || undefined,
    metadata: (candidate.metadata as Record<string, unknown> | undefined) || {},
  };
}

function mapDetectionPage(page: Record<string, unknown>): DetectionPageResult {
  return {
    pageIndex: typeof page.page_index === "number" ? page.page_index : 1,
    matched: Boolean(page.matched),
    bestCandidate: page.best_candidate ? mapDetectionCandidate(page.best_candidate as Record<string, unknown>) : null,
    candidates: Array.isArray(page.candidates) ? (page.candidates as Record<string, unknown>[]).map(mapDetectionCandidate) : [],
    imagePreviewDataUrl: (page.image_preview_data_url as string | null | undefined) ?? null,
    originalImagePreviewUrl: (page.original_image_preview_url as string | null | undefined) ?? null,
    normalizedImagePreviewUrl: (page.normalized_image_preview_url as string | null | undefined) ?? null,
    originalImagePath: (page.original_image_path as string | null | undefined) ?? null,
    normalizedImagePath: (page.normalized_image_path as string | null | undefined) ?? null,
    normalization: (page.normalization as Record<string, unknown> | undefined) || {},
    debug: (page.debug as Record<string, unknown> | undefined) || {},
  };
}

const mapApiTemplatePage = (page: ApiTemplatePage): TemplatePage => ({
  id: page.id,
  templateId: page.template_id,
  pageNumber: page.page_number,
  pageName: page.page_name || undefined,
  sampleImageUrl: page.sample_image_url || undefined,
  normalizedImageUrl: page.normalized_image_url || undefined,
  layoutSignatureJson: page.layout_signature_json || undefined,
  similarityThreshold: page.similarity_threshold ?? 0.75,
  finalConfidenceThreshold: page.final_confidence_threshold ?? 0.75,
});

const mapApiTemplateField = (field: ApiTemplateField): TemplateField => ({
  id: field.id,
  templateId: field.template_id,
  templatePageId: field.template_page_id,
  pageNumber: field.page_number,
  fieldName: field.field_name,
  displayLabel: field.display_label,
  roi: {
    pageNumber: field.roi.page_number,
    xRatio: field.roi.x_ratio,
    yRatio: field.roi.y_ratio,
    widthRatio: field.roi.width_ratio,
    heightRatio: field.roi.height_ratio,
  },
  dataType: (field.data_type || "text") as RoiDataType,
  userSelectable: field.user_selectable,
  defaultSelected: field.default_selected,
  useForVerification: field.use_for_verification,
  expectedText: field.expected_text || undefined,
  matchType: field.match_type || undefined,
  requiredForVerification: field.required_for_verification,
  extractionMethod: normalizeExtractionMethod(field.extraction_method),
  roiMode: field.roi_mode === "flexible" ? "flexible" : "fix",
  expectedContent: field.expected_content === "text" ? "text" : null,
  roiPadding: field.roi_padding ?? undefined,
  verificationWeight: field.verification_weight ?? undefined,
  imageCategory: parseImageCategoryValue(field.image_category),
  sortOrder: field.sort_order,
});

const mapApiIgnoreRegion = (region: ApiIgnoreRegion): IgnoreRegion => ({
  id: region.id,
  templateId: region.template_id,
  templatePageId: region.template_page_id,
  pageNumber: region.page_number,
  fieldName: region.field_name,
  roi: {
    pageNumber: region.roi.page_number,
    xRatio: region.roi.x_ratio,
    yRatio: region.roi.y_ratio,
    widthRatio: region.roi.width_ratio,
    heightRatio: region.roi.height_ratio,
  },
});

function normalizeTemplateBundle(data: Partial<ApiTemplate> | null | undefined, fallbackId = "") {
  debugPrepublishPagesAccess("normalizeTemplateBundle", data);
  const source = asRecord(data) as Partial<ApiTemplate>;
  return {
    template: mapApiTemplate(source, fallbackId),
    pages: (Array.isArray(source.pages) ? source.pages : []).map(mapApiTemplatePage),
    fields: (Array.isArray(source.fields) ? source.fields : []).map(mapApiTemplateField),
    ignoreRegions: (Array.isArray(source.ignore_regions) ? source.ignore_regions : []).map(mapApiIgnoreRegion),
  };
}

interface ConvertTemplateResponse {
  template_request_id: string;
  converted_template_id?: string | null;
  template_id?: string | null;
  status: string;
  created_records?: {
    templates: number;
    template_pages: number;
    template_fields: number;
  };
}

export const fetchTemplateRequests = async () => {
  if (templateRequestListCache) {
    return cloneTemplateRequests(templateRequestListCache);
  }
  if (templateRequestListPromise) {
    return cloneTemplateRequests(await templateRequestListPromise);
  }

  templateRequestListPromise = (async () => {
    const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/template-requests`);
    if (!response.ok) {
      throw new Error(`Template request load failed with ${response.status}`);
    }

    const json = await response.json();
    const apiRequests = json?.data?.template_requests as ApiTemplateRequest[] | undefined;
    const requests = (apiRequests || []).map(mapApiRequest);
    setTemplateRequestListCache(requests);
    return cloneTemplateRequests(requests);
  })();

  try {
    return cloneTemplateRequests(await templateRequestListPromise);
  } catch (error) {
    templateRequestListPromise = null;
    throw error;
  }
};

export const fetchAdminDashboard = async () => {
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/dashboard`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Admin dashboard load failed with ${response.status}`);
  }

  const json = await response.json();
  const data = json?.data || {};
  const latestRequests = data.latest_requests as ApiTemplateRequest[] | undefined;
  const latestTemplates = data.latest_templates as ApiTemplate[] | undefined;
  return {
    pendingRequests: typeof data.pending_request_count === "number" ? data.pending_request_count : 0,
    draftTemplates: typeof data.draft_template_count === "number" ? data.draft_template_count : 0,
    activeTemplates: typeof data.active_template_count === "number" ? data.active_template_count : 0,
    rejectedRequests: typeof data.rejected_request_count === "number" ? data.rejected_request_count : 0,
    templateCount: typeof data.template_count === "number" ? data.template_count : 0,
    latestRequests: (latestRequests || []).map(mapApiRequest),
    latestTemplates: (latestTemplates || []).map((template) => mapApiTemplate(template)),
  };
};

export const preloadAdminLists = () => {
  void fetchTemplateRequests().catch((error) => {
    console.warn("Template request preload failed.", error);
  });
  void fetchTemplates().catch((error) => {
    console.warn("Template preload failed.", error);
  });
};

export const createTemplateRequest = async (payload: {
  requestTitle: string;
  documentType?: string;
  requestMode?: "image_only" | "image_with_roi";
  pageCount?: number;
  userNote?: string;
  requestedBy?: string;
}) => {
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/template-requests`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      request_title: payload.requestTitle,
      document_type: payload.documentType || "à¹€à¸­à¸à¸ªà¸²à¸£à¸—à¸±à¹ˆà¸§à¹„à¸›",
      request_mode: payload.requestMode || "image_only",
      page_count: payload.pageCount || 1,
      user_note: payload.userNote,
    }),
  });
  const json = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(json?.detail || json?.error?.message || `Create template request failed with ${response.status}`);
  }
  return mapApiRequest(json?.data as ApiTemplateRequest);
};

export const fetchTemplateRequest = async (requestId: string) => {
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/template-requests/${requestId}`);
  if (!response.ok) {
    throw new Error(`Template request detail failed with ${response.status}`);
  }

  const json = await response.json();
  const request = mapApiRequest(json?.data as ApiTemplateRequest);
  upsertTemplateRequestListCache(request);
  return request;
};

export const updateTemplateRequest = async (
  requestId: string,
  patch: {
    requestTitle?: string;
    documentType?: string;
    adminNote?: string;
    status?: string;
  }
) => {
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/template-requests/${requestId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      request_title: patch.requestTitle,
      document_type: patch.documentType,
      admin_note: patch.adminNote,
      status: patch.status,
    }),
  });
  const json = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(json?.detail || json?.error?.message || `Template request update failed with ${response.status}`);
  }
  const request = mapApiRequest(json?.data as ApiTemplateRequest);
  upsertTemplateRequestListCache(request);
  return request;
};

export const fetchTemplateRequestPages = async (requestId: string) => {
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/template-requests/${requestId}/pages`);
  if (!response.ok) {
    throw new Error(`Template request pages failed with ${response.status}`);
  }

  const json = await response.json();
  const pages = json?.data?.pages as ApiTemplateRequestPage[] | undefined;
  return (pages || []).map((page) => ({
    id: page.id,
    templateRequestId: page.template_request_id,
    pageNumber: page.page_number,
    sampleImageUrl: page.sample_image_url || undefined,
    sourceFileId: page.source_file_id || undefined,
    sourceFileName: page.source_file_name || undefined,
    imageSource: page.image_source || undefined,
    reviewStatus: page.review_status || undefined,
    isCanonical: Boolean(page.is_canonical),
    layoutSignatureJson: page.layout_signature_json || undefined,
  }));
};

export const addTemplateRequestImage = async (
  requestId: string,
  sampleImageUrl: string,
  imageSource: "user_request" | "admin_upload" = "admin_upload",
  sourceFileId?: string,
  sourceFileName?: string
) => {
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/template-requests/${requestId}/images`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sample_image_url: sampleImageUrl,
      image_source: imageSource,
      source_file_id: sourceFileId,
      source_file_name: sourceFileName,
      review_status: "pending",
      is_canonical: false,
    }),
  });
  const json = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(json?.detail || json?.error?.message || `Add image failed with ${response.status}`);
  }
  invalidateAdminListCache("requests");
  const page = json?.data as ApiTemplateRequestPage;
  return {
    id: page.id,
    templateRequestId: page.template_request_id,
    pageNumber: page.page_number,
    sampleImageUrl: page.sample_image_url || undefined,
    sourceFileId: page.source_file_id || undefined,
    sourceFileName: page.source_file_name || undefined,
    imageSource: page.image_source || undefined,
    reviewStatus: page.review_status || undefined,
    isCanonical: Boolean(page.is_canonical),
    layoutSignatureJson: page.layout_signature_json || undefined,
  };
};

export const updateTemplateRequestImage = async (
  requestId: string,
  imageId: string,
  patch: {
    sampleImageUrl?: string;
    reviewStatus?: "pending" | "approved" | "rejected";
    isCanonical?: boolean;
    imageSource?: "user_request" | "admin_upload";
    sourceFileId?: string;
    sourceFileName?: string;
  }
) => {
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/template-requests/${requestId}/images/${imageId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sample_image_url: patch.sampleImageUrl,
      review_status: patch.reviewStatus,
      is_canonical: patch.isCanonical,
      image_source: patch.imageSource,
      source_file_id: patch.sourceFileId,
      source_file_name: patch.sourceFileName,
    }),
  });
  const json = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(json?.detail || json?.error?.message || `Update image failed with ${response.status}`);
  }
  invalidateAdminListCache("requests");
  const page = json?.data as ApiTemplateRequestPage;
  return {
    id: page.id,
    templateRequestId: page.template_request_id,
    pageNumber: page.page_number,
    sampleImageUrl: page.sample_image_url || undefined,
    sourceFileId: page.source_file_id || undefined,
    sourceFileName: page.source_file_name || undefined,
    imageSource: page.image_source || undefined,
    reviewStatus: page.review_status || undefined,
    isCanonical: Boolean(page.is_canonical),
    layoutSignatureJson: page.layout_signature_json || undefined,
  };
};

export const deleteTemplateRequestImage = async (requestId: string, imageId: string) => {
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/template-requests/${requestId}/images/${imageId}`, {
    method: "DELETE",
  });
  const json = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(json?.detail || json?.error?.message || `Delete image failed with ${response.status}`);
  }
  invalidateAdminListCache("requests");
  return json?.data;
};

export const deleteTemplateRequest = async (requestId: string) => {
  let response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/template-requests/${requestId}`, {
    method: "DELETE",
  });

  if (response.status === 405) {
    response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/template-requests/${requestId}`, {
      method: "DELETE",
    });
  }

  const json = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = json?.detail || json?.error?.message || json?.error || `Delete failed with ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : `Delete failed with ${response.status}`);
  }

  const verifyResponse = await fetchWithAuth(`${ADMIN_API_BASE_URL}/template-requests/${requestId}`, {
    cache: "no-store",
  });
  if (verifyResponse.ok) {
    const verifyJson = await verifyResponse.json().catch(() => null);
    const verifyData = verifyJson?.data;
    if (verifyData && verifyData.status !== "not_found") {
      throw new Error("Backend reported delete success, but the template request still exists. Restart the backend so the real delete service is loaded.");
    }
  }

  removeTemplateRequestListCache(requestId);

  return json?.data as {
    id: string;
    deleted: boolean;
    converted_template_id?: string | null;
    deleted_records?: {
      template_requests: number;
      template_request_pages: number;
      requested_fields: number;
    };
  };
};

export const fetchTemplateBundle = async (templateId: string) => {
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/templates/${templateId}`);
  if (!response.ok) {
    throw new Error(`Template load failed with ${response.status}`);
  }

  const json = await response.json();
  const data = json?.data as ApiTemplate;
  if (!data || data.status === "not_found") {
    throw new Error("Template not found");
  }

  return normalizeTemplateBundle(data, templateId);
};

export const fetchTemplates = async () => {
  if (templateListCache) {
    return cloneTemplates(templateListCache);
  }
  if (templateListPromise) {
    return cloneTemplates(await templateListPromise);
  }

  templateListPromise = (async () => {
    const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/templates`);
    if (!response.ok) {
      throw new Error(`Template list failed with ${response.status}`);
    }

    const json = await response.json();
    const templates = json?.data?.templates as ApiTemplate[] | undefined;
    const mappedTemplates = (templates || []).map((template) => mapApiTemplate(template));
    setTemplateListCache(mappedTemplates);
    return cloneTemplates(mappedTemplates);
  })();

  try {
    return cloneTemplates(await templateListPromise);
  } catch (error) {
    templateListPromise = null;
    throw error;
  }
};

export const deleteTemplateApi = async (templateId: string) => {
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/templates/${templateId}`, {
    method: "DELETE",
  });
  const json = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = json?.detail || json?.error?.message || json?.error || `Delete failed with ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : `Delete failed with ${response.status}`);
  }
  return json?.data as {
    id: string;
    deleted: boolean;
    deleted_records?: {
      templates: number;
      template_pages: number;
      template_fields: number;
      ignore_regions: number;
      embedding_jobs: number;
    };
  };
};

const isTemplateBundle = (data: Partial<ApiTemplate> | null | undefined): data is ApiTemplate =>
  Boolean(data && typeof data.name === "string" && Array.isArray(data.pages) && Array.isArray(data.fields) && Array.isArray(data.ignore_regions));

const mapTemplateBundleResponse = async (response: Response, templateId: string) => {
  if (!response.ok) {
    throw new Error(`Template mutation failed with ${response.status}`);
  }

  const json = await response.json();
  const data = json?.data as Partial<ApiTemplate> | undefined;
  if (data?.status === "not_found") {
    throw new Error("Template mutation did not return a template bundle");
  }

  if (!isTemplateBundle(data)) {
    return fetchTemplateBundle(templateId);
  }

  return normalizeTemplateBundle(data, templateId);
};

export const updateTemplateApi = async (templateId: string, patch: Partial<Template>) => {
  const bundle = await mapTemplateBundleResponse(
    await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/templates/${templateId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: patch.name,
        document_type: patch.documentType,
        category: patch.category,
        status: patch.status,
        page_count: patch.pageCount,
        similarity_threshold: patch.similarityThreshold,
        final_confidence_threshold: patch.finalConfidenceThreshold,
        layout_weight: patch.layoutWeight,
        text_anchor_weight: patch.textAnchorWeight,
        image_anchor_weight: patch.imageAnchorWeight,
        rejection_reason: patch.rejectionReason,
      }),
    }),
    templateId
  );
  upsertTemplateListCache(bundle.template);
  return bundle;
};

export const updateTemplateStatus = async (templateId: string, status: TemplateStatus) =>
  updateTemplateApi(templateId, { status });

const embeddingJobResponseError = async (response: Response, fallback: string) => {
  const json = await response.json().catch(() => null);
  const detail = json?.detail || json?.error?.message || json?.error || fallback;
  return new Error(typeof detail === "string" ? detail : fallback);
};

const mapEmbeddingJobMutationResponse = async (response: Response) => {
  if (!response.ok) {
    throw await embeddingJobResponseError(response, `Embedding job request failed with ${response.status}`);
  }

  const json = await response.json();
  const data = json?.data as { job?: ApiEmbeddingJob | null; template?: ApiTemplate } | undefined;
  const job = mapApiEmbeddingJob(data?.job);
  if (!job || !data?.template) {
    throw new Error("Embedding job mutation did not return job and template data");
  }

  return {
    job,
    template: mapApiTemplate(data.template),
  };
};

export const createEmbeddingJob = async (templateId: string) =>
  mapEmbeddingJobMutationResponse(
    await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/templates/${templateId}/embedding-jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    })
  );

export const fetchLatestEmbeddingJob = async (templateId: string) => {
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/templates/${templateId}/embedding-jobs/latest`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw await embeddingJobResponseError(response, `Embedding job load failed with ${response.status}`);
  }

  const json = await response.json();
  const job = json?.data?.job as ApiEmbeddingJob | null | undefined;
  return mapApiEmbeddingJob(job);
};

export const completeEmbeddingJobDev = async (jobId: string) =>
  mapEmbeddingJobMutationResponse(
    await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/embedding-jobs/${jobId}/complete-dev`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    })
  );

export const runEmbeddingJobDev = async (jobId: string) =>
  mapEmbeddingJobMutationResponse(
    await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/embedding-jobs/${jobId}/run-dev`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    })
  );

export const failEmbeddingJobDev = async (jobId: string) =>
  mapEmbeddingJobMutationResponse(
    await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/embedding-jobs/${jobId}/fail-dev`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    })
  );

export const detectTemplateDev = async (file: File | File[]): Promise<DetectionDevResult> => {
  const formData = new FormData();
  const files = Array.isArray(file) ? file : [file];
  files.forEach((item, index) => {
    formData.append("file", item, item.name || `page-${index + 1}.jpg`);
  });
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/api/templates/detect-dev`, {
    method: "POST",
    body: formData,
  });
  const json = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = json?.detail || json?.error?.message || json?.error || `Detection failed with ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : `Detection failed with ${response.status}`);
  }

  const data = json?.data as Record<string, unknown> | undefined;
  const candidates = Array.isArray(data?.candidates) ? (data.candidates as Record<string, unknown>[]).map(mapDetectionCandidate) : [];
  const pages = Array.isArray(data?.pages) ? (data.pages as Record<string, unknown>[]).map(mapDetectionPage) : [];
  return {
    queryId: String(data?.query_id || ""),
    engine: String(data?.engine || "stub"),
    version: String(data?.version || "phase7.0"),
    threshold: typeof data?.threshold === "number" ? data.threshold : 0.75,
    matched: Boolean(data?.matched),
    bestCandidate: data?.best_candidate ? mapDetectionCandidate(data.best_candidate as Record<string, unknown>) : null,
    candidates,
    pages,
    message: (data?.message as string | null | undefined) ?? null,
    debug: (data?.debug as Record<string, unknown> | undefined) || {},
  };
};

function mapPrepublishCandidate(candidate: Record<string, unknown>): PrepublishCandidate {
  const globalScore =
    typeof candidate.global_score === "number"
      ? candidate.global_score
      : typeof candidate.score === "number"
        ? candidate.score
        : typeof candidate.layout_score === "number"
          ? candidate.layout_score
          : 0;
  const finalScore =
    typeof candidate.final_score === "number"
      ? candidate.final_score
      : typeof candidate.score === "number"
        ? candidate.score
        : globalScore;
  return {
  rank: Number(candidate.rank || 0),
  templateId: String(candidate.template_id || ""),
  templateName: (candidate.template_name as string | null | undefined) ?? null,
  templateStatus: (candidate.template_status as string | null | undefined) ?? null,
  vectorId: (candidate.vector_id as string | null | undefined) ?? null,
  globalScore,
  textAnchorScore: Number(candidate.text_anchor_score || 0),
  imageAnchorScore: Number(candidate.image_anchor_score || 0),
  verificationScore: Number(candidate.verification_score || 0),
  finalScore,
  matchingWeights: (candidate.matching_weights as Record<string, number> | undefined) || undefined,
  effectiveMatchingWeights: (candidate.effective_matching_weights as Record<string, number> | undefined) || undefined,
  alignmentStatus: String(candidate.alignment_status || "skipped"),
  alignmentReason: (candidate.alignment_reason as string | null | undefined) ?? null,
  alignmentDetails: Array.isArray(candidate.alignment_details)
    ? (candidate.alignment_details as Record<string, unknown>[])
    : [],
  verificationSourceUsed: (candidate.verification_source_used as string | null | undefined) ?? null,
  decision: String(candidate.decision || ""),
  finalPassed: Boolean(candidate.final_passed),
  requiredPassed: typeof candidate.required_passed === "boolean" ? candidate.required_passed : null,
  requiredFailedFields: Array.isArray(candidate.required_failed_fields)
    ? (candidate.required_failed_fields as Record<string, unknown>[])
    : [],
  isCurrentDraft: Boolean(candidate.is_current_draft),
  source: (candidate.source as string | undefined) || (candidate.is_current_draft ? "draft" : "published"),
  sourceLabel: (candidate.source_label as string | null | undefined) ?? null,
  pageCount: typeof candidate.page_count === "number" ? candidate.page_count : null,
  fieldCount: typeof candidate.field_count === "number" ? candidate.field_count : null,
  matchedLayoutReference: (candidate.matched_layout_reference as Record<string, unknown> | null | undefined) ?? null,
  layoutReferenceCount: typeof candidate.layout_reference_count === "number" ? candidate.layout_reference_count : null,
  verification: (candidate.verification as Record<string, unknown> | undefined) || {},
  verificationDetails: Array.isArray(candidate.verification_details)
    ? (candidate.verification_details as Record<string, unknown>[])
    : [],
  };
}

function mapPrepublishLayoutSignaturePage(page: Record<string, unknown>): PrepublishLayoutSignaturePage {
  return {
    templatePageId: (page.template_page_id as string | null | undefined) ?? null,
    templateLayoutReferenceId: (page.template_layout_reference_id as string | null | undefined) ?? null,
    pageNumber: Number(page.page_number || 0),
    status: String(page.status || "pending"),
    engine: (page.engine as string | null | undefined) ?? null,
    version: (page.version as string | null | undefined) ?? null,
    modelName: (page.model_name as string | null | undefined) ?? (page.model as string | null | undefined) ?? null,
    labelCount: typeof page.label_count === "number" ? page.label_count : typeof page.region_count === "number" ? page.region_count : null,
    imageUrl:
      (page.image_url as string | null | undefined) ??
      (page.normalized_image_url as string | null | undefined) ??
      (page.sample_image_url as string | null | undefined) ??
      null,
    imageSource: (page.image_source as string | null | undefined) ?? null,
    isCanonical: Boolean(page.is_canonical),
    referenceRole: (page.reference_role as string | null | undefined) ?? null,
    persisted: Boolean(page.persisted),
    reason: (page.reason as string | null | undefined) ?? null,
  };
}

export const runPrepublishSimulation = async (templateId: string): Promise<PrepublishSimulationResult> => {
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/templates/${templateId}/prepublish-simulation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  const json = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = json?.detail || json?.error?.message || json?.error || `Pre-publish simulation failed with ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : `Pre-publish simulation failed with ${response.status}`);
  }

  const data = asRecord(json?.data);
  debugPrepublishPagesAccess("runPrepublishSimulation:data", data);
  const summary = asRecord(data.draft_summary);
  const temp = asRecord(data.temporary_embedding);
  const separation = asRecord(data.separation_analysis);
  const layoutSignaturePages = Array.isArray(data.layout_signature_pages)
    ? asRecordArray(data.layout_signature_pages).map(mapPrepublishLayoutSignaturePage)
    : [];
  const candidates = Array.isArray(data.candidates)
    ? asRecordArray(data.candidates).map(mapPrepublishCandidate)
    : [];
  const conflictTemplates = Array.isArray(separation.conflict_templates)
    ? asRecordArray(separation.conflict_templates).map(mapPrepublishCandidate)
    : [];
  const rootPassed = typeof data.passed === "boolean" ? data.passed : null;
  const separationPassed = typeof separation.simulation_passed === "boolean" ? separation.simulation_passed : null;

  const responseTemplate = data.template && typeof data.template === "object" && !Array.isArray(data.template)
    ? (data.template as ApiTemplate)
    : undefined;
  let template = mapApiTemplate(responseTemplate, templateId);
  if (!responseTemplate) {
    try {
      template = (await fetchTemplateBundle(templateId)).template;
    } catch {
      template = mapApiTemplate(
        {
          id: templateId,
          name: String(summary.template_name || data.template_name || templateId),
          status: String(summary.status || data.status || "draft"),
          version: 1,
          page_count: Number(summary.page_count || 0),
          similarity_threshold: typeof summary.similarity_threshold === "number" ? summary.similarity_threshold : 0.75,
          final_confidence_threshold: typeof summary.final_confidence_threshold === "number" ? summary.final_confidence_threshold : 0.75,
          layout_weight: typeof summary.layout_weight === "number" ? summary.layout_weight : 0.5,
          text_anchor_weight: typeof summary.text_anchor_weight === "number" ? summary.text_anchor_weight : 0.35,
          image_anchor_weight: typeof summary.image_anchor_weight === "number" ? summary.image_anchor_weight : 0.15,
        },
        templateId
      );
    }
  }

  return {
    template,
    draftSummary: {
      templateName: (summary.template_name as string | null | undefined) ?? null,
      templateId: String(summary.template_id || templateId),
      status: String(summary.status || "draft"),
      pageCount: Number(summary.page_count || 0),
      extractionFieldCount: Number(summary.extraction_field_count || 0),
      textAnchorCount: Number(summary.text_anchor_count || 0),
      imageAnchorCount: Number(summary.image_anchor_count || 0),
      similarityThreshold: typeof summary.similarity_threshold === "number" ? summary.similarity_threshold : null,
      finalConfidenceThreshold: typeof summary.final_confidence_threshold === "number" ? summary.final_confidence_threshold : null,
      layoutWeight: typeof summary.layout_weight === "number" ? summary.layout_weight : null,
      textAnchorWeight: typeof summary.text_anchor_weight === "number" ? summary.text_anchor_weight : null,
      imageAnchorWeight: typeof summary.image_anchor_weight === "number" ? summary.image_anchor_weight : null,
    },
    temporaryEmbedding: {
      status: String(temp.status || "not_generated"),
      engine: String(temp.engine || "stub"),
      version: String(temp.version || ""),
      modelName: String(temp.model_name || ""),
      embeddingDimension: Number(temp.embedding_dimension || 0),
      inputCount: Number(temp.input_count || 0),
      generatedAt: (temp.generated_at as string | undefined) || (data.timestamp as string | undefined) || undefined,
      persisted: Boolean(temp.persisted),
      note: (temp.note as string | undefined) || undefined,
      layoutSignaturePages: Array.isArray(temp.layout_signature_pages)
        ? asRecordArray(temp.layout_signature_pages).map(mapPrepublishLayoutSignaturePage)
        : layoutSignaturePages,
    },
    layoutSignaturePages,
    candidates,
    verificationAnchorResults: Array.isArray(data.verification_anchor_results)
      ? asRecordArray(data.verification_anchor_results)
      : [],
    separationAnalysis: {
      top1Score: Number(separation.top1_score || 0),
      top2Score: typeof separation.top2_score === "number" ? separation.top2_score : null,
      status: String(separation.status || data.status || (rootPassed ? "ready_to_publish" : "not_ready")),
      simulationPassed: separationPassed ?? Boolean(rootPassed),
      conflictTemplates,
      message: (separation.message as string | undefined) || undefined,
    },
  };
};

export const runPrepublishDetectionTest = async (templateId: string, file: File): Promise<PrepublishDetectionTestResult> => {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/templates/${templateId}/prepublish-detection-test`, {
    method: "POST",
    body: formData,
  });
  const json = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = json?.detail || json?.error?.message || json?.error || `Pre-publish detection test failed with ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : `Pre-publish detection test failed with ${response.status}`);
  }

  const data = (json?.data as Record<string, unknown> | undefined) || {};
  const candidates = Array.isArray(data.candidates)
    ? (data.candidates as Record<string, unknown>[]).map(mapPrepublishCandidate)
    : [];
  const separation = (data.separation_result as Record<string, unknown> | undefined) || {};
  return {
    testId: String(data.test_id || ""),
    templateId: String(data.template_id || templateId),
    matched: Boolean(data.matched),
    selectedTemplate: data.selected_template ? mapPrepublishCandidate(data.selected_template as Record<string, unknown>) : null,
    selectedTemplateType: (data.selected_template_type as string | null | undefined) ?? null,
    finalConfidence: Number(data.final_confidence || 0),
    decisionReason: (data.decision_reason as string | null | undefined) ?? null,
    draftTemplateRank: typeof data.draft_template_rank === "number" ? data.draft_template_rank : null,
    passed: Boolean(data.passed),
    warning: Boolean(data.warning),
    candidates,
    separationResult: {
      draftTemplateRank: typeof separation.draft_template_rank === "number" ? separation.draft_template_rank : null,
      draftFinalScore: Number(separation.draft_final_score || 0),
      closestPublishedTemplate: (separation.closest_published_template as string | null | undefined) ?? null,
      closestPublishedScore: typeof separation.closest_published_score === "number" ? separation.closest_published_score : null,
      conflictLevel: String(separation.conflict_level || "not_ready"),
      recommendation: String(separation.recommendation || ""),
    },
    debug: (data.debug as Record<string, unknown> | undefined) || {},
  };
};

export const confirmTemplatePublish = async (templateId: string) => {
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/templates/${templateId}/confirm-publish`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  return mapEmbeddingJobMutationResponse(response);
};

function mapTemplateStepTestItem(item: Record<string, unknown>): TemplateStepTestItem {
  const rawSections = Array.isArray(item.table_sections)
    ? item.table_sections
    : Array.isArray(item.tableSections)
      ? item.tableSections
      : null;
  return {
    fieldId: (item.field_id as string | undefined) || undefined,
    anchorId: (item.anchor_id as string | undefined) || undefined,
    fieldName: (item.field_name as string | null | undefined) ?? null,
    displayLabel: (item.display_label as string | null | undefined) ?? null,
    pageNumber: typeof item.page_number === "number" ? item.page_number : null,
    dataType: (item.data_type as string | null | undefined) ?? null,
    extractionMethod: (item.extraction_method as string | null | undefined) ?? null,
    roiSource: (item.roi_source as string | null | undefined) ?? null,
    roi: (item.roi as Record<string, unknown> | undefined) || null,
    roiMode: item.roi_mode === "flexible" ? "flexible" : "fix",
    expectedContent: item.expected_content === "text" ? "text" : null,
    flexibleOverlayPreviewDataUrl: (item.flexible_overlay_preview_data_url as string | null | undefined) ?? (item.flexibleOverlayPreviewDataUrl as string | null | undefined) ?? null,
    resolvedBlocks: Array.isArray(item.resolved_blocks)
      ? (item.resolved_blocks as Record<string, unknown>[]).map((block, index) => ({
          index: typeof block.index === "number" ? block.index : index,
          text: (block.text as string | null | undefined) ?? null,
          confidence: typeof block.confidence === "number" ? block.confidence : null,
          roi: (block.roi as Record<string, unknown> | undefined) || null,
          type: (block.type as string | null | undefined) ?? null,
          dataType: (block.data_type as string | null | undefined) ?? (block.dataType as string | null | undefined) ?? null,
          extractionMethod: (block.extraction_method as string | null | undefined) ?? (block.extractionMethod as string | null | undefined) ?? null,
          layoutType: (block.layout_type as string | null | undefined) ?? (block.layoutType as string | null | undefined) ?? null,
          tableRows: Array.isArray(block.table_rows)
            ? (block.table_rows as unknown[][]).map((row) => row.map((cell) => String(cell ?? "")))
            : Array.isArray(block.tableRows)
              ? (block.tableRows as unknown[][]).map((row) => row.map((cell) => String(cell ?? "")))
              : null,
          tableStructured:
            block.table_structured && typeof block.table_structured === "object"
              ? (block.table_structured as TemplateStepTestItem["tableStructured"])
              : block.tableStructured && typeof block.tableStructured === "object"
                ? (block.tableStructured as TemplateStepTestItem["tableStructured"])
                : undefined,
          tableHtml: (block.table_html as string | null | undefined) ?? (block.tableHtml as string | null | undefined) ?? null,
          source: (block.source as string | null | undefined) ?? null,
          cropPreviewDataUrl: (block.crop_preview_data_url as string | null | undefined) ?? (block.cropPreviewDataUrl as string | null | undefined) ?? null,
          ocrError: (block.ocr_error as string | null | undefined) ?? (block.ocrError as string | null | undefined) ?? null,
        }))
      : [],
    ocrText: (item.ocr_text as string | null | undefined) ?? null,
    actualText: (item.actual_text as string | null | undefined) ?? null,
    expectedText: (item.expected_text as string | null | undefined) ?? null,
    confidence: typeof item.confidence === "number" ? item.confidence : typeof item.ocr_confidence === "number" ? item.ocr_confidence : null,
    score: typeof item.score === "number" ? item.score : null,
    fieldScore: typeof item.field_score === "number" ? item.field_score : null,
    textMatchScore: typeof item.text_match_score === "number" ? item.text_match_score : null,
    tableRows: Array.isArray(item.table_rows)
      ? (item.table_rows as unknown[][]).map((row) => row.map((cell) => String(cell ?? "")))
      : Array.isArray(item.tableRows)
        ? (item.tableRows as unknown[][]).map((row) => row.map((cell) => String(cell ?? "")))
        : null,
    tableStructured:
      item.table_structured && typeof item.table_structured === "object"
        ? (item.table_structured as TemplateStepTestItem["tableStructured"])
        : item.tableStructured && typeof item.tableStructured === "object"
          ? (item.tableStructured as TemplateStepTestItem["tableStructured"])
          : null,
    tableSections: rawSections
      ? (rawSections as Record<string, unknown>[]).map((section, index) => ({
          ...section,
          regionId: String(section.regionId || section.region_id || `region_${index + 1}`),
          rows: Array.isArray(section.rows)
            ? (section.rows as unknown[][]).map((row) => row.map((cell) => String(cell ?? "")))
            : undefined,
          tableStructured:
            section.table_structured && typeof section.table_structured === "object"
              ? (section.table_structured as TemplateStepTestItem["tableStructured"])
              : section.tableStructured && typeof section.tableStructured === "object"
                ? (section.tableStructured as TemplateStepTestItem["tableStructured"])
                : undefined,
        }))
      : null,
    tableHtml: (item.table_html as string | null | undefined) ?? (item.tableHtml as string | null | undefined) ?? null,
    tableDebug:
      (item.table_debug as Record<string, unknown> | null | undefined) ??
      (item.tableDebug as Record<string, unknown> | null | undefined) ??
      null,
    passed: Boolean(item.passed),
    status: (item.status as string | null | undefined) ?? null,
    failureReason: (item.failure_reason as string | null | undefined) ?? null,
    anchorType: (item.anchor_type as string | null | undefined) ?? null,
    verificationMethod: (item.verification_method as string | null | undefined) ?? null,
    siglipSimilarityScore: typeof item.siglip_similarity_score === "number" ? item.siglip_similarity_score : null,
    evidenceScore: typeof item.evidence_score === "number" ? item.evidence_score : null,
    rawLogit: typeof item.raw_logit === "number" ? item.raw_logit : null,
    rawPairScore: typeof item.raw_pair_score === "number" ? item.raw_pair_score : null,
    relativePercentage: typeof item.relative_percentage === "number" ? item.relative_percentage : null,
    marginThreshold: typeof item.margin_threshold === "number" ? item.margin_threshold : null,
    scoringVersion: (item.scoring_version as string | null | undefined) ?? null,
    siglipUiPercentages: Array.isArray(item.siglip_ui_percentages) ? (item.siglip_ui_percentages as Record<string, unknown>[]) : [],
    imageCategory: (item.image_category as string | null | undefined) ?? null,
    imageCategoryLabel: (item.image_category_label as string | null | undefined) ?? null,
    imageCategoryPrompt: (item.image_category_prompt as string | null | undefined) ?? null,
    predictedImageCategory: (item.predicted_image_category as string | null | undefined) ?? null,
    predictedImageCategoryLabel: (item.predicted_image_category_label as string | null | undefined) ?? null,
    predictedImageCategoryPrompt: (item.predicted_image_category_prompt as string | null | undefined) ?? null,
    siglipTargetRank: typeof item.siglip_target_rank === "number" ? item.siglip_target_rank : null,
    siglipScoreMargin: typeof item.siglip_score_margin === "number" ? item.siglip_score_margin : null,
    referenceCropPreviewDataUrl: (item.reference_crop_preview_data_url as string | null | undefined) ?? null,
    currentCropPreviewDataUrl: (item.current_crop_preview_data_url as string | null | undefined) ?? null,
    referenceCropPreviewUrl: (item.reference_crop_preview_url as string | null | undefined) ?? null,
    currentCropPreviewUrl: (item.current_crop_preview_url as string | null | undefined) ?? null,
    cropPreviewDataUrl:
      (item.crop_preview_data_url as string | null | undefined) ??
      (item.current_crop_preview_data_url as string | null | undefined) ??
      null,
    cropPreviewUrl:
      (item.crop_preview_url as string | null | undefined) ??
      (item.current_crop_preview_url as string | null | undefined) ??
      null,
  };
}

function mapTemplateStepTestResult(data: Record<string, unknown>): TemplateStepTestResult {
  return {
    templateId: String(data.template_id || ""),
    status: String(data.status || ""),
    passed: typeof data.passed === "boolean" ? data.passed : undefined,
    score: typeof data.score === "number" ? data.score : null,
    testedCount: Number(data.tested_count || 0),
    passedCount: Number(data.passed_count || 0),
    failedCount: Number(data.failed_count || 0),
    imagePreviewUrl: (data.image_preview_url as string | null | undefined) ?? null,
    roiCoordinateSpace: (data.roi_coordinate_space as string | null | undefined) ?? null,
    fields: Array.isArray(data.fields) ? (data.fields as Record<string, unknown>[]).map(mapTemplateStepTestItem) : undefined,
    anchors: Array.isArray(data.anchors) ? (data.anchors as Record<string, unknown>[]).map(mapTemplateStepTestItem) : undefined,
  };
}

const runTemplateStepTest = async (templateId: string, path: "test-extraction" | "test-verification") => {
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/templates/${templateId}/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  const json = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = json?.detail || json?.error?.message || json?.error || `Template step test failed with ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : `Template step test failed with ${response.status}`);
  }
  return mapTemplateStepTestResult((json?.data as Record<string, unknown> | undefined) || {});
};

export const testTemplateExtractionFields = (templateId: string) => runTemplateStepTest(templateId, "test-extraction");

export const testTemplateVerificationAnchors = (templateId: string) => runTemplateStepTest(templateId, "test-verification");

export const createTemplatePageApi = async (templateId: string, pageNumber: number, sampleImageUrl?: string) =>
  mapTemplateBundleResponse(
    await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/templates/${templateId}/pages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        page_number: pageNumber,
        page_name: `Page ${pageNumber}`,
        sample_image_url: sampleImageUrl,
        normalized_image_url: sampleImageUrl,
      }),
    }),
    templateId
  );

export const updateTemplatePageApi = async (templateId: string, pageId: string, patch: Partial<TemplatePage>) =>
  mapTemplateBundleResponse(
    await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/templates/${templateId}/pages/${pageId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        page_number: patch.pageNumber,
        page_name: patch.pageName,
        sample_image_url: patch.sampleImageUrl,
        normalized_image_url: patch.normalizedImageUrl,
        similarity_threshold: patch.similarityThreshold,
        final_confidence_threshold: patch.finalConfidenceThreshold,
      }),
    }),
    templateId
  );

export const deleteTemplatePageApi = async (templateId: string, pageId: string) =>
  mapTemplateBundleResponse(
    await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/templates/${templateId}/pages/${pageId}`, {
      method: "DELETE",
    }),
    templateId
  );

const fieldToApiPayload = (
  field: Partial<TemplateField> & Pick<TemplateField, "templatePageId" | "pageNumber" | "fieldName" | "displayLabel" | "roi">
) => ({
  template_page_id: field.templatePageId,
  page_number: field.pageNumber,
  field_name: field.fieldName,
  display_label: field.displayLabel,
  roi: {
    page_number: field.roi.pageNumber,
    x_ratio: field.roi.xRatio,
    y_ratio: field.roi.yRatio,
    width_ratio: field.roi.widthRatio,
    height_ratio: field.roi.heightRatio,
  },
  data_type: field.dataType || "text",
  user_selectable: field.userSelectable ?? true,
  default_selected: field.defaultSelected ?? true,
  use_for_verification: field.useForVerification ?? false,
  expected_text: field.expectedText,
  match_type: field.matchType,
  required_for_verification: field.requiredForVerification ?? false,
  extraction_method: normalizeExtractionMethod(field.extractionMethod),
  roi_mode: field.roiMode === "flexible" ? "flexible" : "fix",
  expected_content: field.roiMode === "flexible" ? "text" : null,
  roi_padding: field.roiPadding ?? 0,
  verification_weight: field.verificationWeight ?? 1,
  image_category: serializeImageCategoryValue(field.imageCategory),
  sort_order: field.sortOrder ?? 0,
});

export const createTemplateFieldApi = async (
  templateId: string,
  field: Partial<TemplateField> & Pick<TemplateField, "templatePageId" | "pageNumber" | "fieldName" | "displayLabel" | "roi">
) =>
  mapTemplateBundleResponse(
    await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/templates/${templateId}/fields`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fieldToApiPayload(field)),
    }),
    templateId
  );

export const updateTemplateFieldApi = async (templateId: string, fieldId: string, patch: Partial<TemplateField>) => {
  const payload: Record<string, unknown> = {
    template_page_id: patch.templatePageId,
    page_number: patch.pageNumber,
    field_name: patch.fieldName,
    display_label: patch.displayLabel,
    data_type: patch.dataType,
    user_selectable: patch.userSelectable,
    default_selected: patch.defaultSelected,
    use_for_verification: patch.useForVerification,
    expected_text: patch.expectedText,
    match_type: patch.matchType,
    required_for_verification: patch.requiredForVerification,
    extraction_method: patch.extractionMethod ? normalizeExtractionMethod(patch.extractionMethod) : undefined,
    roi_mode: patch.roiMode,
    expected_content: patch.expectedContent,
    roi_padding: patch.roiPadding,
    verification_weight: patch.verificationWeight,
    image_category: serializeImageCategoryValue(patch.imageCategory),
    sort_order: patch.sortOrder,
  };

  if (patch.roi) {
    payload.roi = {
      page_number: patch.roi.pageNumber,
      x_ratio: patch.roi.xRatio,
      y_ratio: patch.roi.yRatio,
      width_ratio: patch.roi.widthRatio,
      height_ratio: patch.roi.heightRatio,
    };
  }

  return mapTemplateBundleResponse(
    await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/templates/${templateId}/fields/${fieldId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
    templateId
  );
};

export const deleteTemplateFieldApi = async (templateId: string, fieldId: string) =>
  mapTemplateBundleResponse(
    await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/templates/${templateId}/fields/${fieldId}`, {
      method: "DELETE",
    }),
    templateId
  );

const ignoreRegionToApiPayload = (
  region: Partial<IgnoreRegion> & Pick<IgnoreRegion, "templatePageId" | "pageNumber" | "fieldName" | "roi">
) => ({
  template_page_id: region.templatePageId,
  page_number: region.pageNumber,
  field_name: region.fieldName,
  roi: {
    page_number: region.roi.pageNumber,
    x_ratio: region.roi.xRatio,
    y_ratio: region.roi.yRatio,
    width_ratio: region.roi.widthRatio,
    height_ratio: region.roi.heightRatio,
  },
});

export const createIgnoreRegionApi = async (
  templateId: string,
  region: Partial<IgnoreRegion> & Pick<IgnoreRegion, "templatePageId" | "pageNumber" | "fieldName" | "roi">
) =>
  mapTemplateBundleResponse(
    await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/templates/${templateId}/ignore-regions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ignoreRegionToApiPayload(region)),
    }),
    templateId
  );

export const updateIgnoreRegionApi = async (templateId: string, regionId: string, patch: Partial<IgnoreRegion>) => {
  const payload: Record<string, unknown> = {
    template_page_id: patch.templatePageId,
    page_number: patch.pageNumber,
    field_name: patch.fieldName,
  };

  if (patch.roi) {
    payload.roi = {
      page_number: patch.roi.pageNumber,
      x_ratio: patch.roi.xRatio,
      y_ratio: patch.roi.yRatio,
      width_ratio: patch.roi.widthRatio,
      height_ratio: patch.roi.heightRatio,
    };
  }

  return mapTemplateBundleResponse(
    await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/templates/${templateId}/ignore-regions/${regionId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
    templateId
  );
};

export const deleteIgnoreRegionApi = async (templateId: string, regionId: string) =>
  mapTemplateBundleResponse(
    await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/templates/${templateId}/ignore-regions/${regionId}`, {
      method: "DELETE",
    }),
    templateId
  );

export const convertTemplateRequestToTemplate = async (
  requestId: string,
  payload?: {
    detectionMode?: "all_pages" | "main_page";
    mainPageNumber?: number;
  }
) => {
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/template-requests/${requestId}/convert-to-template`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      detection_mode: payload?.detectionMode || "all_pages",
      main_page_number: payload?.mainPageNumber || 1,
    }),
  });
  if (!response.ok) {
    throw new Error(`Template request conversion failed with ${response.status}`);
  }

  const json = await response.json();
  const result = json?.data as ConvertTemplateResponse;
  const templateId = result?.template_id || result?.converted_template_id;
  if (!templateId) {
    throw new Error("Template request conversion did not return a template id");
  }
  invalidateAdminListCache("all");
  return {
    templateId,
    status: result.status,
    createdRecords: result.created_records,
  };
};

export const suggestTemplateRequestBaseVersion = async (
  requestId: string,
  baseTemplateId: string,
  similarityThreshold = 0.72
) => {
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/template-requests/${requestId}/suggest-base-version`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      base_template_id: baseTemplateId,
      similarity_threshold: similarityThreshold,
    }),
  });
  const json = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = json?.detail || json?.error?.message || `Base version suggestion failed with ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : `Base version suggestion failed with ${response.status}`);
  }
  return json?.data as {
    request_id: string;
    template_group_id: string;
    versions: ApiTemplate[];
    suggested_base_version?: {
      template_id: string;
      template_page_id?: string | null;
      page_number: number;
      request_page_id: string;
      request_page_number: number;
      similarity_score: number;
    } | null;
    reuse_roi: boolean;
    similarity_threshold: number;
    message: string;
  };
};

export const convertTemplateRequestToVersion = async (
  requestId: string,
  payload: {
    baseTemplateId: string;
    templateName?: string;
    description?: string;
    sharedFields?: string[];
    documentType?: string;
    similarityThreshold?: number;
    reuseRoi?: boolean;
    detectionMode?: "all_pages" | "main_page";
    mainPageNumber?: number;
  }
) => {
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/template-requests/${requestId}/convert-to-version`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      base_template_id: payload.baseTemplateId,
      template_name: payload.templateName,
      description: payload.description,
      shared_fields: payload.sharedFields || [],
      document_type: payload.documentType,
      similarity_threshold: payload.similarityThreshold ?? 0.72,
      reuse_roi: payload.reuseRoi ?? true,
      detection_mode: payload.detectionMode || "all_pages",
      main_page_number: payload.mainPageNumber || 1,
    }),
  });
  const json = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = json?.detail || json?.error?.message || `Template version creation failed with ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : `Template version creation failed with ${response.status}`);
  }
  const result = json?.data as ConvertTemplateResponse & {
    base_template_id?: string;
    template_group_id?: string;
    version_number?: number;
    reuse_roi?: boolean;
  };
  const templateId = result?.template_id || result?.converted_template_id;
  if (!templateId) {
    throw new Error("Template version creation did not return a template id");
  }
  invalidateAdminListCache("all");
  return {
    templateId,
    status: result.status,
    createdRecords: result.created_records,
    baseTemplateId: result.base_template_id,
    templateGroupId: result.template_group_id,
    versionNumber: result.version_number,
    reuseRoi: Boolean(result.reuse_roi),
  };
};

const mapImageVerificationCategory = (item: Record<string, unknown>): ImageVerificationCategory => ({
  value: String(item.value || ""),
  label: String(item.label || item.value || ""),
  prompt: String(item.prompt || ""),
  matchThreshold: typeof item.match_threshold === "number" ? item.match_threshold : Number(item.matchThreshold || 0.7),
  marginThreshold: typeof item.margin_threshold === "number" ? item.margin_threshold : Number(item.marginThreshold || 0.05),
  evidenceTemperature:
    typeof item.evidence_temperature === "number" ? item.evidence_temperature : Number(item.evidenceTemperature || 1),
  enabled: Boolean(item.enabled),
});

export const listImageVerificationCategories = async (enabledOnly = false): Promise<ImageVerificationCategory[]> => {
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/image-verification-categories?enabled_only=${enabledOnly ? "true" : "false"}`);
  const json = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = json?.detail || json?.error?.message || json?.error || `Load image categories failed with ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : `Load image categories failed with ${response.status}`);
  }
  const categories = Array.isArray(json?.data?.categories) ? json.data.categories : [];
  return categories.map((item: Record<string, unknown>) => mapImageVerificationCategory(item));
};

export const createImageVerificationCategory = async (
  payload: Omit<ImageVerificationCategory, "matchThreshold" | "marginThreshold" | "evidenceTemperature"> & {
    matchThreshold?: number;
    marginThreshold?: number;
    evidenceTemperature?: number;
  }
): Promise<ImageVerificationCategory> => {
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/image-verification-categories`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      value: payload.value,
      label: payload.label,
      prompt: payload.prompt,
      match_threshold: payload.matchThreshold ?? 0.7,
      margin_threshold: payload.marginThreshold ?? 0.05,
      evidence_temperature: payload.evidenceTemperature ?? 1,
      enabled: payload.enabled,
    }),
  });
  const json = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = json?.detail || json?.error?.message || json?.error || `Create image category failed with ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : `Create image category failed with ${response.status}`);
  }
  return mapImageVerificationCategory(json?.data?.category || {});
};

export const updateImageVerificationCategory = async (
  value: string,
  patch: Partial<ImageVerificationCategory>
): Promise<ImageVerificationCategory> => {
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/image-verification-categories/${encodeURIComponent(value)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      label: patch.label,
      prompt: patch.prompt,
      match_threshold: patch.matchThreshold,
      margin_threshold: patch.marginThreshold,
      evidence_temperature: patch.evidenceTemperature,
      enabled: patch.enabled,
    }),
  });
  const json = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = json?.detail || json?.error?.message || json?.error || `Update image category failed with ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : `Update image category failed with ${response.status}`);
  }
  return mapImageVerificationCategory(json?.data?.category || {});
};

export const deleteImageVerificationCategory = async (value: string): Promise<{ value: string; deleted: boolean }> => {
  const response = await fetchWithAuth(`${ADMIN_API_BASE_URL}/admin/image-verification-categories/${encodeURIComponent(value)}`, {
    method: "DELETE",
  });
  const json = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = json?.detail || json?.error?.message || json?.error || `Delete image category failed with ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : `Delete image category failed with ${response.status}`);
  }
  return {
    value: String(json?.data?.value || value),
    deleted: Boolean(json?.data?.deleted),
  };
};
