"""SigLIP document image classification: Single Prompt V2.3 (V2.1 prompts + bar chart negative only). No accuracy guarantee.

Install: pip install torch transformers==4.48.0 pillow pandas sentencepiece
Run: python siglip_single_v2_3.py --input ./input --output ./results_v2_3.csv
Optional labeled CSV: --truth ./ground_truth.csv (columns: File,Ground Truth)
Thai Garuda ground truth includes Thai_Garuda_emblem.png and screenshot.
"""
import argparse
from pathlib import Path

import pandas as pd
import torch
from PIL import Image
from transformers import AutoModel, AutoProcessor

MODEL_NAME = 'google/siglip-so400m-patch14-384'
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.svg'}

# Experimental prompts for the agreed eight categories; actual backend DB prompts may differ.
SINGLE = {
    'emblem': 'This is an institutional crest, university seal, or organization insignia, not the standalone Thai national Garuda emblem.',
    'thai_garuda': 'This is the standalone official Thai national Garuda (Phra Khrut) emblem, showing a winged human-bird deity, not a university or agency crest.',
    'company_logo': 'This is a commercial brand identity logo, company trademark, or business wordmark.',
    'document_stamp': 'An inked rubber stamp impression applied to a paper document.',
    'portrait': 'A portrait photograph of a real person.',
    'signature': 'A handwritten signature made with a pen.',
    'barcode': 'A linear barcode made of parallel vertical bars.',
    'qr_code': 'A square QR code with a black and white matrix pattern.',
}


# Negative prompts are single text descriptions, not additional positive categories.
NEGATIVE = {
    'printed_text': ['Ordinary printed text on a document without a logo or emblem.'],
    'numbers': ['A cropped document region containing printed numbers and digits.'],
    'table': ['A table with rows, columns, and printed text.'],
    'blank': ['An empty or nearly blank document region.'],
    'background': ['A decorative document security background without a distinct emblem.'],
    'illustration': ['A generic illustration, icon, or decorative graphic rather than an official emblem or brand logo.'],
    'pattern': ['A repetitive black and white texture or stripes, not a scannable barcode.'],
    'bar_chart': ['A bar chart showing statistical values with colored rectangular bars, axes, tick labels, and a legend.'],
}


def score_image(image, prompts, processor, model, device):
    labels = list(prompts)
    texts = [prompts[k][0] for k in labels]
    inputs = processor(text=texts, images=image, padding='max_length',
                       truncation=True, return_tensors='pt').to(device)
    with torch.inference_mode():
        logits = model(**inputs).logits_per_image[0].float().cpu().tolist()
    scores = dict(zip(labels, logits))
    ranked = sorted(scores, key=scores.get, reverse=True)
    top1, top2 = ranked[:2]
    return (top1 if top1 in SINGLE else 'Other'), top1, top2, scores


