import pytesseract  # type: ignore
import numpy as np  # type: ignore

try:
    import easyocr  # type: ignore
except ImportError:
    pass

# User may configure this later via settings
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

_easyocr_reader = None
_easyocr_available = None

def get_easyocr_reader():
    global _easyocr_reader, _easyocr_available
    if _easyocr_available is False:
        return None
    if _easyocr_reader is None:
        try:
            import warnings
            warnings.filterwarnings("ignore", category=UserWarning, module="torch")
            _easyocr_reader = easyocr.Reader(['vi', 'en'], gpu=False, verbose=False)
            _easyocr_available = True
        except Exception:
            _easyocr_available = False
            return None
    return _easyocr_reader


def run_tesseract(pil_img):
    config = (
        "--psm 6 "
        "--oem 3 "
        "--dpi 300 "
        "-c preserve_interword_spaces=1 "
        "-c tessedit_do_invert=0"
    )
    return pytesseract.image_to_string(pil_img, lang="vie+eng", config=config)


def run_easyocr(pil_img):
    reader = get_easyocr_reader()
    if reader is None:
        return "", 0.0, []
    try:
        np_img = np.array(pil_img)
        results = reader.readtext(np_img, detail=1, paragraph=False)
        results.sort(key=lambda r: (r[0][0][1], r[0][0][0]))
        lines = [text for _, text, _ in results]
        confidences = [conf for _, _, conf in results]
        full_text = "\n".join(lines)
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
        return full_text, avg_conf, results
    except Exception:
        return "", 0.0, []


def merge_dual_ocr(tess_text, easy_text, easy_confidence):
    if not easy_text.strip():
        return tess_text

    tess_lines = [l for l in tess_text.splitlines() if l.strip()]
    easy_lines = [l for l in easy_text.splitlines() if l.strip()]

    if len(easy_lines) > len(tess_lines) * 1.3 and easy_confidence >= 0.65:
        return easy_text

    merged = []
    for t_line in tess_lines:
        best_easy = None
        best_score = 0.0
        t_stripped = t_line.strip().lower()
        for e_line in easy_lines:
            e_stripped = e_line.strip().lower()
            if not t_stripped or not e_stripped: continue
            common = sum(c in e_stripped for c in t_stripped)
            score = common / max(len(t_stripped), len(e_stripped))
            if score > best_score:
                best_score = score
                best_easy = e_line

        digit_count = sum(c.isdigit() for c in t_line)
        has_numbers = digit_count >= 3

        if best_easy and best_score >= 0.5 and has_numbers and easy_confidence >= 0.65:
            merged.append(best_easy)
        else:
            merged.append(t_line)

    return "\n".join(merged)
