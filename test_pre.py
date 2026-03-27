import os
from PIL import Image, ImageEnhance, ImageFilter
import pytesseract
import re

pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

def test_preprocess():
    folder = r"C:\Users\ETC\Downloads\2303"
    files = [f for f in os.listdir(folder) if f.endswith('.jpg')][:3]
    
    for f in files:
        img_path = os.path.join(folder, f)
        img = Image.open(img_path).convert('L')
        print(f"\n--- Testing {f} ({img.size}) ---")
        
        # Test 1: Current scan_ctk logic (0.5x if > 1000)
        w, h = img.size
        img1 = img.resize((int(w * 0.5), int(h * 0.5)), Image.Resampling.LANCZOS)
        txt1 = pytesseract.image_to_string(img1, lang="vie+eng", config="--psm 4")
        
        # Test 2: Scale 1.0 (No resize), just enhance contrast
        img2 = ImageEnhance.Contrast(img).enhance(2.0)
        txt2 = pytesseract.image_to_string(img2, lang="vie+eng", config="--psm 4")
        
        # Test 3: Scale 1.5 + Binarize (thresh=180)
        img3 = img.resize((int(w * 1.5), int(h * 1.5)), Image.Resampling.LANCZOS)
        img3 = img3.point(lambda p: 255 if p > 180 else 0)
        txt3 = pytesseract.image_to_string(img3, lang="vie+eng", config="--psm 4")
        
        for name, tk in [("1: 0.5x Scale", txt1), ("2: 1.0x+Contrast", txt2), ("3: 1.5x+Binarize", txt3)]:
            mgd = re.search(r"M[ãa]?\s*giao d[ịi]ch[;:.\s]*([^\n\r]+)", tk, re.IGNORECASE)
            tgv = re.search(r"TG v[àa]o[;:.\s]*([^\n\r]+)", tk, re.IGNORECASE)
            epc = re.search(r"EPC[;:.\s]*([A-Z0-9]+)", tk, re.IGNORECASE)
            mgd_val = mgd.group(1).strip() if mgd else "None"
            tgv_val = tgv.group(1).strip() if tgv else "None"
            epc_val = epc.group(1).strip() if epc else "None"
            print(f"[{name}] MGD: {mgd_val} | TGV: {tgv_val} | EPC: {epc_val}")

test_preprocess()
