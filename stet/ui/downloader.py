import time
import tempfile
import hashlib
import zipfile
import tarfile
import urllib.request
import sys
import ctypes
if sys.platform == "win32":
    import ctypes.wintypes
from pathlib import Path

from PyQt6 import sip

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QWidget,
    QStyleFactory,
    QApplication,
)
from PyQt6.QtGui import QIcon, QCursor

try:
    from stet.ui.settings import THEME
except ImportError:
    THEME = ""


def format_bytes(b: int) -> str:
    """Format bytes into human-readable string."""
    if b >= 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024 * 1024):.2f} GB"
    elif b >= 1024 * 1024:
        return f"{b / (1024 * 1024):.2f} MB"
    elif b >= 1024:
        return f"{b / 1024:.2f} KB"
    else:
        return f"{b} B"


class DownloadWorker(QThread):
    """Worker thread that sequentially downloads a list of files, computes SHA-256 hash,
    reports progress, verifies hash, extracts zip files if requested, and performs cleanups.
    """

    progress = pyqtSignal(int, int, int, str)  # (bytes_downloaded, total_bytes, file_index, speed_str)
    finished = pyqtSignal(bool, str)          # (success, error_message)

    def __init__(self, downloads: list[dict], parent=None):
        super().__init__(parent)
        self.downloads = downloads
        self._is_cancelled = False

    def cancel(self):
        """Safely request cancellation of the worker."""
        self._is_cancelled = True

    def run(self):
        try:
            for idx, dl in enumerate(self.downloads):
                if self._is_cancelled:
                    self.finished.emit(False, "Download cancelled.")
                    return

                url = dl["url"]
                dest = Path(dl["dest"])
                expected_hash = dl.get("hash")
                label = dl.get("label", dest.name)
                extract_dir = dl.get("extract_dir")

                # Ensure target directory exists
                dest.parent.mkdir(parents=True, exist_ok=True)

                # Suffix for temp download file
                tmp_dest = dest.parent / (dest.name + ".tmp")

                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "Stet Downloader/1.0"})
                    with urllib.request.urlopen(req, timeout=30) as response:
                        content_length = response.getheader('Content-Length')
                        total_bytes = int(content_length) if content_length is not None else -1

                        bytes_downloaded = 0
                        sha256 = hashlib.sha256()
                        start_time = time.perf_counter()
                        last_emit_time = 0.0

                        with open(tmp_dest, "wb") as f:
                            while True:
                                if self._is_cancelled:
                                    break

                                # Read chunk (64 KB)
                                chunk = response.read(65536)
                                if not chunk:
                                    break

                                f.write(chunk)
                                if expected_hash:
                                    sha256.update(chunk)
                                bytes_downloaded += len(chunk)

                                now = time.perf_counter()
                                # Throttle progress emits to 100ms or on completion to prevent UI lag
                                if now - last_emit_time >= 0.1 or bytes_downloaded == total_bytes:
                                    elapsed = now - start_time
                                    speed = bytes_downloaded / elapsed if elapsed > 0 else 0

                                    # Format speed
                                    if speed >= 1024 * 1024:
                                        speed_str = f"{speed / (1024 * 1024):.2f} MB/s"
                                    elif speed >= 1024:
                                        speed_str = f"{speed / 1024:.2f} KB/s"
                                    else:
                                        speed_str = f"{speed:.2f} B/s"

                                    self.progress.emit(bytes_downloaded, total_bytes, idx, speed_str)
                                    last_emit_time = now

                        if self._is_cancelled:
                            self.finished.emit(False, "Download cancelled.")
                            return

                        # Emit final progress to update UI to 100% or final size
                        now = time.perf_counter()
                        elapsed = now - start_time
                        speed = bytes_downloaded / elapsed if elapsed > 0 else 0
                        if speed >= 1024 * 1024:
                            speed_str = f"{speed / (1024 * 1024):.2f} MB/s"
                        elif speed >= 1024:
                            speed_str = f"{speed / 1024:.2f} KB/s"
                        else:
                            speed_str = f"{speed:.2f} B/s"
                        self.progress.emit(bytes_downloaded, total_bytes, idx, speed_str)

                        # Verify SHA-256 hash if provided
                        if expected_hash:
                            calculated_hash = sha256.hexdigest().lower()
                            if calculated_hash != expected_hash.lower():
                                raise ValueError(
                                    f"Hash verification failed for {label}.\n"
                                    f"Expected: {expected_hash.lower()}\n"
                                    f"Got: {calculated_hash}"
                                )

                        # Safely replace existing destination file
                        if dest.exists():
                            dest.unlink()
                        tmp_dest.rename(dest)

                    # Extract ZIP file if extract_dir provided
                    if extract_dir is not None:
                        extract_path = Path(extract_dir)
                        extract_path.mkdir(parents=True, exist_ok=True)
                        root = extract_path.resolve()
                        if zipfile.is_zipfile(dest):
                            with zipfile.ZipFile(dest, "r") as archive:
                                members = archive.infolist()
                                for member in members:
                                    member_path = (extract_path / member.filename).resolve()
                                    if member_path != root and root not in member_path.parents:
                                        raise ValueError("Archive contains an unsafe extraction path")
                                for member in members:
                                    archive.extract(member, extract_path)
                                    mode = member.external_attr >> 16
                                    if mode & 0o111:
                                        try:
                                            (extract_path / member.filename).chmod(mode & 0o777)
                                        except OSError:
                                            pass
                        else:
                            with tarfile.open(dest, "r:*") as archive:
                                members = archive.getmembers()
                                for member in members:
                                    if member.issym() or member.islnk():
                                        raise ValueError("Archive contains unsafe symlinks or hardlinks")
                                    member_path = (extract_path / member.name).resolve()
                                    if (
                                        (member_path != root and root not in member_path.parents)
                                        or member.isdev()
                                    ):
                                        raise ValueError("Archive contains an unsafe extraction path")
                                for member in members:
                                    archive.extract(member, extract_path)
                                    if member.mode & 0o111:
                                        try:
                                            (extract_path / member.name).chmod(member.mode & 0o777)
                                        except OSError:
                                            pass
                        # Remove the leftover zip file after successful extraction
                        try:
                            if dest.exists():
                                dest.unlink()
                        except Exception:
                            pass

                finally:
                    # Clean up temporary download file if it still exists
                    if tmp_dest.exists():
                        try:
                            tmp_dest.unlink()
                        except Exception:
                            pass

            self.finished.emit(True, "")
        except Exception as e:
            self.finished.emit(False, str(e))


