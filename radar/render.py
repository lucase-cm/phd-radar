"""Static outputs: a filterable HTML page and an RSS feed of matching offers."""
from __future__ import annotations

import json
from datetime import datetime
from email.utils import format_datetime
from html import escape
from pathlib import Path


def write_feed(path: Path, offers: list[dict], site_url: str, now: datetime):
    items = []
    for o in offers[:300]:
        pub = datetime.strptime(o["first_seen"], "%Y-%m-%d").replace(tzinfo=now.tzinfo)
        desc = " · ".join(x for x in [o["source"], o.get("institution"), o.get("location"),
                                       ("Deadline " + o["deadline"]) if o.get("deadline") else "",
                                       "Keywords: " + ", ".join(o["matched"])] if x)
        desc += "\n\n" + o.get("summary", "")
        items.append(
            "<item>"
            f"<title>{escape('[' + o['source'] + '] ' + o['title'])}</title>"
            f"<link>{escape(o['url'])}</link>"
            f"<guid isPermaLink=\"false\">{escape(o['uid'])}</guid>"
            f"<pubDate>{format_datetime(pub)}</pubDate>"
            f"<description>{escape(desc)}</description>"
            "</item>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
           "<title>PhD Radar — matching offers</title>"
           f"<link>{escape(site_url or 'https://github.com')}</link>"
           "<description>New PhD offers matching your keywords</description>"
           f"<lastBuildDate>{format_datetime(now)}</lastBuildDate>"
           + "".join(items) + "</channel></rss>\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(xml, encoding="utf-8")


def write_page(path: Path, offers: list[dict], status: list[dict], now: datetime):
    slim = [{k: o.get(k, "") for k in ("source", "title", "url", "institution", "location",
                                       "summary", "deadline", "first_seen", "matched")} for o in offers]
    data = json.dumps({"offers": slim, "status": status, "run_at": now.strftime("%Y-%m-%d %H:%M UTC")},
                      ensure_ascii=False).replace("</", "<\\/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE.replace("__DATA__", data), encoding="utf-8")


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PhD Radar</title>
<link rel="alternate" type="application/rss+xml" title="PhD Radar" href="feed.xml">
<style>
:root{--bg:#f7f6f2;--card:#fff;--ink:#1d1d1b;--muted:#6b6a65;--line:#e4e2db;--accent:#2f5d50;--new:#c2410c;--chip:#eef2ef}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1e1e1c;--ink:#ecebe6;--muted:#9a988f;--line:#33322e;--accent:#7fb8a4;--new:#fb923c;--chip:#24302b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header{padding:28px 20px 8px;max-width:1100px;margin:auto}h1{margin:0;font-size:26px;letter-spacing:-.01em}
.sub{color:var(--muted);margin-top:4px}.sub a{color:var(--accent)}
.bar{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--line)}
.bar-in{max-width:1100px;margin:auto;padding:12px 20px;display:flex;flex-wrap:wrap;gap:10px;align-items:center}
input[type=search]{flex:1 1 260px;padding:9px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);font:inherit}
select,label.tg{padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);font:inherit;cursor:pointer}
label.tg input{margin-right:6px}
main{max-width:1100px;margin:auto;padding:14px 20px 60px}
.count{color:var(--muted);margin:4px 0 12px}
.o{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:13px 15px;margin-bottom:9px}
.o a.t{color:var(--ink);font-weight:600;text-decoration:none}.o a.t:hover{color:var(--accent);text-decoration:underline}
.meta{display:flex;flex-wrap:wrap;gap:6px 12px;color:var(--muted);font-size:13px;margin-top:5px}
.src{font-weight:600;color:var(--accent)}.nw{color:var(--new);font-weight:700}
.kw{display:inline-block;background:var(--chip);border-radius:99px;padding:1px 8px;margin-right:4px;font-size:12px;color:var(--ink)}
.sum{color:var(--muted);font-size:13px;margin-top:6px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
details{max-width:1100px;margin:0 auto;padding:0 20px}summary{cursor:pointer;color:var(--muted)}
table{border-collapse:collapse;margin:8px 0 14px;font-size:13px}td,th{padding:4px 10px;border-bottom:1px solid var(--line);text-align:left}
.bad{color:var(--new)}
</style></head><body>
<header><h1>PhD Radar</h1>
<div class="sub">CS / AI PhD offers across Europe · updated <span id="run"></span> · <a href="feed.xml">RSS feed</a></div></header>
<details id="st"><summary>Source status</summary><table id="stt"></table></details>
<div class="bar"><div class="bar-in">
<input id="q" type="search" placeholder="Filter: e.g. NLP, Saclay, robotics…">
<select id="src"><option value="">All sources</option></select>
<select id="age"><option value="0">Any time</option><option value="1">New today</option><option value="7" selected>Last 7 days</option><option value="30">Last 30 days</option></select>
<label class="tg"><input id="m" type="checkbox" checked>Keyword matches only</label>
</div></div>
<main><div class="count" id="cnt"></div><div id="list"></div></main>
<script>
const D=__DATA__;
const $=s=>document.querySelector(s);
$('#run').textContent=D.run_at;
const srcs=[...new Set(D.offers.map(o=>o.source))].sort();
srcs.forEach(s=>{const o=document.createElement('option');o.value=s;o.textContent=s;$('#src').append(o)});
$('#stt').innerHTML='<tr><th>Source</th><th>Offers</th><th>New</th><th>Matches</th><th>Status</th></tr>'+
 D.status.map(s=>`<tr><td>${s.source}</td><td>${s.count}</td><td>${s.new}</td><td>${s.matched}</td><td class="${s.ok?'':'bad'}">${s.ok?'OK':esc(s.error)}</td></tr>`).join('');
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function fold(s){return (s||'').normalize('NFD').replace(/[̀-ͯ]/g,'').toLowerCase()}
const today=new Date().toISOString().slice(0,10);
function daysAgo(d){return Math.round((Date.parse(today)-Date.parse(d))/864e5)}
try{const p=JSON.parse(localStorage.getItem('radar')||'{}');for(const k in p){const el=$('#'+k);if(el){el.type==='checkbox'?el.checked=p[k]:el.value=p[k]}}}catch(e){}
function draw(){
 const q=fold($('#q').value).split(/\s+/).filter(Boolean),src=$('#src').value,age=+$('#age').value,m=$('#m').checked;
 try{localStorage.setItem('radar',JSON.stringify({q:$('#q').value,src,age,m}))}catch(e){}
 const rows=D.offers.filter(o=>(!m||o.matched.length)&&(!src||o.source===src)&&(!age||daysAgo(o.first_seen)<age)&&
   q.every(w=>fold(o.title+' '+o.summary+' '+o.institution+' '+o.location+' '+o.source).includes(w)));
 $('#cnt').textContent=rows.length+' offer'+(rows.length===1?'':'s');
 $('#list').innerHTML=rows.slice(0,800).map(o=>{const a=daysAgo(o.first_seen);
  return `<div class="o"><a class="t" href="${esc(o.url)}" target="_blank" rel="noopener">${esc(o.title)}</a>
  <div class="meta"><span class="src">${esc(o.source)}</span>${o.institution?`<span>${esc(o.institution)}</span>`:''}${o.location?`<span>${esc(o.location)}</span>`:''}
  ${o.deadline?`<span>Deadline ${esc(o.deadline)}</span>`:''}<span class="${a<=1?'nw':''}">${a<=1?'NEW · ':''}seen ${o.first_seen}</span>
  <span>${o.matched.map(k=>`<span class="kw">${esc(k)}</span>`).join('')}</span></div>
  ${o.summary&&o.summary!==o.title?`<div class="sum">${esc(o.summary)}</div>`:''}</div>`}).join('');
}
['q','src','age','m'].forEach(id=>$('#'+id).addEventListener('input',draw));draw();
</script></body></html>
"""
