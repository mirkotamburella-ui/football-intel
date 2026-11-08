"""AGENT 10 – RISK MANAGER: qualità dei dati, rischio per selezione, correlazioni, esclusioni."""
from __future__ import annotations

from datetime import timedelta

from ..leagues import TIER_RELIABILITY, tier
from ..providers.espn import parse_dt
from .base import Agent

DQ_WEIGHTS = {
    "standings": 0.15, "sample_size": 0.25, "odds_books": 0.15, "odds_fresh": 0.10,
    "models": 0.15, "h2h": 0.05, "lineups_injuries": 0.10, "xg": 0.05,
}


def data_quality(ctx, fx, a) -> tuple[float, dict, list[str]]:
    s = ctx.settings
    eid = fx["event_id"]
    form, offdef, h2h, lin, mot, odds, mth = (a[k]["matches"].get(eid, {}) for k in ("A02", "A03", "A04", "A05", "A06", "A07", "A08"))
    comp, notes = {}, []
    comp["standings"] = 1.0 if (mot.get("home", {}).get("rank") and mot.get("away", {}).get("rank")) else 0.0
    if not comp["standings"]:
        notes.append("classifica non disponibile")
    n_h = form.get("home", {}).get("league_games_season", 0)
    n_a = form.get("away", {}).get("league_games_season", 0)
    n_min = min(n_h, n_a)
    comp["sample_size"] = min(1.0, n_min / 10)
    if n_min < s.min_team_games:
        notes.append(f"campione ridotto: {n_min} partite di campionato per la squadra con meno dati")
    books = len(odds.get("bookmakers", []))
    comp["odds_books"] = 1.0 if books >= 2 else 0.5 if books == 1 else 0.0
    ages = []
    for mk in (odds.get("markets") or {}).values():
        try:
            ages.append((ctx.now_utc - parse_dt(mk["playable"]["fetched_at"])).total_seconds() / 60)
        except (KeyError, ValueError):
            pass
    max_age = max(ages) if ages else None
    comp["odds_fresh"] = 1.0 if max_age is not None and max_age <= s.max_odds_age_minutes else 0.0
    if max_age is not None and max_age > s.max_odds_age_minutes:
        notes.append(f"quote non aggiornate ({max_age:.0f} min)")
    nm = len(mth.get("models_used", []))
    comp["models"] = 1.0 if nm >= 3 else 0.7 if nm == 2 else 0.4 if nm == 1 else 0.0
    comp["h2h"] = 1.0 if h2h.get("available") else 0.0
    lin_ok = any(lin.get(sd, {}).get("injury_data") or lin.get(sd, {}).get("lineup", {}).get("status") == "ufficiale" for sd in ("home", "away"))
    comp["lineups_injuries"] = 1.0 if lin_ok else 0.0
    if not lin_ok:
        notes.append("formazioni/indisponibili non verificabili")
    comp["xg"] = 1.0 if offdef.get("home", {}).get("xg_for_pg") and offdef.get("away", {}).get("xg_for_pg") else 0.0
    base = sum(DQ_WEIGHTS[k] * v for k, v in comp.items())
    t = tier(fx.get("league_slug"))
    dq = base * (0.6 + 0.4 * TIER_RELIABILITY[t])
    if t == 3:
        notes.append("competizione con dati/quote poco affidabili (tier 3)")
    return round(dq, 3), {k: round(v, 2) for k, v in comp.items()}, notes


class RiskManager(Agent):
    id = "A10"
    name = "Risk Manager"
    depends_on = ("A01", "A02", "A03", "A04", "A05", "A06", "A07", "A08", "A09")
    description = "Elimina partite con dati insufficienti, segnala campionati poco affidabili e mercati troppo rischiosi, definisce le correlazioni tra selezioni."

    def analyze(self, ctx, inputs):
        s = ctx.settings
        out = {}
        for fx in inputs["A01"]["summary"]["fixtures"]:
            eid = fx["event_id"]
            dq, comp, notes = data_quality(ctx, fx, inputs)
            t = tier(fx.get("league_slug"))
            form = inputs["A02"]["matches"].get(eid, {})
            n_min = min(form.get("home", {}).get("league_games_season", 0), form.get("away", {}).get("league_games_season", 0))
            match_excl = []
            if dq < s.min_data_quality:
                match_excl.append(f"qualità dati {dq:.2f} < soglia {s.min_data_quality}")
            if n_min < s.min_team_games:
                match_excl.append(f"meno di {s.min_team_games} partite di campionato per squadra")
            if t == 3:
                match_excl.append("campionato con dati poco affidabili (escluso dalle multiple)")
            if parse_dt(fx["kickoff_utc"]) < ctx.now_utc + timedelta(minutes=s.min_minutes_before_kickoff):
                match_excl.append("partita in procinto di iniziare o iniziata")
            sel = []
            m8 = inputs["A08"]["matches"].get(eid, {})
            for c in inputs["A09"]["matches"].get(eid, {}).get("candidates", []):
                reasons = list(match_excl)
                p = c["p_est"]
                width = c["p_est_range_90"][1] - c["p_est_range_90"][0]
                if p < 0.35:
                    reasons.append("probabilità stimata < 35%: mercato eccessivamente rischioso")
                if c["odds"] > 3.5:
                    reasons.append("quota > 3.50: varianza troppo alta per una multipla")
                if c["key"] == "1X2|DRAW":
                    reasons.append("pareggio secco: mercato ad alta varianza, non usato nelle multiple")
                if m8.get("model_disagreement_sd", 0) > 0.08:
                    reasons.append("modelli statistici in forte disaccordo")
                level = 0 if p >= 0.70 else 1 if p >= 0.55 else 2
                if dq < 0.7 or width > 0.15:
                    level = min(2, level + 1)
                sel.append({"key": c["key"], "risk_level": ["Basso", "Medio", "Alto"][level], "prob_interval_width": round(width, 3),
                            "allowed_in_accumulator": not reasons, "exclusion_reasons": reasons})
            ko = parse_dt(fx["kickoff_utc"])
            out[eid] = {
                "data_quality": dq, "data_quality_components": comp, "data_notes": notes, "league_tier": t,
                "reliability_level": "Alta" if dq >= 0.8 else "Media" if dq >= 0.6 else "Bassa",
                "match_exclusions": match_excl, "selections": sel,
                "correlation_tags": {"event_id": eid, "league": fx.get("league_slug"), "kickoff_slot": ko.strftime("%Y%m%d%H")},
            }
        return {
            "matches_analyzed": len(out),
            "matches_excluded": sum(1 for v in out.values() if v["match_exclusions"]),
            "correlation_policy": {
                "same_match": "vietato: al massimo una selezione per partita (eventi fortemente correlati)",
                "same_league": s.same_league_correlation,
                "same_league_same_kickoff": round(s.same_league_correlation * 1.6, 3),
                "different_league": 0.0,
                "method": "copula gaussiana nella simulazione Monte Carlo della multipla",
            },
            "dq_weights": DQ_WEIGHTS,
        }, out