class DownloadProgressDialog(QDialog):
    """Frameless dark-navy themed dialog displaying download progress,
    providing options to cancel, handle failures, show errors, and retry.
    """

    def __init__(self, downloads: list[dict], parent=None):
        super().__init__(parent)
        self._downloads = downloads
        self._worker = None
        self._is_cancelled = False
        self._drag_pos = None

        self._setup_window()
        self._build_ui()
        self._load_style()
        self._start_download()

    def _setup_window(self):
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        from stet.ui.utils import set_window_icon
        set_window_icon(self)

        # Set Fusion style if available
        fusion = QStyleFactory.create("Fusion")
        if fusion:
            self.setStyle(fusion)

        # Set minimum width and center on screen
        self.setMinimumWidth(460)
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        sr = screen.availableGeometry() if screen else None
        if sr:
            geo = self.frameGeometry()
            geo.moveCenter(sr.center())
            self.move(geo.topLeft())
        self.setMouseTracking(True)
        self._ui_built = True

    def _build_ui(self):
        outer_lay = QVBoxLayout(self)
        outer_lay.setContentsMargins(0, 0, 0, 0)

        # Outer card container
        card = QWidget()
        card.setObjectName("downloadCard")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        card_lay.setSpacing(0)
        outer_lay.addWidget(card)

        # Header / Title Bar
        title_bar = QWidget()
        title_bar.setObjectName("downloadHeader")
        title_bar_lay = QHBoxLayout(title_bar)
        title_bar_lay.setContentsMargins(16, 12, 16, 12)
        title_bar_lay.setSpacing(0)

        title_lbl = QLabel("STET DOWNLOADER")
        title_lbl.setObjectName("downloadHeaderTitle")
        title_bar_lay.addWidget(title_lbl)
        title_bar_lay.addStretch()

        # Small Titlebar Close Button
        close_svg = Path(tempfile.gettempdir()) / "stet_close.svg"
        try:
            if not close_svg.exists():
                close_svg.write_text(
                    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                    '<line x1="1.5" y1="1.5" x2="8.5" y2="8.5" stroke="#ffffff" stroke-width="1" stroke-linecap="round"/>'
                    '<line x1="8.5" y1="1.5" x2="1.5" y2="8.5" stroke="#ffffff" stroke-width="1" stroke-linecap="round"/>'
                    '</svg>',
                    encoding="utf-8",
                )
        except Exception:
            pass

        self._title_close_btn = QPushButton()
        self._title_close_btn.setObjectName("windowCloseBtn")
        if close_svg.exists():
            self._title_close_btn.setIcon(QIcon(str(close_svg)))
            self._title_close_btn.setIconSize(QSize(10, 10))
        else:
            self._title_close_btn.setText("×")
        self._title_close_btn.setFixedSize(20, 20)
        self._title_close_btn.clicked.connect(self.reject)
        title_bar_lay.addWidget(self._title_close_btn)

        card_lay.addWidget(title_bar)

        # Body Content
        body = QWidget()
        body.setObjectName("downloadBody")
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(20, 20, 20, 20)
        body_lay.setSpacing(12)

        # File Label
        self._file_lbl = QLabel("Connecting...")
        self._file_lbl.setObjectName("file_label")
        self._file_lbl.setWordWrap(True)
        body_lay.addWidget(self._file_lbl)

        # Progress Bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setValue(0)
        body_lay.addWidget(self._progress_bar)

        # Stats Label
        self._stats_lbl = QLabel("Connecting...")
        self._stats_lbl.setObjectName("stats_label")
        body_lay.addWidget(self._stats_lbl)

        # Error Label (hidden by default)
        self._error_lbl = QLabel("")
        self._error_lbl.setObjectName("error_label")
        self._error_lbl.setWordWrap(True)
        self._error_lbl.hide()
        body_lay.addWidget(self._error_lbl)

        body_lay.addStretch()

        # Buttons Area
        btn_lay = QHBoxLayout()
        btn_lay.setContentsMargins(0, 0, 0, 0)
        btn_lay.setSpacing(10)
        btn_lay.addStretch()

        self._retry_btn = QPushButton("Retry")
        self._retry_btn.setObjectName("retry_btn")
        self._retry_btn.clicked.connect(self._start_download)
        self._retry_btn.hide()
        btn_lay.addWidget(self._retry_btn)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setObjectName("cancel_btn")
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        btn_lay.addWidget(self._cancel_btn)

        body_lay.addLayout(btn_lay)
        card_lay.addWidget(body)

    def _load_style(self):
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
        qss_base = THEME.replace("{checkmark_url}", p) if THEME else ""

        custom_qss = """
            QWidget#downloadCard {
                background-color: #121315;
                border: 1px solid #28292c;
            }
            QWidget#downloadHeader {
                background-color: #090a0b;
                border-bottom: 1px solid #28292c;
            }
            QLabel#downloadHeaderTitle {
                color: #ededee;
                font-family: 'IBM Plex Mono', 'Consolas', monospace;
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 1px;
            }
            QWidget#downloadBody {
                background-color: #121315;
            }
            QLabel {
                color: #ededee;
                font-family: 'IBM Plex Mono', 'Consolas', monospace;
            }
            QLabel#file_label {
                font-size: 12px;
                font-weight: bold;
                color: #d4a373;
            }
            QLabel#stats_label {
                color: #94a3b8;
                font-size: 11px;
            }
            QLabel#error_label {
                color: #f87171;
                font-size: 11px;
            }
            QProgressBar {
                border: 1px solid #28292c;
                border-radius: 0px;
                background-color: #090a0b;
                text-align: center;
                color: #ededee;
                font-family: 'IBM Plex Mono', 'Consolas', monospace;
                font-size: 11px;
                height: 18px;
            }
            QProgressBar::chunk {
                background-color: #3b82f6;
            }
            QPushButton {
                background-color: transparent;
                border: 1px solid #28292c;
                border-radius: 0px;
                padding: 6px 16px;
                color: #ededee;
                font-family: 'IBM Plex Mono', 'Consolas', monospace;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #1c1d1f;
                border-color: #d4a373;
            }
            QPushButton:pressed {
                background-color: #121315;
            }
            QPushButton#windowCloseBtn {
                background: transparent;
                border: none;
                border-radius: 2px;
            }
            QPushButton#windowCloseBtn:hover {
                background-color: #ef4444;
            }
            QPushButton#windowCloseBtn:pressed {
                background-color: #b91c1c;
            }
        """
        self.setStyleSheet(qss_base + "\n" + custom_qss)

    def _start_download(self):
        self._is_cancelled = False
        self._error_lbl.hide()
        self._error_lbl.setText("")
        self._stats_lbl.setText("Starting download...")
        self._file_lbl.setText("Connecting...")
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setValue(0)
        self._cancel_btn.setText("Cancel")
        self._cancel_btn.setEnabled(True)
        self._retry_btn.hide()

        self._worker = DownloadWorker(self._downloads, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_progress(self, bytes_downloaded, total_bytes, file_index, speed_str):
        if self._is_cancelled:
            return

        num_files = len(self._downloads)
        current_download = self._downloads[file_index]
        label_text = current_download.get("label", Path(current_download["dest"]).name)
        self._file_lbl.setText(f"Downloading ({file_index + 1}/{num_files}): {label_text}")

        if total_bytes > 0:
            self._progress_bar.setRange(0, total_bytes)
            self._progress_bar.setValue(bytes_downloaded)
            remaining_bytes = total_bytes - bytes_downloaded
            remaining_str = format_bytes(remaining_bytes)
            total_str = format_bytes(total_bytes)
            self._stats_lbl.setText(f"Speed: {speed_str} | Remaining: {remaining_str} of {total_str}")
        else:
            self._progress_bar.setRange(0, 0)
            self._stats_lbl.setText(f"Speed: {speed_str} | Downloaded: {format_bytes(bytes_downloaded)}")

    def _on_finished(self, success, error_message):
        self._cleanup_partial_files()

        if self._is_cancelled:
            if self.isVisible():
                super().reject()
            return

        if success:
            self.accept()
        else:
            self._error_lbl.setText(f"Error: {error_message}")
            self._error_lbl.show()
            self._stats_lbl.setText("Download failed.")
            self._cancel_btn.setText("Close")
            self._cancel_btn.setEnabled(True)
            self._retry_btn.show()

    def _on_cancel_clicked(self):
        if self._is_cancelled or not (self._worker and self._worker.isRunning()):
            self.reject()
            return

        self._is_cancelled = True
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setText("Cancelling...")
        self._stats_lbl.setText("Cancellation requested...")
        self._worker.cancel()

    def _cleanup_partial_files(self):
        for dl in self._downloads:
            dest = Path(dl["dest"])
            tmp_dest = dest.parent / (dest.name + ".tmp")
            if tmp_dest.exists():
                try:
                    tmp_dest.unlink()
                except Exception:
                    pass

    def reject(self):
        self._is_cancelled = True
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(1000)
        self._cleanup_partial_files()
        super().reject()

    def nativeEvent(self, eventType, message):
        try:
            if sip.isdeleted(self) or not getattr(self, "_ui_built", False):
                return False, 0
            if sys.platform == "win32" and (eventType == b"windows_generic_MSG" or eventType == b"windows_dispatcher_MSG"):
                msg_ptr = int(message)
                if not msg_ptr:
                    return False, 0
                msg = ctypes.wintypes.MSG.from_address(msg_ptr)
                if msg.message == 0x0084:  # WM_NCHITTEST
                    pos = self.mapFromGlobal(QCursor.pos())
                    w, h = self.width(), self.height()
                    x, y = pos.x(), pos.y()
                    b = 6  # 6px unconditional outer border margin

                    # Unconditional Border Hit Tests (when not maximized/fullscreen)
                    if not (self.isMaximized() or self.isFullScreen()):
                        if x < b and y < b:
                            return True, 13  # HTTOPLEFT
                        if x >= w - b and y < b:
                            return True, 14  # HTTOPRIGHT
                        if x < b and y >= h - b:
                            return True, 16  # HTBOTTOMLEFT
                        if x >= w - b and y >= h - b:
                            return True, 17  # HTBOTTOMRIGHT
                        if x < b:
                            return True, 10  # HTLEFT
                        if x >= w - b:
                            return True, 11  # HTRIGHT
                        if y < b:
                            return True, 12  # HTTOP
                        if y >= h - b:
                            return True, 15  # HTBOTTOM

                    # Title bar Area (top 48px)
                    if y < 48 and not (self.isMaximized() or self.isFullScreen()):
                        child = self.childAt(pos)
                        is_interactive = False
                        curr = child
                        while curr is not None and curr is not self:
                            try:
                                if sip.isdeleted(curr):
                                    break
                                if isinstance(curr, (QPushButton, QProgressBar)):
                                    is_interactive = True
                                    break
                                curr = curr.parentWidget()
                            except Exception:
                                break
                        if not is_interactive:
                            return True, 2  # HTCAPTION
        except Exception:
            pass
        return False, 0

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            if pos.y() < 48 and sys.platform != "win32":
                ch = self.childAt(pos)
                is_interactive = False
                curr = ch
                while curr is not None and curr is not self:
                    try:
                        if sip.isdeleted(curr):
                            break
                        if isinstance(curr, (QPushButton, QProgressBar)):
                            is_interactive = True
                            break
                        curr = curr.parentWidget()
                    except Exception:
                        break
                if not is_interactive:
                    wh = self.windowHandle()
                    if wh and hasattr(wh, "startSystemMove"):
                        wh.startSystemMove()
                        return

            self._drag_pos = event.globalPosition().toPoint() - self.pos()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if hasattr(self, "_drag_pos") and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)
