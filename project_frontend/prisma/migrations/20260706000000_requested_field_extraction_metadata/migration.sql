ALTER TABLE "requested_fields" ADD COLUMN "data_type" TEXT DEFAULT 'text';
ALTER TABLE "requested_fields" ADD COLUMN "extraction_method" TEXT NOT NULL DEFAULT 'ocr_text';
