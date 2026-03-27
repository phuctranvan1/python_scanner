import sys
import os
import threading
import re
import zipfile
import unicodedata
import shutil
import numpy as np  # type: ignore
import csv
import json
import difflib
import sqlite3
import cv2  # type: ignore
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageTk, ImageEnhance, ImageFilter, ImageOps  # type: ignore

try:
    import pytesseract  # type: ignore
    import customtkinter as ctk  # type: ignore
except ImportError:
    print("Missing required libraries. Please run in your terminal:")
    print("pip install Pillow pytesseract customtkinter opencv-python")
    sys.exit(1)

# Set Tesseract path
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# ─────────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────────

def init_db():
    try:
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_time TEXT,
                receipt_type TEXT,
                transaction_code TEXT,
                license_plate TEXT,
                price TEXT,
                status TEXT,
                epc TEXT,
                time_in TEXT,
                station_in TEXT,
                station_in_id TEXT,
                lane_in TEXT,
                time_out TEXT,
                station_out TEXT,
                station_out_id TEXT,
                lane_out TEXT,
                ticket_type TEXT,
                unit TEXT,
                raw_text TEXT
            )
        ''')
        # Migrate: add columns that may be missing from older schema
        new_columns = [
            ("receipt_type", "TEXT"),
            ("station_out",  "TEXT"),
            ("lane_out",     "TEXT"),
            ("ticket_type",  "TEXT"),
            ("unit",         "TEXT"),
        ]
        existing = {row[1] for row in cursor.execute("PRAGMA table_info(scans)")}
        for col_name, col_type in new_columns:
            if col_name not in existing:
                cursor.execute(f"ALTER TABLE scans ADD COLUMN {col_name} {col_type}")
        conn.commit()
    except Exception as e:
        print(f"DB Error: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

def save_to_db(data, raw_text, receipt_type="unknown"):
    try:
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO scans (
                scan_time, receipt_type, transaction_code, license_plate, price, status, epc,
                time_in, station_in, station_in_id, lane_in,
                time_out, station_out, station_out_id, lane_out,
                ticket_type, unit, raw_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            receipt_type,
            data.get('Mã giao dịch', ''),
            data.get('Biển số', ''),
            data.get('Giá tiền', ''),
            data.get('Trạng thái', ''),
            data.get('EPC', data.get('RFID', '')),
            data.get('TG vào', data.get('Thời gian vào', '')),
            data.get('Trạm vào', ''),
            data.get('Id trạm vào', ''),
            data.get('Làn vào', ''),
            data.get('TG ra', data.get('Thời gian ra', '')),
            data.get('Trạm ra', ''),
            data.get('Id trạm ra', ''),
            data.get('Làn ra', ''),
            data.get('Loại vé', ''),
            data.get('Đơn vị', ''),
            raw_text
        ))
        conn.commit()
    except Exception as e:
        print(f"DB Save Error: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

# ─────────────────────────────────────────────────────────────────
# EasyOCR — Lazy-load
# ─────────────────────────────────────────────────────────────────
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
            import easyocr  # type: ignore
            _easyocr_reader = easyocr.Reader(['vi', 'en'], gpu=False, verbose=False)
            _easyocr_available = True
        except Exception:
            _easyocr_available = False
            return None
    return _easyocr_reader

# Configure CustomTkinter
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# ─────────────────────────────────────────────────────────────────
# Image Preprocessing
# ─────────────────────────────────────────────────────────────────

def preprocess_image(img):
    """
    Preprocessing pipeline tối ưu cho hai loại screenshot điện thoại:
    - Loại 1: Nền trắng, chữ đen, layout dọc (giao diện web)
    - Loại 2: Nền trắng/xanh lá, text đen, layout key:value (VETC app)
    Các screenshot này thường rõ ràng hơn ảnh scan thật → cần xử lý nhẹ nhàng,
    tránh làm mờ hoặc méo chữ.
    """
    open_cv_image = np.array(img)

    # Convert to grayscale
    if len(open_cv_image.shape) == 3:
        gray = cv2.cvtColor(open_cv_image, cv2.COLOR_RGB2GRAY)
    else:
        gray = open_cv_image.copy()

    h, w = gray.shape

    # Upscale nhẹ nếu ảnh nhỏ (screenshot điện thoại thường >= 1000px nên skip)
    if w < 800:
        scale = 2
        gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_LANCZOS4)

    # Phân tích độ sáng trung bình để phát hiện nền tối / sáng
    mean_brightness = np.mean(gray)

    if mean_brightness > 180:
        # Ảnh sáng (screenshot điện thoại nền trắng): chỉ cần sharpen nhẹ
        # Không dùng CLAHE mạnh vì sẽ làm nhiễu nền trắng
        kernel = np.array([[0, -0.5, 0],
                            [-0.5, 3, -0.5],
                            [0, -0.5, 0]])
        enhanced = cv2.filter2D(gray, -1, kernel)
        enhanced = np.clip(enhanced, 0, 255).astype(np.uint8)
    else:
        # Ảnh tối / scan thật: dùng CLAHE để tăng tương phản
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        enhanced = cv2.bilateralFilter(enhanced, d=5, sigmaColor=50, sigmaSpace=50)

    # Binarize nhẹ: ngưỡng adaptive để tách chữ khỏi nền không đều
    # blockSize phải lẻ, chọn giá trị đủ lớn để không bị ảnh hưởng bởi shadow
    binary = cv2.adaptiveThreshold(
        enhanced, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=31,
        C=10
    )

    return Image.fromarray(binary)


def preprocess_for_easyocr(img):
    """Preprocessing riêng cho EasyOCR: giữ màu xám (không binarize) để
    EasyOCR tự học features tốt hơn."""
    open_cv_image = np.array(img)
    if len(open_cv_image.shape) == 3:
        gray = cv2.cvtColor(open_cv_image, cv2.COLOR_RGB2GRAY)
    else:
        gray = open_cv_image.copy()

    h, w = gray.shape
    if w < 800:
        gray = cv2.resize(gray, (w * 2, h * 2), interpolation=cv2.INTER_LANCZOS4)

    mean_brightness = np.mean(gray)
    if mean_brightness > 180:
        kernel = np.array([[0, -0.5, 0], [-0.5, 3, -0.5], [0, -0.5, 0]])
        enhanced = cv2.filter2D(gray, -1, kernel)
        enhanced = np.clip(enhanced, 0, 255).astype(np.uint8)
    else:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)

    return Image.fromarray(enhanced)

# ─────────────────────────────────────────────────────────────────
# OCR Engines
# ─────────────────────────────────────────────────────────────────

def run_tesseract(pil_img):
    """Chạy Tesseract với config tối ưu cho hai loại hóa đơn."""
    # PSM 6: Assume a single uniform block of text — tốt cho layout dọc lẫn ngang
    config = (
        "--psm 6 "
        "--oem 3 "
        "--dpi 300 "
        "-c preserve_interword_spaces=1 "
        "-c tessedit_do_invert=0"
    )
    return pytesseract.image_to_string(pil_img, lang="vie+eng", config=config)


def run_easyocr(pil_img):
    """Chạy EasyOCR, sắp xếp kết quả top→bottom, left→right."""
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
    """Merge kết quả Tesseract và EasyOCR theo confidence voting.
    Ưu tiên EasyOCR cho dòng có nhiều số, Tesseract cho text tiếng Việt."""
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
            if not t_stripped or not e_stripped:
                continue
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

# ─────────────────────────────────────────────────────────────────
# OCR Correction Helpers
# ─────────────────────────────────────────────────────────────────

def correct_numeric_ocr(text):
    """Sửa các ký tự bị nhận nhầm trong trường thuần số."""
    text = re.sub(r'[,.\-]', '', text)
    mapping = {
        'O': '0', 'o': '0', 'D': '0', 'Q': '0', 'C': '0',
        'l': '1', 'I': '1', 'i': '1', '|': '1', ']': '1', '[': '1', 't': '1',
        'Z': '2', 'z': '2',
        'A': '4', 'h': '4',
        'S': '5', 's': '5',
        'G': '6', 'b': '6',
        'T': '7',
        'B': '8',
        'g': '9', 'q': '9', 'P': '9',
    }
    for k, v in mapping.items():
        text = text.replace(k, v)
    return text.replace(" ", "")


def correct_hex_ocr(text):
    """Sửa EPC/RFID hex string (chỉ giữ 0-9 A-F)."""
    mapping = {
        'O': '0', 'Q': '0',
        'L': '1', 'I': '1',
        'Z': '2',
        'G': '6',
        'T': '7',
        'S': '5',
    }
    text = text.upper().replace(" ", "")
    for k, v in mapping.items():
        text = text.replace(k, v)
    text = re.sub(r'[^0-9A-F]', '', text)
    return text


def normalize_datetime(text):
    """Chuẩn hoá chuỗi thời gian về dạng DD/MM/YYYY HH:MM:SS."""
    # Xoá các ký tự dư thừa do OCR
    text = text.strip()
    # Thay thế dấu phân cách thời gian bị nhận sai
    text = re.sub(r'[,;|]', ':', text)
    # Đảm bảo dấu / trong ngày
    text = re.sub(r'(\d{2})[-.](\d{2})[-.](\d{4})', r'\1/\2/\3', text)
    return text

# ─────────────────────────────────────────────────────────────────
# Receipt Type Detection & Parsing
# ─────────────────────────────────────────────────────────────────

# Các keyword đặc trưng phân biệt hai loại hóa đơn
TYPE1_KEYWORDS = [
    "Thời gian vào trạm", "Thời gian ra trạm",
    "Biển số xe", "Id trạm vào", "Id trạm ra",
    "Trạng thái", "RFID",
    "Mã giao dịch", "Đơn vị"
]

TYPE2_KEYWORDS = [
    "Mã giao dịch:", "EPC:", "TG vào:", "TG Ra:", "Id trạm vào:",
    "Loại vé:", "Giá tiền:", "Boo:", "Làn vào:", "Làn ra:"
]


def detect_receipt_type(text):
    """
    Phát hiện loại hóa đơn dựa trên cấu trúc văn bản:
    - Loại 1: Mỗi label trên 1 dòng, giá trị ở dòng kế tiếp (dạng dọc)
    - Loại 2: key: value cùng dòng (VETC app)
    """
    # Dấu ':' trong datetime (HH:MM:SS, HH:MM) KHÔNG phải key:value
    # Chỉ đếm dòng có ':' mà KHÔNG phải datetime và không phải URL
    datetime_pattern = re.compile(r'\d{1,2}:\d{2}')
    url_pattern = re.compile(r'https?://|www\.')

    kv_lines = 0
    total_lines = 0
    for l in text.splitlines():
        ls = l.strip()
        if not ls:
            continue
        total_lines += 1
        if ':' in ls and len(ls) < 80:
            # Bỏ qua nếu dấu ':' chỉ xuất hiện trong datetime hoặc URL
            # Dòng key:value thật sự phải có phần trước ':' là text chữ (label)
            colon_idx = ls.index(':')
            before_colon = ls[:colon_idx].strip()
            # Nếu phần trước dấu ':' chứa chữ (không phải toàn số/ký tự đặc biệt)
            # và không phải dạng HH:MM:SS
            if (re.search(r'[a-zA-ZàáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđĐ]', before_colon)
                    and not datetime_pattern.search(before_colon)
                    and not url_pattern.search(ls)
                    and len(before_colon) > 1):
                kv_lines += 1

    # Nếu > 35% dòng có dạng key:value thật sự → Loại 2
    if total_lines > 0 and kv_lines / total_lines > 0.35:
        return "type2_vetc"

    # Kiểm tra keyword đặc trưng Type 1
    text_lower = text.lower()
    type1_score = sum(1 for kw in TYPE1_KEYWORDS if kw.lower() in text_lower)
    if type1_score >= 2:
        return "type1_web"

    # Default: type2 nếu không xác định được
    return "type2_vetc"


def parse_type1_web(text):
    """
    Parser cho Loại 1: Giao diện web dọc.
    Format: Label trên 1 dòng (center), value ở dòng TIẾP THEO.
    Ví dụ:
        Mã giao dịch
        16808431
        Trạng thái
        PENDING
    """
    data = {}
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # Mapping: label chuẩn hoá → key trong data
    label_map = {
        "mã giao dịch": "Mã giao dịch",
        "ma giao dich": "Mã giao dịch",
        "trạng thái": "Trạng thái",
        "trang thai": "Trạng thái",
        "biển số xe": "Biển số",
        "biển số": "Biển số",
        "bien so xe": "Biển số",
        "bien so": "Biển số",
        "rfid": "EPC",
        "epc": "EPC",
        "id trạm vào": "Id trạm vào",
        "id tram vao": "Id trạm vào",
        "id trạm ra": "Id trạm ra",
        "id tram ra": "Id trạm ra",
        "trạm vào": "Trạm vào",
        "tram vao": "Trạm vào",
        "trạm ra": "Trạm ra",
        "tram ra": "Trạm ra",
        "thời gian vào trạm": "Thời gian vào",
        "thoi gian vao tram": "Thời gian vào",
        "thời gian vào": "Thời gian vào",
        "thời gian ra trạm": "Thời gian ra",
        "thoi gian ra tram": "Thời gian ra",
        "thời gian ra": "Thời gian ra",
        "làn vào": "Làn vào",
        "lan vao": "Làn vào",
        "làn ra": "Làn ra",
        "lan ra": "Làn ra",
        "đơn vị": "Đơn vị",
        "don vi": "Đơn vị",
        "giá tiền": "Giá tiền",
        "gia tien": "Giá tiền",
        "loại vé": "Loại vé",
    }

    # Chuỗi các label để phát hiện bằng fuzzy nếu regex không khớp
    all_label_keys = list(label_map.keys())

    i = 0
    while i < len(lines):
        line = lines[i]
        line_lower = line.lower()

        # Thử exact match trước
        matched_key = None
        for label_norm, field_name in label_map.items():
            if label_norm in line_lower:
                matched_key = field_name
                break

        # Nếu không match, thử fuzzy match
        if matched_key is None:
            clean = re.sub(r'[^a-z0-9àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ\s]', '', line_lower).strip()
            fuzzy_matches = difflib.get_close_matches(clean, all_label_keys, n=1, cutoff=0.75)
            if fuzzy_matches:
                matched_key = label_map[fuzzy_matches[0]]

        if matched_key and i + 1 < len(lines):
            # Thu thập tất cả label fragments phía sau (OCR hay cắt text label thành nhiều dòng)
            # Ví dụ: "Thời gian ra" / "trạm" → cả hai là label, value ở dòng tiếp theo nữa
            j = i + 1
            # Bỏ qua các dòng là label-fragment: ngắn, không có số, và là substring của 1 label key
            while j < len(lines):
                frag = lines[j].strip()
                frag_lower = frag.lower()
                # Là label fragment nếu: không chứa số, độ dài ngắn, và xuất hiện bên trong ít nhất 1 label key
                is_fragment = (
                    len(frag.split()) <= 3           # tối đa 3 từ
                    and not re.search(r'\d', frag)   # không có số (value thường có số/chữ hoa)
                    and len(frag) <= 20              # không quá dài
                    and any(frag_lower in lbl for lbl in label_map.keys())  # là part of a label
                )
                if is_fragment:
                    j += 1  # Bỏ qua fragment, tìm dòng tiếp theo
                else:
                    break   # Dòng này không phải fragment → đây là value

            if j < len(lines):
                value_line = lines[j]
                value_lower = value_line.lower()
                is_value_a_label = any(lbl in value_lower for lbl in label_map.keys())
                if not is_value_a_label and value_line not in data.values():
                    data[matched_key] = value_line
                    i = j + 1  # Tiếp tục từ sau value_line
                    continue

        # Fallback: thử lấy value từ CÙNG dòng (nếu OCR merge label+value)
        # Ví dụ: 'Thời gian ra trạm: 25/03/2026 12:04:47'
        if matched_key:
            inline_match = re.split(r'[:;]\s*', line, maxsplit=1)
            if len(inline_match) == 2 and inline_match[1].strip():
                val = inline_match[1].strip()
                # Chỉ lấy nếu value không phải label khác
                if val.lower() not in label_map:
                    data[matched_key] = val
                    i += 1
                    continue


        i += 1

    return data


def parse_type2_vetc(text):
    """
    Parser cho Loại 2: VETC app — format key: value cùng dòng.
    Ví dụ:
        Mã giao dịch: 3078714761
        Trạng thái: PENDING
        Biển số: 51D-152.83T
    """
    data = {}

    # Mapping pattern → field name chuẩn
    # Lưu ý: các pattern phải xử lý cả trường hợp OCR nhận dạng sai dấu tiếng Việt
    heuristics = {
        "Mã giao dịch":  [r"M[ãa\*]?[ \t]*giao[ \t]*d[ịi]ch", r"M[ãa][ \t]*GD", r"M[ãa][ \t]*v[eé]"],
        "Trạng thái":    [r"Tr[ạa]ng[ \t]*th[áa]i", r"T[ìi]nh[ \t]*tr[ạa]ng"],
        # Biển số: bắt cả dạng 'Biến số', 'Bien so', 'Biển số xe', 'BSX'
        "Biển số":       [
            r"Bi[eêếềệểễ][nń][ \t]*s[oôốồổỗộ][ \t]*xe?",
            r"Bi[eê]n[ \t]*so",
            r"\bBKS\b",
            r"\bBSX\b",
            r"Bi[ểe]n[ \t]*ki[eê]m",
        ],
        "EPC":           [r"\bEPC\b", r"\bRFID\b", r"M[ãa][ \t]*th[ẻe]"],
        "TG vào":        [r"TG[ \t]*v[àa]o", r"Gi[ờo][ \t]*v[àa]o", r"Th[ờo]i[ \t]*gian[ \t]*v[àa]o"],
        # Id trạm vào phải đứng trước Trạm vào để không bị bắt nhầm
        "Id trạm vào":   [r"Id[ \t]*tr[ạa]m[ \t]*v[àa]o"],
        "Trạm vào":      [r"Tr[ạa]m[ \t]*v[àa]o"],
        "Làn vào":       [r"L[àa]n[ \t]*v[àa]o"],
        # TG Ra: chỉ khớp 'TG Ra' chứ KHÔNG khớp 'Id trạm ra'
        "TG ra":         [r"TG[ \t]*[Rr]a\b", r"Gi[ờo][ \t]*[Rr]a\b", r"Th[ờo]i[ \t]*gian[ \t]*[Rr]a\b"],
        "Id trạm ra":    [r"Id[ \t]*tr[ạa]m[ \t]*ra"],
        "Trạm ra":       [r"Tr[ạa]m[ \t]*ra"],
        # Làn ra: dùng word boundary để không bắt 'Làn vào'
        "Làn ra":        [r"L[àa]n[ \t]*ra\b"],
        "Loại vé":       [r"Lo[ạa]i[ \t]*v[eé]"],
        "Giá tiền":      [r"Gi[áa][ \t]*ti[ềe]n", r"S[ốo][ \t]*ti[ềe]n", r"T[ổo]ng[ \t]*c[ộo]ng"],
        "Đơn vị":        [r"[ĐDd][ơo]n[ \t]*v[ịi]", r"\bBoo\b"],
    }

    delimiter = r"[\s]*[:;.\-][\s]*"

    for field_name, patterns in heuristics.items():
        for pattern in patterns:
            # Patterns already use [ \t]* explicitly; no need to replace \s
            full_pattern = r"^[^\w\n]*" + pattern + delimiter + r"(.+)"
            match = re.search(full_pattern, text, re.IGNORECASE | re.MULTILINE | re.UNICODE)
            if match:
                val = match.group(1).strip()
                # Loại bỏ trailing garbage
                val = re.sub(r'\s*[|\\].*$', '', val).strip()
                if val:
                    data[field_name] = val
                    break

    return data


def post_process_data(data, receipt_type):
    """Áp dụng sửa lỗi OCR sau khi parse."""
    # Trường thuần số
    numeric_fields = [
        "Mã giao dịch", "Id trạm vào", "Id trạm ra",
        "Giá tiền", "Làn vào", "Làn ra", "Loại vé"
    ]
    for field in numeric_fields:
        if field in data and data[field]:
            data[field] = correct_numeric_ocr(data[field])

    # EPC / RFID
    for epc_field in ["EPC", "RFID"]:
        if epc_field in data and data[epc_field]:
            data[epc_field] = correct_hex_ocr(data[epc_field])

    # Biển số xe: 2 ký tự đầu là tỉnh (số), phần sau giữ nguyên
    if "Biển số" in data:
        bs = data["Biển số"].strip()
        if len(bs) >= 3:
            prefix = correct_numeric_ocr(bs[:2])
            data["Biển số"] = prefix + bs[2:]

    # Thời gian
    for time_field in ["TG vào", "TG ra", "Thời gian vào", "Thời gian ra"]:
        if time_field in data:
            data[time_field] = normalize_datetime(data[time_field])

    return data


def parse_receipt(text):
    """
    Entry point: tự động phát hiện loại hóa đơn và gọi parser phù hợp.
    Trả về (data: dict, receipt_type: str)
    """
    # Normalize Unicode sang NFC để tránh lỗi dấu tiếng Việt (NFD vs NFC)
    text = unicodedata.normalize('NFC', text)

    receipt_type = detect_receipt_type(text)

    if receipt_type == "type1_web":
        data = parse_type1_web(text)
    else:
        data = parse_type2_vetc(text)

    # Nếu parser chính trả về rỗng, thử parser kia (fallback)
    if len(data) < 3:
        if receipt_type == "type1_web":
            data_alt = parse_type2_vetc(text)
            if len(data_alt) > len(data):
                data = data_alt
                receipt_type = "type2_vetc"
        else:
            data_alt = parse_type1_web(text)
            if len(data_alt) > len(data):
                data = data_alt
                receipt_type = "type1_web"

    # ── Fallback cuối: quét trực tiếp biển số xe bằng pattern cứng ──
    # VN plate format: 2 số/chữ + chữ + '-' + 3-4 số + '.' + 2-3 ký tự
    # Ví dụ: 51D-152.83T, 74H-022.55V, 50E-38610V, 30A-123.45
    if "Biển số" not in data:
        plate_pattern = r'\b(\d{2}[A-Z]\d?\s*[-–]\s*\d{3,4}[.\s]?\d{1,3}[A-Z]?)\b'
        for line in text.splitlines():
            m = re.search(plate_pattern, line, re.IGNORECASE)
            if m:
                data["Biển số"] = m.group(1).strip()
                break

    data = post_process_data(data, receipt_type)
    return data, receipt_type


# ─────────────────────────────────────────────────────────────────
# GUI
# ─────────────────────────────────────────────────────────────────

class NextLevelOCRScanner(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Toll Receipt OCR — VETC Scanner")
        self.geometry("1300x800")

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=4)
        self.grid_columnconfigure(2, weight=2)
        self.grid_rowconfigure(0, weight=1)
        self.latest_metadata = None
        self.latest_receipt_type = "unknown"

        # --- LEFT PANEL ---
        self.sidebar_frame = ctk.CTkFrame(self, corner_radius=10, fg_color="#1E293B")
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        self.logo_label = ctk.CTkLabel(
            self.sidebar_frame, text="Receipt Scanner\nVETC PRO",
            font=ctk.CTkFont(size=22, weight="bold")
        )
        self.logo_label.pack(pady=20, padx=20)

        self.btn_select_dir = ctk.CTkButton(
            self.sidebar_frame, text="📁 Load Directory",
            fg_color="#3B82F6", hover_color="#2563EB",
            command=lambda: self.load_directory()
        )
        self.btn_select_dir.pack(fill='x', padx=20, pady=10)

        self.btn_select_file = ctk.CTkButton(
            self.sidebar_frame, text="🖼️ Load File",
            fg_color="#6366F1", hover_color="#4F46E5",
            command=self.load_file
        )
        self.btn_select_file.pack(fill='x', padx=20, pady=10)

        self.btn_load_zip = ctk.CTkButton(
            self.sidebar_frame, text="📦 Load ZIP",
            fg_color="#F59E0B", hover_color="#D97706",
            command=self.load_zip_file
        )
        self.btn_load_zip.pack(fill='x', padx=20, pady=10)

        self.file_menu_label = ctk.CTkLabel(self.sidebar_frame, text="Available Images:")
        self.file_menu_label.pack(pady=(20, 5), padx=20, anchor='w')

        self.scrollable_file_list = ctk.CTkScrollableFrame(self.sidebar_frame, fg_color="transparent")
        self.scrollable_file_list.pack(fill='both', expand=True, padx=20, pady=10)

        # --- MIDDLE PANEL ---
        self.preview_frame = ctk.CTkFrame(self, corner_radius=10)
        self.preview_frame.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)

        self.preview_label = ctk.CTkLabel(self.preview_frame, text="No Image Selected", font=ctk.CTkFont(size=14))
        self.preview_label.pack(fill='both', expand=True, padx=20, pady=20)

        # --- RIGHT PANEL ---
        self.results_frame = ctk.CTkFrame(self, corner_radius=10)
        self.results_frame.grid(row=0, column=2, sticky="nsew", padx=10, pady=10)
        self.results_frame.grid_rowconfigure(1, weight=1)
        self.results_frame.grid_columnconfigure(0, weight=1)

        self.results_title = ctk.CTkLabel(
            self.results_frame, text="Scan Results",
            font=ctk.CTkFont(size=20, weight="bold")
        )
        self.results_title.grid(row=0, column=0, pady=(20, 5), padx=20, sticky="w")

        self.tabview = ctk.CTkTabview(self.results_frame)
        self.tabview.grid(row=1, column=0, sticky="nsew", padx=20, pady=5)
        self.tabview.add("Structured Data")
        self.tabview.add("Raw OCR Text")

        self.tabview.tab("Structured Data").grid_rowconfigure(0, weight=1)
        self.tabview.tab("Structured Data").grid_columnconfigure(0, weight=1)
        self.smart_data_box = ctk.CTkTextbox(
            self.tabview.tab("Structured Data"),
            fg_color="#0F111A", text_color="#00FFAA",
            font=ctk.CTkFont(family="Courier New", size=13),
            corner_radius=8
        )
        self.smart_data_box.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        self.smart_data_box.insert("0.0", "--- No Data ---\nHit SCAN to extract structured receipt data.")
        self.smart_data_box.configure(state="disabled")

        self.tabview.tab("Raw OCR Text").grid_rowconfigure(0, weight=1)
        self.tabview.tab("Raw OCR Text").grid_columnconfigure(0, weight=1)
        self.raw_data_box = ctk.CTkTextbox(
            self.tabview.tab("Raw OCR Text"),
            fg_color="#1E1E1E", text_color="white",
            font=ctk.CTkFont(size=12), corner_radius=8
        )
        self.raw_data_box.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        self.raw_data_box.insert("0.0", "Waiting for input...\n")
        self.raw_data_box.configure(state="disabled")

        self.progress_bar = ctk.CTkProgressBar(
            self.results_frame, mode="indeterminate",
            height=4, fg_color="#333333", progress_color="#10B981"
        )
        self.progress_bar.grid(row=2, column=0, sticky="ew", padx=20, pady=(10, 0))
        self.progress_bar.set(0)

        self.actions_frame = ctk.CTkFrame(self.results_frame, fg_color="transparent")
        self.actions_frame.grid(row=3, column=0, sticky="ew", padx=20, pady=10)
        self.actions_frame.grid_columnconfigure((0, 1), weight=1)

        self.btn_copy = ctk.CTkButton(
            self.actions_frame, text="📄 Copy Data",
            fg_color="#4B5563", hover_color="#374151",
            command=self.copy_data_to_clipboard
        )
        self.btn_copy.grid(row=0, column=0, sticky="ew", padx=(0, 5))

        self.btn_export = ctk.CTkButton(
            self.actions_frame, text="📊 Export CSV",
            fg_color="#B91C1C", hover_color="#991B1B",
            command=self.export_to_csv
        )
        self.btn_export.grid(row=0, column=1, sticky="ew", padx=(5, 0))

        self.btn_scan = ctk.CTkButton(
            self.results_frame, text="START SCAN 🚀",
            font=ctk.CTkFont(size=16, weight="bold"),
            height=50, fg_color="#10B981", hover_color="#059669",
            corner_radius=8, command=self.start_scan_thread
        )
        self.btn_scan.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 20))

        self.image_files = []
        self.file_buttons = {}
        self.current_image_path = None

        if os.path.exists(r"C:\Users\ETC\Downloads\2303"):
            self.load_directory(r"C:\Users\ETC\Downloads\2303")

    def load_directory(self, dir_path=None):
        if not dir_path:
            dir_path = ctk.filedialog.askdirectory()
        if dir_path:
            for widget in self.scrollable_file_list.winfo_children():
                widget.destroy()

            self.image_files = []
            self.file_buttons = {}
            for f in sorted(os.listdir(dir_path)):
                if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff')):
                    full_path = os.path.join(dir_path, f)
                    self.image_files.append(full_path)

            for path in self.image_files:
                fname = os.path.basename(path)
                btn = ctk.CTkButton(
                    self.scrollable_file_list, text=fname,
                    anchor="w", fg_color="transparent",
                    text_color="white", hover_color="gray50",
                    command=lambda p=path: self.display_image(p)
                )
                btn.pack(fill='x', pady=2)
                self.file_buttons[path] = btn

            if self.image_files:
                self.display_image(self.image_files[0])

    def load_file(self):
        file_path = ctk.filedialog.askopenfilename(
            filetypes=[("Image Files", "*.png;*.jpg;*.jpeg;*.bmp;*.tiff")]
        )
        if file_path:
            for widget in self.scrollable_file_list.winfo_children():
                widget.destroy()
            self.image_files = [file_path]
            self.file_buttons = {}
            fname = os.path.basename(file_path)
            btn = ctk.CTkButton(
                self.scrollable_file_list, text=fname,
                anchor="w", fg_color="transparent",
                text_color="white", hover_color="gray50",
                command=lambda p=file_path: self.display_image(p)
            )
            btn.pack(fill='x', pady=2)
            self.file_buttons[file_path] = btn
            self.display_image(file_path)

    def load_zip_file(self):
        zip_path = ctk.filedialog.askopenfilename(filetypes=[("ZIP Archives", "*.zip")])
        if zip_path:
            extract_dir = os.path.splitext(zip_path)[0] + "_extracted"
            try:
                if os.path.exists(extract_dir):
                    shutil.rmtree(extract_dir)
                os.makedirs(extract_dir, exist_ok=True)
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(extract_dir)
                self.load_directory(extract_dir)
            except Exception as e:
                import tkinter.messagebox as messagebox
                messagebox.showerror("Error", f"Failed to extract ZIP:\n{e}")

    def display_image(self, path):
        self.current_image_path = path

        for p, btn in self.file_buttons.items():
            btn.configure(fg_color="gray30" if p == path else "transparent")

        try:
            img = Image.open(path)
            img.thumbnail((800, 900), Image.Resampling.LANCZOS)
            tk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(img.width, img.height))
            self.preview_label.configure(image=tk_img, text="")
            self.preview_label.image = tk_img

            self.update_textbox(self.smart_data_box, "--- Ready to parse ---\nClick START SCAN to begin.")
            self.update_textbox(self.raw_data_box, "Image loaded. Ready for OCR.")
        except Exception as e:
            self.preview_label.configure(image=None, text=f"Error: {e}")

    def update_textbox(self, textbox, text):
        textbox.configure(state="normal")
        textbox.delete("0.0", "end")
        textbox.insert("0.0", text)
        textbox.configure(state="disabled")

    def start_scan_thread(self):
        if not self.current_image_path:
            return
        self.btn_scan.configure(state="disabled", text="SCANNING...")
        self.progress_bar.start()
        self.update_textbox(
            self.smart_data_box,
            "🔬 Đang chạy Dual-Engine OCR...\n\n"
            "• Tesseract LSTM (cấu trúc dòng)\n"
            "• EasyOCR CRNN  (nhận dạng ký tự)\n\n"
            "Lần đầu EasyOCR cần tải model (~1 phút)..."
        )
        self.update_textbox(self.raw_data_box, "⚙️ Đang xử lý...")
        t = threading.Thread(target=self.run_ocr_scan)
        t.daemon = True
        t.start()

    def run_ocr_scan(self):
        try:
            original_img = Image.open(self.current_image_path)

            # Preprocessing riêng cho mỗi engine
            img_tess = preprocess_image(original_img)
            img_easy = preprocess_for_easyocr(original_img)

            tess_text = ""
            easy_text = ""
            easy_conf = 0.0
            engine_label = "Tesseract"

            def run_tesseract_task():
                return run_tesseract(img_tess)

            def run_easyocr_task():
                return run_easyocr(img_easy)

            with ThreadPoolExecutor(max_workers=2) as executor:
                fut_tess = executor.submit(run_tesseract_task)
                fut_easy = executor.submit(run_easyocr_task)
                tess_text = fut_tess.result()
                easy_text, easy_conf, _ = fut_easy.result()

            if easy_text.strip():
                engine_label = f"Tesseract + EasyOCR ✅  (conf: {easy_conf * 100:.1f}%)"
                extracted_text = merge_dual_ocr(tess_text, easy_text, easy_conf)
            else:
                engine_label = "Tesseract only"
                extracted_text = tess_text

            # Parse
            metadata, receipt_type = parse_receipt(extracted_text)

            # Save
            if metadata:
                save_to_db(metadata, extracted_text, receipt_type)

            # Format output
            type_label = {
                "type1_web": "🌐 Loại 1 — Giao diện web (nhãn dọc)",
                "type2_vetc": "📱 Loại 2 — VETC App (key:value)",
            }.get(receipt_type, "❓ Không xác định")

            smart_out = f"🔬 Engine: {engine_label}\n"
            smart_out += f"📋 Loại hóa đơn: {type_label}\n"
            smart_out += "─" * 40 + "\n\n"

            if metadata:
                for key, val in metadata.items():
                    smart_out += f"► {key}:\n   [ {val} ]\n\n"
            else:
                smart_out += "Không tìm thấy dữ liệu hóa đơn. Hãy kiểm tra Raw OCR Text."

            raw_out = "=== MERGED OCR TEXT ===\n"
            raw_out += extracted_text.strip() if extracted_text.strip() else "[No text found]"
            if easy_text.strip():
                raw_out += "\n\n=== EASYOCR RAW ===\n" + easy_text.strip()

            self.after(0, self._finish_scan_sync, smart_out.strip(), raw_out, metadata, receipt_type)

        except pytesseract.TesseractError as e:
            if 'Failed loading language' in str(e):
                err_msg = "Language Pack Error: vie.traineddata missing!"
            else:
                err_msg = str(e)
            self.after(0, self._finish_scan_sync, "ERROR\n" + err_msg, err_msg, None, "unknown")
        except Exception as e:
            self.after(0, self._finish_scan_sync, "CRITICAL ERROR\n" + str(e), str(e), None, "unknown")

    def _finish_scan_sync(self, smart_text, raw_text, metadata, receipt_type):
        self.update_textbox(self.smart_data_box, smart_text)
        self.update_textbox(self.raw_data_box, raw_text)
        self.latest_metadata = metadata
        self.latest_receipt_type = receipt_type
        self.progress_bar.stop()
        self.progress_bar.set(0)
        self.btn_scan.configure(state="normal", text="START SCAN 🚀")

    def copy_data_to_clipboard(self):
        metadata = getattr(self, 'latest_metadata', None)
        if metadata:
            text_to_copy = json.dumps(metadata, ensure_ascii=False, indent=4)
            self.clipboard_clear()
            self.clipboard_append(text_to_copy)
            original = self.btn_copy.cget("text")
            self.btn_copy.configure(text="✅ Copied!")
            self.after(2000, lambda: self.btn_copy.configure(text=original))
        else:
            self.btn_copy.configure(text="⚠️ No Data")
            self.after(2000, lambda: self.btn_copy.configure(text="📄 Copy Data"))

    def export_to_csv(self):
        metadata = getattr(self, 'latest_metadata', None)
        if not metadata:
            self.btn_export.configure(text="⚠️ No Data")
            self.after(2000, lambda: self.btn_export.configure(text="📊 Export CSV"))
            return

        default_name = f"receipt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        file_path = ctk.filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=default_name,
            filetypes=[("CSV files", "*.csv")]
        )
        if file_path:
            try:
                with open(file_path, 'w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.writer(f)
                    writer.writerow(metadata.keys())
                    writer.writerow(metadata.values())
                original = self.btn_export.cget("text")
                self.btn_export.configure(text="✅ Exported!")
                self.after(2000, lambda: self.btn_export.configure(text=original))
            except Exception as e:
                import tkinter.messagebox as messagebox
                messagebox.showerror("Export Error", f"Failed:\n{e}")


if __name__ == "__main__":
    init_db()
    app = NextLevelOCRScanner()
    app.mainloop()
