import json
from pathlib import Path
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
    monkeypatch.setattr(config_mod, "MODELS_DIR", tmp_path / "Models")
    return config_file


class TestConfigMigration:
    def test_fresh_install_initializes_defaults(self, temp_config_setup):
        # Config file does not exist initially
        assert not temp_config_setup.exists()

        cfg = ConfigManager()
        # Verify fresh config initializes new default keys
        assert cfg.get("show_welcome_on_startup") is True
        assert cfg.get("chat_thinking_enabled") is False
        assert cfg.get("startup_on_login") is False
        assert cfg.get("mtp_enabled") is True
        assert cfg.get("chat_mtp_enabled") is True

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
        assert cfg.get("chat_thinking_enabled") is False

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
            assert modes[0]["hallucination_threshold"] == 0.45

    def test_migrate_rewrite_polish_prompt_to_formatting_version(self, temp_config_setup):
        """Both pre-1.3.0 Rewrite & Polish prompt generations must migrate to
        the formatting-preserving default; customized prompts stay untouched."""
        from stet.constants import DEFAULT_CONFIG
        from stet.core.config import (
            _OLD_REWRITE_POLISH_MODE_PROMPT,
            _OLD_REWRITE_POLISH_MODE_PROMPT_V2,
        )

        new_prompt = DEFAULT_CONFIG["correction_modes"][2]["prompt"]
        assert new_prompt != _OLD_REWRITE_POLISH_MODE_PROMPT_V2

        for old_prompt in (_OLD_REWRITE_POLISH_MODE_PROMPT, _OLD_REWRITE_POLISH_MODE_PROMPT_V2, "my custom polish"):
            legacy_data = {
                "correction_modes": [
                    {"name": "Spelling Only", "prompt": "fix typos", "builtin": True},
                    {"name": "Full Correction", "prompt": "fix everything", "builtin": True},
                    {"name": "Rewrite & Polish", "prompt": old_prompt, "builtin": True},
                ]
            }
            temp_config_setup.write_text(json.dumps(legacy_data), encoding="utf-8")

            cfg = ConfigManager()
            modes = cfg.get("correction_modes")
            if old_prompt == "my custom polish":
                assert modes[2]["prompt"] == "my custom polish"
            else:
                assert modes[2]["prompt"] == new_prompt
                assert "Preserve all existing formatting" in modes[2]["prompt"]

    def test_migrate_drifted_thresholds_reset_to_defaults(self, temp_config_setup):
        """Drifted builtin thresholds [0.7, 1.0, 1.0] must be reset to [0.45, 0.75, 0.97]."""
        drifted_data = {
            "correction_modes": [
                {"name": "Spelling Only", "hallucination_threshold": 0.7, "builtin": True},
                {"name": "Full Correction", "hallucination_threshold": 1.0, "builtin": True},
                {"name": "Rewrite & Polish", "hallucination_threshold": 1.0, "builtin": True},
            ]
        }
        temp_config_setup.write_text(json.dumps(drifted_data), encoding="utf-8")

        cfg = ConfigManager()
        modes = cfg.get("correction_modes")
        assert modes[0]["hallucination_threshold"] == 0.45
        assert modes[1]["hallucination_threshold"] == 0.75
        assert modes[2]["hallucination_threshold"] == 0.97

    def test_custom_mode_deliberate_threshold_preserved(self, temp_config_setup):
        """Custom (non-builtin) mode with deliberate threshold must NOT be reset."""
        custom_data = {
            "correction_modes": [
                {"name": "Spelling Only", "hallucination_threshold": 0.45, "builtin": True},
                {"name": "Full Correction", "hallucination_threshold": 0.75, "builtin": True},
                {"name": "Rewrite & Polish", "hallucination_threshold": 0.97, "builtin": True},
                {"name": "Custom Mode", "hallucination_threshold": 0.7, "builtin": False},
            ]
        }
        temp_config_setup.write_text(json.dumps(custom_data), encoding="utf-8")

        cfg = ConfigManager()
        modes = cfg.get("correction_modes")
        assert len(modes) >= 4
        assert modes[3]["name"] == "Custom Mode"
        assert modes[3]["hallucination_threshold"] == 0.7
        assert modes[3]["builtin"] is False

    def test_valid_thresholds_invariant_reload(self, temp_config_setup):
        """Valid thresholds [0.45, 0.75, 0.97] must remain unchanged across reloads."""
        valid_data = {
            "correction_modes": [
                {"name": "Spelling Only", "hallucination_threshold": 0.45, "builtin": True},
                {"name": "Full Correction", "hallucination_threshold": 0.75, "builtin": True},
                {"name": "Rewrite & Polish", "hallucination_threshold": 0.97, "builtin": True},
                {"name": "Custom Mode", "hallucination_threshold": 0.85, "builtin": False},
            ]
        }
        temp_config_setup.write_text(json.dumps(valid_data), encoding="utf-8")

        cfg1 = ConfigManager()
        modes1 = cfg1.get("correction_modes")
        assert [m["hallucination_threshold"] for m in modes1[:3]] == [0.45, 0.75, 0.97]
        assert modes1[3]["hallucination_threshold"] == 0.85

        # Reloading config
        cfg2 = ConfigManager()
        modes2 = cfg2.get("correction_modes")
        assert [m["hallucination_threshold"] for m in modes2[:3]] == [0.45, 0.75, 0.97]
        assert modes2[3]["hallucination_threshold"] == 0.85

    def test_invalid_non_finite_thresholds_reset_to_defaults(self, temp_config_setup):
        """Non-finite or out-of-range thresholds on builtin modes reset to defaults."""
        invalid_data = {
            "correction_modes": [
                {"name": "Spelling Only", "hallucination_threshold": -0.5, "builtin": True},
                {"name": "Full Correction", "hallucination_threshold": 1.5, "builtin": True},
                {"name": "Rewrite & Polish", "hallucination_threshold": "not_a_number", "builtin": True},
            ]
        }
        temp_config_setup.write_text(json.dumps(invalid_data), encoding="utf-8")

        cfg = ConfigManager()
        modes = cfg.get("correction_modes")
        assert modes[0]["hallucination_threshold"] == 0.45
        assert modes[1]["hallucination_threshold"] == 0.75
        assert modes[2]["hallucination_threshold"] == 0.97


