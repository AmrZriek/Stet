from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (

    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from stet.ui.utils import no_scroll


class ClickableLabel(QLabel):
    clicked = pyqtSignal()

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


def make_scrollable_page(title_str, page, add_to_stack=True):
    """Configures the given page widget with a full-height scrollable area.
    The title is placed inside the scrollable layout so the vertical
    scrollbar naturally spans top-to-bottom of the entire content container."""
    pl = QVBoxLayout(page)
    pl.setContentsMargins(0, 0, 0, 0)
    pl.setSpacing(0)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setFrameShape(QFrame.Shape.NoFrame)

    inner = QWidget()
    form = QVBoxLayout(inner)
    form.setAlignment(Qt.AlignmentFlag.AlignTop)
    form.setContentsMargins(0, 0, 32, 0)
    form.setSpacing(16)

    if title_str:
        t = QLabel(title_str)
        t.setObjectName("pageTitle")
        form.addWidget(t)

    scroll.setWidget(inner)
    pl.addWidget(scroll, 1)

    if add_to_stack:
        page.dialog.stack.addWidget(page)
    return form


class ServerPage(QWidget):
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self._build_ui()

    def _build_ui(self):
        form = make_scrollable_page("About Stet", self)

        self.dialog.server_edit = QLineEdit()
        self.dialog.server_edit.setReadOnly(True)
        btn_s = QPushButton("Browse")
        btn_s.setObjectName("ghost")
        btn_s.setFixedWidth(80)
        btn_s.clicked.connect(
            lambda: self.dialog._browse_file(
                self.dialog.server_edit,
                "Select llama-server",
                "Executable (llama-server*);;All (*)",
            )
        )

        srv_row = QHBoxLayout()
        srv_row.setContentsMargins(0, 0, 0, 0)
        srv_row.addWidget(self.dialog.server_edit, 1)
        srv_row.addWidget(btn_s)
        srv_w = QWidget()
        srv_w.setLayout(srv_row)
        form.addLayout(self.dialog._field_group("Server Binary Path", srv_w))

        self.dialog.model_edit = QLineEdit()
        self.dialog.model_edit.setReadOnly(True)
        btn_m = QPushButton("Browse")
        btn_m.setObjectName("ghost")
        btn_m.setFixedWidth(80)
        btn_m.clicked.connect(
            lambda: self.dialog._browse_file(
                self.dialog.model_edit, "Select GGUF model", "GGUF (*.gguf)"
            )
        )

        mod_row = QHBoxLayout()
        mod_row.setContentsMargins(0, 0, 0, 0)
        mod_row.addWidget(self.dialog.model_edit, 1)
        mod_row.addWidget(btn_m)
        mod_w = QWidget()
        mod_w.setLayout(mod_row)
        form.addLayout(self.dialog._field_group("Model Weights (.gguf)", mod_w))

        self.dialog.recent_combo = QComboBox()
        self.dialog.recent_combo.currentTextChanged.connect(
            lambda t: self.dialog.model_edit.setText(t) if t else None
        )
        form.addLayout(
            self.dialog._field_group("Recent models", self.dialog.recent_combo)
        )

        self.dialog.port_spin = no_scroll(QSpinBox())
        self.dialog.port_spin.setRange(1024, 65535)
        self.dialog.port_spin.setFixedWidth(100)
        form.addLayout(self.dialog._field_group("Port", self.dialog.port_spin))

        sep_updates = QFrame()
        sep_updates.setObjectName("sep")
        sep_updates.setFrameShape(QFrame.Shape.HLine)
        form.addWidget(sep_updates)

        updates_title = QLabel("Application Updates")
        updates_title.setObjectName("pageSubtitle")
        form.addWidget(updates_title)

        update_copy = QLabel(
            "Check for a newer Stet build on demand. Automatic update checks still "
            "run shortly after startup."
        )
        update_copy.setObjectName("fieldGroupDesc")
        update_copy.setWordWrap(True)

        self.dialog.update_btn = QPushButton(self.dialog._app_update_label)
        self.dialog.update_btn.setObjectName("ghost")
        self.dialog.update_btn.setMinimumWidth(180)
        self.dialog.update_btn.clicked.connect(self.dialog._run_app_update_action)

        update_row = QHBoxLayout()
        update_row.setContentsMargins(0, 0, 0, 0)
        update_row.setSpacing(16)
        update_row.addWidget(update_copy, 1)
        update_row.addWidget(self.dialog.update_btn, 0)
        form.addLayout(update_row)

        sep_support = QFrame()
        sep_support.setObjectName("sep")
        sep_support.setFrameShape(QFrame.Shape.HLine)
        form.addWidget(sep_support)

        support_title = QLabel("Support Development")
        support_title.setObjectName("pageSubtitle")
        form.addWidget(support_title)

        support_copy = QLabel(
            "Stet is an open-source project built with love to make writing effortless and clean. "
            "If Stet has saved you time or made your writing better, please consider supporting its development. "
            "Every donation helps keep the project active, continuously updated, and completely free."
        )
        support_copy.setObjectName("fieldGroupDesc")
        support_copy.setWordWrap(True)

        btn_donate = QPushButton("Support Stet")
        btn_donate.setObjectName("donateBtn")
        btn_donate.setMinimumWidth(180)
        import webbrowser
        btn_donate.clicked.connect(lambda: webbrowser.open("https://ko-fi.com/amrzriek"))

        support_row = QHBoxLayout()
        support_row.setContentsMargins(0, 0, 0, 0)
        support_row.setSpacing(16)
        support_row.addWidget(support_copy, 1)
        support_row.addWidget(btn_donate, 0)
        form.addLayout(support_row)

        form.addStretch()


def _apply_tooltips(dialog, prefix: str = ""):
    tooltips = {
        "temp_spin": "Higher = more creative, lower = more precise",
        "topk_spin": "Limits choices to the K most likely next words",
        "topp_spin": "Only considers words whose probabilities sum to this value",
        "minp_spin": "Filters out words less likely than this threshold",
        "tfs_z_spin": "Tail Free Sampling \u2014 reduces unlikely word choices",
        "mirostat_spin": "Dynamic sampling that targets a specific surprise level",
        "mtp_cb": "Multi-Token Prediction \u2014 generates multiple tokens at once for speed",
        "gpu_spin": "How many model layers run on GPU (more = faster, uses more VRAM)",
        "ctx_spin": "How much text the model can 'see' at once (in tokens)",
        "flash_attn_cb": "Faster attention computation \u2014 requires GPU support",
        "batch_spin": "How many tokens are processed together (higher = faster, more RAM)",
    }
    for key, text in tooltips.items():
        widget = getattr(dialog, f"{prefix}{key}", None)
        if widget is not None:
            widget.setToolTip(text)


class ParametersPage(QWidget):
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self._build_ui()

    def _build_ui(self):
        pl = QVBoxLayout(self)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)

        # Custom header labels acting as tabs
        header_widget = QWidget()
        header_lay = QHBoxLayout(header_widget)
        header_lay.setContentsMargins(0, 0, 32, 8)
        header_lay.setSpacing(16)

        self.tab_model_lbl = ClickableLabel("Model Parameters")
        self.tab_model_lbl.setObjectName("pageTitle")
        self.tab_model_lbl.setProperty("active", True)
        self.tab_model_lbl.clicked.connect(lambda: self._switch_tab(0))

        self.tab_chat_lbl = ClickableLabel("Chat Parameters")
        self.tab_chat_lbl.setObjectName("pageTitle")
        self.tab_chat_lbl.setProperty("active", False)
        self.tab_chat_lbl.clicked.connect(lambda: self._switch_tab(1))

        header_lay.addWidget(self.tab_model_lbl, 1)
        header_lay.addWidget(self.tab_chat_lbl, 1)
        pl.addWidget(header_widget)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("paramsTabWidget")
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().hide()
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Model Parameters tab
        model_tab = QWidget()
        model_tab.setObjectName("modelParamsTab")
        form = make_scrollable_page(None, model_tab, add_to_stack=False)

        # Helper for a labeled grid cell
        def _grid_cell(label: str, widget) -> QWidget:
            cell = QWidget()
            cell_lay = QVBoxLayout(cell)
            cell_lay.setContentsMargins(0, 0, 0, 0)
            cell_lay.setSpacing(4)
            lbl = QLabel(label)
            lbl.setObjectName("fieldGroupLabel")
            cell_lay.addWidget(lbl)
            cell_lay.addWidget(widget)
            return cell

        # --- Architecture Section ---
        arch_title = QLabel("Architecture")
        arch_title.setObjectName("pageSubtitle")
        form.addWidget(arch_title)

        self.dialog.ctx_spin = no_scroll(QSpinBox())
        self.dialog.ctx_spin.setRange(512, 131072)
        self.dialog.ctx_spin.setSingleStep(512)
        self.dialog.ctx_spin.setFixedWidth(100)

        self.dialog.rope_base_spin = no_scroll(QDoubleSpinBox())
        self.dialog.rope_base_spin.setRange(0.0, 10000000.0)
        self.dialog.rope_base_spin.setSingleStep(1000.0)
        self.dialog.rope_base_spin.setDecimals(1)
        self.dialog.rope_base_spin.setFixedWidth(100)
        self.dialog.rope_base_spin.setSpecialValueText("Auto (0.0)")

        self.dialog.rope_scale_spin = no_scroll(QDoubleSpinBox())
        self.dialog.rope_scale_spin.setRange(0.0, 1000.0)
        self.dialog.rope_scale_spin.setSingleStep(0.1)
        self.dialog.rope_scale_spin.setDecimals(2)
        self.dialog.rope_scale_spin.setFixedWidth(100)
        self.dialog.rope_scale_spin.setSpecialValueText("Auto (0.0)")

        self.dialog.flash_attn_cb = QCheckBox("Flash Attention")
        self.dialog.mtp_cb = QCheckBox("MTP Speculative Decoding")

        self.dialog.mtp_max_spin = no_scroll(QSpinBox())
        self.dialog.mtp_max_spin.setRange(1, 16)
        self.dialog.mtp_max_spin.setFixedWidth(100)

        self.dialog.mtp_min_spin = no_scroll(QSpinBox())
        self.dialog.mtp_min_spin.setRange(0, 16)
        self.dialog.mtp_min_spin.setFixedWidth(100)

        self.dialog.mtp_max_cell = _grid_cell("MTP Max Draft", self.dialog.mtp_max_spin)
        self.dialog.mtp_min_cell = _grid_cell("MTP Min Draft", self.dialog.mtp_min_spin)

        arch_grid_w = QWidget()
        arch_grid = QGridLayout(arch_grid_w)
        arch_grid.setContentsMargins(0, 0, 0, 0)
        arch_grid.setHorizontalSpacing(24)
        arch_grid.setVerticalSpacing(12)

        arch_grid.addWidget(_grid_cell("Context size", self.dialog.ctx_spin), 0, 0)
        arch_grid.addWidget(_grid_cell("RoPE Base", self.dialog.rope_base_spin), 0, 1)
        arch_grid.addWidget(_grid_cell("RoPE Scale", self.dialog.rope_scale_spin), 0, 2)
        arch_grid.addWidget(self.dialog.flash_attn_cb, 1, 0)
        arch_grid.addWidget(self.dialog.mtp_cb, 1, 1, 1, 2)
        arch_grid.addWidget(self.dialog.mtp_max_cell, 2, 0)
        arch_grid.addWidget(self.dialog.mtp_min_cell, 2, 1)

        form.addWidget(arch_grid_w)
        
        sep_arch = QFrame()
        sep_arch.setObjectName("sep")
        sep_arch.setFrameShape(QFrame.Shape.HLine)
        form.addWidget(sep_arch)

        # --- Sampling & Penalties Section ---
        samp_title = QLabel("Sampling & Penalties")
        samp_title.setObjectName("pageSubtitle")
        form.addWidget(samp_title)

        self.dialog.temp_spin = no_scroll(QDoubleSpinBox())
        self.dialog.temp_spin.setRange(0.0, 2.0)
        self.dialog.temp_spin.setSingleStep(0.05)
        self.dialog.temp_spin.setDecimals(2)
        self.dialog.temp_spin.setFixedWidth(100)

        self.dialog.topk_spin = no_scroll(QSpinBox())
        self.dialog.topk_spin.setRange(0, 1000)
        self.dialog.topk_spin.setFixedWidth(100)

        self.dialog.topp_spin = no_scroll(QDoubleSpinBox())
        self.dialog.topp_spin.setRange(0.0, 1.0)
        self.dialog.topp_spin.setSingleStep(0.05)
        self.dialog.topp_spin.setDecimals(2)
        self.dialog.topp_spin.setFixedWidth(100)

        self.dialog.minp_spin = no_scroll(QDoubleSpinBox())
        self.dialog.minp_spin.setRange(0.0, 1.0)
        self.dialog.minp_spin.setSingleStep(0.01)
        self.dialog.minp_spin.setDecimals(2)
        self.dialog.minp_spin.setFixedWidth(100)
        
        self.dialog.typical_p_spin = no_scroll(QDoubleSpinBox())
        self.dialog.typical_p_spin.setRange(0.0, 1.0)
        self.dialog.typical_p_spin.setSingleStep(0.05)
        self.dialog.typical_p_spin.setDecimals(2)
        self.dialog.typical_p_spin.setFixedWidth(100)

        self.dialog.tfs_z_spin = no_scroll(QDoubleSpinBox())
        self.dialog.tfs_z_spin.setRange(1.0, 10.0)
        self.dialog.tfs_z_spin.setSingleStep(0.1)
        self.dialog.tfs_z_spin.setDecimals(2)
        self.dialog.tfs_z_spin.setFixedWidth(100)

        self.dialog.seed_spin = no_scroll(QSpinBox())
        self.dialog.seed_spin.setRange(-1, 2147483647)
        self.dialog.seed_spin.setFixedWidth(100)
        self.dialog.seed_spin.setSpecialValueText("Random (-1)")

        self.dialog.mirostat_spin = no_scroll(QSpinBox())
        self.dialog.mirostat_spin.setRange(0, 2)
        self.dialog.mirostat_spin.setFixedWidth(100)

        self.dialog.mirostat_tau_spin = no_scroll(QDoubleSpinBox())
        self.dialog.mirostat_tau_spin.setRange(0.0, 10.0)
        self.dialog.mirostat_tau_spin.setSingleStep(0.1)
        self.dialog.mirostat_tau_spin.setDecimals(2)
        self.dialog.mirostat_tau_spin.setFixedWidth(100)

        self.dialog.mirostat_eta_spin = no_scroll(QDoubleSpinBox())
        self.dialog.mirostat_eta_spin.setRange(0.0, 1.0)
        self.dialog.mirostat_eta_spin.setSingleStep(0.05)
        self.dialog.mirostat_eta_spin.setDecimals(2)
        self.dialog.mirostat_eta_spin.setFixedWidth(100)

        self.dialog.repeat_penalty_spin = no_scroll(QDoubleSpinBox())
        self.dialog.repeat_penalty_spin.setRange(1.0, 2.0)
        self.dialog.repeat_penalty_spin.setSingleStep(0.05)
        self.dialog.repeat_penalty_spin.setDecimals(2)
        self.dialog.repeat_penalty_spin.setFixedWidth(100)

        self.dialog.freq_penalty_spin = no_scroll(QDoubleSpinBox())
        self.dialog.freq_penalty_spin.setRange(0.0, 2.0)
        self.dialog.freq_penalty_spin.setSingleStep(0.05)
        self.dialog.freq_penalty_spin.setDecimals(2)
        self.dialog.freq_penalty_spin.setFixedWidth(100)

        self.dialog.pres_penalty_spin = no_scroll(QDoubleSpinBox())
        self.dialog.pres_penalty_spin.setRange(0.0, 2.0)
        self.dialog.pres_penalty_spin.setSingleStep(0.05)
        self.dialog.pres_penalty_spin.setDecimals(2)
        self.dialog.pres_penalty_spin.setFixedWidth(100)

        samp_grid_w = QWidget()
        samp_grid = QGridLayout(samp_grid_w)
        samp_grid.setContentsMargins(0, 0, 0, 0)
        samp_grid.setHorizontalSpacing(24)
        samp_grid.setVerticalSpacing(12)

        samp_grid.addWidget(_grid_cell("Temperature", self.dialog.temp_spin), 0, 0)
        samp_grid.addWidget(_grid_cell("Top-K", self.dialog.topk_spin), 0, 1)
        samp_grid.addWidget(_grid_cell("Top-P", self.dialog.topp_spin), 0, 2)
        samp_grid.addWidget(_grid_cell("Min-P", self.dialog.minp_spin), 1, 0)
        samp_grid.addWidget(_grid_cell("Typical-P", self.dialog.typical_p_spin), 1, 1)
        samp_grid.addWidget(_grid_cell("TFS-Z", self.dialog.tfs_z_spin), 1, 2)
        samp_grid.addWidget(_grid_cell("Seed", self.dialog.seed_spin), 2, 0)
        samp_grid.addWidget(_grid_cell("Mirostat", self.dialog.mirostat_spin), 2, 1)
        samp_grid.addWidget(_grid_cell("Mirostat Tau", self.dialog.mirostat_tau_spin), 2, 2)
        samp_grid.addWidget(_grid_cell("Mirostat Eta", self.dialog.mirostat_eta_spin), 3, 0)
        samp_grid.addWidget(_grid_cell("Repeat Penalty", self.dialog.repeat_penalty_spin), 3, 1)
        samp_grid.addWidget(_grid_cell("Freq Penalty", self.dialog.freq_penalty_spin), 3, 2)
        samp_grid.addWidget(_grid_cell("Pres Penalty", self.dialog.pres_penalty_spin), 4, 0)

        form.addWidget(samp_grid_w)
        
        sep_samp = QFrame()
        sep_samp.setObjectName("sep")
        sep_samp.setFrameShape(QFrame.Shape.HLine)
        form.addWidget(sep_samp)

        # --- Hardware & Server Section ---
        hw_title = QLabel("Hardware & Server")
        hw_title.setObjectName("pageSubtitle")
        form.addWidget(hw_title)

        self.dialog.gpu_spin = no_scroll(QSpinBox())
        self.dialog.gpu_spin.setRange(0, 999)
        self.dialog.gpu_spin.setFixedWidth(100)

        self.dialog.threads_spin = no_scroll(QSpinBox())
        self.dialog.threads_spin.setRange(-1, 256)
        self.dialog.threads_spin.setSpecialValueText("Auto (-1)")
        self.dialog.threads_spin.setFixedWidth(100)

        self.dialog.threads_batch_spin = no_scroll(QSpinBox())
        self.dialog.threads_batch_spin.setRange(-1, 256)
        self.dialog.threads_batch_spin.setSpecialValueText("Auto (-1)")
        self.dialog.threads_batch_spin.setFixedWidth(100)
        
        self.dialog.parallel_spin = no_scroll(QSpinBox())
        self.dialog.parallel_spin.setRange(1, 16)
        self.dialog.parallel_spin.setFixedWidth(100)

        self.dialog.batch_spin = no_scroll(QSpinBox())
        self.dialog.batch_spin.setRange(1, 131072)
        self.dialog.batch_spin.setSingleStep(512)
        self.dialog.batch_spin.setFixedWidth(100)

        self.dialog.ubatch_spin = no_scroll(QSpinBox())
        self.dialog.ubatch_spin.setRange(1, 131072)
        self.dialog.ubatch_spin.setSingleStep(128)
        self.dialog.ubatch_spin.setFixedWidth(100)

        self.dialog.keep_cb = QCheckBox("Keep autocorrect model loaded in memory")

        self.dialog.idle_spin = no_scroll(QSpinBox())
        self.dialog.idle_spin.setRange(30, 3600)
        self.dialog.idle_spin.setSingleStep(30)
        self.dialog.idle_spin.setFixedWidth(100)

        hw_grid_w = QWidget()
        hw_grid = QGridLayout(hw_grid_w)
        hw_grid.setContentsMargins(0, 0, 0, 0)
        hw_grid.setHorizontalSpacing(24)
        hw_grid.setVerticalSpacing(12)

        hw_grid.addWidget(_grid_cell("GPU layers", self.dialog.gpu_spin), 0, 0)
        hw_grid.addWidget(_grid_cell("Parallel slots", self.dialog.parallel_spin), 0, 1)
        hw_grid.addWidget(_grid_cell("CPU Threads", self.dialog.threads_spin), 0, 2)
        hw_grid.addWidget(_grid_cell("CPU Threads Batch", self.dialog.threads_batch_spin), 1, 0)
        hw_grid.addWidget(_grid_cell("Eval Batch", self.dialog.batch_spin), 1, 1)
        hw_grid.addWidget(_grid_cell("Phys Batch", self.dialog.ubatch_spin), 1, 2)
        
        hw_grid.addWidget(self.dialog.keep_cb, 2, 0, 1, 2)
        self.dialog.idle_timeout_cell = _grid_cell(
            "Idle timeout (s)", self.dialog.idle_spin
        )
        hw_grid.addWidget(self.dialog.idle_timeout_cell, 2, 2)

        form.addWidget(hw_grid_w)
        
        _apply_tooltips(self.dialog, "")
        
        form.addStretch()

        # Chat Parameters tab
        self.dialog.chat_params_tab = ChatParametersPage(self.dialog)

        self.tabs.addTab(model_tab, "Model Parameters")
        self.tabs.addTab(self.dialog.chat_params_tab, "Chat Parameters")
        pl.addWidget(self.tabs, 1)
        self.dialog.stack.addWidget(self)

    def _switch_tab(self, index):
        self.tabs.setCurrentIndex(index)

    def _on_tab_changed(self, index):
        self.tab_model_lbl.setProperty("active", index == 0)
        self.tab_chat_lbl.setProperty("active", index == 1)
        self.tab_model_lbl.style().unpolish(self.tab_model_lbl)
        self.tab_model_lbl.style().polish(self.tab_model_lbl)
        self.tab_chat_lbl.style().unpolish(self.tab_chat_lbl)
        self.tab_chat_lbl.style().polish(self.tab_chat_lbl)



