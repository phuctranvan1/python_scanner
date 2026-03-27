import sys, pytesseract, re, os
from PIL import Image, ImageEnhance, ImageFilter
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

def search_perfect_config():
    d = r'C:\Users\ETC\Downloads\Vận Hành NOC [24-03-2026 08_07]_extracted'
    target_img = None
    for f in os.listdir(d):
        if not f.endswith('.jpg'): continue
        p = os.path.join(d, f)
        try:
            txt = pytesseract.image_to_string(Image.open(p), lang="vie")
            if "3076" in txt or "15:25:16" in txt:
                target_img = p
                break
        except: pass
    
    if not target_img:
        print("COULD NOT FIND TARGET IMAGE")
        return
        
    print(f"FOUND TARGET IMAGE: {target_img}")
    img = Image.open(target_img).convert('L')
    
    scales = [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.5, 2.0]
    psms = ['--psm 6', '--psm 4', '--psm 11']
    
    results = []
    for scale in scales:
        t_img = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)
        for psm in psms:
            for lang in ["vie", "eng", "vie+eng"]:
                try:
                    txt = pytesseract.image_to_string(t_img, lang=lang, config=psm)
                    match = re.search(r'TG v[àa]o[;:.\s]*([^\n\r]+)', txt, re.IGNORECASE)
                    if match:
                        res = match.group(1).strip()
                        if '23' in res or '03' in res:
                            print(f"[SUCCESS] Scale={scale} | psm={psm} | lang={lang} => {res}")
                            results.append((scale, psm, lang, res))
                except Exception as e: pass

search_perfect_config()
