# Project Database Schema V2

Source review: 2026-11-10 against this checkout. Documentation review only; no live database, model or deployment validation was performed.

The executable source is `app/core/db.py` (`_POSTGRES_SCHEMA`) plus service ensure logic. The notes below describe the current runtime; the SQL appendix is extracted from that source without importing the app or connecting to a DB. Old Frontend Prisma migrations are not the runtime migration authority. Bootstrap includes incremental ALTER/backfill statements for V2, but does not migrate arbitrary legacy table names.

## Core Tables

### users

Stores account identity and role.

- `id` primary key
- `email` unique
- `password_hash`
- `role`
- `created_at`, `updated_at`

### app_settings

Stores global system settings as key/value rows.

Important keys:

- `verification_strategy`
- `pending_verification_strategy`
- `detection_batch_processing_mode`
- `pending_detection_batch_processing_mode`
- `ocr_model_settings`
- `pending_ocr_model_settings`
- `system_maintenance`
- `pending_template_changes`

`system_maintenance` is backend source of truth for maintenance enforcement. Pending settings are applied when maintenance start time is reached and are cleared only after successful apply.

### template_groups

Stores the logical document family.

- `id` primary key
- `template_code` unique
- `name`
- `document_type`
- `category`
- `description`
- `created_by`
- `created_at`, `updated_at`

### template_versions

Stores each template version under a group.

- `id` primary key
- `template_group_id`
- `version_number`
- `version_name`
- `status`
- `detection_mode`: `all_pages` or `main_page`
- `main_page_number`
- `similarity_threshold`
- `final_confidence_threshold`
- `layout_weight`, `text_anchor_weight`, `image_anchor_weight`
- `created_from_version_id`
- `created_by`
- `created_at`, `updated_at`, `published_at`

Constraint: unique `(template_group_id, version_number)`.

Current behavior:

- `all_pages` uses page-count gate and page-number matching.
- `main_page` uses one configured page number. Backend supports any page number >= 1, while current Admin UI presents this as page 1.
- Matching uses all in-scope Anchors, staged scheduling, Anchor-count ranking and template ID tie-breaking. Stored similarity/final-confidence/weight fields remain for compatibility; they do not select the current winner.

### template_pages

Stores page images, persisted geometry and optional legacy layout signatures for a version.

- `id` primary key
- `template_version_id`
- `page_number`
- `page_name`
- `sample_image_url`
- `normalized_image_url`
- `image_width`, `image_height`, `image_aspect_ratio`, `image_geometry_checked`
- `layout_signature_json`
- `created_at`, `updated_at`

Constraint: unique `(template_version_id, page_number)`.

`template_pages` supplies page image references and cached geometry. Geometry is measured on image save/replacement and backfilled at startup; unreadable images can retain null dimensions. Current matching/publish does not require layout signatures.

### extraction_fields

Stores ROI fields returned to the end user.

- `id` primary key
- `template_page_id`
- `field_name`
- `display_label`
- `data_type`: `text`, `table`, or `image`
- `extraction_method`
- ROI ratios: `roi_x_ratio`, `roi_y_ratio`, `roi_width_ratio`, `roi_height_ratio`
- optional ROI points JSON
- `roi_mode`: `fix` or `flexible`
- `expected_content`
- `required`
- `sort_order`
- `created_at`, `updated_at`

Field names are not unique per page in the current DDL; bootstrap drops the old name constraint. Use IDs for identity.

### verification_anchors

Stores verification-only ROI anchors.

- `id` primary key
- `template_page_id`
- `anchor_name`
- `anchor_type`: `text` or `image`
- ROI ratios and optional points
- `required`
- `weight`
- `expected_text`
- `match_type`
- `regex_pattern`
- `image_category_id`
- `sort_order`
- `created_at`, `updated_at`

Anchor names are not unique per page in the current DDL; bootstrap drops the old name constraint. `match_type`/`regex_pattern` are nullable legacy archives, not runtime text rules. All in-scope Anchors are required by current matching irrespective of stored `required`/`weight`.

### ignore_regions

Stores persisted ignore-region geometry. Retained layout/signature utilities can consume it; do not assume the current Anchor pipeline performs layout retrieval from this table.

- `id` primary key
- `template_page_id`
- `region_name`
- ROI ratios
- `reason`
- `created_at`

