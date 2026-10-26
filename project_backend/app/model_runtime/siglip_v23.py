"""Single Prompt V2.3 policy transcribed from siglip_single_v2_3.py."""

import math


SINGLE = {
    "emblem": "This is an institutional crest, university seal, or organization insignia, not the standalone Thai national Garuda emblem.",
    "thai_garuda": "This is the standalone official Thai national Garuda (Phra Khrut) emblem, showing a winged human-bird deity, not a university or agency crest.",
    "company_logo": "This is a commercial brand identity logo, company trademark, or business wordmark.",
    "document_stamp": "An inked rubber stamp impression applied to a paper document.",
    "portrait": "A portrait photograph of a real person.",
    "signature": "A handwritten signature made with a pen.",
    "barcode": "A linear barcode made of parallel vertical bars.",
    "qr_code": "A square QR code with a black and white matrix pattern.",
}
NEGATIVE = {
    "printed_text": "Ordinary printed text on a document without a logo or emblem.",
    "numbers": "A cropped document region containing printed numbers and digits.",
    "table": "A table with rows, columns, and printed text.",
    "blank": "An empty or nearly blank document region.",
    "background": "A decorative document security background without a distinct emblem.",
    "illustration": "A generic illustration, icon, or decorative graphic rather than an official emblem or brand logo.",
    "pattern": "A repetitive black and white texture or stripes, not a scannable barcode.",
    "bar_chart": "A bar chart showing statistical values with colored rectangular bars, axes, tick labels, and a legend.",
}
LABELS = {
    "emblem": "ตราสถาบัน / องค์กร", "thai_garuda": "ตราครุฑ",
    "company_logo": "โลโก้บริษัท", "document_stamp": "ตราประทับ",
    "portrait": "รูปถ่ายบุคคล", "signature": "ลายเซ็น",
    "barcode": "บาร์โค้ด", "qr_code": "QR Code",
}
SCORING_VERSION = "siglip-single-prompt-v2.3"


def runtime_categories():
    return [{"value": name, "prompt": prompt, "enabled": True}
            for name, prompt in {**SINGLE, **NEGATIVE}.items()]


def classify(logits):
    names = list(SINGLE) + list(NEGATIVE)
    if len(logits) != len(names):
        raise ValueError(f"V2.3 requires {len(names)} raw logits; received {len(logits)}")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in logits):
        raise ValueError("V2.3 requires finite numeric raw logits")
    scores = dict(zip(names, logits))
    top = max(names, key=scores.get)
    baseline = top if top in SINGLE else "other"
    positive = max(SINGLE, key=scores.get)
    negative = max(NEGATIVE, key=scores.get)
    if positive == "thai_garuda" and scores["thai_garuda"] - scores["emblem"] < 2.0:
        if scores["emblem"] >= scores[negative]:
            return "emblem", "garuda_emblem_ambiguity"
    if positive == "thai_garuda" and scores[positive] - scores[negative] < 2.0:
        return "other", "weak_garuda_vs_negative"
    if positive == "emblem" and scores["emblem"] < -6.3:
        return "other", "weak_emblem_score"
    return baseline, "unchanged"
