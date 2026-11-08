"""AGENT 11 – CONTRARIAN ANALYST: per ogni selezione cerca i motivi per cui potrebbe perdere.

Ogni obiezione è basata su un dato prodotto da un altro agente (citato in `evidence`)
e ha un peso; la somma dei pesi (max 10) è una penalità applicata al punteggio finale.
"""
from __future__ import annotations

from .base import Agent

HOME_SIDE = {"1X2|HOME", "DNB|HOME", "DC|1X"}
AWAY_SIDE = {"1X2|AWAY", "DNB|AWAY", "DC|X2"}


def side_of(key: str) -> str | None:
    return "home" if key in HOME_SIDE else "away" if key in AWAY_SIDE else None


def goals_direction(key: str) -> str | None:
    if key.startswith("OU|OVER") or key == "BTTS|YES":
        return "over"
    if key.startswith("OU|UNDER") or key == "BTTS|NO":
        return "under"
    return None


def challenge(c: dict, eid: str, a: dict) -> list[dict]:
    form, offdef, h2h, lin, mot, mth = (a[k]["matches"].get(eid, {}) for k in ("A02", "A03", "A04", "A05", "A06", "A08"))
    objs = []

    def obj(weight, text, evidence):
        objs.append({"weight": weight, "objection": text, "evidence": evidence})

    key, side, gdir = c["key"], side_of(c["key"]), goals_direction(c["key"])
    opp = {"home": "away", "away": "home"}.get(side or "")
    # 1. modello vs mercato
    if abs(c["edge_pp"]) >= 8:
        w = 4.0 if abs(c["edge_pp"]) >= 15 else 2.0
        obj(w, f"Il modello si discosta di {c['edge_pp']:+.0f} punti dal mercato: un divario così ampio è più spesso un errore "
               "del modello (informazioni che non vede) che un vero vantaggio",
            {"p_model": c["p_model"], "p_market_fair": c["p_market_fair"], "agent": "A09"})
    # 2. movimento di quota contrario (quota in crescita = mercato meno convinto)
    if c.get("movement_pct") is not None and c["movement_pct"] >= 5:
        obj(2.0, f"La quota è salita del {c['movement_pct']}% dall'apertura: il mercato si sta spostando contro la selezione",
            {"movement_pct": c["movement_pct"], "agent": "A07"})
    # 3. forma dell'avversario
    if opp and form.get(opp, {}).get("last5_league"):
        l5 = form[opp]["last5_league"]
        if l5["ppg"] >= 2.0:
            obj(2.0, f"{form[opp]['team']} è in ottima forma ({l5['sequence']}, {l5['ppg']} punti/partita nelle ultime 5)",
                {"last5": l5, "agent": "A02"})
    if side and form.get(side, {}).get("last5_league"):
        l5 = form[side]["last5_league"]
        if l5["ppg"] < 1.0:
            obj(1.5, f"{form[side]['team']} in cattiva forma recente ({l5['sequence']})", {"last5": l5, "agent": "A02"})
    # 4. tendenze gol contrarie
    if gdir and c["market"] == "OU":
        line = float(key.split("|")[2])
        avgs = [offdef.get(sd, {}).get("last5_total_goals_avg") for sd in ("home", "away")]
        avgs = [x for x in avgs if x is not None]
        if len(avgs) == 2:
            mean5 = sum(avgs) / 2
            if gdir == "over" and mean5 < line:
                obj(2.0, f"Media gol nelle ultime 5 delle due squadre ({mean5:.2f}) inferiore alla linea {line}", {"avgs": avgs, "agent": "A03"})
            if gdir == "under" and mean5 > line:
                obj(2.0, f"Media gol nelle ultime 5 delle due squadre ({mean5:.2f}) superiore alla linea {line}", {"avgs": avgs, "agent": "A03"})
    if key == "BTTS|YES":
        for sd in ("home", "away"):
            fts = offdef.get(sd, {}).get("failed_to_score_pct")
            if fts is not None and fts >= 0.35:
                obj(1.5, f"{offdef[sd]['team']} non segna nel {fts:.0%} delle partite", {"failed_to_score_pct": fts, "agent": "A03"})
    # 5. scontri diretti contrari (solo segnale secondario, peso ridotto)
    if h2h.get("available") and h2h.get("effective_sample", 0) >= 2.5:
        w = h2h["weighted"]
        contra = None
        if side == "home" and w["away_team_win"] >= 0.5:
            contra = "gli ospiti hanno vinto la maggioranza dei confronti recenti"
        elif side == "away" and w["home_team_win"] >= 0.5:
            contra = "i padroni di casa hanno vinto la maggioranza dei confronti recenti"
        elif key == "OU|OVER|2.5" and w["over25"] <= 0.3:
            contra = "i confronti diretti recenti sono stati quasi sempre Under 2.5"
        elif key == "OU|UNDER|2.5" and w["over25"] >= 0.7:
            contra = "i confronti diretti recenti sono stati quasi sempre Over 2.5"
        elif key == "BTTS|YES" and w["btts"] <= 0.3:
            contra = "nei confronti recenti raramente hanno segnato entrambe"
        if contra:
            obj(1.0, f"H2H: {contra}", {"weighted": w, "effective_sample": h2h["effective_sample"], "agent": "A04"})
    # 6. motivazioni asimmetriche
    if side and mot.get(opp, {}).get("stake_index") is not None and mot.get(side, {}).get("stake_index") is not None:
        if mot[opp]["stake_index"] - mot[side]["stake_index"] >= 0.3:
            obj(1.5, f"Motivazioni a favore dell'avversario ({', '.join(mot[opp].get('objectives') or []) or 'obiettivo di classifica'})",
                {"stake_selection_side": mot[side]["stake_index"], "stake_opponent": mot[opp]["stake_index"], "agent": "A06"})
    if side and mot.get(side, {}).get("turnover_risk") == "alto":
        obj(1.0, f"Rischio turnover/stanchezza per {mot[side].get('team')} ({mot[side].get('days_since_last_match')} giorni dall'ultima gara)",
            {"agent": "A06"})
    # 7. assenze
    if side and lin.get(side, {}).get("absences_count", 0) >= 2:
        obj(2.0, f"{lin[side]['team']}: {lin[side]['absences_count']} assenti segnalati", {"absences": lin[side]["absences"][:6], "agent": "A05"})
    if not any(lin.get(sd, {}).get("injury_data") for sd in ("home", "away")):
        obj(0.5, "Indisponibili non verificabili: possibili assenze non considerate", {"agent": "A05"})
    # 8. incertezza e campione
    lo, hi = c["p_est_range_90"]
    if hi - lo > 0.15:
        obj(1.0, f"Intervallo di probabilità ampio ({lo:.0%}–{hi:.0%})", {"agent": "A08/A09"})
    smp = mth.get("sample", {})
    if smp and min(smp.get("home_games", 0), smp.get("away_games", 0)) < 8:
        obj(1.5, "Campione statistico ridotto (< 8 partite per una delle squadre)", {"sample": smp, "agent": "A08"})
    if c.get("books_disagreement_pp") and c["books_disagreement_pp"] >= 4:
        obj(1.0, f"I bookmaker non concordano ({c['books_disagreement_pp']} punti percentuali di differenza)", {"agent": "A07"})
    # 9. partita confermata solo da ESPN
    ver = (a.get("A10") or {}).get("matches", {}).get(eid, {}).get("fixture_verification") if "A10" in a else None
    if not ver:
        sources = 1 + bool((a["A07"]["matches"].get(eid) or {}).get("odds_api_matched")) + bool(lin.get("api_football_fixture"))
        if sources < 2:
            obj(1.5, "Partita confermata da una sola fonte (ESPN): potrebbe essere rinviata o non offerta dal tuo bookmaker",
                {"agent": "A01/A07"})
    # 10. quota sopravvalutata?
    if not c["value"]:
        obj(1.0, f"EV stimato negativo ({c['ev']:+.1%}): la quota non compensa la probabilità", {"ev": c["ev"], "agent": "A09"})
    return objs


class ContrarianAnalyst(Agent):
    id = "A11"
    name = "Contrarian Analyst"
    depends_on = ("A01", "A02", "A03", "A04", "A05", "A06", "A07", "A08", "A09")
    description = "Contesta ogni selezione: statistiche contrarie, fattori non considerati, possibili errori di valutazione, quote sopravvalutate."

    def analyze(self, ctx, inputs):
        out, total = {}, 0
        for eid, v in inputs["A09"]["matches"].items():
            sel = {}
            for c in v.get("candidates", []):
                objs = challenge(c, eid, inputs)
                pen = round(min(10.0, sum(o["weight"] for o in objs)), 1)
                sel[c["key"]] = {"objections": objs, "penalty": pen}
                total += len(objs)
            out[eid] = {"selections": sel}
        return {"matches_analyzed": len(out), "objections_raised": total, "max_penalty": 10}, out