### version_test_cases

Stores test/reference images for a template version.

- `id` primary key
- `template_version_id`
- `test_name`
- `page_number`
- `image_url`
- `expected_match`
- `test_type`
- `created_at`

### publish_jobs

Stores validation and publish job state/metadata.

- `id` primary key
- `template_version_id`
- `status`
- `step`
- `error_message`
- `metadata_json`
- `requested_at`, `started_at`, `completed_at`

Publishing rechecks stored page/Anchor readiness, respects Maintenance pending activation and refreshes Published Template Cache. It does not generate Layout Signatures. Detection/prepublish test polling dictionaries are process-local and are not made durable by this table.

### template_requests

Stores template requests from users or admin-created drafts.

- `id` primary key
- `requested_by`
- `request_title`
- `document_type`
- `request_mode`
- `status`
- `user_note`
- `admin_note`
- `converted_template_group_id`
- `converted_template_version_id`
- `created_at`, `reviewed_at`

### template_request_pages

Stores uploaded pages for a request.

- `id` primary key
- `template_request_id`
- `page_number`
- `page_name`
- `sample_image_url`
- `source_file_id`
- `source_file_name`
- `image_source`
- `review_status`
- `is_canonical`
- `layout_signature_json`
- `created_at`, `updated_at`

Constraint: unique `(template_request_id, page_number)`.

### requested_fields

Stores user-proposed ROI fields for a request page.

- `id` primary key
- `template_request_page_id`
- `field_name`
- `display_label`
- `data_type`
- `extraction_method`
- ROI ratios
- `user_note`
- `created_at`

### ocr_jobs

Stores asynchronous OCR processing jobs.

- `id` primary key
- `requested_by`
- `template_version_id`
- `status`
- `request_json`
- `result_json`
- `error_message`
- `requested_at`, `started_at`, `completed_at`

### processing_logs

Stores user processing snapshots for Admin review.

Typical persisted groups:

- general metadata
- template detection JSON
- matched template JSON
- source pages JSON
- ROI snapshot JSON
- OCR original JSON
- Ground Truth JSON
- processing result summary
- timing metadata

Page images are stored as Base64/MIME rows in `processing_log_images`, keyed by
`(log_id, page_number, kind)` with kind `source` or `processing`. The foreign key
cascades on log deletion. Image writes and snapshots share a transaction.
List/detail JSON contains metadata and preview URLs, not image Base64.

Preview reads one image from PostgreSQL. Legacy local references can be validated,
read and backfilled without deleting originals. Detection images still use
`storage/detection_queries/{queryId}/page_N.png`; legacy log files may exist under
`app/storage/processing_logs/{logId}/...`. New references can be logical, without
a corresponding local file. See [Processing Log images](PROCESSING_LOG_IMAGES.md).

### notifications

Stores audience, event type, title/message, optional reason/related entity,
metadata JSON and creation time. The default audience is `user`.

### image_verification_categories

Stores image verification labels and prompts.

- `id` primary key
- `value` unique
- `label`
- `prompt`
- `match_threshold`
- `margin_threshold`
- `evidence_temperature`
- `enabled`
- `created_at`, `updated_at`

## Developer Views

### template_versions_view

Readable version list with template group name, version name, status, page count, detection mode, thresholds, and matching weights.

### template_fields_view

Readable extraction field list with template name, version, page, field name, data type, ROI mode, sort order, and status.

### verification_anchors_view

Readable verification anchor list with template name, version, page, anchor name, anchor type, required flag, weight, image category, and status.

## Runtime Caches

### Processing Log images

`processing_log_images` stores image Base64 and MIME type separately from Log
metadata, keyed by `(log_id, page_number, kind)`, where kind is `source` or
`processing`. The foreign key to `processing_logs` cascades on deletion. Log JSON
responses contain Preview URLs only; the existing image endpoint reads one image
at a time. Startup creates this additional table without deleting existing data.
See `PROCESSING_LOG_IMAGES.md` for compatibility and legacy-file migration.

The database remains source of truth, but current runtime uses:

- PostgreSQL `ThreadedConnectionPool` with age/idle recycling
- process-level Published Template Cache for active templates
- process-level verification strategy cache
- request-scoped detection cache for template metadata, field count, and verification fields

