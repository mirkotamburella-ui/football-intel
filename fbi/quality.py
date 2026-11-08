"""Controllo qualità: impedisce dati inventati, quote vecchie, partite non verificate,
doppioni, selezioni basate solo sull'intuito, falsa precisione e consigli senza dati."""
from __future__ import annotations

from datetime import timedelta

from .providers.espn import parse_dt


def qc_selection(ctx, c: dict, score: dict, risk_match: dict, risk_sel: dict, fixture: dict, model: dict) -> list[str]:
    s = ctx.settings
    fails = []
    # dati reali e tracciabili
    if not c.get("odds_source") or not c.get("odds_fetched_at"):
        fails.append("quota senza fonte o timestamp")
    else:
        age = (ctx.now_utc - parse_dt(c["odds_fetched_at"])).total_seconds() / 60
        if age > s.max_odds_age_minutes:
            fails.append(f"quota non aggiornata ({age:.0f} min > {s.max_odds_age_minutes})")
    if s.require_preferred_bookmaker and not c.get("preferred_bookmaker"):
        fails.append(f"quota disponibile solo presso bookmaker non preferito ({c.get('bookmaker')})")
    # partita verificata e non iniziata
    if fixture.get("state") != "pre":
        fails.append("partita non in stato pre-match")
    if parse_dt(fixture["kickoff_utc"]) < ctx.now_utc + timedelta(minutes=s.min_minutes_before_kickoff):
        fails.append("partita già iniziata o in procinto di iniziare")
    if not fixture.get("league_slug"):
        fails.append("competizione non verificata")
    # niente scelte basate solo sull'intuito: serve un modello statistico
    if not model.get("available") or c.get("p_model") is None:
        fails.append("nessun modello statistico a supporto")
    # dati sufficienti
    fails += risk_sel.get("exclusion_reasons", [])
    # soglie
    if score["final"] < s.min_selection_score:
        fails.append(f"punteggio {score['final']} < {s.min_selection_score}")
    if c["ev"] < s.min_ev:
        fails.append(f"EV {c['ev']:+.1%} < soglia {s.min_ev:+.1%}")
    if c["odds"] < s.min_leg_odds:
        fails.append(f"quota {c['odds']} < minimo {s.min_leg_odds}")
    return sorted(set(fails), key=fails.index)


def round_prob(p: float) -> float:
    """Contro la falsa precisione: probabilità mostrate al punto percentuale intero."""
    return round(p, 2)


def qc_report(report: dict) -> list[str]:
    """Verifiche globali sul report finale (usate anche nei test)."""
    problems = []
    for name in ("main_accumulator", "prudent_accumulator"):
        acc = report.get(name) or {}
        legs = acc.get("legs") or []
        ids = [l["event_id"] for l in legs]
        if len(ids) != len(set(ids)):
            problems.append(f"{name}: partite duplicate")
        if acc.get("status") == "ok":
            if not (5 <= len(legs) <= 10):
                problems.append(f"{name}: numero di eventi fuori da 5–10")
            if acc.get("stake_eur", 0) > 10:
                problems.append(f"{name}: puntata oltre 10 €")
            for l in legs:
                if not l.get("odds_source") or not l.get("odds_fetched_at"):
                    problems.append(f"{name}: gamba senza fonte quota")
    return problems
