"""Entry point: python -m radar.run"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .core import Http, Matcher, Offer
from .render import write_feed, write_page
from .sources import NOTES, SOURCES

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "offers.json"
STATUS = ROOT / "data" / "status.json"


def main(only: list[str] | None = None) -> int:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    kw = cfg.get("keywords", {})
    matcher = Matcher(kw.get("include", []), kw.get("exclude", []))
    http = Http(cfg.get("delay_seconds", 2))
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    store: dict[str, dict] = json.loads(DATA.read_text()) if DATA.exists() else {}
    status = []

    for name, fn in SOURCES.items():
        scfg = cfg.get("sources", {}).get(name, {})
        if not scfg.get("enabled", False) or (only and name not in only):
            continue
        t0 = time.time()
        try:
            offers: list[Offer] = fn(http, scfg)
            new = 0
            for o in offers:
                o.matched = matcher.match(o.title, o.summary)
                prev = store.get(o.uid)
                d = o.to_dict()
                d["first_seen"] = prev["first_seen"] if prev else today
                d["last_seen"] = today
                if not prev:
                    new += 1
                store[o.uid] = d
            status.append({"source": name, "ok": bool(offers), "count": len(offers), "new": new,
                           "matched": sum(1 for o in offers if o.matched),
                           "seconds": round(time.time() - t0, 1),
                           "error": "" if offers else NOTES.get(name, "0 offers found (site layout may have changed or blocked us)"),
                           "note": NOTES.get(name, "")})
        except Exception as e:  # one broken source must not stop the others
            traceback.print_exc()
            status.append({"source": name, "ok": False, "count": 0, "new": 0, "matched": 0,
                           "seconds": round(time.time() - t0, 1), "error": f"{type(e).__name__}: {e}"[:300]})
        print(f"[{name}] {status[-1]}", flush=True)

    # Expire offers not seen for a while (only for sources that worked this run)
    ok_sources = {s["source"] for s in status if s["ok"]}
    cutoff = (now - timedelta(days=cfg.get("expire_after_days", 45))).strftime("%Y-%m-%d")
    for uid in list(store):
        src = uid.split(":")[0]
        if src in ok_sources and store[uid]["last_seen"] < cutoff:
            del store[uid]

    DATA.parent.mkdir(exist_ok=True)
    DATA.write_text(json.dumps(store, ensure_ascii=False, indent=1, sort_keys=True))
    STATUS.write_text(json.dumps({"run_at": now.isoformat(timespec="minutes"), "sources": status,
                                  "http_log": http.log[-400:]}, ensure_ascii=False, indent=1))

    offers = sorted(store.values(), key=lambda d: (d["first_seen"], d["uid"]), reverse=True)
    site_url = os.environ.get("SITE_URL", "")
    write_page(ROOT / "docs" / "index.html", offers, status, now)
    write_feed(ROOT / "docs" / "feed.xml", [o for o in offers if o["matched"]], site_url, now)
    print(f"Total offers stored: {len(store)}; matched: {sum(1 for o in offers if o['matched'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or None))
