-- Optional Neon cleanup for legacy document persistence.
-- Run manually only after confirming the current app no longer needs /documents/* history.

DROP TABLE IF EXISTS detection_logs CASCADE;
DROP TABLE IF EXISTS extraction_results CASCADE;
DROP TABLE IF EXISTS document_pages CASCADE;
DROP TABLE IF EXISTS documents CASCADE;
