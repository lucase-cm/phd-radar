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


# ---------------------------------------------------------------- CEA (INSTN thesis catalogue, WordPress REST API)
NOTES: dict[str, str] = {}  # sources can leave a note for the status table


def cea(http: Http, cfg: dict) -> list[Offer]:
    """instn.cea.fr is WordPress; thesis topics are the public 'these' post type.
    The catalogue is emptied between campaigns (topics usually appear Dec-Jan), so 0 can be normal."""
    api = "https://instn.cea.fr/wp-json/wp/v2"
    themes = {}
    for page in range(1, 6):
        r = http.get(f"{api}/thematique-these?per_page=100&page={page}&_fields=id,name")
        if r.status_code != 200 or not r.json():
            break
        themes.update({t["id"]: clean(t["name"]) for t in r.json()})
    out = []
    for page in range(1, 30):
        r = http.get(f"{api}/these?per_page=100&page={page}"
                     "&_fields=id,date,modified,link,title,excerpt,content,thematique-these")
        if r.status_code != 200:
            break
        items = r.json()
        if not items:
            break
        for t in items:
            text = BeautifulSoup((t.get("excerpt") or {}).get("rendered", "") or
                                 (t.get("content") or {}).get("rendered", ""), "lxml").get_text(" ")
            theme = ", ".join(themes.get(i, "") for i in t.get("thematique-these", []))
            out.append(Offer(source="CEA", title=BeautifulSoup(t["title"]["rendered"], "lxml").get_text(),
                             url=t["link"], summary=(theme + " — " if theme else "") + text,
                             posted=t.get("date", "")[:10], institution="CEA", location="France",
                             uid=f"cea:{t['id']}"))
    if not out:
        NOTES["cea"] = "Catalogue currently empty (CEA usually publishes thesis topics in Dec–Jan)"
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
