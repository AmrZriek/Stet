from pathlib import Path
import tarfile
import urllib.request

from stet.ui.downloader import DownloadProgressDialog, DownloadWorker, format_bytes


def test_format_bytes():
    assert format_bytes(500) == "500 B"
    assert format_bytes(1500) == "1.46 KB"
    assert format_bytes(2 * 1024 * 1024) == "2.00 MB"
    assert format_bytes(3 * 1024 * 1024 * 1024) == "3.00 GB"


def test_download_worker_init():
    downloads = [{"url": "http://example.com", "dest": Path("test.txt")}]
    worker = DownloadWorker(downloads)
    assert worker.downloads == downloads
    assert not worker._is_cancelled
    worker.cancel()
    assert worker._is_cancelled


def test_download_dialog_init(qtbot):
    downloads = [{"url": "http://example.com", "dest": Path("test.txt")}]
    dialog = DownloadProgressDialog(downloads)
    qtbot.addWidget(dialog)
    assert dialog._downloads == downloads
    # Clean shutdown
    dialog.reject()


def test_downloader_rejects_tar_symlinks(tmp_path, monkeypatch):
    tar_path = tmp_path / "malicious.tar"
    extract_dir = tmp_path / "extract"
    dest_path = tmp_path / "downloaded.tar"

    with tarfile.open(tar_path, "w") as tar:
        tinfo = tarfile.TarInfo(name="symlink_to_root")
        tinfo.type = tarfile.SYMTYPE
        tinfo.linkname = "/etc/passwd"
        tar.addfile(tinfo)

    tar_bytes = tar_path.read_bytes()

    class _Resp:
        def __init__(self, data):
            self._data = data
            self.headers = {"Content-Length": str(len(data))}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def getheader(self, name):
            return self.headers.get(name)

        def read(self, size=-1):
            if size < 0:
                d, self._data = self._data, b""
                return d
            d, self._data = self._data[:size], self._data[size:]
            return d

    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, timeout=15: _Resp(tar_bytes),
    )

    downloads = [{
        "url": "https://example.com/malicious.tar",
        "dest": dest_path,
        "extract_dir": str(extract_dir),
    }]

    worker = DownloadWorker(downloads)
    results = []

    def on_finished(success, msg):
        results.append((success, msg))

    worker.finished.connect(on_finished)
    worker.run()

    assert len(results) == 1
    success, msg = results[0]
    assert not success
    assert "Archive contains unsafe symlinks or hardlinks" in msg

