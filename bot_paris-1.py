#!/usr/bin/env python3
"""
Bot d'analyse de paris sportifs (football) - modèle de Poisson.

Chaque jour il :
  1. repère TOUS les matchs du jour (tous championnats couverts par football-data.co.uk),
  2. télécharge l'historique de chaque championnat concerné,
  3. calcule les probabilités : victoire domicile / nul / victoire extérieur, +2.5 buts, les deux marquent,
  4. compare avec les cotes pour repérer les "value bets",
  5. affiche un rapport adapté au téléphone et l'enregistre dans reports/.

Utilisation (aucun réglage nécessaire) :
  python3 bot_paris.py                 # tous les matchs d'aujourd'hui
  python3 bot_paris.py --days 3        # les 3 prochains jours
  python3 bot_paris.py --div E0 F1     # (optionnel) limiter à certains championnats

Aucune bibliothèque externe nécessaire (Python 3.8+).
"""
import argparse
import csv
import io
import math
import os
import ssl
import urllib.request
from datetime import date, datetime, timedelta

BASE = "https://www.football-data.co.uk"
CACHE = "cache"
REPORTS = "reports"
MAX_GOALS = 10
MIN_HISTORY = 40  # nombre minimum de matchs passés pour faire confiance au modèle

LEAGUES = {
    "E0": "Angleterre - Premier League", "E1": "Angleterre - Championship",
    "E2": "Angleterre - League One", "E3": "Angleterre - League Two",
    "EC": "Angleterre - Conference", "SC0": "Écosse - Premiership",
    "SC1": "Écosse - Championship", "SC2": "Écosse - League One",
    "SC3": "Écosse - League Two", "D1": "Allemagne - Bundesliga",
    "D2": "Allemagne - 2. Bundesliga", "I1": "Italie - Serie A",
    "I2": "Italie - Serie B", "SP1": "Espagne - Liga", "SP2": "Espagne - Liga 2",
    "F1": "France - Ligue 1", "F2": "France - Ligue 2", "N1": "Pays-Bas - Eredivisie",
    "B1": "Belgique - Pro League", "P1": "Portugal - Liga", "T1": "Turquie - Süper Lig",
    "G1": "Grèce - Super League",
}


def current_seasons():
    """Codes de saison automatiques (ex. octobre 2026 -> ['2627', '2526'])."""
    t = date.today()
    y = t.year if t.month >= 7 else t.year - 1
    return [f"{y % 100:02d}{(y + 1) % 100:02d}", f"{(y - 1) % 100:02d}{y % 100:02d}"]


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def fetch(url, cache_name):
    """Télécharge un CSV ; en cas d'échec réseau, utilise la copie locale."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, cache_name)
    last_err = None
    for u in (url, url.replace("https://", "http://")):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=25, context=_ssl_context()) as r:
                raw = r.read().decode("utf-8-sig", errors="replace")
            with open(path, "w", encoding="utf-8") as f:
                f.write(raw)
            return raw
        except Exception as e:
            last_err = e
    if os.path.exists(path):
        print(f"[!] Réseau indisponible -> copie locale de {cache_name}")
        with open(path, encoding="utf-8") as f:
            return f.read()
    print(f"[!] Impossible de récupérer {url} : {last_err}")
    return ""


def parse_date(s):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    return None


def read_rows(raw):
    return list(csv.DictReader(io.StringIO(raw))) if raw else []


def to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_results(div, seasons):
    matches = []
    for season in seasons:
        raw = fetch(f"{BASE}/mmz4281/{season}/{div}.csv", f"{div}_{season}.csv")
        for r in read_rows(raw):
            hg, ag = to_float(r.get("FTHG")), to_float(r.get("FTAG"))
            if hg is None or ag is None or not r.get("HomeTeam"):
                continue
            matches.append((r["HomeTeam"], r["AwayTeam"], hg, ag))
    return matches


def build_model(matches, prior=5):
    """Force d'attaque/défense (domicile et extérieur) avec lissage vers la moyenne."""
    if not matches:
        return None
    hg_avg = sum(m[2] for m in matches) / len(matches)
    ag_avg = sum(m[3] for m in matches) / len(matches)
    if hg_avg == 0 or ag_avg == 0:
        return None
    t = {}
    for h, a, hg, ag in matches:
        t.setdefault(h, dict(hf=0, ha=0, hn=0, af=0, aa=0, an=0))
        t.setdefault(a, dict(hf=0, ha=0, hn=0, af=0, aa=0, an=0))
        t[h]["hf"] += hg; t[h]["ha"] += ag; t[h]["hn"] += 1
        t[a]["af"] += ag; t[a]["aa"] += hg; t[a]["an"] += 1
    f = {}
    for team, s in t.items():
        f[team] = dict(
            att_h=((s["hf"] + prior * hg_avg) / (s["hn"] + prior)) / hg_avg,
            def_h=((s["ha"] + prior * ag_avg) / (s["hn"] + prior)) / ag_avg,
            att_a=((s["af"] + prior * ag_avg) / (s["an"] + prior)) / ag_avg,
            def_a=((s["aa"] + prior * hg_avg) / (s["an"] + prior)) / hg_avg,
        )
    return dict(hg=hg_avg, ag=ag_avg, teams=f, n=len(matches))


