"""Tests for stet.llm.gguf_info -- offline GGUF metadata reading and caching.

Builds tiny synthetic GGUF files with ``gguf.GGUFWriter``; no llama-server
and no Qt involved.
"""

import os

import pytest

from stet.llm.gguf_info import (
    GgufModelInfo,
    GgufReadError,
    get_gguf_info_cached,
    read_gguf_info,
)

import stet.llm.gguf_info as gguf_info_module


# -- Helpers ------------------------------------------------------------------


def _write_gguf(
    path,
    arch="qwen2",
    name="TestModel",
    chat_template=None,
    context_length=None,
    legacy_context_length=None,
):
    """Write a minimal synthetic GGUF and return its path."""
    from gguf import GGUFWriter

    writer = GGUFWriter(str(path), arch)
    if name is not None:
        writer.add_name(name)
    if chat_template is not None:
        writer.add_chat_template(chat_template)
    if context_length is not None:
        writer.add_context_length(context_length)
    if legacy_context_length is not None:
        # Raw key write to emulate pre-architecture-era files
        writer.add_uint32("llama.context_length", legacy_context_length)
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.close()
    return path


@pytest.fixture(autouse=True)
def _clear_cache():
    """Keep the module-level cache isolated between tests."""
    gguf_info_module._cache.clear()
    yield
    gguf_info_module._cache.clear()


# -- Round-trip ---------------------------------------------------------------


class TestRoundTrip:
    def test_all_fields_round_trip(self, tmp_path):
        path = _write_gguf(
            tmp_path / "model.gguf",
            arch="qwen2",
            name="Qwen2-1.5B-Instruct",
            chat_template="{{bos}}<think>{{prompt}}</think>{{eos}}",
            context_length=32768,
        )

        info = read_gguf_info(path)

        assert isinstance(info, GgufModelInfo)
        assert info.path == str(path)
        assert info.architecture == "qwen2"
        assert info.name == "Qwen2-1.5B-Instruct"
        assert info.chat_template == "{{bos}}<think>{{prompt}}</think>{{eos}}"
        assert info.n_ctx_train == 32768
        assert info.reasoning_capable is True
        assert info.file_size == os.path.getsize(path)
        assert info.mtime == os.path.getmtime(path)

    def test_legacy_context_length_fallback(self, tmp_path):
        """Old files store context length under ``llama.context_length``."""
        path = _write_gguf(
            tmp_path / "legacy.gguf",
            arch="qwen2",
            name="LegacyModel",
            legacy_context_length=16384,
        )

        info = read_gguf_info(path)

        assert info.architecture == "qwen2"
        assert info.n_ctx_train == 16384


# -- reasoning_capable --------------------------------------------------------


class TestReasoningCapable:
    @pytest.mark.parametrize(
        "template",
        [
            "{{<think>{{prompt}}</think>}}",
            "<thinking>{{prompt}}</thinking>",
            "<reasoning>{{prompt}}</reasoning>",
            "Upper <THINK case",
        ],
    )
    def test_true_for_thinking_templates(self, tmp_path, template):
        path = _write_gguf(tmp_path / "reason.gguf", chat_template=template)
        info = read_gguf_info(path)
        assert info.reasoning_capable is True

    @pytest.mark.parametrize(
        "template",
        [
            "{{prompt}}",
            "{{bos}}Hi there{{eos}}",
            "plain, no tags",
            None,
        ],
    )
    def test_false_without_thinking_tags(self, tmp_path, template):
        path = _write_gguf(tmp_path / "plain.gguf", chat_template=template)
        info = read_gguf_info(path)
        assert info.reasoning_capable is False


# -- Missing keys -------------------------------------------------------------


