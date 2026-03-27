import customtkinter as ctk  # type: ignore
from tkinter import ttk, filedialog, messagebox
import csv
import sqlite3
import csv
from datetime import datetime
import os
from db.sqlite_manager import get_recent_scans, delete_scan, update_scan
from utils.helpers import create_header

class EditDialog(ctk.CTkToplevel):
    def __init__(self, master, scan_id, current_plate, current_price, current_status, callback):
        super().__init__(master)
        self.title("✏️ Sửa dữ liệu OCR")
        self.geometry("400x350")
        self.attributes("-topmost", True)
        self.configure(fg_color="#0F172A")
        
        self.scan_id = scan_id
        self.callback = callback
        
        lbl = ctk.CTkLabel(self, text=f"Chỉnh sửa Bản ghi #{scan_id}", font=("Segoe UI", 18, "bold"), text_color="white")
        lbl.pack(pady=(20, 10))
        
        f = ctk.CTkFrame(self, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=40)
        
        ctk.CTkLabel(f, text="Biển số:", text_color="gray", font=("Segoe UI", 13)).grid(row=0, column=0, sticky="w", pady=10)
        self.ent_plate = ctk.CTkEntry(f, width=220, fg_color="#1E293B", border_color="#334155")
        self.ent_plate.grid(row=0, column=1, padx=10, pady=10)
        self.ent_plate.insert(0, str(current_plate))
        
        ctk.CTkLabel(f, text="Giá tiền:", text_color="gray", font=("Segoe UI", 13)).grid(row=1, column=0, sticky="w", pady=10)
        self.ent_price = ctk.CTkEntry(f, width=220, fg_color="#1E293B", border_color="#334155")
        self.ent_price.grid(row=1, column=1, padx=10, pady=10)
        self.ent_price.insert(0, str(current_price))
        
        ctk.CTkLabel(f, text="Trạng thái:", text_color="gray", font=("Segoe UI", 13)).grid(row=2, column=0, sticky="w", pady=10)
        self.ent_status = ctk.CTkEntry(f, width=220, fg_color="#1E293B", border_color="#334155")
        self.ent_status.grid(row=2, column=1, padx=10, pady=10)
        self.ent_status.insert(0, str(current_status))
        
        btn_save = ctk.CTkButton(self, text="Lưu thay đổi", fg_color="#10B981", hover_color="#059669", font=("Segoe UI", 14, "bold"), command=self.save)
        btn_save.pack(pady=20)
        
    def save(self):
        self.callback(self.scan_id, self.ent_plate.get(), self.ent_price.get(), self.ent_status.get())
        self.destroy()

class HistoryView(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="#0F172A", corner_radius=15)
        
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.header = ctk.CTkFrame(self, fg_color="transparent")
        self.header.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 0))
        lbl_title = ctk.CTkLabel(self.header, text="📁 Database Manager", font=ctk.CTkFont(family="Segoe UI", size=28, weight="bold"))
        lbl_title.pack(side="left")
        
        # Tools row
        self.tools_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.tools_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=15)
        
        self.btn_refresh = ctk.CTkButton(self.tools_frame, text="🔄 Refresh", font=ctk.CTkFont(weight="bold"), fg_color="#3B82F6", hover_color="#2563EB", command=self.load_data, width=100)
        self.btn_refresh.pack(side="left", padx=5)

        self.btn_export = ctk.CTkButton(self.tools_frame, text="📊 Export CSV", font=ctk.CTkFont(weight="bold"), fg_color="#10B981", hover_color="#059669", command=self.export_csv, width=120)
        self.btn_export.pack(side="left", padx=5)
        
        self.btn_delete = ctk.CTkButton(self.tools_frame, text="🗑️ Delete", font=ctk.CTkFont(weight="bold"), fg_color="#EF4444", hover_color="#DC2626", command=self.delete_selected, width=100)
        self.btn_delete.pack(side="right", padx=5)

        self.btn_edit = ctk.CTkButton(self.tools_frame, text="✏️ Edit OCR", font=ctk.CTkFont(weight="bold"), fg_color="#F59E0B", hover_color="#D97706", command=self.edit_selected, width=100)
        self.btn_edit.pack(side="right", padx=5)

        # Table frame
        self.table_frame = ctk.CTkFrame(self, fg_color="#1E293B", corner_radius=15)
        self.table_frame.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 20))
        self.table_frame.grid_rowconfigure(0, weight=1)
        self.table_frame.grid_columnconfigure(0, weight=1)
        
        # Style ttk Treeview for dark mode
        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview", 
                        background="#0F172A", foreground="#E2E8F0",
                        rowheight=35, fieldbackground="#0F172A",
                        bordercolor="#334155", lightcolor="#334155", font=("Segoe UI", 11))
        style.map('Treeview', background=[('selected', '#3B82F6')])
        style.configure("Treeview.Heading", background="#1E293B", foreground="white", relief="flat", font=("Segoe UI", 12, "bold"))
        style.map("Treeview.Heading", background=[('active', '#334155')])

        columns = ("id", "time", "type", "plate", "price", "status")
        self.tree = ttk.Treeview(self.table_frame, columns=columns, show="headings")
        
        self.tree.heading("id", text="ID")
        self.tree.heading("time", text="Scan Time")
        self.tree.heading("type", text="Receipt Type")
        self.tree.heading("plate", text="License Plate")
        self.tree.heading("price", text="Price")
        self.tree.heading("status", text="Status")
        
        self.tree.column("id", width=50, anchor="center")
        self.tree.column("time", width=150, anchor="center")
        self.tree.column("type", width=120, anchor="center")
        self.tree.column("plate", width=120, anchor="center")
        self.tree.column("price", width=100, anchor="e")
        self.tree.column("status", width=100, anchor="center")

        self.tree.grid(row=0, column=0, sticky="nsew")
        
        scrollbar = ttk.Scrollbar(self.table_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

    def on_show(self):
        self.load_data()

    def load_data(self):
        # Clear existing
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        rows = get_recent_scans(500)
        for r in rows:
            self.tree.insert("", "end", values=(
                r["id"], r["scan_time"], r["receipt_type"],
                r["license_plate"], r["price"], r["status"]
            ))

    def delete_selected(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Warning", "Vui lòng chọn ít nhất một dòng để xóa!")
            return
            
        if messagebox.askyesno("Confirm", "Bạn có chắc muốn xóa các dòng đã chọn?"):
            for item in selected:
                scan_id = self.tree.item(item, "values")[0]
                delete_scan(scan_id)
            self.load_data()

    def edit_selected(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Warning", "Vui lòng chọn một dòng để chỉnh sửa!")
            return
        
        item = selected[0]
        values = self.tree.item(item, "values")
        scan_id = values[0]
        plate = values[3]
        price = values[4]
        status = values[5]
        
        # Disable main window while editing
        EditDialog(self, scan_id, plate, price, status, self._on_edit_save)
        
    def _on_edit_save(self, scan_id, plate, price, status):
        update_scan(scan_id, plate, price, status)
        self.load_data()

    def export_csv(self):
        rows = get_recent_scans(1000)
        if not rows:
            return
            
        filename = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path = ctk.filedialog.asksaveasfilename(defaultextension=".csv", initialfile=filename)
        if path:
            try:
                with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                    writer.writeheader()
                    writer.writerows(rows)
            except Exception as e:
                print("Export error:", e)