class ProfilesPage(QWidget):
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self._build_ui()

    def _build_ui(self):
        form = make_scrollable_page("Correction Profiles", self)

        lbl_hk = QLabel("Custom Profiles")
        lbl_hk.setObjectName("pageSubtitle")
        form.addWidget(lbl_hk)

        desc_hk = QLabel(
            "Add custom hotkeys and assign a mode and strength to each profile."
        )
        desc_hk.setObjectName("settingsDescription")
        form.addWidget(desc_hk)

        sep_hk = QFrame()
        sep_hk.setObjectName("sep")
        sep_hk.setFrameShape(QFrame.Shape.HLine)
        form.addWidget(sep_hk)

        self.dialog.hotkeys_list_w = QListWidget()
        self.dialog.hotkeys_list_w.setObjectName("optionList")
        self.dialog.hotkeys_list_w.setSelectionMode(
            QListWidget.SelectionMode.SingleSelection
        )
        self.dialog.hotkeys_list_w.itemDoubleClicked.connect(
            self.dialog._on_hotkey_double_clicked
        )
        form.addWidget(self.dialog.hotkeys_list_w)

        add_hk_btn = QPushButton("+ Add Profile")
        add_hk_btn.setObjectName("addButton")
        add_hk_btn.clicked.connect(lambda: self.dialog._edit_hotkey(-1))
        form.addWidget(add_hk_btn)

        form.addStretch()


