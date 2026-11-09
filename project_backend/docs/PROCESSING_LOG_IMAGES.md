# Processing Log images in PostgreSQL

Source review: 2026-11-10. PostgreSQL image persistence, preview/backfill and migration paths checked against current source. No database migration, image backfill or restore was executed in this documentation review.

Processing Log images are now stored as Base64 in `processing_log_images`.
Template images, login, OCR, Template matching, ROI coordinates and Ground Truth
behavior are unchanged. No Object Storage SDK, bucket or storage credentials are required.

## Data and response boundaries

- Primary key: `(log_id, page_number, kind)`; kind is `source` or `processing`.
- Image fields: `mime_type`, `image_base64`, creation/update timestamps.
- `log_id` references `processing_logs` with `ON DELETE CASCADE`.
- Original and aligned/processing images remain separate so ROI overlays use the
  same image/coordinate space as before.
- PostgreSQL stores images separately from `source_pages_json`. Log lists/details
  still return only metadata and the existing preview URLs, never the image Base64.
- `GET /admin/processing-logs/{id}/pages/{page}?kind=source|processing` reads and
  decodes only the requested image and returns its original bytes and MIME type.
- Log snapshot and image writes share a transaction. A failed image save rolls back
  the snapshot rather than marking a missing image as successfully persisted.
- Saving edits without new page files retains the existing image and original OCR.
- Equal image content does not rewrite the image row on conflict.

`sourceReference`, `storageReference` and `processingReference` retain the old
relative-reference format for compatibility. For new logs they are logical references;
the corresponding local file does not need to exist.

## Deploy

Deploy the Backend changes and restart it. The existing database bootstrap creates
`processing_log_images` with `CREATE TABLE IF NOT EXISTS`; it does not recreate or
delete `processing_logs` or Template data. The database role needs its usual schema
creation privileges. No new Python dependencies or Frontend environment variables
are required. Deploying code alone does not reconstruct already-lost image bytes.

## Existing local logs

The Preview endpoint first reads PostgreSQL. If the image is not there, it validates
the old local reference, reads the file and saves it in PostgreSQL before returning
it. Original local files are retained. Once backfilled, preview works even if the
local copy disappears.

For bulk migration, run on the machine that still has the old image files:

```powershell
cd project_backend
python scripts/migrate_processing_log_images.py
python scripts/migrate_processing_log_images.py --log-id proc_log_example
python scripts/migrate_processing_log_images.py --apply
```

The default is a dry run of image writes (database bootstrap still ensures the
required schema). `--apply` saves available images, keeps local originals, skips
already-stored page/kind pairs and reports missing page/kind pairs. Run it before
replacing an ephemeral filesystem if preserving all old images is required.

An image already missing from both PostgreSQL and disk still returns 404. It can
only be restored from an original/backup or by resaving the same Log with its
`pageFiles`; a stale persisted metadata flag no longer prevents that repair.

## Validation

`python -m unittest tests.test_processing_log_images` covers image byte equality,
source/processing separation, multi-page mapping, no image loading in list/detail,
edit preservation, transactional rollback, cascading delete, legacy read/backfill,
missing-file repair, invalid paths and dry-run/apply migration.

Tests use isolated SQLite fixtures only; the application's database remains PostgreSQL.
No live document, Model API or external storage requests are needed by these tests.

The prior experimental Neon bucket was not deleted by this change. The application
does not use it or its credentials. Infrastructure deletion is a separate operation.
