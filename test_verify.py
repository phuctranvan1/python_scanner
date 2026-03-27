"""Save verification results to UTF-8 file."""
import sys, os, re, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.insert(0, r'C:\Users\ETC\Desktop\python')
from scan_ctk import preprocess_image, parse_receipt
import pytesseract
from PIL import Image

pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

def test_dir(folder):
    if not os.path.exists(folder): return
    files = [f for f in os.listdir(folder) if f.lower().endswith(('.jpg', '.jpeg', '.png'))][:5]
    print(f"\n=== {folder} ({len(files)} images) ===")
    for f in files:
        path = os.path.join(folder, f)
        img = Image.open(path)
        img = preprocess_image(img)
        txt = pytesseract.image_to_string(img, lang="vie+eng", config="--psm 4 --oem 3 --dpi 300")
        data = parse_receipt(txt)
        print(f"\n[{f}]")
        for k, v in data.items():
            print(f"  {k}: {v}")
        if not data:
            print("  (No fields extracted)")

test_dir(r"C:\Users\ETC\Downloads\2303")
test_dir(r"C:\Users\ETC\Downloads\Vận Hành NOC [24-03-2026 08_07]_extracted")
print("\n[DONE]")