class TemplatesPage(QWidget):
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self._build_ui()

    def _build_ui(self):
        form = make_scrollable_page("Templates & Profile", self)

        sub1 = QLabel("Correction Settings")
        sub1.setObjectName("pageSubtitle")
        form.addWidget(sub1)

        self.dialog.sysprompt_edit = QTextEdit()
        self.dialog.sysprompt_edit.setPlaceholderText(
            "Leave blank to use the built-in correction prompt."
        )
        self.dialog.sysprompt_edit.setFixedHeight(140)
        self.dialog.sysprompt_edit.setObjectName("settingsPrompt")
        form.addLayout(
            self.dialog._field_group(
                "System prompt (override)",
                self.dialog.sysprompt_edit,
                desc="By using this, the patch method will no longer work. The app will behave like a normal chatbot, streaming the output word-by-word. This is much slower but can occasionally increase performance."
            )
        )

        self.dialog.chat_mode_combo = no_scroll(QComboBox())
        self.dialog.chat_mode_combo.addItems(
            [
                "Conversation (persistent chat history)",
                "Single Response (each message replaces the view)",
            ]
        )
        form.addLayout(
            self.dialog._field_group(
                "Chat Mode",
                self.dialog.chat_mode_combo,
                "Conversation mode keeps a running chat history. "
                "Single Response mode replaces the text view after each message.",
            )
        )

        sep_sections = QFrame()
        sep_sections.setObjectName("sep")
        sep_sections.setFrameShape(QFrame.Shape.HLine)
        form.addWidget(sep_sections)

        sub2 = QLabel("Model & Templates")
        sub2.setObjectName("pageSubtitle")
        form.addWidget(sub2)

        self.dialog.chat_use_separate_cb = QCheckBox("Use a separate, larger model for chatting")
        form.addWidget(self.dialog.chat_use_separate_cb)

        self.dialog.chat_model_edit = QLineEdit()
        self.dialog.chat_model_edit.setReadOnly(True)
        btn_chat = QPushButton("Browse")
        btn_chat.setObjectName("ghost")
        btn_chat.setFixedWidth(80)
        btn_chat.clicked.connect(
            lambda: self.dialog._browse_file(
                self.dialog.chat_model_edit, "Select chat model", "GGUF (*.gguf)"
            )
        )

        chat_row = QHBoxLayout()
        chat_row.setContentsMargins(0, 0, 0, 0)
        chat_row.addWidget(self.dialog.chat_model_edit, 1)
        chat_row.addWidget(btn_chat)
        self.dialog.chat_row_w = QWidget()
        self.dialog.chat_row_w.setLayout(chat_row)

        self.dialog.chat_model_lbl = QLabel("Chat model")
        self.dialog.chat_model_lbl.setObjectName("fieldGroupLabel")
        form.addWidget(self.dialog.chat_model_lbl)
        form.addWidget(self.dialog.chat_row_w)

        self.dialog.chat_keep_cb = QCheckBox("Keep chat model loaded in memory")
        self.dialog.chat_idle_spin = no_scroll(QSpinBox())
        self.dialog.chat_idle_spin.setRange(30, 3600)
        self.dialog.chat_idle_spin.setSingleStep(30)
        self.dialog.chat_idle_spin.setFixedWidth(100)

        self.dialog.chat_idle_timeout_cell = QWidget()
        chat_idle_cell_lay = QVBoxLayout(self.dialog.chat_idle_timeout_cell)
        chat_idle_cell_lay.setContentsMargins(0, 0, 0, 0)
        chat_idle_cell_lay.setSpacing(4)
        chat_idle_lbl = QLabel("Idle timeout (s)")
        chat_idle_lbl.setObjectName("fieldGroupLabel")
        chat_idle_cell_lay.addWidget(chat_idle_lbl)
        chat_idle_cell_lay.addWidget(self.dialog.chat_idle_spin)

        chat_param_row = QHBoxLayout()
        chat_param_row.setContentsMargins(0, 0, 0, 0)
        chat_param_row.addWidget(self.dialog.chat_keep_cb)
        chat_param_row.addSpacing(24)
        chat_param_row.addWidget(self.dialog.chat_idle_timeout_cell)
        chat_param_row.addStretch()

        self.dialog.chat_param_w = QWidget()
        self.dialog.chat_param_w.setLayout(chat_param_row)
        form.addWidget(self.dialog.chat_param_w)

        lbl = QLabel("Custom Templates")
        lbl.setObjectName("pageSubtitle")
        form.addWidget(lbl)

        desc_lbl = QLabel("Drag and drop to reorder templates.")
        desc_lbl.setObjectName("settingsDescription")
        form.addWidget(desc_lbl)

        self.dialog.templates_list_w = QListWidget()
        self.dialog.templates_list_w.setObjectName("optionList")
        self.dialog.templates_list_w.setDragDropMode(
            QListWidget.DragDropMode.InternalMove
        )
        self.dialog.templates_list_w.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.dialog.templates_list_w.setSelectionMode(
            QListWidget.SelectionMode.SingleSelection
        )
        self.dialog.templates_list_w.setMinimumHeight(80)
        self.dialog.templates_list_w.setMaximumHeight(250)
        self.dialog.templates_list_w.model().rowsMoved.connect(
            self.dialog._on_templates_reordered
        )
        self.dialog.templates_list_w.itemDoubleClicked.connect(
            self.dialog._on_template_double_clicked
        )
        form.addWidget(self.dialog.templates_list_w)

        buttons_layout = QHBoxLayout()
        buttons_layout.setContentsMargins(0, 0, 0, 0)

        add_tmpl_btn = QPushButton("+ Add Template")
        add_tmpl_btn.setObjectName("addButton")
        add_tmpl_btn.clicked.connect(lambda: self.dialog._edit_template(-1))

        delete_tmpl_btn = QPushButton("- Delete Template")
        delete_tmpl_btn.setObjectName("danger")
        delete_tmpl_btn.clicked.connect(self.dialog._delete_selected_template)

        buttons_layout.addWidget(add_tmpl_btn)
        buttons_layout.addWidget(delete_tmpl_btn)

        buttons_widget = QWidget()
        buttons_widget.setLayout(buttons_layout)
        form.addWidget(buttons_widget)

        form.addStretch()


