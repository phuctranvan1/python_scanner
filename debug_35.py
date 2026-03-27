"""
Debug script: dump raw OCR text for EVERY image to hiểu rõ đang đọc gì.
So sánh output TRƯỚC và SAU preprocessing để xác định vấn đề 3 vs 5.
"""
import sys, os, re
sys.path.insert(0, r'C:\Users\ETC\Desktop\python')
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

def ocr_raw(img, config):
    return pytesseract.image_to_string(img, lang="vie+eng", config=config)

def extract_key_lines(txt):
    found = []
    for line in txt.splitlines():
        l = line.strip()
        if any(k in l.lower() for k in ['ma giao', 'mã giao', 'bien so', 'biển số', 'epc', 'tg v']):
            found.append(l)
    return found if found else ["(No key lines found)"]

folder = r"C:\Users\ETC\Downloads\2303"
if not os.path.exists(folder):
    print(f"Folder not found: {folder}")
else:
    files = [f for f in os.listdir(folder) if f.lower().endswith(('.jpg','jpeg','.png'))][:3]
    for fname in files:
        path = os.path.join(folder, fname)
        print(f"\n{'='*60}")
        print(f"FILE: {fname}")
        img = Image.open(path).convert('L')
        w, h = img.size
        print(f"Size: {w}x{h}px")

        # Test 1: Original 1x (no resize)
        t1 = ocr_raw(img, "--psm 4 --oem 3")
        print("\n[1x no resize, psm4]")
        for l in extract_key_lines(t1): print("  ", l)

        # Test 2: 0.7x scale (gentle downscale)
        img2 = img.resize((int(w*0.7), int(h*0.7)), Image.Resampling.LANCZOS)
        t2 = ocr_raw(img2, "--psm 4 --oem 3")
        print("\n[0.7x scale, psm4]")
        for l in extract_key_lines(t2): print("  ", l)

        # Test 3: 1x + Contrast + Sharpen
        img3 = ImageEnhance.Contrast(img).enhance(2.0)
        img3 = img3.filter(ImageFilter.SHARPEN)
        t3 = ocr_raw(img3, "--psm 4 --oem 3")
        print("\n[1x + Contrast2.0 + Sharpen, psm4]")
        for l in extract_key_lines(t3): print("  ", l)
        
        # Test 4: 1x + Otsu binarize (threshold ~180)
        img4 = img.point(lambda p: 255 if p > 180 else 0)
        t4 = ocr_raw(img4, "--psm 4 --oem 3")
        print("\n[1x + Binarize(180), psm4]")
        for l in extract_key_lines(t4): print("  ", l)

        # Test 5: 1x lang=eng only
        t5 = pytesseract.image_to_string(img, lang="eng", config="--psm 4 --oem 3")
        print("\n[1x no resize, lang=ENG only, psm4]")
        for l in extract_key_lines(t5): print("  ", l)

print("\n[DONE]")
