#!/usr/bin/env python3
"""
Génère le site "Paris Bot" (dossier site/).

Sources :
  - ESPN (API publique) : TOUS les championnats du jour, scores en direct, forme,
    classements, compositions, absents, face-à-face, cotes quand elles existent.
  - football-data.co.uk (via bot_paris.py) : historique de 2 saisons + cotes moyennes
    pour ~22 championnats européens (modèle de Poisson).

Les probabilités viennent du modèle, mélangées aux cotes du marché quand elles existent
(ça évite les "value bets" absurdes). Chaque erreur de source est ignorée : le site
se génère toujours, avec ce qui est disponible.
"""
import difflib
import json
import os
import re
import ssl
import struct
import unicodedata
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import bot_paris as bp

OUT = "site"
DAYS = 3
ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer"
ESPN_V2 = "https://site.api.espn.com/apis/v2/sports/soccer"
JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]

# Championnats connus (utilisés si la liste globale d'ESPN est indisponible, et pour l'ordre d'affichage)
SLUGS = {
    "uefa.champions": "Ligue des champions", "uefa.europa": "Ligue Europa", "uefa.europa.conf": "Ligue Conférence",
    "eng.1": "Angleterre - Premier League", "eng.2": "Angleterre - Championship", "eng.3": "Angleterre - League One",
    "eng.4": "Angleterre - League Two", "eng.fa": "Angleterre - FA Cup", "eng.league_cup": "Angleterre - Carabao Cup",
    "esp.1": "Espagne - LaLiga", "esp.2": "Espagne - LaLiga 2", "esp.copa_del_rey": "Espagne - Copa del Rey",
    "ger.1": "Allemagne - Bundesliga", "ger.2": "Allemagne - 2. Bundesliga", "ger.dfb_pokal": "Allemagne - DFB-Pokal",
    "ita.1": "Italie - Serie A", "ita.2": "Italie - Serie B", "ita.coppa_italia": "Italie - Coppa Italia",
    "fra.1": "France - Ligue 1", "fra.2": "France - Ligue 2", "fra.coupe_de_france": "France - Coupe de France",
    "por.1": "Portugal - Liga", "ned.1": "Pays-Bas - Eredivisie", "bel.1": "Belgique - Pro League",
    "tur.1": "Turquie - Süper Lig", "gre.1": "Grèce - Super League", "sco.1": "Écosse - Premiership",
    "rus.1": "Russie - Premier League", "ukr.1": "Ukraine - Premier League", "aut.1": "Autriche - Bundesliga",
    "sui.1": "Suisse - Super League", "den.1": "Danemark - Superliga", "swe.1": "Suède - Allsvenskan",
    "nor.1": "Norvège - Eliteserien", "pol.1": "Pologne - Ekstraklasa", "cze.1": "Tchéquie - Liga",
    "bra.1": "Brésil - Série A", "arg.1": "Argentine - Liga", "col.1": "Colombie - Liga", "chi.1": "Chili - Primera",
    "usa.1": "États-Unis - MLS", "mex.1": "Mexique - Liga MX", "conmebol.libertadores": "Copa Libertadores",
    "conmebol.sudamericana": "Copa Sudamericana", "concacaf.champions": "Concacaf Champions Cup",
    "jpn.1": "Japon - J1 League", "kor.1": "Corée du Sud - K League 1", "chn.1": "Chine - Super League",
    "aus.1": "Australie - A-League", "sau.1": "Arabie saoudite - Pro League", "uae.1": "Émirats - Pro League",
    "caf.champions": "Ligue des champions CAF", "caf.confed": "Coupe de la Confédération CAF",
    "egy.1": "Égypte - Premier League", "mar.1": "Maroc - Botola", "rsa.1": "Afrique du Sud - Premiership",
    "afc.champions": "Ligue des champions AFC", "fifa.friendly": "Matchs amicaux", "uefa.nations": "Ligue des nations",
}
ORDER = {s: i for i, s in enumerate(SLUGS)}


# ------------------------------------------------------------------ utilitaires
def benin_now():
    return datetime.now(timezone.utc) + timedelta(hours=1)  # Bénin = UTC+1


def g(d, *path, default=None):
    """Accès sûr dans des dict/list imbriqués."""
    for k in path:
        if isinstance(d, dict):
            d = d.get(k)
        elif isinstance(d, list) and isinstance(k, int) and -len(d) <= k < len(d):
            d = d[k]
        else:
            return default
        if d is None:
            return default
    return d


def http_json(url, timeout=20, tries=2):
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout, context=bp._ssl_context()) as r:
                return json.loads(r.read().decode("utf-8", errors="replace"))
        except Exception:
            continue
    return None


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    stop = {"fc", "afc", "cf", "sc", "ac", "as", "ssc", "club", "de", "the", "calcio", "1", "sv", "fk", "bk", "if"}
    return " ".join(w for w in s.split() if w not in stop)