GROUND_TRUTH = {
    "Screenshot 2026-10-16 105726.png": "Other",
    "Screenshot 2026-10-16 105709.png": "Other",
    "zDHHFH51gFPfk1hYsaiWzNJACUFsYx0wQxsYNCMRJiG2XNEdMqCwrVfJZoNJ6WW-G_WRykrRb2UDza4oAXRLZmi61R_Om0Ri5GouaPE72qnTpgz3POIJi3AvXH4u8cdOa1Z69WiZQZSB6e1S_g4U3YDlQmX9y6nk63unOt3ecTjHFvrMAuAIDfztwFE8GnlJ.jpg": "document_stamp",
    "Screenshot 2026-10-16 105609.png": "Other",
    "images (5).png": "Other",
    "image-2.webp": "Other",
    "Screenshot 2026-10-16 105443.png": "Other",
    "1.webp": "Other",
    "ประวัติกระดาษ-1.jpg": "Other",
    "pngtree-signature-isolated-the-signature-is-neatly-written-in-black-ink-the-png-image_12728547.png": "signature",
    "ลายเซ็น_วิเชียร_ขาวขำ.png": "signature",
    "ลายเซ็น_ศิริโชค_โสภา.png": "signature",
    "ลายเซ็น_ชวรัตน์_ชาญวีรกูล.png": "signature",
    "6a581bda-e4ce-4a01-bb2a-620bf77673cd.jpg": "signature",
    "oykhqlade5u9Chn2MjB-o.gif": "Other",
    "images (5).jpg": "Other",
    "Screenshot 2026-10-16 105804.png": "Other",
    "Screenshot 2026-10-16 105751.png": "Other",
    "images (6).png": "Other",
    "Screenshot 2026-10-16 105731.png": "Other",
    "images (4).png": "emblem",
    "images (2).jpg": "emblem",
    "w280.jpg": "emblem",
    "logo-sau-2024-back.png": "emblem",
    "133330489.jpg": "emblem",
    "Guizhou-University-001.png": "emblem",
    "TFSU2.png": "emblem",
    "mt3.png": "emblem",
    "Screenshot 2026-10-16 104254.png": "thai_garuda",
    "Screenshot 2026-10-16 104254(1).png": "thai_garuda",
    "images (1).jpg": "emblem",
    "csm_logo_mfu_3d_colour_630b77d675.png": "emblem",
    "26.สงขลานครินทร์.png": "emblem",
    "3.เกษตรศาสตร์.png": "emblem",
    "KSP-300x300.jpg": "emblem",
    "picture-3-36483.jpg": "emblem",
    "logo1moi.png": "emblem",
    "images.jpg": "emblem",
    "Thai_Garuda_emblem.png": "thai_garuda",
    "รร.น้ำคำใหญ่วิทยา-mockup-for-line-1.jpg": "emblem",
    "images (2).png": "emblem",
    "Emblem_of_Royal_Thai_Police.png": "emblem",
    "Emblem_of_the_Royal_Thai_Air_Force.svg.webp": "emblem",
    "image9.jpg": "document_stamp",
    "02.jpg": "document_stamp",
    "images (4).jpg": "document_stamp",
    "4648cb8b-0631-450f-bc02-a426031bb404.jpg": "document_stamp",
    "images (3).jpg": "document_stamp",
    "barcode-1.png": "barcode",
    "barcode.png": "barcode",
    "a1.jpg": "barcode",
    "Screenshot 2026-10-16 104247.png": "barcode",
    "example_qrcode_promptpay_th.png": "qr_code",
    "dFQROr7oWzulq5FZWubw0MsLJoDb6aiALV9INE9yVIWSKX8bhq0owYOjlh3UXrJWatC.webp": "qr_code",
    "main.png": "qr_code",
    "qrcode_bombik.com.png": "qr_code",
    "images (1).png": "company_logo",
    "uhe-logo-design-initial-letter-uhe-monogram-logo-using-hexagon-shape_1101554-55776.jpg": "company_logo",
    "6804169-nike-cool-logo.jpg": "company_logo",
    "images.png": "company_logo",
    "images (3).png": "emblem",
}

# User-confirmed corrections to labels (do not merge Garuda into emblem).
GROUND_TRUTH.update({
    'oykhqlade5u9Chn2MjB-o.gif': 'signature',
    'images (5).jpg': 'document_stamp',
    'images (3).png': 'emblem',
    'images (2).png': 'company_logo',
    'imagesำำพำ.jpg': 'thai_garuda',
})

def load_image(path):
    if path.suffix.lower() == '.svg':
        try:
            import cairosvg
        except ImportError as exc:
            raise RuntimeError('SVG needs cairosvg: pip install cairosvg') from exc
        from io import BytesIO
        data = cairosvg.svg2png(url=str(path))
        with Image.open(BytesIO(data)) as img:
            return img.convert('RGB')
    with Image.open(path) as img:
        # Normalize palette transparency without the Pillow warning.
        if img.mode == 'P' and 'transparency' in img.info:
            return img.convert('RGBA').convert('RGB')
        return img.convert('RGB')


def guarded_decision(scores, baseline):
    """Experimental V2.1 post-processing. Thresholds are exploratory, not calibrated.

    These rules were inspired by errors on the existing 59-image set; results on
    that set are *training-set diagnostics*, not an unbiased accuracy estimate.
    """
    pos = max(SINGLE, key=lambda k: scores[k])
    neg = max(NEGATIVE, key=lambda k: scores[k])
    pn_margin = scores[pos] - scores[neg]

    # Garuda must clearly outrank the generic institutional emblem; when the
    # two are close, prefer the broader emblem class instead of a specific one.
    if pos == 'thai_garuda' and scores['thai_garuda'] - scores['emblem'] < 2.0:
        if scores['emblem'] >= scores[neg]:
            return 'emblem', 'garuda_emblem_ambiguity'
    # Weak Garuda against a negative description is likely non-target content.
    if pos == 'thai_garuda' and pn_margin < 2.0:
        return 'Other', 'weak_garuda_vs_negative'
    # Weak generic emblems are a common false-positive class.
    if pos == 'emblem' and scores['emblem'] < -6.3:
        return 'Other', 'weak_emblem_score'
    return baseline, 'unchanged'


