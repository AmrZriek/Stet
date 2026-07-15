r"""
windows_installer_payload.py — Stet standalone Windows installer (QWizard)
===========================================================================
Compiles with Nuitka into StetSetup.exe.  Bundles stet_portable.zip
internally as a data file using Nuitka's ``--include-data-file`` flag.

Distribution: StetSetup.exe is completely self-contained.  Simply upload
it directly.  When run, it presents a standard Windows-style installation
wizard (QWizard) and unpacks the bundled ZIP to the user's chosen directory.

Wizard page flow:
  Welcome → License Agreement → Destination Folder →
  Ready to Install → Installing (progress) → Installation Complete
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import stat
import subprocess
import sys
import winreg
import zipfile
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QIcon, QPalette, QColor, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
    QCheckBox,
    QFrame,
    QButtonGroup,
)

# ── Constants ─────────────────────────────────────────────────────────────────

ZIP_NAME = "stet_portable.zip"
DEFAULT_INSTALL_DIR = os.path.expandvars(r"%LOCALAPPDATA%\Stet")
CREATE_NO_WINDOW = 0x08000000

# SHBrowseForFolder flags
BIF_RETURNONLYFSDIRS = 0x00000001
BIF_NEWDIALOGSTYLE = 0x00000040
BIF_EDITBOX = 0x00000010
BIF_USENEWUI = BIF_NEWDIALOGSTYLE | BIF_EDITBOX

# Page IDs
PAGE_WELCOME = 0
PAGE_LICENSE = 1
PAGE_DESTINATION = 2
PAGE_READY = 3
PAGE_PROGRESS = 4
PAGE_COMPLETION = 5

# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    try:
        print(f"[Installer] {msg}")
    except Exception:
        pass


# ── Zip / install helpers (unchanged from original) ───────────────────────────

def _find_zip_path() -> Path | None:
    """Locate the portable zip bundled inside the installer exe.

    Search order:
      1. Nuitka onefile temp folder (if zip was embedded)
      2. Same directory as the installer exe
      3. Current working directory
    For each directory, try the canonical name first, then any Stet*.zip.
    """
    candidate_dirs: list[Path] = []
    onefile_temp = os.environ.get("_NUITKA_ONEFILE_TEMP")
    if onefile_temp:
        candidate_dirs.append(Path(onefile_temp))
    try:
        candidate_dirs.append(Path(sys.argv[0]).resolve().parent)
    except Exception:
        pass
    candidate_dirs.append(Path.cwd())

    for d in candidate_dirs:
        p = d / ZIP_NAME
        if p.exists():
            return p
        matches = sorted(
            d.glob("Stet*.zip"), key=lambda m: m.stat().st_mtime, reverse=True
        )
        if matches:
            return matches[0]

    return None


def _safe_extract(
    zip_ref: zipfile.ZipFile, dest: Path, members: list | None = None
) -> None:
    """Extract ZIP with path-traversal and symlink validation.

    Defense-in-depth: resolves every member path and rejects anything that
    escapes the destination directory.  Also rejects symlink members.

    Args:
        zip_ref: Open ZipFile object.
        dest: Destination directory.
        members: Optional list of ZipInfo objects to extract.  If None,
                 all members are extracted.  All members in the archive are
                 validated regardless of this parameter.
    """
    dest = dest.resolve()
    for member in zip_ref.infolist():
        mode = (member.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise RuntimeError(f"Refusing symlink in ZIP: {member.filename}")
        target = (dest / member.filename).resolve()
        if dest not in target.parents:
            raise RuntimeError(f"Unsafe path in ZIP: {member.filename}")
    zip_ref.extractall(dest, members=members)


def create_shortcut(
    shortcut_path: Path, target_path: Path, working_dir: Path, icon_path: Path
) -> None:
    """Create a Windows .lnk shortcut via PowerShell COM."""
    s_path = str(shortcut_path).replace("'", "''")
    t_path = str(target_path).replace("'", "''")
    w_dir = str(working_dir).replace("'", "''")
    i_path = str(icon_path).replace("'", "''")

    ps_cmd = (
        f"$WshShell = New-Object -ComObject WScript.Shell; "
        f"$Shortcut = $WshShell.CreateShortcut('{s_path}'); "
        f"$Shortcut.TargetPath = '{t_path}'; "
        f"$Shortcut.WorkingDirectory = '{w_dir}'; "
        f"$Shortcut.IconLocation = '{i_path}'; "
        f"$Shortcut.Save()"
    )
    subprocess.run(
        ["powershell", "-Command", ps_cmd],
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
    )




# ── Background installation worker ────────────────────────────────────────────

class InstallWorker(QThread):
    """Runs ZIP extraction in a background thread to keep the UI responsive.

    Signals:
        progress(int, str): current step index (0-based) and status message.
        total_steps(int): emitted once at start with the total number of files.
        finished(): emitted when installation completes successfully.
        error(str): emitted if a fatal error occurs; installation is aborted.
    """

    progress = pyqtSignal(int, str)
    total_steps = pyqtSignal(int)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, zip_path: Path, target_dir: Path) -> None:
        super().__init__()
        self.zip_path = zip_path
        self.target_dir = target_dir

    def run(self) -> None:
        try:
            with zipfile.ZipFile(self.zip_path, "r") as zf:
                # Validate entire archive first (security check)
                dest = self.target_dir.resolve()
                for member in zf.infolist():
                    mode = (member.external_attr >> 16) & 0xFFFF
                    if stat.S_ISLNK(mode):
                        self.error.emit(
                            f"Installation aborted: the installation package contains "
                            f"an invalid file type (symlink):\n{member.filename}"
                        )
                        return
                    target = (dest / member.filename).resolve()
                    if dest not in target.parents:
                        self.error.emit(
                            f"Installation aborted: the installation package contains "
                            f"an unsafe file path:\n{member.filename}"
                        )
                        return

                # Build filtered member list (preserve existing user config)
                existing_config = (self.target_dir / "config.json").exists()
                members = []
                for member in zf.infolist():
                    if (
                        existing_config
                        and Path(member.filename).name.lower() == "config.json"
                    ):
                        log(f"  Preserving existing configuration: {member.filename}")
                        continue
                    members.append(member)

                self.total_steps.emit(len(members))
                self.target_dir.mkdir(parents=True, exist_ok=True)

                for i, member in enumerate(members):
                    self.progress.emit(i, f"Extracting: {member.filename}")
                    zf.extract(member, self.target_dir)

                self.progress.emit(len(members), "Finalizing installation...")

        except zipfile.BadZipFile:
            self.error.emit(
                f"{ZIP_NAME} is corrupted or is not a valid ZIP file.\n\n"
                "Please re-download the installer and try again."
            )
            return
        except Exception as exc:
            self.error.emit(f"An unexpected error occurred during installation:\n\n{exc}")
            return

        self.finished.emit()


# ── Wizard pages ──────────────────────────────────────────────────────────────

class WelcomePage(QWizardPage):
    """Page 0 — Welcome screen."""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Welcome to Stet Setup")
        self.setSubTitle(
            "This wizard will guide you through the installation of Stet."
        )

        layout = QVBoxLayout()
        layout.setSpacing(14)

        intro = QLabel(
            "<p>Stet is an AI-powered writing assistant that corrects your spelling "
            "and grammar on demand, working directly inside any application.</p>"
            "<p>The installer will:</p>"
            "<ul>"
            "<li>Copy the application files to your chosen directory</li>"
            "<li>Create shortcuts on your Desktop and Start Menu</li>"
            "<li>Optionally help you download the required AI language model</li>"
            "</ul>"
            "<p>Click <b>Next</b> to continue, or <b>Cancel</b> to exit Setup.</p>"
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(intro)
        layout.addStretch()
        self.setLayout(layout)

    def nextId(self) -> int:
        return PAGE_LICENSE


class LicensePage(QWizardPage):
    """Page 1 — License Agreement (GPL-3.0)."""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle("License Agreement")
        self.setSubTitle(
            "Please review the license terms before installing Stet."
        )

        layout = QVBoxLayout()
        layout.setSpacing(10)

        # License text area
        self._license_edit = QTextEdit()
        self._license_edit.setReadOnly(True)
        self._license_edit.setFont(QFont("Consolas", 8))
        self._license_edit.setMinimumHeight(180)
        self._license_edit.setPlainText(_load_license_text())
        layout.addWidget(self._license_edit)

        # Separator
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        accept_label = QLabel(
            "Do you accept the terms of the license agreement? "
            "You must accept to install Stet."
        )
        accept_label.setWordWrap(True)
        layout.addWidget(accept_label)

        # Accept / decline radio buttons
        self._accept_radio = QRadioButton(
            "I &accept the terms of the License Agreement"
        )
        self._decline_radio = QRadioButton(
            "I &do not accept the terms of the License Agreement"
        )
        self._decline_radio.setChecked(True)

        self._button_group = QButtonGroup(self)
        self._button_group.addButton(self._accept_radio)
        self._button_group.addButton(self._decline_radio)

        layout.addWidget(self._accept_radio)
        layout.addWidget(self._decline_radio)

        self.setLayout(layout)

        # Register field so wizard can query acceptance state
        self.registerField("licenseAccepted", self._accept_radio)
        self._accept_radio.toggled.connect(self.completeChanged)

    def isComplete(self) -> bool:
        return self._accept_radio.isChecked()

    def nextId(self) -> int:
        return PAGE_DESTINATION


class DestinationPage(QWizardPage):
    """Page 2 — Destination folder selection."""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Destination Folder")
        self.setSubTitle(
            "Choose the folder where Stet will be installed."
        )

        layout = QVBoxLayout()
        layout.setSpacing(10)

        install_label = QLabel("Stet will be installed to the following folder.")
        install_label.setWordWrap(True)
        layout.addWidget(install_label)

        # Path row
        path_row = QHBoxLayout()
        self._path_edit = QLineEdit(DEFAULT_INSTALL_DIR)
        self._path_edit.setPlaceholderText("Installation directory...")
        path_row.addWidget(self._path_edit)
        browse_btn = QPushButton("&Browse...")
        browse_btn.setFixedWidth(90)
        browse_btn.clicked.connect(self._browse)
        path_row.addWidget(browse_btn)
        layout.addLayout(path_row)

        # Info label
        self._info_label = QLabel("")
        self._info_label.setWordWrap(True)
        self._info_label.setStyleSheet("color: #888;")
        layout.addWidget(self._info_label)

        layout.addStretch()

        change_label = QLabel(
            "To install to a different folder, click Browse and select another folder. "
            "Click Next to continue."
        )
        change_label.setWordWrap(True)
        layout.addWidget(change_label)

        self.setLayout(layout)

        self.registerField("installDir*", self._path_edit)
        self._path_edit.textChanged.connect(self._on_path_changed)
        self._on_path_changed(self._path_edit.text())

    def _browse(self) -> None:
        """Open folder browser and update the path field."""
        current = self._path_edit.text().strip()
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Choose installation directory",
            current or str(Path.home()),
        )
        if chosen:
            self._path_edit.setText(str(Path(chosen) / "Stet"))

    def _on_path_changed(self, text: str) -> None:
        path = text.strip()
        p = Path(path) if path else None
        if p and p.exists() and (p / "Stet.exe").exists():
            self._info_label.setText(
                "⚠  An existing Stet installation was detected. "
                "Your configuration will be preserved."
            )
            self._info_label.setStyleSheet("color: #d4a373;")
        else:
            self._info_label.setText("")
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return bool(self._path_edit.text().strip())

    def nextId(self) -> int:
        return PAGE_READY


class ReadyPage(QWizardPage):
    """Page 3 — Ready to Install (commit page).

    After this page, Back is automatically disabled on the next page.
    The Next button is relabelled to "Install".
    """

    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Ready to Install")
        self.setSubTitle(
            "The wizard is ready to install Stet on your computer."
        )
        self.setCommitPage(True)
        self.setButtonText(QWizard.WizardButton.CommitButton, "&Install")

        layout = QVBoxLayout()
        layout.setSpacing(10)

        preamble = QLabel(
            "Click <b>Install</b> to begin the installation.\n\n"
            "If you want to review or change any of your settings, "
            "click <b>Back</b>."
        )
        preamble.setWordWrap(True)
        layout.addWidget(preamble)

        # Summary box
        summary_frame = QFrame()
        summary_frame.setFrameShape(QFrame.Shape.StyledPanel)
        summary_frame.setFrameShadow(QFrame.Shadow.Sunken)
        summary_layout = QVBoxLayout(summary_frame)
        summary_layout.setContentsMargins(12, 8, 12, 8)

        summary_title = QLabel("<b>Installation summary</b>")
        summary_layout.addWidget(summary_title)

        self._dir_label = QLabel()
        self._dir_label.setWordWrap(True)
        summary_layout.addWidget(self._dir_label)

        self._reinstall_label = QLabel()
        self._reinstall_label.setWordWrap(True)
        self._reinstall_label.setStyleSheet("color: #d4a373;")
        self._reinstall_label.hide()
        summary_layout.addWidget(self._reinstall_label)

        layout.addWidget(summary_frame)
        layout.addStretch()
        self.setLayout(layout)

    def initializePage(self) -> None:
        install_dir = self.field("installDir")
        self._dir_label.setText(f"Destination folder:  {install_dir}")
        p = Path(install_dir)
        if p.exists() and (p / "Stet.exe").exists():
            self._reinstall_label.setText(
                "⚠  Existing installation detected — files will be updated "
                "and your configuration will be preserved."
            )
            self._reinstall_label.show()
        else:
            self._reinstall_label.hide()

    def nextId(self) -> int:
        return PAGE_PROGRESS


class ProgressPage(QWizardPage):
    """Page 4 — Installation progress.

    Runs InstallWorker in a background thread and tracks progress.
    Navigation (Back/Next) is blocked until installation completes or fails.
    """

    def __init__(self, zip_path: Path) -> None:
        super().__init__()
        self.setTitle("Installing")
        self.setSubTitle("Please wait while Stet is being installed.")
        self._zip_path = zip_path
        self._install_done = False
        self._install_failed = False
        self._worker: InstallWorker | None = None

        layout = QVBoxLayout()
        layout.setSpacing(12)

        self._status_label = QLabel("Preparing installation...")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setMinimum(0)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        layout.addWidget(self._progress_bar)

        self._detail_label = QLabel("")
        self._detail_label.setWordWrap(True)
        self._detail_label.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(self._detail_label)

        layout.addStretch()
        self.setLayout(layout)

    def initializePage(self) -> None:
        install_dir = Path(self.field("installDir"))
        log(f"Installing to: {install_dir}")
        self._install_done = False
        self._install_failed = False
        self._status_label.setText("Starting installation...")
        self._progress_bar.setValue(0)
        self._detail_label.setText("")

        # Disable wizard buttons while installing
        self.wizard().button(QWizard.WizardButton.BackButton).setEnabled(False)
        self.wizard().button(QWizard.WizardButton.NextButton).setEnabled(False)
        self.wizard().button(QWizard.WizardButton.CancelButton).setEnabled(True)

        self._worker = InstallWorker(self._zip_path, install_dir)
        self._worker.total_steps.connect(self._on_total_steps)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_total_steps(self, total: int) -> None:
        self._progress_bar.setMaximum(total)
        self._status_label.setText("Extracting files...")

    def _on_progress(self, step: int, message: str) -> None:
        self._progress_bar.setValue(step)
        self._detail_label.setText(message)

    def _on_finished(self) -> None:
        self._install_done = True
        self._status_label.setText("Installation complete.")
        self._detail_label.setText("")
        self._progress_bar.setValue(self._progress_bar.maximum())
        self.wizard().button(QWizard.WizardButton.CancelButton).setEnabled(False)
        self.completeChanged.emit()
        # Auto-advance to completion page
        self.wizard().next()

    def _on_error(self, message: str) -> None:
        self._install_failed = True
        self._status_label.setText("Installation failed.")
        self._detail_label.setText("")
        self.wizard().button(QWizard.WizardButton.BackButton).setEnabled(True)
        self.wizard().button(QWizard.WizardButton.CancelButton).setEnabled(True)
        QMessageBox.critical(
            self,
            "Stet Setup — Installation Error",
            message,
        )

    def isComplete(self) -> bool:
        return self._install_done

    def nextId(self) -> int:
        return PAGE_COMPLETION


class CompletionPage(QWizardPage):
    """Page 5 — Installation complete."""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Installation Complete")
        self.setSubTitle(
            "Stet has been successfully installed on your computer."
        )
        self.setFinalPage(True)

        layout = QVBoxLayout()
        layout.setSpacing(10)

        success_label = QLabel(
            "<p>Setup has finished installing Stet.</p>"
            "<p>Choose the options below, then click <b>Finish</b> to complete Setup.</p>"
        )
        success_label.setWordWrap(True)
        success_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(success_label)

        # Options frame
        options_frame = QFrame()
        options_frame.setFrameShape(QFrame.Shape.StyledPanel)
        options_frame.setFrameShadow(QFrame.Shadow.Sunken)
        options_layout = QVBoxLayout(options_frame)
        options_layout.setContentsMargins(12, 8, 12, 8)
        options_layout.setSpacing(6)

        self._desktop_cb = QCheckBox("Create a &Desktop shortcut")
        self._desktop_cb.setChecked(True)
        options_layout.addWidget(self._desktop_cb)

        self._startmenu_cb = QCheckBox("Create a &Start Menu shortcut")
        self._startmenu_cb.setChecked(True)
        options_layout.addWidget(self._startmenu_cb)

        options_layout.addSpacing(4)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        options_layout.addWidget(sep)
        options_layout.addSpacing(4)

        self._download_backend_cb = QCheckBox(
            "&Download llama.cpp Backend & CUDA Runtime (~652 MB)\n"
            "Required for local model execution."
        )
        self._download_backend_cb.setChecked(True)
        options_layout.addWidget(self._download_backend_cb)

        options_layout.addSpacing(4)
        sep_dl = QFrame()
        sep_dl.setFrameShape(QFrame.Shape.HLine)
        sep_dl.setFrameShadow(QFrame.Shadow.Sunken)
        options_layout.addWidget(sep_dl)
        options_layout.addSpacing(4)

        self._download_model_cb = QCheckBox(
            "&Download the recommended AI model (~1.8 GB)\n"
            "Required for grammar and spelling correction."
        )
        self._download_model_cb.setChecked(True)
        options_layout.addWidget(self._download_model_cb)

        options_layout.addSpacing(4)
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        options_layout.addWidget(sep2)
        options_layout.addSpacing(4)

        self._launch_cb = QCheckBox("&Launch Stet")
        self._launch_cb.setChecked(True)
        options_layout.addWidget(self._launch_cb)

        layout.addWidget(options_frame)
        layout.addStretch()
        self.setLayout(layout)

    def nextId(self) -> int:
        return -1  # Final page

    @property
    def create_desktop_shortcut(self) -> bool:
        return self._desktop_cb.isChecked()

    @property
    def create_startmenu_shortcut(self) -> bool:
        return self._startmenu_cb.isChecked()

    @property
    def download_backend(self) -> bool:
        return self._download_backend_cb.isChecked()

    @property
    def download_model(self) -> bool:
        return self._download_model_cb.isChecked()

    @property
    def launch_stet(self) -> bool:
        return self._launch_cb.isChecked()


# ── Wizard shell ──────────────────────────────────────────────────────────────

class StetInstaller(QWizard):
    """Main installer wizard window."""

    def __init__(self, zip_path: Path) -> None:
        super().__init__()
        self._zip_path = zip_path
        self._install_completed = False

        self.setWindowTitle("Stet Setup")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setFixedSize(600, 460)

        # Window icon
        _set_window_icon(self)

        # Logo pixmap (shown on the right side of each page in ModernStyle)
        logo_pix = _load_logo_pixmap()
        if logo_pix:
            self.setPixmap(QWizard.WizardPixmap.LogoPixmap, logo_pix)

        # Options
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setOption(QWizard.WizardOption.NoBackButtonOnLastPage, True)
        self.setOption(QWizard.WizardOption.NoCancelButtonOnLastPage, True)
        self.setOption(QWizard.WizardOption.HaveHelpButton, False)

        # Pages
        self._progress_page = ProgressPage(zip_path)
        self._completion_page = CompletionPage()

        self.setPage(PAGE_WELCOME, WelcomePage())
        self.setPage(PAGE_LICENSE, LicensePage())
        self.setPage(PAGE_DESTINATION, DestinationPage())
        self.setPage(PAGE_READY, ReadyPage())
        self.setPage(PAGE_PROGRESS, self._progress_page)
        self.setPage(PAGE_COMPLETION, self._completion_page)

        self.setStartId(PAGE_WELCOME)

        # Button layout — standard Windows order
        self.setButtonLayout([
            QWizard.WizardButton.Stretch,
            QWizard.WizardButton.BackButton,
            QWizard.WizardButton.NextButton,
            QWizard.WizardButton.CommitButton,
            QWizard.WizardButton.FinishButton,
            QWizard.WizardButton.CancelButton,
        ])

    def reject(self) -> None:
        """Override Cancel to show confirmation dialog (except on completion page)."""
        if self.currentId() == PAGE_COMPLETION:
            super().reject()
            return

        # If installation is in progress, ask before aborting
        reply = QMessageBox.question(
            self,
            "Cancel Setup",
            "Are you sure you want to cancel Stet Setup?\n\n"
            "Stet will not be installed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            # If worker is running, wait for it to stop
            worker = self._progress_page._worker
            if worker and worker.isRunning():
                worker.terminate()
                worker.wait(3000)
            super().reject()

    def accept(self) -> None:
        """Run post-install actions (shortcuts, downloads) before closing the wizard."""
        if self.currentId() == PAGE_COMPLETION:
            self._run_post_install_actions()
        super().accept()

    def _write_arp_registry(self, install_dir: Path) -> None:
        """Write Add/Remove Programs registry entry for Stet."""
        version_file = install_dir / "VERSION"
        version = "1.0.0"
        if version_file.exists():
            version = version_file.read_text(encoding="utf-8").strip()

        estimated_size = 0
        for f in install_dir.rglob("*"):
            if f.is_file():
                estimated_size += f.stat().st_size
        estimated_size //= 1024

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Stet"
        try:
            key = winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE
            )
            values = [
                ("DisplayName", winreg.REG_SZ, "Stet"),
                ("DisplayVersion", winreg.REG_SZ, version),
                ("Publisher", winreg.REG_SZ, "Stet"),
                ("InstallLocation", winreg.REG_SZ, str(install_dir)),
                ("UninstallString", winreg.REG_SZ, str(install_dir / "StetUninstall.exe")),
                ("QuietUninstallString", winreg.REG_SZ,
                 f'"{install_dir / "StetUninstall.exe"}" --silent'),
                ("DisplayIcon", winreg.REG_SZ, f"{install_dir / 'Stet.exe'},0"),
                ("EstimatedSize", winreg.REG_DWORD, estimated_size),
                ("NoModify", winreg.REG_DWORD, 1),
                ("NoRepair", winreg.REG_DWORD, 1),
                ("InstallDate", winreg.REG_SZ, datetime.now().strftime("%Y%m%d")),
            ]
            for name, reg_type, data in values:
                winreg.SetValueEx(key, name, 0, reg_type, data)
            winreg.CloseKey(key)
            log("Registered in Add/Remove Programs")
        except Exception as exc:
            log(f"WARNING: Failed to write ARP registry: {exc}")

    def _run_post_install_actions(self) -> None:
        """Create shortcuts, write ARP registry, optionally launch model downloader and Stet."""
        install_dir = Path(self.field("installDir"))
        target_exe = install_dir / "Stet.exe"
        icon_path = install_dir / "logo.ico"

        if self._completion_page.create_desktop_shortcut:
            desktop_lnk = Path(
                os.path.expandvars(r"%USERPROFILE%\Desktop\Stet.lnk")
            )
            log("Creating Desktop shortcut...")
            try:
                create_shortcut(desktop_lnk, target_exe, install_dir, icon_path)
            except Exception as exc:
                log(f"Desktop shortcut failed: {exc}")

        if self._completion_page.create_startmenu_shortcut:
            startmenu_lnk = Path(
                os.path.expandvars(
                    r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Stet.lnk"
                )
            )
            log("Creating Start Menu shortcut...")
            try:
                create_shortcut(startmenu_lnk, target_exe, install_dir, icon_path)
            except Exception as exc:
                log(f"Start Menu shortcut failed: {exc}")

        self._write_arp_registry(install_dir)

        downloads = []
        if self._completion_page.download_backend:
            from stet.constants import LLAMA_BACKEND_URLS, LLAMA_BACKEND_HASHES, LLAMA_BACKEND_DIR
            downloads.append({
                "url": LLAMA_BACKEND_URLS["llama"],
                "dest": install_dir / "llama_zip.zip",
                "hash": LLAMA_BACKEND_HASHES["llama"],
                "label": "llama.cpp server binary",
                "extract_dir": install_dir / LLAMA_BACKEND_DIR
            })
            downloads.append({
                "url": LLAMA_BACKEND_URLS["cuda"],
                "dest": install_dir / "cuda_zip.zip",
                "hash": LLAMA_BACKEND_HASHES["cuda"],
                "label": "CUDA runtime dependencies",
                "extract_dir": install_dir / LLAMA_BACKEND_DIR
            })

        if self._completion_page.download_model:
            from stet.constants import RECOMMENDED_MODEL_URL, RECOMMENDED_MODEL_FILE, RECOMMENDED_MODEL_HASH
            downloads.append({
                "url": RECOMMENDED_MODEL_URL,
                "dest": install_dir / RECOMMENDED_MODEL_FILE,
                "hash": RECOMMENDED_MODEL_HASH,
                "label": "AI language model"
            })

        if downloads:
            log("Running native download progress dialog...")
            try:
                from stet.ui.downloader import DownloadProgressDialog
                dialog = DownloadProgressDialog(downloads, parent=self)
                dialog.exec()

                if self._completion_page.download_backend:
                    # Update config.json with correct server path
                    config_path = install_dir / "config.json"
                    if config_path.exists():
                        import json
                        with open(config_path, "r", encoding="utf-8") as f:
                            cfg_data = json.load(f)
                        from stet.constants import LLAMA_BACKEND_DIR, SERVER_EXE
                        cfg_data["llama_server_path"] = str(install_dir / LLAMA_BACKEND_DIR / SERVER_EXE)
                        with open(config_path, "w", encoding="utf-8") as f:
                            json.dump(cfg_data, f, indent=2)
            except Exception as exc:
                log(f"Native download/config update failed: {exc}")

        if self._completion_page.launch_stet and target_exe.exists():
            log("Launching Stet...")
            try:
                subprocess.Popen([str(target_exe)], cwd=str(install_dir))
            except Exception as exc:
                log(f"Launch failed: {exc}")




# ── Styling / icon helpers ─────────────────────────────────────────────────────

def _set_window_icon(widget: QWidget) -> None:
    """Set the window icon from logo.ico / logo.png next to the exe."""
    for name in ("logo.ico", "logo.png"):
        for directory in _asset_search_dirs():
            candidate = directory / name
            if candidate.exists():
                widget.setWindowIcon(QIcon(str(candidate)))
                return


def _load_logo_pixmap() -> QPixmap | None:
    """Load logo.png as a QPixmap for the wizard page sidebar."""
    for directory in _asset_search_dirs():
        candidate = directory / "logo.png"
        if candidate.exists():
            pix = QPixmap(str(candidate))
            if not pix.isNull():
                return pix
    return None


def _asset_search_dirs() -> list[Path]:
    dirs: list[Path] = []
    onefile_temp = os.environ.get("_NUITKA_ONEFILE_TEMP")
    if onefile_temp:
        dirs.append(Path(onefile_temp))
    try:
        dirs.append(Path(sys.argv[0]).resolve().parent)
    except Exception:
        pass
    dirs.append(Path.cwd())
    return dirs


def _load_license_text() -> str:
    """Load the LICENSE file from next to the installer exe, or return inline text."""
    for directory in _asset_search_dirs():
        candidate = directory / "LICENSE"
        if candidate.exists():
            try:
                return candidate.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
    # Fallback: short summary
    return (
        "GNU General Public License v3.0\n\n"
        "Stet is free software: you can redistribute it and/or modify it under "
        "the terms of the GNU General Public License as published by the Free "
        "Software Foundation, either version 3 of the License, or (at your "
        "option) any later version.\n\n"
        "This program is distributed in the hope that it will be useful, but "
        "WITHOUT ANY WARRANTY; without even the implied warranty of "
        "MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU "
        "General Public License for more details.\n\n"
        "You should have received a copy of the GNU General Public License "
        "along with this program. If not, see <https://www.gnu.org/licenses/>.\n\n"
        "Source code: https://github.com/AmrZriek/Stet"
    )


def _apply_stylesheet(app: QApplication) -> None:
    """Apply a clean, professional dark stylesheet and palette to the installer."""
    palette = QPalette()
    dark_color = QColor("#121315")
    base_color = QColor("#090a0b")
    text_color = QColor("#ededee")
    
    palette.setColor(QPalette.ColorRole.Window, dark_color)
    palette.setColor(QPalette.ColorRole.WindowText, text_color)
    palette.setColor(QPalette.ColorRole.Base, base_color)
    palette.setColor(QPalette.ColorRole.AlternateBase, dark_color)
    palette.setColor(QPalette.ColorRole.ToolTipBase, dark_color)
    palette.setColor(QPalette.ColorRole.ToolTipText, text_color)
    palette.setColor(QPalette.ColorRole.Text, text_color)
    palette.setColor(QPalette.ColorRole.Button, QColor("#1c1d1f"))
    palette.setColor(QPalette.ColorRole.ButtonText, text_color)
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Link, QColor("#d4a373"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#C0B8A8"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#121212"))
    
    # Disabled colors
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#555659"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#555659"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#555659"))
    
    app.setPalette(palette)
    
    app.setStyleSheet("""
        QWizard {
            background-color: #121315;
        }
        QWizardPage {
            background-color: #121315;
        }
        QLabel {
            color: #ededee;
            font-size: 10pt;
        }
        QPushButton {
            background-color: #1c1d1f;
            color: #ededee;
            border: 1px solid #28292c;
            border-radius: 4px;
            min-width: 88px;
            padding: 5px 16px;
            font-size: 10pt;
        }
        QPushButton:hover {
            background-color: #28292c;
        }
        QPushButton:pressed {
            background-color: #121315;
        }
        QPushButton:disabled {
            color: #555659;
            background-color: #0c0d0e;
            border: 1px solid #1c1d1f;
        }
        QLineEdit {
            background-color: #090a0b;
            color: #ffffff;
            border: 1px solid #28292c;
            border-radius: 3px;
            padding: 5px 8px;
            font-size: 10pt;
        }
        QLineEdit:focus {
            border: 1px solid #d4a373;
        }
        QTextEdit {
            background-color: #090a0b;
            color: #ededee;
            border: 1px solid #28292c;
            border-radius: 3px;
            font-size: 9pt;
        }
        QProgressBar {
            background-color: #090a0b;
            border: 1px solid #28292c;
            border-radius: 4px;
            text-align: center;
            color: #ededee;
            font-size: 9pt;
        }
        QProgressBar::chunk {
            background-color: #C0B8A8;
            border-radius: 3px;
        }
        QCheckBox, QRadioButton {
            color: #ededee;
            font-size: 10pt;
            spacing: 6px;
        }
        QFrame[frameShape="4"] {
            background-color: #28292c;
            max-height: 1px;
        }
    """)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if sys.platform != "win32":
        try:
            print("Error: Stet installer is only supported on Windows.")
        except Exception:
            pass
        sys.exit(1)

    log("Initializing Stet Setup...")

    # Locate the bundled ZIP before showing any UI
    zip_path = _find_zip_path()
    if zip_path is None:
        # Show a native error box (QApplication not yet created)
        ctypes.windll.user32.MessageBoxW(
            0,
            f"Error: Could not locate {ZIP_NAME}.\n\n"
            "If you are running the standalone StetSetup.exe, the bundled "
            "payload might be missing or corrupted. Otherwise, please ensure "
            "both the executable and stet_portable.zip are in the same folder.",
            "Stet Setup — Error",
            0x10,  # MB_ICONERROR
        )
        sys.exit(1)

    log(f"Found application archive: {zip_path}")

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("Stet Setup")
    app.setOrganizationName("Stet")

    _apply_stylesheet(app)

    wizard = StetInstaller(zip_path)

    # Connect Finish button to post-install actions *before* showing
    # (finished signal is connected inside StetInstaller.__init__)

    wizard.show()
    result = app.exec()

    log("Installation complete. Exiting Setup." if result == 0 else "Setup exited.")
    sys.exit(0)


if __name__ == "__main__":
    main()
