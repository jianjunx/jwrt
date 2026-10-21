"""Stream-extract /usr/lib/opkg/status from the CI targz rootfs."""
import io
import tarfile
import urllib.request

URL = "https://dl.openwrt.ai/releases/25.12/targets/x86/64/kwrt-10.08.2026-x86-64-generic-targz-rootfs.tar.gz"
OUT = "D:/projects/jwrt/ci-rootfs-opkg-status"

req = urllib.request.Request(URL, headers={"User-Agent": "curl/8"})
resp = urllib.request.urlopen(req, timeout=120)
fobj = io.BufferedReader(resp, buffer_size=1 << 20)
tar = tarfile.open(fileobj=fobj, mode="r|gz")

found = False
for m in tar:
    name = m.name.lstrip("./")
    if name == "usr/lib/opkg/status" and m.isfile():
        data = tar.extractfile(m).read()
        with open(OUT, "wb") as fh:
            fh.write(data)
        print(f"extracted {name} -> {OUT} ({len(data)} bytes)")
        found = True
        break
if not found:
    print("status file not found")
