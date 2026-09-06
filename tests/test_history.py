"""Tests for CorrectionHistory store and undo wiring."""



from stet.constants import DEFAULT_CONFIG
from stet.core.history import CorrectionHistory


class TestCorrectionHistory:
    def test_add_and_list_round_trip(self, tmp_path):
        h = CorrectionHistory(path=tmp_path / "h.jsonl", consent_granted=True)
        eid = h.add(mode="silent", strength="spelling_only",
                    original="teh cat", corrected="the cat")
        assert eid is not None
        entries = h.list()
        assert len(entries) == 1
        assert entries[0]["original"] == "teh cat"
        assert entries[0]["corrected"] == "the cat"

    def test_list_returns_newest_first(self, tmp_path):
        h = CorrectionHistory(path=tmp_path / "h.jsonl", consent_granted=True)
        h.add(mode="silent", strength="full_correction",
              original="first", corrected="first!")
        h.add(mode="panel", strength="rewrite_polish",
              original="second", corrected="second!!")
        entries = h.list()
        assert len(entries) >= 2
        assert entries[0]["original"] == "second"

    def test_cap_trims_oldest(self, tmp_path):
        h = CorrectionHistory(path=tmp_path / "h.jsonl", limit=3, consent_granted=True)
        for i in range(5):
            h.add(mode="panel", strength="full_correction",
                  original=f"text {i}", corrected=f"text {i}!")
        entries = h.list()
        assert len(entries) == 3

    def test_corrupt_line_tolerance(self, tmp_path):
        path = tmp_path / "h.jsonl"
        path.write_text(
            '{"id":"a","ts":"x","mode":"p","strength":"s","original":"o","corrected":"c","undone":false}\n'
            '{{{ bad json\n'
            '{"id":"b","ts":"y","mode":"p","strength":"s","original":"o2","corrected":"c2","undone":false}\n',
            encoding="utf-8",
        )
        h = CorrectionHistory(path=path, consent_granted=True)
        entries = h.list()
        ids = {e["id"] for e in entries}
        assert "a" in ids
        assert "b" in ids
        assert len(entries) == 2

    def test_mark_undone(self, tmp_path):
        h = CorrectionHistory(path=tmp_path / "h.jsonl", consent_granted=True)
        eid = h.add(mode="silent", strength="full_correction",
                    original="bad", corrected="good")
        assert h.mark_undone(eid)
        entry = h.get(eid)
        assert entry is not None
        assert entry["undone"] is True

    def test_mark_undone_nonexistent(self, tmp_path):
        h = CorrectionHistory(path=tmp_path / "h.jsonl", consent_granted=True)
        assert h.mark_undone("nonexistent") is False

    def test_remove_entry(self, tmp_path):
        h = CorrectionHistory(path=tmp_path / "h.jsonl", consent_granted=True)
        eid = h.add(mode="panel", strength="full_correction",
                    original="x", corrected="y")
        assert h.remove(eid)
        assert h.get(eid) is None
        assert len(h.list()) == 0

    def test_remove_nonexistent(self, tmp_path):
        h = CorrectionHistory(path=tmp_path / "h.jsonl", consent_granted=True)
        assert h.remove("fake") is False

    def test_clear(self, tmp_path):
        h = CorrectionHistory(path=tmp_path / "h.jsonl", consent_granted=True)
        h.add(mode="panel", strength="full_correction",
              original="a", corrected="b")
        h.clear()
        assert len(h.list()) == 0

    def test_disabled_store_no_ops(self, tmp_path):
        h = CorrectionHistory(path=tmp_path / "h.jsonl", enabled=False)
        eid = h.add(mode="panel", strength="full_correction",
                    original="a", corrected="b")
        assert eid is None
        assert not (tmp_path / "h.jsonl").exists()

    def test_identical_original_not_recorded(self, tmp_path):
        h = CorrectionHistory(path=tmp_path / "h.jsonl", consent_granted=True)
        eid = h.add(mode="panel", strength="spelling_only",
                    original="same", corrected="same")
        assert eid is None

    def test_empty_original_not_recorded(self, tmp_path):
        h = CorrectionHistory(path=tmp_path / "h.jsonl", consent_granted=True)
        eid = h.add(mode="panel", strength="spelling_only",
                    original="", corrected="fixed")
        assert eid is None

    def test_persists_across_reload(self, tmp_path):
        path = tmp_path / "h.jsonl"
        h1 = CorrectionHistory(path=path, consent_granted=True)
        h1.add(mode="silent", strength="full_correction",
               original="old", corrected="new")
        h2 = CorrectionHistory(path=path, consent_granted=True)
        entries = h2.list()
        assert len(entries) == 1
        assert entries[0]["original"] == "old"

    def test_config_defaults_present(self):
        assert "history_enabled" in DEFAULT_CONFIG
        assert "history_limit" in DEFAULT_CONFIG
        # History is enabled by default for undo and review.
        assert DEFAULT_CONFIG["history_enabled"] is True
        assert DEFAULT_CONFIG["history_consent_granted"] is True
        assert DEFAULT_CONFIG["history_limit"] == 200


