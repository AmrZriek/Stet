import os
import sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

from stet.core.config import ConfigManager
from stet.ui.main_window import CorrectionWindow

class DummySig:
    def connect(self, fn):
        pass

class DummyModel:
    def __init__(self):
        self.status_changed = DummySig()

def main():
    app = QApplication(sys.argv)
    cfg = ConfigManager()
    dummy = DummyModel()
    text = "The algorithmm performence is grate, but we need to fix the memry leek before relese."
    win = CorrectionWindow(text, dummy, dummy, cfg)
    
    win.status_lbl.setText("● Gemma 4 E2B")
    win.resize(740, 520)
    win.show()
    
    app.processEvents()
    
    out_path = root_dir / "marketing" / "promo-video" / "snapshots" / "stet_actual_ui.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pm = win.grab()
    pm.save(str(out_path))
    print(f"Captured screenshot: {out_path} (size: {pm.width()}x{pm.height()})")

if __name__ == "__main__":
    main()