Cache refresh/removal is tied to Admin publish/update/unpublish/delete paths and maintenance pending apply.

## Removed Legacy Tables

The V2 schema does not create these tables:

- `templates`
- `template_fields`
- `template_layout_references`
- `embedding_jobs`

Use a fresh database when moving from incompatible legacy table names, or design and validate an explicit migration. Existing V2 databases do receive the ALTER/data updates in bootstrap; review these before deployment. Never run Prisma reset/db push against a live runtime database without a schema migration plan.

## Executable schema snapshot (2026-11-10)

Extracted in source order from `_POSTGRES_SCHEMA`. This is reference SQL, not a recommendation to execute it manually against production. CREATE IF NOT EXISTS alone does not reconcile arbitrary existing column definitions. Service initialization also seeds/normalizes image categories and users; review that code separately.

### Tables

```sql
CREATE TABLE IF NOT EXISTS users (
    id TEXT NOT NULL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT NOT NULL PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS image_verification_categories (
    id TEXT NOT NULL PRIMARY KEY,
    value TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    prompt TEXT NOT NULL,
    match_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.70,
    margin_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.05,
    evidence_temperature DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS template_groups (
    id TEXT NOT NULL PRIMARY KEY,
    template_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    document_type TEXT,
    category TEXT,
    description TEXT,
    created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS template_versions (
    id TEXT NOT NULL PRIMARY KEY,
    template_group_id TEXT NOT NULL REFERENCES template_groups(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    version_name TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    detection_mode TEXT NOT NULL DEFAULT 'all_pages',
    main_page_number INTEGER NOT NULL DEFAULT 1,
    similarity_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.50,
    final_confidence_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.75,
    layout_weight DOUBLE PRECISION NOT NULL DEFAULT 0.50,
    text_anchor_weight DOUBLE PRECISION NOT NULL DEFAULT 0.35,
    image_anchor_weight DOUBLE PRECISION NOT NULL DEFAULT 0.15,
    created_from_version_id TEXT REFERENCES template_versions(id) ON DELETE SET NULL,
    created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMPTZ,
    CONSTRAINT template_versions_group_version_key UNIQUE (template_group_id, version_number)
);

CREATE TABLE IF NOT EXISTS template_pages (
    id TEXT NOT NULL PRIMARY KEY,
    template_version_id TEXT NOT NULL REFERENCES template_versions(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    page_name TEXT,
    sample_image_url TEXT,
    normalized_image_url TEXT,
    image_width INTEGER,
    image_height INTEGER,
    image_aspect_ratio DOUBLE PRECISION,
    image_geometry_checked BOOLEAN NOT NULL DEFAULT FALSE,
    layout_signature_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT template_pages_version_page_key UNIQUE (template_version_id, page_number)
);

CREATE TABLE IF NOT EXISTS extraction_fields (
    id TEXT NOT NULL PRIMARY KEY,
    template_page_id TEXT NOT NULL REFERENCES template_pages(id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    display_label TEXT NOT NULL,
    data_type TEXT NOT NULL DEFAULT 'text',
    extraction_method TEXT NOT NULL DEFAULT 'fixed_roi',
    roi_x_ratio DOUBLE PRECISION NOT NULL,
    roi_y_ratio DOUBLE PRECISION NOT NULL,
    roi_width_ratio DOUBLE PRECISION NOT NULL,
    roi_height_ratio DOUBLE PRECISION NOT NULL,
    roi_points_json JSONB,
    roi_mode TEXT NOT NULL DEFAULT 'fix',
    expected_content TEXT,
    required BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT extraction_fields_data_type_check CHECK (data_type IN ('text', 'table', 'image')),
    CONSTRAINT extraction_fields_roi_mode_check CHECK (roi_mode IN ('fix', 'flexible')),
    CONSTRAINT extraction_fields_roi_ratio_check CHECK (
        roi_x_ratio >= 0 AND roi_x_ratio <= 1 AND
        roi_y_ratio >= 0 AND roi_y_ratio <= 1 AND
        roi_width_ratio > 0 AND roi_width_ratio <= 1 AND
        roi_height_ratio > 0 AND roi_height_ratio <= 1
    )
);

CREATE TABLE IF NOT EXISTS verification_anchors (
    id TEXT NOT NULL PRIMARY KEY,
    template_page_id TEXT NOT NULL REFERENCES template_pages(id) ON DELETE CASCADE,
    anchor_name TEXT NOT NULL,
    anchor_type TEXT NOT NULL DEFAULT 'text',
    roi_x_ratio DOUBLE PRECISION NOT NULL,
    roi_y_ratio DOUBLE PRECISION NOT NULL,
    roi_width_ratio DOUBLE PRECISION NOT NULL,
    roi_height_ratio DOUBLE PRECISION NOT NULL,
    roi_points_json JSONB,
    required BOOLEAN NOT NULL DEFAULT FALSE,
    weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    expected_text TEXT,
    -- Legacy archive only; text verification always uses normalized Levenshtein.
    match_type TEXT,
    regex_pattern TEXT,
    image_category_id TEXT REFERENCES image_verification_categories(id) ON DELETE SET NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT verification_anchors_type_check CHECK (anchor_type IN ('text', 'image')),
    CONSTRAINT verification_anchors_roi_ratio_check CHECK (
        roi_x_ratio >= 0 AND roi_x_ratio <= 1 AND
        roi_y_ratio >= 0 AND roi_y_ratio <= 1 AND
        roi_width_ratio > 0 AND roi_width_ratio <= 1 AND
        roi_height_ratio > 0 AND roi_height_ratio <= 1
    )
);

CREATE TABLE IF NOT EXISTS ignore_regions (
    id TEXT NOT NULL PRIMARY KEY,
    template_page_id TEXT NOT NULL REFERENCES template_pages(id) ON DELETE CASCADE,
    region_name TEXT NOT NULL,
    roi_x_ratio DOUBLE PRECISION NOT NULL,
    roi_y_ratio DOUBLE PRECISION NOT NULL,
    roi_width_ratio DOUBLE PRECISION NOT NULL,
    roi_height_ratio DOUBLE PRECISION NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ignore_regions_roi_ratio_check CHECK (
        roi_x_ratio >= 0 AND roi_x_ratio <= 1 AND
        roi_y_ratio >= 0 AND roi_y_ratio <= 1 AND
        roi_width_ratio > 0 AND roi_width_ratio <= 1 AND
        roi_height_ratio > 0 AND roi_height_ratio <= 1
    )
);

CREATE TABLE IF NOT EXISTS template_requests (
    id TEXT NOT NULL PRIMARY KEY,
    requested_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    request_title TEXT NOT NULL,
    document_type TEXT,
    request_mode TEXT NOT NULL DEFAULT 'image_only',
    status TEXT NOT NULL DEFAULT 'draft',
    user_note TEXT,
    admin_note TEXT,
    converted_template_group_id TEXT REFERENCES template_groups(id) ON DELETE SET NULL,
    converted_template_version_id TEXT REFERENCES template_versions(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS template_request_pages (
    id TEXT NOT NULL PRIMARY KEY,
    template_request_id TEXT NOT NULL REFERENCES template_requests(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    page_name TEXT,
    sample_image_url TEXT,
    source_file_id TEXT,
    source_file_name TEXT,
    image_source TEXT NOT NULL DEFAULT 'user_request',
    review_status TEXT NOT NULL DEFAULT 'pending',
    is_canonical BOOLEAN NOT NULL DEFAULT FALSE,
    layout_signature_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT template_request_pages_request_page_key UNIQUE (template_request_id, page_number)
);

CREATE TABLE IF NOT EXISTS version_test_cases (
    id TEXT NOT NULL PRIMARY KEY,
    template_version_id TEXT NOT NULL REFERENCES template_versions(id) ON DELETE CASCADE,
    test_name TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    image_url TEXT NOT NULL,
    expected_match BOOLEAN NOT NULL DEFAULT TRUE,
    test_type TEXT NOT NULL DEFAULT 'pre_publish',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS requested_fields (
    id TEXT NOT NULL PRIMARY KEY,
    template_request_page_id TEXT NOT NULL REFERENCES template_request_pages(id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    display_label TEXT NOT NULL,
    data_type TEXT NOT NULL DEFAULT 'text',
    extraction_method TEXT NOT NULL DEFAULT 'ocr_text',
    roi_x_ratio DOUBLE PRECISION NOT NULL,
    roi_y_ratio DOUBLE PRECISION NOT NULL,
    roi_width_ratio DOUBLE PRECISION NOT NULL,
    roi_height_ratio DOUBLE PRECISION NOT NULL,
    roi_points_json JSONB,
    user_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT requested_fields_roi_ratio_check CHECK (
        roi_x_ratio >= 0 AND roi_x_ratio <= 1 AND
        roi_y_ratio >= 0 AND roi_y_ratio <= 1 AND
        roi_width_ratio > 0 AND roi_width_ratio <= 1 AND
        roi_height_ratio > 0 AND roi_height_ratio <= 1
    )
);

CREATE TABLE IF NOT EXISTS publish_jobs (
    id TEXT NOT NULL PRIMARY KEY,
    template_version_id TEXT NOT NULL REFERENCES template_versions(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued',
    step TEXT NOT NULL DEFAULT 'validation',
    error_message TEXT,
    metadata_json JSONB,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS ocr_jobs (
    id TEXT NOT NULL PRIMARY KEY,
    requested_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    template_version_id TEXT REFERENCES template_versions(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    request_json JSONB NOT NULL,
    result_json JSONB,
    error_message TEXT,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS processing_logs (
    id TEXT NOT NULL PRIMARY KEY,
    processing_run_id TEXT NOT NULL UNIQUE,
    document_name TEXT,
    user_email TEXT,
    source_file_id TEXT,
    source_file_name TEXT,
    status TEXT NOT NULL DEFAULT 'completed',
    current_step TEXT,
    page_count INTEGER NOT NULL DEFAULT 0,
    source_pages_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    template_detection_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    matched_template_json JSONB,
    roi_snapshot_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    ocr_original_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    ground_truth_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS processing_log_images (
    log_id TEXT NOT NULL REFERENCES processing_logs(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL CHECK (page_number > 0),
    kind TEXT NOT NULL CHECK (kind IN ('source', 'processing')),
    mime_type TEXT NOT NULL,
    image_base64 TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (log_id, page_number, kind)
);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT NOT NULL PRIMARY KEY,
    audience TEXT NOT NULL DEFAULT 'user',
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    reason TEXT,
    related_entity_type TEXT,
    related_entity_id TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### Incremental changes and data updates

```sql
ALTER TABLE template_pages ADD COLUMN IF NOT EXISTS image_width INTEGER;

