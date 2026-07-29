"""Correction history viewer dialog with modern dark card styling."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from stet.core.history import CorrectionHistory
from stet.ui.utils import _checkbox_css


def _format_ts(ts_str: str) -> str:
    """Format ISO timestamp string into clean human-readable date/time."""
    if not ts_str:
        return ""
    try:
        if "T" in ts_str:
            date_part, time_part = ts_str.split("T", 1)
            time_clean = time_part.split(".")[0]
            return f"{date_part} {time_clean}"
    except Exception:
        pass
    return ts_str


class HistoryWindow(QDialog):
    """Modern dark dialog showing past corrections with undo & side-by-side diff cards."""

    def __init__(self, history: CorrectionHistory, cfg, undo_callback, parent=None):
        super().__init__(parent)
        self._history = history
        self._cfg = cfg
        self._undo_callback = undo_callback

        self.setWindowTitle("Correction History")
        self.setMinimumSize(800, 520)

        # Set Stet window icon (Logo)
        from stet.ui.utils import set_window_icon
        set_window_icon(self)

        # Unified dark styling stylesheet (eliminating native purple accents & mismatched backgrounds)
        self.setStyleSheet("""
            QDialog {
                background: #121315;
                color: #ededee;
                font-family: 'IBM Plex Mono', 'Consolas', monospace;
            }
            QSplitter::handle {
                background: #28292c;
            }
