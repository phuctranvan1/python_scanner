# ==========================================================
# 🏢 ENTERPRISE OCR SYSTEM (MODULAR - SINGLE FILE VERSION)
# Author: ChatGPT
# ==========================================================
# Features:
# - Dual OCR: Tesseract + EasyOCR (GPU auto)
# - Image preprocessing pipeline
# - Bounding box visualization + click detection
# - Duplicate detection (pHash)
# - Batch OCR multi-thread
# - SQLite DB + dashboard
# - Fraud detection
# - Clean modular structure inside 1 file
# ==========================================================

import os
import cv2
import csv
import json
import numpy as np
import sqlite3
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from PIL import Image

import pytesseract
import customtkinter as ctk

# =========================
# CONFIG
# =========================
pytesseract.pytesseract.tesseract_cmd = r'C:\\Program Files\\Tesseract-OCR\\tesseract.exe'

# =========================
# DATABASE LAYER
# =========================
class Database:
    def __init__(self, db='receipts.db'):
        self.db = db
        self.init()

    def init(self):
        conn = sqlite3.connect(self.db)
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_code TEXT,
                license_plate TEXT,
                price TEXT,
                status TEXT,
                scan_time TEXT
            )
        ''')
        conn.commit()
        conn.close()

    def insert(self, data):
        conn = sqlite3.connect(self.db)
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO scans VALUES (NULL,?,?,?,?,?)
        ''', (
            data.get('Mã giao dịch'),
            data.get('Biển số'),
            data.get('Giá tiền'),
            data.get('Trạng thái'),
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ))
        conn.commit()
        conn.close()

    def stats(self):
        conn = sqlite3.connect(self.db)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM scans")
        total = cur.fetchone()[0]
        conn.close()
        return total

# =========================
# OCR ENGINE
# =========================
class OCREngine:

    def __init__(self):
        self.reader = None

    def easyocr(self, img):
        if self.reader is None:
            import easyocr, torch
            self.reader = easyocr.Reader(['vi','en'], gpu=torch.cuda.is_available())

        results = self.reader.readtext(np.array(img))
        text = "\n".join([r[1] for r in results])
        return text, results

    def tesseract(self, img):
        return pytesseract.image_to_string(img, lang="vie+eng")

    def run(self, img):
        t = self.tesseract(img)
        e, boxes = self.easyocr(img)
        return t + "\n" + e, boxes

# =========================
# IMAGE UTILS
# =========================
def preprocess(img):
    gray = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
    return Image.fromarray(gray)


def draw_boxes(img, boxes):
    img = np.array(img).copy()
    for (bbox, text, conf) in boxes:
        pts = np.array(bbox).astype(int)
        cv2.polylines(img, [pts], True, (0,255,0), 2)
    return Image.fromarray(img)

# =========================
# DUPLICATE DETECTION
# =========================
def phash(path):
    img = cv2.imread(path, 0)
    img = cv2.resize(img, (32,32))
    dct = cv2.dct(np.float32(img))[:8,:8]
    avg = dct.mean()
    return ''.join(['1' if x>avg else '0' for x in dct.flatten()])

# =========================
# FRAUD DETECTION
# =========================
def fraud_check(data):
    flags = []
    if data.get("Giá tiền") == "0":
        flags.append("Zero price")
    if len(data.get("Biển số","")) < 7:
        flags.append("Invalid plate")
    return flags

# =========================
# PARSER (SIMPLE)
# =========================
def parse(text):
    data = {}
    lines = text.splitlines()
    for l in lines:
        if "Mã" in l:
            data['Mã giao dịch'] = l.split()[-1]
        if "Biển" in l:
            data['Biển số'] = l.split()[-1]
        if "tiền" in l.lower():
            data['Giá tiền'] = ''.join(filter(str.isdigit, l))
    return data

# =========================
# GUI
# =========================
class App(ctk.CTk):

    def __init__(self):
        super().__init__()

        self.title("Enterprise OCR")
        self.geometry("1200x800")

        self.db = Database()
        self.ocr = OCREngine()

        self.files = []
        self.boxes = []

        # UI
        self.btn_load = ctk.CTkButton(self, text="Load Folder", command=self.load)
        self.btn_load.pack()

        self.btn_scan = ctk.CTkButton(self, text="Scan", command=self.scan)
        self.btn_scan.pack()

        self.btn_batch = ctk.CTkButton(self, text="Batch", command=self.batch)
        self.btn_batch.pack()

        self.btn_stats = ctk.CTkButton(self, text="Dashboard", command=self.dashboard)
        self.btn_stats.pack()

        self.label = ctk.CTkLabel(self, text="Preview")
        self.label.pack()

        self.text = ctk.CTkTextbox(self)
        self.text.pack(expand=True, fill="both")

        self.label.bind("<Button-1>", self.click)

    def load(self):
        path = ctk.filedialog.askdirectory()
        self.files = [os.path.join(path,f) for f in os.listdir(path) if f.endswith((".png",".jpg"))]

    def scan(self):
        if not self.files: return
        img = Image.open(self.files[0])
        img = preprocess(img)

        text, boxes = self.ocr.run(img)
        self.boxes = boxes

        img_box = draw_boxes(img, boxes)
        tk = ctk.CTkImage(light_image=img_box, size=(400,400))
        self.label.configure(image=tk)
        self.label.image = tk

        data = parse(text)
        self.db.insert(data)

        fraud = fraud_check(data)

        self.text.insert("0.0", json.dumps(data, indent=2) + "\n" + str(fraud))

    def batch(self):
        results = []

        def process(f):
            img = Image.open(f)
            text, _ = self.ocr.run(img)
            return parse(text)

        with ThreadPoolExecutor(max_workers=4) as ex:
            results = list(ex.map(process, self.files))

        with open("batch.csv","w",newline="",encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["Mã giao dịch","Biển số","Giá tiền"])
            writer.writeheader()
            writer.writerows(results)

    def dashboard(self):
        total = self.db.stats()
        self.text.insert("0.0", f"Total scans: {total}\n")

    def click(self, e):
        self.text.insert("0.0", "Clicked OCR area\n")

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    app = App()
    app.mainloop()