ALTER TABLE template_pages ADD COLUMN IF NOT EXISTS image_height INTEGER;

ALTER TABLE template_pages ADD COLUMN IF NOT EXISTS image_aspect_ratio DOUBLE PRECISION;

ALTER TABLE template_pages ADD COLUMN IF NOT EXISTS image_geometry_checked BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE template_request_pages ADD COLUMN IF NOT EXISTS source_file_id TEXT;

ALTER TABLE template_request_pages ADD COLUMN IF NOT EXISTS image_source TEXT NOT NULL DEFAULT 'user_request';

ALTER TABLE template_request_pages ADD COLUMN IF NOT EXISTS is_canonical BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE template_request_pages ADD COLUMN IF NOT EXISTS layout_signature_json JSONB;

ALTER TABLE template_request_pages ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE template_versions ALTER COLUMN similarity_threshold SET DEFAULT 0.50;

UPDATE template_versions SET similarity_threshold = 0.50 WHERE similarity_threshold IN (0.75, 0.72);

ALTER TABLE extraction_fields ADD COLUMN IF NOT EXISTS roi_points_json JSONB;

ALTER TABLE extraction_fields DROP CONSTRAINT IF EXISTS extraction_fields_page_field_name_key;

ALTER TABLE verification_anchors ADD COLUMN IF NOT EXISTS roi_points_json JSONB;

