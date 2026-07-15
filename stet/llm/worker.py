import json

import requests
from PyQt6.QtCore import QThread, pyqtSignal


class StreamWorker(QThread):
    token = pyqtSignal(str)
    done = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, url: str, payload: dict):
        super().__init__()
        self.url = url
        self.payload = {**payload, "stream": True}
        self._stop = False

    def stop(self):
        self._stop = True
        if hasattr(self, "_session"):
            try:
                self._session.close()
            except Exception:
                pass

    def run(self):
        if self._stop:
            return
        full = ""
        self._session = requests.Session()
        try:
            with self._session.post(
                self.url, json=self.payload, stream=True, timeout=120
            ) as r:
                r.raise_for_status()
                for raw in r.iter_lines():
                    if self._stop:
                        break
                    if not raw:
                        continue
                    line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        t = chunk["choices"][0]["delta"].get("content", "")
                        if t:
                            full += t
                            if not self._stop:
                                self.token.emit(t)
                    except Exception:
                        pass
            if not self._stop:
                self.done.emit(full)
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        ):
            if not self._stop:
                self.error.emit("Stream connection closed unexpectedly.")
        except Exception as e:
            if not self._stop:
                self.error.emit(str(e))
        finally:
            try:
                self._session.close()
            except Exception:
                pass