def main():
    parser = argparse.ArgumentParser(description='SigLIP single-prompt classification and error analysis')
    parser.add_argument('--input', default='input', help='Folder containing test images')
    parser.add_argument('--output', default='results_v2_3.csv')
    parser.add_argument('--truth', default=None, help='Optional CSV with File and Ground Truth columns')
    parser.add_argument('--device', choices=['auto', 'cpu', 'cuda'], default='auto')
    parser.add_argument('--decision-policy', choices=['guarded', 'baseline'], default='guarded', help='Guarded: experimental V2.1 rules; baseline: exact V2 scoring')
    parser.add_argument('--min-logit', type=float, default=None, help='Optional rejection threshold: top positive score below this becomes Other; tune only on separate validation data')
    parser.add_argument('--min-margin', type=float, default=None, help='Optional rejection threshold: positive-vs-best-negative margin below this becomes Other; tune only on separate validation data')
    args = parser.parse_args()
    folder = Path(args.input)
    if not folder.is_dir():
        parser.error(f'Input folder not found: {folder}')
    paths = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS)
    if not paths:
        parser.error(f'No images found in {folder}')
    device = ('cuda' if torch.cuda.is_available() else 'cpu') if args.device == 'auto' else args.device
    print(f'Loading {MODEL_NAME} on {device}...')
    processor = AutoProcessor.from_pretrained(MODEL_NAME)
    model = AutoModel.from_pretrained(MODEL_NAME).to(device).eval()
    prompts = {**{k: [v] for k, v in SINGLE.items()}, **NEGATIVE}
    truths = dict(GROUND_TRUTH)
    if args.truth:
        truth_df = pd.read_csv(args.truth).fillna('')
        if not {'File', 'Ground Truth'}.issubset(truth_df.columns):
            parser.error('Truth CSV must contain File and Ground Truth')
        truths.update(dict(zip(truth_df['File'].astype(str), truth_df['Ground Truth'].astype(str))))
    rows = []
    for i, path in enumerate(paths, 1):
        try:
            image = load_image(path)
            prediction, raw, second, scores = score_image(image, prompts, processor, model, device)
            positive_top = max(SINGLE, key=lambda k: scores[k])
            negative_top = max(NEGATIVE, key=lambda k: scores[k])
            positive_negative_margin = scores[positive_top] - scores[negative_top]
            if prediction != 'Other' and (
                (args.min_logit is not None and scores[positive_top] < args.min_logit) or
                (args.min_margin is not None and positive_negative_margin < args.min_margin)
            ):
                prediction = 'Other'
            baseline_prediction = prediction
            rule = 'baseline'
            if args.decision_policy == 'guarded':
                prediction, rule = guarded_decision(scores, prediction)
            truth = truths.get(path.name, '')
            row = {
                'File': path.name, 'Ground Truth': truth, 'Prediction': prediction,
                'Baseline Prediction': baseline_prediction, 'Decision Rule': rule,
                'Top1 Raw': raw, 'Top1 Score': round(scores[raw], 5),
                'Top2 Raw': second, 'Top2 Score': round(scores[second], 5),
                'Margin': round(scores[raw] - scores[second], 5),
                'Best Positive': positive_top, 'Best Negative': negative_top,
                'Positive-Negative Margin': round(positive_negative_margin, 5),
                'Correct': prediction == truth if truth else '',
            }
            row.update({f'Score {key}': round(scores[key], 5) for key in prompts})
            rows.append(row)
            print(f'[{i}/{len(paths)}] {path.name}: {prediction} (truth: {truth or "unlabeled"})')
        except Exception as exc:
            print(f'[{i}/{len(paths)}] ERROR {path.name}: {exc}')
    if not rows:
        raise RuntimeError('No images processed')
    df = pd.DataFrame(rows)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output, index=False, encoding='utf-8-sig')
    labeled = df[df['Ground Truth'].notna() & df['Ground Truth'].ne('')].copy()
    if len(labeled):
        correct = labeled['Correct'].astype(bool)
        print(f'\nAccuracy: {correct.sum()}/{len(labeled)} = {correct.mean():.2%}')
        print('\nPER CATEGORY (ground truth):')
        print(labeled.groupby('Ground Truth')['Correct'].agg(['count', 'sum']).to_string())
        errors = labeled[~correct]
        print('\nERRORS:')
        if errors.empty:
            print('None')
        else:
            print(errors[['File', 'Ground Truth', 'Prediction', 'Top1 Raw', 'Top2 Raw', 'Margin']].to_string(index=False))
    print(f'\nSaved: {output.resolve()}')
    print('Raw logits and margins are NOT calibrated probabilities.')
    print('V2.3 changes ONLY the bar_chart negative prompt relative to V2.1; evaluate on independent images.')
    print('Validate with a NEW, separate dataset before deployment; no 100% accuracy guarantee.')


if __name__ == '__main__':
    main()
