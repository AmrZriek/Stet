from PyQt6.QtWidgets import QApplication, QStyleFactory

def _force_fusion():
    app = QApplication.instance()
    if app:
        if "Fusion" in QStyleFactory.keys():
            app.setStyle("Fusion")

_force_fusion()
