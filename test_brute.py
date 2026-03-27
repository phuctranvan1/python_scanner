import sys, pytesseract, re
from PIL import Image, ImageEnhance, ImageFilter
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
file = r'C:\Users\ETC\Downloads\Vận Hành NOC [24-03-2026 08_07]_extracted\134770242590708409910.jpg'

try:
    img = Image.open(file).convert('L')
except Exception as e:
    print(f"Could not open image: {e}")
    sys.exit(1)

configs=[('--psm 6', 1), ('--psm 4', 1), ('--psm 11', 1), ('--psm 3', 1), ('--psm 6', 2), ('--psm 4', 2), ('--psm 6', 3), ('--psm 4', 3), ('--psm 4', 0.5)]
langs=['vie', 'eng', 'vie+eng']

print("Starting brute-force tests...")
for b_thresh in [None, 120, 150, 180, 200]:
  for lang in langs:
    for cfg, scale in configs:
      test_img = img.copy()
      if scale != 1: 
          test_img = test_img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)
      if b_thresh: 
          test_img = test_img.point(lambda p: 255 if p > b_thresh else 0)
          
      try:
        txt = pytesseract.image_to_string(test_img, lang=lang, config=cfg)
        match = re.search(r'TG v[àa]o[;:.\s]*([^\n\r]+)', txt, re.IGNORECASE)
        if match: 
            res = match.group(1).strip()
            print(f"[{lang} | {cfg} | {scale}x | thr={b_thresh}] => {res}")
            if '23' in res or '03' in res:
                print(f"   !!! EXACT MATCH !!!")
        elif '23/03' in txt or '23/' in txt:
            print(f">>> RAW MATCH. CONFIG: {lang} | {cfg} | {scale}x | thr={b_thresh}")
            for l in txt.split('\n'):
                if '23' in l or 'TG' in l: print("     ", l)
      except Exception as e: 
          pass
