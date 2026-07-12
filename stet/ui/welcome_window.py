import difflib
import html as _html
import tempfile
import threading
from pathlib import Path

from PyQt6.QtCore import QEvent, QPoint, QRect, Qt, QTimer, pyqtSignal, QSize
from PyQt6.QtGui import QCursor, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QLayout,
)

from stet.constants import DEFAULT_TEMPLATES, WELCOME_SAMPLE_TEXT
from stet.core.config import ConfigManager
from stet.core.text_utils import strip_preamble, strip_think
from stet.llm.worker import StreamWorker


class FlowLayout(QLayout):
    """A layout that arranges widgets in a flowing grid, wrapping to new rows."""

    def __init__(self, parent=None, h_spacing=8, v_spacing=8):
        super().__init__(parent)
        self._h_space = h_spacing
        self._v_space = v_spacing
        self._items: list[QLayout] = []

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only=False):
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        line_h = 0

        for item in self._items:
            wid = item.widget()
            if wid and not wid.isVisible():
                continue
            space_x = self._h_space
            space_y = self._v_space
            next_x = x + item.sizeHint().width() + space_x
            if next_x - space_x > effective.right() + 1 and line_h > 0:
                x = effective.x()
                y = y + line_h + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_h = max(line_h, item.sizeHint().height())

        return y + line_h - rect.y() + m.bottom()


