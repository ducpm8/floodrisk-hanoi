-- FIR-73: seed aliases for landmarks blocking ward resolution
SET client_encoding TO 'UTF8';

INSERT INTO location_aliases (alias_norm, alias_vi, ward_name, alias_type, confidence, source)
VALUES
  ('giap bat', 'Giáp Bát', 'phuong giap bat', 'landmark', 0.95, 'manual'),
  ('ben xe giap bat', 'Bến xe Giáp Bát', 'phuong giap bat', 'landmark', 0.95, 'manual'),
  ('cao toc ha noi  thai nguyen', 'Cao tốc Hà Nội – Thái Nguyên', 'thi tran dong anh', 'other', 0.7, 'manual')
ON CONFLICT (alias_norm) DO NOTHING;
