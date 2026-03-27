import customtkinter as ctk  # type: ignore
import os
import threading
import zipfile
import shutil
from PIL import Image, ImageTk  # type: ignore
from core.ocr_engine import run_tesseract, run_easyocr, merge_dual_ocr
from core.image_processing import preprocess_image, preprocess_for_easyocr
from core.parsers import parse_receipt
from db.sqlite_manager import save_to_db
from concurrent.futures import ThreadPoolExecutor
from utils.helpers import create_header

class ScannerView(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="#0F172A", corner_radius=15)
        
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure((0, 1), weight=1)
        
        self.header = create_header(self, "🔍 Scan Studio & Batch Processing")
        self.header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 20))

        # Left Column (File list & Controls)
        self.left_panel = ctk.CTkFrame(self, fg_color="#1E293B", corner_radius=15)
        self.left_panel.grid(row=1, column=0, sticky="nsew", padx=15, pady=(0, 15))
        self.left_panel.grid_rowconfigure(2, weight=1)
        
        # Tools
        self.controls = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        self.controls.grid(row=0, column=0, sticky="ew", padx=15, pady=15)
        
        # Upper tools
        self.controls_top = ctk.CTkFrame(self.controls, fg_color="transparent")
        self.controls_top.pack(fill="x", pady=(0, 5))
        
        self.btn_load_file = ctk.CTkButton(self.controls_top, text="🖼️ Load File", font=ctk.CTkFont(weight="bold"), fg_color="#6366F1", hover_color="#4F46E5", command=self.load_file, width=100)
        self.btn_load_file.pack(side="left", padx=2, expand=True, fill="x")

        self.btn_load_zip = ctk.CTkButton(self.controls_top, text="📦 Load ZIP", font=ctk.CTkFont(weight="bold"), fg_color="#8B5CF6", hover_color="#7C3AED", command=self.load_zip_file, width=100)
        self.btn_load_zip.pack(side="left", padx=2, expand=True, fill="x")
        
        # Lower tools
        self.controls_bot = ctk.CTkFrame(self.controls, fg_color="transparent")
        self.controls_bot.pack(fill="x")
        
        self.btn_load_dir = ctk.CTkButton(self.controls_bot, text="📁 Chọn Thư Mục", font=ctk.CTkFont(weight="bold"), fg_color="#3B82F6", hover_color="#2563EB", command=self.load_directory, width=100)
        self.btn_load_dir.pack(side="left", padx=2, expand=True, fill="x")
        
        self.btn_batch_scan = ctk.CTkButton(self.controls_bot, text="▶️ Start Scan", font=ctk.CTkFont(weight="bold"), fg_color="#10B981", hover_color="#059669", command=self.start_batch, width=100)
        self.btn_batch_scan.pack(side="right", padx=2, expand=True, fill="x")

        self.progress = ctk.CTkProgressBar(self.left_panel, progress_color="#10B981", fg_color="#334155")
        self.progress.grid(row=1, column=0, sticky="ew", padx=20, pady=5)
        self.progress.set(0)

        self.file_list = ctk.CTkScrollableFrame(self.left_panel, fg_color="transparent")
        self.file_list.grid(row=2, column=0, sticky="nsew", padx=10, pady=10)
        
        # Right Column (Preview & Log)
        self.right_panel = ctk.CTkFrame(self, fg_color="#1E293B", corner_radius=15)
        self.right_panel.grid(row=1, column=1, sticky="nsew", padx=15, pady=(0, 15))
        self.right_panel.grid_rowconfigure(0, weight=2)
        self.right_panel.grid_rowconfigure(1, weight=3)

        self.preview_lbl = ctk.CTkLabel(self.right_panel, text="No Image Selected", font=ctk.CTkFont(size=16), text_color="gray")
        self.preview_lbl.grid(row=0, column=0, sticky="nsew", padx=15, pady=15)

        # Tabs for output like the old app
        self.tabview = ctk.CTkTabview(self.right_panel, segmented_button_fg_color="#334155", segmented_button_selected_color="#3B82F6", fg_color="transparent")
        self.tabview.grid(row=1, column=0, sticky="nsew", padx=15, pady=15)
        self.tabview.add("Structured Data")
        self.tabview.add("Raw OCR Text")

        self.tabview.tab("Structured Data").grid_rowconfigure(0, weight=1)
        self.tabview.tab("Structured Data").grid_columnconfigure(0, weight=1)
        self.smart_data_box = ctk.CTkTextbox(
            self.tabview.tab("Structured Data"),
            fg_color="#0F172A", text_color="#10B981", font=("Courier New", 14), corner_radius=10
        )
        self.smart_data_box.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

        self.tabview.tab("Raw OCR Text").grid_rowconfigure(0, weight=1)
        self.tabview.tab("Raw OCR Text").grid_columnconfigure(0, weight=1)
        self.raw_data_box = ctk.CTkTextbox(
            self.tabview.tab("Raw OCR Text"),
            fg_color="#0F172A", text_color="#E2E8F0", font=("Courier New", 13), corner_radius=10
        )
        self.raw_data_box.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

        self.image_files = []
        self.is_scanning = False

    def load_file(self):
        file_path = ctk.filedialog.askopenfilename(filetypes=[("Image Files", "*.png;*.jpg;*.jpeg;*.bmp;*.tiff")])
        if not file_path: return
        self.image_files = [file_path]
        self._refresh_file_list()
        self.log(f"Loaded a single file: {os.path.basename(file_path)}")

    def load_zip_file(self):
        zip_path = ctk.filedialog.askopenfilename(filetypes=[("ZIP Archives", "*.zip")])
        if not zip_path: return
        
        extract_dir = os.path.join(os.path.dirname(zip_path), os.path.splitext(os.path.basename(zip_path))[0] + "_extracted")
        try:
            if os.path.exists(extract_dir):
                shutil.rmtree(extract_dir)
            os.makedirs(extract_dir, exist_ok=True)
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
                
            self.image_files = []
            for f in sorted(os.listdir(extract_dir)):
                if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff')):
                    full_path = os.path.join(extract_dir, f)
                    self.image_files.append(full_path)
            
            self._refresh_file_list()
            self.log(f"Loaded {len(self.image_files)} from ZIP: {os.path.basename(zip_path)}")
        except Exception as e:
            self.log(f"Error loading ZIP: {e}")

    def load_directory(self):
        dir_path = ctk.filedialog.askdirectory()
        if not dir_path: return
        self.image_files = []
        for f in sorted(os.listdir(dir_path)):
            if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff')):
                full_path = os.path.join(dir_path, f)
                self.image_files.append(full_path)
        
        self._refresh_file_list()
        self.log(f"Loaded {len(self.image_files)} images from {dir_path}")

    def _refresh_file_list(self):
        for widget in self.file_list.winfo_children():
            widget.destroy()

        for full_path in self.image_files:
            btn = ctk.CTkButton(self.file_list, text=os.path.basename(full_path), anchor="w", fg_color="transparent", 
                                text_color="white", command=lambda p=full_path: self.display_image(p))
            btn.pack(fill='x', pady=2)
            
        if self.image_files:
            self.display_image(self.image_files[0])

    def display_image(self, path):
        try:
            img = Image.open(path)
            img.thumbnail((600, 600), Image.Resampling.LANCZOS)
            tk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(img.width, img.height))
            self.preview_lbl.configure(image=tk_img, text="")
            self.preview_lbl.image = tk_img
        except Exception as e:
            self.log(f"Error loading image: {e}")

    def log(self, text):
        self._append_to_box(self.smart_data_box, text)

    def log_raw(self, text):
        self._append_to_box(self.raw_data_box, text)

    def _append_to_box(self, box, text):
        box.configure(state="normal")
        box.insert("end", text + "\n")
        box.see("end")
        box.configure(state="disabled")

    def start_batch(self):
        if not self.image_files:
            self.log("No images to scan!")
            return
        if self.is_scanning:
            return
        
        self.is_scanning = True
        self.btn_batch_scan.configure(state="disabled", text="Scanning...")
        self.log("Starting batch scan...")
        self.progress.set(0)
        
        t = threading.Thread(target=self.run_batch_thread)
        t.daemon = True
        t.start()

    def run_batch_thread(self):
        total = len(self.image_files)
        for i, path in enumerate(self.image_files):
            try:
                self.after(0, self.display_image, path)
                self.after(0, self.log, f"[{i+1}/{total}] Scanning: {os.path.basename(path)}")
                
                original_img = Image.open(path)
                img_tess = preprocess_image(original_img)
                img_easy = preprocess_for_easyocr(original_img)
                
                tess_text = run_tesseract(img_tess)
                easy_text, easy_conf, _ = run_easyocr(img_easy)
                
                extracted_text = merge_dual_ocr(tess_text, easy_text, easy_conf)
                metadata, receipt_type = parse_receipt(extracted_text)
                
                # Report Raw
                raw_out = f"=== File: {os.path.basename(path)} ===\n"
                raw_out += extracted_text.strip() if extracted_text.strip() else "[No text found]"
                raw_out += "\n" + "-"*40 + "\n"
                self.after(0, self.log_raw, raw_out)
                
                # Report Structured
                engine_label = f"Tesseract + EasyOCR ✅  (conf: {easy_conf * 100:.1f}%)" if easy_text.strip() else "Tesseract only"
                type_label = {
                    "type1_web": "🌐 Loại 1 — Giao diện web (nhãn dọc)",
                    "type2_vetc": "📱 Loại 2 — VETC App (key:value)",
                    "type3_photo": "📷 Loại 3 — Ảnh chụp (watermark Timemark)",
                }.get(receipt_type, "❓ Không xác định")
                
                smart_out = f"=== File: {os.path.basename(path)} ===\n"
                smart_out += f"🔬 Engine: {engine_label}\n"
                smart_out += f"📋 Loại hóa đơn: {type_label}\n"
                smart_out += "─" * 40 + "\n"
                
                if metadata:
                    save_to_db(metadata, extracted_text, receipt_type)
                    for key, val in metadata.items():
                        smart_out += f"► {key}:\n   [ {val} ]\n\n"
                else:
                    smart_out += "Không tìm thấy dữ liệu hóa đơn.\n\n"
                
                self.after(0, self.log, smart_out.strip() + "\n")
                self.after(0, self.progress.set, (i+1)/total)
            except Exception as e:
                self.after(0, self.log, f"   -> ERROR processing {os.path.basename(path)}: {e}")
        
        self.after(0, self.finish_batch)

    def finish_batch(self):
        self.is_scanning = False
        self.btn_batch_scan.configure(state="normal", text="▶️ Start Scan")
        self.log("=== BATCH SCAN COMPLETE ===")
        self.progress.set(1.0)