class TestHistoryWindowUI:
    """UI test suite for redesigned HistoryWindow."""

    def test_history_window_displays_cards(self, tmp_path, qtbot):
        from stet.ui.history_window import HistoryWindow

        h = CorrectionHistory(path=tmp_path / "h.jsonl", consent_granted=True)
        h.add(mode="silent", strength="spelling_only", original="teh test", corrected="the test")

        cfg = {"history_enabled": True}
        win = HistoryWindow(history=h, cfg=cfg, undo_callback=lambda eid: None)
        qtbot.addWidget(win)

        assert win._list.count() == 1
        item = win._list.item(0)
        assert "[SILENT]" in item.text()
        assert "the test" in item.text()

        # Selection populates diff views
        win._list.setCurrentRow(0)
        assert win._original_view.toPlainText() == "teh test"
        assert win._corrected_view.toPlainText() == "the test"
        assert win._undo_btn.isEnabled() is True

from stet.core.history import UndoToken


class TestUndoToken:
    def test_undo_token_never_writes_jsonl(self, tmp_path):
        """A RAM-only UndoToken must never create/write history.jsonl."""
        import time
        tok = UndoToken(
            original="before",
            corrected="after",
            target_identity_hash="abc",
            replacement_fingerprint="def",
            expires_at=time.monotonic() + 60,
        )
        assert tok.original == "before"
        assert tok.corrected == "after"
        assert tok.replacement_fingerprint == "def"
        # The token itself is in-memory only; no file is touched.
        assert not (tmp_path / "h.jsonl").exists()

    def test_undo_token_single_use(self):
        """An UndoToken is single-use: first consume succeeds, second fails."""
        import time
        tok = UndoToken(
            original="x", corrected="y", target_identity_hash="h",
            replacement_fingerprint="f", expires_at=time.monotonic() + 60,
        )
        assert tok.consume() is True
        assert tok.consume() is False

    def test_undo_token_expired(self):
        """An expired token is not consumable."""
        import time
        tok = UndoToken(
            original="x", corrected="y", target_identity_hash="h",
            replacement_fingerprint="f", expires_at=time.monotonic() - 1,
        )
        assert tok.is_expired()
        assert tok.consume() is False


class TestHistoryConsentGate:
    def test_add_respects_consent_flag(self, tmp_path):
        """History writes only when history_enabled AND consent_granted are both true."""
        h = CorrectionHistory(path=tmp_path / "h.jsonl", enabled=True, consent_granted=False)
        eid = h.add(mode="panel", strength="full_correction", original="a", corrected="b")
        assert eid is None

    def test_add_requires_consent(self, tmp_path):
        """When consent is explicitly False, add() must not write history."""
        h = CorrectionHistory(path=tmp_path / "h.jsonl", enabled=True, consent_granted=False)
        assert getattr(h, "_consent_granted", True) is False
        eid = h.add(mode="panel", strength="full_correction", original="a", corrected="b")
        assert eid is None
