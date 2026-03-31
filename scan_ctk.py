import sys
import os
import threading
import re
import zipfile
import unicodedata
import shutil
import logging
import logging.handlers
import time
import math
import functools
import tkinter as tk
import tkinter.messagebox as messagebox
import tkinter.ttk as ttk
import numpy as np  # type: ignore
import csv
import json
import difflib
import sqlite3
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageTk, ImageEnhance, ImageFilter, ImageOps  # type: ignore

try:
    import pytesseract  # type: ignore
    import customtkinter as ctk  # type: ignore
except ImportError:
    print("Missing required libraries. Please run in your terminal:")
    print("pip install Pillow pytesseract customtkinter opencv-python")
    sys.exit(1)

try:
    import cv2  # type: ignore
except ImportError:
    print("Missing opencv-python. Please run: pip install opencv-python")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────
# App Metadata
# ─────────────────────────────────────────────────────────────────
APP_NAME    = "VETCScanner"
APP_VERSION = "3.0.0"
APP_TITLE   = f"Toll Receipt OCR — VETC Enterprise v{APP_VERSION}"

# Supported image file extensions
_SUPPORTED_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.webp')

# Image preview zoom constraints
_MIN_ZOOM = 0.1
_MAX_ZOOM = 8.0

# Fallback preview panel dimensions (pixels) used when the widget has not yet been rendered
_DEFAULT_PREVIEW_W = 780
_DEFAULT_PREVIEW_H = 860

# Sidebar thumbnail dimensions
_THUMB_W = 68
_THUMB_H = 46

# Maximum recent files remembered
_RECENT_FILES_MAX = 10

# Per-field color theming for the structured data panel
_FIELD_COLORS: dict = {
    "Mã giao dịch":  "#FFD700",   # gold
    "Biển số":       "#60A5FA",   # sky blue
    "EPC":           "#A78BFA",   # violet
    "Giá tiền":      "#34D399",   # emerald
    "Trạng thái":    "#F87171",   # red
    "TG vào":        "#FB923C",   # orange
    "TG ra":         "#FB923C",
    "Thời gian vào": "#FB923C",
    "Thời gian ra":  "#FB923C",
    "Trạm vào":      "#38BDF8",   # light blue
    "Trạm ra":       "#38BDF8",
    "Id trạm vào":   "#94A3B8",   # slate
    "Id trạm ra":    "#94A3B8",
    "Làn vào":       "#C084FC",   # purple
    "Làn ra":        "#C084FC",
    "Loại vé":       "#4ADE80",   # green
    "Đơn vị":        "#F9A8D4",   # pink
}

# ─────────────────────────────────────────────────────────────────
# Configuration Management
# ─────────────────────────────────────────────────────────────────
_CONFIG_DIR  = Path.home() / ".vetc_scanner"
_CONFIG_FILE = _CONFIG_DIR / "config.json"
_LOG_DIR     = _CONFIG_DIR / "logs"

_DEFAULT_CONFIG: dict = {
    "tesseract_path":     "",
    "db_path":            str(_CONFIG_DIR / "receipts.db"),
    "theme":              "Dark",
    "color_theme":        "blue",
    "last_directory":     "",
    "auto_scan_on_load":  False,
    "log_level":          "INFO",
    "max_log_size_mb":    10,
    "log_backup_count":   5,
    "export_directory":   str(Path.home()),
    "recent_files":       [],
    "show_thumbnails":    True,
    "auto_deskew":        False,
    "zoom_step":          1.2,
    "window_geometry":    "",
}


