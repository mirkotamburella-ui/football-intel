"""AGENT 8 – MATHEMATICAL PREDICTION.

Modelli (ognuno indipendente, poi combinati in un ensemble trasparente):
  M1 "strength"   Poisson con forze attacco/difesa da tassi casa/trasferta (shrinkage)
  M2 "glm"        regressione di Poisson log-lineare (ridge, pesi temporali) sui risultati
  M3 "xg"         Poisson su xG/xGA (solo se disponibili)
  M4 "elo"        Elo dai risultati → probabilità 1X2
  MC              Monte Carlo: incertezza parametri (intervalli 5–95%) e verifica simulata
"""
from __future__ import annotations

import math
from statistics import mean, pstdev

from ..context import get_standings, get_team_results, src, team_view
from ..stats.elo import Elo
from ..stats.glm import PoissonGLM
from ..stats.montecarlo import match_intervals, simulate_scorelines
from ..stats.poisson import market_probs, most_likely_scores, score_matrix
from .a01_fixture_scout import fixtures_from
from .base import Agent

SHRINK = 6
# pesi dell'ensemble sui λ (media geometrica pesata). Nella verifica dell'8/11/2026 su 107
# partite la GLM è risultata la più vicina alle probabilità eque di mercato (scarto medio
# 6,1 pp sull'1 contro 9,1 pp del modello "strength"), quindi pesa di più. Ipotesi da
# ricalibrare con lo storico dei risultati (comando `verify`).
ENSEMBLE_WEIGHTS = {"glm": 0.55, "strength": 0.20, "xg": 0.25}


