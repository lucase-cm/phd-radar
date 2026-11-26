"""Shared helpers: Offer model, polite HTTP client, link extraction, keyword matching."""
from __future__ import annotations

import hashlib
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field, asdict
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 phd-radar/1.0 (personal job-alert bot)"
)
DEBUG_DIR = os.environ.get("RADAR_DEBUG_DIR")  # if set, raw pages are saved here


@dataclass
class Offer:
    source: str
    title: str
    url: str
    institution: str = ""
    location: str = ""
    summary: str = ""
    deadline: str = ""
    posted: str = ""
    uid: str = ""
    # filled by the pipeline
    first_seen: str = ""
    last_seen: str = ""
    matched: list = field(default_factory=list)

    def __post_init__(self):
        self.title = clean(self.title)
        self.summary = clean(self.summary)[:600]
        self.institution = clean(self.institution)
        self.location = clean(self.location)
        if not self.uid:
            self.uid = self.source + ":" + hashlib.sha1(canon_url(self.url).encode()).hexdigest()[:16]

    def to_dict(self):
        return asdict(self)


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def canon_url(u: str) -> str:
    p = urlparse(u)
    return f"{p.netloc.lower().removeprefix('www.')}{p.path.rstrip('/')}?{p.query}"


class Http:
    def __init__(self, delay: float = 2.0):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en,fr;q=0.8",
        })
        self.delay = delay
        self._last: dict[str, float] = {}
        self.log: list[str] = []

    def get(self, url: str, **kw) -> requests.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> requests.Response:
        return self.request("POST", url, **kw)

    def request(self, method: str, url: str, **kw) -> requests.Response:
        host = urlparse(url).netloc
        wait = self.delay - (time.time() - self._last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        last_exc = None
        for attempt in range(3):
            try:
                r = self.s.request(method, url, timeout=40, **kw)
                self._last[host] = time.time()
                self.log.append(f"{r.status_code} {len(r.content):>8}B {method} {url}")
                if r.status_code in (429, 502, 503, 504):
                    time.sleep(5 * (attempt + 1))
                    continue
                self._dump(url, r)
                return r
            except requests.RequestException as e:
                last_exc = e
                self.log.append(f"ERR {url} {e}")
                time.sleep(3 * (attempt + 1))
        if last_exc:
            raise last_exc
        return r

    def soup(self, url: str, **kw) -> BeautifulSoup:
        r = self.get(url, **kw)
        r.raise_for_status()
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding
        return BeautifulSoup(r.text, "lxml")

    def try_soup(self, url: str, **kw) -> BeautifulSoup | None:
        """Like soup() but returns None on any failure (for pagination and fallbacks)."""
        try:
            return self.soup(url, **kw)
        except Exception as e:
            self.log.append(f"FAIL {url} {type(e).__name__}: {e}"[:300])
            return None

    def _dump(self, url: str, r: requests.Response):
        if not DEBUG_DIR:
            return
        os.makedirs(DEBUG_DIR, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9]+", "_", r.request.method + "_" + url + "_" + str(r.request.body or "")[:60])[:150]
        with open(os.path.join(DEBUG_DIR, name + ".html"), "wb") as f:
            f.write(r.content)


def links_matching(soup: BeautifulSoup, base: str, pattern: str):
    """Yield (absolute_url, anchor_tag) for <a> whose href matches `pattern`, deduped."""
    rx = re.compile(pattern)
    seen = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"])
        if rx.search(href):
            key = canon_url(href)
            if key in seen:
                continue
            seen.add(key)
            yield href, a


ROW_TAGS = {"tr", "li", "article", "dl"}


def card_text(a, max_up: int = 4, max_len: int = 700) -> str:
    """Text of the smallest ancestor 'card' around a link (for summaries / filtering).

    Stops at row-like elements and never climbs into a container holding other offer links,
    so one offer's summary can't absorb its neighbours."""
    node, best = a, clean(a.get_text(" "))
    href = a.get("href")
    for _ in range(max_up):
        parent = node.parent
        if parent is None or parent.name in ("body", "html", "table", "tbody", "ul", "ol"):
            break
        other = [x for x in parent.find_all("a", href=True) if x.get("href") != href
                 and len(clean(x.get_text(" "))) > 25]
        if other and parent.name not in ROW_TAGS:
            break
        node = parent
        best = clean(node.get_text(" "))
        if node.name in ROW_TAGS:
            break
    return best[:max_len]


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


class Matcher:
    def __init__(self, include: list[str], exclude: list[str]):
        self.inc = [(k, self._rx(k)) for k in include]
        self.exc = [(k, self._rx(k)) for k in exclude]

    @staticmethod
    def _rx(k: str):
        k = fold(k)
        return re.compile(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])")

    def match(self, *texts: str) -> list[str]:
        t = fold(" ".join(texts))
        if any(rx.search(t) for _, rx in self.exc):
            return []
        return [k for k, rx in self.inc if rx.search(t)]