def load_config() -> dict:
    """Load config from disk, merging missing keys from defaults."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    if _CONFIG_FILE.exists():
        try:
            with open(_CONFIG_FILE, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            for k, v in _DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except Exception as exc:
            print(f"WARNING: Could not load config ({exc}). Using defaults.")
    return _DEFAULT_CONFIG.copy()


def save_config(cfg: dict) -> None:
    """Persist config to disk."""
    try:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=4, ensure_ascii=False)
    except Exception as exc:
        logging.getLogger(APP_NAME).error("Failed to save config: %s", exc)


_config = load_config()

# ─────────────────────────────────────────────────────────────────
# Logging Setup
# ─────────────────────────────────────────────────────────────────

def _setup_logging(cfg: dict) -> logging.Logger:
    log_level   = getattr(logging, cfg.get("log_level", "INFO"), logging.INFO)
    log_path    = _LOG_DIR / f"{APP_NAME}.log"
    max_bytes   = cfg.get("max_log_size_mb", 10) * 1024 * 1024
    backup_cnt  = cfg.get("log_backup_count", 5)

    _log = logging.getLogger(APP_NAME)
    _log.setLevel(log_level)
    if not _log.handlers:
        fh = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=max_bytes, backupCount=backup_cnt, encoding='utf-8'
        )
        fh.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(funcName)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        _log.addHandler(fh)
        _log.addHandler(ch)
    return _log


logger = _setup_logging(_config)
logger.info("Starting %s %s", APP_NAME, APP_VERSION)

# ─────────────────────────────────────────────────────────────────
# Tesseract Auto-Detection
# ─────────────────────────────────────────────────────────────────
_TESSERACT_CANDIDATES = [
    r'C:\Program Files\Tesseract-OCR\tesseract.exe',
    r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
    '/usr/bin/tesseract',
    '/usr/local/bin/tesseract',
    '/opt/homebrew/bin/tesseract',
]


def configure_tesseract(cfg: dict) -> None:
    """Set Tesseract binary path from config or auto-detect."""
    tess_path = cfg.get("tesseract_path", "")
    if tess_path and os.path.exists(tess_path):
        pytesseract.pytesseract.tesseract_cmd = tess_path
        logger.info("Tesseract set from config: %s", tess_path)
        return
    for p in _TESSERACT_CANDIDATES:
        if os.path.exists(p):
            pytesseract.pytesseract.tesseract_cmd = p
            cfg["tesseract_path"] = p
            logger.info("Tesseract auto-detected: %s", p)
            return
    logger.warning("Tesseract binary not found. Set path in Settings.")


configure_tesseract(_config)

# ─────────────────────────────────────────────────────────────────
# Database Manager
# ─────────────────────────────────────────────────────────────────

class DatabaseManager:
    """Thread-safe SQLite manager with schema migration and audit support."""

    # Columns that must exist (added in v2 migrations)
    _MIGRATE_COLUMNS = [
        ("receipt_type",       "TEXT"),
        ("station_out",        "TEXT"),
        ("lane_out",           "TEXT"),
        ("ticket_type",        "TEXT"),
        ("unit",               "TEXT"),
        ("processing_time_ms", "INTEGER"),
        ("image_path",         "TEXT"),
        ("ocr_engine",         "TEXT"),
        ("ocr_confidence",     "REAL"),
    ]

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock   = threading.Lock()
        logger.info("DatabaseManager using: %s", db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        """Create tables and run column migrations."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS scans (
                        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                        scan_time          TEXT,
                        receipt_type       TEXT,
                        transaction_code   TEXT,
                        license_plate      TEXT,
                        price              TEXT,
                        status             TEXT,
                        epc                TEXT,
                        time_in            TEXT,
                        station_in         TEXT,
                        station_in_id      TEXT,
                        lane_in            TEXT,
                        time_out           TEXT,
                        station_out        TEXT,
                        station_out_id     TEXT,
                        lane_out           TEXT,
                        ticket_type        TEXT,
                        unit               TEXT,
                        raw_text           TEXT,
                        processing_time_ms INTEGER,
                        image_path         TEXT,
                        ocr_engine         TEXT,
                        ocr_confidence     REAL
                    )
                ''')
                existing = {row[1] for row in cur.execute("PRAGMA table_info(scans)")}
                # Validate col_name/col_type against the known-safe whitelist before DDL
                _valid_types = {"TEXT", "INTEGER", "REAL", "BLOB", "NUMERIC"}
                for col_name, col_type in self._MIGRATE_COLUMNS:
                    if col_name not in existing:
                        if not re.match(r'^[a-z_]+$', col_name) or col_type.upper() not in _valid_types:
                            logger.warning("Skipping unsafe migration column: %s %s", col_name, col_type)
                            continue
                        cur.execute(f"ALTER TABLE scans ADD COLUMN {col_name} {col_type}")
                        logger.info("DB migration: added column '%s'", col_name)
                conn.commit()
                logger.info("Database schema ready.")
            except Exception as exc:
                logger.error("Schema init error: %s", exc)
                raise
            finally:
                conn.close()

    def save_scan(self, data: dict, raw_text: str, receipt_type: str = "unknown",
                  processing_time_ms: int = 0, image_path: str = "",
                  ocr_engine: str = "", ocr_confidence: float = 0.0) -> int:
        """Insert one scan record. Returns new row id or -1 on error."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute('''
                    INSERT INTO scans (
                        scan_time, receipt_type, transaction_code, license_plate,
                        price, status, epc,
                        time_in, station_in, station_in_id, lane_in,
                        time_out, station_out, station_out_id, lane_out,
                        ticket_type, unit, raw_text,
                        processing_time_ms, image_path, ocr_engine, ocr_confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    raw_text,
                    processing_time_ms,
                    image_path,
                    ocr_engine,
                    ocr_confidence,
                ))
                conn.commit()
                row_id = cur.lastrowid
                logger.info("Scan saved — id=%d type=%s plate=%s time=%dms",
                            row_id, receipt_type,
                            data.get('Biển số', 'N/A'), processing_time_ms)
                return row_id
            except Exception as exc:
                logger.error("DB save error: %s", exc)
                return -1
            finally:
                conn.close()

    def get_recent_scans(self, limit: int = 200, search: str = "") -> list:
        """Return recent scan rows (dicts), optionally filtered by search term."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.cursor()
                if search:
                    # Escape LIKE wildcards so user input is treated as literal text
                    escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    q = f'%{escaped}%'
                    cur.execute('''
                        SELECT id, scan_time, receipt_type, transaction_code,
                               license_plate, price, status, ocr_engine, ocr_confidence,
                               processing_time_ms
                        FROM scans
                        WHERE transaction_code LIKE ? ESCAPE '\\'
                           OR license_plate LIKE ? ESCAPE '\\'
                           OR raw_text LIKE ? ESCAPE '\\'
                        ORDER BY id DESC LIMIT ?
                    ''', (q, q, q, limit))
                else:
                    cur.execute('''
                        SELECT id, scan_time, receipt_type, transaction_code,
                               license_plate, price, status, ocr_engine, ocr_confidence,
                               processing_time_ms
                        FROM scans ORDER BY id DESC LIMIT ?
                    ''', (limit,))
                return [dict(row) for row in cur.fetchall()]
            except Exception as exc:
                logger.error("DB query error: %s", exc)
                return []
            finally:
                conn.close()

    def get_statistics(self) -> dict:
        """Return aggregate DB statistics."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) AS total FROM scans")
                total = cur.fetchone()["total"]
                cur.execute("SELECT receipt_type, COUNT(*) AS cnt FROM scans GROUP BY receipt_type")
                by_type = {r["receipt_type"]: r["cnt"] for r in cur.fetchall()}
                cur.execute("SELECT AVG(processing_time_ms) AS avg_ms FROM scans WHERE processing_time_ms > 0")
                row = cur.fetchone()
                avg_ms = row["avg_ms"] if row and row["avg_ms"] else 0.0
                return {"total": total, "by_type": by_type, "avg_processing_ms": round(avg_ms)}
            except Exception as exc:
                logger.error("DB statistics error: %s", exc)
                return {}
            finally:
                conn.close()

    def get_today_stats(self) -> dict:
        """Return scan counts for today and this week."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.cursor()
                today_str = date.today().strftime('%Y-%m-%d')
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM scans WHERE scan_time LIKE ?",
                    (f"{today_str}%",))
                today_count = cur.fetchone()["cnt"]
                # Last 7 days
                cur.execute('''
                    SELECT DATE(scan_time) AS day, COUNT(*) AS cnt
                    FROM scans
                    WHERE scan_time >= DATE('now', '-6 days')
                    GROUP BY day ORDER BY day
                ''')
                week_by_day = {r["day"]: r["cnt"] for r in cur.fetchall()}
                return {"today": today_count, "week_by_day": week_by_day}
            except Exception as exc:
                logger.error("DB today stats error: %s", exc)
                return {"today": 0, "week_by_day": {}}
            finally:
                conn.close()

    def delete_scan(self, scan_id: int) -> bool:
        """Delete a single scan record by id. Returns True on success."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM scans WHERE id = ?", (scan_id,))
                conn.commit()
                logger.info("Deleted scan id=%d", scan_id)
                return True
            except Exception as exc:
                logger.error("DB delete error: %s", exc)
                return False
            finally:
                conn.close()

    def export_all_csv(self, file_path: str) -> int:
        """Dump all rows to CSV. Returns number of rows written."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute("SELECT * FROM scans ORDER BY id")
                rows = cur.fetchall()
                if not rows:
                    return 0
                with open(file_path, 'w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.writer(f)
                    writer.writerow([d[0] for d in cur.description])
                    writer.writerows(rows)
                logger.info("Exported %d rows to CSV: %s", len(rows), file_path)
                return len(rows)
            except Exception as exc:
                logger.error("CSV export error: %s", exc)
                return 0
            finally:
                conn.close()


# Singleton DB manager (path resolved after config is loaded)
_db = DatabaseManager(_config["db_path"])

# ─────────────────────────────────────────────────────────────────
# EasyOCR — Lazy-load
# ─────────────────────────────────────────────────────────────────
_easyocr_reader    = None
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
            _easyocr_reader    = easyocr.Reader(['vi', 'en'], gpu=False, verbose=False)
            _easyocr_available = True
            logger.info("EasyOCR reader loaded successfully.")
        except Exception as exc:
            logger.warning("EasyOCR unavailable: %s", exc)
            _easyocr_available = False
            return None
    return _easyocr_reader

# Apply CustomTkinter theme from config
ctk.set_appearance_mode(_config.get("theme", "Dark"))
ctk.set_default_color_theme(_config.get("color_theme", "blue"))

# ─────────────────────────────────────────────────────────────────
# Image Deskewing
# ─────────────────────────────────────────────────────────────────

def detect_skew_angle(gray_img: np.ndarray) -> float:
    """Detect the dominant text skew angle (degrees) in a grayscale image.

    Uses Probabilistic Hough Line Transform on a Canny-edge map.  Returns 0.0
    if no reliable angle is found or if the detected skew is negligible
    (< 0.5°).  The returned angle is clamped to ±45° to avoid catastrophic
    misdetections.
    """
    edges = cv2.Canny(gray_img, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(
        edges, rho=1, theta=np.pi / 180, threshold=100,
        minLineLength=gray_img.shape[1] // 5,
        maxLineGap=20,
    )
    if lines is None:
        return 0.0

    angles = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if x2 == x1:
            continue
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
        # Keep only near-horizontal lines (text baselines)
        if abs(angle) < 45:
            angles.append(angle)

    if not angles:
        return 0.0

    # Use median for robustness against outliers
    median_angle = float(np.median(angles))
    if abs(median_angle) < 0.5:
        return 0.0
    return max(-45.0, min(45.0, median_angle))


def deskew_image(pil_img: Image.Image) -> Image.Image:
    """Rotate *pil_img* to correct any detected text skew.

    Returns the corrected PIL image (RGB), or the original unchanged if the
    skew is negligible or detection fails.
    """
    try:
        gray = np.array(pil_img.convert("L"))
        angle = detect_skew_angle(gray)
        if angle == 0.0:
            return pil_img
        rotated = pil_img.rotate(-angle, expand=True, fillcolor=(255, 255, 255),
                                 resample=Image.Resampling.BICUBIC)
        logger.debug("Deskew: corrected %.2f°", angle)
        return rotated
    except Exception as exc:
        logger.warning("Deskew failed: %s", exc)
        return pil_img

# ─────────────────────────────────────────────────────────────────
# Image Preprocessing
# ─────────────────────────────────────────────────────────────────

def preprocess_image(img, auto_deskew: bool = False):
    """
    Preprocessing pipeline tối ưu cho hai loại screenshot điện thoại:
    - Loại 1: Nền trắng, chữ đen, layout dọc (giao diện web)
    - Loại 2: Nền trắng/xanh lá, text đen, layout key:value (VETC app)
    Các screenshot này thường rõ ràng hơn ảnh scan thật → cần xử lý nhẹ nhàng,
    tránh làm mờ hoặc méo chữ.

    If *auto_deskew* is True the image is skew-corrected before binarisation.
    """
    if auto_deskew:
        img = deskew_image(img)

    open_cv_image = np.array(img)

    # Convert to grayscale
    if len(open_cv_image.shape) == 3:
        gray = cv2.cvtColor(open_cv_image, cv2.COLOR_RGB2GRAY)
    else:
        gray = open_cv_image.copy()

    h, w = gray.shape

    # Upscale if image is small — higher threshold (1200px) catches more cases
    # Use scale=3 for very small images for better OCR quality
    if w < 1200:
        scale = 3 if w < 600 else 2
        gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_LANCZOS4)

    # Phân tích độ sáng trung bình để phát hiện nền tối / sáng
    mean_brightness = np.mean(gray)

    if mean_brightness > 180:
        # Ảnh sáng (screenshot điện thoại nền trắng): sharpen để tăng độ nét chữ
        kernel = np.array([[ 0, -1,  0],
                            [-1,  5, -1],
                            [ 0, -1,  0]], dtype=np.float32)
        enhanced = cv2.filter2D(gray, -1, kernel)
        enhanced = np.clip(enhanced, 0, 255).astype(np.uint8)
        # Light denoising to remove scanner/compression artifacts
        enhanced = cv2.fastNlMeansDenoising(enhanced, h=5, templateWindowSize=7, searchWindowSize=21)
    else:
        # Ảnh tối / scan thật: dùng CLAHE để tăng tương phản
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        enhanced = cv2.bilateralFilter(enhanced, d=7, sigmaColor=75, sigmaSpace=75)

    # Binarize: ngưỡng adaptive Gaussian để tách chữ khỏi nền không đều
    # blockSize nhỏ hơn (21) bắt được nét chữ nhỏ tốt hơn; C=8 ít agressive hơn
    binary = cv2.adaptiveThreshold(
        enhanced, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=21,
        C=8
    )

    # Morphological opening with a 2×2 kernel to remove isolated noise pixels
    # without breaking connected character strokes
    kernel_morph = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel_morph)

    return Image.fromarray(binary)


def preprocess_for_easyocr(img, auto_deskew: bool = False):
    """Preprocessing riêng cho EasyOCR: giữ ảnh màu (RGB) và tăng tương phản.
    EasyOCR được huấn luyện trên ảnh màu nên cho kết quả tốt hơn grayscale.

    If *auto_deskew* is True the image is skew-corrected first.
    """
    if auto_deskew:
        img = deskew_image(img)

    open_cv_image = np.array(img)

    # Keep colour — convert to BGR for OpenCV processing
    if len(open_cv_image.shape) == 3:
        bgr = cv2.cvtColor(open_cv_image, cv2.COLOR_RGB2BGR)
    else:
        bgr = cv2.cvtColor(open_cv_image, cv2.COLOR_GRAY2BGR)

    h, w = bgr.shape[:2]

    # Upscale small images — same threshold as Tesseract pipeline
    if w < 1200:
        scale = 3 if w < 600 else 2
        bgr = cv2.resize(bgr, (w * scale, h * scale), interpolation=cv2.INTER_LANCZOS4)

    # Use grayscale only for brightness analysis
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    mean_brightness = np.mean(gray)

    if mean_brightness > 180:
        # Light image: gentle unsharp-mask style sharpening on the colour image
        kernel = np.array([[ 0, -1,  0],
                            [-1,  5, -1],
                            [ 0, -1,  0]], dtype=np.float32)
        bgr = cv2.filter2D(bgr, -1, kernel)
        bgr = np.clip(bgr, 0, 255).astype(np.uint8)
    else:
        # Dark/scanned image: enhance contrast via CLAHE on the L channel (LAB space)
        lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
        l_ch, a_ch, b_ch = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        l_ch = clahe.apply(l_ch)
        lab = cv2.merge([l_ch, a_ch, b_ch])
        bgr = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    # Return as RGB for EasyOCR
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)

# ─────────────────────────────────────────────────────────────────
# OCR Engines
# ─────────────────────────────────────────────────────────────────

def run_tesseract(pil_img):
    """Chạy Tesseract với config tối ưu cho hai loại hóa đơn.

    Runs PSM 6 (uniform block) and PSM 11 (sparse text) in parallel and returns
    whichever produces more non-empty lines, giving better coverage for both
    dense-layout and sparse-layout receipts.
    """
    config_psm6 = (
        "--psm 6 "
        "--oem 3 "
        "--dpi 300 "
        "-c preserve_interword_spaces=1 "
        "-c tessedit_do_invert=0"
    )
    config_psm11 = (
        "--psm 11 "
        "--oem 3 "
        "--dpi 300 "
        "-c preserve_interword_spaces=1 "
        "-c tessedit_do_invert=0"
    )
    try:
        # ThreadPoolExecutor is appropriate here: pytesseract spawns an external
        # tesseract process for each call, so the GIL is released while waiting,
        # allowing genuine I/O-bound parallelism between the two PSM runs.
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut6  = ex.submit(pytesseract.image_to_string, pil_img,
                              lang="vie+eng", config=config_psm6)
            fut11 = ex.submit(pytesseract.image_to_string, pil_img,
                              lang="vie+eng", config=config_psm11)
            text6  = fut6.result()
            text11 = fut11.result()

        lines6  = [l for l in text6.splitlines()  if l.strip()]
        lines11 = [l for l in text11.splitlines() if l.strip()]
        # Prefer PSM 11 if it captures meaningfully more content
        return text11 if len(lines11) > len(lines6) * 1.1 else text6
    except Exception:
        # Fallback: try PSM 6 alone
        return pytesseract.image_to_string(pil_img, lang="vie+eng", config=config_psm6)


def run_easyocr(pil_img):
    """Chạy EasyOCR, sắp xếp kết quả top→bottom, left→right.

    Uses improved readtext parameters:
    - contrast_ths=0.1  : detect low-contrast text regions
    - adjust_contrast=0.7: normalize contrast before recognition
    - text_threshold=0.6 : lower threshold to catch faint characters
    - link_threshold=0.4 : group nearby text boxes more aggressively
    """
    reader = get_easyocr_reader()
    if reader is None:
        return "", 0.0, []
    try:
        np_img = np.array(pil_img)
        results = reader.readtext(
            np_img,
            detail=1,
            paragraph=False,
            contrast_ths=0.1,
            adjust_contrast=0.7,
            text_threshold=0.6,
            link_threshold=0.4,
        )
        results.sort(key=lambda r: (r[0][0][1], r[0][0][0]))
        lines = [text for _, text, _ in results]
        confidences = [conf for _, _, conf in results]
        full_text = "\n".join(lines)
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
        return full_text, avg_conf, results
    except Exception:
        return "", 0.0, []


def _group_tess_boxes_to_lines(word_boxes: list[tuple]) -> list[tuple]:
    """Merge Tesseract word-level boxes into line-level boxes.

    Words whose vertical centres are within ``line_gap`` pixels of each other
    are grouped into a single bounding rectangle.  The text and confidence of
    each line box are aggregated (text joined by space, confidence averaged).

    Returns a list of (x, y, w, h, text, confidence) tuples — same format as
    the word boxes — but one entry per detected line instead of per word.
    """
    if not word_boxes:
        return []

    # Sort by vertical position first, then horizontal
    sorted_boxes = sorted(word_boxes, key=lambda b: (b[1], b[0]))

    lines: list[list[tuple]] = []    # list of accumulated line groups
    current_line: list[tuple] = []   # current group of word boxes

    for box in sorted_boxes:
        bx, by, bw, bh, text, conf = box
        cy = by + bh / 2  # vertical centre of this word

        if not current_line:
            current_line.append(box)
        else:
            # Estimate typical character height from already-collected words
            avg_h = sum(b[3] for b in current_line) / len(current_line)
            line_gap = max(avg_h * 0.6, 8)

            # Check against the last word's centre in the current line
            prev = current_line[-1]
            prev_cy = prev[1] + prev[3] / 2

            if abs(cy - prev_cy) <= line_gap:
                current_line.append(box)
            else:
                lines.append(current_line)
                current_line = [box]

    if current_line:
        lines.append(current_line)

    # Convert each group into a single bounding box
    result = []
    for group in lines:
        x1 = min(b[0] for b in group)
        y1 = min(b[1] for b in group)
        x2 = max(b[0] + b[2] for b in group)
        y2 = max(b[1] + b[3] for b in group)
        line_text = " ".join(b[4] for b in group)
        avg_conf  = sum(b[5] for b in group) / len(group)
        result.append((x1, y1, x2 - x1, y2 - y1, line_text, avg_conf))

    return result


def get_tess_word_boxes(pil_img) -> list:
    """Return line-level bounding boxes from Tesseract.

    Internally collects word-level boxes then merges them into line groups via
    ``_group_tess_boxes_to_lines`` for cleaner, more informative overlays.
    Each entry is (x, y, w, h, text, confidence) with confidence in [0, 1].
    """
    config = "--psm 6 --oem 3 --dpi 300"
    try:
        data = pytesseract.image_to_data(
            pil_img, lang="vie+eng", config=config,
            output_type=pytesseract.Output.DICT,
        )
        word_boxes = []
        for i in range(len(data["text"])):
            word = data["text"][i].strip()
            conf = int(data["conf"][i])
            if word and conf > 0:
                word_boxes.append((
                    data["left"][i], data["top"][i],
                    data["width"][i], data["height"][i],
                    word, conf / 100.0,
                ))
        return _group_tess_boxes_to_lines(word_boxes)
    except Exception:
        return []


def merge_dual_ocr(tess_text, easy_text, easy_confidence):
    """Merge kết quả Tesseract và EasyOCR theo confidence voting.
    Ưu tiên EasyOCR cho dòng có nhiều số, Tesseract cho text tiếng Việt.

    Threshold tuned down (1.2×, conf ≥ 0.60) so EasyOCR is preferred whenever
    it extracts meaningfully more content at reasonable confidence.
    """
    if not easy_text.strip():
        return tess_text

    tess_lines = [l for l in tess_text.splitlines() if l.strip()]
    easy_lines = [l for l in easy_text.splitlines() if l.strip()]

    if len(easy_lines) > len(tess_lines) * 1.2 and easy_confidence >= 0.60:
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

        if best_easy and best_score >= 0.5 and has_numbers and easy_confidence >= 0.60:
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
# Settings Dialog
# ─────────────────────────────────────────────────────────────────

class SettingsDialog(ctk.CTkToplevel):
    """Modal settings dialog for configuring application paths and options."""

    def __init__(self, parent, cfg: dict, on_save):
        super().__init__(parent)
        self.title("⚙️ Settings")
        self.geometry("580x560")
        self.resizable(False, False)
        self.grab_set()           # modal
        self._cfg    = cfg
        self._on_save = on_save

        pad = dict(padx=20, pady=8)

        ctk.CTkLabel(self, text="Application Settings",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(20, 10))

        # Tesseract path
        ctk.CTkLabel(self, text="Tesseract Binary Path:", anchor="w").pack(fill='x', **pad)
        tess_frame = ctk.CTkFrame(self, fg_color="transparent")
        tess_frame.pack(fill='x', padx=20, pady=0)
        self._tess_var = ctk.StringVar(value=cfg.get("tesseract_path", ""))
        ctk.CTkEntry(tess_frame, textvariable=self._tess_var).pack(side='left', fill='x', expand=True)
        ctk.CTkButton(tess_frame, text="Browse", width=70,
                      command=self._browse_tesseract).pack(side='left', padx=(6, 0))

        # Database path
        ctk.CTkLabel(self, text="Database File Path:", anchor="w").pack(fill='x', **pad)
        db_frame = ctk.CTkFrame(self, fg_color="transparent")
        db_frame.pack(fill='x', padx=20, pady=0)
        self._db_var = ctk.StringVar(value=cfg.get("db_path", ""))
        ctk.CTkEntry(db_frame, textvariable=self._db_var).pack(side='left', fill='x', expand=True)
        ctk.CTkButton(db_frame, text="Browse", width=70,
                      command=self._browse_db).pack(side='left', padx=(6, 0))

        # Theme
        ctk.CTkLabel(self, text="Appearance Theme:", anchor="w").pack(fill='x', **pad)
        self._theme_var = ctk.StringVar(value=cfg.get("theme", "Dark"))
        ctk.CTkOptionMenu(self, variable=self._theme_var,
                          values=["Dark", "Light", "System"]).pack(fill='x', padx=20, pady=0)

        # Log level
        ctk.CTkLabel(self, text="Log Level:", anchor="w").pack(fill='x', **pad)
        self._log_var = ctk.StringVar(value=cfg.get("log_level", "INFO"))
        ctk.CTkOptionMenu(self, variable=self._log_var,
                          values=["DEBUG", "INFO", "WARNING", "ERROR"]).pack(fill='x', padx=20, pady=0)

        # Toggles
        self._auto_var = ctk.BooleanVar(value=cfg.get("auto_scan_on_load", False))
        ctk.CTkCheckBox(self, text="Auto-scan first image when loading directory",
                        variable=self._auto_var).pack(anchor='w', padx=20, pady=(12, 4))

        self._deskew_var = ctk.BooleanVar(value=cfg.get("auto_deskew", False))
        ctk.CTkCheckBox(self, text="Auto-deskew images before OCR (corrects text angle)",
                        variable=self._deskew_var).pack(anchor='w', padx=20, pady=4)

        self._thumb_var = ctk.BooleanVar(value=cfg.get("show_thumbnails", True))
        ctk.CTkCheckBox(self, text="Show image thumbnails in file list",
                        variable=self._thumb_var).pack(anchor='w', padx=20, pady=4)

        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill='x', padx=20, pady=(14, 20))
        ctk.CTkButton(btn_frame, text="💾 Save", fg_color="#10B981", hover_color="#059669",
                      command=self._save).pack(side='left', expand=True, fill='x', padx=(0, 5))
        ctk.CTkButton(btn_frame, text="✖ Cancel", fg_color="#6B7280", hover_color="#4B5563",
                      command=self.destroy).pack(side='left', expand=True, fill='x', padx=(5, 0))

    def _browse_tesseract(self):
        path = ctk.filedialog.askopenfilename(
            title="Select Tesseract Executable",
            filetypes=[("Executables", "tesseract tesseract.exe *")]
        )
        if path:
            self._tess_var.set(path)

    def _browse_db(self):
        path = ctk.filedialog.asksaveasfilename(
            title="Select Database File",
            defaultextension=".db",
            filetypes=[("SQLite DB", "*.db")]
        )
        if path:
            self._db_var.set(path)

    def _save(self):
        self._cfg["tesseract_path"]    = self._tess_var.get().strip()
        self._cfg["db_path"]           = self._db_var.get().strip()
        self._cfg["theme"]             = self._theme_var.get()
        self._cfg["log_level"]         = self._log_var.get()
        self._cfg["auto_scan_on_load"] = self._auto_var.get()
        self._cfg["auto_deskew"]       = self._deskew_var.get()
        self._cfg["show_thumbnails"]   = self._thumb_var.get()
        save_config(self._cfg)
        if self._on_save:
            self._on_save(self._cfg)
        self.destroy()


# ─────────────────────────────────────────────────────────────────
# History Dialog
# ─────────────────────────────────────────────────────────────────

class HistoryDialog(ctk.CTkToplevel):
    """Browsable scan history from the database."""

    _COLUMNS = ("id", "scan_time", "receipt_type", "transaction_code",
                 "license_plate", "price", "status", "ocr_engine",
                 "ocr_confidence", "processing_time_ms")
    _HEADERS = ("ID", "Scan Time", "Type", "Transaction",
                 "Plate", "Price", "Status", "Engine",
                 "Conf%", "Time(ms)")

    def __init__(self, parent, db: DatabaseManager):
        super().__init__(parent)
        self.title("📋 Scan History")
        self.geometry("1150x580")
        self._db = db

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill='x', padx=15, pady=10)
        ctk.CTkLabel(top, text="Search:", anchor="w").pack(side='left')
        self._search_var = ctk.StringVar()
        search_entry = ctk.CTkEntry(top, textvariable=self._search_var, width=250)
        search_entry.pack(side='left', padx=8)
        search_entry.bind("<Return>", lambda _e: self._refresh())
        ctk.CTkButton(top, text="🔍 Search", width=90,
                      command=self._refresh).pack(side='left')
        ctk.CTkButton(top, text="↺ Reset", width=80,
                      command=self._reset).pack(side='left', padx=6)
        ctk.CTkButton(top, text="🗑 Delete", width=80,
                      fg_color="#991B1B", hover_color="#7F1D1D",
                      command=self._delete_selected).pack(side='left', padx=6)
        ctk.CTkButton(top, text="📊 Export All CSV", fg_color="#B91C1C",
                      hover_color="#991B1B", command=self._export_all_csv).pack(side='right')

        # Row count label
        self._count_label = ctk.CTkLabel(top, text="", font=ctk.CTkFont(size=11),
                                          text_color="gray60")
        self._count_label.pack(side='right', padx=10)

        # Treeview inside a frame
        tree_frame = ctk.CTkFrame(self)
        tree_frame.pack(fill='both', expand=True, padx=15, pady=(0, 15))

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background="#1E1E2E", foreground="white",
                        fieldbackground="#1E1E2E", rowheight=24,
                        font=("Consolas", 11))
        style.configure("Treeview.Heading", background="#374151",
                        foreground="white", font=("Segoe UI", 11, "bold"))
        style.map("Treeview", background=[("selected", "#3B82F6")])

        self._tree = ttk.Treeview(tree_frame, columns=self._COLUMNS,
                                   show="headings", selectmode="browse")
        col_widths = (40, 140, 100, 110, 100, 80, 80, 140, 60, 75)
        for col, hdr, w in zip(self._COLUMNS, self._HEADERS, col_widths):
            self._tree.heading(col, text=hdr,
                               command=lambda c=col: self._sort_by(c))
            self._tree.column(col, width=w, anchor="w")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical",   command=self._tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side='right',  fill='y')
        hsb.pack(side='bottom', fill='x')
        self._tree.pack(fill='both', expand=True)

        self._sort_col   = "id"
        self._sort_asc   = False
        self._cached_rows: list = []
        self._refresh()

    def _sort_by(self, col: str):
        """Toggle sort order and re-display the cached rows."""
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True
        self._populate(self._cached_rows)

    def _populate(self, rows: list):
        """Fill the treeview with *rows*, applying the current sort."""
        col = self._sort_col
        try:
            sorted_rows = sorted(rows, key=lambda r: (r.get(col) or ""),
                                 reverse=not self._sort_asc)
        except Exception:
            sorted_rows = rows
        self._tree.delete(*self._tree.get_children())
        for r in sorted_rows:
            conf_pct = f"{r.get('ocr_confidence', 0) * 100:.1f}" if r.get('ocr_confidence') else ""
            self._tree.insert("", "end", iid=str(r.get("id", "")), values=(
                r.get("id", ""),
                r.get("scan_time", ""),
                r.get("receipt_type", ""),
                r.get("transaction_code", ""),
                r.get("license_plate", ""),
                r.get("price", ""),
                r.get("status", ""),
                r.get("ocr_engine", ""),
                conf_pct,
                r.get("processing_time_ms", ""),
            ))
        self._count_label.configure(text=f"{len(sorted_rows)} records")


    def _refresh(self):
        search = self._search_var.get().strip()
        self._cached_rows = self._db.get_recent_scans(limit=200, search=search)
        self._populate(self._cached_rows)

    def _reset(self):
        self._search_var.set("")
        self._refresh()

    def _delete_selected(self):
        """Delete the selected scan record from the database."""
        sel = self._tree.selection()
        if not sel:
            return
        scan_id = int(sel[0])
        if messagebox.askyesno("Delete Record",
                               f"Delete scan record ID {scan_id}?",
                               parent=self):
            if self._db.delete_scan(scan_id):
                self._refresh()

    def _export_all_csv(self):
        ts   = datetime.now().strftime('%Y%m%d_%H%M%S')
        path = ctk.filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=f"history_export_{ts}.csv",
            filetypes=[("CSV files", "*.csv")]
        )
        if not path:
            return
        count = self._db.export_all_csv(path)
        messagebox.showinfo("Export", f"Exported {count} records to:\n{path}")


# ─────────────────────────────────────────────────────────────────
# Shortcuts Help Dialog
# ─────────────────────────────────────────────────────────────────

class ShortcutsDialog(ctk.CTkToplevel):
    """Non-modal dialog listing all keyboard shortcuts."""

    _SHORTCUTS = [
        ("Ctrl + O",       "Load directory"),
        ("Ctrl + S",       "Scan current image"),
        ("Ctrl + E",       "Export to CSV"),
        ("Ctrl + H",       "Open scan history"),
        ("Ctrl + ?",       "Show this shortcuts dialog"),
        ("← / →",          "Previous / Next image"),
        ("↑ / ↓",          "Previous / Next image (alternative)"),
        ("R",              "Rotate image 90° clockwise"),
        ("Shift + R",      "Rotate image 90° counter-clockwise"),
        ("F",              "Fit image to window"),
        ("D",              "Auto-deskew current image"),
        ("B",              "Toggle OCR bounding boxes"),
        ("+ / =",          "Zoom in"),
        ("- / _",          "Zoom out"),
        ("0",              "Reset zoom to 100%"),
        ("Delete",         "Remove current image from list"),
    ]

    def __init__(self, parent):
        super().__init__(parent)
        self.title("⌨️  Keyboard Shortcuts")
        self.geometry("440x480")
        self.resizable(False, False)

        ctk.CTkLabel(self, text="Keyboard Shortcuts",
                     font=ctk.CTkFont(size=17, weight="bold")).pack(pady=(18, 10))

        frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        frame.pack(fill='both', expand=True, padx=16, pady=(0, 10))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_columnconfigure(1, weight=2)

        for row_idx, (key, desc) in enumerate(self._SHORTCUTS):
            bg = "#1E293B" if row_idx % 2 == 0 else "transparent"
            row_frame = ctk.CTkFrame(frame, fg_color=bg, corner_radius=4)
            row_frame.grid(row=row_idx, column=0, columnspan=2,
                           sticky="ew", pady=1, padx=2)
            row_frame.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(row_frame, text=key, width=120,
                         font=ctk.CTkFont(family="Courier New", size=12, weight="bold"),
                         text_color="#60A5FA", anchor="w"
                         ).grid(row=0, column=0, padx=(8, 4), pady=4, sticky="w")
            ctk.CTkLabel(row_frame, text=desc,
                         font=ctk.CTkFont(size=12),
                         text_color="#CBD5E1", anchor="w"
                         ).grid(row=0, column=1, padx=(4, 8), pady=4, sticky="w")

        ctk.CTkButton(self, text="Close", width=120,
                      command=self.destroy).pack(pady=(4, 16))


# ─────────────────────────────────────────────────────────────────
# Statistics Dialog
# ─────────────────────────────────────────────────────────────────

class StatisticsDialog(ctk.CTkToplevel):
    """Display aggregate scan statistics in a simple read-only window."""

    def __init__(self, parent, db: DatabaseManager):
        super().__init__(parent)
        self.title("📊 Scan Statistics")
        self.geometry("420x440")
        self.resizable(False, False)
        self._db = db

        ctk.CTkLabel(self, text="Scan Statistics",
                     font=ctk.CTkFont(size=17, weight="bold")).pack(pady=(18, 6))

        self._text = ctk.CTkTextbox(
            self, fg_color="#0F111A", text_color="#00FFAA",
            font=ctk.CTkFont(family="Courier New", size=12), corner_radius=8)
        self._text.pack(fill='both', expand=True, padx=16, pady=(6, 0))

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill='x', padx=16, pady=12)
        ctk.CTkButton(btn_frame, text="↺ Refresh", width=100,
                      command=self._load).pack(side='left', padx=(0, 6))
        ctk.CTkButton(btn_frame, text="Close", width=100,
                      command=self.destroy).pack(side='left')

        self._load()

    def _load(self):
        stats = self._db.get_statistics()
        today = self._db.get_today_stats()
        lines = []
        lines.append(f"{'─' * 34}")
        lines.append(f"  Total scans:      {stats.get('total', 0)}")
        lines.append(f"  Today:            {today.get('today', 0)}")
        avg = stats.get("avg_processing_ms", 0)
        lines.append(f"  Avg process time: {avg:.0f} ms")
        lines.append(f"{'─' * 34}")
        lines.append("  By receipt type:")
        for rtype, cnt in (stats.get("by_type") or {}).items():
            lines.append(f"    {rtype:<22} {cnt}")
        lines.append(f"{'─' * 34}")
        lines.append("  Last 7 days:")
        week = today.get("week_by_day", {})
        if week:
            for day, cnt in sorted(week.items()):
                bar = "█" * min(cnt, 30)
                lines.append(f"    {day}  {bar} {cnt}")
        else:
            lines.append("    (no data)")
        lines.append(f"{'─' * 34}")

        self._text.configure(state="normal")
        self._text.delete("0.0", "end")
        self._text.insert("0.0", "\n".join(lines))
        self._text.configure(state="disabled")


# ─────────────────────────────────────────────────────────────────
# Enterprise GUI
# ─────────────────────────────────────────────────────────────────

class NextLevelOCRScanner(ctk.CTk):
    """Enterprise-grade OCR scanner GUI with batch processing, history, and audit logging."""

    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry(_config.get("window_geometry", "") or "1440x900")
        self.minsize(1100, 700)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # ── State ──────────────────────────────────────────────
        self.image_files: list       = []
        self.file_buttons: dict      = {}
        self._thumb_refs: dict       = {}   # path → PhotoImage thumbnail
        self.current_image_path: str = ""
        self.latest_metadata: dict   = {}
        self.latest_receipt_type     = "unknown"
        self._scan_lock              = threading.Lock()
        self._batch_running          = False
        self._batch_cancel           = threading.Event()
        self._rotation_angle: int    = 0    # cumulative rotation (0/90/180/270)

        # ── Bounding-box / OCR overlay state ───────────────────
        self._ocr_boxes_easy: list       = []   # [(bbox_quad, text, conf), ...]
        self._ocr_boxes_tess: list       = []   # [(x, y, w, h, text, conf), ...]
        self._show_boxes: bool           = True
        self._box_source: str            = "both"  # "easy" | "tess" | "both"
        self._canvas_img_offset          = (0, 0)
        self._canvas_display_size        = (0, 0)
        self._canvas_orig_size           = (0, 0)
        self._preview_canvas_img         = None    # keep PhotoImage reference
        self._tooltip_window: tk.Toplevel | None = None

        # ── Layout ─────────────────────────────────────────────
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=4)
        self.grid_columnconfigure(2, weight=2)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)   # status bar row

        self._build_sidebar()
        self._build_preview()
        self._build_results()
        self._build_statusbar()
        self._bind_shortcuts()

        # Restore last directory from config
        last_dir = _config.get("last_directory", "")
        if last_dir and os.path.isdir(last_dir):
            self.load_directory(last_dir)

        self.set_status(f"Ready — {APP_NAME} {APP_VERSION} | DB: {_config['db_path']}")
        logger.info("GUI initialised.")

    def _on_close(self):
        """Persist window geometry before quit."""
        try:
            _config["window_geometry"] = self.geometry()
            save_config(_config)
        except Exception:
            pass
        self.destroy()


    # ──────────────────────────────────────────────────────────
    # Build helpers
    # ──────────────────────────────────────────────────────────

    def _build_sidebar(self):
        self.sidebar_frame = ctk.CTkFrame(self, corner_radius=10, fg_color="#1E293B")
        self.sidebar_frame.grid(row=0, column=0, rowspan=1, sticky="nsew", padx=10, pady=10)

        ctk.CTkLabel(
            self.sidebar_frame,
            text=f"VETC Enterprise\n{APP_VERSION}",
            font=ctk.CTkFont(size=20, weight="bold")
        ).pack(pady=16, padx=16)

        btns = [
            ("📁 Load Directory", "#3B82F6", "#2563EB", lambda: self.load_directory()),
            ("🖼️  Load File",      "#6366F1", "#4F46E5", self.load_file),
            ("📦 Load ZIP",       "#F59E0B", "#D97706", self.load_zip_file),
            ("⚡ Batch Scan All", "#10B981", "#059669", self.start_batch_scan),
        ]
        for text, fg, hov, cmd in btns:
            ctk.CTkButton(
                self.sidebar_frame, text=text,
                fg_color=fg, hover_color=hov, command=cmd
            ).pack(fill='x', padx=16, pady=5)

        ctk.CTkFrame(self.sidebar_frame, height=1, fg_color="#334155").pack(
            fill='x', padx=16, pady=10)

        # Navigation row: Previous / Next
        nav_frame = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        nav_frame.pack(fill='x', padx=16, pady=2)
        nav_frame.grid_columnconfigure((0, 1), weight=1)
        self._btn_prev = ctk.CTkButton(
            nav_frame, text="◀ Prev", width=0,
            fg_color="#334155", hover_color="#1E293B",
            command=lambda: self._navigate(-1))
        self._btn_prev.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self._btn_next = ctk.CTkButton(
            nav_frame, text="Next ▶", width=0,
            fg_color="#334155", hover_color="#1E293B",
            command=lambda: self._navigate(1))
        self._btn_next.grid(row=0, column=1, sticky="ew", padx=(3, 0))

        ctk.CTkButton(
            self.sidebar_frame, text="📋 History",
            fg_color="#475569", hover_color="#334155",
            command=self._open_history
        ).pack(fill='x', padx=16, pady=5)

        ctk.CTkButton(
            self.sidebar_frame, text="📊 Statistics",
            fg_color="#475569", hover_color="#334155",
            command=self._open_statistics
        ).pack(fill='x', padx=16, pady=2)

        ctk.CTkButton(
            self.sidebar_frame, text="⚙️  Settings",
            fg_color="#374151", hover_color="#1F2937",
            command=self._open_settings
        ).pack(fill='x', padx=16, pady=5)

        ctk.CTkButton(
            self.sidebar_frame, text="⌨️  Shortcuts",
            fg_color="#1E293B", hover_color="#0F172A",
            command=self._open_shortcuts
        ).pack(fill='x', padx=16, pady=2)

        # File list header with count badge and Clear button
        list_header = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        list_header.pack(fill='x', padx=16, pady=(12, 2))
        ctk.CTkLabel(list_header, text="Images:",
                     font=ctk.CTkFont(size=12)).pack(side='left')
        self._file_count_label = ctk.CTkLabel(
            list_header, text="", font=ctk.CTkFont(size=10),
            text_color="#60A5FA")
        self._file_count_label.pack(side='left', padx=4)
        ctk.CTkButton(
            list_header, text="✕ Clear", width=60,
            fg_color="transparent", hover_color="#374151",
            font=ctk.CTkFont(size=10), text_color="#9CA3AF",
            command=self._clear_file_list
        ).pack(side='right')

        self.scrollable_file_list = ctk.CTkScrollableFrame(
            self.sidebar_frame, fg_color="transparent")
        self.scrollable_file_list.pack(fill='both', expand=True, padx=16, pady=(0, 10))

        # Stats mini-label at bottom
        self._stats_label = ctk.CTkLabel(
            self.sidebar_frame, text="", font=ctk.CTkFont(size=10),
            text_color="gray60", wraplength=180, justify="left")
        self._stats_label.pack(pady=(4, 10), padx=16, anchor='w')
        self._refresh_stats_label()

    def _build_preview(self):
        self.preview_frame = ctk.CTkFrame(self, corner_radius=10)
        self.preview_frame.grid(row=0, column=1, sticky="nsew", padx=0, pady=10)
        self.preview_frame.grid_columnconfigure(0, weight=1)
        # row 0 = zoom toolbar, row 1 = manipulation toolbar, row 2 = canvas

        # ── Toolbar row 1 ──────────────────────────────────────
        tb = ctk.CTkFrame(self.preview_frame, fg_color="transparent")
        tb.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 0))

        self._img_name_label = ctk.CTkLabel(
            tb, text="No image selected",
            font=ctk.CTkFont(size=12, weight="bold"))
        self._img_name_label.pack(side='left')

        # Right side: zoom controls
        ctk.CTkButton(tb, text="🔍+", width=36,
                      command=lambda: self._zoom(1.2)).pack(side='right', padx=2)
        ctk.CTkButton(tb, text="🔍−", width=36,
                      command=lambda: self._zoom(1 / 1.2)).pack(side='right', padx=2)
        ctk.CTkButton(tb, text="⊞", width=36,
                      command=self._zoom_reset).pack(side='right', padx=2)
        ctk.CTkButton(tb, text="↔ Fit", width=46,
                      fg_color="#374151", hover_color="#1F2937",
                      command=self._fit_to_window).pack(side='right', padx=2)

        # Zoom level indicator
        self._zoom_label = ctk.CTkLabel(
            tb, text="100%", font=ctk.CTkFont(size=10), text_color="#9CA3AF", width=38)
        self._zoom_label.pack(side='right', padx=(0, 4))

        # ── Toolbar row 2 ──────────────────────────────────────
        self.preview_frame.grid_rowconfigure(0, weight=0)  # row 0 = tb (zoom controls)
        tb2 = ctk.CTkFrame(self.preview_frame, fg_color="transparent")
        tb2.grid(row=1, column=0, sticky="ew", padx=10, pady=(2, 0))
        self.preview_frame.grid_rowconfigure(2, weight=1)  # canvas row

        # Rotation buttons
        ctk.CTkButton(tb2, text="↺ 90°", width=56,
                      fg_color="#374151", hover_color="#1F2937",
                      command=lambda: self._rotate_image(-90)
                      ).pack(side='left', padx=2)
        ctk.CTkButton(tb2, text="↻ 90°", width=56,
                      fg_color="#374151", hover_color="#1F2937",
                      command=lambda: self._rotate_image(90)
                      ).pack(side='left', padx=2)
        ctk.CTkButton(tb2, text="🔄 180°", width=62,
                      fg_color="#374151", hover_color="#1F2937",
                      command=lambda: self._rotate_image(180)
                      ).pack(side='left', padx=2)
        ctk.CTkButton(tb2, text="📐 Deskew", width=76,
                      fg_color="#065F46", hover_color="#047857",
                      command=self._deskew_current).pack(side='left', padx=2)

        # Box-source selector (EasyOCR / Tesseract / Both)
        self._box_source_var = ctk.StringVar(value="both")
        ctk.CTkOptionMenu(
            tb2, variable=self._box_source_var,
            values=["both", "easy", "tess"],
            width=84, command=self._on_box_source_change,
        ).pack(side='right', padx=2)
        ctk.CTkLabel(tb2, text="Source:", font=ctk.CTkFont(size=11)
                     ).pack(side='right', padx=(10, 2))

        # Boxes toggle
        self._boxes_btn = ctk.CTkButton(
            tb2, text="🔲 Boxes ON", width=100,
            fg_color="#1D4ED8", hover_color="#1E40AF",
            command=self._toggle_boxes)
        self._boxes_btn.pack(side='right', padx=(2, 6))

        # Save annotated image button (in toolbar row 1)
        self._btn_save_img = ctk.CTkButton(
            tb2, text="💾 Save", width=70,
            fg_color="#065F46", hover_color="#047857",
            command=self.save_annotated_image)
        self._btn_save_img.pack(side='left', padx=(6, 2))

        # ── Canvas ─────────────────────────────────────────────
        canvas_host = ctk.CTkFrame(self.preview_frame, fg_color="#111827")
        canvas_host.grid(row=2, column=0, sticky="nsew", padx=10, pady=10)
        canvas_host.grid_rowconfigure(0, weight=1)
        canvas_host.grid_columnconfigure(0, weight=1)

        self._preview_canvas = tk.Canvas(
            canvas_host, bg="#111827", highlightthickness=0, cursor="crosshair")
        self._preview_canvas.grid(row=0, column=0, sticky="nsew")

        # Bind mouse events
        self._preview_canvas.bind("<MouseWheel>", self._on_canvas_scroll)   # Win/Mac
        self._preview_canvas.bind("<Button-4>",   self._on_canvas_scroll)   # Linux up
        self._preview_canvas.bind("<Button-5>",   self._on_canvas_scroll)   # Linux down
        self._preview_canvas.bind("<Button-1>",   self._on_canvas_click)
        self._preview_canvas.bind("<Motion>",     self._on_canvas_hover)
        self._preview_canvas.bind("<Leave>",      self._on_canvas_leave)

        # Placeholder text
        self._preview_canvas.create_text(
            400, 300, text="No Image Selected",
            fill="gray40", font=("Segoe UI", 16), tags="placeholder")

        self._zoom_factor  = 1.0
        self._base_pil_img = None

    def _build_results(self):
        self.results_frame = ctk.CTkFrame(self, corner_radius=10)
        self.results_frame.grid(row=0, column=2, sticky="nsew", padx=10, pady=10)
        self.results_frame.grid_rowconfigure(1, weight=1)
        self.results_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self.results_frame, text="Scan Results",
                     font=ctk.CTkFont(size=18, weight="bold")
                     ).grid(row=0, column=0, pady=(16, 4), padx=20, sticky="w")

        self.tabview = ctk.CTkTabview(self.results_frame)
        self.tabview.grid(row=1, column=0, sticky="nsew", padx=16, pady=4)
        self.tabview.add("Structured Data")
        self.tabview.add("Raw OCR Text")

        # ── Structured Data tab ────────────────────────────────
        sd_tab = self.tabview.tab("Structured Data")
        sd_tab.grid_rowconfigure(0, weight=1)
        sd_tab.grid_columnconfigure(0, weight=1)
        self.smart_data_box = ctk.CTkTextbox(
            sd_tab, fg_color="#0F111A", text_color="#00FFAA",
            font=ctk.CTkFont(family="Courier New", size=12), corner_radius=8)
        self.smart_data_box.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        self.smart_data_box.insert("0.0",
            "--- No Data ---\nHit SCAN to extract structured receipt data.")
        self.smart_data_box.configure(state="disabled")

        # ── Raw OCR Text tab ───────────────────────────────────
        raw_tab = self.tabview.tab("Raw OCR Text")
        raw_tab.grid_rowconfigure(1, weight=1)
        raw_tab.grid_columnconfigure(0, weight=1)

        # Search bar inside the Raw OCR tab
        search_frame = ctk.CTkFrame(raw_tab, fg_color="transparent")
        search_frame.grid(row=0, column=0, sticky="ew", pady=(4, 2))
        search_frame.grid_columnconfigure(0, weight=1)
        self._ocr_search_var = ctk.StringVar()
        search_entry = ctk.CTkEntry(
            search_frame, textvariable=self._ocr_search_var,
            placeholder_text="🔍 Search in OCR text…", height=28)
        search_entry.grid(row=0, column=0, sticky="ew", padx=(4, 2))
        ctk.CTkButton(
            search_frame, text="Find", width=50,
            command=self._ocr_text_search).grid(row=0, column=1, padx=(0, 4))
        ctk.CTkButton(
            search_frame, text="✕", width=30,
            fg_color="#4B5563", hover_color="#374151",
            command=self._ocr_text_clear_search).grid(row=0, column=2, padx=(0, 4))
        search_entry.bind("<Return>", lambda _e: self._ocr_text_search())

        self.raw_data_box = ctk.CTkTextbox(
            raw_tab, fg_color="#1E1E1E", text_color="white",
            font=ctk.CTkFont(family="Courier New", size=12), corner_radius=8)
        self.raw_data_box.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        self.raw_data_box.insert("0.0", "Waiting for input…\n")
        self.raw_data_box.configure(state="disabled")

        # Configure highlight tags on the underlying tk Text widget
        self.raw_data_box._textbox.tag_configure(
            "search_hit", background="#FFD700", foreground="#000000")
        self.raw_data_box._textbox.tag_configure(
            "box_hit", background="#00BFFF", foreground="#000000")

        # Progress
        self.progress_bar = ctk.CTkProgressBar(
            self.results_frame, mode="indeterminate",
            height=5, fg_color="#2D3748", progress_color="#10B981")
        self.progress_bar.grid(row=2, column=0, sticky="ew", padx=16, pady=(6, 0))
        self.progress_bar.set(0)

        # Batch progress (determinate, hidden until batch starts)
        self._batch_progress = ctk.CTkProgressBar(
            self.results_frame, mode="determinate",
            height=5, fg_color="#2D3748", progress_color="#F59E0B")
        self._batch_progress.grid(row=3, column=0, sticky="ew", padx=16, pady=(2, 0))
        self._batch_progress.set(0)
        self._batch_progress.grid_remove()

        # Action buttons row 1
        af1 = ctk.CTkFrame(self.results_frame, fg_color="transparent")
        af1.grid(row=4, column=0, sticky="ew", padx=16, pady=(8, 0))
        af1.grid_columnconfigure((0, 1, 2), weight=1)

        self.btn_copy = ctk.CTkButton(
            af1, text="📄 Copy JSON",
            fg_color="#4B5563", hover_color="#374151",
            command=self.copy_data_to_clipboard)
        self.btn_copy.grid(row=0, column=0, sticky="ew", padx=(0, 3))

        self.btn_export_csv = ctk.CTkButton(
            af1, text="📊 CSV",
            fg_color="#B91C1C", hover_color="#991B1B",
            command=self.export_to_csv)
        self.btn_export_csv.grid(row=0, column=1, sticky="ew", padx=3)

        self.btn_export_json = ctk.CTkButton(
            af1, text="🗂 JSON",
            fg_color="#7C3AED", hover_color="#6D28D9",
            command=self.export_to_json)
        self.btn_export_json.grid(row=0, column=2, sticky="ew", padx=(3, 0))

        # Scan button
        self.btn_scan = ctk.CTkButton(
            self.results_frame, text="▶  START SCAN  (Ctrl+S)",
            font=ctk.CTkFont(size=15, weight="bold"),
            height=48, fg_color="#10B981", hover_color="#059669",
            corner_radius=8, command=self.start_scan_thread)
        self.btn_scan.grid(row=5, column=0, sticky="ew", padx=16, pady=(8, 16))

    def _build_statusbar(self):
        self._status_bar = ctk.CTkFrame(self, height=28, corner_radius=0,
                                         fg_color="#111827")
        self._status_bar.grid(row=1, column=0, columnspan=3, sticky="ew")
        self._status_label = ctk.CTkLabel(
            self._status_bar, text="Initialising…",
            font=ctk.CTkFont(size=11), text_color="#9CA3AF", anchor="w")
        self._status_label.pack(side='left', padx=12)
        self._clock_label = ctk.CTkLabel(
            self._status_bar, text="",
            font=ctk.CTkFont(size=11), text_color="#6B7280", anchor="e")
        self._clock_label.pack(side='right', padx=12)
        # Today's scan count
        self._today_label = ctk.CTkLabel(
            self._status_bar, text="",
            font=ctk.CTkFont(size=11), text_color="#4ADE80", anchor="e")
        self._today_label.pack(side='right', padx=8)
        self._tick_clock()
        self._refresh_today_label()

    def _bind_shortcuts(self):
        self.bind("<Control-o>", lambda e: self.load_directory())
        self.bind("<Control-O>", lambda e: self.load_directory())
        self.bind("<Control-s>", lambda e: self.start_scan_thread())
        self.bind("<Control-S>", lambda e: self.start_scan_thread())
        self.bind("<Control-e>", lambda e: self.export_to_csv())
        self.bind("<Control-E>", lambda e: self.export_to_csv())
        self.bind("<Control-h>", lambda e: self._open_history())
        self.bind("<Control-H>", lambda e: self._open_history())
        self.bind("<Control-question>", lambda e: self._open_shortcuts())

        # Image navigation
        self.bind("<Left>",  lambda e: self._navigate(-1))
        self.bind("<Right>", lambda e: self._navigate(1))
        self.bind("<Up>",    lambda e: self._navigate(-1))
        self.bind("<Down>",  lambda e: self._navigate(1))

        # Image manipulation
        self.bind("<r>",     lambda e: self._rotate_image(90))
        self.bind("<R>",     lambda e: self._rotate_image(-90))
        self.bind("<f>",     lambda e: self._fit_to_window())
        self.bind("<F>",     lambda e: self._fit_to_window())
        self.bind("<d>",     lambda e: self._deskew_current())
        self.bind("<D>",     lambda e: self._deskew_current())
        self.bind("<b>",     lambda e: self._toggle_boxes())
        self.bind("<B>",     lambda e: self._toggle_boxes())

        # Zoom
        self.bind("<plus>",      lambda e: self._zoom(1.2))
        self.bind("<equal>",     lambda e: self._zoom(1.2))
        self.bind("<minus>",     lambda e: self._zoom(1 / 1.2))
        self.bind("<underscore>", lambda e: self._zoom(1 / 1.2))
        self.bind("<0>",         lambda e: self._zoom_reset())

        # Delete from list
        self.bind("<Delete>",  lambda e: self._remove_current_from_list())

    # ──────────────────────────────────────────────────────────
    # Clock / Status helpers
    # ──────────────────────────────────────────────────────────

    def _tick_clock(self):
        self._clock_label.configure(
            text=datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))
        self.after(1000, self._tick_clock)

    def _refresh_today_label(self):
        today = _db.get_today_stats().get("today", 0)
        self._today_label.configure(text=f"Today: {today} scans")
        self.after(60_000, self._refresh_today_label)   # refresh every minute

    def set_status(self, msg: str):
        self._status_label.configure(text=msg)
        logger.debug("Status: %s", msg)

    def _show_toast(self, msg: str, color: str = "#1E293B",
                    text_color: str = "#00FFAA", duration_ms: int = 2500):
        """Display a brief floating toast notification near the bottom of the window."""
        try:
            tw = tk.Toplevel(self)
            tw.wm_overrideredirect(True)
            # Position at bottom-centre of main window
            rx = self.winfo_rootx() + self.winfo_width() // 2
            ry = self.winfo_rooty() + self.winfo_height() - 70
            tw.wm_geometry(f"+{rx - 150}+{ry}")
            lbl = tk.Label(tw, text=msg, justify="center",
                           background=color, foreground=text_color,
                           font=("Segoe UI", 11), relief="flat",
                           padx=18, pady=8, bd=1)
            lbl.pack()
            tw.after(duration_ms, tw.destroy)
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────
    # Image management
    # ──────────────────────────────────────────────────────────

    def load_directory(self, dir_path: str = ""):
        if not dir_path:
            dir_path = ctk.filedialog.askdirectory()
        if not dir_path:
            return
        _config["last_directory"] = dir_path
        save_config(_config)
        for w in self.scrollable_file_list.winfo_children():
            w.destroy()
        self.image_files  = []
        self.file_buttons = {}
        self._thumb_refs  = {}
        for f in sorted(os.listdir(dir_path)):
            if f.lower().endswith(_SUPPORTED_EXTENSIONS):
                self.image_files.append(os.path.join(dir_path, f))
        for path in self.image_files:
            self._add_file_button(path)
        n = len(self.image_files)
        self._file_count_label.configure(text=f"({n})")
        self.set_status(f"Loaded {n} image{'s' if n != 1 else ''} from: {dir_path}")
        logger.info("Directory loaded: %s (%d images)", dir_path, n)
        self._update_nav_buttons()
        if self.image_files:
            self.display_image(self.image_files[0])
            if _config.get("auto_scan_on_load"):
                self.start_scan_thread()

    def load_file(self):
        ext_str = " ".join(f"*{e}" for e in _SUPPORTED_EXTENSIONS)
        path = ctk.filedialog.askopenfilename(
            filetypes=[("Image Files", ext_str)]
        )
        if not path:
            return
        for w in self.scrollable_file_list.winfo_children():
            w.destroy()
        self.image_files  = [path]
        self.file_buttons = {}
        self._thumb_refs  = {}
        self._add_file_button(path)
        self._file_count_label.configure(text="(1)")
        self.display_image(path)
        self._update_nav_buttons()
        self.set_status(f"File loaded: {os.path.basename(path)}")

    def load_zip_file(self):
        zip_path = ctk.filedialog.askopenfilename(filetypes=[("ZIP Archives", "*.zip")])
        if not zip_path:
            return
        extract_dir = os.path.splitext(zip_path)[0] + "_extracted"
        try:
            if os.path.exists(extract_dir):
                shutil.rmtree(extract_dir)
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(extract_dir)
            self.load_directory(extract_dir)
            logger.info("ZIP extracted to: %s", extract_dir)
        except Exception as exc:
            logger.error("ZIP extraction failed: %s", exc)
            messagebox.showerror("Error", f"Failed to extract ZIP:\n{exc}")

    def _add_file_button(self, path: str):
        if _config.get("show_thumbnails", True):
            row_frame = ctk.CTkFrame(
                self.scrollable_file_list, fg_color="transparent", corner_radius=4)
            row_frame.pack(fill='x', pady=1)
            row_frame.grid_columnconfigure(1, weight=1)

            # Thumbnail placeholder — loaded lazily in background
            thumb_label = ctk.CTkLabel(row_frame, text="", width=_THUMB_W, height=_THUMB_H,
                                        fg_color="#1E293B", corner_radius=2)
            thumb_label.grid(row=0, column=0, padx=(2, 4), pady=2)

            btn = ctk.CTkButton(
                row_frame, text=os.path.basename(path),
                anchor="w", fg_color="transparent",
                text_color="#CBD5E1", hover_color="#334155",
                font=ctk.CTkFont(size=10),
                command=lambda p=path: self.display_image(p)
            )
            btn.grid(row=0, column=1, sticky="ew", padx=(0, 4))

            # Load thumbnail in background
            def _load_thumb(p=path, lbl=thumb_label):
                try:
                    img = Image.open(p)
                    img.thumbnail((_THUMB_W, _THUMB_H))
                    tk_img = ImageTk.PhotoImage(img)
                    self._thumb_refs[p] = tk_img
                    lbl.after(0, lambda i=tk_img: lbl.configure(image=i, text=""))
                except Exception:
                    pass
            threading.Thread(target=_load_thumb, daemon=True).start()
        else:
            btn = ctk.CTkButton(
                self.scrollable_file_list, text=os.path.basename(path),
                anchor="w", fg_color="transparent",
                text_color="white", hover_color="gray40",
                command=lambda p=path: self.display_image(p)
            )
            btn.pack(fill='x', pady=1)
        self.file_buttons[path] = btn

    def display_image(self, path: str):
        self.current_image_path = path
        self._rotation_angle = 0   # reset rotation when loading new image
        for p, btn in self.file_buttons.items():
            is_active = (p == path)
            btn.configure(fg_color="#1E3A5F" if is_active else "transparent")
        # Clear previous OCR boxes when a new image is loaded
        self._ocr_boxes_easy = []
        self._ocr_boxes_tess = []
        try:
            self._base_pil_img = Image.open(path)
            self._zoom_factor  = 1.0
            self._render_image()
            idx = self.image_files.index(path) + 1 if path in self.image_files else "?"
            total = len(self.image_files)
            self._img_name_label.configure(
                text=f"[{idx}/{total}]  {os.path.basename(path)}")
            self.update_textbox(self.smart_data_box,
                                "--- Ready ---\nPress START SCAN or Ctrl+S.")
            self.update_textbox(self.raw_data_box, "Image loaded. Ready for OCR.")
            self.set_status(f"Preview: {os.path.basename(path)}")
            self._update_nav_buttons()
        except Exception as exc:
            logger.error("display_image error: %s", exc)
            self._preview_canvas.delete("all")
            self._preview_canvas.create_text(
                400, 300, text=f"Cannot open image:\n{exc}",
                fill="#FF6B6B", font=("Segoe UI", 13))

    def _render_image(self):
        """Render the current PIL image (+ optional bounding boxes) onto the Canvas."""
        if self._base_pil_img is None:
            return
        canvas = self._preview_canvas
        cw = canvas.winfo_width()
        ch = canvas.winfo_height()
        # winfo_width/height returns 1 before the widget is fully mapped
        if cw <= 1:
            cw = _DEFAULT_PREVIEW_W
        if ch <= 1:
            ch = _DEFAULT_PREVIEW_H

        img = self._base_pil_img.copy()

        # Apply cumulative rotation
        if self._rotation_angle % 360 != 0:
            img = img.rotate(-self._rotation_angle, expand=True)

        orig_w, orig_h = img.size

        # Compute display size respecting zoom and canvas bounds
        display_w = int(orig_w * self._zoom_factor)
        display_h = int(orig_h * self._zoom_factor)
        if display_w > cw or display_h > ch:
            ratio     = min((cw - 20) / max(orig_w, 1), (ch - 20) / max(orig_h, 1))
            display_w = max(1, int(orig_w * ratio))
            display_h = max(1, int(orig_h * ratio))

        img = img.resize((display_w, display_h), Image.Resampling.LANCZOS)

        # Center image inside canvas
        offset_x = max(0, (cw - display_w) // 2)
        offset_y = max(0, (ch - display_h) // 2)
        self._canvas_img_offset   = (offset_x, offset_y)
        self._canvas_display_size = (display_w, display_h)
        self._canvas_orig_size    = (orig_w, orig_h)

        # Draw bounding boxes directly onto the image pixels if toggled on
        if self._show_boxes:
            img = self._draw_boxes_on_image(img, display_w, display_h, orig_w, orig_h)

        tk_img = ImageTk.PhotoImage(img)
        self._preview_canvas_img = tk_img   # keep reference to prevent GC

        canvas.delete("all")
        canvas.create_image(offset_x, offset_y, anchor="nw", image=tk_img)

        # Place invisible hit-areas on the canvas so click/hover interactions still work
        if self._show_boxes:
            self._draw_boxes_on_canvas(hit_only=True)

        # Update zoom percentage label
        zoom_pct = int(self._zoom_factor * 100)
        try:
            self._zoom_label.configure(text=f"{zoom_pct}%")
        except Exception:
            pass

    def _zoom(self, factor: float):
        self._zoom_factor = max(_MIN_ZOOM, min(self._zoom_factor * factor, _MAX_ZOOM))
        self._render_image()

    def _zoom_reset(self):
        self._zoom_factor = 1.0
        self._render_image()

    def _fit_to_window(self):
        """Scale zoom so the image fits exactly within the canvas."""
        if self._base_pil_img is None:
            return
        canvas = self._preview_canvas
        cw = max(canvas.winfo_width(), _DEFAULT_PREVIEW_W)
        ch = max(canvas.winfo_height(), _DEFAULT_PREVIEW_H)
        img = self._base_pil_img
        if self._rotation_angle % 180 != 0:
            ow, oh = img.size[1], img.size[0]
        else:
            ow, oh = img.size
        ratio = min((cw - 20) / max(ow, 1), (ch - 20) / max(oh, 1))
        self._zoom_factor = max(_MIN_ZOOM, min(ratio, _MAX_ZOOM))
        self._render_image()

    def _rotate_image(self, angle: int):
        """Rotate the preview by *angle* degrees (positive = clockwise)."""
        if self._base_pil_img is None:
            return
        self._rotation_angle = (self._rotation_angle + angle) % 360
        # Clear bounding boxes since they no longer match the rotated view
        self._ocr_boxes_easy = []
        self._ocr_boxes_tess = []
        self._render_image()
        self.set_status(f"🔄 Rotated {self._rotation_angle}°")

    def _deskew_current(self):
        """Auto-detect and correct the skew of the currently displayed image."""
        if self._base_pil_img is None:
            self.set_status("⚠️  No image loaded.")
            return
        self.set_status("📐 Deskewing…")
        original = self._base_pil_img.copy()
        def _do():
            corrected = deskew_image(original)
            self.after(0, self._apply_deskewed, corrected)
        threading.Thread(target=_do, daemon=True).start()

    def _apply_deskewed(self, corrected_img: Image.Image):
        self._base_pil_img = corrected_img
        self._rotation_angle = 0
        self._ocr_boxes_easy = []
        self._ocr_boxes_tess = []
        self._render_image()
        self.set_status("📐 Deskew applied.")
        self._show_toast("📐 Deskew applied!", color="#0F172A", text_color="#38BDF8")

    def _navigate(self, direction: int):
        """Move to the previous (-1) or next (+1) image in the list."""
        if not self.image_files or not self.current_image_path:
            return
        try:
            idx = self.image_files.index(self.current_image_path)
        except ValueError:
            return
        new_idx = idx + direction
        if 0 <= new_idx < len(self.image_files):
            self.display_image(self.image_files[new_idx])

    def _update_nav_buttons(self):
        """Enable/disable Prev/Next buttons based on current position."""
        if not self.image_files or not self.current_image_path:
            self._btn_prev.configure(state="disabled")
            self._btn_next.configure(state="disabled")
            return
        try:
            idx = self.image_files.index(self.current_image_path)
        except ValueError:
            return
        self._btn_prev.configure(state="normal" if idx > 0 else "disabled")
        self._btn_next.configure(state="normal" if idx < len(self.image_files) - 1 else "disabled")

    def _clear_file_list(self):
        """Remove all images from the file list."""
        for w in self.scrollable_file_list.winfo_children():
            w.destroy()
        self.image_files  = []
        self.file_buttons = {}
        self._thumb_refs  = {}
        self.current_image_path = ""
        self._file_count_label.configure(text="")
        self._preview_canvas.delete("all")
        self._preview_canvas.create_text(
            400, 300, text="No Image Selected",
            fill="gray40", font=("Segoe UI", 16), tags="placeholder")
        self._base_pil_img = None
        self._update_nav_buttons()
        self.set_status("File list cleared.")

    def _remove_current_from_list(self):
        """Remove the currently selected image from the list."""
        if not self.current_image_path:
            return
        path = self.current_image_path
        try:
            idx = self.image_files.index(path)
        except ValueError:
            return
        # Determine next image to display
        self.image_files.remove(path)
        btn = self.file_buttons.pop(path, None)
        if btn:
            try:
                btn.master.destroy()  # destroy thumbnail row frame (if using thumbnails)
            except Exception:
                try:
                    btn.destroy()
                except Exception:
                    pass
        self._thumb_refs.pop(path, None)
        self._file_count_label.configure(text=f"({len(self.image_files)})")
        if self.image_files:
            next_idx = min(idx, len(self.image_files) - 1)
            self.display_image(self.image_files[next_idx])
        else:
            self.current_image_path = ""
            self._base_pil_img = None
            self._preview_canvas.delete("all")
            self._update_nav_buttons()
        self.set_status(f"Removed: {os.path.basename(path)}")

    # ──────────────────────────────────────────────────────────
    # Bounding-box overlay
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _box_conf_color_rgb(conf: float) -> tuple:
        """Return an RGB colour tuple for a given confidence score."""
        if conf >= 0.80:
            return (0, 255, 136)    # bright green — high confidence
        if conf >= 0.55:
            return (255, 215, 0)    # gold — medium confidence
        return (255, 107, 107)      # red — low confidence

    def _draw_boxes_on_image(
        self,
        img: Image.Image,
        dw: int,
        dh: int,
        ow: int,
        oh: int,
    ) -> Image.Image:
        """Draw OCR bounding boxes directly onto a PIL image (raster pixels).

        Boxes are colour-coded by confidence and drawn at the display resolution
        (``dw`` × ``dh``), which is the already-resized copy of the original
        (``ow`` × ``oh``) image.
        """
        if ow == 0 or oh == 0:
            return img

        draw = ImageDraw.Draw(img)
        sx = dw / ow
        sy = dh / oh
        _BOX_PAD    = 3
        _LABEL_OFFS = 12   # pixels above the top-left corner to place text

        # Try common monospace fonts in order; fall back to PIL's built-in default
        _font = ImageFont.load_default()
        for _fname in ("consola.ttf", "DejaVuSansMono.ttf", "LiberationMono-Regular.ttf",
                       "Courier New.ttf", "cour.ttf"):
            try:
                _font = ImageFont.truetype(_fname, 10)
                break
            except Exception:
                continue

        def _label(text: str, conf: float) -> str:
            return f"{conf * 100:.0f}%  {text[:24]}" if text else f"{conf * 100:.0f}%"

        # EasyOCR boxes (quad polygons)
        if self._box_source in ("easy", "both"):
            for bbox, text, conf in self._ocr_boxes_easy:
                color = self._box_conf_color_rgb(conf)
                pts = [(px * sx, py * sy) for px, py in bbox]

                # Expand each vertex outward from the centroid by _BOX_PAD pixels
                cx = sum(p[0] for p in pts) / len(pts)
                cy = sum(p[1] for p in pts) / len(pts)
                padded = []
                for px, py in pts:
                    dx, dy = px - cx, py - cy
                    dist = (dx * dx + dy * dy) ** 0.5 or 1
                    padded.append((px + dx / dist * _BOX_PAD,
                                   py + dy / dist * _BOX_PAD))

                draw.polygon(padded, outline=color, width=2)
                tx, ty = padded[0]
                draw.text((tx, max(0, ty - _LABEL_OFFS)), _label(text, conf),
                          fill=color, font=_font)

        # Tesseract line boxes (axis-aligned rectangles)
        if self._box_source in ("tess", "both"):
            for bx, by, bw, bh, text, conf in self._ocr_boxes_tess:
                color = self._box_conf_color_rgb(conf)
                x1 = bx * sx - _BOX_PAD
                y1 = by * sy - _BOX_PAD
                x2 = (bx + bw) * sx + _BOX_PAD
                y2 = (by + bh) * sy + _BOX_PAD
                draw.rectangle([x1, y1, x2, y2], outline=color, width=2)
                draw.text((x1, max(0, y1 - _LABEL_OFFS)), _label(text, conf),
                          fill=color, font=_font)

        return img

    def _draw_boxes_on_canvas(self, hit_only: bool = False):
        """Place OCR bounding boxes on the preview canvas.

        When *hit_only* is ``True`` the boxes are drawn as invisible, transparent
        canvas items whose sole purpose is to carry click/hover event bindings so
        that the interactive tooltip and text-highlight features continue to work
        even though the visual box outlines are now rasterised directly onto the
        image via :meth:`_draw_boxes_on_image`.
        """
        canvas = self._preview_canvas
        ox, oy = self._canvas_img_offset
        dw, dh = self._canvas_display_size
        ow, oh = self._canvas_orig_size
        if ow == 0 or oh == 0:
            return
        sx = dw / ow
        sy = dh / oh

        # Padding (in canvas pixels) added around each box
        _BOX_PAD = 3

        def _conf_color(conf: float) -> tuple[str, int]:
            """Return (outline_colour, line_width) based on confidence."""
            if conf >= 0.80:
                return "#00FF88", 2   # bright green — high confidence
            if conf >= 0.55:
                return "#FFD700", 2   # gold — medium confidence
            return "#FF6B6B", 2       # red — low confidence (thicker for visibility)

        def _draw_quad(pts_screen: list, text: str, conf: float, tag: str):
            if hit_only:
                # Invisible stipple polygon — sole purpose is event binding
                canvas.create_polygon(
                    pts_screen,
                    outline="", fill="white", stipple="gray12",
                    tags=(tag, "ocr_box"),
                )
                return
            color, lw = _conf_color(conf)
            # Expand quad outward by _BOX_PAD pixels (axis-aligned approximation)
            xs = pts_screen[0::2]
            ys = pts_screen[1::2]
            cx = sum(xs) / len(xs)
            cy = sum(ys) / len(ys)
            padded = []
            for px, py in zip(xs, ys):
                dx = px - cx
                dy = py - cy
                dist = (dx * dx + dy * dy) ** 0.5 or 1
                padded.extend([px + dx / dist * _BOX_PAD,
                                py + dy / dist * _BOX_PAD])
            canvas.create_polygon(
                padded,
                outline=color, fill="", width=lw,
                tags=(tag, "ocr_box"),
            )
            # Confidence label + truncated text above the top-left corner
            tx = padded[0]
            ty = padded[1] - 3
            label_text = f"{conf * 100:.0f}%  {text[:24]}" if text else f"{conf * 100:.0f}%"
            canvas.create_text(
                tx, ty,
                text=label_text,
                fill=color, font=("Consolas", 7),
                anchor="sw", tags=(f"{tag}_lbl", "ocr_label"),
            )

        # EasyOCR boxes (quad polygons)
        if self._box_source in ("easy", "both"):
            for i, (bbox, text, conf) in enumerate(self._ocr_boxes_easy):
                pts = []
                for px, py in bbox:
                    pts.extend([ox + px * sx, oy + py * sy])
                tag = f"easy_{i}"
                _draw_quad(pts, text, conf, tag)
                canvas.tag_bind(tag, "<Button-1>",
                                lambda _e, t=text, c=conf, s="EasyOCR":
                                    self._on_box_click(t, c, s))
                canvas.tag_bind(tag, "<Enter>",
                                lambda e, t=text, c=conf, s="EasyOCR":
                                    self._show_box_tooltip(e, t, c, s))
                canvas.tag_bind(tag, "<Leave>",
                                lambda _e: self._hide_tooltip())

        # Tesseract line boxes (axis-aligned rectangles, merged from word boxes)
        if self._box_source in ("tess", "both"):
            for i, (bx, by, bw, bh, text, conf) in enumerate(self._ocr_boxes_tess):
                x1 = ox + bx * sx - _BOX_PAD
                y1 = oy + by * sy - _BOX_PAD
                x2 = ox + (bx + bw) * sx + _BOX_PAD
                y2 = oy + (by + bh) * sy + _BOX_PAD
                tag = f"tess_{i}"
                if hit_only:
                    canvas.create_rectangle(
                        x1, y1, x2, y2,
                        outline="", fill="white", stipple="gray12",
                        tags=(tag, "ocr_box"),
                    )
                else:
                    color, lw = _conf_color(conf)
                    canvas.create_rectangle(
                        x1, y1, x2, y2,
                        outline=color, fill="", width=lw,
                        tags=(tag, "ocr_box"),
                    )
                    label_text = f"{conf * 100:.0f}%  {text[:24]}" if text else f"{conf * 100:.0f}%"
                    canvas.create_text(
                        x1, y1 - 2,
                        text=label_text,
                        fill=color, font=("Consolas", 7),
                        anchor="sw", tags=(f"{tag}_lbl", "ocr_label"),
                    )
                canvas.tag_bind(tag, "<Button-1>",
                                lambda _e, t=text, c=conf, s="Tesseract":
                                    self._on_box_click(t, c, s))
                canvas.tag_bind(tag, "<Enter>",
                                lambda e, t=text, c=conf, s="Tesseract":
                                    self._show_box_tooltip(e, t, c, s))
                canvas.tag_bind(tag, "<Leave>",
                                lambda _e: self._hide_tooltip())

    # ──────────────────────────────────────────────────────────
    # Canvas event handlers
    # ──────────────────────────────────────────────────────────

    def _on_canvas_scroll(self, event):
        """Zoom with mouse-wheel."""
        if event.num == 4 or getattr(event, "delta", 0) > 0:
            self._zoom(1.1)
        else:
            self._zoom(1 / 1.1)

    def _on_canvas_click(self, event):
        """Click on canvas background (not on a box) — show pixel info."""
        ox, oy = self._canvas_img_offset
        dw, dh = self._canvas_display_size
        ow, oh = self._canvas_orig_size
        if dw and dh and ow and oh:
            px = int((event.x - ox) * ow / dw)
            py = int((event.y - oy) * oh / dh)
            if 0 <= px < ow and 0 <= py < oh:
                self.set_status(f"📍 Image coords: ({px}, {py})")

    def _on_canvas_hover(self, _event):
        """Placeholder — per-box hover is handled by tag_bind."""

    def _on_canvas_leave(self, _event):
        self._hide_tooltip()

    def _on_box_click(self, text: str, conf: float, source: str):
        """Highlight the clicked OCR word in the Raw OCR Text tab."""
        self._hide_tooltip()
        self.tabview.set("Raw OCR Text")
        tw = self.raw_data_box._textbox
        tw.tag_remove("box_hit", "1.0", "end")
        idx = "1.0"
        count = 0
        while True:
            pos = tw.search(text, idx, "end", nocase=True)
            if not pos:
                break
            end_pos = f"{pos}+{len(text)}c"
            tw.tag_add("box_hit", pos, end_pos)
            if count == 0:
                tw.see(pos)   # scroll to first occurrence
            idx = end_pos
            count += 1
        self.set_status(
            f"🔍 OCR box [{source}]: '{text[:60]}' "
            f"(conf {conf * 100:.0f}%) — {count} match{'es' if count != 1 else ''}")

    def _show_box_tooltip(self, event, text: str, conf: float, source: str):
        """Show a small floating tooltip with OCR text and confidence."""
        self._hide_tooltip()
        tw = tk.Toplevel(self)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{event.x_root + 12}+{event.y_root - 36}")
        msg = f"[{source}]  conf: {conf * 100:.0f}%\n{text[:80]}"
        lbl = tk.Label(
            tw, text=msg, justify="left",
            background="#1E293B", foreground="#00FF88",
            font=("Consolas", 9), relief="solid", borderwidth=1,
            padx=6, pady=4)
        lbl.pack()
        self._tooltip_window = tw

    def _hide_tooltip(self):
        if self._tooltip_window:
            try:
                self._tooltip_window.destroy()
            except Exception:
                pass
            self._tooltip_window = None

    # ──────────────────────────────────────────────────────────
    # Box-overlay controls
    # ──────────────────────────────────────────────────────────

    def _toggle_boxes(self):
        self._show_boxes = not self._show_boxes
        if self._show_boxes:
            self._boxes_btn.configure(
                text="🔲 Boxes ON", fg_color="#1D4ED8", hover_color="#1E40AF")
        else:
            self._boxes_btn.configure(
                text="⬜ Boxes OFF", fg_color="#374151", hover_color="#1F2937")
        self._render_image()

    def _on_box_source_change(self, value: str):
        self._box_source = value
        self._render_image()

    # ──────────────────────────────────────────────────────────
    # OCR text search / highlight
    # ──────────────────────────────────────────────────────────

    def _ocr_text_search(self):
        """Highlight all occurrences of the search term in the Raw OCR Text box."""
        query = self._ocr_search_var.get().strip()
        tw = self.raw_data_box._textbox
        tw.tag_remove("search_hit", "1.0", "end")
        if not query:
            return
        idx = "1.0"
        count = 0
        first_pos = None
        while True:
            pos = tw.search(query, idx, "end", nocase=True)
            if not pos:
                break
            end_pos = f"{pos}+{len(query)}c"
            tw.tag_add("search_hit", pos, end_pos)
            if first_pos is None:
                first_pos = pos
            idx = end_pos
            count += 1
        if first_pos:
            tw.see(first_pos)
        self.set_status(
            f"🔍 Found {count} occurrence{'s' if count != 1 else ''} of '{query}'")

    def _ocr_text_clear_search(self):
        """Clear the search highlight."""
        self._ocr_search_var.set("")
        self.raw_data_box._textbox.tag_remove("search_hit", "1.0", "end")
        self.set_status("Search cleared.")

    # ──────────────────────────────────────────────────────────
    # Single Scan
    # ──────────────────────────────────────────────────────────

    def update_textbox(self, textbox, text: str):
        textbox.configure(state="normal")
        textbox.delete("0.0", "end")
        textbox.insert("0.0", text)
        textbox.configure(state="disabled")

    def start_scan_thread(self):
        if not self.current_image_path:
            self.set_status("⚠️  No image selected.")
            return
        if self._batch_running:
            self.set_status("⚠️  Batch scan in progress. Please wait.")
            return
        if not self._scan_lock.acquire(blocking=False):
            return
        self.btn_scan.configure(state="disabled", text="⏳ SCANNING…")
        self.progress_bar.start()
        self.update_textbox(self.smart_data_box,
                            "🔬 Running Dual-Engine OCR…\n\n"
                            "• Tesseract LSTM\n"
                            "• EasyOCR CRNN\n\n"
                            "First run: EasyOCR may take ~1 min to load model…")
        self.update_textbox(self.raw_data_box, "⚙️  Processing…")
        self.set_status(f"Scanning: {os.path.basename(self.current_image_path)}")
        t = threading.Thread(target=self._run_single_scan,
                              args=(self.current_image_path,), daemon=True)
        t.start()

    def _run_single_scan(self, image_path: str):
        t0 = time.time()
        try:
            auto_deskew = _config.get("auto_deskew", False)
            original_img = Image.open(image_path)
            img_tess     = preprocess_image(original_img, auto_deskew=auto_deskew)
            img_easy     = preprocess_for_easyocr(original_img, auto_deskew=auto_deskew)

            # 3 workers: Tesseract text, EasyOCR text+boxes, Tesseract word boxes
            with ThreadPoolExecutor(max_workers=3) as ex:
                fut_tess      = ex.submit(run_tesseract,       img_tess)
                fut_easy      = ex.submit(run_easyocr,         img_easy)
                fut_tess_boxes = ex.submit(get_tess_word_boxes, img_tess)
                tess_text              = fut_tess.result()
                easy_text, easy_conf, easy_boxes = fut_easy.result()
                tess_boxes             = fut_tess_boxes.result()

            if easy_text.strip():
                engine_label   = f"Tesseract + EasyOCR ✅ (conf: {easy_conf * 100:.1f}%)"
                extracted_text = merge_dual_ocr(tess_text, easy_text, easy_conf)
            else:
                engine_label   = "Tesseract only"
                easy_conf      = 0.0
                extracted_text = tess_text

            metadata, receipt_type = parse_receipt(extracted_text)
            elapsed_ms = int((time.time() - t0) * 1000)

            if metadata:
                _db.save_scan(
                    metadata, extracted_text, receipt_type,
                    processing_time_ms=elapsed_ms,
                    image_path=image_path,
                    ocr_engine=engine_label,
                    ocr_confidence=easy_conf,
                )

            smart_out, raw_out = self._format_output(
                engine_label, receipt_type, metadata, extracted_text,
                easy_text, elapsed_ms, easy_conf)

            self.after(0, self._finish_single_scan,
                       smart_out, raw_out, metadata, receipt_type,
                       elapsed_ms, engine_label, easy_boxes, tess_boxes)

        except pytesseract.TesseractError as exc:
            err = ("Language pack 'vie.traineddata' missing!"
                   if 'Failed loading language' in str(exc) else str(exc))
            logger.error("Tesseract error on %s: %s", image_path, err)
            self.after(0, self._finish_single_scan,
                       f"ERROR\n{err}", err, None, "unknown", 0, "", [], [])
        except Exception as exc:
            logger.error("Scan error on %s: %s", image_path, exc, exc_info=True)
            self.after(0, self._finish_single_scan,
                       f"CRITICAL ERROR\n{exc}", str(exc), None, "unknown", 0, "", [], [])

    def _format_output(self, engine_label, receipt_type, metadata,
                       extracted_text, easy_text, elapsed_ms, easy_conf: float = 0.0):
        type_label = {
            "type1_web":   "🌐 Type 1 — Web UI (vertical labels)",
            "type2_vetc":  "📱 Type 2 — VETC App (key:value)",
        }.get(receipt_type, "❓ Unknown")

        conf_bar = ""
        if metadata and easy_conf > 0:
            filled = int(easy_conf * 20)
            conf_bar = f"\n🎯 Conf: [{'█' * filled}{'░' * (20 - filled)}] {easy_conf * 100:.1f}%"

        smart = (f"🔬 Engine:  {engine_label}\n"
                 f"📋 Type:    {type_label}\n"
                 f"⏱  Time:    {elapsed_ms} ms"
                 + conf_bar + "\n"
                 + "─" * 44 + "\n\n")
        if metadata:
            for k, v in metadata.items():
                smart += f"► {k}:\n   [ {v} ]\n\n"
        else:
            smart += "⚠️  No receipt data found.\nCheck Raw OCR Text tab for raw output."

        raw = "=== MERGED OCR TEXT ===\n"
        raw += extracted_text.strip() or "[No text found]"
        if easy_text.strip():
            raw += "\n\n=== EASYOCR RAW ===\n" + easy_text.strip()

        return smart.strip(), raw

    def _finish_single_scan(self, smart_text, raw_text, metadata,
                             receipt_type, elapsed_ms, engine_label,
                             easy_boxes=None, tess_boxes=None):
        self.update_textbox(self.smart_data_box, smart_text)
        self.update_textbox(self.raw_data_box,   raw_text)
        self.latest_metadata     = metadata or {}
        self.latest_receipt_type = receipt_type

        # Store OCR boxes and re-render preview with overlays
        self._ocr_boxes_easy = easy_boxes or []
        self._ocr_boxes_tess = tess_boxes or []
        self._render_image()

        easy_n = len(self._ocr_boxes_easy)
        tess_n = len(self._ocr_boxes_tess)
        logger.info("Boxes — EasyOCR: %d, Tesseract: %d", easy_n, tess_n)

        self.progress_bar.stop()
        self.progress_bar.set(0)
        self.btn_scan.configure(state="normal", text="▶  START SCAN  (Ctrl+S)")
        self._scan_lock.release()
        self._refresh_stats_label()
        self._refresh_today_label()
        fields_n = len(metadata) if metadata else 0
        status = (f"✅ Done in {elapsed_ms} ms | "
                  f"Engine: {engine_label} | "
                  f"Fields: {fields_n} | "
                  f"Boxes: {easy_n} Easy / {tess_n} Tess")
        self.set_status(status)
        # Show toast only when scan actually produced fields
        if fields_n > 0:
            self._show_toast(f"✅ {fields_n} fields extracted in {elapsed_ms} ms",
                             color="#0F2027", text_color="#34D399")
        logger.info("Single scan done: %dms, fields=%d",
                    elapsed_ms, len(metadata) if metadata else 0)

    # ──────────────────────────────────────────────────────────
    # Batch Scan
    # ──────────────────────────────────────────────────────────

    def start_batch_scan(self):
        if not self.image_files:
            messagebox.showwarning("No Images", "Load a directory or file first.")
            return
        if self._batch_running:
            # Cancel running batch
            self._batch_cancel.set()
            self.set_status("⛔ Cancelling batch…")
            return
        self._batch_running = True
        self._batch_cancel.clear()
        self._batch_progress.grid()
        self._batch_progress.set(0)
        self.btn_scan.configure(state="disabled")
        self.set_status(f"⚡ Batch scan started: {len(self.image_files)} images")
        logger.info("Batch scan started: %d images", len(self.image_files))
        t = threading.Thread(target=self._run_batch_scan, daemon=True)
        t.start()

    def _run_batch_scan(self):
        total   = len(self.image_files)
        success = 0
        failed  = 0
        t_start = time.time()
        auto_deskew = _config.get("auto_deskew", False)

        for idx, image_path in enumerate(self.image_files):
            if self._batch_cancel.is_set():
                break
            progress = idx / total
            self.after(0, self._batch_progress.set, progress)

            # ETA calculation
            elapsed_so_far = time.time() - t_start
            if idx > 0:
                avg_per_img = elapsed_so_far / idx
                eta_s = int(avg_per_img * (total - idx))
                eta_str = f"ETA {eta_s}s"
            else:
                eta_str = "…"
            self.after(0, self.set_status,
                       f"⚡ Batch [{idx + 1}/{total}]  {eta_str}: "
                       f"{os.path.basename(image_path)}")

            try:
                original_img = Image.open(image_path)
                img_tess     = preprocess_image(original_img, auto_deskew=auto_deskew)
                img_easy     = preprocess_for_easyocr(original_img, auto_deskew=auto_deskew)
                t0 = time.time()
                with ThreadPoolExecutor(max_workers=2) as ex:
                    tess_text            = ex.submit(run_tesseract, img_tess).result()
                    easy_text, conf, _   = ex.submit(run_easyocr,   img_easy).result()
                elapsed_ms = int((time.time() - t0) * 1000)

                if easy_text.strip():
                    engine_lbl     = f"Dual (conf {conf * 100:.0f}%)"
                    extracted_text = merge_dual_ocr(tess_text, easy_text, conf)
                else:
                    engine_lbl     = "Tesseract"
                    conf           = 0.0
                    extracted_text = tess_text

                metadata, receipt_type = parse_receipt(extracted_text)
                if metadata:
                    _db.save_scan(metadata, extracted_text, receipt_type,
                                  processing_time_ms=elapsed_ms,
                                  image_path=image_path,
                                  ocr_engine=engine_lbl,
                                  ocr_confidence=conf)
                success += 1

                # Show last image in preview
                self.after(0, self.display_image, image_path)

            except Exception as exc:
                failed += 1
                logger.error("Batch scan error on %s: %s", image_path, exc)

        elapsed_total = int(time.time() - t_start)
        self.after(0, self._finish_batch_scan, total, success, failed, elapsed_total)

    def _finish_batch_scan(self, total, success, failed, elapsed_total):
        self._batch_running = False
        self._batch_cancel.clear()
        self._batch_progress.set(1)
        self.btn_scan.configure(state="normal", text="▶  START SCAN  (Ctrl+S)")
        self._refresh_stats_label()
        self._refresh_today_label()
        msg = (f"⚡ Batch complete: {success}/{total} OK, "
               f"{failed} failed — {elapsed_total}s total")
        self.set_status(msg)
        logger.info("Batch scan done: %d/%d OK, %d failed, %ds",
                    success, total, failed, elapsed_total)
        messagebox.showinfo("Batch Complete",
                            f"Processed {total} images\n"
                            f"✅ Success: {success}\n"
                            f"❌ Failed:  {failed}\n"
                            f"⏱  Total:   {elapsed_total}s")
        self.after(3000, self._batch_progress.grid_remove)

    # ──────────────────────────────────────────────────────────
    # Export / Clipboard
    # ──────────────────────────────────────────────────────────

    def save_annotated_image(self):
        """Save the current image with OCR bounding boxes burned directly into it."""
        if self._base_pil_img is None:
            self.set_status("⚠️  No image loaded.")
            return

        ts   = datetime.now().strftime('%Y%m%d_%H%M%S')
        base = os.path.splitext(os.path.basename(self.current_image_path or "image"))[0]
        path = ctk.filedialog.asksaveasfilename(
            defaultextension=".png",
            initialfile=f"{base}_annotated_{ts}.png",
            filetypes=[("PNG image", "*.png"), ("JPEG image", "*.jpg")],
        )
        if not path:
            return

        try:
            img = self._base_pil_img.copy()
            ow, oh = img.size
            # Draw boxes at original full resolution (no scaling needed — OCR coords
            # are already in original-image pixel space)
            img = self._draw_boxes_on_image(img, ow, oh, ow, oh)
            img.save(path)
            self._flash_button(self._btn_save_img, "✅ Saved!", "💾 Save Image")
            self.set_status(f"Annotated image saved: {path}")
            logger.info("Annotated image saved: %s", path)
        except Exception as exc:
            logger.error("Save annotated image error: %s", exc)
            messagebox.showerror("Save Error", f"Failed to save image:\n{exc}")

    def copy_data_to_clipboard(self):
        if self.latest_metadata:
            self.clipboard_clear()
            self.clipboard_append(
                json.dumps(self.latest_metadata, ensure_ascii=False, indent=4))
            self._flash_button(self.btn_copy, "✅ Copied!", "📄 Copy JSON")
            self.set_status("Data copied to clipboard.")
        else:
            self._flash_button(self.btn_copy, "⚠️ No Data", "📄 Copy JSON")

    def export_to_csv(self):
        if not self.latest_metadata:
            self._flash_button(self.btn_export_csv, "⚠️ No Data", "📊 CSV")
            return
        ts   = datetime.now().strftime('%Y%m%d_%H%M%S')
        path = ctk.filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=f"receipt_{ts}.csv",
            filetypes=[("CSV files", "*.csv")]
        )
        if not path:
            return
        try:
            with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                w = csv.writer(f)
                w.writerow(self.latest_metadata.keys())
                w.writerow(self.latest_metadata.values())
            self._flash_button(self.btn_export_csv, "✅ Saved!", "📊 CSV")
            self.set_status(f"CSV exported: {path}")
            logger.info("CSV exported: %s", path)
        except Exception as exc:
            logger.error("CSV export error: %s", exc)
            messagebox.showerror("Export Error", f"Failed:\n{exc}")

    def export_to_json(self):
        if not self.latest_metadata:
            self._flash_button(self.btn_export_json, "⚠️ No Data", "🗂 JSON")
            return
        ts   = datetime.now().strftime('%Y%m%d_%H%M%S')
        path = ctk.filedialog.asksaveasfilename(
            defaultextension=".json",
            initialfile=f"receipt_{ts}.json",
            filetypes=[("JSON files", "*.json")]
        )
        if not path:
            return
        try:
            payload = {
                "exported_at":   datetime.now().isoformat(),
                "app_version":   APP_VERSION,
                "receipt_type":  self.latest_receipt_type,
                "fields":        self.latest_metadata,
            }
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=4)
            self._flash_button(self.btn_export_json, "✅ Saved!", "🗂 JSON")
            self.set_status(f"JSON exported: {path}")
            logger.info("JSON exported: %s", path)
        except Exception as exc:
            logger.error("JSON export error: %s", exc)
            messagebox.showerror("Export Error", f"Failed:\n{exc}")

    def _flash_button(self, btn, temp_text: str, orig_text: str, delay: int = 2000):
        btn.configure(text=temp_text)
        self.after(delay, lambda: btn.configure(text=orig_text))

    # ──────────────────────────────────────────────────────────
    # Dialogs
    # ──────────────────────────────────────────────────────────

    def _open_history(self):
        HistoryDialog(self, _db)

    def _open_statistics(self):
        StatisticsDialog(self, _db)

    def _open_shortcuts(self):
        ShortcutsDialog(self)

    def _open_settings(self):
        SettingsDialog(self, _config, on_save=self._apply_settings)

    def _apply_settings(self, cfg: dict):
        ctk.set_appearance_mode(cfg.get("theme", "Dark"))
        configure_tesseract(cfg)
        # Reinitialise DB if path changed; guard against concurrent scans
        global _db
        if _db.db_path != cfg["db_path"]:
            if self._batch_running or not self._scan_lock.acquire(blocking=False):
                messagebox.showwarning(
                    "Settings",
                    "Cannot change DB path while a scan is running.\n"
                    "Please wait for the current operation to finish.")
                return
            try:
                _db = DatabaseManager(cfg["db_path"])
                _db.init_schema()
            finally:
                self._scan_lock.release()
        log_level = getattr(logging, cfg.get("log_level", "INFO"), logging.INFO)
        logging.getLogger(APP_NAME).setLevel(log_level)
        self.set_status("Settings applied.")
        logger.info("Settings applied: theme=%s, tess=%s, db=%s",
                    cfg.get("theme"), cfg.get("tesseract_path"), cfg.get("db_path"))

    # ──────────────────────────────────────────────────────────
    # Stats
    # ──────────────────────────────────────────────────────────

    def _refresh_stats_label(self):
        stats = _db.get_statistics()
        if stats:
            lines = [f"Total scans: {stats.get('total', 0)}"]
            for rtype, cnt in stats.get("by_type", {}).items():
                lines.append(f"  {rtype}: {cnt}")
            avg = stats.get("avg_processing_ms", 0)
            if avg is not None and avg:
                lines.append(f"Avg time: {avg:.0f} ms")
            self._stats_label.configure(text="\n".join(lines))


# ─────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _db.init_schema()
    app = NextLevelOCRScanner()
    app.mainloop()

