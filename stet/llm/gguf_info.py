"""Offline GGUF metadata reader.

Reads model metadata (architecture, name, chat template, training context
length) directly from a .gguf file using the official ``gguf`` package —
no llama-server startup required. Used to surface model info in the UI and
to detect reasoning-capable models before a correction run.

The ``gguf`` package is an optional dependency: if it is not installed the
module still imports (so the app can degrade gracefully) and
:func:`read_gguf_info` raises :class:`GgufReadError` with a clear message.
"""

import os
import struct
from dataclasses import dataclass

from stet.core.utils import log

try:
    from gguf import GGUFReader
except ImportError:  # pragma: no cover - exercised via monkeypatch in tests
    GGUFReader = None  # type: ignore[assignment]
__all__ = [
    "GgufModelInfo",
    "GgufReadError",
    "read_gguf_info",
    "get_gguf_info_cached",
]


class GgufReadError(Exception):
    """Raised when GGUF metadata cannot be read (missing package, file, or invalid file)."""


@dataclass
class GgufModelInfo:
    """Metadata extracted from a GGUF file, best-effort (None when absent)."""

    path: str
    architecture: str | None = None
    name: str | None = None
    chat_template: str | None = None
    n_ctx_train: int | None = None
    parameter_count: int | None = None
    supports_mtp: bool = False
    reasoning_capable: bool = False
    file_size: int = 0
    mtime: float = 0.0


# Module-level cache: path -> (file_size, mtime, GgufModelInfo).
# Entries are invalidated when the file's size or mtime changes on disk.
_cache: dict[str, tuple[int, float, "GgufModelInfo"]] = {}

# Tokens that mark a chat template as belonging to a reasoning model.
_REASONING_MARKERS = (
    "<think", "<thinking", "<reasoning", "<thought",
    "<|thought", "<|start_of_thought", "[think", "[thought",
)


def _read_gguf_binary(path_str: str) -> tuple[dict[str, object], bool]:
    """Fast streaming binary parser for GGUF key-value metadata and tensor names.

    Avoids memory-mapping the entire multi-gigabyte file or constructing numpy
    arrays for hundreds of tensors. Reads only the header and KV entries
    buffered, completing in ~200-300ms instead of 28+ seconds.
    """
    with open(path_str, "rb", buffering=2 * 1024 * 1024) as f:
        magic, ver, tensor_cnt, kv_cnt = struct.unpack("<4sIQQ", f.read(24))
        if magic != b"GGUF":
            raise ValueError("Invalid GGUF magic bytes")

        def read_str() -> str:
            raw_len = f.read(8)
            if len(raw_len) < 8:
                raise ValueError("Unexpected EOF reading string length")
            slen = struct.unpack("<Q", raw_len)[0]
            sdata = f.read(slen)
            return sdata.decode("utf-8", errors="replace")

        kv: dict[str, object] = {}
        for _ in range(kv_cnt):
            key = read_str()
            raw_vtype = f.read(4)
            if len(raw_vtype) < 4:
                break
            vtype = struct.unpack("<I", raw_vtype)[0]
            if vtype == 8:  # String
                val = read_str()
            elif vtype in (4, 5):  # uint32 / int32
                val = struct.unpack("<I" if vtype == 4 else "<i", f.read(4))[0]
            elif vtype in (10, 11):  # uint64 / int64
                val = struct.unpack("<Q" if vtype == 10 else "<q", f.read(8))[0]
            elif vtype == 7:  # bool
                val = struct.unpack("<?", f.read(1))[0]
            elif vtype in (0, 1):  # u8 / i8
                val = struct.unpack("<B" if vtype == 0 else "<b", f.read(1))[0]
            elif vtype in (2, 3):  # u16 / i16
                val = struct.unpack("<H" if vtype == 2 else "<h", f.read(2))[0]
            elif vtype == 6:  # float32
                val = struct.unpack("<f", f.read(4))[0]
            elif vtype == 12:  # float64
                val = struct.unpack("<d", f.read(8))[0]
            elif vtype == 9:  # array
                raw_arr = f.read(12)
                if len(raw_arr) < 12:
                    break
                arr_type, arr_len = struct.unpack("<IQ", raw_arr)
                if arr_type == 8:  # array of strings
                    if key.startswith("tokenizer.ggml.tokens") or key.startswith("tokenizer.ggml.merges"):
                        # Fast-skip massive tokenizer vocabularies without decoding strings
                        for _ in range(arr_len):
                            slen = struct.unpack("<Q", f.read(8))[0]
                            f.seek(slen, 1)
                        val = None
                    else:
                        val = [read_str() for _ in range(arr_len)]
                elif arr_type in (0, 1, 7):
                    f.seek(arr_len * 1, 1)
                    val = None
                elif arr_type in (2, 3):
                    f.seek(arr_len * 2, 1)
                    val = None
                elif arr_type in (4, 5, 6):
                    f.seek(arr_len * 4, 1)
                    val = None
                elif arr_type in (10, 11, 12):
                    f.seek(arr_len * 8, 1)
                    val = None
                else:
                    break
            else:
                break
            kv[key] = val

        # Fast-scan tensor names for MTP detection
        has_mtp_tensor = False
        for _ in range(tensor_cnt):
            raw_tlen = f.read(8)
            if len(raw_tlen) < 8:
                break
            tlen = struct.unpack("<Q", raw_tlen)[0]
            tname = f.read(tlen).decode("utf-8", errors="replace")
            if "nextn" in tname.lower():
                has_mtp_tensor = True
            raw_dims = f.read(4)
            if len(raw_dims) < 4:
                break
            n_dims = struct.unpack("<I", raw_dims)[0]
            f.seek(n_dims * 8 + 4 + 8, 1)  # dims (8*n) + type (4) + offset (8)

        return kv, has_mtp_tensor

