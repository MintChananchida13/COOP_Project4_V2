CREATE TABLE IF NOT EXISTS "image_verification_categories" (
    "value" TEXT NOT NULL PRIMARY KEY,
    "label" TEXT NOT NULL,
    "prompt" TEXT NOT NULL,
    "match_threshold" REAL NOT NULL DEFAULT 0.70,
    "margin_threshold" REAL NOT NULL DEFAULT 0.05,
    "evidence_temperature" REAL NOT NULL DEFAULT 1.0,
    "enabled" BOOLEAN NOT NULL DEFAULT TRUE,
    "created_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO "image_verification_categories" (
    "value", "label", "prompt", "match_threshold", "margin_threshold",
    "evidence_temperature", "enabled"
)
VALUES
    ('company_logo', 'โลโก้บริษัท', 'company logo', 0.50, 0.05, 1.0, TRUE),
    ('official_stamp', 'ตราประทับ', 'official stamp', 0.50, 0.05, 1.0, TRUE),
    ('signature', 'ลายเซ็น', 'handwritten signature', 0.45, 0.04, 1.0, TRUE),
    ('qr_code', 'QR Code', 'QR code', 0.55, 0.05, 1.0, TRUE),
    ('barcode', 'บาร์โค้ด', 'barcode', 0.55, 0.05, 1.0, TRUE),
    ('portrait', 'รูปถ่ายบุคคล', 'portrait photograph', 0.45, 0.04, 1.0, TRUE),
    ('government_emblem', 'ตราครุฑ', 'government emblem', 0.40, 0.03, 1.0, TRUE),
    ('thailand_symbol', 'สัญลักษณ์ประเทศไทย', 'symbol of Thailand', 0.40, 0.03, 1.0, TRUE)
ON CONFLICT("value") DO NOTHING;
