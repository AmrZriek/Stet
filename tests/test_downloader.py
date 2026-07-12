from pathlib import Path
from stet.ui.downloader import format_bytes, DownloadWorker, DownloadProgressDialog


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