class WelcomeWindow(QWidget):
    """Onboarding and home base window for Stet. Shown on launch or from tray."""

    settings_requested = pyqtSignal()
    correction_requested = pyqtSignal(str, str, str)  # (text, strength, template_name)
    closed_signal = pyqtSignal()

    # Chat streaming signals
    _chat_token = pyqtSignal(str)
    _chat_done = pyqtSignal(str)
    _chat_error = pyqtSignal(str)
    _do_stream_signal = pyqtSignal()

    def __init__(self, cfg: ConfigManager, ac_model=None):
        super().__init__()
        self.cfg = cfg
        self.ac_model = ac_model
        self._drag_pos = None
        self._resize_start = None
        self._resize_start_geometry = None
        self._selected_template = ""
        self._max_btn = None
        self._chat_history: list[dict] = []
        self._stream_worker: StreamWorker | None = None
        self._stream_buf = ""
        self._target_chat_model = None
        self._stream_backend = None
        self._chat_start_text = None
        self._output_mode: str | None = None  # "correction" or "chat"
        self._last_assistant_response: str = ""

        # 1. Window Flags and Attributes
        self._position_window()
        self.setMouseTracking(True)

        # 2. Build UI
        self._build_ui()

        # 3. Load QSS Style
        self._load_style()

        # 4. Connect Signals and Check Model
        self._connect_signals()
        self._update_correct_button_state()

    def _position_window(self):
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Set logo icon
        logo_path = Path(__file__).parents[2] / "logo.png"
        if logo_path.exists():
            self.setWindowIcon(QIcon(str(logo_path)))

        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        sr = screen.availableGeometry() if screen else None
        if sr:
            w = max(480, min(int(sr.width() * 0.4), 580, int(sr.width() * 0.85)))
            h = max(480, min(int(sr.height() * 0.75), 640, int(sr.height() * 0.85)))
        else:
            w, h = 580, 640
        self.setMinimumSize(480, 480)
        self.resize(w, h)

        # Center on available screen area
        if sr:
            cx, cy = sr.center().x(), sr.center().y()
        else:
            cx, cy = w // 2, h // 2
        self.move(cx - w // 2, cy - h // 2)

    def _load_style(self):
        from stet.ui.settings import THEME
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
        self.setStyleSheet(THEME.replace("{checkmark_url}", p))

    def _build_ui(self):
        # Outer layout containing the main card
        outer_lay = QVBoxLayout(self)
        outer_lay.setContentsMargins(0, 0, 0, 0)

        card = QWidget()
        card.setObjectName("welcomeCard")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        card_lay.setSpacing(0)
        outer_lay.addWidget(card)

        # Header Title Bar
        header = QWidget()
        header.setObjectName("welcomeHeader")
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(16, 12, 16, 12)

        header_title = QLabel("STET")
        header_title.setObjectName("welcomeHeaderTitle")
        header_lay.addWidget(header_title)

        header_lay.addStretch()

        # Window controls
        controls_lay = QHBoxLayout()
        controls_lay.setSpacing(8)
        controls_lay.setContentsMargins(0, 0, 0, 0)

        # Write standard SVG icons to tempfile
        min_svg = Path(tempfile.gettempdir()) / "stet_min.svg"
        max_svg = Path(tempfile.gettempdir()) / "stet_max.svg"
        close_svg = Path(tempfile.gettempdir()) / "stet_close.svg"
        restore_svg = Path(tempfile.gettempdir()) / "stet_restore.svg"
        try:
            if not min_svg.exists():
                min_svg.write_text(
                    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                    '<line x1="1" y1="5" x2="9" y2="5" stroke="#ffffff" stroke-width="1" stroke-linecap="round"/>'
                    '</svg>',
                    encoding="utf-8"
                )
            if not max_svg.exists():
                max_svg.write_text(
                    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                    '<rect x="1.5" y="1.5" width="7" height="7" fill="none" stroke="#ffffff" stroke-width="1"/>'
                    '</svg>',
                    encoding="utf-8"
                )
            if not close_svg.exists():
                close_svg.write_text(
                    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                    '<line x1="1.5" y1="1.5" x2="8.5" y2="8.5" stroke="#ffffff" stroke-width="1" stroke-linecap="round"/>'
                    '<line x1="8.5" y1="1.5" x2="1.5" y2="8.5" stroke="#ffffff" stroke-width="1" stroke-linecap="round"/>'
                    '</svg>',
                    encoding="utf-8"
                )
            if not restore_svg.exists():
                restore_svg.write_text(
                    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                    '<path d="M3,1.5 h5.5 v5.5 h-1.5 v-4 h-4 z" fill="none" stroke="#ffffff" stroke-width="1"/>'
                    '<rect x="1.5" y="3" width="5.5" height="5.5" fill="none" stroke="#ffffff" stroke-width="1"/>'
                    '</svg>',
                    encoding="utf-8"
                )
        except Exception:
            pass

        self._min_btn = QPushButton()
        self._min_btn.setObjectName("windowMinBtn")
        self._min_btn.setIcon(QIcon(str(min_svg)))
        self._min_btn.setFixedSize(28, 28)
        self._min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._min_btn.clicked.connect(self.showMinimized)

        self._max_btn = QPushButton()
        self._max_btn.setObjectName("windowMaxBtn")
        self._max_btn.setIcon(QIcon(str(restore_svg if self.isMaximized() else max_svg)))
        self._max_btn.setFixedSize(28, 28)
        self._max_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._max_btn.clicked.connect(self._toggle_maximized)

        self._close_btn = QPushButton()
        self._close_btn.setObjectName("windowCloseBtn")
        self._close_btn.setIcon(QIcon(str(close_svg)))
        self._close_btn.setFixedSize(28, 28)
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.clicked.connect(self.close)

        self._min_btn.setAccessibleName("Minimize window")
        self._min_btn.setToolTip("Minimize")
        self._max_btn.setAccessibleName("Maximize or restore window")
        self._max_btn.setToolTip("Maximize")
        self._close_btn.setAccessibleName("Close window")
        self._close_btn.setToolTip("Close")

        controls_lay.addWidget(self._min_btn)
        controls_lay.addWidget(self._max_btn)
        controls_lay.addWidget(self._close_btn)
        header_lay.addLayout(controls_lay)

        card_lay.addWidget(header)

        # Main Scrollable Area
        self._scroll = QScrollArea()
        self._scroll.setObjectName("welcomeScrollArea")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._scroll_content = QWidget()
        self._scroll_content.setObjectName("welcomeContent")
        self._scroll_content.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Expanding,
        )
        scroll_lay = QVBoxLayout(self._scroll_content)
        scroll_lay.setContentsMargins(24, 16, 24, 24)
        scroll_lay.setSpacing(16)
        self._scroll_lay = scroll_lay

        # Title & Subtitle
        title_widget = QWidget()
        tw_lay = QVBoxLayout(title_widget)
        tw_lay.setContentsMargins(0, 0, 0, 0)
        tw_lay.setSpacing(4)

        welcome_title = QLabel("Welcome to Stet")
        welcome_title.setObjectName("welcomeTitle")
        welcome_subtitle = QLabel("AI-powered text correction, running locally.")
        welcome_subtitle.setObjectName("welcomeSubtitle")
        welcome_subtitle.setWordWrap(True)

        tw_lay.addWidget(welcome_title)
        tw_lay.addWidget(welcome_subtitle)
        scroll_lay.addWidget(title_widget)

        # Try It Out
        try_it_hdr = QLabel("TRY IT OUT")
        try_it_hdr.setObjectName("welcomeSectionHeader")
        scroll_lay.addWidget(try_it_hdr)

        self._sample_input = QTextEdit()
        self._sample_input.setObjectName("welcomeSampleInput")
        self._sample_input.setPlaceholderText("Enter some text to try it out...")
        self._sample_input.setAcceptRichText(False)
        self._sample_input.setPlainText(WELCOME_SAMPLE_TEXT)
        self._sample_input.setMinimumHeight(80)
        self._sample_input.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        scroll_lay.addWidget(self._sample_input)

        # Correction Strength Selection
        strength_row = QHBoxLayout()
        strength_row.setContentsMargins(0, 0, 0, 0)
        strength_row.setSpacing(16)

        self._spelling_radio = QRadioButton("Spelling Only")
        self._spelling_radio.setObjectName("welcomeStrengthRadio")
        self._spelling_radio.setToolTip(
            "Fixes obvious typos and misspellings only. Leaves grammar and sentence structure untouched.\n\n"
            "Customizable in Settings > Correction Profiles."
        )
        strength_row.addWidget(self._spelling_radio)

        self._full_radio = QRadioButton("Full Correction")
        self._full_radio.setObjectName("welcomeStrengthRadio")
        self._full_radio.setChecked(True)
        self._full_radio.setToolTip(
            "Fixes spelling, grammar, and punctuation. Preserves your words and tone.\n\n"
            "Customizable in Settings > Correction Profiles."
        )
        strength_row.addWidget(self._full_radio)

        self._rewrite_radio = QRadioButton("Rewrite & Polish".replace("&", "&&"))
        self._rewrite_radio.setObjectName("welcomeStrengthRadio")
        self._rewrite_radio.setToolTip(
            "Rewrites sentences for clarity, flow, and polish. Best for formal writing.\n\n"
            "Customizable in Settings > Correction Profiles."
        )
        strength_row.addWidget(self._rewrite_radio)

        strength_row.addStretch()
        scroll_lay.addLayout(strength_row)

        # Template Pills (wrapping flow layout)
        template_container = QWidget()
        template_container.setObjectName("welcomeTemplateContainer")
        template_flow = FlowLayout(template_container, h_spacing=8, v_spacing=8)
        template_flow.setContentsMargins(0, 0, 0, 0)

        # Combine default and custom templates
        templates = self.cfg.get("custom_templates", []) or DEFAULT_TEMPLATES
        self._template_btns = []

        for tmpl in templates:
            name = tmpl.get("name", "")
            cleaned_name = name.replace("_", " ").replace("&", "&&")
            prompt = tmpl.get("prompt", "")
            btn = QPushButton(cleaned_name)
            btn.setObjectName("welcomeTemplateBtn")
            btn.setCheckable(True)
            first_line = prompt.splitlines()[0] if prompt else ""
            btn.setToolTip(f"{cleaned_name}\n\n{first_line}")
            btn.clicked.connect(lambda checked, b=btn, n=name: self._on_template_clicked(b, n))
            template_flow.addWidget(btn)
            self._template_btns.append(btn)

        scroll_lay.addWidget(template_container)

        # Correct CTA Row
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(12)

        self._correct_btn = QPushButton("Correct My Text")
        self._correct_btn.setObjectName("welcomeCorrectBtn")
        self._correct_btn.setMinimumHeight(32)
        self._correct_btn.clicked.connect(self._on_correct_clicked)
        action_row.addWidget(self._correct_btn, 1)

        chat_layout = QVBoxLayout()
        chat_layout.setSpacing(4)
        chat_lbl = QLabel("Chat Mode:")
        chat_lbl.setStyleSheet("color:#88898c; font-size:11px;")
        self._chat_combo = QComboBox()
        self._chat_combo.setObjectName("welcomeChatCombo")
        self._chat_combo.addItems(["Fresh", "Conversation"])
        self._chat_combo.setCurrentText("Fresh")
        self._chat_combo.setFixedWidth(120)
        chat_layout.addWidget(chat_lbl)
        chat_layout.addWidget(self._chat_combo)
        action_row.addLayout(chat_layout)

        scroll_lay.addLayout(action_row)

        # Unified Output Panel (initially hidden)
        # Serves as the single display for both correction results and chat
        # streaming output — same pattern as CorrectionWindow's corr_edit.
        self._unified_output = QWidget()
        self._unified_output.setObjectName("welcomeResultPanel")
        self._unified_output.hide()

        rp_lay = QVBoxLayout(self._unified_output)
        rp_lay.setContentsMargins(12, 12, 12, 12)
        rp_lay.setSpacing(8)

        # Output header: title + Reset + Copy buttons
        rp_hdr_row = QHBoxLayout()
        rp_hdr_row.setContentsMargins(0, 0, 0, 0)
        self._output_title = QLabel("CORRECTED TEXT")
        self._output_title.setObjectName("welcomeSectionHeader")
        rp_hdr_row.addWidget(self._output_title)
        rp_hdr_row.addStretch()

        self._chat_reset_btn = QPushButton("Reset")
        self._chat_reset_btn.setObjectName("welcomeChatResetBtn")
        self._chat_reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._chat_reset_btn.clicked.connect(self._on_chat_reset)
        rp_hdr_row.addWidget(self._chat_reset_btn)

        self._copy_btn = QPushButton("Copy Result")
        self._copy_btn.setObjectName("welcomeCopyBtn")
        self._copy_btn.setMinimumHeight(28)
        self._copy_btn.clicked.connect(self._on_copy_clicked)
        rp_hdr_row.addWidget(self._copy_btn)
        rp_lay.addLayout(rp_hdr_row)

        self._result_output = QTextEdit()
        self._result_output.setObjectName("welcomeResultOutput")
        self._result_output.setReadOnly(True)
        self._result_output.setMinimumHeight(80)
        self._result_output.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._result_output.setAcceptRichText(True)
        rp_lay.addWidget(self._result_output)

        scroll_lay.addWidget(self._unified_output)

        # Chat Input Section
        chat_input_widget = QWidget()
        chat_input_widget.setObjectName("welcomeChatInput")
        chat_input_lay = QHBoxLayout(chat_input_widget)
        chat_input_lay.setContentsMargins(0, 0, 0, 0)
        chat_input_lay.setSpacing(8)

        self._chat_input = QLineEdit()
        self._chat_input.setObjectName("welcomeChatLineEdit")
        self._chat_input.setPlaceholderText("Ask Stet anything...")
        self._chat_input.setMinimumHeight(32)
        self._chat_input.returnPressed.connect(self._on_chat_send)
        chat_input_lay.addWidget(self._chat_input, 1)

        self._chat_send_btn = QPushButton("Send")
        self._chat_send_btn.setObjectName("welcomeChatSendBtn")
        self._chat_send_btn.setMinimumHeight(32)
        self._chat_send_btn.setFixedWidth(60)
        self._chat_send_btn.clicked.connect(self._on_chat_send)
        chat_input_lay.addWidget(self._chat_send_btn)

        scroll_lay.addWidget(chat_input_widget)

        # Status/Error message label
        self._status_lbl = QLabel()
        self._status_lbl.setObjectName("welcomeStatusLabel")
        self._status_lbl.hide()
        scroll_lay.addWidget(self._status_lbl)

        # How Stet Works Infographic
        info_header = QLabel("HOW STET WORKS")
        info_header.setObjectName("welcomeSectionHeader")
        scroll_lay.addWidget(info_header)

        self.info_lbl = QLabel()
        self.info_lbl.setObjectName("welcomeInfographic")
        self.info_lbl.setMinimumHeight(100)
        self.info_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        resources_dir = Path(__file__).parent / "resources"
        svg_path = resources_dir / "how_stet_works.svg"
        png_path = resources_dir / "how_stet_works.png"

        self._info_svg_path = None
        self._info_pixmap = None

        if svg_path.exists():
            self._info_svg_path = svg_path
        elif png_path.exists():
            self._info_pixmap = QPixmap(str(png_path))

        info_container = QWidget()
        info_container.setObjectName("infoContainer")
        info_container_lay = QVBoxLayout(info_container)
        info_container_lay.setContentsMargins(0, 0, 0, 0)
        info_container_lay.addWidget(self.info_lbl, alignment=Qt.AlignmentFlag.AlignCenter)
        scroll_lay.addWidget(info_container)

        # How to Use Guide
        how_to_use_hdr = QLabel("HOW TO USE")
        how_to_use_hdr.setObjectName("welcomeSectionHeader")
        scroll_lay.addWidget(how_to_use_hdr)

        # Guide - Keyboard Shortcuts
        shortcuts_widget = QWidget()
        sw_lay = QVBoxLayout(shortcuts_widget)
        sw_lay.setContentsMargins(0, 0, 0, 0)
        sw_lay.setSpacing(6)

        shortcuts_title = QLabel("Keyboard Shortcuts")
        shortcuts_title.setObjectName("welcomeSectionSubTitle")
        sw_lay.addWidget(shortcuts_title)

        hotkeys = self.cfg.get("hotkeys", [])
        if not hotkeys:
            hotkeys = [
                {"shortcut": "f9", "mode": "panel", "strength": "full_correction"},
                {"shortcut": "f10", "mode": "silent", "strength": "spelling_only"},
                {"shortcut": "shift+f9", "mode": "panel", "strength": "rewrite_polish"},
            ]

        for hk in hotkeys:
            shortcut_str = "+".join(p.capitalize() for p in hk.get("shortcut", "").split("+"))
            mode = hk.get("mode", "panel")
            strength = hk.get("strength", "full_correction")

            mode_desc = "Open correction panel" if mode == "panel" else "Silent correct & paste"
            strength_lbls = {
                "spelling_only": "Spelling Only",
                "full_correction": "Full Correction",
                "rewrite_polish": "Rewrite & Polish",
            }
            strength_desc = strength_lbls.get(strength, strength.replace("_", " ").title())

            hk_lbl = QLabel(
                f'<span style="color:#d4a373; font-weight:bold;">{shortcut_str}</span>'
                f'&nbsp;&nbsp;&middot;&nbsp;&nbsp;'
                f'<span style="color:#88898c;">{mode_desc} ({strength_desc})</span>'
            )
            hk_lbl.setObjectName("welcomeHotkeyLabel")
            sw_lay.addWidget(hk_lbl)

        scroll_lay.addWidget(shortcuts_widget)

        # Guide - Correction Strengths
        strengths_widget = QWidget()
        str_lay = QVBoxLayout(strengths_widget)
        str_lay.setContentsMargins(0, 0, 0, 0)
        str_lay.setSpacing(8)

        str_hdr_row = QHBoxLayout()
        str_hdr_row.setContentsMargins(0, 0, 0, 0)
        str_title = QLabel("Correction Strengths")
        str_title.setObjectName("welcomeSectionSubTitle")
        str_hdr_row.addWidget(str_title)
        str_hdr_row.addStretch()

        str_link = QPushButton("Customizable ↗")
        str_link.setObjectName("welcomeLinkBtn")
        str_link.setCursor(Qt.CursorShape.PointingHandCursor)
        str_link.clicked.connect(self.settings_requested.emit)
        str_hdr_row.addWidget(str_link)
        str_lay.addLayout(str_hdr_row)

        str_desc_grid = QGridLayout()
        str_desc_grid.setContentsMargins(0, 0, 0, 0)
        str_desc_grid.setHorizontalSpacing(16)
        str_desc_grid.setVerticalSpacing(6)

        strengths_data = [
            ("Spelling Only", "Fix obvious typos and misspellings only. Leaves grammar and structure untouched."),
            ("Full Correction", "Fixes spelling, grammar, and punctuation. Preserves your words and tone."),
            ("Rewrite & Polish", "Rewrites sentences for clarity, flow, and polish. Best for formal writing."),
        ]
        for i, (name, desc) in enumerate(strengths_data):
            name_lbl = QLabel(name)
            name_lbl.setObjectName("welcomeStrengthLabel")
            name_lbl.setMinimumWidth(110)
            name_lbl.setMaximumWidth(140)

            desc_lbl = QLabel(desc)
            desc_lbl.setObjectName("welcomeStrengthDesc")
            desc_lbl.setWordWrap(True)

            str_desc_grid.addWidget(name_lbl, i, 0)
            str_desc_grid.addWidget(desc_lbl, i, 1)

        str_lay.addLayout(str_desc_grid)
        scroll_lay.addWidget(strengths_widget)

        # Guide - Templates
        templates_widget = QWidget()
        tmpl_lay = QVBoxLayout(templates_widget)
        tmpl_lay.setContentsMargins(0, 0, 0, 0)
        tmpl_lay.setSpacing(6)

        tmpl_hdr_row = QHBoxLayout()
        tmpl_hdr_row.setContentsMargins(0, 0, 0, 0)
        tmpl_title = QLabel("Templates")
        tmpl_title.setObjectName("welcomeSectionSubTitle")
        tmpl_hdr_row.addWidget(tmpl_title)
        tmpl_hdr_row.addStretch()

        tmpl_link = QPushButton("Manage ↗")
        tmpl_link.setObjectName("welcomeLinkBtn")
        tmpl_link.setCursor(Qt.CursorShape.PointingHandCursor)
        tmpl_link.clicked.connect(self.settings_requested.emit)
        tmpl_hdr_row.addWidget(tmpl_link)
        tmpl_lay.addLayout(tmpl_hdr_row)

        tmpl_desc = QLabel(
            "Templates let you give Stet a specific goal — 'Professional tone', 'Clean up dictation', and more. "
            "Click any pill above to try one."
        )
        tmpl_desc.setObjectName("welcomeTemplatesDesc")
        tmpl_desc.setWordWrap(True)
        tmpl_lay.addWidget(tmpl_desc)

        scroll_lay.addWidget(templates_widget)

        # Bottom Quick Links
        links_row = QHBoxLayout()
        links_row.setContentsMargins(0, 0, 0, 0)
        links_row.setSpacing(16)

        settings_link = QPushButton("→ Open Settings")
        settings_link.setObjectName("welcomeLinkBtn")
        settings_link.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_link.clicked.connect(self.settings_requested.emit)
        links_row.addWidget(settings_link)

        browse_templates_link = QPushButton("→ Browse Templates")
        browse_templates_link.setObjectName("welcomeLinkBtn")
        browse_templates_link.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_templates_link.clicked.connect(self.settings_requested.emit)
        links_row.addWidget(browse_templates_link)

        links_row.addStretch()
        scroll_lay.addLayout(links_row)

        # Set Scroll Widget
        self._scroll.setWidget(self._scroll_content)
        card_lay.addWidget(self._scroll)

        # Bottom Configuration Checkboxes (Footer)
        footer = QWidget()
        footer.setObjectName("welcomeFooter")
        footer_lay = QHBoxLayout(footer)
        footer_lay.setContentsMargins(24, 12, 24, 12)
        footer_lay.setSpacing(24)

        self._startup_cb = QCheckBox("Run at startup")
        self._startup_cb.setObjectName("welcomeCheckbox")
        self._startup_cb.setChecked(self.cfg.get("startup_on_login", False))
        self._startup_cb.toggled.connect(self._on_startup_toggled)
        footer_lay.addWidget(self._startup_cb)

        self._show_on_launch_cb = QCheckBox("Show this window on launch")
        self._show_on_launch_cb.setObjectName("welcomeCheckbox")
        self._show_on_launch_cb.setChecked(self.cfg.get("show_welcome_on_startup", True))
        self._show_on_launch_cb.toggled.connect(self._on_show_on_launch_toggled)
        footer_lay.addWidget(self._show_on_launch_cb)

        footer_lay.addStretch()
        card_lay.addWidget(footer)



    def _on_template_clicked(self, clicked_btn, name):
        for btn in self._template_btns:
            if btn != clicked_btn:
                btn.setChecked(False)

        if clicked_btn.isChecked():
            self._selected_template = name
        else:
            self._selected_template = ""

    def _on_correct_clicked(self):
        text = self._sample_input.toPlainText()
        if self._spelling_radio.isChecked():
            strength = "spelling_only"
        elif self._rewrite_radio.isChecked():
            strength = "rewrite_polish"
        else:
            strength = "full_correction"
        self.correction_requested.emit(text, strength, self._selected_template)

    def _on_chat_send(self):
        msg = self._chat_input.text().strip()
        if not msg:
            return
        if self.ac_model is None or not self.ac_model.is_loaded():
            self._chat_history.clear()
            self._output_mode = "chat"
            self._unified_output.show()
            self._output_title.setText("CHAT")
            self._render_chat_transcript(assistant_msg="Model not loaded. Open Settings to load a model first.")
            return

        self._chat_input.clear()
        self._chat_send_btn.setEnabled(False)
        self._output_mode = "chat"
        self._output_title.setText("CHAT")
        self._unified_output.show()

        conversation_mode = self._chat_combo.currentText() == "Conversation"
        if not conversation_mode:
            self._chat_history.clear()

        system = (
            "You are a helpful writing assistant. The user may ask you to rewrite, "
            "shorten, change tone, or otherwise modify text. "
            "Respond with ONLY the new text unless the user explicitly asks a question."
        )
        sample_text = self._sample_input.toPlainText().strip()
        if sample_text:
            system += f'\n\nThe user\'s text to work with:\n"""\n{sample_text}\n"""'
        custom_sys = self.cfg.get("system_prompt", "").strip()
        if custom_sys:
            system += f"\n\nAdditional instructions:\n{custom_sys}"

        # Always (re)set the system message so the current sample text is used,
        # even in Conversation mode where history persists between sends.
        if self._chat_history and self._chat_history[0]["role"] == "system":
            self._chat_history[0]["content"] = system
        else:
            self._chat_history.insert(0, {"role": "system", "content": system})
        self._chat_history.append({"role": "user", "content": msg})

        self._render_chat_transcript(assistant_msg="Generating...")

        self._target_chat_model = self.ac_model

        if not self._target_chat_model.is_loaded():
            self._render_chat_transcript(assistant_msg="Loading model...")
            threading.Thread(target=self._load_then_stream, daemon=True).start()
            return

        self._do_stream()

    def _load_then_stream(self):
        self._target_chat_model.load_model()
        if self._target_chat_model.is_loaded():
            self._do_stream_signal.emit()
        else:
            self._chat_error.emit("Model could not be loaded. Check Settings.")

    def _do_stream(self):
        self._stream_buf = ""
        self._stream_backend = self._target_chat_model
        worker = self._target_chat_model.make_stream_worker(self._chat_history, max_tokens=1024)
        worker.token.connect(self._chat_token)
        worker.done.connect(self._chat_done)
        worker.error.connect(self._chat_error)
        self._target_chat_model.mark_used()
        self._stream_worker = worker
        worker.start()

    def _on_chat_token(self, token: str):
        self._stream_buf += token
        self._render_chat_transcript(assistant_msg=self._stream_buf)

    def _on_chat_done(self, full: str):
        if self._stream_backend is not None:
            self._stream_backend.mark_used()
        full = strip_preamble(strip_think(full), "")
        self._chat_history.append({"role": "assistant", "content": full})
        if len(self._chat_history) > 40:
            self._chat_history = self._chat_history[-40:]
        self._last_assistant_response = full
        self._render_chat_transcript()
        self._chat_send_btn.setEnabled(True)

    def _on_chat_error(self, err: str):
        error_text = f"Error: {err}"
        self._chat_history.append({"role": "assistant", "content": error_text})
        self._last_assistant_response = error_text
        self._render_chat_transcript()
        self._chat_send_btn.setEnabled(True)

    def _render_chat_transcript(self, assistant_msg: str | None = None):
        """Render the chat conversation as an HTML transcript in the unified output.

        If ``assistant_msg`` is provided, it is shown as a temporary assistant
        message at the end of the transcript (used while streaming or for
        placeholder/error states) without adding it to history.
        """
        parts = [
            '<div style="font-family: IBM Plex Mono, Consolas, monospace; '
            'font-size:13px; line-height:1.5;">'
        ]
        for entry in self._chat_history:
            if entry["role"] == "system":
                continue
            role = entry["role"]
            text = entry["content"]
            parts.append(self._chat_message_html(role, text))
        if assistant_msg is not None:
            parts.append(self._chat_message_html("assistant", assistant_msg))
        parts.append("</div>")
        self._result_output.setHtml("".join(parts))
        self._unified_output.show()

    @staticmethod
    def _chat_message_html(role: str, text: str) -> str:
        escaped = _html.escape(text).replace("\n", "<br>")
        if role == "user":
            return (
                f'<div style="margin:4px 0; text-align:right;">'
                f'<span style="color:#93c5fd; font-weight:500;">{escaped}</span>'
                f'</div>'
            )
        return (
            f'<div style="margin:4px 0; text-align:left;">'
            f'<span style="color:#e2e8f0;">{escaped}</span>'
            f'</div>'
        )

    def _on_chat_reset(self):
        if self._stream_worker and self._stream_worker.isRunning():
            try:
                self._stream_worker.blockSignals(True)
                self._stream_worker.stop()
                self._stream_worker.wait(500)
            except Exception:
                pass
            self._stream_worker = None
        self._chat_history.clear()
        self._last_assistant_response = ""
        # Hide the output area when chat is reset.
        self._unified_output.hide()
        # If a correction result exists, restore the correction diff view.
        if hasattr(self, "_corrected_text") and self._corrected_text:
            self._output_mode = "correction"
            self._output_title.setText("CORRECTED TEXT")
            self._result_output.setHtml(self._diff_html(self._corrected_text))
            self._unified_output.show()
        self._chat_send_btn.setEnabled(True)

    def _on_copy_clicked(self):
        if self._output_mode == "chat" and self._last_assistant_response:
            QApplication.clipboard().setText(self._last_assistant_response)
        elif hasattr(self, "_corrected_text") and self._corrected_text:
            QApplication.clipboard().setText(self._corrected_text)

    def _on_startup_toggled(self, checked):
        self.cfg.set("startup_on_login", checked)

    def _on_show_on_launch_toggled(self, checked):
        self.cfg.set("show_welcome_on_startup", checked)

    def _connect_signals(self):
        if self.ac_model is not None and hasattr(self.ac_model, "status_changed"):
            self.ac_model.status_changed.connect(self._on_model_status)
        self._chat_token.connect(self._on_chat_token)
        self._chat_done.connect(self._on_chat_done)
        self._chat_error.connect(self._on_chat_error)
        self._do_stream_signal.connect(self._do_stream)

    def _on_model_status(self, status: str):
        self._update_correct_button_state()

    def _update_correct_button_state(self):
        if self.ac_model is None or not self.ac_model.is_loaded():
            self._correct_btn.setEnabled(False)
            self._correct_btn.setText("Load model in Settings first")
        else:
            self._correct_btn.setEnabled(True)
            self._correct_btn.setText("Correct My Text")

    def _toggle_maximized(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            if self._max_btn is not None:
                max_svg = Path(tempfile.gettempdir()) / "stet_max.svg"
                restore_svg = Path(tempfile.gettempdir()) / "stet_restore.svg"
                icon_path = restore_svg if self.isMaximized() else max_svg
                self._max_btn.setIcon(QIcon(str(icon_path)))
        super().changeEvent(event)

    def set_correcting(self, active: bool):
        if active:
            self._correct_btn.setEnabled(False)
            self._correct_btn.setText("Correcting...")
            self._status_lbl.setText("● AI is correcting...")
            self._status_lbl.setStyleSheet("color: #fbbf24;")
            self._status_lbl.show()
        else:
            self._update_correct_button_state()
            self._status_lbl.hide()

    def set_error(self, message: str = "Correction failed — check model status in Settings"):
        self._update_correct_button_state()
        self._status_lbl.setText(f"● {message}")
        self._status_lbl.setStyleSheet("color: #f87171;")
        self._status_lbl.show()

    def set_corrected_text(self, original: str, corrected: str):
        # Stop any running chat stream so it doesn't overwrite the correction result
        if self._stream_worker and self._stream_worker.isRunning():
            try:
                self._stream_worker.blockSignals(True)
                self._stream_worker.stop()
                self._stream_worker.wait(500)
            except Exception:
                pass
            self._stream_worker = None
        self._corrected_text = corrected
        self.original = original
        self._output_mode = "correction"
        self._output_title.setText("CORRECTED TEXT")
        self.set_correcting(False)
        html = self._diff_html(corrected)
        self._result_output.setHtml(html)
        self._unified_output.show()
        # Force re-layout at every level so the scroll area's viewport re-measures the
        # expanded content rather than reusing a stale size cache.
        self._unified_output.adjustSize()
        self._scroll_lay.activate()
        self._scroll_content.updateGeometry()
        self._scroll.update()
        self._scroll.ensureWidgetVisible(self._unified_output, 24, 24)

    # --- Mouse Event Dragging & Resizing ---
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            pos = e.position().toPoint()
            if pos.x() >= self.width() - 15 and pos.y() >= self.height() - 15:
                self._resize_start = e.globalPosition().toPoint()
                self._resize_start_geometry = self.geometry()
                return

            ch = self.childAt(pos)
            block_list = (
                QTextEdit,
                QPlainTextEdit,
                QLineEdit,
                QComboBox,
                QScrollBar,
                QCheckBox,
                QRadioButton,
                QPushButton,
            )
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
            pos = e.position().toPoint()
            if pos.x() >= self.width() - 15 and pos.y() >= self.height() - 15:
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            else:
                self.unsetCursor()

        if hasattr(self, "_resize_start") and self._resize_start:
            delta = e.globalPosition().toPoint() - self._resize_start
            new_w = max(self.minimumWidth(), self._resize_start_geometry.width() + delta.x())
            new_h = max(self.minimumHeight(), self._resize_start_geometry.height() + delta.y())
            self.resize(new_w, new_h)
        elif hasattr(self, "_drag_pos") and self._drag_pos and e.buttons() == Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        self._resize_start = None

    # --- Infographic Scaling ---
    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(50, self._update_infographic)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_infographic()

    def _update_infographic(self):
        max_w = self.width() - 48
        max_h = int(self.height() * 0.40)

        if hasattr(self, "_info_svg_path") and self._info_svg_path:
            try:
                from PyQt6.QtSvg import QSvgRenderer
                from PyQt6.QtGui import QImage, QPainter

                sz = QSize(800, 400)
                sz.scale(max_w, max_h, Qt.AspectRatioMode.KeepAspectRatio)

                if sz.width() > 0 and sz.height() > 0:
                    renderer = QSvgRenderer(str(self._info_svg_path))
                    if renderer.isValid():
                        image = QImage(sz, QImage.Format.Format_ARGB32)
                        image.fill(0)  # Transparent
                        painter = QPainter(image)
                        renderer.render(painter)
                        painter.end()

                        self.info_lbl.setFixedSize(sz)
                        self.info_lbl.setPixmap(QPixmap.fromImage(image))
                        return
            except Exception as ex:
                print(f"Error rendering SVG infographic: {ex}")

        if hasattr(self, "_info_pixmap") and self._info_pixmap and not self._info_pixmap.isNull():
            sz = QSize(self._info_pixmap.size())
            sz.scale(max_w, max_h, Qt.AspectRatioMode.KeepAspectRatio)
            scaled = self._info_pixmap.scaled(
                sz,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.info_lbl.setFixedSize(sz)
            self.info_lbl.setPixmap(scaled)

    def closeEvent(self, e):
        if hasattr(self, "_stream_worker") and self._stream_worker and self._stream_worker.isRunning():
            try:
                self._stream_worker.blockSignals(True)
                self._stream_worker.stop()
                self._stream_worker.wait(500)
            except Exception:
                pass
            self._stream_worker = None
        if self.ac_model is not None and hasattr(self.ac_model, "status_changed"):
            try:
                self.ac_model.status_changed.disconnect(self._on_model_status)
            except TypeError:
                pass
        self.closed_signal.emit()
        super().closeEvent(e)

    # --- Diff Highlighting Logic ---
    def _diff_html(self, corrected: str, final_only: bool = False) -> str:
        NL, orig_words, corr_words, opcodes = self._word_diff(corrected)
        parts: list[str] = []

        def check_typo_fix(w1: str, w2: str) -> bool:
            import string
            s1 = w1.strip(string.punctuation).strip().lower()
            s2 = w2.strip(string.punctuation).strip().lower()
            if not s1 or not s2:
                return False
            if s1 == s2:
                return True
            m, n = len(s1), len(s2)
            if abs(m - n) > 2:
                return False
            prev = list(range(n + 1))
            curr = [0] * (n + 1)
            for i in range(1, m + 1):
                curr[0] = i
                for j in range(1, n + 1):
                    cost = 0 if s1[i - 1] == s2[j - 1] else 1
                    curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
                prev = list(curr)
            return prev[n] <= 2

        for tag, i1, i2, j1, j2 in opcodes:
            if tag == "equal":
                for w in corr_words[j1:j2]:
                    if w == NL:
                        parts.append("<br>")
                    else:
                        parts.append(_html.escape(w) + " ")
            elif tag == "delete":
                if not final_only:
                    for w in orig_words[i1:i2]:
                        if w == NL:
                            parts.append("<br>")
                        else:
                            parts.append(
                                f'<span style="background:rgba(248,113,113,0.1);'
                                f'color:#f87171;text-decoration:line-through;border-radius:0px;padding:0px 2px;">'
                                f"{_html.escape(w)}</span> "
                            )
            elif tag == "insert":
                for w in corr_words[j1:j2]:
                    if w == NL:
                        parts.append("<br>")
                    else:
                        if final_only:
                            parts.append(
                                f'<span style="color:#60a5fa;text-decoration:underline;">'
                                f"{_html.escape(w)}</span> "
                            )
                        else:
                            parts.append(
                                f'<span style="background:rgba(96,165,250,0.12);'
                                f'color:#60a5fa;text-decoration:none;border-radius:0px;padding:0px 2px;">'
                                f"{_html.escape(w)}</span> "
                            )
            elif tag == "replace":
                if not final_only:
                    for w in orig_words[i1:i2]:
                        if w == NL:
                            parts.append("<br>")
                        else:
                            parts.append(
                                f'<span style="background:rgba(248,113,113,0.1);'
                                f'color:#f87171;text-decoration:line-through;border-radius:0px;padding:0px 2px;">'
                                f"{_html.escape(w)}</span> "
                            )

                is_single_typo = (i2 - i1 == 1) and (j2 - j1 == 1) and check_typo_fix(orig_words[i1], corr_words[j1])
                for w in corr_words[j1:j2]:
                    if w == NL:
                        parts.append("<br>")
                    else:
                        if is_single_typo:
                            if final_only:
                                parts.append(
                                    f'<span style="color:#4ade80;text-decoration:underline;">'
                                    f"{_html.escape(w)}</span> "
                                )
                            else:
                                parts.append(
                                    f'<span style="background:rgba(74,222,128,0.12);'
                                    f'color:#4ade80;text-decoration:none;border-radius:0px;padding:0px 2px;">'
                                    f"{_html.escape(w)}</span> "
                                )
                        else:
                            if final_only:
                                parts.append(
                                    f'<span style="color:#60a5fa;text-decoration:underline;">'
                                    f"{_html.escape(w)}</span> "
                                )
                            else:
                                parts.append(
                                    f'<span style="background:rgba(96,165,250,0.12);'
                                    f'color:#60a5fa;text-decoration:none;border-radius:0px;padding:0px 2px;">'
                                    f"{_html.escape(w)}</span> "
                                )
        return "".join(parts).replace(" <br>", "<br>").replace("<br> ", "<br>")

    def _split_opcodes_by_nl(
        self,
        orig_words: list[str],
        corr_words: list[str],
        opcodes: list[tuple[str, int, int, int, int]],
        nl_token: str,
    ) -> list[tuple[str, int, int, int, int]]:
        new_opcodes = []
        for tag, i1, i2, j1, j2 in opcodes:
            has_nl_orig = any(w == nl_token for w in orig_words[i1:i2])
            has_nl_corr = any(w == nl_token for w in corr_words[j1:j2])
            if not has_nl_orig and not has_nl_corr:
                new_opcodes.append((tag, i1, i2, j1, j2))
                continue

            orig_lines = []
            curr_start = i1
            for idx in range(i1, i2):
                if orig_words[idx] == nl_token:
                    orig_lines.append((curr_start, idx))
                    curr_start = idx + 1
            orig_lines.append((curr_start, i2))

            corr_lines = []
            curr_start = j1
            for idx in range(j1, j2):
                if corr_words[idx] == nl_token:
                    corr_lines.append((curr_start, idx))
                    curr_start = idx + 1
            corr_lines.append((curr_start, j2))

            M = len(orig_lines)
            N = len(corr_lines)
            max_len = max(M, N)

            for idx in range(max_len):
                o_start, o_end = (orig_lines[idx] if idx < M else (i2, i2))
                c_start, c_end = (corr_lines[idx] if idx < N else (j2, j2))

                if idx < M and idx < N:
                    if o_start == o_end and c_start == c_end:
                        pass
                    elif o_start == o_end:
                        new_opcodes.append(("insert", o_start, o_end, c_start, c_end))
                    elif c_start == c_end:
                        new_opcodes.append(("delete", o_start, o_end, c_start, c_end))
                    else:
                        if orig_words[o_start:o_end] == corr_words[c_start:c_end]:
                            new_opcodes.append(("equal", o_start, o_end, c_start, c_end))
                        else:
                            new_opcodes.append(("replace", o_start, o_end, c_start, c_end))
                elif idx < M:
                    if o_start != o_end:
                        new_opcodes.append(("delete", o_start, o_end, j2, j2))
                else:
                    if c_start != c_end:
                        new_opcodes.append(("insert", i2, i2, c_start, c_end))

                if idx < max_len - 1:
                    has_nl_o = idx < M - 1
                    has_nl_c = idx < N - 1
                    if has_nl_o and has_nl_c:
                        nl_o = orig_lines[idx][1]
                        nl_c = corr_lines[idx][1]
                        new_opcodes.append(("equal", nl_o, nl_o + 1, nl_c, nl_c + 1))
                    elif has_nl_o:
                        nl_o = orig_lines[idx][1]
                        new_opcodes.append(("delete", nl_o, nl_o + 1, j2, j2))
                    elif has_nl_c:
                        nl_c = corr_lines[idx][1]
                        new_opcodes.append(("insert", i2, i2, nl_c, nl_c + 1))
        return new_opcodes

    def _word_diff(
        self, corrected: str
    ) -> tuple[str, list[str], list[str], list[tuple[str, int, int, int, int]]]:
        nl_token = "\x00NL\x00"

        def prep(text: str) -> list[str]:
            normalized = text.replace("\r\n", "\n").replace("\r", "\n")
            normalized = normalized.replace("\n", f" {nl_token} ")
            return normalized.split()

        orig_words = prep(self.original)
        corr_words = prep(corrected)
        opcodes = difflib.SequenceMatcher(None, orig_words, corr_words).get_opcodes()
        split_opcodes = self._split_opcodes_by_nl(orig_words, corr_words, opcodes, nl_token)
        return nl_token, orig_words, corr_words, split_opcodes