ALTER TABLE verification_anchors DROP CONSTRAINT IF EXISTS verification_anchors_page_anchor_name_key;

ALTER TABLE requested_fields ADD COLUMN IF NOT EXISTS roi_points_json JSONB;

ALTER TABLE requested_fields DROP CONSTRAINT IF EXISTS requested_fields_page_field_name_key;
```

### Indexes

```sql
CREATE INDEX IF NOT EXISTS template_groups_document_type_idx ON template_groups(document_type);

CREATE INDEX IF NOT EXISTS template_versions_group_status_idx ON template_versions(template_group_id, status);

CREATE INDEX IF NOT EXISTS template_versions_status_updated_at_idx ON template_versions(status, updated_at);

CREATE INDEX IF NOT EXISTS template_pages_version_page_idx ON template_pages(template_version_id, page_number);

CREATE INDEX IF NOT EXISTS extraction_fields_template_page_id_sort_order_idx ON extraction_fields(template_page_id, sort_order);

CREATE INDEX IF NOT EXISTS verification_anchors_template_page_id_sort_order_idx ON verification_anchors(template_page_id, sort_order);

CREATE INDEX IF NOT EXISTS ignore_regions_template_page_id_idx ON ignore_regions(template_page_id);

CREATE INDEX IF NOT EXISTS template_requests_status_created_at_idx ON template_requests(status, created_at);

