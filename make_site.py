#!/usr/bin/env python3
"""
Génère le site "Paris Bot" (dossier site/) à partir de bot_paris.py.
Lancé chaque matin par GitHub Actions, puis publié sur GitHub Pages.
"""
import html
import os
import struct
import zlib
from datetime import datetime, timedelta, timezone

import bot_paris as bp

OUT = "site"
DAYS = 3
MIN_EDGE = 0.05
JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def benin_now():
    return datetime.now(timezone.utc) + timedelta(hours=1)  # Bénin = UTC+1


def collect(start, end):
    seasons = bp.current_seasons()
    fx = []
    for r in bp.read_rows(bp.fetch(f"{bp.BASE}/fixtures.csv", "fixtures.csv")):
        d = bp.parse_date(r.get("Date", ""))
        if d and start <= d <= end and r.get("HomeTeam"):
            fx.append(r)
    times = {(r["HomeTeam"], r["AwayTeam"]): (r.get("Time") or "") for r in fx}
    matches = []
    for div in sorted({r["Div"] for r in fx}):
        matches += bp.analyse_div(div, [r for r in fx if r["Div"] == div], seasons, MIN_EDGE)
    for m in matches:
        m["time"] = times.get((m["home"], m["away"]), "")
    return matches


def pct(x):
    return f"{x * 100:.0f}"


def who(m, label):
    return {"1": m["home"], "N": "Match nul", "2": m["away"]}[label]


def card(m):
    e = html.escape
    p = m["pred"]
    badges = "".join(
        f'<div class="value">VALUE · {e(who(m, l))} · cote {o:.2f} · avantage {ed * 100:+.0f}%</div>'
        for l, _p, o, ed in m["picks"])
    odds = ""
    if m["odds"]:
        odds = f'<div class="odds">Cotes 1 / N / 2 : {m["odds"][0]:.2f} · {m["odds"][1]:.2f} · {m["odds"][2]:.2f}</div>'
    when = f" · {e(m['time'])}" if m["time"] else ""
    return f"""<article class="card">
<div class="meta">{e(bp.LEAGUES.get(m['div'], m['div']))}{when}</div>
<h3>{e(m['home'])} <span>vs</span> {e(m['away'])}</h3>
<div class="bar"><i class="h" style="width:{p['p1'] * 100:.1f}%"></i><i class="x" style="width:{p['px'] * 100:.1f}%"></i><i class="a" style="width:{p['p2'] * 100:.1f}%"></i></div>
<div class="legend"><span>Dom. {pct(p['p1'])}%</span><span>Nul {pct(p['px'])}%</span><span>Ext. {pct(p['p2'])}%</span></div>
<div class="chips"><b>+2,5 buts {pct(p['over25'])}%</b><b>Les 2 marquent {pct(p['btts'])}%</b></div>
{odds}{badges}
</article>"""


def day_label(d, today):
    if d == today:
        return "Aujourd'hui"
    if d == today + timedelta(days=1):
        return "Demain"
    return f"{JOURS[d.weekday()][:3]}. {d.day} {MOIS[d.month - 1]}"


def day_section(d, matches, today, idx):
    e = html.escape
    ms = sorted([m for m in matches if m["date"] == d], key=lambda m: (m["div"], m["time"]))
    if not ms:
        body = '<p class="empty">Aucun match trouvé pour ce jour dans les championnats couverts.</p>'
        return f'<section class="day" id="d{idx}" hidden>{body}</section>'
    values = sorted(((pk[3], m, pk) for m in ms for pk in m["picks"]), key=lambda x: -x[0])[:6]
    if values:
        items = "".join(
            f'<li><b>{e(m["home"])} - {e(m["away"])}</b><br>{e(who(m, pk[0]))} · proba {pct(pk[1])}% · cote {pk[2]:.2f} · avantage {pk[3] * 100:+.0f}%</li>'
            for _, m, pk in values)
        top = f'<div class="box"><h2>Value bets</h2><ol>{items}</ol><small>Un avantage supérieur à 30 % est souvent une erreur du modèle : méfie-toi.</small></div>'
    else:
        top = '<div class="box"><h2>Value bets</h2><p>Aucun value bet ce jour-là : mieux vaut ne pas parier.</p></div>'
    fav = sorted(ms, key=lambda m: -max(m["pred"]["p1"], m["pred"]["px"], m["pred"]["p2"]))[:5]
    fitems = ""
    for m in fav:
        p = m["pred"]
        pr, name = max((p["p1"], m["home"]), (p["px"], "Nul"), (p["p2"], m["away"]))
        fitems += f'<li><b>{e(m["home"])} - {e(m["away"])}</b><br>{e(name)} {pct(pr)}%</li>'
    favbox = f'<div class="box"><h2>Favoris les plus nets</h2><ol>{fitems}</ol></div>'
    cards = "".join(card(m) for m in ms)
    return f'<section class="day" id="d{idx}" hidden>{top}{favbox}<h2 class="all">Tous les matchs ({len(ms)})</h2>{cards}</section>'


