import customtkinter as ctk  # type: ignore

def create_header(parent, title_text, font_size=24, fg_color="transparent"):
    frame = ctk.CTkFrame(parent, fg_color=fg_color, corner_radius=0)
    label = ctk.CTkLabel(frame, text=title_text, font=ctk.CTkFont(size=font_size, weight="bold"))
    label.pack(side="left", padx=20, pady=15)
    return frame