def _is_reasoning_capable(template: str | None) -> bool:
    """A model is reasoning-capable when its chat template emits a thinking tag."""
    if not template:
        return False
    lowered = template.lower()
    return any(marker in lowered for marker in _REASONING_MARKERS)


def read_gguf_info(path) -> GgufModelInfo:
    """Read GGUF metadata from *path* and return a :class:`GgufModelInfo`.

    Raises :class:`GgufReadError` when the ``gguf`` package is unavailable,
    the file is missing, or the file is not a valid GGUF. Missing metadata
    keys never raise — they simply yield ``None``.
    """
    path_str = os.fspath(path)
    if GGUFReader is None:
        raise GgufReadError(
            "GGUF metadata reader unavailable: the 'gguf' package is not installed. "
            "Install it with `pip install gguf` to read model metadata."
        )

    try:
        stat = os.stat(path_str)
    except OSError as exc:
        raise GgufReadError(f"GGUF file not found: {path_str}") from exc

    try:
        kv, has_mtp_tensor = _read_gguf_binary(path_str)
    except Exception as exc:
        raise GgufReadError(f"Not a valid GGUF file: {path_str} ({exc})") from exc

    architecture = str(kv["general.architecture"]) if "general.architecture" in kv and kv["general.architecture"] is not None else None
    name = str(kv["general.name"]) if "general.name" in kv and kv["general.name"] is not None else None
    chat_template = str(kv["tokenizer.chat_template"]) if "tokenizer.chat_template" in kv and kv["tokenizer.chat_template"] is not None else None
    param_count = None
    if "general.parameter_count" in kv and kv["general.parameter_count"] is not None:
        try:
            param_count = int(kv["general.parameter_count"])
        except (ValueError, TypeError):
            pass
    # Context length: try the architecture-scoped key first, then legacy llama.context_length
    n_ctx_train = None
    if architecture:
        arch_ctx = kv.get(f"{architecture}.context_length")
        if arch_ctx is not None:
            try:
                n_ctx_train = int(arch_ctx)
            except (ValueError, TypeError):
                pass
    if n_ctx_train is None and "llama.context_length" in kv and kv["llama.context_length"] is not None:
        try:
            n_ctx_train = int(kv["llama.context_length"])
        except (ValueError, TypeError):
            pass

    # MTP detection from GGUF metadata, tensor table, and companion drafts
    supports_mtp = False
    if architecture:
        mtp_layers = kv.get(f"{architecture}.nextn_predict_layers")
        if mtp_layers is not None:
            try:
                if int(mtp_layers) > 0:
                    supports_mtp = True
            except (ValueError, TypeError):
                pass
    if not supports_mtp:
        gen_mtp = kv.get("general.nextn_predict_layers")
        if gen_mtp is not None:
            try:
                if int(gen_mtp) > 0:
                    supports_mtp = True
            except (ValueError, TypeError):
                pass
    if not supports_mtp and has_mtp_tensor:
        supports_mtp = True
    if not supports_mtp:
        from stet.llm.utils import _find_mtp_draft_model

        if _find_mtp_draft_model(path_str) is not None:
            supports_mtp = True
    info = GgufModelInfo(
        path=path_str,
        architecture=architecture,
        name=name,
        chat_template=chat_template,
        n_ctx_train=n_ctx_train,
        parameter_count=param_count,
        supports_mtp=supports_mtp,
        reasoning_capable=_is_reasoning_capable(chat_template),
        file_size=stat.st_size,
        mtime=stat.st_mtime,
    )
    log(
        f"[GGUF] Read metadata from {path_str}: arch={architecture!r} "
        f"name={name!r} n_ctx={n_ctx_train} params={param_count} mtp={supports_mtp} reasoning={info.reasoning_capable}"
    )
    return info


def get_gguf_info_cached(path) -> GgufModelInfo:
    """Like :func:`read_gguf_info` but cached per path.

    The cached result is returned while the file's (size, mtime) is
    unchanged; any change on disk triggers a re-read. Errors propagate as
    :class:`GgufReadError` and evict any stale cache entry.
    """
    path_str = os.fspath(path)
    try:
        stat = os.stat(path_str)
    except OSError as exc:
        _cache.pop(path_str, None)
        raise GgufReadError(f"GGUF file not found: {path_str}") from exc

    cached = _cache.get(path_str)
    if cached is not None and cached[0] == stat.st_size and cached[1] == stat.st_mtime:
        return cached[2]

    info = read_gguf_info(path_str)
    _cache[path_str] = (stat.st_size, stat.st_mtime, info)
    return info
