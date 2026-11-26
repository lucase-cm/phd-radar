"""One function per source. Each takes (http, cfg) and returns a list of Offers.

Sites change their HTML from time to time. Every scraper here works by finding
links whose URL looks like an offer page, so a redesign usually doesn't break it.
If one source fails, the others still run and the page reports which one failed.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus, urljoin

import feedparser
from bs4 import BeautifulSoup

from .core import Http, Offer, card_text, clean, links_matching

PHD_RX = re.compile(r"\b(ph\.?\s?d|doctora|doctoral|th[eè]se|thesis|doktorand|promovend)", re.I)


def is_phd(*texts: str) -> bool:
    return bool(PHD_RX.search(" ".join(texts)))


# ---------------------------------------------------------------- ADUM (French doctoral schools)
def adum(http: Http, cfg: dict) -> list[Offer]:
    out: dict[str, Offer] = {}
    for site in cfg.get("sites", ["adumR"]):
        for lang_page in ("propositionFR.pl", "proposition.pl"):
            url = f"https://adum.fr/as/ed/{lang_page}?site={site}"
            try:
                soup = http.try_soup(url)
                if soup is None:
                    break
            except Exception:
                continue
            found = 0
            for href, a in links_matching(soup, url, r"voirproposition\.pl.*matricule_prop=\d+"):
                mid = re.search(r"matricule_prop=(\d+)", href).group(1)
                title = clean(a.get_text(" "))
                if len(title) < 8:  # link is an icon/"voir" button: use surrounding text
                    title = card_text(a, 3, 250)
                if mid in out:
                    continue
                out[mid] = Offer(
                    source="ADUM", title=title,
                    url=f"https://adum.fr/as/ed/voirproposition.pl?site={site}&matricule_prop={mid}",
                    summary=card_text(a), institution=f"ADUM · {site}", location="France",
                    uid=f"adum:{mid}",
                )
                found += 1
            if found:
                break  # FR page worked for this site; no need for the other variant
    return list(out.values())


# ---------------------------------------------------------------- ABG (French thesis-topic board, mirrors many ADUM offers)
ABG = "https://emploi.abg.asso.fr"


def abg(http: Http, cfg: dict) -> list[Offer]:
    """ABG's listing is filled by a form POST to /fr/candidatOffres/recherche.
    Offer type 2 = 'Sujet de Thèse'. One search per keyword, newest first."""
    out: dict[str, Offer] = {}
    http.try_soup(f"{ABG}/fr/candidatOffres")  # get a session cookie, like a browser would
    for kw in cfg.get("search", [""]):
        for page in range(1, cfg.get("max_pages", 3) + 1):
            form = {"page": str(page), "orderBy": "updated_at_desc", "autoload": "0",
                    "criteria[mot_cle]": kw, "criteria[id_type_offre]": "2", "reset_all": "0"}
            try:
                r = http.post(f"{ABG}/fr/candidatOffres/recherche", data=form,
                              headers={"X-Requested-With": "XMLHttpRequest",
                                       "Referer": f"{ABG}/fr/candidatOffres"})
                r.raise_for_status()
            except Exception:
                break
            soup = BeautifulSoup(r.text, "lxml")
            n = 0
            for href, a in links_matching(soup, ABG + "/fr/", r"/candidatOffres/show/id_offre/\d+"):
                oid = re.search(r"id_offre/(\d+)", href).group(1)
                title = clean(a.get_text(" "))
                if oid in out or len(title) < 8:
                    continue
                ctx = card_text(a, 5, 900)
                m = re.search(r"(\d{2}/\d{2}/\d{4})", ctx)
                out[oid] = Offer(source="ABG", title=title, url=f"{ABG}/fr/candidatOffres/show/id_offre/{oid}",
                                 summary=ctx, posted=m.group(1) if m else "", uid=f"abg:{oid}")
                n += 1
            if n == 0:
                break
    return list(out.values())


# ---------------------------------------------------------------- CNRS
def cnrs(http: Http, cfg: dict) -> list[Offer]:
    out: dict[str, Offer] = {}
    # 1) RSS (the per-unit endpoint; without a unit code it may return everything)
    for feed_url in ("https://emploi.cnrs.fr/Rss/Offres.ashx",
                     "https://emploi.cnrs.fr/Rss/Offres.ashx?lang=EN"):
        try:
            r = http.get(feed_url)
            f = feedparser.parse(r.content)
        except Exception:
            continue
        for e in f.entries:
            title, link = e.get("title", ""), e.get("link", "")
            if not link or not is_phd(title, link, e.get("summary", "")):
                continue
            ref = (re.search(r"/Offres/\w+/([A-Z0-9-]+)/", link) or re.search(r"(.*)", link)).group(1)
            out.setdefault(ref, Offer(source="CNRS", title=title, url=link,
                                      summary=re.sub("<[^>]+>", " ", e.get("summary", "")),
                                      posted=e.get("published", "")[:16], location="France",
                                      uid=f"cnrs:{ref}"))
        if out:
            return list(out.values())
    # 2) HTML fallback: doctoral-contract listing pages
    for url in ("https://emploi.cnrs.fr/Offres/Recherche.aspx?lang=EN",
                "https://emploi.cnrs.fr/Offres/Doctorant.aspx?lang=EN",
                "https://emploi.cnrs.fr/Bassins/non-determine/Offres.aspx?lang=EN"):
        try:
            soup = http.try_soup(url)
            if soup is None:
                break
        except Exception:
            continue
        for href, a in links_matching(soup, url, r"/Offres/Doctorant/[A-Za-z0-9-]+/Default\.aspx"):
            ref = re.search(r"/Doctorant/([A-Za-z0-9-]+)/", href).group(1)
            out.setdefault(ref, Offer(source="CNRS", title=a.get_text(" "), url=href,
                                      summary=card_text(a), location="France", uid=f"cnrs:{ref}"))
    return list(out.values())


# ---------------------------------------------------------------- CEA (INSTN thesis catalogue, WordPress)
def cea(http: Http, cfg: dict) -> list[Offer]:
    """The thesis pages are listed in the site's public sitemap (with last-modified dates).
    Titles come from the URL slug; the most recent pages are opened to get the real title and text."""
    idx = http.get("https://instn.cea.fr/sitemap_index.xml")
    idx.raise_for_status()
    maps = [u for u in re.findall(r"<loc>([^<]+)</loc>", idx.text) if re.search(r"/these-sitemap", u)]
    entries = []
    for sm in maps:
        r = http.get(sm)
        if r.status_code != 200:
            continue
        for block in re.findall(r"<url>(.*?)</url>", r.text, re.S):
            loc = re.search(r"<loc>([^<]+)</loc>", block)
            mod = re.search(r"<lastmod>([^<]+)</lastmod>", block)
            if loc:
                entries.append((mod.group(1)[:10] if mod else "", loc.group(1).strip()))
    entries.sort(reverse=True)
    max_age = cfg.get("max_age_days", 300)
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=max_age)).isoformat()
    entries = [(m, u) for m, u in entries if not m or m >= cutoff]
    # keep one language version per slug (prefer English)
    by_slug: dict[str, tuple] = {}
    for m, u in entries:
        slug = u.rstrip("/").rsplit("/", 1)[-1].removesuffix("-en")
        if slug not in by_slug or "/en/" in u:
            by_slug[slug] = (m, u)
    out = []
    details_left = cfg.get("max_details", 60)
    for slug, (m, u) in sorted(by_slug.items(), key=lambda kv: kv[1][0], reverse=True):
        title, summary = slug.replace("-", " ").capitalize(), ""
        if details_left > 0:
            details_left -= 1
            soup = http.try_soup(u)
            if soup is not None:
                h1 = soup.find("h1")
                title = clean(h1.get_text(" ")) if h1 else title
                main = soup.find("main") or soup.find("article") or soup.body
                summary = clean(main.get_text(" "))[:900] if main else ""
        out.append(Offer(source="CEA", title=title, url=u, summary=summary, posted=m,
                         institution="CEA", location="France", uid=f"cea:{slug}"))
    return out


# ---------------------------------------------------------------- Inria
def inria(http: Http, cfg: dict) -> list[Offer]:
    out: dict[str, Offer] = {}
    bases = ["https://jobs.inria.fr/public/classic/en/offres",
             "https://jobs.inria.fr/public/classic/fr/offres"]
    for base in bases:
        for page in range(1, 11):
            url = base if page == 1 else f"{base}?page={page}"
            try:
                soup = http.try_soup(url)
                if soup is None:
                    break
            except Exception:
                break
            n = 0
            for href, a in links_matching(soup, url, r"/public/classic/(en|fr)/offres/\d{4}-\d+"):
                ref = re.search(r"offres/(\d{4}-\d+)", href).group(1)
                t = clean(a.get_text(" "))
                if ref in out or len(t) < 8:
                    continue
                ctx = card_text(a)
                if not is_phd(t, ctx):
                    continue
                out[ref] = Offer(source="Inria", title=t, url=href, summary=ctx,
                                 institution="Inria", location="France", uid=f"inria:{ref}")
                n += 1
            if n == 0 and page > 1:
                break
        if out:
            break
    return list(out.values())


# ---------------------------------------------------------------- EURAXESS
def euraxess(http: Http, cfg: dict) -> list[Offer]:
    out: dict[str, Offer] = {}
    for q in cfg.get("queries", ["computer science"]):
        for page in range(cfg.get("max_pages", 2)):
            url = (f"https://euraxess.ec.europa.eu/jobs/search?keywords={quote_plus('PhD ' + q)}"
                   f"&sort%5Bname%5D=created&sort%5Bdirection%5D=DESC&page={page}")
            soup = http.try_soup(url)
            if soup is None:
                break
            n = 0
            for href, a in links_matching(soup, url, r"euraxess\.ec\.europa\.eu/jobs/\d+$"):
                jid = re.search(r"/jobs/(\d+)", href).group(1)
                t = clean(a.get_text(" "))
                if jid in out or len(t) < 8:
                    continue
                ctx = card_text(a, 5, 900)
                if not is_phd(t, ctx):
                    continue
                dl = re.search(r"(?:Deadline|Application Deadline)[:\s]*([0-9]{1,2} \w+ \d{4}|\d{4}-\d{2}-\d{2})", ctx)
                out[jid] = Offer(source="EURAXESS", title=t, url=href, summary=ctx,
                                 deadline=dl.group(1) if dl else "", uid=f"euraxess:{jid}")
                n += 1
            if n == 0:
                break
    return list(out.values())


# ---------------------------------------------------------------- FindAPhD
def findaphd(http: Http, cfg: dict) -> list[Offer]:
    out: dict[str, Offer] = {}
    for path in cfg.get("paths", ["/phds/computer-science/"]):
        for page in range(1, cfg.get("max_pages", 2) + 1):
            url = urljoin("https://www.findaphd.com", path) + (f"?PG={page}" if page > 1 else "")
            soup = http.try_soup(url)
            if soup is None:
                break
            n = 0
            for href, a in links_matching(soup, url, r"/phds/project/[^/]+/\?p\d+"):
                pid = re.search(r"\?p(\d+)", href).group(1)
                t = clean(a.get_text(" "))
                if pid in out or len(t) < 8:
                    continue
                out[pid] = Offer(source="FindAPhD", title=t, url=href.split("&")[0],
                                 summary=card_text(a, 5, 900), uid=f"findaphd:{pid}")
                n += 1
            if n == 0:
                break
    return list(out.values())


# ---------------------------------------------------------------- Academic Positions
def academicpositions(http: Http, cfg: dict) -> list[Offer]:
    out: dict[str, Offer] = {}
    for path in cfg.get("paths", ["/jobs/position/phd/field/computer-science"]):
        for page in range(1, cfg.get("max_pages", 2) + 1):
            url = urljoin("https://academicpositions.com", path) + (f"?page={page}" if page > 1 else "")
            soup = http.try_soup(url)
            if soup is None:
                break
            n = 0
            for href, a in links_matching(soup, url, r"academicpositions\.com/ad/[^?#]+"):
                t = clean(a.get_text(" "))
                if href in out or len(t) < 8:
                    continue
                out[href] = Offer(source="Academic Positions", title=t, url=href,
                                  summary=card_text(a, 5, 900))
                n += 1
            if n == 0:
                break
    return list(out.values())


SOURCES = {
    "adum": adum, "abg": abg, "cnrs": cnrs, "cea": cea, "inria": inria,
    "euraxess": euraxess, "findaphd": findaphd, "academicpositions": academicpositions,
}
