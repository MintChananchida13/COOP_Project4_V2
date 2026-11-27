"""Single Prompt V2.3 policy transcribed from siglip_single_v2_3.py."""

import math


SINGLE = {
    'emblem': 'A company logo or official institutional emblem, featuring text, symbols, or decorative designs.',
    'thai_garuda': 'A Garuda figure with a human body, bird head, and wings.',
    'portrait': 'An ID-style portrait photograph showing a persons face and upper body, possibly with numbers or measurement lines in the background.',
    'signature': 'A handwritten signature.',
    'barcode': 'A barcode with parallel vertical black bars.',
    'qr_code': 'A square QR code with a black and white matrix pattern.',
}
NEGATIVE = {
    'text': 'Printed or handwritten text, letters, dates, and numbers.',
    'table': 'A table with rows and columns.',
    'blank': 'A blank document area.',
    'background': 'A document background or watermark.',
    'illustration': 'A drawing, diagram, or illustrative photograph.',
    'bar_chart': 'A statistical bar chart.',
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
