import customtkinter as ctk  # type: ignore
import sqlite3
from utils.helpers import create_header

class DashboardView(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="#0F172A", corner_radius=15)

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Header
        self.header = ctk.CTkFrame(self, fg_color="transparent")
        self.header.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 0))
        lbl_title = ctk.CTkLabel(self.header, text="📊 Dashboard Analytics", font=ctk.CTkFont(family="Segoe UI", size=28, weight="bold"))
        lbl_title.pack(side="left")

        # Stats Container
        self.stats_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.stats_frame.grid(row=1, column=0, sticky="nsew")
        self.stats_frame.grid_columnconfigure((0,1,2), weight=1)

        # Stat cards
        self.total_scans_lbl = self.create_stat_card(self.stats_frame, "Total Scans Dành Cho VETC", "0", 0, "#10B981")
        self.success_rate_lbl = self.create_stat_card(self.stats_frame, "Average Success Rate", "0%", 1, "#3B82F6")
        self.types_lbl = self.create_stat_card(self.stats_frame, "Most Scanner Type", "VETC App", 2, "#F59E0B")

    def create_stat_card(self, parent, title, value, col, accent_color):
        card = ctk.CTkFrame(parent, corner_radius=20, fg_color="#1E293B", border_width=2, border_color="#334155")
        card.grid(row=0, column=col, padx=15, pady=20, sticky="nsew")
        
        # Adding a subtle accent line at the top
        accent = ctk.CTkFrame(card, fg_color=accent_color, height=6, corner_radius=6)
        accent.pack(fill="x", padx=20, pady=(20, 5))
        
        lbl_title = ctk.CTkLabel(card, text=title, font=ctk.CTkFont(family="Segoe UI", size=15), text_color="#94A3B8")
        lbl_title.pack(pady=(10, 5))
        
        lbl_val = ctk.CTkLabel(card, text=value, font=ctk.CTkFont(family="Segoe UI", size=48, weight="bold"), text_color="white")
        lbl_val.pack(pady=(5, 30))
        return lbl_val

    def on_show(self):
        # Refresh Data from DB
        try:
            conn = sqlite3.connect('receipts_pro.db')
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM scans")
            total = cursor.fetchone()[0]
            self.total_scans_lbl.configure(text=str(total))
            
            cursor.execute("SELECT COUNT(*) FROM scans WHERE license_plate != ''")
            success = cursor.fetchone()[0]
            if total > 0:
                self.success_rate_lbl.configure(text=f"{int((success/total)*100)}%")
            else:
                self.success_rate_lbl.configure(text="0%")
                
            conn.close()
        except Exception as e:
            print("DB read err:", e)
