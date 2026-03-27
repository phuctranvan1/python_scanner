import customtkinter as ctk  # type: ignore
from ui.views.dashboard_view import DashboardView
from ui.views.scanner_view import ScannerView
from ui.views.history_view import HistoryView
from db.sqlite_manager import init_db

class MainApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")
        
        # Init DB
        init_db()

        self.title("ScanPro Enterprise V2")
        self.geometry("1400x900")
        self.configure(fg_color="#0F172A")  # Modern slate dark background
        
        # Setup grid: 1 col for sidebar, 1 col for main content
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # ====== Sidebar ======
        self.sidebar_frame = ctk.CTkFrame(self, width=240, corner_radius=0, fg_color="#1E293B")
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(5, weight=1)

        self.logo_label = ctk.CTkLabel(
            self.sidebar_frame, text="✨ ScanPro\nEnterprise",
            font=ctk.CTkFont(family="Segoe UI", size=26, weight="bold"),
            text_color="#38BDF8"  # Light blue accent
        )
        self.logo_label.grid(row=0, column=0, padx=20, pady=(40, 40))

        # Navigation Buttons
        self.btn_dashboard = ctk.CTkButton(
            self.sidebar_frame, text="📊 Dashboard", anchor="w", fg_color="transparent",
            hover_color="#334155", font=ctk.CTkFont(size=17, weight="bold"), height=45, corner_radius=8,
            command=lambda: self.select_frame("dashboard")
        )
        self.btn_dashboard.grid(row=1, column=0, padx=20, pady=5, sticky="ew")

        self.btn_scanner = ctk.CTkButton(
            self.sidebar_frame, text="🔍 Scan Studio", anchor="w", fg_color="transparent",
            hover_color="#334155", font=ctk.CTkFont(size=17, weight="bold"), height=45, corner_radius=8,
            command=lambda: self.select_frame("scanner")
        )
        self.btn_scanner.grid(row=2, column=0, padx=20, pady=5, sticky="ew")

        self.btn_history = ctk.CTkButton(
            self.sidebar_frame, text="📁 Data Manager", anchor="w", fg_color="transparent",
            hover_color="#334155", font=ctk.CTkFont(size=17, weight="bold"), height=45, corner_radius=8,
            command=lambda: self.select_frame("history")
        )
        self.btn_history.grid(row=3, column=0, padx=20, pady=5, sticky="ew")

        # ====== Main Views ======
        self.views = {
            "dashboard": DashboardView(self),
            "scanner": ScannerView(self),
            "history": HistoryView(self),
        }

        # Select default view
        self.select_frame("dashboard")

    def select_frame(self, name):
        # Update button colors
        self.btn_dashboard.configure(fg_color="#334155" if name == "dashboard" else "transparent")
        self.btn_scanner.configure(fg_color="#334155" if name == "scanner" else "transparent")
        self.btn_history.configure(fg_color="#334155" if name == "history" else "transparent")

        # Hide all views
        for view in self.views.values():
            view.grid_forget()

        # Show selected view
        frame = self.views[name]
        frame.grid(row=0, column=1, sticky="nsew", padx=30, pady=30)
        
        # Call on_show if exists (to refresh data)
        if hasattr(frame, 'on_show'):
            frame.on_show()