""" + _checkbox_css() + """
            QPushButton#ghost {
                background: transparent;
                border: 1px solid #28292c;
                color: #ededee;
                font-size: 11px;
                padding: 4px 12px;
                outline: none;
            }
            QPushButton#ghost:hover {
                background: #1c1d1f;
            }
            QPushButton#primary {
                background: #C0B8A8;
                color: #121212;
                border: 1px solid #C0B8A8;
                font-size: 11px;
                font-weight: 600;
                padding: 4px 12px;
                outline: none;
            }
            QPushButton#primary:hover {
                background: #A8A094;
            }
            QPushButton#danger {
                background: transparent;
                color: #f87171;
                border: 1px solid rgba(248, 113, 113, 0.2);
                font-size: 11px;
                padding: 4px 12px;
                outline: none;
            }
            QPushButton#danger:hover {
                background: rgba(248, 113, 113, 0.1);
                border: 1px solid #f87171;
            }
            QPushButton:disabled, QPushButton#ghost:disabled, QPushButton#primary:disabled, QPushButton#danger:disabled {
                background: #1c1d20;
                color: #a0a2a8;
                border: 1px solid #2e3035;
            }
        """)

        self.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.WindowCloseButtonHint
        )

        self._build_ui()
        self._refresh()

    def mousePressEvent(self, e):
        self.raise_()
        self.activateWindow()
        super().mousePressEvent(e)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        # Header bar
        header_row = QHBoxLayout()
        header_title = QLabel("Correction History")
        header_title.setStyleSheet(
            "QLabel{color:#ededee;font-size:16px;font-weight:600;"
            "font-family:'IBM Plex Mono','Consolas',monospace;}"
        )
        header_row.addWidget(header_title)
        header_row.addStretch()

        self._counter_lbl = QLabel("")
        self._counter_lbl.setStyleSheet(
            "QLabel{color:#88898c;font-size:11px;}"
        )
        header_row.addWidget(self._counter_lbl)
        root.addLayout(header_row)

        # Main horizontal splitter: entry list | diff view
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # Left panel: entry list
        left = QWidget()
        left.setStyleSheet("QWidget{background:#121315;}")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(8)

        self._list = QListWidget()
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setStyleSheet(
            "QListWidget{background:#090a0b;border:1px solid #28292c;color:#ededee;"
            "font-size:11px;font-family:'IBM Plex Mono','Consolas',monospace;outline:none;}"
            "QListWidget::item{padding:8px 10px;border-bottom:1px solid #202124;}"
            "QListWidget::item:hover{background:#1c1d20;}"
            "QListWidget::item:selected{background:#222429;border-left:3px solid #d4a373;color:#ffffff;}"
        )
        self._list.currentItemChanged.connect(self._on_selection_changed)
        left_layout.addWidget(self._list)

        splitter.addWidget(left)

        # Right panel: Diff inspector cards & buttons
        right = QWidget()
        right.setStyleSheet("QWidget{background:#121315;}")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(10)

        # Entry Info Bar
        self._info_lbl = QLabel("Select an entry from the list to view details.")
        self._info_lbl.setStyleSheet(
            "QLabel{color:#d4a373;font-size:11px;font-weight:600;padding-bottom:4px;}"
        )
        self._info_lbl.setWordWrap(True)
        right_layout.addWidget(self._info_lbl)

        # Diff cards container (vertical split: Original top, Corrected bottom)
        diff_split = QSplitter(Qt.Orientation.Vertical)
        right_layout.addWidget(diff_split, 1)

        # Original Card
        orig_card = QFrame()
        orig_card.setObjectName("card")
        orig_card.setStyleSheet("QFrame#card{background:#090a0b;border:1px solid #28292c;}")
        orig_lay = QVBoxLayout(orig_card)
        orig_lay.setContentsMargins(10, 8, 10, 8)
        orig_lay.setSpacing(6)

        orig_hdr = QHBoxLayout()
        orig_lbl = QLabel("ORIGINAL INPUT")
        orig_lbl.setStyleSheet("QLabel{color:#f87171;font-size:10px;font-weight:700;letter-spacing:0.05em;}")
        orig_hdr.addWidget(orig_lbl)
        orig_hdr.addStretch()

        btn_copy_orig = QPushButton("Copy")
        btn_copy_orig.setObjectName("ghost")
        btn_copy_orig.setFixedHeight(22)
        btn_copy_orig.setStyleSheet("QPushButton{padding:2px 8px;font-size:10px;outline:none;}")
        btn_copy_orig.clicked.connect(self._copy_original)
        orig_hdr.addWidget(btn_copy_orig)

        orig_lay.addLayout(orig_hdr)

        self._original_view = QPlainTextEdit()
        self._original_view.setReadOnly(True)
        self._original_view.setPlaceholderText("Original text will appear here")
        self._original_view.setStyleSheet(
            "QPlainTextEdit{background:#090a0b;border:none;color:#d1d5db;"
            "font-size:12px;font-family:'IBM Plex Mono','Consolas',monospace;selection-background-color:rgba(212, 163, 115, 0.3);outline:none;}"
        )
        orig_lay.addWidget(self._original_view)
        diff_split.addWidget(orig_card)

        # Corrected Card
        corr_card = QFrame()
        corr_card.setObjectName("card")
        corr_card.setStyleSheet("QFrame#card{background:#090a0b;border:1px solid #28292c;}")
        corr_lay = QVBoxLayout(corr_card)
        corr_lay.setContentsMargins(10, 8, 10, 8)
        corr_lay.setSpacing(6)

        corr_hdr = QHBoxLayout()
        corr_lbl = QLabel("CORRECTED OUTPUT")
        corr_lbl.setStyleSheet("QLabel{color:#4ade80;font-size:10px;font-weight:700;letter-spacing:0.05em;}")
        corr_hdr.addWidget(corr_lbl)
        corr_hdr.addStretch()

        btn_copy_corr = QPushButton("Copy")
        btn_copy_corr.setObjectName("ghost")
        btn_copy_corr.setFixedHeight(22)
        btn_copy_corr.setStyleSheet("QPushButton{padding:2px 8px;font-size:10px;outline:none;}")
        btn_copy_corr.clicked.connect(self._copy_corrected)
        corr_hdr.addWidget(btn_copy_corr)

        corr_lay.addLayout(corr_hdr)

        self._corrected_view = QPlainTextEdit()
        self._corrected_view.setReadOnly(True)
        self._corrected_view.setPlaceholderText("Corrected text will appear here")
        self._corrected_view.setStyleSheet(
            "QPlainTextEdit{background:#090a0b;border:none;color:#ededee;"
            "font-size:12px;font-family:'IBM Plex Mono','Consolas',monospace;selection-background-color:rgba(212, 163, 115, 0.3);outline:none;}"
        )
        corr_lay.addWidget(self._corrected_view)
        diff_split.addWidget(corr_card)

        diff_split.setSizes([200, 200])

        # Action Buttons Toolbar
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._undo_btn = QPushButton("Undo Correction")
        self._undo_btn.setObjectName("primary")
        self._undo_btn.clicked.connect(self._undo_entry)
        self._undo_btn.setEnabled(False)
        btn_row.addWidget(self._undo_btn)

        btn_row.addStretch()

        btn_delete = QPushButton("Delete Entry")
        btn_delete.setObjectName("ghost")
        btn_delete.clicked.connect(self._delete_entry)
        btn_row.addWidget(btn_delete)

        btn_clear = QPushButton("Clear All History")
        btn_clear.setObjectName("danger")
        btn_clear.clicked.connect(self._clear_all)
        btn_row.addWidget(btn_clear)

        right_layout.addLayout(btn_row)

        # Footer checkbox
        cb_row = QHBoxLayout()
        cb_row.addStretch()
        self._enable_cb = QCheckBox("Keep correction history")
        self._enable_cb.setChecked(bool(self._cfg.get("history_enabled", True)))
        self._enable_cb.toggled.connect(self._on_toggle_enabled)
        cb_row.addWidget(self._enable_cb)
        right_layout.addLayout(cb_row)

        splitter.addWidget(right)
        splitter.setSizes([300, 470])

    def _refresh(self):
        self._list.clear()
        entries = self._history.list(200)
        count = len(entries)
        self._counter_lbl.setText(f"{count} {'entry' if count == 1 else 'entries'}")

        for entry in entries:
            mode = entry.get("mode", "?").upper()
            corrected = entry.get("corrected", "").replace("\n", " ").strip()
            if len(corrected) > 38:
                corrected = corrected[:38] + "…"
            ts_clean = _format_ts(entry.get("ts", ""))
            undone = entry.get("undone", False)

            status_tag = " [UNDONE]" if undone else ""
            line1 = f"{ts_clean}  [{mode}]{status_tag}"
            line2 = corrected or "(empty text)"
            display_text = f"{line1}\n{line2}"

            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, entry.get("id"))
            if undone:
                item.setForeground(Qt.GlobalColor.darkGray)
            self._list.addItem(item)

        if self._list.count() > 0:
            self._list.setCurrentRow(0)
            self._on_selection_changed(self._list.currentItem(), None)

    def _on_selection_changed(self, current, _previous):
        if current is None:
            self._original_view.clear()
            self._corrected_view.clear()
            self._info_lbl.setText("Select an entry from the list to view details.")
            self._undo_btn.setText("Undo Correction")
            self._undo_btn.setEnabled(False)
            self._undo_btn.setStyleSheet(
                "QPushButton{background:#1c1d20; color:#55565a; border:1px solid #2e3035; font-size:11px; padding:4px 12px; outline:none;}"
            )
            return
        entry_id = current.data(Qt.ItemDataRole.UserRole)
        entry = self._history.get(entry_id)
        if entry is None:
            return

        self._original_view.setPlainText(entry.get("original", ""))
        self._corrected_view.setPlainText(entry.get("corrected", ""))
        undone = entry.get("undone", False)

        ts_formatted = _format_ts(entry.get("ts", ""))
        mode_str = entry.get("mode", "").upper()
        strength_str = entry.get("strength", "")
        info = f"{ts_formatted}  •  Mode: {mode_str} ({strength_str})"
        if undone:
            info += "  [UNDONE]"
        self._info_lbl.setText(info)

        self._undo_btn.setEnabled(not undone)
        if not undone:
            self._undo_btn.setText("Undo Correction")
            self._undo_btn.setStyleSheet(
                "QPushButton{background:#C0B8A8; color:#121212; border:1px solid #C0B8A8; font-size:11px; font-weight:600; padding:4px 12px; outline:none;}"
                "QPushButton:hover{background:#A8A094; border:1px solid #A8A094;}"
            )
        else:
            self._undo_btn.setText("Already Undone")
            self._undo_btn.setStyleSheet(
                "QPushButton{background:#1c1d20; color:#55565a; border:1px solid #2e3035; font-size:11px; padding:4px 12px; outline:none;}"
            )

    def _undo_entry(self):
        item = self._list.currentItem()
        if item is None:
            return
        entry_id = item.data(Qt.ItemDataRole.UserRole)
        if self._undo_callback:
            self._undo_callback(entry_id)
        self.close()

    def _copy_original(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._original_view.toPlainText())

    def _copy_corrected(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._corrected_view.toPlainText())

    def _delete_entry(self):
        item = self._list.currentItem()
        if item is None:
            return
        entry_id = item.data(Qt.ItemDataRole.UserRole)
        self._history.remove(entry_id)
        self._refresh()

    def _clear_all(self):
        confirm = QMessageBox.question(
            self,
            "Clear History",
            "Delete all correction history entries?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self._history.clear()
            self._original_view.clear()
            self._corrected_view.clear()
            self._info_lbl.setText("Select an entry from the list to view details.")
            self._undo_btn.setEnabled(False)
            self._refresh()

    def _on_toggle_enabled(self, checked):
        self._cfg.set("history_enabled", checked)
        self._history._enabled = checked
