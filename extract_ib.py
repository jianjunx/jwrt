"""Stream-extract .targetinfo/.packageinfo/.config from the Kwrt ImageBuilder tar.zst."""
import io
import sys
import tarfile
import urllib.request

URL = "https://dl.openwrt.ai/releases/25.12/targets/x86/64/kwrt-imagebuilder-x86-64.Linux-x86_64.tar.zst"
WANT = {".targetinfo", ".packageinfo", "repositories.conf", ".config", "Makefile"}
OUT = "D:/projects/jwrt"

import zstandard

req = urllib.request.Request(URL, headers={"User-Agent": "curl/8"})
resp = urllib.request.urlopen(req, timeout=120)
dctx = zstandard.ZstdDecompressor()
reader = dctx.stream_reader(resp)

found = set()
buf = b""
# Read the zstd stream into incremental tar parsing via a file-like wrapper
class Tee(io.RawIOBase):
    def __init__(self):
        self.partial = bytearray()

    def readable(self):
        return True

    def readinto(self, b):
        data = reader.read(len(b))
        if not data:
            return 0
        b[: len(data)] = data
        self.partial.extend(data)
        return len(data)

fobj = io.BufferedReader(Tee(), buffer_size=1 << 20)
tar = tarfile.open(fileobj=fobj, mode="r|")
for m in tar:
    name = m.name.lstrip("./")
    base = name.split("/")[-1]
    if m.isfile() and base in WANT and name.count("/") <= 1:
        data = tar.extractfile(m).read()
        path = f"{OUT}/ib-{base}"
        with open(path, "wb") as fh:
            fh.write(data)
        print(f"extracted {name} -> {path} ({len(data)} bytes)")
        found.add(base)
        if found >= {".targetinfo", ".packageinfo"}:
            break
print("done, found:", sorted(found))
