import sys
import argparse
import os

try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    import pytesseract
except ImportError:
    print("Missing required libraries. Please run in your terminal:")
    print("pip install Pillow pytesseract")
    sys.exit(1)

# ===============================================================================
# IMPORTANT: On Windows, you usually need to install the Tesseract executable.
# 1. Download the installer from: https://github.com/UB-Mannheim/tesseract/wiki
# 2. Run the installer (remember the path where it installs, usually C:\Program Files\Tesseract-OCR)
# 3. Uncomment and update the line below to point to your tesseract.exe:
# ===============================================================================

pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

def preprocess_image(img):
    """Enhance image for better OCR accuracy and distinguish 3 vs 5"""
    img = img.convert('L')
    
    # Autocontrast to maximize the black/white spread before any scaling
    img = ImageOps.autocontrast(img)
    
    # Upscale 3x for high detail
    w, h = img.size
    img = img.resize((w * 3, h * 3), Image.Resampling.LANCZOS)
    
    # Increase contrast
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)
    
    # Sharpen to crispen edges
    img = img.filter(ImageFilter.SHARPEN)
    
    # MinFilter(3) thickens dark pixels. Thermal receipts often have
    # broken dotted lines that make "3" look like "5" or "8".
    # This acts like morphological erosion to connect broken dots.
    img = img.filter(ImageFilter.MinFilter(3))
    
    # Thresholding
    img = img.point(lambda p: 255 if p > 140 else 0)
    
    return img


def extract_text(image_path):
    try:
        print(f"Loading image from '{image_path}'...")
        img = Image.open(image_path)
        
        # Preprocess to improve accuracy for 3 and 5
        img = preprocess_image(img)
        
        print("Scanning for text... (this may take a moment)")
        # Extract text using Tesseract with Vietnamese language support
        try:
            extracted_text = pytesseract.image_to_string(img, lang="vie+eng", config="--psm 6 --dpi 300")
        except pytesseract.TesseractError as e:
            if 'Failed loading language' in str(e):
                print("\n[ERROR] Vietnamese language data ('vie') is not installed for Tesseract.")
                print("1. Download 'vie.traineddata' from: https://github.com/tesseract-ocr/tessdata_best/raw/main/vie.traineddata")
                print("2. Place it in your Tesseract 'tessdata' folder (e.g., C:\\Program Files\\Tesseract-OCR\\tessdata\\vie.traineddata)")
                return
            else:
                raise
        
        if extracted_text.strip():
            print("\n" + "="*40)
            print("         EXTRACTED TEXT")
            print("="*40)
            print(extracted_text.strip())
            print("="*40 + "\n")
        else:
            print("\nNo readable text was detected in this photo.\n")
            
    except pytesseract.TesseractNotFoundError:
        print("\n[ERROR] Tesseract OCR executable is not found!")
        print("Please install it and uncomment 'tesseract_cmd' on line 18.")
        print("Download link: https://github.com/UB-Mannheim/tesseract/wiki\n")
    except FileNotFoundError:
        print(f"\n[ERROR] The image file '{image_path}' was not found.\n")
    except Exception as e:
        print(f"\n[ERROR] An unexpected error occurred: {e}\n")


if __name__ == "__main__":
    # Handle command-line arguments
    parser = argparse.ArgumentParser(description="Extract text/values from any photo.")
    parser.add_argument("image", nargs="?", default=r"C:\Users\ETC\Downloads\2303", help="Path to the image or directory to scan")
    
    args = parser.parse_args()
    
    if os.path.isdir(args.image):
        print(f"Processing all images in directory: {args.image}")
        for filename in sorted(os.listdir(args.image)):
            if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff')):
                filepath = os.path.join(args.image, filename)
                print(f"\n--- {filename} ---")
                extract_text(filepath)
    else:
        extract_text(args.image)