class MathematicalPrediction(Agent):
    id = "A08"
    name = "Mathematical Prediction"
    depends_on = ("A01",)
    description = "Distribuzione di Poisson (Dixon–Coles), regressione di Poisson, Elo, modelli xG e simulazioni Monte Carlo; stima le probabilità dei mercati con intervalli di incertezza."

    def league_models(self, ctx, slug, fixtures):
        """Adatta GLM ed Elo una sola volta per competizione sui risultati disponibili."""
        def build():
            pool = {}
            for fx in fixtures:
                if fx.get("league_slug") != slug:
                    continue
                for side in ("home", "away"):
                    res, sources = get_team_results(ctx, slug, fx[side]["id"])
                    for s in sources:
                        self.add_source(s)
                    for m in res:
                        pool[m["event_id"]] = m
            matches = list(pool.values())
            if len(matches) < 10:
                return None
            glm = PoissonGLM().fit(matches, ctx.now_utc)
            elo = Elo().fit(matches)
            cur = [m for m in matches if not m.get("prev_season")]
            draw_rate = mean(m["home_goals"] == m["away_goals"] for m in cur) if len(cur) >= 20 else 0.26
            draw_rate = (draw_rate * len(cur) + 0.26 * 40) / (len(cur) + 40)  # shrink verso media storica ~26%
            home_g = [m["home_goals"] for m in cur if not m.get("neutral")]
            away_g = [m["away_goals"] for m in cur if not m.get("neutral")]
            profile = None
            if len(cur) >= 20:
                profile = {"matches": len(cur), "season": "stagione in corso",
                           "goals_per_match": round(mean(m["home_goals"] + m["away_goals"] for m in cur), 2),
                           "draw_rate": round(mean(m["home_goals"] == m["away_goals"] for m in cur), 3),
                           "home_win_rate": round(mean(m["home_goals"] > m["away_goals"] for m in cur), 3),
                           "over25_rate": round(mean(m["home_goals"] + m["away_goals"] > 2.5 for m in cur), 3),
                           "btts_rate": round(mean(m["home_goals"] > 0 and m["away_goals"] > 0 for m in cur), 3),
                           "home_advantage_glm": round(glm.home_advantage(), 3) if glm.fitted else None}
            return {"glm": glm, "elo": elo, "n_matches": len(matches), "draw_rate": draw_rate, "profile": profile,
                    "home_avg": mean(home_g) if len(home_g) >= 10 else None,
                    "away_avg": mean(away_g) if len(away_g) >= 10 else None}
        return ctx.memo(f"league_models:{slug}", build)

    def analyze(self, ctx, inputs):
        fixtures = fixtures_from(inputs)
        seed = ctx.settings.random_seed

        def one(fx):
            slug = fx.get("league_slug")
            hid, aid = fx["home"]["id"], fx["away"]["id"]
            lm = self.league_models(ctx, slug, fixtures)
            res_h, _ = get_team_results(ctx, slug, hid)
            res_a, _ = get_team_results(ctx, slug, aid)
            gh, ga = team_view(res_h, hid, current_only=False), team_view(res_a, aid, current_only=False)
            n_h, n_a = len(gh), len(ga)
            models, lambdas = {}, {}
            neutral = bool(fx.get("neutral_site"))

            # M1 strength (tassi casa/trasferta + shrinkage verso medie di lega)
            st, f_st = get_standings(ctx, slug)
            if f_st:
                self.add_source(src(f_st))
            if lm and lm["home_avg"] and lm["away_avg"]:
                # ultime 20 partite di campionato (stagione corrente + coda della precedente)
                hh = [g for g in gh[-20:] if g["home"]]
                aa = [g for g in ga[-20:] if not g["home"]]
                if len(hh) >= 3 and len(aa) >= 3:
                    mh, ma = lm["home_avg"], lm["away_avg"]
                    att_h = (sum(g["gf"] for g in hh) + mh * SHRINK) / (len(hh) + SHRINK) / mh
                    def_h = (sum(g["ga"] for g in hh) + ma * SHRINK) / (len(hh) + SHRINK) / ma
                    att_a = (sum(g["gf"] for g in aa) + ma * SHRINK) / (len(aa) + SHRINK) / ma
                    def_a = (sum(g["ga"] for g in aa) + mh * SHRINK) / (len(aa) + SHRINK) / mh
                    lambdas["strength"] = (mh * att_h * def_a, ma * att_a * def_h)
            # M2 regressione di Poisson
            if lm and lm["glm"].fitted:
                p = lm["glm"].predict(hid, aid, neutral)
                if p and lm["glm"].games_per_team.get(hid, 0) >= 3 and lm["glm"].games_per_team.get(aid, 0) >= 3:
                    lambdas["glm"] = p
            # M3 xG (se A03/API-Football li ha resi disponibili tramite cache condivisa)
            if ctx.api_football.enabled:
                af = ctx.api_football
                # stesse chiavi memo di A03: il calcolo (e il budget API) avviene una volta sola
                fxa = ctx.memo(f"af_fixture:{fx['event_id']}", lambda: af.find_fixture(ctx.today.isoformat(), [fx["home"]["name"], fx["home"].get("espn_name")], [fx["away"]["name"], fx["away"].get("espn_name")], fx["kickoff_utc"]))
                if fxa:
                    th, ta = fxa["teams"]["home"]["id"], fxa["teams"]["away"]["id"]
                    xh = ctx.memo(f"af_xg:{th}", lambda: af.team_xg(th))
                    xa = ctx.memo(f"af_xg:{ta}", lambda: af.team_xg(ta))
                    if xh and xa:
                        lambdas["xg"] = ((xh["xg_for"] + xa["xga"]) / 2, (xa["xg_for"] + xh["xga"]) / 2)
            for name, (lh, la) in lambdas.items():
                models[name] = {"lambda_home": round(lh, 3), "lambda_away": round(la, 3),
                                "probs": {k: round(v, 4) for k, v in market_probs(score_matrix(lh, la)).items()}}
            # M4 Elo
            if lm and lm["elo"].n.get(hid, 0) >= 5 and lm["elo"].n.get(aid, 0) >= 5:
                e = lm["elo"]
                models["elo"] = {"rating_home": round(e.rating(hid)), "rating_away": round(e.rating(aid)),
                                 "games_home": e.n[hid], "games_away": e.n[aid],
                                 "probs": {k: round(v, 4) for k, v in e.probs_1x2(hid, aid, lm["draw_rate"], neutral).items()}}
            if not lambdas:
                return {"available": False, "models": models,
                        "note": "dati insufficienti per i modelli di Poisson (servono ≥3 partite casa/trasferta e ≥10 risultati di lega)"}

            # Ensemble: media geometrica dei λ, poi Elo mescolato sull'1X2 con peso proporzionale al campione
            ws = {k: ENSEMBLE_WEIGHTS[k] for k in lambdas}
            tw = sum(ws.values())
            lh = math.exp(sum(ws[k] * math.log(v[0]) for k, v in lambdas.items()) / tw)
            la = math.exp(sum(ws[k] * math.log(v[1]) for k, v in lambdas.items()) / tw)
            m = score_matrix(lh, la)
            ens = market_probs(m)
            elo_w = 0.0
            if "elo" in models:
                elo_w = min(0.3, min(models["elo"]["games_home"], models["elo"]["games_away"]) / 60)
                ep = models["elo"]["probs"]
                for k in ("1X2|HOME", "1X2|DRAW", "1X2|AWAY"):
                    ens[k] = (1 - elo_w) * ens[k] + elo_w * ep[k]
                ens["DC|1X"] = ens["1X2|HOME"] + ens["1X2|DRAW"]
                ens["DC|X2"] = ens["1X2|AWAY"] + ens["1X2|DRAW"]
                ens["DC|12"] = ens["1X2|HOME"] + ens["1X2|AWAY"]
                ens["DNB|HOME"], ens["DNB|AWAY"] = ens["1X2|HOME"], ens["1X2|AWAY"]
            # accordo tra modelli (dev. standard della prob. di vittoria casa)
            homes = [mm["probs"]["1X2|HOME"] for mm in models.values()]
            overs = [mm["probs"]["OU|OVER|2.5"] for mm in models.values() if "OU|OVER|2.5" in mm["probs"]]
            disagreement = round(max(pstdev(homes) if len(homes) > 1 else 0, pstdev(overs) if len(overs) > 1 else 0), 4)
            # Monte Carlo: incertezza dei parametri → intervalli (CV ~ 1/sqrt(partite))
            n_min = max(1, min(n_h, n_a))
            cv = min(0.35, max(0.08, 0.9 / math.sqrt(n_min)))
            intervals = match_intervals(lh, la, cv, draws=200, seed=seed + int(fx["event_id"]) % 1000)
            # gli intervalli Monte Carlo sono attorno alla stima Poisson: li si ricentra sull'ensemble
            # (che per l'1X2 include l'Elo) mantenendo le ampiezze
            pois = market_probs(m)
            intervals = {k: (max(0.0, min(ens[k], lo + ens[k] - pois[k])), min(1.0, max(ens[k], hi + ens[k] - pois[k])))
                         for k, (lo, hi) in intervals.items() if k in ens}
            sim = simulate_scorelines(lh, la, n=ctx.settings.monte_carlo_sims, seed=seed)
            sim_check = round(max(abs(sim[k] - market_probs(score_matrix(lh, la, rho=0))[k]) for k in sim), 4)
            return {
                "available": True,
                "lambda_home": round(lh, 3), "lambda_away": round(la, 3),
                "models_used": sorted(models), "models": models, "ensemble_weights": {k: round(w / tw, 2) for k, w in ws.items()}, "elo_weight_1x2": round(elo_w, 2),
                "ensemble": {k: round(v, 4) for k, v in ens.items()},
                "intervals_90": {k: [round(v[0], 3), round(v[1], 3)] for k, v in intervals.items()},
                "lambda_cv": round(cv, 3),
                "model_disagreement_sd": disagreement,
                "most_likely_scores": most_likely_scores(m),
                "monte_carlo": {"simulations": ctx.settings.monte_carlo_sims, "max_abs_diff_vs_analytic_rho0": sim_check},
                "sample": {"home_games": n_h, "away_games": n_a, "league_matches_in_fit": lm["n_matches"] if lm else 0},
                "league_profile": lm.get("profile") if lm else None,
            }

        matches = self.per_match(ctx, fixtures, one)
        return {"matches_analyzed": len(matches), "with_model": sum(1 for x in matches.values() if x.get("available")),
                "models": ["strength (Poisson)", "glm (regressione di Poisson)", "xg (se disponibile)", "elo", "monte carlo"]}, matches
