import sys
import os

# Ensure the scan_pro directory is in Python path since we are running main.py 
# from inside it, but just in case:
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ui.app import MainApp

if __name__ == "__main__":
    app = MainApp()
    app.mainloop()