def sim(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    r = difflib.SequenceMatcher(None, a, b).ratio()
    ta, tb = a.split(), b.split()
    if all(any(y.startswith(x) or x.startswith(y) for y in tb) for x in ta) or \
       all(any(x.startswith(y) or y.startswith(x) for x in ta) for y in tb):
        r = max(r, 0.85)
    return r


def american_to_dec(x):
    if x is None:
        return None
    s = str(x).strip().upper()
    if s in ("EVEN", "EV"):
        return 2.0
    v = num(s.replace("+", ""))
    if v is None or v == 0:
        return None
    if v >= 100:
        return round(1 + v / 100, 3)
    if v <= -100:
        return round(1 + 100 / abs(v), 3)
    if v > 1:
        return v  # déjà décimal
    return None


# ------------------------------------------------------------------ ESPN : matchs
def fetch_events(utc_dates):
    events, leagues = {}, {}

    def ingest(js, slug=None):
        if not isinstance(js, dict):
            return
        for lg in js.get("leagues") or []:
            if isinstance(lg, dict) and lg.get("id") is not None:
                leagues[str(lg["id"])] = {"slug": lg.get("slug") or "", "name": lg.get("name") or lg.get("abbreviation") or ""}
        for ev in js.get("events") or []:
            if isinstance(ev, dict) and ev.get("id"):
                if slug:
                    ev["_slug"] = slug
                events[str(ev["id"])] = ev

    for d in utc_dates:
        ingest(http_json(f"{ESPN}/all/scoreboard?dates={d:%Y%m%d}&limit=1000"))
    if len(events) < 20:  # liste globale indisponible -> on parcourt les championnats connus
        rng = f"{utc_dates[0]:%Y%m%d}-{utc_dates[-1]:%Y%m%d}"
        jobs = [(f"{ESPN}/{s}/scoreboard?dates={rng}&limit=300", s) for s in SLUGS]
        with ThreadPoolExecutor(16) as ex:
            for js, s in ex.map(lambda j: (http_json(j[0]), j[1]), jobs):
                ingest(js, s)
    return events, leagues


def parse_iso(s):
    try:
        return datetime.strptime(s.replace("Z", "+0000").replace("+00:00", "+0000"), "%Y-%m-%dT%H:%M%z")
    except Exception:
        try:
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")
        except Exception:
            return None


def base_match(ev, leagues):
    comp = g(ev, "competitions", 0, default={}) or {}
    cs = comp.get("competitors") or []
    h = next((c for c in cs if c.get("homeAway") == "home"), None)
    a = next((c for c in cs if c.get("homeAway") == "away"), None)
    if not h or not a:
        return None
    dt = parse_iso(str(ev.get("date") or comp.get("date") or ""))
    if not dt:
        return None
    loc = dt.astimezone(timezone.utc) + timedelta(hours=1)
    m_lid = re.search(r"l:(\d+)", str(ev.get("uid") or ""))
    lid = m_lid.group(1) if m_lid else (ev.get("_slug") or "?")
    lg = leagues.get(lid, {})
    slug = lg.get("slug") or ev.get("_slug") or ""
    state = g(ev, "status", "type", "state", default="pre")
    side = lambda c: dict(id=str(g(c, "team", "id", default="")), name=g(c, "team", "displayName", default="?"),
                          logo=g(c, "team", "logo", default="") or "", score=c.get("score"))
    return dict(
        id=str(ev["id"]), slug=slug, lid=lid, league=lg.get("name") or SLUGS.get(slug) or "Autres compétitions",
        d=loc.strftime("%Y-%m-%d"), time=loc.strftime("%H:%M"), state=state,
        clock=g(ev, "status", "displayClock", default="") if state == "in" else "",
        home=side(h), away=side(a), p=None, x=None, odds=None, value=[], src=[], conf="faible", det={},
        _comp_odds=comp.get("odds") or [],
    )


# ------------------------------------------------------------------ ESPN : classements / détails
def walk_entries(node, out):
    if isinstance(node, dict):
        ents = node.get("entries")
        if isinstance(ents, list) and ents and isinstance(ents[0], dict) and "team" in ents[0]:
            out.append(ents)
        for v in node.values():
            walk_entries(v, out)
    elif isinstance(node, list):
        for v in node:
            walk_entries(v, out)


def parse_standings(js):
    """-> {team_id: {rank, gp, w, d, l, gf, ga, pts}} et nombre d'équipes."""
    groups = []
    walk_entries(js, groups)
    table = {}
    for ents in groups:
        rows = []
        for e in ents:
            st = {}
            for s in e.get("stats") or []:
                if isinstance(s, dict) and s.get("name"):
                    st[s["name"]] = num(s.get("value"))
            first = lambda *ks: next((st[k] for k in ks if st.get(k) is not None), None)
            rows.append(dict(
                id=str(g(e, "team", "id", default="")), name=g(e, "team", "displayName", default=""),
                rank=first("rank"), gp=first("gamesPlayed"), w=first("wins"), d=first("ties", "draws"),
                l=first("losses"), gf=first("pointsFor", "goalsFor"), ga=first("pointsAgainst", "goalsAgainst"),
                pts=first("points")))
        if rows and any(r["rank"] is None for r in rows):
            rows.sort(key=lambda r: (-(r["pts"] or 0), -((r["gf"] or 0) - (r["ga"] or 0))))
            for i, r in enumerate(rows, 1):
                r["rank"] = i
        for r in rows:
            r["n"] = len(rows)
            if r["id"]:
                table[r["id"]] = r
    return table


def parse_form(js):
    out = {}
    for blk in g(js, "boxscore", "form", default=[]) or []:
        tid = str(g(blk, "team", "id", default=""))
        evs = []
        for e in (blk.get("events") or []):
            if not isinstance(e, dict):
                continue
            evs.append(dict(
                r=e.get("gameResult") or "", date=str(e.get("gameDate") or "")[:10],
                opp=g(e, "opponent", "displayName") or g(e, "opponent", "abbreviation") or "",
                ha=e.get("atVs") or "", sc=f'{e.get("homeTeamScore", "")}-{e.get("awayTeamScore", "")}'))
        if evs and all(x["date"] for x in evs):
            evs.sort(key=lambda x: x["date"], reverse=True)
        out[tid] = evs[:5]
    return out


def parse_h2h(js, names):
    games = []
    for blk in js.get("headToHeadGames") or []:
        for e in (blk.get("events") or []):
            if isinstance(e, dict):
                games.append(e)
        if games:
            break
    out = []
    for e in games[:5]:
        hn = names.get(str(e.get("homeTeamId")), "") or g(e, "homeTeam", "displayName", default="")
        an = names.get(str(e.get("awayTeamId")), "") or g(e, "awayTeam", "displayName", default="")
        if not (hn and an):
            continue
        out.append(dict(date=str(e.get("gameDate") or "")[:10], h=hn, a=an,
                        sc=f'{e.get("homeTeamScore", "")}-{e.get("awayTeamScore", "")}'))
    return out


def parse_rosters(js):
    out = {}
    for r in js.get("rosters") or []:
        side = r.get("homeAway")
        if side not in ("home", "away"):
            continue
        players = []
        for p in r.get("roster") or []:
            nm = g(p, "athlete", "displayName") or g(p, "athlete", "shortName")
            if nm:
                players.append(dict(n=nm, pos=g(p, "position", "abbreviation", default=""), j=p.get("jersey", ""),
                                    st=bool(p.get("starter"))))
        starters = [p for p in players if p["st"]]
        out[side] = dict(formation=r.get("formation") or "", starters=starters[:11],
                         subs=[p for p in players if not p["st"]][:9])
    return out


def parse_injuries(js):
    out = {}
    for blk in js.get("injuries") or []:
        tid = str(g(blk, "team", "id", default=""))
        items = []
        for i in blk.get("injuries") or []:
            nm = g(i, "athlete", "displayName")
            if not nm:
                continue
            stt = i.get("status")
            stt = stt.get("description") if isinstance(stt, dict) else stt
            items.append(dict(n=nm, s=str(stt or g(i, "type", "description", default="") or "")))
        if items:
            out[tid] = items[:8]
    return out


def espn_odds(js, comp_odds):
    cands = []
    for pc in (js or {}).get("pickcenter") or []:
        cands.append((g(pc, "homeTeamOdds", "moneyLine"), g(pc, "drawOdds", "moneyLine"), g(pc, "awayTeamOdds", "moneyLine")))
    for o in list((js or {}).get("odds") or []) + list(comp_odds or []):
        ml = o.get("moneyline") or {}
        cands.append((g(ml, "home", "close", "odds"), g(ml, "draw", "close", "odds"), g(ml, "away", "close", "odds")))
        cands.append((g(o, "homeTeamOdds", "moneyLine"), g(o, "drawOdds", "moneyLine"), g(o, "awayTeamOdds", "moneyLine")))
    for c in cands:
        vals = [american_to_dec(x) for x in c]
        if all(vals):
            return vals
    return None


# ------------------------------------------------------------------ modèles
def probs_from_lambdas(lh, la):
    mg = bp.MAX_GOALS
    ph = [bp.poisson(i, lh) for i in range(mg + 1)]
    pa = [bp.poisson(i, la) for i in range(mg + 1)]
    p1 = px = p2 = over = btts = 0.0
    for i in range(mg + 1):
        for j in range(mg + 1):
            p = ph[i] * pa[j]
            if i > j: p1 += p
            elif i == j: px += p
            else: p2 += p
            if i + j > 2: over += p
            if i > 0 and j > 0: btts += p
    t = p1 + px + p2
    return dict(h=p1 / t, x=px / t, a=p2 / t, over=over, btts=btts, lh=lh, la=la)


def standings_model(st, hid, aid, prior=5):
    h, a = st.get(hid), st.get(aid)
    if not h or not a:
        return None
    rows = list(st.values())
    tot_gp = sum(r["gp"] or 0 for r in rows)
    tot_gf = sum(r["gf"] or 0 for r in rows)
    if tot_gp < 10 or tot_gf <= 0 or (h["gp"] or 0) < 3 or (a["gp"] or 0) < 3 or h["gf"] is None or a["gf"] is None:
        return None
    base = tot_gf / tot_gp

    def rate(r, key):
        return (((r[key] or 0) + prior * base) / ((r["gp"] or 0) + prior)) / base

    lh = base * 1.12 * rate(h, "gf") * rate(a, "ga")
    la = base * 0.89 * rate(a, "gf") * rate(h, "ga")
    return probs_from_lambdas(lh, la)


def devig(odds):
    inv = [1 / o for o in odds]
    s = sum(inv)
    return [x / s for x in inv]


# ------------------------------------------------------------------ football-data (enrichissement)
def load_fd(start, end):
    out = []
    try:
        fx = []
        for r in bp.read_rows(bp.fetch(f"{bp.BASE}/fixtures.csv", "fixtures.csv")):
            d = bp.parse_date(r.get("Date", ""))
            if d and start <= d <= end and r.get("HomeTeam"):
                fx.append(r)
        for div in sorted({r["Div"] for r in fx}):
            out += bp.analyse_div(div, [r for r in fx if r["Div"] == div], bp.current_seasons(), 0.05)
    except Exception as ex:
        print("football-data indisponible :", ex)
    return out


def find_fd(m, fd):
    best, bs = None, 0
    md = datetime.strptime(m["d"], "%Y-%m-%d").date()
    for f in fd:
        if f["date"] is None or abs((f["date"] - md).days) > 1:
            continue
        sh, sa = sim(m["home"]["name"], f["home"]), sim(m["away"]["name"], f["away"])
        if sh >= 0.72 and sa >= 0.72 and sh + sa > bs:
            best, bs = f, sh + sa
    return best


# ------------------------------------------------------------------ assemblage
def assemble(matches, fd, standings, summaries):
    for m in matches:
        js = summaries.get(m["id"]) or {}
        hid, aid = m["home"]["id"], m["away"]["id"]
        names = {hid: m["home"]["name"], aid: m["away"]["name"]}
        det = dict(form={}, h2h=[], lineups={}, injuries={}, st={})
        form = parse_form(js)
        det["form"] = dict(home=form.get(hid, []), away=form.get(aid, []))
        det["h2h"] = parse_h2h(js, names)
        det["lineups"] = parse_rosters(js)
        inj = parse_injuries(js)
        det["injuries"] = dict(home=inj.get(hid, []), away=inj.get(aid, []))
        st = standings.get(m["slug"]) or parse_standings(js)
        if st.get(hid) and st.get(aid):
            det["st"] = dict(home=st[hid], away=st[aid])
        m["det"] = det

        # --- modèle
        model, srcs = None, []
        f = find_fd(m, fd)
        if f:
            p = f["pred"]
            model = dict(h=p["p1"], x=p["px"], a=p["p2"], over=p["over25"], btts=p["btts"], lh=p["lam_h"], la=p["lam_a"])
            srcs.append("historique 2 saisons")
        else:
            model = standings_model(st, hid, aid)
            if model:
                srcs.append("classement saison")
        # --- marché
        odds = (f or {}).get("odds") or espn_odds(js, m["_comp_odds"])
        market = devig(odds) if odds else None
        if market:
            srcs.append("cotes du marché")
            m["odds"] = [round(o, 2) for o in odds]

        fin = None
        if model and market:
            fin = [0.6 * model["h"] + 0.4 * market[0], 0.6 * model["x"] + 0.4 * market[1], 0.6 * model["a"] + 0.4 * market[2]]
        elif model:
            fin = [model["h"], model["x"], model["a"]]
        elif market:
            fin = market
        if fin:
            s = sum(fin)
            m["p"] = dict(h=round(fin[0] / s, 4), x=round(fin[1] / s, 4), a=round(fin[2] / s, 4))
        if model:
            m["x"] = dict(over=round(model["over"], 3), btts=round(model["btts"], 3),
                          lh=round(model["lh"], 2), la=round(model["la"], 2))
        m["src"] = srcs
        gp = min((g(det, "st", "home", "gp", default=0) or 0), (g(det, "st", "away", "gp", default=0) or 0))
        m["conf"] = "haute" if (model and market and gp >= 6) else ("moyenne" if (model or market) and len(srcs) >= 1 and model else "faible")

        # --- value bets : uniquement si modèle ET marché existent (sinon l'écart n'est pas fiable)
        if m["p"] and model and market and m["state"] == "pre":
            names3 = [m["home"]["name"], "Match nul", m["away"]["name"]]
            for i, key in enumerate(("h", "x", "a")):
                p, o = m["p"][key], odds[i]
                edge = p * o - 1
                if 0.03 <= edge <= 0.25 and o <= 5.5 and p >= 0.20:
                    m["value"].append(dict(pick=names3[i], p=round(p, 3), o=round(o, 2), e=round(edge, 3)))
        del m["_comp_odds"]
    return matches


# ------------------------------------------------------------------ page
CSS = r"""
:root{--bg:#070b12;--bg2:#0d1420;--card:#121a28;--card2:#182236;--line:#223049;--tx:#eaf0fa;--mut:#8696b2;
--g:#2ee59d;--gd:#0c3d2b;--y:#ffd54a;--b:#58a6ff;--r:#ff6b7a;--rad:16px}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}html{scroll-behavior:smooth}
body{margin:0;background:radial-gradient(1200px 500px at 50% -150px,#12324a 0,var(--bg) 70%) no-repeat,var(--bg);color:var(--tx);
font:15px/1.4 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;padding-bottom:40px}
.wrap{max-width:720px;margin:0 auto;padding:0 14px}
header{padding:20px 0 6px;display:flex;align-items:center;gap:12px}
.logo{width:42px;height:42px;border-radius:13px;background:linear-gradient(135deg,var(--g),#0aa86b);display:grid;place-items:center;font-size:22px;box-shadow:0 6px 18px #2ee59d44}
h1{margin:0;font-size:21px;letter-spacing:.2px}.upd{color:var(--mut);font-size:12.5px}
.top{position:sticky;top:0;z-index:5;background:linear-gradient(var(--bg) 82%,#070b1200);padding:8px 0 10px}
.days{display:flex;gap:8px}.days button{flex:1;border:1px solid var(--line);background:var(--card);color:var(--tx);border-radius:14px;padding:9px 4px;font:inherit;font-weight:600;transition:.15s}
.days button small{display:block;color:var(--mut);font-weight:500;font-size:11.5px}
.days button.on{background:linear-gradient(135deg,var(--g),#16c486);color:#03150d;border-color:transparent;box-shadow:0 6px 16px #2ee59d33}.days button.on small{color:#0b3a28}
.tools{display:flex;gap:8px;margin-top:9px}
input{flex:1 1 40%}select{flex:1 1 60%}input,select{min-width:0;text-overflow:ellipsis;background:var(--card);border:1px solid var(--line);color:var(--tx);border-radius:12px;padding:10px 12px;font:inherit}
.chips{display:flex;gap:7px;margin-top:8px;overflow-x:auto;scrollbar-width:none}
.chips button{flex:none;border:1px solid var(--line);background:transparent;color:var(--mut);border-radius:20px;padding:6px 13px;font:inherit;font-size:13.5px}
.chips button.on{color:var(--g);border-color:var(--g);background:#2ee59d14}
.lh{display:flex;justify-content:space-between;align-items:center;font-size:13px;letter-spacing:.4px;text-transform:uppercase;color:var(--mut);margin:22px 4px 9px;font-weight:700}
.lh small{background:var(--card);border-radius:10px;padding:2px 8px;font-size:12px}
.list{background:var(--card);border:1px solid var(--line);border-radius:var(--rad);overflow:hidden}
.match{display:grid;grid-template-columns:50px 1fr 96px;gap:8px;align-items:center;width:100%;text-align:left;color:inherit;font:inherit;background:none;border:0;border-bottom:1px solid var(--line);padding:12px;position:relative;cursor:pointer}
.match:last-child{border-bottom:0}.match:active{background:var(--card2)}
.t{font-size:13px;color:var(--mut);text-align:center;line-height:1.2}.t b{color:var(--tx);font-size:14px}.t .live{color:var(--r)}
.teams>div{display:flex;align-items:center;gap:9px;min-height:27px}.teams span.n{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600}
.teams em{font-style:normal;font-weight:800;min-width:16px;text-align:right}
.lg{width:24px;height:24px;border-radius:50%;background:var(--card2);display:inline-grid;place-items:center;font-size:12px;font-weight:700;color:var(--mut);position:relative;flex:none;overflow:hidden}
.lg img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:var(--card2);padding:2px}
.mini{display:flex;height:7px;border-radius:5px;overflow:hidden;background:#222c3f}.mini i{display:block}
.h{background:var(--g)}.x{background:#6f7f9b}.a{background:var(--b)}
.pc{display:flex;justify-content:space-between;font-size:12px;color:var(--mut);margin-top:5px;font-variant-numeric:tabular-nums}
.nodata{color:var(--mut);text-align:center;font-size:12px}
.vb{position:absolute;right:10px;top:7px;background:#ffd54a22;border:1px solid var(--y);color:var(--y);font-size:9.5px;font-weight:800;border-radius:6px;padding:1px 5px;letter-spacing:.5px}
.empty{text-align:center;color:var(--mut);padding:50px 10px}
.foot{color:var(--mut);font-size:12px;margin:28px 4px 0;padding-top:14px;border-top:1px solid var(--line)}
#sheet{position:fixed;inset:0;background:var(--bg);z-index:20;overflow-y:auto;transform:translateY(100%);transition:transform .28s cubic-bezier(.2,.8,.2,1);visibility:hidden}
#sheet.open{transform:none;visibility:visible}
.sh-head{background:linear-gradient(180deg,#14405a 0,var(--bg) 100%);padding:14px 14px 18px}
.back{background:#ffffff14;border:0;color:var(--tx);border-radius:12px;padding:8px 14px;font:inherit;font-weight:600}
.lgname{color:#a9c1dc;font-size:12.5px;text-align:center;margin-top:10px;text-transform:uppercase;letter-spacing:.5px}
.duel{display:grid;grid-template-columns:1fr auto 1fr;gap:6px;align-items:center;margin-top:12px;text-align:center}
.duel .tm{font-weight:700;font-size:15px}.duel .lg{width:56px;height:56px;font-size:22px;margin:0 auto 8px}
.duel .mid{font-size:26px;font-weight:800;min-width:70px}.duel .mid small{display:block;font-size:12.5px;color:var(--mut);font-weight:500}
.sec{background:var(--card);border:1px solid var(--line);border-radius:var(--rad);padding:15px;margin:12px 14px}
.sec h3{margin:0 0 12px;font-size:14px;letter-spacing:.4px;text-transform:uppercase;color:var(--mut)}
.pr{display:grid;grid-template-columns:92px 1fr 64px;gap:10px;align-items:center;margin-bottom:11px}
.pr label{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.pr .bar{height:10px;border-radius:6px;background:#222c3f;overflow:hidden}.pr .bar i{display:block;height:100%;border-radius:6px}
.pr b{text-align:right;font-variant-numeric:tabular-nums}.pr small{display:block;color:var(--mut);font-size:11.5px;white-space:nowrap}
.tags{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}.tag{background:var(--card2);border-radius:10px;padding:6px 10px;font-size:13px}
.tag b{color:var(--g)}
.vbet{border:1px solid var(--y);background:#ffd54a12;border-radius:12px;padding:10px 12px;margin-bottom:8px}.vbet b{color:var(--y)}
.note{color:var(--mut);font-size:12.5px;margin-top:8px}
.frm{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}.frm>span{font-weight:600}
.dots{display:flex;gap:5px}.dots i{width:24px;height:24px;border-radius:7px;display:grid;place-items:center;font-style:normal;font-size:12px;font-weight:800;color:#04130c}
.dots .W{background:var(--g)}.dots .D{background:#9aa6bd}.dots .L{background:var(--r)}.dots .n{background:var(--card2);color:var(--mut)}
.res{color:var(--mut);font-size:12.5px;margin:-4px 0 12px}
table{width:100%;border-collapse:collapse;font-size:13.5px}th{color:var(--mut);font-weight:600;text-align:center;padding:4px}td{text-align:center;padding:8px 4px;border-top:1px solid var(--line)}
td:first-child,th:first-child{text-align:left}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:12px}.cols h4{margin:0 0 6px;font-size:13.5px}.cols p{margin:0 0 3px;font-size:13px}.cols p span{color:var(--mut);display:inline-block;min-width:24px}
.fm{color:var(--g);font-size:12.5px;margin-bottom:6px;font-weight:700}
.row2{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-top:1px solid var(--line);font-size:13.5px}.row2:first-of-type{border-top:0}.row2 span{color:var(--mut)}
.badge{display:inline-block;border-radius:8px;padding:2px 9px;font-size:12px;font-weight:700}
.b-haute{background:#2ee59d22;color:var(--g)}.b-moyenne{background:#ffd54a22;color:var(--y)}.b-faible{background:#ff6b7a22;color:var(--r)}
"""

JS = r"""
const D=JSON.parse(document.getElementById('data').textContent);
const $=s=>document.querySelector(s);
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct=v=>Math.round(v*100);
let day=0,q='',lg='',flt='all';
try{day=Math.min(+localStorage.getItem('day')||0,D.days.length-1)}catch(e){}
function logo(t){const i=esc((t.name||'?').trim().slice(0,1).toUpperCase());const u=t.logo&&t.logo.indexOf('https://')===0?esc(t.logo):'';
return '<span class="lg">'+i+(u?'<img loading="lazy" alt="" src="'+u+'" onerror="this.remove()">':'')+'</span>'}
function when(m){if(m.state==='in')return '<b class="live">'+esc(m.clock||'EN DIRECT')+'</b>';if(m.state==='post')return '<b>Fin</b>';return '<b>'+esc(m.time)+'</b>'}
function sc(m,t){return m.state==='pre'||t.score==null?'':esc(t.score)}
function row(m){const p=m.p;
const bar=p?'<div class="mini"><i class="h" style="width:'+p.h*100+'%"></i><i class="x" style="width:'+p.x*100+'%"></i><i class="a" style="width:'+p.a*100+'%"></i></div><div class="pc"><span>'+pct(p.h)+'</span><span>'+pct(p.x)+'</span><span>'+pct(p.a)+'</span></div>':'<div class="nodata">pas de stats</div>';
return '<button class="match" data-id="'+esc(m.id)+'"><div class="t">'+when(m)+'</div><div class="teams"><div>'+logo(m.home)+'<span class="n">'+esc(m.home.name)+'</span><em>'+sc(m,m.home)+'</em></div><div>'+logo(m.away)+'<span class="n">'+esc(m.away.name)+'</span><em>'+sc(m,m.away)+'</em></div></div><div>'+bar+'</div>'+(m.value.length?'<span class="vb">VALUE</span>':'')+'</button>'}
function render(){
const key=D.days[day].key;const ql=q.trim().toLowerCase();
let ms=D.matches.filter(m=>m.d===key);
const lgs=[...new Map(ms.map(m=>[m.lid,m])).values()].sort((a,b)=>a.lo-b.lo);
const sel=$('#lg');const cur=lg;sel.innerHTML='<option value="">Tous les championnats ('+lgs.length+')</option>'+lgs.map(m=>'<option value="'+esc(m.lid)+'">'+esc(m.league)+'</option>').join('');sel.value=lgs.some(m=>m.lid===cur)?cur:'';lg=sel.value;
if(lg)ms=ms.filter(m=>m.lid===lg);
if(ql)ms=ms.filter(m=>(m.home.name+' '+m.away.name+' '+m.league).toLowerCase().includes(ql));
if(flt==='value')ms=ms.filter(m=>m.value.length);
if(flt==='live')ms=ms.filter(m=>m.state==='in');
if(flt==='stats')ms=ms.filter(m=>m.p);
ms.sort((a,b)=>a.lo-b.lo||a.time.localeCompare(b.time));
let h='',last=null,buf=[];
const flush=()=>{if(buf.length)h+='<div class="lh"><span>'+esc(last)+'</span><small>'+buf.length+'</small></div><div class="list">'+buf.map(row).join('')+'</div>';buf=[]};
ms.forEach(m=>{if(m.league!==last){flush();last=m.league}buf.push(m)});flush();
$('#list').innerHTML=h||'<div class="empty">Aucun match ne correspond.</div>';
document.querySelectorAll('.days button').forEach((b,i)=>b.classList.toggle('on',i===day));
document.querySelectorAll('.chips button').forEach(b=>b.classList.toggle('on',b.dataset.f===flt))}
function dots(a){let h='';for(let i=0;i<5;i++){const r=a[i]?a[i].r:'';h+=r?'<i class="'+esc(r)+'">'+esc(r==='D'?'N':r==='W'?'V':'D')+'</i>':'<i class="n">·</i>'}return '<div class="dots">'+h+'</div>'}
function resl(a){return a.length?'<div class="res">'+a.map(e=>esc((e.ha||'')+' '+(e.opp||'?')+' '+e.sc)).join(' · ')+'</div>':''}
function lineup(l,name){if(!l||!l.starters.length)return '';return '<div><h4>'+esc(name)+'</h4>'+(l.formation?'<div class="fm">'+esc(l.formation)+'</div>':'')+l.starters.map(p=>'<p><span>'+esc(p.j)+'</span>'+esc(p.n)+(p.pos?' <span>'+esc(p.pos)+'</span>':'')+'</p>').join('')+'</div>'}
function detail(m){
const p=m.p,d=m.det||{},x=m.x;let h='';
const mid=m.state==='pre'?'<div class="mid">'+esc(m.time)+'<small>heure du Bénin</small></div>':'<div class="mid">'+esc(m.home.score)+' - '+esc(m.away.score)+'<small>'+(m.state==='in'?esc(m.clock||'en direct'):'terminé')+'</small></div>';
h+='<div class="sh-head"><button class="back" id="back">← Retour</button><div class="lgname">'+esc(m.league)+'</div><div class="duel"><div>'+logo(m.home).replace('class="lg"','class="lg"')+'<div class="tm">'+esc(m.home.name)+'</div></div>'+mid+'<div>'+logo(m.away)+'<div class="tm">'+esc(m.away.name)+'</div></div></div></div>';
if(p){const rows=[[m.home.name,p.h,'h',m.odds&&m.odds[0]],['Match nul',p.x,'x',m.odds&&m.odds[1]],[m.away.name,p.a,'a',m.odds&&m.odds[2]]];
h+='<div class="sec"><h3>Chances de victoire</h3>'+rows.map(r=>'<div class="pr"><label>'+esc(r[0])+'</label><div class="bar"><i class="'+r[2]+'" style="width:'+r[1]*100+'%"></i></div><b>'+pct(r[1])+'%'+(r[3]?'<small>cote '+r[3]+'</small>':'')+'</b></div>').join('');
if(x)h+='<div class="tags"><span class="tag">Buts attendus <b>'+x.lh+' - '+x.la+'</b></span><span class="tag">+2,5 buts <b>'+pct(x.over)+'%</b></span><span class="tag">Les 2 marquent <b>'+pct(x.btts)+'%</b></span></div>';
h+='<div class="note">Fiabilité des données : <span class="badge b-'+esc(m.conf)+'">'+esc(m.conf)+'</span> · Sources : '+esc(m.src.join(' + '))+'</div></div>'}
else h+='<div class="sec"><h3>Chances de victoire</h3><div class="note">Pas assez de données (classement, historique ou cotes) pour estimer ce match. Aucun pari conseillé.</div></div>';
if(m.value.length)h+='<div class="sec"><h3>Value bet détecté</h3>'+m.value.map(v=>'<div class="vbet"><b>'+esc(v.pick)+'</b><br>Proba '+pct(v.p)+'% · cote '+v.o+' · avantage estimé '+(v.e>0?'+':'')+pct(v.e)+'%</div>').join('')+'<div class="note">Estimation du modèle, pas une certitude : le marché tient compte d\'infos que le modèle ne voit pas (blessures, motivation…).</div></div>';
if((d.form&&(d.form.home.length||d.form.away.length)))h+='<div class="sec"><h3>Forme (5 derniers matchs)</h3><div class="frm"><span>'+esc(m.home.name)+'</span>'+dots(d.form.home)+'</div>'+resl(d.form.home)+'<div class="frm"><span>'+esc(m.away.name)+'</span>'+dots(d.form.away)+'</div>'+resl(d.form.away)+'</div>';
if(d.st&&d.st.home){const r=(t,s)=>'<tr><td>'+esc(t)+'</td><td>'+esc(s.rank==null?'-':s.rank)+'<small>/'+esc(s.n)+'</small></td><td>'+esc(s.gp==null?'-':s.gp)+'</td><td>'+esc(s.w==null?'-':s.w)+'-'+esc(s.d==null?'-':s.d)+'-'+esc(s.l==null?'-':s.l)+'</td><td>'+esc(s.gf==null?'-':s.gf)+':'+esc(s.ga==null?'-':s.ga)+'</td><td><b>'+esc(s.pts==null?'-':s.pts)+'</b></td></tr>';
h+='<div class="sec"><h3>Classement</h3><table><tr><th>Équipe</th><th>Rang</th><th>J</th><th>V-N-D</th><th>BP:BC</th><th>Pts</th></tr>'+r(m.home.name,d.st.home)+r(m.away.name,d.st.away)+'</table></div>'}
const L=d.lineups||{};
if(L.home&&L.home.starters.length||L.away&&L.away.starters.length)h+='<div class="sec"><h3>Compositions probables / officielles</h3><div class="cols">'+lineup(L.home,m.home.name)+lineup(L.away,m.away.name)+'</div></div>';
else h+='<div class="sec"><h3>Compositions</h3><div class="note">Pas encore publiées. Elles sont en général connues environ 1 heure avant le coup d\'envoi ; le site se met à jour plusieurs fois par jour.</div></div>';
const I=d.injuries||{};
if((I.home&&I.home.length)||(I.away&&I.away.length)){const li=a=>a&&a.length?a.map(i=>'<p>'+esc(i.n)+(i.s?' <span>'+esc(i.s)+'</span>':'')+'</p>').join(''):'<p><span>Aucun signalé</span></p>';
h+='<div class="sec"><h3>Absents / blessés</h3><div class="cols"><div><h4>'+esc(m.home.name)+'</h4>'+li(I.home)+'</div><div><h4>'+esc(m.away.name)+'</h4>'+li(I.away)+'</div></div></div>'}
if(d.h2h&&d.h2h.length)h+='<div class="sec"><h3>Face-à-face</h3>'+d.h2h.map(e=>'<div class="row2"><span>'+esc(e.date)+'</span><div>'+esc(e.h)+' <b>'+esc(e.sc)+'</b> '+esc(e.a)+'</div></div>').join('')+'</div>';
h+='<div class="foot" style="margin:14px">Statistiques indicatives. Aucun modèle ne garantit un gain ; les bookmakers gardent une marge. Ne mise que ce que tu peux te permettre de perdre.</div>';
return h}
function openM(id){const m=D.matches.find(x=>x.id===id);if(!m)return;$('#sheet').innerHTML=detail(m);$('#sheet').classList.add('open');$('#sheet').scrollTop=0;document.body.style.overflow='hidden';try{history.pushState({s:1},'')}catch(e){}
$('#back').onclick=()=>history.back()}
function closeM(){$('#sheet').classList.remove('open');document.body.style.overflow=''}
window.addEventListener('popstate',closeM);
$('#list').addEventListener('click',e=>{const b=e.target.closest('.match');if(b)openM(b.dataset.id)});
document.querySelectorAll('.days button').forEach((b,i)=>b.onclick=()=>{day=i;try{localStorage.setItem('day',i)}catch(e){}render()});
document.querySelectorAll('.chips button').forEach(b=>b.onclick=()=>{flt=b.dataset.f;render()});
$('#q').oninput=e=>{q=e.target.value;render()};$('#lg').onchange=e=>{lg=e.target.value;render()};
render();
if('serviceWorker' in navigator)navigator.serviceWorker.register('sw.js').catch(()=>{});
"""

SW = """const C='parisbot-v2';
self.addEventListener('install',e=>{self.skipWaiting()});
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==C).map(k=>caches.delete(k)))).then(()=>clients.claim())));
self.addEventListener('fetch',e=>{if(e.request.method!=='GET')return;
e.respondWith(fetch(e.request).then(r=>{const cp=r.clone();caches.open(C).then(c=>c.put(e.request,cp));return r}).catch(()=>caches.match(e.request)))});
"""

MANIFEST = """{
 "name": "Paris Bot",
 "short_name": "Paris Bot",
 "start_url": "./",
 "scope": "./",
 "display": "standalone",
 "background_color": "#070b12",
 "theme_color": "#070b12",
 "lang": "fr",
 "icons": [
  {"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
  {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
 ]
}
"""


def make_png(size, path):
    bg, fg = (12, 61, 43, 255), (46, 229, 157, 255)
    cx = size / 2
    r2, r3 = (size * 0.30) ** 2, (size * 0.12) ** 2
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            d = (x - cx) ** 2 + (y - cx) ** 2
            raw += bytes(bg if d <= r3 else fg if d <= r2 else bg)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def day_label(d, today):
    if d == today:
        return "Aujourd'hui"
    if d == today + timedelta(days=1):
        return "Demain"
    return f"{JOURS[d.weekday()][:3]}. {d.day} {MOIS[d.month - 1]}"


def build():
    now = benin_now()
    today = now.date()
    days = [today + timedelta(days=i) for i in range(DAYS)]
    keys = {d.strftime("%Y-%m-%d") for d in days}
    utc_dates = [today + timedelta(days=i) for i in range(-1, DAYS + 1)]

    events, leagues = fetch_events(utc_dates)
    matches = [m for m in (base_match(e, leagues) for e in events.values()) if m and m["d"] in keys]
    print(f"ESPN : {len(matches)} matchs sur {len({m['lid'] for m in matches})} championnats")

    # ordre d'affichage des championnats
    def lorder(m):
        return (ORDER.get(m["slug"], 999), m["league"])
    for i, lid in enumerate(sorted({(m["lid"], lorder(m)) for m in matches}, key=lambda t: t[1])):
        for m in matches:
            if m["lid"] == lid[0]:
                m["lo"] = i

    fd = load_fd(days[0], days[-1] + timedelta(days=1))
    print(f"football-data : {len(fd)} matchs avec modèle")

    slugs = sorted({m["slug"] for m in matches if m["slug"]})
    standings, summaries = {}, {}
    with ThreadPoolExecutor(16) as ex:
        for s, js in zip(slugs, ex.map(lambda s: http_json(f"{ESPN_V2}/{s}/standings"), slugs)):
            try:
                standings[s] = parse_standings(js) if js else {}
            except Exception:
                standings[s] = {}
        todo = [m for m in matches if m["state"] != "post"]
        for m, js in zip(todo, ex.map(lambda m: http_json(f"{ESPN}/{m['slug'] or 'all'}/summary?event={m['id']}"), todo)):
            if js:
                summaries[m["id"]] = js

    for m in matches:  # sécurité : une erreur sur un match ne casse pas le site
        try:
            assemble([m], fd, standings, summaries)
        except Exception as ex:
            print("match ignoré :", m["home"]["name"], ex)
            m.pop("_comp_odds", None)
    matches.sort(key=lambda m: (m["d"], m["lo"], m["time"]))
    return now, days, today, matches


def main():
    now, days, today, matches = build()
    data = dict(
        days=[dict(key=d.strftime("%Y-%m-%d"), label=day_label(d, today),
                   n=sum(1 for m in matches if m["d"] == d.strftime("%Y-%m-%d"))) for d in days],
        matches=matches)
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    tabs = "".join(f'<button>{day_label(d, today)}<small>{x["n"]} matchs</small></button>' for d, x in zip(days, data["days"]))
    stamp = f"Mis à jour le {JOURS[now.weekday()].lower()} {now.day} {MOIS[now.month - 1]} à {now:%H:%M} (heure du Bénin)"
    page = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#070b12"><title>Paris Bot</title>
<link rel="manifest" href="manifest.webmanifest"><link rel="icon" href="icon-192.png">
<link rel="apple-touch-icon" href="icon-192.png"><style>{CSS}</style></head><body>
<div class="wrap">
<header><div class="logo">⚽</div><div><h1>Paris Bot</h1><div class="upd">{stamp}</div></div></header>
<div class="top"><div class="days">{tabs}</div>
<div class="tools"><input id="q" type="search" placeholder="Rechercher…" autocomplete="off"><select id="lg"></select></div>
<div class="chips"><button data-f="all" class="on">Tous</button><button data-f="value">⭐ Value bets</button><button data-f="live">🔴 En direct</button><button data-f="stats">📊 Avec stats</button></div></div>
<div id="list"></div>
<div class="foot">Probabilités estimées par un modèle statistique (historique, classement, forme) et ajustées aux cotes du marché quand elles existent. Aucun modèle ne garantit un gain : la plupart des parieurs perdent sur la durée. Jeu réservé aux adultes.</div>
</div><div id="sheet"></div>
<script id="data" type="application/json">{blob}</script>
<script>{JS}</script></body></html>"""

    os.makedirs(OUT, exist_ok=True)
    for name, content in (("index.html", page), ("manifest.webmanifest", MANIFEST), ("sw.js", SW)):
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
            f.write(content)
    make_png(192, os.path.join(OUT, "icon-192.png"))
    make_png(512, os.path.join(OUT, "icon-512.png"))
    print(f"Site généré : {len(matches)} matchs -> {OUT}/")


if __name__ == "__main__":
    main()
