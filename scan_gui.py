import sys
import os
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from PIL import Image, ImageTk
import pytesseract

try:
    from PIL import Image
    import pytesseract
except ImportError:
    print("Missing required libraries. Please run in your terminal:")
    print("pip install Pillow pytesseract")
    sys.exit(1)

# Set Tesseract path
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

class OCRScannerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Vietnamese OCR Scanner")
        self.root.geometry("1000x600")
        
        # Left Panel (Controls and File List)
        self.left_panel = tk.Frame(root, width=250, bg="#f0f0f0")
        self.left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)
        
        self.btn_select_dir = tk.Button(self.left_panel, text="Select Directory", command=self.load_directory)
        self.btn_select_dir.pack(fill=tk.X, pady=5)
        
        self.btn_select_file = tk.Button(self.left_panel, text="Select Image File", command=self.load_file)
        self.btn_select_file.pack(fill=tk.X, pady=5)
        
        self.file_listbox = tk.Listbox(self.left_panel)
        self.file_listbox.pack(fill=tk.BOTH, expand=True, pady=10)
        self.file_listbox.bind('<<ListboxSelect>>', self.on_select_file)
        
        # Middle Panel (Image Preview)
        self.mid_panel = tk.Frame(root, width=400)
        self.mid_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        self.lbl_image = tk.Label(self.mid_panel, text="Image Preview", bg="white", relief=tk.SUNKEN)
        self.lbl_image.pack(fill=tk.BOTH, expand=True)
        
        # Right Panel (Extracted Text)
        self.right_panel = tk.Frame(root, width=350)
        self.right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        self.btn_scan = tk.Button(self.right_panel, text="SCAN TEXT", font=("Arial", 12, "bold"), bg="#4CAF50", fg="white", command=self.scan_current_image)
        self.btn_scan.pack(fill=tk.X, pady=5)
        
        self.text_result = scrolledtext.ScrolledText(self.right_panel, font=("Consolas", 11), wrap=tk.WORD)
        self.text_result.pack(fill=tk.BOTH, expand=True)
        
        self.image_files = []
        self.current_image_path = None
        self.current_tk_image = None

        # Automatically load the default directory if it exists
        default_dir = r"C:\Users\ETC\Downloads\2303"
        if os.path.isdir(default_dir):
            self.load_directory(default_dir)
        
    def load_directory(self, dir_path=None):
        if not dir_path:
            dir_path = filedialog.askdirectory()
        if dir_path:
            self.file_listbox.delete(0, tk.END)
            self.image_files = []
            for f in sorted(os.listdir(dir_path)):
                if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff')):
                    full_path = os.path.join(dir_path, f)
                    self.image_files.append(full_path)
                    self.file_listbox.insert(tk.END, f)
                    
            if self.image_files:
                self.file_listbox.selection_set(0)
                self.on_select_file(None)

    def load_file(self):
        file_path = filedialog.askopenfilename(filetypes=[("Image Files", "*.png;*.jpg;*.jpeg;*.bmp;*.tiff")])
        if file_path:
            self.file_listbox.delete(0, tk.END)
            self.image_files = [file_path]
            self.file_listbox.insert(tk.END, os.path.basename(file_path))
            self.file_listbox.selection_set(0)
            self.on_select_file(None)
            
    def on_select_file(self, event):
        selection = self.file_listbox.curselection()
        if not selection:
            return
        index = selection[0]
        self.current_image_path = self.image_files[index]
        self.display_image(self.current_image_path)
        self.text_result.delete(1.0, tk.END)
        self.text_result.insert(tk.END, "Ready to scan. Click 'SCAN TEXT' button.")
        
    def display_image(self, path):
        try:
            img = Image.open(path)
            # Resize for preview
            img.thumbnail((400, 500), Image.Resampling.LANCZOS)
            self.current_tk_image = ImageTk.PhotoImage(img)
            self.lbl_image.config(image=self.current_tk_image, text="")
        except Exception as e:
            self.lbl_image.config(image='', text=f"Error loading image:\n{e}")

    def scan_current_image(self):
        if not self.current_image_path:
            messagebox.showwarning("Warning", "Please select an image first.")
            return
            
        self.text_result.delete(1.0, tk.END)
        self.text_result.insert(tk.END, "Scanning for text... This may take a moment...\n\n")
        self.root.update()
        
        try:
            img = Image.open(self.current_image_path)
            extracted_text = pytesseract.image_to_string(img, lang="vie")
            
            self.text_result.delete(1.0, tk.END)
            if extracted_text.strip():
                self.text_result.insert(tk.END, extracted_text)
            else:
                self.text_result.insert(tk.END, "[No readable text detected]")
                
        except pytesseract.TesseractNotFoundError:
            messagebox.showerror("Error", "Tesseract executable not found! Make sure Tesseract is installed at C:\\Program Files\\Tesseract-OCR\\tesseract.exe")
        except pytesseract.TesseractError as e:
            if 'Failed loading language' in str(e):
                err_msg = "Vietnamese language data ('vie') is not installed for Tesseract.\n\n"
                err_msg += "1. Download 'vie.traineddata' from github tesseract-ocr/tessdata_best\n"
                err_msg += "2. Place it in your tessdata folder."
                messagebox.showerror("Language Error", err_msg)
            else:
                messagebox.showerror("Tesseract Error", str(e))
        except Exception as e:
            messagebox.showerror("Error", f"An unexpected error occurred:\n{e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = OCRScannerApp(root)
    root.mainloop()
