"""AGENT 3 – OFFENSIVE & DEFENSIVE ANALYST: gol fatti/subiti, xG/xGA, clean sheet, frequenze Over/BTTS."""
from __future__ import annotations

from statistics import mean

from ..context import get_standings, get_team_results, src, standings_row, team_view
from ..stats.poisson import market_probs, score_matrix
from .a01_fixture_scout import fixtures_from
from .base import Agent

SHRINK_GAMES = 6  # pseudo-partite di "media di lega" aggiunte alle stime (shrinkage bayesiano)


def team_offdef(ctx, fx, side, agent):
    t = fx[side]
    slug, tid = fx.get("league_slug"), t["id"]
    res, sources = get_team_results(ctx, slug, tid)
    for s in sources:
        agent.add_source(s)
    g = team_view(res, tid)
    out = {"team": t["name"], "games": len(g)}
    if g:
        out.update({
            "gf_pg": round(mean(x["gf"] for x in g), 2), "ga_pg": round(mean(x["ga"] for x in g), 2),
            "clean_sheet_pct": round(mean(x["ga"] == 0 for x in g), 2),
            "failed_to_score_pct": round(mean(x["gf"] == 0 for x in g), 2),
            "over15_pct": round(mean(x["gf"] + x["ga"] > 1.5 for x in g), 2),
            "over25_pct": round(mean(x["gf"] + x["ga"] > 2.5 for x in g), 2),
            "under35_pct": round(mean(x["gf"] + x["ga"] < 3.5 for x in g), 2),
            "btts_pct": round(mean(x["gf"] > 0 and x["ga"] > 0 for x in g), 2),
            "last5_total_goals_avg": round(mean(x["gf"] + x["ga"] for x in g[-5:]), 2),
        })
        hv = [x for x in g if x["home"] == (side == "home")]
        if hv:
            out["venue_games"] = len(hv)
            out["venue_gf_pg"] = round(mean(x["gf"] for x in hv), 2)
            out["venue_ga_pg"] = round(mean(x["ga"] for x in hv), 2)
    # xG / xGA (solo con API-Football)
    xg = None
    if ctx.api_football.enabled:
        af = ctx.api_football
        fxa = ctx.memo(f"af_fixture:{fx['event_id']}", lambda: af.find_fixture(ctx.today.isoformat(), fx["home"]["name"], fx["away"]["name"], fx["kickoff_utc"]))
        if fxa:
            af_tid = fxa["teams"][side]["id"]
            xg = ctx.memo(f"af_xg:{af_tid}", lambda: af.team_xg(af_tid))
    if xg:
        out["xg_for_pg"], out["xga_pg"], out["xg_matches"] = xg["xg_for"], xg["xga"], xg["matches"]
        out["xg_source"] = "API-Football /fixtures/statistics"
        for s in xg["sources"]:
            agent.add_source({"source": s, "fetched_at": ctx.now_utc.isoformat()})
    else:
        out["xg_for_pg"] = out["xga_pg"] = None
        out["xg_note"] = "xG/xGA non disponibili (serve API_FOOTBALL_KEY o budget esaurito)" if not ctx.api_football.enabled else "xG/xGA non disponibili per questa squadra"
    out["shots_on_target"] = None
    out["shots_note"] = "tiri in porta/occasioni create non disponibili dalle fonti configurate"
    return out


class OffDefAnalyst(Agent):
    id = "A03"
    name = "Offensive & Defensive Analyst"
    depends_on = ("A01",)
    description = "Media gol fatti/subiti (totale e casa/trasferta), xG/xGA se disponibili, clean sheet, solidità difensiva e frequenze Over/BTTS; stima Poisson da tassi osservati."

    def analyze(self, ctx, inputs):
        fixtures = fixtures_from(inputs)

        def one(fx):
            h, a = team_offdef(ctx, fx, "home", self), team_offdef(ctx, fx, "away", self)
            st, f_st = get_standings(ctx, fx.get("league_slug"))
            if f_st:
                self.add_source(src(f_st))
            _, table = standings_row(st, fx["home"]["id"])
            out = {"home": h, "away": a}
            if table:
                gp = sum(r["gp"] for r in table.values())
                gf = sum(r["gf"] for r in table.values())
                if gp:
                    out["league_goals_per_team_game"] = round(gf / gp, 3)
            # stima "osservata": λ da tassi casa/trasferta con shrinkage verso la media di lega
            mu = out.get("league_goals_per_team_game")
            if mu and h.get("venue_games") and a.get("venue_games"):
                def shrink(x, n):
                    return (x * n + mu * SHRINK_GAMES) / (n + SHRINK_GAMES)
                att_h = shrink(h["venue_gf_pg"], h["venue_games"]) / mu
                def_a = shrink(a["venue_ga_pg"], a["venue_games"]) / mu
                att_a = shrink(a["venue_gf_pg"], a["venue_games"]) / mu
                def_h = shrink(h["venue_ga_pg"], h["venue_games"]) / mu
                lh, la = mu * att_h * def_a, mu * att_a * def_h
                if h.get("xg_for_pg") and a.get("xga_pg") and a.get("xg_for_pg") and h.get("xga_pg"):
                    # se disponibili, gli xG pesano il 50%
                    lh = 0.5 * lh + 0.5 * (h["xg_for_pg"] + a["xga_pg"]) / 2
                    la = 0.5 * la + 0.5 * (a["xg_for_pg"] + h["xga_pg"]) / 2
                    out["lambda_basis"] = "tassi casa/trasferta + xG (50/50)"
                else:
                    out["lambda_basis"] = "tassi gol casa/trasferta con shrinkage verso la media di lega"
                out["lambda_home"], out["lambda_away"] = round(lh, 3), round(la, 3)
                out["observed_rates_probs"] = {k: round(v, 4) for k, v in market_probs(score_matrix(lh, la)).items()}
            else:
                out["note"] = "stima da tassi osservati non disponibile: mancano classifica o partite casa/trasferta"
            return out

        matches = self.per_match(ctx, fixtures, one)
        return {"matches_analyzed": len(matches),
                "with_rates_model": sum(1 for m in matches.values() if m.get("observed_rates_probs")),
                "xg_available": ctx.api_football.enabled}, matches
