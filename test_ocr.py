import sys
import os
from PIL import Image
import pytesseract

pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

def run_tests():
    folder = r"C:\Users\ETC\Downloads\2303"
    images = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".jpg")]
    if not images:
        print("No images found.")
        return
        
    print(f"Testing {len(images)} images.")
    # Just test first 3 images to save time
    for img_path in images[:3]:
        img = Image.open(img_path).convert('L')
        print(f"\n--- Testing {os.path.basename(img_path)} ---")
        
        # Test configurations
        test_cases = [
            ("vie+eng", "--psm 6", 1),
            ("vie+eng", "--psm 6", 2), # Current failing config
            ("vie+eng", "--psm 4", 2),
            ("vie+eng", "--psm 4", 1),
            ("vie", "--psm 6", 2),     # Try native Vietnamese only
            ("vie", "--psm 4", 1)
        ]
        
        found_date = False
        for lang, cfg, scale in test_cases:
            t_img = img
            if scale > 1:
                t_img = img.resize((img.width * scale, img.height * scale), Image.Resampling.LANCZOS)
            
            try:
                txt = pytesseract.image_to_string(t_img, lang=lang, config=cfg)
                # Find the TG vào line
                for line in txt.split("\n"):
                    if "23/" in line or "25/" in line or "TG" in line:
                        print(f"[{lang} | {cfg} | {scale}x] => {line.strip()}")
                        found_date = True
            except Exception as e:
                pass

if __name__ == "__main__":
    run_tests()
