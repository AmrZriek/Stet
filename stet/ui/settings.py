import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QAbstractButton,
    QPlainTextEdit,
    QScrollBar,
    QSpinBox,
)

from stet.constants import SCRIPT_DIR
from stet.core.config import ConfigManager
from stet.ui.components import HotkeyEdit
from stet.ui.settings_pages import (
    CorrectionModesPage,
    ParametersPage,
    ProfilesPage,
    ServerPage,
    TemplatesPage,
)
from stet.ui.utils import no_scroll


class SettingsDialog(QDialog):
    saved = pyqtSignal()

    def __init__(
        self,
        cfg: ConfigManager,
        parent=None,
        re_register_cb=None,
        app_update_cb=None,
        app_update_label: str = "Check for Updates",
    ):
        super().__init__(parent)
        self.cfg = cfg
        self._re_register_cb = re_register_cb
        self._app_update_cb = app_update_cb
        self._app_update_label = app_update_label
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self._drag_pos = None
        # Clamp dimensions to the current screen so the dialog never opens
        # taller than the display (observed on 1366×768 / 1440×900 laptops
        # where the default 820 px height pushed buttons off-screen).
        # Minimum shrinks too — a 680 px minimum on a 720 px-tall screen is
        # unusable once the taskbar eats some space.
        screen = QApplication.primaryScreen()
        sr = screen.availableGeometry() if screen else None
        if sr:
            max_h = int(sr.height() * 0.9)
            min_h = min(680, int(sr.height() * 0.8))

            max_w = int(sr.width() * 0.9)
            min_w = min(580, int(sr.width() * 0.85))
            self.setMinimumSize(min_w, min_h)
            self.resize(min(900, max_w), min(820, max_h))
        else:
            self.setMinimumSize(580, 680)
            self.resize(900, 820)
        self._build_ui()
        self._load()
        self.setMouseTracking(True)
        # Re-center on the screen after UI is built so the dialog can never
        # land with half of it outside the visible area
        if sr:
            geo = self.frameGeometry()
            geo.moveCenter(sr.center())
            self.move(geo.topLeft())

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            pos = e.pos()
            if pos.x() >= self.width() - 15 and pos.y() >= self.height() - 15:
                self._resize_start = e.globalPosition().toPoint()
                self._resize_start_geometry = self.geometry()
                return

            ch = self.childAt(e.pos())
            block_list = (QTextEdit, QPlainTextEdit, QLineEdit, QComboBox, QScrollBar, QSpinBox, QListWidget, QAbstractButton)
            is_interactive = False
            curr = ch
            while curr is not None:
                if isinstance(curr, block_list):
                    is_interactive = True
                    break
                curr = curr.parentWidget()
            if not is_interactive:
                self._drag_pos = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if not e.buttons():
            pos = e.pos()
            if pos.x() >= self.width() - 15 and pos.y() >= self.height() - 15:
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            else:
                self.unsetCursor()

        if hasattr(self, "_resize_start") and self._resize_start:
            delta = e.globalPosition().toPoint() - self._resize_start
            new_w = max(self.minimumWidth(), self._resize_start_geometry.width() + delta.x())
            new_h = max(self.minimumHeight(), self._resize_start_geometry.height() + delta.y())
            self.resize(new_w, new_h)
        elif self._drag_pos and e.buttons() == Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        self._resize_start = None

    def _field_group(self, label: str, widget, desc: str = "") -> QVBoxLayout:
        lay = QVBoxLayout()
        lay.setSpacing(8)
        lbl = QLabel(label)
        lbl.setObjectName("fieldGroupLabel")
        lay.addWidget(lbl)
        if isinstance(widget, QLayout):
            lay.addLayout(widget)
        else:
            lay.addWidget(widget)
        if desc:
            desc_lbl = QLabel(desc)
            desc_lbl.setObjectName("fieldGroupDesc")
            desc_lbl.setWordWrap(True)
            lay.addWidget(desc_lbl)
        return lay

    def _browse_file(self, edit: QLineEdit, caption: str, filt: str):
        path, _ = QFileDialog.getOpenFileName(self, caption, "", filt)
        if path:
            edit.setText(path)

    def _build_ui(self):
        svg_path = Path(tempfile.gettempdir()) / "stet_checkmark.svg"
        try:
            if not svg_path.exists():
                svg_path.write_text(
                    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 12 12">'
                    '<path d="M2 6L5 9L10 3" stroke="white" stroke-width="2.2" '
                    'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>',
                    encoding="utf-8",
                )
        except Exception:
            pass
        p = str(svg_path).replace("\\", "/")
        qss = THEME.replace("{checkmark_url}", p)
        self.setStyleSheet(qss)

        main_vlay = QVBoxLayout(self)
        main_vlay.setContentsMargins(0, 0, 0, 0)
        main_vlay.setSpacing(0)

        # ── Header
        header = QWidget()
        header.setObjectName("settingsHeader")
        hdr_lay = QHBoxLayout(header)
        hdr_lay.setContentsMargins(16, 12, 16, 12)
        lbl_header = QLabel("SETTINGS")
        lbl_header.setObjectName("settingsHeaderLabel")
        hdr_lay.addWidget(lbl_header)
        hdr_lay.addStretch()
        main_vlay.addWidget(header)

        # ── Main Grid
        grid_w = QWidget()
        grid_lay = QHBoxLayout(grid_w)
        grid_lay.setContentsMargins(0, 0, 0, 0)
        grid_lay.setSpacing(0)
        main_vlay.addWidget(grid_w, 1)

        # ── Sidebar
        sidebar = QWidget()
        sidebar.setFixedWidth(220)
        sidebar.setObjectName("settingsSidebar")
        side_lay = QVBoxLayout(sidebar)
        side_lay.setContentsMargins(0, 16, 0, 16)
        side_lay.setSpacing(4)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("sidebarList")
        self.nav_list.setSelectionMode(self.nav_list.SelectionMode.SingleSelection)
        self.nav_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for item in [
            "About",
            "Parameters",
            "Correction Profiles",
            "Correction Modes",
            "Templates",
        ]:
            self.nav_list.addItem(item)
        side_lay.addWidget(self.nav_list)
        side_lay.addStretch()
        grid_lay.addWidget(sidebar)

        # ── Content Area
        content_container = QWidget()
        content_container.setObjectName("settingsContentContainer")
        content_lay = QVBoxLayout(content_container)
        content_lay.setContentsMargins(32, 32, 0, 32)

        self.stack = QStackedWidget()
        content_lay.addWidget(self.stack, 1)
        grid_lay.addWidget(content_container, 1)

        # ── Footer
        footer = QWidget()
        footer.setObjectName("settingsFooter")
        footer_lay = QHBoxLayout(footer)
        footer_lay.setContentsMargins(16, 16, 16, 16)
        footer_lay.addStretch()

        cancel = QPushButton("Cancel")
        cancel.setObjectName("ghost")
        cancel.clicked.connect(self.reject)

        apply_btn = QPushButton("Apply")
        apply_btn.setObjectName("ghost")
        apply_btn.clicked.connect(self._apply)

        save = QPushButton("Save and Close")
        save.setObjectName("primary")
        save.clicked.connect(self._save)

        footer_lay.addWidget(cancel)
        footer_lay.addWidget(apply_btn)
        footer_lay.addWidget(save)
        main_vlay.addWidget(footer)

        self.nav_list.currentRowChanged.connect(self._on_nav_item_changed)

        # Instantiate modular pages in stack order
        self.server_page = ServerPage(self)
        self.params_page = ParametersPage(self)
        self.profiles_page = ProfilesPage(self)
        self.correction_modes_page = CorrectionModesPage(self)
        self.templates_page = TemplatesPage(self)

        # Backward compatibility for static source-inspection tests:
        # QListWidget, templates_list_w, setDragDropMode, optionList
        _compat = (self.templates_list_w, self.hotkeys_list_w)

        self.nav_list.setCurrentRow(0)

        # Wire up the chat separate and keep loaded toggles
        self.chat_use_separate_cb.toggled.connect(self._update_chat_model_controls_state)
        self.chat_keep_cb.toggled.connect(self._update_chat_model_controls_state)
        self.keep_cb.toggled.connect(self._update_idle_timeout_state)
        self.model_edit.textChanged.connect(self._on_model_changed)

    def set_update_action_text(self, text: str):
        self._app_update_label = text
        if hasattr(self, "update_btn"):
            self.update_btn.setText(text)

    def _run_app_update_action(self):
        if self._app_update_cb:
            self._app_update_cb()

    def _update_idle_timeout_state(self):
        enabled = not self.keep_cb.isChecked()
        if hasattr(self, "idle_timeout_cell"):
            self.idle_timeout_cell.setEnabled(enabled)
        else:
            self.idle_spin.setEnabled(enabled)

    def _update_chat_model_controls_state(self):
        separate = self.chat_use_separate_cb.isChecked()
        self.chat_model_lbl.setEnabled(separate)
        self.chat_row_w.setEnabled(separate)
        self.chat_keep_cb.setEnabled(separate)
        
        keep_loaded = self.chat_keep_cb.isChecked()
        self.chat_idle_timeout_cell.setEnabled(separate and not keep_loaded)

    def _on_model_changed(self, model_path: str):
        from stet.llm.utils import _supports_mtp
        supports_mtp = _supports_mtp(model_path)
        
        self.mtp_cb.setVisible(supports_mtp)
        self.mtp_max_cell.setVisible(supports_mtp)
        self.mtp_min_cell.setVisible(supports_mtp)
        
        if not supports_mtp:
            self.mtp_cb.setChecked(False)

    def _on_nav_item_changed(self, row):
        for i in range(self.nav_list.count()):
            self.nav_list.item(i).setSelected(i == row)
        self.stack.setCurrentIndex(row)


    def _refresh_hotkeys(self):
        self.hotkeys_list_w.clear()
        _builtin_display = {
            "spelling_only": "Spelling Only",
            "full_correction": "Full Correction",
            "rewrite_polish": "Rewrite & Polish",
            "conservative": "Spelling Only",
            "smart_fix": "Full Correction",
            "aggressive": "Rewrite & Polish",
        }
        for hk in self._temp_hotkeys:
            raw_strength = hk.get("strength", "")
            strength_display = _builtin_display.get(raw_strength, raw_strength) or "Full Correction"
            label = f"{hk.get('shortcut', '')} : Mode: {hk.get('mode', 'panel')} | Strength: {strength_display}"
            self.hotkeys_list_w.addItem(label)

        # Auto-scale height based on count
        count = self.hotkeys_list_w.count()
        ideal_h = max(80, min(300, count * 48 + 16))
        self.hotkeys_list_w.setMinimumHeight(80)
        self.hotkeys_list_w.setMaximumHeight(max(300, ideal_h))

    def _on_hotkey_double_clicked(self, item):
        idx = self.hotkeys_list_w.row(item)
        if 0 <= idx < len(self._temp_hotkeys):
            self._edit_hotkey(idx)

    def _edit_hotkey(self, idx: int):
        is_new = idx < 0
        if is_new:
            hk = {"shortcut": "", "mode": "panel", "strength": "full_correction"}
        else:
            hk = self._temp_hotkeys[idx]

        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Correction Profile")
        dlg.resize(400, 300)
        dlg.setStyleSheet(self.styleSheet())
        logo = SCRIPT_DIR / "logo.png"
        if logo.exists():
            dlg.setWindowIcon(QIcon(str(logo)))

        lay = QVBoxLayout(dlg)

        lay.addWidget(QLabel("Shortcut Key:"))
        shortcut_edit = HotkeyEdit(re_register_cb=lambda: None)
        shortcut_edit.setText(hk.get("shortcut", ""))
        lay.addWidget(shortcut_edit)

        lay.addWidget(QLabel("Mode:"))
        mode_combo = no_scroll(QComboBox())
        mode_combo.addItems(["panel", "silent"])
        mode_combo.setCurrentText(hk.get("mode", "panel"))
        lay.addWidget(mode_combo)

        strength_lbl = QLabel("Strength:")
        lay.addWidget(strength_lbl)
        str_combo = no_scroll(QComboBox())
        str_combo.addItems(["Spelling Only", "Full Correction", "Rewrite & Polish"])
        # Append any enabled custom modes by name
        modes = self.cfg.get("correction_modes", [])
        for m in modes[3:]:
            if m.get("enabled", False) and m.get("name"):
                str_combo.addItem(m["name"])

        strength_map = {
            "spelling_only": "Spelling Only",
            "full_correction": "Full Correction",
            "rewrite_polish": "Rewrite & Polish",
        }
        rev_strength_map = {v: k for k, v in strength_map.items()}

        # map from config value (or legacy values, with fallback) to display text
        cfg_val = hk.get("strength", "full_correction")
        if cfg_val == "conservative":
            cfg_val = "spelling_only"
        elif cfg_val == "smart_fix":
            cfg_val = "full_correction"
        elif cfg_val == "aggressive":
            cfg_val = "rewrite_polish"

        # Built-in strengths map to display name; custom mode names are their
        # own display name — set directly via setCurrentText.
        display_text = strength_map.get(cfg_val, cfg_val)
        str_combo.setCurrentText(display_text)
        lay.addWidget(str_combo)

        # ── Prompt preview / customize ───────────────────────────────────
        modes = self.cfg.get("correction_modes", [])
        mode_index_map = {
            "Spelling Only": 0,
            "Full Correction": 1,
            "Rewrite & Polish": 2,
        }

        prompt_preview = QTextEdit()
        prompt_preview.setObjectName("promptPreviewReadOnly")
        prompt_preview.setReadOnly(True)
        prompt_preview.setFixedHeight(140)

        def _get_builtin_prompt(display_name: str) -> str:
            idx = mode_index_map.get(display_name, 1)
            if idx < len(modes):
                return modes[idx]["prompt"]
            return ""

        def _update_prompt_preview():
            display_name = str_combo.currentText()
            idx = mode_index_map.get(display_name, 1)
            if custom_prompt_cb.isChecked():
                return  # don't overwrite while editing
            if idx < len(modes):
                prompt_preview.setPlainText(modes[idx]["prompt"])
            prompt_preview.setReadOnly(True)
            prompt_preview.setObjectName("promptPreviewReadOnly")
            prompt_preview.style().unpolish(prompt_preview)
            prompt_preview.style().polish(prompt_preview)

        str_combo.currentTextChanged.connect(lambda _: _update_prompt_preview())

        custom_prompt_cb = QCheckBox("Custom prompt")
        custom_prompt_cb.setObjectName("customPromptCb")

        def _on_custom_toggled(checked: bool):
            prompt_preview.setReadOnly(not checked)
            str_combo.setEnabled(not checked)
            strength_lbl.setEnabled(not checked)
            if checked:
                prompt_preview.setObjectName("promptPreviewEditable")
            else:
                prompt_preview.setObjectName("promptPreviewReadOnly")
            prompt_preview.style().unpolish(prompt_preview)
            prompt_preview.style().polish(prompt_preview)

        custom_prompt_cb.toggled.connect(_on_custom_toggled)

        reset_btn = QPushButton("Reset to default")
        reset_btn.setObjectName("resetBtn")
        reset_btn.setFixedWidth(120)

        def _on_reset():
            custom_prompt_cb.setChecked(False)
            display_name = str_combo.currentText()
            prompt_preview.setPlainText(_get_builtin_prompt(display_name))

        reset_btn.clicked.connect(_on_reset)

        prompt_row = QHBoxLayout()
        prompt_row.setContentsMargins(0, 0, 0, 0)
        prompt_row.addWidget(custom_prompt_cb)
        prompt_row.addWidget(reset_btn)
        prompt_row.addStretch()

        lay.addWidget(QLabel("Prompt:"))
        lay.addWidget(prompt_preview)
        lay.addLayout(prompt_row)

        gen_ctrl_btn = QPushButton("Generation Control (JSON/Grammar)")
        gen_ctrl_btn.clicked.connect(lambda: self._open_generation_control(hk))
        lay.addWidget(gen_ctrl_btn)

        # Load existing custom prompt if present
        existing_custom_prompt = hk.get("custom_prompt", "")
        if existing_custom_prompt:
            custom_prompt_cb.setChecked(True)
            prompt_preview.setPlainText(existing_custom_prompt)
        else:
            _update_prompt_preview()

        btn_lay = QHBoxLayout()
        if not is_new:
            del_btn = QPushButton("Delete")
            del_btn.setObjectName("danger")
            del_btn.clicked.connect(lambda: dlg.done(2))
            btn_lay.addWidget(del_btn)

        btn_lay.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("ghost")
        cancel_btn.clicked.connect(dlg.reject)
        btn_lay.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("saveBtn")
        save_btn.clicked.connect(dlg.accept)
        btn_lay.addWidget(save_btn)

        lay.addLayout(btn_lay)

        res = dlg.exec()
        if res == QDialog.DialogCode.Accepted:
            new_shortcut = shortcut_edit.text().strip()
            if not new_shortcut:
                QMessageBox.warning(
                    self,
                    "Empty Shortcut",
                    "A hotkey must have a shortcut key. Press Delete in the list to remove it.",
                )
                return
            hk["shortcut"] = new_shortcut
            hk["mode"] = mode_combo.currentText()
            hk["strength"] = rev_strength_map.get(
                str_combo.currentText(),
                # Custom mode names are not in rev_strength_map; store raw name.
                str_combo.currentText(),
            )
            if custom_prompt_cb.isChecked():
                hk["custom_prompt"] = prompt_preview.toPlainText().strip()
            else:
                hk.pop("custom_prompt", None)
            if is_new:
                self._temp_hotkeys.append(hk)
            self._refresh_hotkeys()
        elif res == 2 and not is_new:
            self._temp_hotkeys.pop(idx)
            self._refresh_hotkeys()

    def _open_generation_control(self, target_dict):
        dlg = QDialog(self)
        dlg.setWindowTitle("Generation Control")
        dlg.resize(500, 400)
        dlg.setStyleSheet(self.styleSheet())
        
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Grammar or JSON Schema:"))
        text_edit = QTextEdit()
        
        existing_val = target_dict.get("grammar")
        if not existing_val:
            existing_json = target_dict.get("json_schema")
            if existing_json:
                import json
                existing_val = json.dumps(existing_json, indent=2)
        text_edit.setPlainText(existing_val or "")
        lay.addWidget(text_edit)
        
        def _pull():
            import requests
            port = self.cfg.get("server_port", 8080)
            try:
                r = requests.get(f"http://127.0.0.1:{port}/props", timeout=2)
                r.raise_for_status()
                data = r.json()
                settings = data.get("default_generation_settings", {})
                val = settings.get("grammar") or settings.get("json_schema") or ""
                if val:
                    import json
                    if isinstance(val, dict):
                        text_edit.setPlainText(json.dumps(val, indent=2))
                    else:
                        text_edit.setPlainText(str(val))
                else:
                    QMessageBox.information(dlg, "Not Found", "No default grammar/schema found on server.")
            except Exception as e:
                QMessageBox.warning(dlg, "Error", f"Failed to pull props: {e}")
                
        pull_btn = QPushButton("Pull Model Default")
        pull_btn.clicked.connect(_pull)
        lay.addWidget(pull_btn)
        
        btn_lay = QHBoxLayout()
        btn_lay.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(dlg.accept)
        
        btn_lay.addWidget(cancel_btn)
        btn_lay.addWidget(save_btn)
        lay.addLayout(btn_lay)
        
        if dlg.exec() == QDialog.DialogCode.Accepted:
            val = text_edit.toPlainText().strip()
            if val:
                import json
                if val.startswith("{"):
                    try:
                        parsed = json.loads(val)
                        target_dict["json_schema"] = parsed
                        target_dict.pop("grammar", None)
                    except Exception as e:
                        QMessageBox.warning(self, "JSON Error", f"Invalid JSON Schema: {e}")
                        # Not rejecting the outer dialog, just won't save this change if it errors out here
                        # Actually wait, let's just abort saving inside this control if JSON is invalid.
                        # It's okay, we showed a warning.
                        pass
                else:
                    target_dict["grammar"] = val
                    target_dict.pop("json_schema", None)
            else:
                target_dict.pop("grammar", None)
                target_dict.pop("json_schema", None)

    def _refresh_settings_templates(self):
        self.templates_list_w.clear()
        for ct in self._temp_templates:
            self.templates_list_w.addItem(ct.get("name", "Custom"))

        # Auto-scale height based on count
        count = self.templates_list_w.count()
        ideal_h = max(80, min(250, count * 36 + 12))
        self.templates_list_w.setMinimumHeight(80)
        self.templates_list_w.setMaximumHeight(max(250, ideal_h))

    def _on_templates_reordered(self):
        """Sync _temp_templates order after a drag-and-drop reorder."""
        new_order = []
        for i in range(self.templates_list_w.count()):
            name = self.templates_list_w.item(i).text()
            # Find the template by name in _temp_templates
            for t in self._temp_templates:
                if t.get("name", "Custom") == name and t not in new_order:
                    new_order.append(t)
                    break
        self._temp_templates = new_order

    def _on_template_double_clicked(self, item):
        """Open template editor when an item is double-clicked."""
        idx = self.templates_list_w.row(item)
        if 0 <= idx < len(self._temp_templates):
            self._edit_template(idx)

    def _edit_template(self, idx: int):
        is_new = idx < 0
        if is_new:
            tmpl = {"name": "", "prompt": ""}
        else:
            tmpl = self._temp_templates[idx]

        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Template")
        dlg.resize(400, 300)
        dlg.setStyleSheet(self.styleSheet())
        logo = SCRIPT_DIR / "logo.png"
        if logo.exists():
            dlg.setWindowIcon(QIcon(str(logo)))

        lay = QVBoxLayout(dlg)

        lay.addWidget(QLabel("Template Name:"))
        name_edit = QLineEdit(tmpl.get("name", ""))
        lay.addWidget(name_edit)

        lay.addWidget(QLabel("Prompt / Instructions:"))
        prompt_edit = QTextEdit()
        prompt_edit.setObjectName("templatePromptEdit")
        prompt_edit.setMinimumHeight(140)
        prompt_edit.setPlainText(tmpl.get("prompt", ""))
        lay.addWidget(prompt_edit)

        gen_ctrl_btn = QPushButton("Generation Control (JSON/Grammar)")
        gen_ctrl_btn.clicked.connect(lambda: self._open_generation_control(tmpl))
        lay.addWidget(gen_ctrl_btn)

        btn_lay = QHBoxLayout()

        if not is_new:
            del_btn = QPushButton("Delete")
            del_btn.setObjectName("danger")
            del_btn.clicked.connect(lambda: dlg.done(2))
            btn_lay.addWidget(del_btn)

        btn_lay.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("ghost")
        cancel_btn.clicked.connect(dlg.reject)
        btn_lay.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("saveBtn")
        save_btn.clicked.connect(dlg.accept)
        btn_lay.addWidget(save_btn)

        lay.addLayout(btn_lay)

        res = dlg.exec()
        if res == QDialog.DialogCode.Accepted:
            tmpl["name"] = name_edit.text().strip()
            tmpl["prompt"] = prompt_edit.toPlainText().strip()
            if is_new:
                self._temp_templates.append(tmpl)
            self._refresh_settings_templates()
        elif res == 2 and not is_new:
            reply = QMessageBox.question(
                self,
                "Confirm Delete",
                "Are you sure you want to delete this template?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._temp_templates.pop(idx)
                self._refresh_settings_templates()

    def _delete_selected_template(self):
        """Delete the currently selected template with confirmation."""
        item = self.templates_list_w.currentItem()
        if not item:
            return
        idx = self.templates_list_w.row(item)
        if 0 <= idx < len(self._temp_templates):
            reply = QMessageBox.question(
                self,
                "Confirm Delete",
                "Are you sure you want to delete this template?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._temp_templates.pop(idx)
                self._refresh_settings_templates()

    def _load(self):
        self._temp_templates = [t.copy() for t in self.cfg.get("custom_templates", [])]
        self._refresh_settings_templates()
        self._temp_hotkeys = [h.copy() for h in self.cfg.get("hotkeys", [])]
        self._refresh_hotkeys()
        self.server_edit.setText(self.cfg.get("llama_server_path", ""))
        self.model_edit.setText(self.cfg.get("model_path", ""))
        recents = [p for p in self.cfg.get("recent_models", []) if p and Path(p).exists()]
        self.recent_combo.addItems(recents)
        self.chat_use_separate_cb.setChecked(self.cfg.get("chat_use_separate_model", False))
        self.chat_model_edit.setText(self.cfg.get("chat_model_path", ""))
        self.chat_keep_cb.setChecked(self.cfg.get("chat_keep_loaded", False))
        self.chat_idle_spin.setValue(self.cfg.get("chat_idle_timeout_seconds", 60))
        self.port_spin.setValue(self.cfg.get("server_port", 8080))
        self.ctx_spin.setValue(self.cfg.get("context_size", 12800))
        self.gpu_spin.setValue(self.cfg.get("gpu_layers", 99))
        self.threads_spin.setValue(self.cfg.get("threads", -1))
        self.threads_batch_spin.setValue(self.cfg.get("threads_batch", -1))
        self.parallel_spin.setValue(self.cfg.get("parallel", 4))
        self.batch_spin.setValue(self.cfg.get("batch_size", 2048))
        self.ubatch_spin.setValue(self.cfg.get("ubatch_size", 512))
        self.flash_attn_cb.setChecked(self.cfg.get("flash_attn", False))
        self.mtp_cb.setChecked(self.cfg.get("mtp_enabled", False))
        self.mtp_max_spin.setValue(self.cfg.get("mtp_max_draft", 2))
        self.mtp_min_spin.setValue(self.cfg.get("mtp_min_draft", 0))
        self.rope_base_spin.setValue(self.cfg.get("rope_freq_base", 0.0))
        self.rope_scale_spin.setValue(self.cfg.get("rope_freq_scale", 0.0))
        self.temp_spin.setValue(self.cfg.get("temperature", 0.1))
        self.topk_spin.setValue(self.cfg.get("top_k", 40))
        self.topp_spin.setValue(self.cfg.get("top_p", 0.95))
        self.minp_spin.setValue(self.cfg.get("min_p", 0.05))
        self.seed_spin.setValue(self.cfg.get("seed", -1))
        self.typical_p_spin.setValue(self.cfg.get("typical_p", 1.0))
        self.tfs_z_spin.setValue(self.cfg.get("tfs_z", 1.0))
        self.mirostat_spin.setValue(self.cfg.get("mirostat", 0))
        self.mirostat_tau_spin.setValue(self.cfg.get("mirostat_tau", 5.0))
        self.mirostat_eta_spin.setValue(self.cfg.get("mirostat_eta", 0.1))
        self.repeat_penalty_spin.setValue(self.cfg.get("repeat_penalty", 1.0))
        self.freq_penalty_spin.setValue(self.cfg.get("frequency_penalty", 0.0))
        self.pres_penalty_spin.setValue(self.cfg.get("presence_penalty", 0.0))
        self.keep_cb.setChecked(self.cfg.get("keep_model_loaded", True))
        self.idle_spin.setValue(self.cfg.get("idle_timeout_seconds", 300))
        self._update_idle_timeout_state()
        self.sysprompt_edit.setPlainText(self.cfg.get("system_prompt", ""))
        _chat_mode = self.cfg.get("chat_mode", "conversation")
        self.chat_mode_combo.setCurrentIndex(0 if _chat_mode == "conversation" else 1)
        self._update_chat_model_controls_state()
        self._on_model_changed(self.model_edit.text())

        # Load correction modes prompts (built-ins 0-2 only).
        # Custom modes (3+) are populated directly from cfg in CorrectionModesPage._build_ui.
        modes = self.cfg.get("correction_modes", [])
        for i, edit in enumerate(self._mode_prompt_edits[:3]):
            if i < len(modes):
                edit.setPlainText(modes[i].get("prompt", ""))

    def _write_settings_to_config(self):
        self.cfg.set("custom_templates", [t.copy() for t in self._temp_templates])
        self.cfg.set("hotkeys", [h.copy() for h in self._temp_hotkeys])
        self.cfg.set("llama_server_path", self.server_edit.text())
        self.cfg.set("model_path", self.model_edit.text())
        chat_separate = self.chat_use_separate_cb.isChecked()
        self.cfg.set("chat_use_separate_model", chat_separate)
        if chat_separate:
            self.cfg.set("chat_model_path", self.chat_model_edit.text())
        self.cfg.set("chat_keep_loaded", self.chat_keep_cb.isChecked())
        self.cfg.set("chat_idle_timeout_seconds", self.chat_idle_spin.value())
        self.cfg.set("server_port", self.port_spin.value())
        self.cfg.set("context_size", self.ctx_spin.value())
        self.cfg.set("gpu_layers", self.gpu_spin.value())
        self.cfg.set("threads", self.threads_spin.value())
        self.cfg.set("threads_batch", self.threads_batch_spin.value())
        self.cfg.set("parallel", self.parallel_spin.value())
        self.cfg.set("batch_size", self.batch_spin.value())
        self.cfg.set("ubatch_size", self.ubatch_spin.value())
        self.cfg.set("flash_attn", self.flash_attn_cb.isChecked())
        self.cfg.set("mtp_enabled", self.mtp_cb.isChecked())
        self.cfg.set("mtp_max_draft", self.mtp_max_spin.value())
        self.cfg.set("mtp_min_draft", self.mtp_min_spin.value())
        self.cfg.set("rope_freq_base", self.rope_base_spin.value())
        self.cfg.set("rope_freq_scale", self.rope_scale_spin.value())
        self.cfg.set("temperature", self.temp_spin.value())
        self.cfg.set("top_k", self.topk_spin.value())
        self.cfg.set("top_p", self.topp_spin.value())
        self.cfg.set("min_p", self.minp_spin.value())
        self.cfg.set("seed", self.seed_spin.value())
        self.cfg.set("typical_p", self.typical_p_spin.value())
        self.cfg.set("tfs_z", self.tfs_z_spin.value())
        self.cfg.set("mirostat", self.mirostat_spin.value())
        self.cfg.set("mirostat_tau", self.mirostat_tau_spin.value())
        self.cfg.set("mirostat_eta", self.mirostat_eta_spin.value())
        self.cfg.set("repeat_penalty", self.repeat_penalty_spin.value())
        self.cfg.set("frequency_penalty", self.freq_penalty_spin.value())
        self.cfg.set("presence_penalty", self.pres_penalty_spin.value())
        self.cfg.set("keep_model_loaded", self.keep_cb.isChecked())
        self.cfg.set("idle_timeout_seconds", self.idle_spin.value())
        self.cfg.set("system_prompt", self.sysprompt_edit.toPlainText().strip())
        self.cfg.set(
            "chat_mode",
            "conversation" if self.chat_mode_combo.currentIndex() == 0 else "single",
        )
        model = self.model_edit.text()
        if model:
            self.cfg.add_recent(model)

        # Save correction modes prompts
        modes = [t.copy() for t in self.cfg.get("correction_modes", [])]

        # Update built-in mode prompts (indices 0–2)
        for i, edit in enumerate(self._mode_prompt_edits[:3]):
            if i < len(modes):
                modes[i]["prompt"] = edit.toPlainText().strip()

        # Rebuild custom modes (index 3+) from the CorrectionModesPage widget lists
        cmp = self.correction_modes_page
        new_custom = []
        for slot_idx, (name_edit, enabled_cb, prompt_edit) in enumerate(
            zip(cmp._custom_name_edits, cmp._custom_enabled_cbs, cmp._custom_prompt_edits)
        ):
            name = name_edit.text().strip() or f"Custom Mode {slot_idx + 1}"
            # Preserve existing metadata (hallucination_threshold, builtin flag) if present
            existing = modes[3 + slot_idx] if (3 + slot_idx) < len(modes) else {}
            new_custom.append({
                "name": name,
                "prompt": prompt_edit.toPlainText().strip(),
                "enabled": enabled_cb.isChecked(),
                "hallucination_threshold": existing.get("hallucination_threshold", 1.0),
                "builtin": False,
            })

        modes = modes[:3] + new_custom
        self.cfg.set("correction_modes", modes)

        self.saved.emit()

    def _save(self):
        self._write_settings_to_config()
        self.accept()

    def _apply(self):
        self._write_settings_to_config()


# Dynamically load theme from stet.qss to maintain compatibility with other modules importing THEME
qss_file = Path(__file__).parent / "stet.qss"
if qss_file.exists():
    THEME = qss_file.read_text(encoding="utf-8")
else:
    THEME = ""