CSS = """
:root{--bg:#0b0e14;--card:#151a23;--tx:#eceff4;--mut:#8b95a7;--g:#2fbf71;--y:#ffd447;--r:#ff6b6b;--b:#4f9cff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.45 system-ui,-apple-system,Roboto,sans-serif;padding:0 14px 40px}
header{padding:18px 0 8px}h1{margin:0;font-size:22px;color:var(--y)}.upd{color:var(--mut);font-size:13px;margin-top:2px}
nav{display:flex;gap:8px;margin:10px 0 14px;position:sticky;top:0;background:var(--bg);padding:8px 0;z-index:2}
nav button{flex:1;padding:11px 4px;border:0;border-radius:10px;background:#1e2532;color:var(--tx);font-size:15px}
nav button.on{background:var(--g);color:#06210f;font-weight:700}
h2{font-size:17px;margin:0 0 8px}h2.all{margin:20px 0 10px}
.box{background:var(--card);border-radius:14px;padding:14px;margin-bottom:12px}.box ol{margin:0;padding-left:20px}.box li{margin-bottom:9px}.box small{color:var(--mut)}
.card{background:var(--card);border-radius:14px;padding:14px;margin-bottom:12px}
.meta{color:var(--mut);font-size:12.5px;margin-bottom:2px}h3{margin:0 0 10px;font-size:17px}h3 span{color:var(--mut);font-weight:400;font-size:13px}
.bar{display:flex;height:10px;border-radius:6px;overflow:hidden;background:#222}.bar i{display:block;height:100%}
.bar .h{background:var(--g)}.bar .x{background:#8b95a7}.bar .a{background:var(--b)}
.legend{display:flex;justify-content:space-between;font-size:14px;margin-top:6px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.chips b{background:#1e2532;border-radius:8px;padding:5px 9px;font-size:13px;font-weight:600}
.odds{color:var(--mut);font-size:13px;margin-top:9px}
.value{margin-top:8px;background:#3a2f0a;border:1px solid var(--y);color:var(--y);border-radius:8px;padding:6px 9px;font-size:13px;font-weight:600}
.empty{color:var(--mut);text-align:center;padding:30px 0}
footer{color:var(--mut);font-size:12.5px;margin-top:24px;border-top:1px solid #222;padding-top:14px}
"""

JS = """
const tabs=[...document.querySelectorAll('nav button')],days=[...document.querySelectorAll('.day')];
function show(i){tabs.forEach((t,k)=>t.classList.toggle('on',k===i));days.forEach((d,k)=>d.hidden=k!==i);try{localStorage.setItem('tab',i)}catch(e){}}
tabs.forEach((t,k)=>t.onclick=()=>show(k));show(0);
if('serviceWorker' in navigator)navigator.serviceWorker.register('sw.js').catch(()=>{});
"""

SW = """const C='parisbot-v1';
self.addEventListener('install',e=>{self.skipWaiting()});
self.addEventListener('activate',e=>e.waitUntil(clients.claim()));
self.addEventListener('fetch',e=>{if(e.request.method!=='GET')return;
e.respondWith(fetch(e.request).then(r=>{const cp=r.clone();caches.open(C).then(c=>c.put(e.request,cp));return r}).catch(()=>caches.match(e.request)))});
"""

MANIFEST = """{
 "name": "Paris Bot",
 "short_name": "Paris Bot",
 "start_url": "./",
 "scope": "./",
 "display": "standalone",
 "background_color": "#0b0e14",
 "theme_color": "#0b0e14",
 "lang": "fr",
 "icons": [
  {"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
  {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
 ]
}
"""


def make_png(size, path):
    bg, fg = (11, 94, 52, 255), (255, 212, 71, 255)
    cx = size / 2
    r2 = (size * 0.28) ** 2
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            raw += bytes(fg if (x - cx) ** 2 + (y - cx) ** 2 <= r2 else bg)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def main():
    now = benin_now()
    today = now.date()
    days = [today + timedelta(days=i) for i in range(DAYS)]
    try:
        matches = collect(days[0], days[-1])
    except Exception as ex:  # le site doit toujours se générer
        print("Erreur d'analyse :", ex)
        matches = []

    tabs = "".join(
        f'<button>{html.escape(day_label(d, today))}<br><small>{sum(1 for m in matches if m["date"] == d)} matchs</small></button>'
        for d in days)
    sections = "".join(day_section(d, matches, today, i) for i, d in enumerate(days))
    stamp = f"Mis à jour le {JOURS[now.weekday()].lower()} {now.day} {MOIS[now.month - 1]} à {now:%H:%M} (heure du Bénin)"

    page = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0b0e14"><title>Paris Bot</title>
<link rel="manifest" href="manifest.webmanifest"><link rel="icon" href="icon-192.png">
<link rel="apple-touch-icon" href="icon-192.png"><style>{CSS}</style></head><body>
<header><h1>⚽ Paris Bot</h1><div class="upd">{stamp}</div></header>
<nav>{tabs}</nav>{sections}
<footer>Analyse statistique (modèle de Poisson, données football-data.co.uk). Aucun modèle ne garantit un gain : les bookmakers gardent une marge et la plupart des parieurs perdent sur la durée. Ne mise que ce que tu peux te permettre de perdre. Jeu réservé aux adultes.</footer>
<script>{JS}</script></body></html>"""

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    with open(os.path.join(OUT, "manifest.webmanifest"), "w", encoding="utf-8") as f:
        f.write(MANIFEST)
    with open(os.path.join(OUT, "sw.js"), "w", encoding="utf-8") as f:
        f.write(SW)
    make_png(192, os.path.join(OUT, "icon-192.png"))
    make_png(512, os.path.join(OUT, "icon-512.png"))
    print(f"Site généré : {len(matches)} matchs sur {DAYS} jours -> {OUT}/")


if __name__ == "__main__":
    main()
