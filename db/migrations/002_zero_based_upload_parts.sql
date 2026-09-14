ALTER TABLE upload_tasks
  DROP CHECK ck_upload_total_parts,
  ADD CONSTRAINT ck_upload_total_parts CHECK (total_parts <= 1000000);

ALTER TABLE upload_parts
  DROP CHECK ck_upload_part_number,
  ADD CONSTRAINT ck_upload_part_number CHECK (part_number < 1000000);

INSERT INTO schema_migrations(version, name)
VALUES (2, 'zero_based_upload_parts');
