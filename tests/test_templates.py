import json

from stet.constants import DEFAULT_TEMPLATES
from stet.core.config import ConfigManager


def test_config_manager_populates_default_templates(monkeypatch, tmp_path):
    # Use a temporary config file
    config_file = tmp_path / "config.json"
    monkeypatch.setattr("stet.core.config.CONFIG_FILE", config_file)

    mgr = ConfigManager()
    templates = mgr.config.get("custom_templates", [])

    assert len(templates) >= 4

    # Save and reload should preserve
    mgr.save()
    mgr2 = ConfigManager()
    assert len(mgr2.config.get("custom_templates", [])) == len(templates)


def test_default_templates_have_valid_keys():
    for template in DEFAULT_TEMPLATES:
        assert "name" in template
        assert "prompt" in template


def test_config_manager_migrates_legacy_default_templates(monkeypatch, tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(
        """
{
  "custom_templates": [
    {"name": "Tighten", "prompt": "Optimize this text."},
    {"name": "Email", "prompt": "Polish this text for a professional email."},
    {"name": "Formal", "prompt": "Rewrite this in formal English."},
    {"name": "Social", "prompt": "Rewrite this as a social media post."}
  ]
}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("stet.core.config.CONFIG_FILE", config_file)

    mgr = ConfigManager()

    templates = mgr.config["custom_templates"]
    assert [t["name"] for t in templates] == [t["name"] for t in DEFAULT_TEMPLATES]

    persisted = json.loads(config_file.read_text(encoding="utf-8"))
    assert [t["name"] for t in persisted["custom_templates"]] == [
        t["name"] for t in DEFAULT_TEMPLATES
    ]


def test_config_manager_preserves_custom_template_names(monkeypatch, tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(
        """
{
  "custom_templates": [
    {"name": "My Workflow", "prompt": "Do my specific workflow."},
    {"name": "Email", "prompt": "My email prompt."}
  ]
}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("stet.core.config.CONFIG_FILE", config_file)

    mgr = ConfigManager()

    assert [t["name"] for t in mgr.config["custom_templates"]] == [
        "My Workflow",
        "Email",
    ]
    persisted = json.loads(config_file.read_text(encoding="utf-8"))
    assert [t["name"] for t in persisted["custom_templates"]] == [
        "My Workflow",
        "Email",
    ]
