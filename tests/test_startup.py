import stet.core.app as app


def test_startup_command_uses_pythonw_and_main_py_for_source(tmp_path, monkeypatch):
    python_dir = tmp_path / "Python With Spaces"
    python_dir.mkdir()
    python_exe = python_dir / "python.exe"
    pythonw_exe = python_dir / "pythonw.exe"
    python_exe.write_text("", encoding="utf-8")
    pythonw_exe.write_text("", encoding="utf-8")
    main_py = tmp_path / "main.py"
    main_py.write_text("from stet.main import main\n", encoding="utf-8")

    monkeypatch.setattr(app, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(app.sys, "executable", str(python_exe))
    monkeypatch.setattr(app.sys, "frozen", False, raising=False)

    assert app._startup_command() == app._quote_cmd([str(pythonw_exe), str(main_py)])


def test_startup_command_uses_frozen_executable(tmp_path, monkeypatch):
    exe = tmp_path / "Stet.exe"
    exe.write_text("", encoding="utf-8")

    monkeypatch.setattr(app.sys, "executable", str(exe))
    monkeypatch.setattr(app.sys, "frozen", True, raising=False)

    assert app._startup_command() == app._quote_cmd([str(exe)])


def test_startup_command_falls_back_to_module(tmp_path, monkeypatch):
    exe = tmp_path / "python.exe"
    exe.write_text("", encoding="utf-8")

    monkeypatch.setattr(app, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(app.sys, "executable", str(exe))
    monkeypatch.setattr(app.sys, "frozen", False, raising=False)
    monkeypatch.setattr(app.shutil, "which", lambda _: None)

    assert app._startup_command() == app._quote_cmd([str(exe), "-m", "stet.main"])


class MockCompletedProcess:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class MockWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 3

    def __init__(self):
        self.registry = {}
        self.open_keys = []

    def OpenKey(self, hkey, path, reserved, access):
        key_id = f"{hkey}\\{path}"
        self.open_keys.append(key_id)
        return key_id

    def QueryValueEx(self, key, value_name):
        if value_name in self.registry:
            return self.registry[value_name], self.REG_SZ
        raise FileNotFoundError()

    def SetValueEx(self, key, value_name, reserved, type_id, value):
        self.registry[value_name] = value

    def DeleteValue(self, key, value_name):
        if value_name in self.registry:
            del self.registry[value_name]
        else:
            raise FileNotFoundError()

    def CloseKey(self, key):
        if key in self.open_keys:
            self.open_keys.remove(key)


class FakeApp:
    def __init__(self):
        class FakeAction:
            def __init__(self):
                self.checked = False
            def setChecked(self, val):
                self.checked = val
        self._act_startup = FakeAction()
        
        class FakeTray:
            def __init__(self):
                self.messages = []
            def showMessage(self, title, msg, *args):
                self.messages.append((title, msg))
        self.tray = FakeTray()

    _update_startup_action = app.StetApp._update_startup_action
    _toggle_startup = app.StetApp._toggle_startup
    _cleanup_legacy_startup_task = app.StetApp._cleanup_legacy_startup_task


def test_update_startup_action_checked(monkeypatch):
    mock_winreg = MockWinreg()
    mock_winreg.registry["Stet"] = "C:\\Path\\To\\Stet.exe"
    monkeypatch.setattr(app, "winreg", mock_winreg)
    monkeypatch.setattr(app, "WINDOWS", True)

    fa = FakeApp()
    fa._update_startup_action()
    assert fa._act_startup.checked is True


def test_update_startup_action_unchecked(monkeypatch):
    mock_winreg = MockWinreg()
    monkeypatch.setattr(app, "winreg", mock_winreg)
    monkeypatch.setattr(app, "WINDOWS", True)

    fa = FakeApp()
    fa._update_startup_action()
    assert fa._act_startup.checked is False


def test_toggle_startup_enable(monkeypatch):
    mock_winreg = MockWinreg()
    monkeypatch.setattr(app, "winreg", mock_winreg)
    monkeypatch.setattr(app, "WINDOWS", True)
    monkeypatch.setattr(app, "_startup_command", lambda: "C:\\Path\\To\\Stet.exe")

    called_runs = []
    def mock_run(args, **kwargs):
        called_runs.append(args)
        return MockCompletedProcess(0)
    monkeypatch.setattr(app.subprocess, "run", mock_run)

    fa = FakeApp()
    fa._toggle_startup(True)

    assert ["schtasks", "/delete", "/tn", "Stet Startup", "/f"] in called_runs
    assert mock_winreg.registry.get("Stet") == "C:\\Path\\To\\Stet.exe"
    assert any("Added to Windows startup" in msg[1] for msg in fa.tray.messages)


def test_toggle_startup_disable(monkeypatch):
    mock_winreg = MockWinreg()
    mock_winreg.registry["Stet"] = "C:\\Path\\To\\Stet.exe"
    monkeypatch.setattr(app, "winreg", mock_winreg)
    monkeypatch.setattr(app, "WINDOWS", True)

    called_runs = []
    def mock_run(args, **kwargs):
        called_runs.append(args)
        return MockCompletedProcess(0)
    monkeypatch.setattr(app.subprocess, "run", mock_run)

    fa = FakeApp()
    fa._toggle_startup(False)

    assert ["schtasks", "/delete", "/tn", "Stet Startup", "/f"] in called_runs
    assert "Stet" not in mock_winreg.registry
    assert any("Removed from Windows" in msg[1] for msg in fa.tray.messages)


def test_startup_command_uses_vbs_wrapper_if_exists(tmp_path, monkeypatch):
    vbs = tmp_path / "startup.vbs"
    vbs.write_text("Wscript.Echo", encoding="utf-8")
    monkeypatch.setattr(app, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(app.sys, "frozen", False, raising=False)
    # Ensure exe looks like a Python interpreter (source mode)
    monkeypatch.setattr(app.sys, "executable", str(tmp_path / "python.exe"))
    assert app._startup_command() == app._quote_cmd(["wscript.exe", str(vbs)])


def test_cleanup_legacy_startup_task_exception_handling(monkeypatch):
    def mock_run(args, **kwargs):
        raise RuntimeError("simulated error")
    monkeypatch.setattr(app.subprocess, "run", mock_run)
    monkeypatch.setattr(app, "WINDOWS", True)

    fa = FakeApp()
    # This should execute gracefully and not raise any exceptions
    fa._cleanup_legacy_startup_task()


# ── Nuitka detection (exe name, no sys.frozen) ──────────────────────────────

def test_startup_command_detects_nuitka_by_exe_name(tmp_path, monkeypatch):
    """Nuitka builds have sys.frozen=False but exe is Stet.exe (not python*.exe).

    The startup command must return the exe path, NOT pythonw.exe -m stet.main.
    This is the root cause fix for the compiled-build startup failure.
    """
    exe = tmp_path / "Stet.exe"
    exe.write_text("", encoding="utf-8")

    monkeypatch.setattr(app.sys, "executable", str(exe))
    monkeypatch.setattr(app.sys, "frozen", False, raising=False)

    assert app._startup_command() == app._quote_cmd([str(exe)])


def test_startup_command_nuitka_with_custom_exe_name(tmp_path, monkeypatch):
    """Any non-python exe name should be detected as a compiled build."""
    exe = tmp_path / "MyApp.exe"
    exe.write_text("", encoding="utf-8")

    monkeypatch.setattr(app.sys, "executable", str(exe))
    monkeypatch.setattr(app.sys, "frozen", False, raising=False)

    assert app._startup_command() == app._quote_cmd([str(exe)])


def test_startup_command_pythonw_exe_detected_as_source(tmp_path, monkeypatch):
    """pythonw.exe must be detected as source mode, not compiled."""
    monkeypatch.setattr(app, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(app.sys, "executable", str(tmp_path / "pythonw.exe"))
    monkeypatch.setattr(app.sys, "frozen", False, raising=False)
    monkeypatch.setattr(app.shutil, "which", lambda _: None)

    result = app._startup_command()
    assert "stet.main" in result or "main.py" in result


def test_startup_command_python313_exe_detected_as_source(tmp_path, monkeypatch):
    """python3.13.exe must be detected as source mode, not compiled."""
    monkeypatch.setattr(app, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(app.sys, "executable", str(tmp_path / "python3.13.exe"))
    monkeypatch.setattr(app.sys, "frozen", False, raising=False)
    monkeypatch.setattr(app.shutil, "which", lambda _: None)

    result = app._startup_command()
    assert "stet.main" in result or "main.py" in result


def test_toggle_startup_nuitka_registers_exe_path(monkeypatch):
    """Full integration: toggling startup ON with Nuitka build writes exe path to registry."""
    mock_winreg = MockWinreg()
    monkeypatch.setattr(app, "winreg", mock_winreg)
    monkeypatch.setattr(app, "WINDOWS", True)

    # Simulate Nuitka build: exe is Stet.exe, sys.frozen is False
    exe_path = "D:\\Portable\\Stet\\Stet.exe"
    monkeypatch.setattr(app.sys, "executable", exe_path)
    monkeypatch.setattr(app.sys, "frozen", False, raising=False)

    called_runs = []
    def mock_run(args, **kwargs):
        called_runs.append(args)
        return MockCompletedProcess(0)
    monkeypatch.setattr(app.subprocess, "run", mock_run)

    fa = FakeApp()
    fa._toggle_startup(True)

    # Registry should contain the exe path, NOT pythonw.exe -m stet.main
    assert mock_winreg.registry.get("Stet") == exe_path
    assert "pythonw" not in mock_winreg.registry.get("Stet", "")
    assert "stet.main" not in mock_winreg.registry.get("Stet", "")
