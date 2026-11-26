# PhD Radar

Every day, PhD Radar collects PhD offers from European and French sources and flags the ones that match your keywords. It publishes them as a filterable web page and an RSS feed.

| Source | What it covers |
|---|---|
| ADUM | Thesis topics from French doctoral schools: the network-wide listing (`adumR`) plus Paris-Saclay and IP Paris |
| ABG | French thesis-topic board (it mirrors many ADUM offers) |
| CNRS | Doctoral contracts on emploi.cnrs.fr |
| CEA | Thesis catalogue on instn.cea.fr |
| Inria | PhD positions on jobs.inria.fr |
| EURAXESS | EU-wide research jobs portal, searched by keyword |
| FindAPhD | Computer science, Europe |
| Academic Positions | PhD positions in computer science |

## Customising

Everything you'd normally change is in `config.yaml`:
- **keywords.include / exclude**: what counts as a match. Matching ignores case and accents, and short terms like `ai` only match whole words.
- **sources.*.enabled**: turn a source off.
- **adum.sites**: add more ADUM sites. The code is whatever comes after `site=` in a school's ADUM link, e.g. `ed3c` or `UPHF`.
- **euraxess.queries**, **findaphd.paths**, **academicpositions.paths**: what to search for on those sites.

Commit the change and the next run picks it up. To run immediately, open **Actions → PhD Radar → Run workflow**.

## How it works

- `radar/sources.py` has one small scraper per site. Each one finds links shaped like offer pages, so most site redesigns don't break it.
- Offers are stored in `data/offers.json` along with the date each was first seen. Offers that stop appearing for 45 days are removed.
- The page is `docs/index.html`, and the feed (`docs/feed.xml`) contains matching offers only. Both are deployed to GitHub Pages.
- If a source breaks, the others keep working. Open "Source status" at the top of the page to see which one failed and why.
- To debug a source, run the workflow with **debug** ticked. This saves the raw downloaded pages as a run artifact.

## Run locally

```bash
pip install -r requirements.txt
python -m radar.run            # all sources
python -m radar.run adum cnrs  # just some
open docs/index.html
```

## Etiquette

The scraper sends about one request every 2 seconds per site, once a day. ADUM's robots.txt asks automated crawlers not to fetch its pages. This tool reads a handful of public listing pages per day for personal use, but if you'd rather fully respect that, set `adum.enabled: false`. ABG mirrors many ADUM offers anyway.
