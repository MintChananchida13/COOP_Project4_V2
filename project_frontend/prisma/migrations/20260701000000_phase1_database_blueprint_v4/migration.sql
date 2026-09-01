-- CreateTable
CREATE TABLE "templates" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "name" TEXT NOT NULL,
    "document_type" TEXT,
    "category" TEXT,
    "status" TEXT NOT NULL DEFAULT 'draft',
    "version" INTEGER NOT NULL DEFAULT 1,
    "page_count" INTEGER NOT NULL DEFAULT 1,
    "similarity_threshold" REAL NOT NULL DEFAULT 0.75,
    "final_confidence_threshold" REAL NOT NULL DEFAULT 0.8,
    "created_by" TEXT,
    "approved_by" TEXT,
    "rejection_reason" TEXT,
    "created_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- CreateTable
CREATE TABLE "template_pages" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "template_id" TEXT NOT NULL,
    "page_number" INTEGER NOT NULL,
    "page_name" TEXT,
    "sample_image_url" TEXT,
    "normalized_image_url" TEXT,
    "similarity_threshold" REAL,
    "final_confidence_threshold" REAL,
    "created_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "template_pages_template_id_fkey" FOREIGN KEY ("template_id") REFERENCES "templates" ("id") ON DELETE CASCADE ON UPDATE CASCADE
);

-- CreateTable
CREATE TABLE "template_fields" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "template_id" TEXT NOT NULL,
    "template_page_id" TEXT NOT NULL,
    "page_number" INTEGER NOT NULL,
    "field_name" TEXT NOT NULL,
    "display_label" TEXT NOT NULL,
    "roi_x_ratio" REAL NOT NULL,
    "roi_y_ratio" REAL NOT NULL,
    "roi_width_ratio" REAL NOT NULL,
    "roi_height_ratio" REAL NOT NULL,
    "data_type" TEXT,
    "user_selectable" BOOLEAN NOT NULL DEFAULT true,
    "default_selected" BOOLEAN NOT NULL DEFAULT false,
    "use_for_verification" BOOLEAN NOT NULL DEFAULT false,
    "expected_text" TEXT,
    "match_type" TEXT,
    "required_for_verification" BOOLEAN NOT NULL DEFAULT false,
    "extraction_method" TEXT NOT NULL DEFAULT 'fixed_roi',
    "anchor_text" TEXT,
    "regex_pattern" TEXT,
    "roi_padding" REAL,
    "sort_order" INTEGER NOT NULL DEFAULT 0,
    "created_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "template_fields_template_id_fkey" FOREIGN KEY ("template_id") REFERENCES "templates" ("id") ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "template_fields_template_page_id_fkey" FOREIGN KEY ("template_page_id") REFERENCES "template_pages" ("id") ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "template_fields_roi_ratio_check" CHECK (
        "roi_x_ratio" >= 0 AND "roi_x_ratio" <= 1 AND
        "roi_y_ratio" >= 0 AND "roi_y_ratio" <= 1 AND
        "roi_width_ratio" > 0 AND "roi_width_ratio" <= 1 AND
        "roi_height_ratio" > 0 AND "roi_height_ratio" <= 1
    )
);

-- CreateTable
CREATE TABLE "ignore_regions" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "template_id" TEXT NOT NULL,
    "template_page_id" TEXT NOT NULL,
    "page_number" INTEGER NOT NULL,
    "field_name" TEXT NOT NULL,
    "roi_x_ratio" REAL NOT NULL,
    "roi_y_ratio" REAL NOT NULL,
    "roi_width_ratio" REAL NOT NULL,
    "roi_height_ratio" REAL NOT NULL,
    "created_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "ignore_regions_template_id_fkey" FOREIGN KEY ("template_id") REFERENCES "templates" ("id") ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "ignore_regions_template_page_id_fkey" FOREIGN KEY ("template_page_id") REFERENCES "template_pages" ("id") ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "ignore_regions_roi_ratio_check" CHECK (
        "roi_x_ratio" >= 0 AND "roi_x_ratio" <= 1 AND
        "roi_y_ratio" >= 0 AND "roi_y_ratio" <= 1 AND
        "roi_width_ratio" > 0 AND "roi_width_ratio" <= 1 AND
        "roi_height_ratio" > 0 AND "roi_height_ratio" <= 1
    )
);

-- CreateTable
CREATE TABLE "template_requests" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "requested_by" TEXT,
    "request_title" TEXT NOT NULL,
    "document_type" TEXT,
    "sample_file_url" TEXT,
    "request_mode" TEXT NOT NULL DEFAULT 'image_only',
    "status" TEXT NOT NULL DEFAULT 'draft',
    "user_note" TEXT,
    "admin_note" TEXT,
    "converted_template_id" TEXT,
    "page_count" INTEGER NOT NULL DEFAULT 1,
    "created_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "template_requests_converted_template_id_fkey" FOREIGN KEY ("converted_template_id") REFERENCES "templates" ("id") ON DELETE SET NULL ON UPDATE CASCADE
);

-- CreateTable
CREATE TABLE "template_request_pages" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "template_request_id" TEXT NOT NULL,
    "page_number" INTEGER NOT NULL,
    "sample_image_url" TEXT,
    "created_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "template_request_pages_template_request_id_fkey" FOREIGN KEY ("template_request_id") REFERENCES "template_requests" ("id") ON DELETE CASCADE ON UPDATE CASCADE
);

