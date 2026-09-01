# Project Database Schema V2

Schema V2 is the source of truth for a fresh PostgreSQL database. It does not
create legacy tables and does not include migration or compatibility tables.

## Core Tables

### users

Stores account identity and role.

- `id` primary key
- `email` unique
- `password_hash`
- `role`
- `created_at`, `updated_at`

### template_groups

Stores the logical document family, such as Passport or Invoice.

- `id` primary key
- `template_code` unique
- `name`
- `document_type`
- `category`
- `description`
- `created_by` references `users(id)`
- `created_at`, `updated_at`

### template_versions

Stores each usable template version under a group.

- `id` primary key
- `template_group_id` references `template_groups(id)`
- `version_number`
- `version_name`
- `status`
- `detection_mode`
- `main_page_number`
- `similarity_threshold`
- `final_confidence_threshold`
- `layout_weight`, `text_anchor_weight`, `image_anchor_weight`
- `created_from_version_id` references `template_versions(id)`
- `created_by` references `users(id)`
- `created_at`, `updated_at`, `published_at`

Constraint: unique `(template_group_id, version_number)`.

### template_pages

Stores the page images and layout signatures for a version.

- `id` primary key
- `template_version_id` references `template_versions(id)`
- `page_number`
- `page_name`
- `sample_image_url`
- `normalized_image_url`
- `layout_signature_json`
- `created_at`, `updated_at`

Constraint: unique `(template_version_id, page_number)`.

### extraction_fields

Stores ROI fields returned to the end user.

- `id` primary key
- `template_page_id` references `template_pages(id)`
- `field_name`
- `display_label`
- `data_type`: `text`, `table`, or `image`
- `extraction_method`
- ROI ratios: `roi_x_ratio`, `roi_y_ratio`, `roi_width_ratio`, `roi_height_ratio`
- `roi_mode`: `fix` or `flexible`
- `expected_content`
- `required`
- `sort_order`
- `created_at`, `updated_at`

Constraint: unique `(template_page_id, field_name)`.

### verification_anchors

Stores verification-only ROI anchors.

- `id` primary key
- `template_page_id` references `template_pages(id)`
- `anchor_name`
- `anchor_type`: `text` or `image`
- ROI ratios
- `required`
- `weight`
- `expected_text`
- `match_type`
- `regex_pattern`
- `image_category_id` references `image_verification_categories(id)`
- `sort_order`
- `created_at`, `updated_at`

Constraint: unique `(template_page_id, anchor_name)`.

### ignore_regions

Stores layout areas ignored during template matching.

- `id` primary key
- `template_page_id` references `template_pages(id)`
- `region_name`
- ROI ratios
- `reason`
- `created_at`

### version_test_cases

Stores test/reference images for a template version.

- `id` primary key
- `template_version_id` references `template_versions(id)`
- `test_name`
- `page_number`
- `image_url`
- `expected_match`
- `test_type`
- `created_at`

### publish_jobs

Stores validation, layout signature, test, and publish jobs.

- `id` primary key
- `template_version_id` references `template_versions(id)`
- `status`
- `step`
- `error_message`
- `metadata_json`
- `requested_at`, `started_at`, `completed_at`

### template_requests

Stores template requests from users or admin-created drafts.

- `id` primary key
- `requested_by` references `users(id)`
- `request_title`
- `document_type`
- `request_mode`
- `status`
- `user_note`
- `admin_note`
- `converted_template_group_id` references `template_groups(id)`
- `converted_template_version_id` references `template_versions(id)`
- `created_at`, `reviewed_at`

### template_request_pages

Stores uploaded pages for a request.

- `id` primary key
- `template_request_id` references `template_requests(id)`
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
- `template_request_page_id` references `template_request_pages(id)`
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
- `requested_by` references `users(id)`
- `template_version_id` references `template_versions(id)`
- `status`
- `request_json`
- `result_json`
- `error_message`
- `requested_at`, `started_at`, `completed_at`

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

Readable version list with template group name, version name, status, page count,
detection mode, thresholds, and matching weights.

### template_fields_view

Readable extraction field list with template name, version, page, field name,
data type, ROI mode, sort order, and status.

### verification_anchors_view

Readable verification anchor list with template name, version, page, anchor
name, anchor type, required flag, weight, image category, and status.

## Removed Legacy Tables

The V2 schema does not create these tables:

- `templates`
- `template_fields`
- `template_layout_references`
- `embedding_jobs`

Use a fresh database for V2. Existing databases are not migrated by this code.
