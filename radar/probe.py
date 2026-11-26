"""Debug helper: download a list of URLs into debug/ so scrapers can be adjusted.
Usage: python -m radar.probe URL [URL...]"""
import os
import sys

from .core import Http

os.environ.setdefault("RADAR_DEBUG_DIR", "debug")
import radar.core as core  # noqa: E402

core.DEBUG_DIR = os.environ["RADAR_DEBUG_DIR"]

http = Http(1)
for u in sys.argv[1:]:
    try:
        r = http.get(u)
        print(r.status_code, len(r.content), u, "->", r.url)
    except Exception as e:
        print("ERR", u, e)
open(os.path.join(core.DEBUG_DIR, "_log.txt"), "w").write("\n".join(http.log))
