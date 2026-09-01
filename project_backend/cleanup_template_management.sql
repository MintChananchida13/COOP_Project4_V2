DROP TABLE IF EXISTS template_rois CASCADE;
DROP TABLE IF EXISTS document_templates CASCADE;
DROP TABLE IF EXISTS templates CASCADE;
DROP TABLE IF EXISTS verification_fields CASCADE;
DROP TABLE IF EXISTS extraction_fields CASCADE;
DROP TABLE IF EXISTS keywords CASCADE;
DROP TABLE IF EXISTS roi_definitions CASCADE;
DROP TABLE IF EXISTS template_embeddings CASCADE;
DROP TABLE IF EXISTS template_requests CASCADE;
DROP TABLE IF EXISTS template_reviews CASCADE;
DROP TABLE IF EXISTS template_configurations CASCADE;

DO $$
DECLARE
  seq record;
BEGIN
  FOR seq IN
    SELECT sequence_schema, sequence_name
    FROM information_schema.sequences
    WHERE sequence_name IN (
      'template_rois_id_seq',
      'document_templates_id_seq',
      'templates_id_seq',
      'verification_fields_id_seq',
      'extraction_fields_id_seq',
      'keywords_id_seq',
      'roi_definitions_id_seq',
      'template_embeddings_id_seq',
      'template_requests_id_seq',
      'template_reviews_id_seq',
      'template_configurations_id_seq'
    )
  LOOP
    EXECUTE format('ALTER SEQUENCE %I.%I RESTART WITH 1', seq.sequence_schema, seq.sequence_name);
  END LOOP;
END $$;
