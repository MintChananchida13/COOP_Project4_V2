import cv2
import easyocr
import sys
import os

def test_ocr():
    image_path = "cropped_rois/full_page_text_0.png"
    if not os.path.exists(image_path):
        print(f"Error: {image_path} does not exist!")
        return

    print("Loading image...")
    img = cv2.imread(image_path)
    if img is None:
        print("Error: Could not load image!")
        return
    print(f"Image shape: {img.shape}")

    print("Initializing EasyOCR reader...")
    try:
        reader = easyocr.Reader(['th', 'en'], gpu=False)
        print("Reader initialized successfully.")
    except Exception as e:
        print(f"Failed to initialize reader: {e}")
        return

    print("Running readtext on full image...")
    try:
        results = reader.readtext(img)
        print(f"Success! Found {len(results)} lines of text.")
        for idx, res in enumerate(results[:10]):
            bbox, text, conf = res
            print(f"Line {idx+1}: '{text}' (Conf: {conf:.2f})")
    except Exception as e:
        print("Failed during readtext execution!")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_ocr()
