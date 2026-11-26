"""One function per source. Each takes (http, cfg) and returns a list of Offers.

Sites change their HTML from time to time. Every scraper here works by finding
links whose URL looks like an offer page, so a redesign usually doesn't break it.
If one source fails, the others still run and the page reports which one failed.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus, urljoin

import feedparser

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
def abg(http: Http, cfg: dict) -> list[Offer]:
    out: dict[str, Offer] = {}
    for cat in cfg.get("categories", ["informatique"]):
        for page in range(1, cfg.get("max_pages", 3) + 1):
            url = f"https://www.abg.asso.fr/fr/recrutement/sujet-de-these/{cat}"
            if page > 1:
                url += f"?page={page}"
            soup = http.try_soup(url)
            if soup is None:
                break
            n = 0
            for href, a in links_matching(soup, url, r"/candidatOffres/show/id_offre/\d+"):
                oid = re.search(r"id_offre/(\d+)", href).group(1)
                title = clean(a.get_text(" "))
                if oid in out or len(title) < 8:
                    continue
                ctx = card_text(a, 5, 900)
                m = re.search(r"Ref\. ABG-\d+\s+(\d{2}/\d{2}/\d{4})", ctx)
                out[oid] = Offer(source="ABG", title=title, url=href, summary=ctx,
                                 posted=m.group(1) if m else "", location="France",
                                 uid=f"abg:{oid}")
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
    out: dict[str, Offer] = {}
    # WordPress sites expose RSS for any listing; try the thesis archive feeds first.
    for feed_url in ("https://instn.cea.fr/en/these/feed/", "https://instn.cea.fr/these/feed/",
                     "https://instn.cea.fr/en/feed/?post_type=these", "https://instn.cea.fr/feed/?post_type=these"):
        for page in range(1, 6):
            u = feed_url + (("&" if "?" in feed_url else "?") + f"paged={page}" if page > 1 else "")
            try:
                f = feedparser.parse(http.get(u).content)
            except Exception:
                break
            if not f.entries:
                break
            for e in f.entries:
                link = e.get("link", "")
                if "/these/" not in link and "these" not in link:
                    continue
                out.setdefault(link, Offer(source="CEA", title=e.get("title", ""), url=link,
                                           summary=re.sub("<[^>]+>", " ", e.get("summary", "")),
                                           posted=e.get("published", "")[:16], institution="CEA",
                                           location="France"))
        if out:
            return list(out.values())
    # HTML fallback: thematic listing pages
    for url in ("https://instn.cea.fr/en/thematique-these/mathematics-information-scientific-software-en/",
                "https://instn.cea.fr/en/thematique-these/technological-challenges-en/",
                "https://instn.cea.fr/en/these/"):
        for page in range(1, 6):
            u = url if page == 1 else f"{url}page/{page}/"
            try:
                soup = http.try_soup(u)
                if soup is None:
                    break
            except Exception:
                break
            n = 0
            for href, a in links_matching(soup, u, r"instn\.cea\.fr/(en/)?these/[^/?#]+/?$"):
                t = clean(a.get_text(" "))
                if len(t) < 10 or href in out:
                    continue
                out[href] = Offer(source="CEA", title=t, url=href, summary=card_text(a),
                                  institution="CEA", location="France")
                n += 1
            if n == 0:
                break
    return list(out.values())


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
