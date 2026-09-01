import {
  AdminTemplateRequest,
  IgnoreRegion,
  Template,
  TemplateField,
  TemplatePage,
} from "../types/ocr";

export type AdminStatusFilter = "all" | "draft" | "active" | "nonactive";

export interface AdminDashboardSummary {
  pendingRequests: number;
  draftTemplates: number;
  activeTemplates: number;
  rejectedRequests: number;
  templateCount: number;
  latestRequests: AdminTemplateRequest[];
  latestTemplates: Template[];
}

export interface AdminDataSnapshot {
  requests: AdminTemplateRequest[];
  templates: Template[];
  pages: TemplatePage[];
  fields: TemplateField[];
  ignoreRegions: IgnoreRegion[];
}
