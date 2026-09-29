# Project Database Schema V2

Schema V2 is the source of truth for a fresh PostgreSQL database. It does not create legacy tables and does not include compatibility migrations for old schema names.

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

### template_pages

Stores page images and layout signatures for a version.

- `id` primary key
- `template_version_id`
- `page_number`
- `page_name`
- `sample_image_url`
- `normalized_image_url`
- `layout_signature_json`
- `created_at`, `updated_at`

Constraint: unique `(template_version_id, page_number)`.

`template_pages` is the canonical reference for published layout signatures and page image sources.

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

Constraint: unique `(template_page_id, field_name)`.

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

Constraint: unique `(template_page_id, anchor_name)`.

### ignore_regions

Stores layout areas ignored during matching.

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

Stores validation, layout signature, test, and publish jobs.

- `id` primary key
- `template_version_id`
- `status`
- `step`
- `error_message`
- `metadata_json`
- `requested_at`, `started_at`, `completed_at`

Publishing updates layout signatures on `template_pages` and refreshes the process-level Published Template Cache after commit.

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

Page images are stored in filesystem storage and referenced from the JSON payload. Do not store base64 page images in the database.

Filesystem relationship:

```text
storage/detection_queries/{queryId}/page_N.png
app/storage/processing_logs/{logId}/source_pages/page_N.png
app/storage/processing_logs/{logId}/processing_pages/processing_page_N.png
```

Processing pages are stored when the OCR/extraction coordinate space differs from the source page.

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

Use a fresh database for V2. Existing databases are not migrated by this code.