def poisson(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def predict(model, home, away):
    tm = model["teams"]
    if home not in tm or away not in tm:
        return None
    lam_h = model["hg"] * tm[home]["att_h"] * tm[away]["def_a"]
    lam_a = model["ag"] * tm[away]["att_a"] * tm[home]["def_h"]
    ph = [poisson(i, lam_h) for i in range(MAX_GOALS + 1)]
    pa = [poisson(i, lam_a) for i in range(MAX_GOALS + 1)]
    p1 = px = p2 = over = btts = 0.0
    for i in range(MAX_GOALS + 1):
        for j in range(MAX_GOALS + 1):
            p = ph[i] * pa[j]
            if i > j: p1 += p
            elif i == j: px += p
            else: p2 += p
            if i + j > 2: over += p
            if i > 0 and j > 0: btts += p
    tot = p1 + px + p2
    return dict(lam_h=lam_h, lam_a=lam_a, p1=p1 / tot, px=px / tot, p2=p2 / tot,
                over25=over, btts=btts)


def get_odds(row):
    """Cotes 1/N/2 : moyenne du marché si dispo, sinon Bet365."""
    for keys in (("AvgH", "AvgD", "AvgA"), ("B365H", "B365D", "B365A")):
        o = [to_float(row.get(k)) for k in keys]
        if all(o):
            return o
    return None


def analyse_div(div, fx_rows, seasons, min_edge):
    model = build_model(load_results(div, seasons))
    if not model or model["n"] < MIN_HISTORY:
        return []
    out = []
    for r in fx_rows:
        pred = predict(model, r["HomeTeam"], r["AwayTeam"])
        if not pred:
            continue
        odds = get_odds(r)
        picks = []
        if odds:
            for label, p, o in (("1", pred["p1"], odds[0]), ("N", pred["px"], odds[1]),
                                ("2", pred["p2"], odds[2])):
                edge = p * o - 1  # espérance de gain pour 1 misé
                if edge >= min_edge:
                    picks.append((label, p, o, edge))
        out.append(dict(div=div, date=parse_date(r["Date"]), home=r["HomeTeam"],
                        away=r["AwayTeam"], pred=pred, odds=odds, picks=picks))
    return out


def pct(x):
    return f"{x * 100:.0f}%"


def favourite(m):
    p = m["pred"]
    best = max((p["p1"], m["home"]), (p["px"], "Nul"), (p["p2"], m["away"]))
    return best


def build_report(matches, start, end, min_edge):
    L = [f"PARIS DU JOUR  {start}" + (f" -> {end}" if end != start else ""),
         f"{len(matches)} matchs analysés"]
    if not matches:
        L.append("\nAucun match trouvé (pas de match aujourd'hui, ou données indisponibles).")

    values = sorted(((pk[3], m, pk) for m in matches for pk in m["picks"]), key=lambda x: -x[0])
    L.append("\n=== VALUE BETS (cote > risque réel) ===")
    if values:
        for edge, m, (label, prob, odd, _) in values[:8]:
            who = {"1": m["home"], "N": "Match nul", "2": m["away"]}[label]
            L.append(f"* {m['home']} - {m['away']}\n  Pari : {who} | proba {pct(prob)} | cote {odd:.2f} | avantage {edge*100:+.0f}%")
    else:
        L.append("Aucun value bet aujourd'hui : mieux vaut ne pas parier.")

    L.append("\n=== FAVORIS LES PLUS NETS ===")
    for m in sorted(matches, key=lambda m: -favourite(m)[0])[:5]:
        prob, who = favourite(m)
        L.append(f"* {m['home']} - {m['away']} : {who} {pct(prob)}")

    L.append("\n=== TOUS LES MATCHS ===")
    cur = None
    for m in sorted(matches, key=lambda m: (m["div"], m["date"] or date.min)):
        if m["div"] != cur:
            cur = m["div"]
            L.append(f"\n-- {LEAGUES.get(cur, cur)} --")
        p = m["pred"]
        L.append(f"{m['home']} - {m['away']}\n  Dom {pct(p['p1'])} | Nul {pct(p['px'])} | Ext {pct(p['p2'])}"
                 f" | +2.5 buts {pct(p['over25'])} | BTTS {pct(p['btts'])}")
    L.append("\nAVERTISSEMENT : aucun modèle ne garantit un gain. Les bookmakers gardent une marge,")
    L.append("la plupart des parieurs perdent sur la durée. Ne mise que ce que tu peux te permettre de perdre.")
    return "\n".join(L)


def run_analysis(days=1, divs=None, seasons=None, min_edge=0.05):
    """Analyse complète : retourne le texte du rapport (utilisé par le script et l'appli)."""
    seasons = seasons or current_seasons()
    start = date.today()
    end = start + timedelta(days=max(days, 1) - 1)

    fx = []
    for r in read_rows(fetch(f"{BASE}/fixtures.csv", "fixtures.csv")):
        d = parse_date(r.get("Date", ""))
        if d and start <= d <= end and r.get("HomeTeam") and (not divs or r.get("Div") in divs):
            fx.append(r)
    div_list = sorted({r["Div"] for r in fx})

    matches = []
    for div in div_list:
        matches += analyse_div(div, [r for r in fx if r["Div"] == div], seasons, min_edge)
    return build_report(matches, start, end, min_edge)


def main():
    ap = argparse.ArgumentParser(description="Bot d'analyse de paris sportifs (football)")
    ap.add_argument("--div", nargs="+", default=None, help="limiter à certains championnats (E0 F1 ...)")
    ap.add_argument("--seasons", nargs="+", default=None, help="saisons (défaut : automatique)")
    ap.add_argument("--days", type=int, default=1, help="nombre de jours à analyser (défaut : aujourd'hui)")
    ap.add_argument("--min-edge", type=float, default=0.05, help="avantage minimum pour un value bet")
    args = ap.parse_args()

    report = run_analysis(args.days, args.div, args.seasons, args.min_edge)
    print(report)
    os.makedirs(REPORTS, exist_ok=True)
    path = os.path.join(REPORTS, f"rapport_{date.today()}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n[Rapport enregistré : {path}]")


if __name__ == "__main__":
    main()