CREATE INDEX IF NOT EXISTS template_request_pages_request_page_idx ON template_request_pages(template_request_id, page_number);

CREATE INDEX IF NOT EXISTS requested_fields_template_request_page_id_idx ON requested_fields(template_request_page_id);

CREATE INDEX IF NOT EXISTS version_test_cases_template_version_id_idx ON version_test_cases(template_version_id);

CREATE INDEX IF NOT EXISTS publish_jobs_template_version_step_idx ON publish_jobs(template_version_id, step, status);

CREATE INDEX IF NOT EXISTS ocr_jobs_status_requested_at_idx ON ocr_jobs(status, requested_at);

CREATE INDEX IF NOT EXISTS ocr_jobs_template_version_id_idx ON ocr_jobs(template_version_id);

CREATE INDEX IF NOT EXISTS processing_logs_updated_at_idx ON processing_logs(updated_at);

CREATE INDEX IF NOT EXISTS processing_logs_user_email_updated_at_idx ON processing_logs(user_email, updated_at);

CREATE INDEX IF NOT EXISTS notifications_audience_created_at_idx ON notifications(audience, created_at);
```

### Developer views

```sql
CREATE OR REPLACE VIEW template_versions_view AS
SELECT
    tv.id AS template_version_id,
    tg.id AS template_group_id,
    tg.name AS template_name,
    tg.template_code,
    tg.document_type,
    tg.category,
    tv.version_number,
    tv.version_name,
    tv.status,
    tv.detection_mode,
    tv.main_page_number,
    tv.final_confidence_threshold,
    tv.similarity_threshold,
    tv.layout_weight,
    tv.text_anchor_weight,
    tv.image_anchor_weight,
    tv.published_at,
    tv.created_at,
    tv.updated_at
FROM template_versions tv
JOIN template_groups tg ON tg.id = tv.template_group_id;

CREATE OR REPLACE VIEW template_fields_view AS
SELECT
    tv.id AS template_version_id,
    tg.name AS template_name,
    tv.version_number,
    tv.version_name,
    tv.status,
    tp.id AS template_page_id,
    tp.page_number,
    tp.page_name,
    ef.id AS field_id,
    ef.field_name,
    ef.display_label,
    ef.data_type,
    ef.extraction_method,
    ef.roi_mode,
    ef.required,
    ef.sort_order,
    ef.created_at,
    ef.updated_at
FROM extraction_fields ef
JOIN template_pages tp ON tp.id = ef.template_page_id
JOIN template_versions tv ON tv.id = tp.template_version_id
JOIN template_groups tg ON tg.id = tv.template_group_id;

CREATE OR REPLACE VIEW verification_anchors_view AS
SELECT
    tv.id AS template_version_id,
    tg.name AS template_name,
    tv.version_number,
    tv.version_name,
    tv.status,
    tp.id AS template_page_id,
    tp.page_number,
    tp.page_name,
    va.id AS anchor_id,
    va.anchor_name,
    va.anchor_type,
    va.required,
    va.weight,
    va.sort_order,
    va.created_at,
    va.updated_at,
    va.image_category_id,
    ivc.value AS image_category_value,
    ivc.label AS image_category_label
FROM verification_anchors va
JOIN template_pages tp ON tp.id = va.template_page_id
JOIN template_versions tv ON tv.id = tp.template_version_id
JOIN template_groups tg ON tg.id = tv.template_group_id
LEFT JOIN image_verification_categories ivc ON ivc.id = va.image_category_id;
```