class CorrectionModesPage(QWidget):
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self._build_ui()

    def _build_ui(self):
        from stet.core.text_utils import _wrap_correction_prompt

        self._form = make_scrollable_page("Correction Modes", self)
        self.dialog._mode_prompt_edits = []
        self.dialog._mode_reset_btns = []
        self._mode_previews = []
        self._wrap_fn = _wrap_correction_prompt

        # ── Built-in modes (indices 0–2) ──────────────────────────────────
        builtin_descs = [
            "Strict spelling-only corrections.",
            "Standard grammar, spelling, and punctuation fixes.",
            "Comprehensive rewriting for clarity and impact.",
        ]
        for i, (mode_name, desc_text) in enumerate(
            zip(["Spelling Only", "Full Correction", "Rewrite & Polish"], builtin_descs)
        ):
            sub = QLabel(mode_name)
            sub.setObjectName("pageSubtitle")
            self._form.addWidget(sub)

            desc = QLabel(desc_text)
            desc.setObjectName("settingsDescription")
            desc.setWordWrap(True)
            self._form.addWidget(desc)

            instr_label = QLabel("Prompt sent to the LLM for this mode:")
            instr_label.setObjectName("settingsSublabel")
            instr_label.setWordWrap(True)
            self._form.addWidget(instr_label)

            prompt_edit = QTextEdit()
            prompt_edit.setObjectName("settingsPrompt")
            prompt_edit.setFixedHeight(140)
            self._form.addWidget(prompt_edit)
            self.dialog._mode_prompt_edits.append(prompt_edit)

            reset_row = QHBoxLayout()
            reset_row.setContentsMargins(0, 0, 0, 0)
            reset_btn = QPushButton("Reset to Defaults")
            reset_btn.setObjectName("resetBtn")
            reset_btn.setFixedWidth(140)
            reset_btn.clicked.connect(lambda _, idx=i: self._reset_mode(idx))
            reset_row.addWidget(reset_btn)
            reset_row.addStretch()
            self._form.addLayout(reset_row)
            self.dialog._mode_reset_btns.append(reset_btn)

            sep = QFrame()
            sep.setObjectName("sep")
            sep.setFrameShape(QFrame.Shape.HLine)
            self._form.addWidget(sep)

        # ── Custom modes (indices 3+) — populated from config ─────────────
        # Track per-custom-mode container widgets so we can delete them
        self._custom_mode_widgets: list[QWidget] = []  # container per custom mode
        self._custom_name_edits:   list[QLineEdit] = []
        self._custom_enabled_cbs:  list[QCheckBox] = []
        self._custom_prompt_edits: list[QTextEdit] = []
        self._custom_previews:     list[QTextEdit] = []

        existing_customs = self.dialog.cfg.get("correction_modes", [])[3:]
        for slot in existing_customs:
            self._add_custom_mode_section(
                name=slot.get("name", "Custom Patch"),
                prompt=slot.get("prompt", ""),
                enabled=slot.get("enabled", False),
            )

        # "+ Add Custom Mode" button (kept at the bottom)
        self._add_custom_btn = QPushButton("+ Add Custom Mode")
        self._add_custom_btn.setObjectName("addButton")
        self._add_custom_btn.clicked.connect(self._on_add_custom_mode)
        self._form.addWidget(self._add_custom_btn)

        self._form.addStretch()

        # Backward-compat: first custom enabled cb (index 3)
        # settings.py's _load/_save reference self.dialog._custom_patch_enabled_cb
        # We keep that pointing at custom slot 0's checkbox, updated dynamically.
        self._sync_legacy_cb()

    # ── Custom mode helpers ───────────────────────────────────────────────

    def _sync_legacy_cb(self):
        """Keep dialog._custom_patch_enabled_cb pointing at custom slot 0 for
        backward-compat with any code that reads/writes it directly."""
        if self._custom_enabled_cbs:
            self.dialog._custom_patch_enabled_cb = self._custom_enabled_cbs[0]
        else:
            # No custom modes yet — create a detached placeholder so attr exists
            cb = QCheckBox()
            self.dialog._custom_patch_enabled_cb = cb

    def _add_custom_mode_section(
        self,
        name: str = "",
        prompt: str = "",
        enabled: bool = False,
    ) -> None:
        """Append one custom mode section to the form layout."""
        slot_idx = len(self._custom_mode_widgets)
        # global index into _mode_prompt_edits / _mode_previews
        global_idx = 3 + slot_idx

        # Container so we can remove the whole section cleanly
        container = QWidget()
        container_lay = QVBoxLayout(container)
        container_lay.setContentsMargins(0, 0, 0, 0)
        container_lay.setSpacing(8)

        # Header row: subtitle + delete button
        hdr_row = QHBoxLayout()
        hdr_row.setContentsMargins(0, 0, 0, 0)
        sub = QLabel(name if name else f"Custom Mode {slot_idx + 1}")
        sub.setObjectName("pageSubtitle")
        hdr_row.addWidget(sub, 1)

        del_btn = QPushButton("✕ Delete")
        del_btn.setObjectName("danger")
        del_btn.clicked.connect(lambda _, c=container: self._on_delete_custom_mode(c))
        hdr_row.addWidget(del_btn)
        container_lay.addLayout(hdr_row)

        # Mode name field
        name_lbl = QLabel("Mode name (shown in the Strength dropdown):")
        name_lbl.setObjectName("settingsSublabel")
        container_lay.addWidget(name_lbl)

        name_edit = QLineEdit(name)
        name_edit.setPlaceholderText("e.g. Legal Polish, Casual Tone…")
        # Keep subtitle in sync as the user types
        name_edit.textChanged.connect(lambda t, s=sub: s.setText(t or f"Custom Mode {slot_idx + 1}"))
        container_lay.addWidget(name_edit)

        # Enable checkbox
        enable_cb = QCheckBox("Enable — show in Strength dropdown")
        enable_cb.setChecked(enabled)
        container_lay.addWidget(enable_cb)

        # Instruction label + prompt editor
        instr_lbl = QLabel(
            "Behavioral instruction (structural rules and examples are auto-added):"
        )
        instr_lbl.setObjectName("settingsSublabel")
        instr_lbl.setWordWrap(True)
        container_lay.addWidget(instr_lbl)

        prompt_edit = QTextEdit()
        prompt_edit.setObjectName("settingsPrompt")
        prompt_edit.setFixedHeight(140)
        prompt_edit.setPlainText(prompt)
        prompt_edit.textChanged.connect(
            lambda gidx=global_idx: self._update_preview(gidx)
        )
        container_lay.addWidget(prompt_edit)

        # Gray out prompt editor when disabled
        def _on_enable_toggled(checked: bool):
            prompt_edit.setEnabled(checked)
            instr_lbl.setEnabled(checked)
            preview_toggle.setEnabled(checked)

        enable_cb.toggled.connect(_on_enable_toggled)
        if not enabled:
            prompt_edit.setEnabled(False)
            instr_lbl.setEnabled(False)

        # Collapsible preview
        preview_toggle = QPushButton("Show full assembled prompt")
        preview_toggle.setObjectName("previewToggle")
        preview_toggle.setCheckable(True)
        preview_toggle.toggled.connect(
            lambda checked, gidx=global_idx: self._toggle_preview(gidx, checked)
        )
        container_lay.addWidget(preview_toggle)

        preview_edit = QTextEdit()
        preview_edit.setObjectName("settingsPromptPreview")
        preview_edit.setReadOnly(True)
        preview_edit.setFixedHeight(140)
        preview_edit.hide()
        container_lay.addWidget(preview_edit)

        # Separator at the bottom of this section
        sep = QFrame()
        sep.setObjectName("sep")
        sep.setFrameShape(QFrame.Shape.HLine)
        container_lay.addWidget(sep)

        # Register in parallel lists (mirror of built-in pattern)
        self.dialog._mode_prompt_edits.append(prompt_edit)
        self.dialog._mode_reset_btns.append(None)   # no reset for custom modes
        self._mode_previews.append(preview_edit)

        self._custom_mode_widgets.append(container)
        self._custom_name_edits.append(name_edit)
        self._custom_enabled_cbs.append(enable_cb)
        self._custom_prompt_edits.append(prompt_edit)
        self._custom_previews.append(preview_edit)

        # Insert before the "+" button (which is always the second-to-last item)
        # The stretch is last; the button is before the stretch.
        # _form is a QVBoxLayout. We need to insert before the button.
        insert_pos = self._form.count() - 2  # before button and stretch
        self._form.insertWidget(insert_pos, container)

        self._sync_legacy_cb()

    def _on_add_custom_mode(self):
        self._add_custom_mode_section()

    def _on_delete_custom_mode(self, container: QWidget):
        if container not in self._custom_mode_widgets:
            return
        slot_idx = self._custom_mode_widgets.index(container)
        global_idx = 3 + slot_idx

        # Remove from all parallel lists
        self._custom_mode_widgets.pop(slot_idx)
        self._custom_name_edits.pop(slot_idx)
        self._custom_enabled_cbs.pop(slot_idx)
        self._custom_prompt_edits.pop(slot_idx)
        self._custom_previews.pop(slot_idx)

        # Remove from dialog lists too (global_idx into _mode_prompt_edits)
        if global_idx < len(self.dialog._mode_prompt_edits):
            self.dialog._mode_prompt_edits.pop(global_idx)
        if global_idx < len(self.dialog._mode_reset_btns):
            self.dialog._mode_reset_btns.pop(global_idx)
        # _mode_previews only holds custom mode previews (local index)
        local_idx = global_idx - 3
        if 0 <= local_idx < len(self._mode_previews):
            self._mode_previews.pop(local_idx)

        # Remove widget from layout and destroy
        self._form.removeWidget(container)
        container.deleteLater()

        self._sync_legacy_cb()

    # ── Preview helpers (custom modes only) ────────────────────────────────

    def _update_preview(self, idx: int):
        """Refresh the read-only preview when the user edits the instruction.
        Only custom modes (idx >= 3) have a preview; built-in modes show the
        full prompt directly in the editor."""
        local_idx = idx - 3
        if local_idx < 0 or local_idx >= len(self._mode_previews):
            return
        user_text = self.dialog._mode_prompt_edits[idx].toPlainText()
        try:
            full = self._wrap_fn(user_text, idx)
            self._mode_previews[local_idx].setPlainText(full)
        except Exception:
            self._mode_previews[local_idx].setPlainText(
                "(Preview unavailable — check your instruction syntax)"
            )

    def _toggle_preview(self, idx: int, checked: bool):
        local_idx = idx - 3
        if local_idx < 0 or local_idx >= len(self._mode_previews):
            return
        self._mode_previews[local_idx].setVisible(checked)
        self._update_preview(idx)

    def _reset_mode(self, idx: int):
        from stet.constants import DEFAULT_CONFIG

        default_modes = DEFAULT_CONFIG["correction_modes"]
        if idx < len(default_modes):
            self.dialog._mode_prompt_edits[idx].setPlainText(
                default_modes[idx]["prompt"]
            )