class TestMissingKeys:
    def test_missing_keys_yield_none(self, tmp_path):
        """A file without name/template/context keys must not crash."""
        path = _write_gguf(
            tmp_path / "sparse.gguf",
            arch="llama",
            name=None,  # type: ignore[arg-type]
            chat_template=None,
            context_length=None,
        )

        info = read_gguf_info(path)

        assert info.architecture == "llama"
        assert info.name is None
        assert info.chat_template is None
        assert info.n_ctx_train is None
        assert info.reasoning_capable is False

    def test_malformed_param_count_yields_none(self, tmp_path):
        from gguf import GGUFWriter
        path = tmp_path / "bad_param.gguf"
        writer = GGUFWriter(str(path), "llama")
        writer.add_string("general.parameter_count", "not-a-number")
        writer.write_header_to_file()
        writer.write_kv_data_to_file()
        writer.close()

        info = read_gguf_info(path)
        assert info.parameter_count is None

# -- Errors -------------------------------------------------------------------


class TestErrors:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(GgufReadError, match="not found"):
            read_gguf_info(tmp_path / "nope.gguf")

    def test_invalid_file_raises(self, tmp_path):
        path = tmp_path / "garbage.gguf"
        path.write_bytes(b"this is not a gguf file at all")
        with pytest.raises(GgufReadError, match="valid GGUF"):
            read_gguf_info(path)

    def test_gguf_package_missing_raises(self, tmp_path, monkeypatch):
        """With the gguf package unavailable, degrade to GgufReadError."""
        path = _write_gguf(tmp_path / "ok.gguf")
        monkeypatch.setattr(gguf_info_module, "GGUFReader", None)

        with pytest.raises(GgufReadError, match="not installed"):
            read_gguf_info(path)


# -- Caching ------------------------------------------------------------------


class TestCaching:
    def test_same_object_for_unchanged_file(self, tmp_path):
        path = _write_gguf(tmp_path / "cached.gguf", name="CachedModel")

        first = get_gguf_info_cached(path)
        second = get_gguf_info_cached(path)

        assert first is second

    def test_rereads_after_mtime_bump(self, tmp_path):
        path = _write_gguf(tmp_path / "cached.gguf", name="CachedModel")

        first = get_gguf_info_cached(path)

        old_mtime = os.path.getmtime(path)
        os.utime(path, (old_mtime + 10, old_mtime + 10))
        second = get_gguf_info_cached(path)

        assert second is not first
        assert second.mtime == pytest.approx(old_mtime + 10)

    def test_rereads_after_size_change(self, tmp_path):
        path = _write_gguf(tmp_path / "cached.gguf", name="CachedModel")

        first = get_gguf_info_cached(path)
        assert first.name == "CachedModel"

        # Rewrite with a longer name -> different file size -> cache miss
        _write_gguf(path, name="CachedModelWithALongerNameToChangeTheSize")
        second = get_gguf_info_cached(path)

        assert second is not first
        assert second.name == "CachedModelWithALongerNameToChangeTheSize"

    def test_cache_evicted_on_missing_file(self, tmp_path):
        path = _write_gguf(tmp_path / "gone.gguf", name="GoneModel")

        info = get_gguf_info_cached(path)
        assert info.name == "GoneModel"

        os.remove(path)
        with pytest.raises(GgufReadError, match="not found"):
            get_gguf_info_cached(path)


# -- Performance --------------------------------------------------------------


class TestPerformance:
    def test_streaming_binary_read_is_fast(self, tmp_path):
        """GGUF header parsing must complete in sub-second time without memmapping gigabytes."""
        import time
        path = _write_gguf(
            tmp_path / "perf.gguf",
            arch="qwen2",
            name="PerfModel",
            chat_template="{{bos}}<think>{{prompt}}</think>{{eos}}",
            context_length=32768,
        )
        t0 = time.perf_counter()
        info = read_gguf_info(path)
        elapsed = time.perf_counter() - t0
        assert info.name == "PerfModel"
        assert elapsed < 0.5, f"Expected sub-500ms parse, took {elapsed:.3f}s"
