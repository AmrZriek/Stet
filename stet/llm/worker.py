import json
import threading

import requests
from PyQt6.QtCore import QThread, pyqtSignal


class StreamWorker(QThread):
    token = pyqtSignal(str)
    reasoning_token = pyqtSignal(str)
    done = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, url: str, payload: dict, watchdog_timeout: float = 20.0):
        super().__init__()
        self.url = url
        self.payload = {**payload, "stream": True}
        self.watchdog_timeout = watchdog_timeout
        self._stop = False
        self._timeout_aborted = False
        self._watchdog: threading.Timer | None = None

    def stop(self):
        self._stop = True
        self._cancel_watchdog()
        if hasattr(self, "_session"):
            try:
                self._session.close()
            except Exception:
                pass

    def abort_timeout(self):
        self._timeout_aborted = True
        self._cancel_watchdog()
        if hasattr(self, "_session"):
            try:
                self._session.close()
            except Exception:
                pass

    def _cancel_watchdog(self):
        if self._watchdog is not None:
            try:
                self._watchdog.cancel()
            except Exception:
                pass
            self._watchdog = None

    def run(self):
        if self._stop:
            return
        full = ""
        reasoning_full = ""
        self._session = requests.Session()
        if self.watchdog_timeout > 0:
            self._watchdog = threading.Timer(self.watchdog_timeout, self.abort_timeout)
            self._watchdog.daemon = True
            self._watchdog.start()
        try:
            with self._session.post(
                self.url, json=self.payload, stream=True, timeout=120
            ) as r:
                r.raise_for_status()
                for raw in r.iter_lines():
                    if self._stop or self._timeout_aborted:
                        break
                    if not raw:
                        continue
                    line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        self._cancel_watchdog()
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0].get("delta", {})
                        t = delta.get("content", "")
                        rt = delta.get("reasoning_content", "")
                        if t or rt:
                            self._cancel_watchdog()
                        if rt:
                            reasoning_full += rt
                            if self.payload.get("think", False) and not self._stop and not self._timeout_aborted:
                                self.reasoning_token.emit(rt)
                        if t:
                            full += t
                            if not self._stop and not self._timeout_aborted:
                                self.token.emit(t)
                    except Exception:
                        pass
            self._cancel_watchdog()
            if self._timeout_aborted:
                self.error.emit("StetStreamTimeout: engine unresponsive")
                return
            if not full and reasoning_full:
                if "</think>" in reasoning_full:
                    full = reasoning_full.split("</think>")[-1].strip()
                elif "<|im_end|>" in reasoning_full:
                    full = reasoning_full.split("<|im_end|>")[0].strip()
                else:
                    full = reasoning_full
            if not self._stop:
                self.done.emit(full)
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        ):
            if self._timeout_aborted:
                self.error.emit("StetStreamTimeout: engine unresponsive")
            elif not self._stop:
                self.error.emit("Stream connection closed unexpectedly.")
        except Exception as e:
            if self._timeout_aborted:
                self.error.emit("StetStreamTimeout: engine unresponsive")
            elif not self._stop:
                self.error.emit(str(e))
        finally:
            self._cancel_watchdog()
            try:
                self._session.close()
            except Exception:
                pass