class ChatParametersPage(QWidget):
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self._build_ui()

    def _build_ui(self):
        form = make_scrollable_page(None, self, add_to_stack=False)

        # Helper for a labeled grid cell
        def _grid_cell(label: str, widget) -> QWidget:
            cell = QWidget()
            cell_lay = QVBoxLayout(cell)
            cell_lay.setContentsMargins(0, 0, 0, 0)
            cell_lay.setSpacing(4)
            lbl = QLabel(label)
            lbl.setObjectName("fieldGroupLabel")
            cell_lay.addWidget(lbl)
            cell_lay.addWidget(widget)
            return cell

        # --- Architecture Section ---
        arch_title = QLabel("Architecture")
        arch_title.setObjectName("pageSubtitle")
        form.addWidget(arch_title)

        self.dialog.chat_ctx_spin = no_scroll(QSpinBox())
        self.dialog.chat_ctx_spin.setRange(512, 131072)
        self.dialog.chat_ctx_spin.setSingleStep(512)
        self.dialog.chat_ctx_spin.setFixedWidth(100)

        self.dialog.chat_rope_base_spin = no_scroll(QDoubleSpinBox())
        self.dialog.chat_rope_base_spin.setRange(0.0, 10000000.0)
        self.dialog.chat_rope_base_spin.setSingleStep(1000.0)
        self.dialog.chat_rope_base_spin.setDecimals(1)
        self.dialog.chat_rope_base_spin.setFixedWidth(100)
        self.dialog.chat_rope_base_spin.setSpecialValueText("Auto (0.0)")

        self.dialog.chat_rope_scale_spin = no_scroll(QDoubleSpinBox())
        self.dialog.chat_rope_scale_spin.setRange(0.0, 1000.0)
        self.dialog.chat_rope_scale_spin.setSingleStep(0.1)
        self.dialog.chat_rope_scale_spin.setDecimals(2)
        self.dialog.chat_rope_scale_spin.setFixedWidth(100)
        self.dialog.chat_rope_scale_spin.setSpecialValueText("Auto (0.0)")

        self.dialog.chat_flash_attn_cb = QCheckBox("Flash Attention")
        self.dialog.chat_mtp_cb = QCheckBox("MTP Speculative Decoding")

        self.dialog.chat_mtp_max_spin = no_scroll(QSpinBox())
        self.dialog.chat_mtp_max_spin.setRange(1, 16)
        self.dialog.chat_mtp_max_spin.setFixedWidth(100)

        self.dialog.chat_mtp_min_spin = no_scroll(QSpinBox())
        self.dialog.chat_mtp_min_spin.setRange(0, 16)
        self.dialog.chat_mtp_min_spin.setFixedWidth(100)

        self.dialog.chat_mtp_max_cell = _grid_cell("MTP Max Draft", self.dialog.chat_mtp_max_spin)
        self.dialog.chat_mtp_min_cell = _grid_cell("MTP Min Draft", self.dialog.chat_mtp_min_spin)

        arch_grid_w = QWidget()
        arch_grid = QGridLayout(arch_grid_w)
        arch_grid.setContentsMargins(0, 0, 0, 0)
        arch_grid.setHorizontalSpacing(24)
        arch_grid.setVerticalSpacing(12)

        arch_grid.addWidget(_grid_cell("Context size", self.dialog.chat_ctx_spin), 0, 0)
        arch_grid.addWidget(_grid_cell("RoPE Base", self.dialog.chat_rope_base_spin), 0, 1)
        arch_grid.addWidget(_grid_cell("RoPE Scale", self.dialog.chat_rope_scale_spin), 0, 2)
        arch_grid.addWidget(self.dialog.chat_flash_attn_cb, 1, 0)
        arch_grid.addWidget(self.dialog.chat_mtp_cb, 1, 1, 1, 2)
        arch_grid.addWidget(self.dialog.chat_mtp_max_cell, 2, 0)
        arch_grid.addWidget(self.dialog.chat_mtp_min_cell, 2, 1)

        form.addWidget(arch_grid_w)
        
        sep_arch = QFrame()
        sep_arch.setObjectName("sep")
        sep_arch.setFrameShape(QFrame.Shape.HLine)
        form.addWidget(sep_arch)

        # --- Sampling & Penalties Section ---
        samp_title = QLabel("Sampling & Penalties")
        samp_title.setObjectName("pageSubtitle")
        form.addWidget(samp_title)

        self.dialog.chat_temp_spin = no_scroll(QDoubleSpinBox())
        self.dialog.chat_temp_spin.setRange(0.0, 2.0)
        self.dialog.chat_temp_spin.setSingleStep(0.05)
        self.dialog.chat_temp_spin.setDecimals(2)
        self.dialog.chat_temp_spin.setFixedWidth(100)

        self.dialog.chat_topk_spin = no_scroll(QSpinBox())
        self.dialog.chat_topk_spin.setRange(0, 1000)
        self.dialog.chat_topk_spin.setFixedWidth(100)

        self.dialog.chat_topp_spin = no_scroll(QDoubleSpinBox())
        self.dialog.chat_topp_spin.setRange(0.0, 1.0)
        self.dialog.chat_topp_spin.setSingleStep(0.05)
        self.dialog.chat_topp_spin.setDecimals(2)
        self.dialog.chat_topp_spin.setFixedWidth(100)

        self.dialog.chat_minp_spin = no_scroll(QDoubleSpinBox())
        self.dialog.chat_minp_spin.setRange(0.0, 1.0)
        self.dialog.chat_minp_spin.setSingleStep(0.01)
        self.dialog.chat_minp_spin.setDecimals(2)
        self.dialog.chat_minp_spin.setFixedWidth(100)
        
        self.dialog.chat_typical_p_spin = no_scroll(QDoubleSpinBox())
        self.dialog.chat_typical_p_spin.setRange(0.0, 1.0)
        self.dialog.chat_typical_p_spin.setSingleStep(0.05)
        self.dialog.chat_typical_p_spin.setDecimals(2)
        self.dialog.chat_typical_p_spin.setFixedWidth(100)

        self.dialog.chat_tfs_z_spin = no_scroll(QDoubleSpinBox())
        self.dialog.chat_tfs_z_spin.setRange(1.0, 10.0)
        self.dialog.chat_tfs_z_spin.setSingleStep(0.1)
        self.dialog.chat_tfs_z_spin.setDecimals(2)
        self.dialog.chat_tfs_z_spin.setFixedWidth(100)

        self.dialog.chat_seed_spin = no_scroll(QSpinBox())
        self.dialog.chat_seed_spin.setRange(-1, 2147483647)
        self.dialog.chat_seed_spin.setFixedWidth(100)
        self.dialog.chat_seed_spin.setSpecialValueText("Random (-1)")

        self.dialog.chat_mirostat_spin = no_scroll(QSpinBox())
        self.dialog.chat_mirostat_spin.setRange(0, 2)
        self.dialog.chat_mirostat_spin.setFixedWidth(100)

        self.dialog.chat_mirostat_tau_spin = no_scroll(QDoubleSpinBox())
        self.dialog.chat_mirostat_tau_spin.setRange(0.0, 10.0)
        self.dialog.chat_mirostat_tau_spin.setSingleStep(0.1)
        self.dialog.chat_mirostat_tau_spin.setDecimals(2)
        self.dialog.chat_mirostat_tau_spin.setFixedWidth(100)

        self.dialog.chat_mirostat_eta_spin = no_scroll(QDoubleSpinBox())
        self.dialog.chat_mirostat_eta_spin.setRange(0.0, 1.0)
        self.dialog.chat_mirostat_eta_spin.setSingleStep(0.05)
        self.dialog.chat_mirostat_eta_spin.setDecimals(2)
        self.dialog.chat_mirostat_eta_spin.setFixedWidth(100)

        self.dialog.chat_repeat_penalty_spin = no_scroll(QDoubleSpinBox())
        self.dialog.chat_repeat_penalty_spin.setRange(1.0, 2.0)
        self.dialog.chat_repeat_penalty_spin.setSingleStep(0.05)
        self.dialog.chat_repeat_penalty_spin.setDecimals(2)
        self.dialog.chat_repeat_penalty_spin.setFixedWidth(100)

        self.dialog.chat_freq_penalty_spin = no_scroll(QDoubleSpinBox())
        self.dialog.chat_freq_penalty_spin.setRange(0.0, 2.0)
        self.dialog.chat_freq_penalty_spin.setSingleStep(0.05)
        self.dialog.chat_freq_penalty_spin.setDecimals(2)
        self.dialog.chat_freq_penalty_spin.setFixedWidth(100)

        self.dialog.chat_pres_penalty_spin = no_scroll(QDoubleSpinBox())
        self.dialog.chat_pres_penalty_spin.setRange(0.0, 2.0)
        self.dialog.chat_pres_penalty_spin.setSingleStep(0.05)
        self.dialog.chat_pres_penalty_spin.setDecimals(2)
        self.dialog.chat_pres_penalty_spin.setFixedWidth(100)

        samp_grid_w = QWidget()
        samp_grid = QGridLayout(samp_grid_w)
        samp_grid.setContentsMargins(0, 0, 0, 0)
        samp_grid.setHorizontalSpacing(24)
        samp_grid.setVerticalSpacing(12)

        samp_grid.addWidget(_grid_cell("Temperature", self.dialog.chat_temp_spin), 0, 0)
        samp_grid.addWidget(_grid_cell("Top-K", self.dialog.chat_topk_spin), 0, 1)
        samp_grid.addWidget(_grid_cell("Top-P", self.dialog.chat_topp_spin), 0, 2)
        samp_grid.addWidget(_grid_cell("Min-P", self.dialog.chat_minp_spin), 1, 0)
        samp_grid.addWidget(_grid_cell("Typical-P", self.dialog.chat_typical_p_spin), 1, 1)
        samp_grid.addWidget(_grid_cell("TFS-Z", self.dialog.chat_tfs_z_spin), 1, 2)
        samp_grid.addWidget(_grid_cell("Seed", self.dialog.chat_seed_spin), 2, 0)
        samp_grid.addWidget(_grid_cell("Mirostat", self.dialog.chat_mirostat_spin), 2, 1)
        samp_grid.addWidget(_grid_cell("Mirostat Tau", self.dialog.chat_mirostat_tau_spin), 2, 2)
        samp_grid.addWidget(_grid_cell("Mirostat Eta", self.dialog.chat_mirostat_eta_spin), 3, 0)
        samp_grid.addWidget(_grid_cell("Repeat Penalty", self.dialog.chat_repeat_penalty_spin), 3, 1)
        samp_grid.addWidget(_grid_cell("Freq Penalty", self.dialog.chat_freq_penalty_spin), 3, 2)
        samp_grid.addWidget(_grid_cell("Pres Penalty", self.dialog.chat_pres_penalty_spin), 4, 0)

        form.addWidget(samp_grid_w)
        
        sep_samp = QFrame()
        sep_samp.setObjectName("sep")
        sep_samp.setFrameShape(QFrame.Shape.HLine)
        form.addWidget(sep_samp)

        # --- Hardware & Server Section ---
        hw_title = QLabel("Hardware & Server")
        hw_title.setObjectName("pageSubtitle")
        form.addWidget(hw_title)

        self.dialog.chat_gpu_spin = no_scroll(QSpinBox())
        self.dialog.chat_gpu_spin.setRange(0, 999)
        self.dialog.chat_gpu_spin.setFixedWidth(100)

        self.dialog.chat_threads_spin = no_scroll(QSpinBox())
        self.dialog.chat_threads_spin.setRange(-1, 256)
        self.dialog.chat_threads_spin.setSpecialValueText("Auto (-1)")
        self.dialog.chat_threads_spin.setFixedWidth(100)

        self.dialog.chat_threads_batch_spin = no_scroll(QSpinBox())
        self.dialog.chat_threads_batch_spin.setRange(-1, 256)
        self.dialog.chat_threads_batch_spin.setSpecialValueText("Auto (-1)")
        self.dialog.chat_threads_batch_spin.setFixedWidth(100)
        
        self.dialog.chat_parallel_spin = no_scroll(QSpinBox())
        self.dialog.chat_parallel_spin.setRange(1, 16)
        self.dialog.chat_parallel_spin.setFixedWidth(100)

        self.dialog.chat_batch_spin = no_scroll(QSpinBox())
        self.dialog.chat_batch_spin.setRange(1, 131072)
        self.dialog.chat_batch_spin.setSingleStep(512)
        self.dialog.chat_batch_spin.setFixedWidth(100)

        self.dialog.chat_ubatch_spin = no_scroll(QSpinBox())
        self.dialog.chat_ubatch_spin.setRange(1, 131072)
        self.dialog.chat_ubatch_spin.setSingleStep(128)
        self.dialog.chat_ubatch_spin.setFixedWidth(100)

        hw_grid_w = QWidget()
        hw_grid = QGridLayout(hw_grid_w)
        hw_grid.setContentsMargins(0, 0, 0, 0)
        hw_grid.setHorizontalSpacing(24)
        hw_grid.setVerticalSpacing(12)

        hw_grid.addWidget(_grid_cell("GPU layers", self.dialog.chat_gpu_spin), 0, 0)
        hw_grid.addWidget(_grid_cell("Parallel slots", self.dialog.chat_parallel_spin), 0, 1)
        hw_grid.addWidget(_grid_cell("CPU Threads", self.dialog.chat_threads_spin), 0, 2)
        hw_grid.addWidget(_grid_cell("CPU Threads Batch", self.dialog.chat_threads_batch_spin), 1, 0)
        hw_grid.addWidget(_grid_cell("Eval Batch", self.dialog.chat_batch_spin), 1, 1)
        hw_grid.addWidget(_grid_cell("Phys Batch", self.dialog.chat_ubatch_spin), 1, 2)
        
        form.addWidget(hw_grid_w)
        
        _apply_tooltips(self.dialog, "chat_")
        
        form.addStretch()

