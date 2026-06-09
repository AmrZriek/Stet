import pytest
from stet.core.config import ConfigManager
from stet.ui.settings import SettingsDialog

@pytest.fixture
def cfg_manager(tmp_path, monkeypatch):
    import stet.core.config as config_module
    config_file = tmp_path / "config.json"
    config_file.write_text("{}")
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)
    cfg = ConfigManager()
    cfg.config = {
        "model_path": "ac_model.gguf",
        "chat_model_path": "chat_model.gguf",
        "chat_use_separate_model": True
    }
    return cfg

def test_settings_save_does_not_clear_chat_path_when_unchecking(qtbot, cfg_manager):
    dialog = SettingsDialog(cfg_manager)
    qtbot.addWidget(dialog)
    
    # Simulate user unchecking the "use separate model" box
    dialog.chat_use_separate_cb.setChecked(False)
    
    # Save
    dialog._save()
    
    # Check that chat_model_path was correctly synced to model_path by ConfigManager
    # rather than being cleared by SettingsDialog
    assert cfg_manager.get("chat_model_path") == "ac_model.gguf"
    assert cfg_manager.get("chat_use_separate_model") is False