class TestResetDriftedThresholdsHelper:
    def test_helper_returns_false_for_valid_thresholds(self):
        from stet.constants import DEFAULT_CONFIG
        from stet.core.config import _reset_drifted_thresholds

        defaults = DEFAULT_CONFIG["correction_modes"]
        modes = [m.copy() for m in defaults]
        assert _reset_drifted_thresholds(modes, defaults) is False

    def test_helper_resets_drifted_builtin_thresholds(self):
        from stet.constants import DEFAULT_CONFIG
        from stet.core.config import _reset_drifted_thresholds

        defaults = DEFAULT_CONFIG["correction_modes"]
        modes = [
            {"name": "Spelling Only", "hallucination_threshold": 0.7, "builtin": True},
            {"name": "Full Correction", "hallucination_threshold": 1.0, "builtin": True},
            {"name": "Rewrite & Polish", "hallucination_threshold": 1.0, "builtin": True},
            {"name": "Custom", "hallucination_threshold": 0.7, "builtin": False},
        ]
        changed = _reset_drifted_thresholds(modes, defaults)
        assert changed is True
        assert modes[0]["hallucination_threshold"] == 0.45
        assert modes[1]["hallucination_threshold"] == 0.75
        assert modes[2]["hallucination_threshold"] == 0.97
        assert modes[3]["hallucination_threshold"] == 0.7  # preserved

    def test_helper_handles_invalid_inputs(self):
        from stet.core.config import _is_valid_threshold, _reset_drifted_thresholds

        assert _is_valid_threshold(0.0) is True
        assert _is_valid_threshold(1.0) is True
        assert _is_valid_threshold(0.5) is True
        assert _is_valid_threshold(-0.1) is False
        assert _is_valid_threshold(1.1) is False
        assert _is_valid_threshold(float("nan")) is False
        assert _is_valid_threshold(float("inf")) is False
        assert _is_valid_threshold(None) is False
        assert _is_valid_threshold("0.5") is False
        assert _is_valid_threshold(True) is False
        assert _is_valid_threshold(False) is False

        assert _reset_drifted_thresholds(None, []) is False
        assert _reset_drifted_thresholds([], None) is False
        assert _reset_drifted_thresholds([], []) is False

    def test_config_backup_on_corruption(self, tmp_path: Path, monkeypatch):
        from stet.core import config as cfg_module

        corrupt_file = tmp_path / "config.json"
        corrupt_file.write_text("{\"broken\": [1, 2,", encoding="utf-8")
        monkeypatch.setattr(cfg_module, "CONFIG_FILE", corrupt_file)
        cfg = cfg_module.ConfigManager()
        assert cfg is not None
        backup_file = tmp_path / "config.json.corrupt.bak"
        assert backup_file.exists()
        assert backup_file.read_text(encoding="utf-8") == "{\"broken\": [1, 2,"

