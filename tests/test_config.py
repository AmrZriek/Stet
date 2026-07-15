import json
import sys
from unittest.mock import MagicMock
import pytest

import stet.core.config as config_mod
from stet.core.config import ConfigManager

# Ensure winreg is mocked on non-Windows systems for testing
if sys.platform != "win32":
    winreg_mock = MagicMock()
    sys.modules["winreg"] = winreg_mock


@pytest.fixture
def temp_config_setup(tmp_path, monkeypatch):
    """Fixture to set up a temporary config file and mock SCRIPT_DIR."""
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)
    monkeypatch.setattr(config_mod, "SCRIPT_DIR", tmp_path)
    return config_file


class TestConfigMigration:
    def test_fresh_install_initializes_defaults(self, temp_config_setup):
        # Config file does not exist initially
        assert not temp_config_setup.exists()

        cfg = ConfigManager()
        # Verify fresh config initializes new default keys
        assert cfg.get("show_welcome_on_startup") is True
        assert cfg.get("chat_thinking_enabled") is True
        assert cfg.get("startup_on_login") is False

    def test_migrate_existing_config_preserves_values(self, temp_config_setup):
        # Create a legacy config (no presets or welcome keys, but has startup_on_login=True)
        legacy_data = {
            "model_path": "legacy-model.gguf",
            "startup_on_login": True,
        }
        temp_config_setup.write_text(json.dumps(legacy_data), encoding="utf-8")

        cfg = ConfigManager()
        # Check that existing keys were preserved
        assert cfg.get("model_path") == "legacy-model.gguf"
        assert cfg.get("startup_on_login") is True

        # Check that missing keys were migrated
        assert cfg.get("show_welcome_on_startup") is True
        assert cfg.get("chat_thinking_enabled") is True

    def test_migrate_existing_config_missing_startup_registry_true(self, temp_config_setup, monkeypatch):
        # Legacy config exists, has NO startup_on_login key
        legacy_data = {
            "model_path": "legacy-model.gguf",
        }
        temp_config_setup.write_text(json.dumps(legacy_data), encoding="utf-8")

        # Mock sys.platform to win32 and mock winreg to simulate startup registered in registry
        monkeypatch.setattr(sys, "platform", "win32")
        
        mock_winreg = MagicMock()
        # QueryValueEx should return a dummy value (indicating Stet exists in Run registry key)
        mock_winreg.QueryValueEx.return_value = ("cmd.exe", 0)
        sys.modules["winreg"] = mock_winreg

        cfg = ConfigManager()
        # startup_on_login should be True because the registry run key has Stet
        assert cfg.get("startup_on_login") is True

    def test_migrate_existing_config_missing_startup_registry_false(self, temp_config_setup, monkeypatch):
        # Legacy config exists, has NO startup_on_login key
        legacy_data = {
            "model_path": "legacy-model.gguf",
        }
        temp_config_setup.write_text(json.dumps(legacy_data), encoding="utf-8")

        # Mock sys.platform to win32 and mock winreg to simulate startup NOT registered (FileNotFoundError)
        monkeypatch.setattr(sys, "platform", "win32")
        
        mock_winreg = MagicMock()
        mock_winreg.QueryValueEx.side_effect = FileNotFoundError()
        sys.modules["winreg"] = mock_winreg

        cfg = ConfigManager()
        # startup_on_login should be False because QueryValueEx raises FileNotFoundError
        assert cfg.get("startup_on_login") is False

    def test_migrate_existing_config_updates_spelling_only_threshold(self, temp_config_setup):
        for old_val in (0.4, 0.55):
            legacy_data = {
                "correction_modes": [
                    {"name": "Spelling Only", "hallucination_threshold": old_val, "builtin": True},
                    {"name": "Full Correction", "hallucination_threshold": 1.0, "builtin": True},
                ]
            }
            temp_config_setup.write_text(json.dumps(legacy_data), encoding="utf-8")

            cfg = ConfigManager()
            modes = cfg.get("correction_modes")
            assert modes[0]["hallucination_threshold"] == 0.35