-- CreateTable
CREATE TABLE "requested_fields" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "template_request_id" TEXT NOT NULL,
    "template_request_page_id" TEXT NOT NULL,
    "page_number" INTEGER NOT NULL,
    "field_name" TEXT NOT NULL,
    "display_label" TEXT NOT NULL,
    "roi_x_ratio" REAL NOT NULL,
    "roi_y_ratio" REAL NOT NULL,
    "roi_width_ratio" REAL NOT NULL,
    "roi_height_ratio" REAL NOT NULL,
    "user_note" TEXT,
    "created_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "requested_fields_template_request_id_fkey" FOREIGN KEY ("template_request_id") REFERENCES "template_requests" ("id") ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "requested_fields_template_request_page_id_fkey" FOREIGN KEY ("template_request_page_id") REFERENCES "template_request_pages" ("id") ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "requested_fields_roi_ratio_check" CHECK (
        "roi_x_ratio" >= 0 AND "roi_x_ratio" <= 1 AND
        "roi_y_ratio" >= 0 AND "roi_y_ratio" <= 1 AND
        "roi_width_ratio" > 0 AND "roi_width_ratio" <= 1 AND
        "roi_height_ratio" > 0 AND "roi_height_ratio" <= 1
    )
);

-- CreateTable
CREATE TABLE "documents" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "uploaded_by" TEXT,
    "original_file_url" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'uploaded',
    "page_count" INTEGER NOT NULL DEFAULT 1,
    "detected_template_id" TEXT,
    "confidence_score" REAL,
    "created_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "documents_detected_template_id_fkey" FOREIGN KEY ("detected_template_id") REFERENCES "templates" ("id") ON DELETE SET NULL ON UPDATE CASCADE
);

-- CreateTable
CREATE TABLE "document_pages" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "document_id" TEXT NOT NULL,
    "page_number" INTEGER NOT NULL,
    "original_image_url" TEXT,
    "normalized_image_url" TEXT,
    "status" TEXT NOT NULL DEFAULT 'uploaded',
    "detected_template_id" TEXT,
    "detected_template_page_id" TEXT,
    "confidence_score" REAL,
    "created_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "document_pages_document_id_fkey" FOREIGN KEY ("document_id") REFERENCES "documents" ("id") ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "document_pages_detected_template_id_fkey" FOREIGN KEY ("detected_template_id") REFERENCES "templates" ("id") ON DELETE SET NULL ON UPDATE CASCADE,
    CONSTRAINT "document_pages_detected_template_page_id_fkey" FOREIGN KEY ("detected_template_page_id") REFERENCES "template_pages" ("id") ON DELETE SET NULL ON UPDATE CASCADE
);

-- CreateTable
CREATE TABLE "extraction_results" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "document_id" TEXT NOT NULL,
    "document_page_id" TEXT NOT NULL,
    "page_number" INTEGER NOT NULL,
    "template_field_id" TEXT,
    "field_name" TEXT NOT NULL,
    "display_label" TEXT NOT NULL,
    "ocr_text" TEXT,
    "ocr_confidence" REAL,
    "roi_preview_url" TEXT,
    "created_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "extraction_results_document_id_fkey" FOREIGN KEY ("document_id") REFERENCES "documents" ("id") ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "extraction_results_document_page_id_fkey" FOREIGN KEY ("document_page_id") REFERENCES "document_pages" ("id") ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "extraction_results_template_field_id_fkey" FOREIGN KEY ("template_field_id") REFERENCES "template_fields" ("id") ON DELETE SET NULL ON UPDATE CASCADE
);

-- CreateTable
CREATE TABLE "detection_logs" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "document_id" TEXT NOT NULL,
    "document_page_id" TEXT NOT NULL,
    "page_number" INTEGER NOT NULL,
    "candidate_template_id" TEXT,
    "candidate_template_page_id" TEXT,
    "layout_score" REAL,
    "verification_score" REAL,
    "final_score" REAL,
    "decision" TEXT NOT NULL,
    "fail_reason" TEXT,
    "created_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "detection_logs_document_id_fkey" FOREIGN KEY ("document_id") REFERENCES "documents" ("id") ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "detection_logs_document_page_id_fkey" FOREIGN KEY ("document_page_id") REFERENCES "document_pages" ("id") ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "detection_logs_candidate_template_id_fkey" FOREIGN KEY ("candidate_template_id") REFERENCES "templates" ("id") ON DELETE SET NULL ON UPDATE CASCADE,
    CONSTRAINT "detection_logs_candidate_template_page_id_fkey" FOREIGN KEY ("candidate_template_page_id") REFERENCES "template_pages" ("id") ON DELETE SET NULL ON UPDATE CASCADE
);

-- CreateIndex
CREATE UNIQUE INDEX "template_pages_template_id_page_number_key" ON "template_pages"("template_id", "page_number");

-- CreateIndex
CREATE INDEX "template_fields_template_page_id_page_number_idx" ON "template_fields"("template_page_id", "page_number");

-- CreateIndex
CREATE INDEX "ignore_regions_template_page_id_page_number_idx" ON "ignore_regions"("template_page_id", "page_number");

-- CreateIndex
CREATE UNIQUE INDEX "template_request_pages_template_request_id_page_number_key" ON "template_request_pages"("template_request_id", "page_number");

-- CreateIndex
CREATE INDEX "requested_fields_template_request_page_id_page_number_idx" ON "requested_fields"("template_request_page_id", "page_number");

-- CreateIndex
CREATE UNIQUE INDEX "document_pages_document_id_page_number_key" ON "document_pages"("document_id", "page_number");

-- CreateIndex
CREATE INDEX "extraction_results_document_page_id_page_number_idx" ON "extraction_results"("document_page_id", "page_number");

-- CreateIndex
CREATE INDEX "detection_logs_document_page_id_page_number_idx" ON "detection_logs"("document_page_id", "page_number");
