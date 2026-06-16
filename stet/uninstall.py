"""
uninstall.py — Stet uninstaller (compiled to StetUninstall.exe via Nuitka)
===========================================================================
Removes Stet from the system: deletes application files, shortcuts, and
registry entries.  Preserves user configuration and AI model files by default.

Usage:
    StetUninstall.exe            # interactive (confirmation dialog)
    StetUninstall.exe --silent   # silent uninstall (no dialog)
    StetUninstall.exe --purge    # also delete config and model files
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import winreg
from pathlib import Path

ARP_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Stet"
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"

MB_YESNO = 0x04
MB_ICONQUESTION = 0x20
MB_ICONERROR = 0x10
IDYES = 6


def _message_box(text: str, title: str, flags: int) -> int:
    import ctypes
    return ctypes.windll.user32.MessageBoxW(0, text, title, flags)


def _read_install_dir() -> str | None:
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, ARP_KEY_PATH, 0, winreg.KEY_READ
        )
        install_dir, _ = winreg.QueryValueEx(key, "InstallLocation")
        winreg.CloseKey(key)
        return install_dir
    except OSError:
        return None


def _kill_stet_processes() -> None:
    try:
        subprocess.run(
            ["taskkill", "/IM", "Stet.exe", "/F"],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass


def _delete_tree(path: Path) -> None:
    import shutil
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except PermissionError:
        pass


def _delete_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        pass


def _remove_app_files(install_dir: Path, purge: bool) -> None:
    preserve_config = not purge
    preserve_models = not purge

    for item in install_dir.iterdir():
        if item.name == "StetUninstall.exe":
            continue
        if item.name == "_uninstall_cleanup.bat":
            continue

        if preserve_config and item.name.lower() == "config.json":
            continue

        if preserve_models and item.suffix.lower() == ".gguf":
            continue

        if item.is_dir():
            _delete_tree(item)
        else:
            _delete_file(item)


def _create_cleanup_bat(install_dir: Path, purge: bool) -> Path:
    bat_path = Path(tempfile.gettempdir()) / "stet_uninstall_cleanup.bat"

    install_dir_escaped = str(install_dir).replace('"', '""')
    desktop_lnk = r"%USERPROFILE%\Desktop\Stet.lnk"
    startmenu_lnk = r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Stet.lnk"

    lines = [
        "@echo off",
        "timeout /t 2 /nobreak >nul",
        f'del /f /q "{install_dir_escaped}\\StetUninstall.exe" 2>nul',
        f'del /f /q "{desktop_lnk}" 2>nul',
        f'del /f /q "{startmenu_lnk}" 2>nul',
        f'reg delete "HKCU\\{ARP_KEY_PATH}" /f 2>nul',
        f'reg delete "HKCU\\{RUN_KEY_PATH}" /v "Stet" /f 2>nul',
    ]

    if purge:
        lines.append(f'rd /q /s "{install_dir_escaped}" 2>nul')
    else:
        lines.append(f'rd /q "{install_dir_escaped}" 2>nul')

    lines.append(f'del /f /q "{bat_path}" 2>nul')

    bat_path.write_text("\r\n".join(lines), encoding="utf-8")
    return bat_path


def _spawn_cleanup(bat_path: Path) -> None:
    subprocess.Popen(
        ["cmd.exe", "/c", str(bat_path)],
        creationflags=0x08000000 | 0x00000008,
        close_fds=True,
    )


def main() -> None:
    if sys.platform != "win32":
        print("Error: Stet uninstaller is only supported on Windows.")
        sys.exit(1)

    silent = "--silent" in sys.argv
    purge = "--purge" in sys.argv

    install_dir_str = _read_install_dir()
    if not install_dir_str:
        if not silent:
            _message_box(
                "Could not find Stet installation in the Windows registry.\n\n"
                "Stet may have been installed as a portable ZIP and is not "
                "registered. Please delete the Stet folder manually.",
                "Stet Uninstaller — Not Found",
                MB_ICONERROR,
            )
        sys.exit(1)

    install_dir = Path(install_dir_str)
    if not install_dir.exists():
        if not silent:
            _message_box(
                f"The installation directory no longer exists:\n{install_dir}\n\n"
                "Registry entries will be cleaned up.",
                "Stet Uninstaller",
                MB_ICONERROR,
            )
        _cleanup_registry_only()
        sys.exit(0)

    if not silent:
        msg = (
            f"Remove Stet from your computer?\n\n"
            f"Install path: {install_dir}\n\n"
        )
        if purge:
            msg += "All files including settings and AI models will be deleted."
        else:
            msg += (
                "Your settings (config.json) and AI model files (.gguf) "
                "will be preserved.\n\n"
                "To remove everything, run: StetUninstall.exe --purge"
            )

        result = _message_box(msg, "Stet Uninstaller", MB_YESNO | MB_ICONQUESTION)
        if result != IDYES:
            sys.exit(0)

    _kill_stet_processes()
    _remove_app_files(install_dir, purge)

    bat_path = _create_cleanup_bat(install_dir, purge)
    _spawn_cleanup(bat_path)


def _cleanup_registry_only() -> None:
    for key_path, value_name in [
        (ARP_KEY_PATH, None),
        (RUN_KEY_PATH, "Stet"),
    ]:
        try:
            if value_name:
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
                )
                winreg.DeleteValue(key, value_name)
                winreg.CloseKey(key)
            else:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
        except OSError:
            pass


if __name__ == "__main__":
    main()
