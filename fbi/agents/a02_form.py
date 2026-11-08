"""AGENT 2 – TEAM FORM ANALYST: forma recente (5/10), casa/trasferta, continuità, qualità avversari."""
from __future__ import annotations

from statistics import mean, pstdev

from ..context import get_standings, get_summary, get_team_results, src, standings_row, team_view
from ..providers import espn as E
from .a01_fixture_scout import fixtures_from
from .base import Agent


def record(games: list[dict]) -> dict | None:
    if not games:
        return None
    pts = [g["points"] for g in games]
    return {
        "n": len(games),
        "w": sum(g["result"] == "W" for g in games), "d": sum(g["result"] == "D" for g in games),
        "l": sum(g["result"] == "L" for g in games),
        "gf": sum(g["gf"] for g in games), "ga": sum(g["ga"] for g in games),
        "ppg": round(mean(pts), 2),
        "sequence": "".join(g["result"] for g in games[::-1]),  # più recente a sinistra
    }


def team_form(ctx, fx, side, agent) -> dict:
    team = fx[side]
    tid, slug = team["id"], fx.get("league_slug")
    res, sources = get_team_results(ctx, slug, tid)
    for s in sources:
        agent.add_source(s)
    league = team_view(res, tid)
    st, f_st = get_standings(ctx, slug)
    if f_st:
        agent.add_source(src(f_st))
    _, table = standings_row(st, tid)

    out = {"team": team["name"], "league_games_season": len(league)}
    out["last5_league"] = record(league[-5:])
    out["last10_league"] = record(league[-10:])
    out["home_season"] = record([g for g in league if g["home"]])
    out["away_season"] = record([g for g in league if not g["home"]])
    if len(league) >= 3:
        pts = [g["points"] for g in league[-10:]]
        out["consistency_sd_points"] = round(pstdev(pts), 2)  # più basso = risultati più costanti
    # qualità degli avversari: punti per partita degli avversari affrontati (classifica attuale)
    if table and league:
        opp_ppg = [table[g["opponent_id"]]["pts"] / table[g["opponent_id"]]["gp"]
                   for g in league[-10:] if g["opponent_id"] in table and table[g["opponent_id"]]["gp"]]
        league_ppg = mean(r["pts"] / r["gp"] for r in table.values() if r["gp"]) if table else None
        if opp_ppg and league_ppg:
            out["opponents_avg_ppg_last10"] = round(mean(opp_ppg), 2)
            out["league_avg_ppg"] = round(league_ppg, 2)
            # forma corretta per avversari: ppg × (qualità avversari / media lega)
            out["adjusted_ppg_last10"] = round(out["last10_league"]["ppg"] * mean(opp_ppg) / league_ppg, 2)
    # ultime 5 in tutte le competizioni (ESPN summary)
    sm = get_summary(ctx, fx["event_id"])
    if sm:
        agent.add_source(src(sm))
        l5 = E.parse_last_five(sm).get(tid) or []
        if l5:
            out["last5_all_competitions"] = {
                "sequence": "".join((g["result"] or "?") for g in l5),
                "gf": sum(g["gf"] for g in l5), "ga": sum(g["ga"] for g in l5),
                "matches": [{"date": g["date"], "competition": g["competition"], "opponent": g["opponent"],
                             "home": g["home"], "score": f"{g['gf']}-{g['ga']}", "result": g["result"]} for g in l5],
            }
            out["last_match_date"] = max(g["date"] for g in l5)
    # indice sintetico di forma (0–3 punti/partita): 60% ultime 5, 40% ultime 10, sul campionato
    l5r, l10r = out["last5_league"], out["last10_league"]
    if l5r and l10r:
        out["form_index"] = round(0.6 * l5r["ppg"] + 0.4 * (out.get("adjusted_ppg_last10") or l10r["ppg"]), 2)
    elif out.get("last5_all_competitions"):
        seq = out["last5_all_competitions"]["sequence"]
        pts = [3 if c == "W" else 1 if c == "D" else 0 for c in seq if c in "WDL"]
        out["form_index"] = round(mean(pts), 2) if pts else None
        out["form_index_basis"] = "ultime 5 in tutte le competizioni (campionato non disponibile)"
    else:
        out["form_index"] = None
    # forma specifica del contesto (casa per chi gioca in casa, trasferta per gli ospiti)
    venue = out["home_season"] if side == "home" else out["away_season"]
    out["venue_ppg"] = venue["ppg"] if venue and venue["n"] >= 3 else None
    out["sources"] = sources
    return out


class TeamFormAnalyst(Agent):
    id = "A02"
    name = "Team Form Analyst"
    depends_on = ("A01",)
    description = "Forma recente (ultime 5 e 10), V/N/P, gol fatti/subiti, rendimento casa/trasferta, continuità e qualità degli avversari affrontati."

    def analyze(self, ctx, inputs):
        fixtures = fixtures_from(inputs)

        def one(fx):
            h, a = team_form(ctx, fx, "home", self), team_form(ctx, fx, "away", self)
            edge = None
            if h.get("form_index") is not None and a.get("form_index") is not None:
                hv = h["venue_ppg"] if h.get("venue_ppg") is not None else h["form_index"]
                av = a["venue_ppg"] if a.get("venue_ppg") is not None else a["form_index"]
                edge = round(0.7 * (h["form_index"] - a["form_index"]) + 0.3 * (hv - av), 2)
            return {"home": h, "away": a, "form_edge_home": edge,
                    "note": None if edge is not None else "forma non calcolabile: dati insufficienti"}

        matches = self.per_match(ctx, fixtures, one)
        ok = sum(1 for m in matches.values() if m.get("form_edge_home") is not None)
        return {"matches_analyzed": len(matches), "with_complete_form": ok}, matches
