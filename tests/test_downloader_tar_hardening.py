import urllib.request
import tarfile
from stet.ui.downloader import DownloadWorker

def test_downloader_rejects_tar_symlinks(tmp_path, monkeypatch):
    # Create a tar file with a symlink
    tar_path = tmp_path / "malicious.tar"
    extract_dir = tmp_path / "extract"
    dest_path = tmp_path / "downloaded.tar"
    
    with tarfile.open(tar_path, "w") as tar:
        # We can just create a TarInfo object that is a symlink
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
        lambda req, timeout=15: _Resp(tar_bytes)
    )

    downloads = [{
        "url": "https://example.com/malicious.tar",
        "dest": dest_path,
        "extract_dir": str(extract_dir)
    }]
    
    worker = DownloadWorker(downloads)
    
    results = []
    def on_finished(success, msg):
        results.append((success, msg))
        
    worker.finished.connect(on_finished)
    
    worker.run() # Call run directly to avoid QThread event loop issues in tests
    
    assert len(results) == 1
    success, msg = results[0]
    
    assert not success
    assert "Archive contains unsafe symlinks or hardlinks" in msg
