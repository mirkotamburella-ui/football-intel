"""Costruzione delle multiple e calcolo della probabilità congiunta con correlazioni."""
from __future__ import annotations

import math

import numpy as np

from .stats.montecarlo import accumulator_joint


def corr_matrix(legs: list[dict], policy: dict) -> np.ndarray:
    n = len(legs)
    C = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = legs[i]["correlation_tags"], legs[j]["correlation_tags"]
            rho = 0.0
            if a["league"] and a["league"] == b["league"]:
                rho = policy["same_league_same_kickoff"] if a["kickoff_slot"] == b["kickoff_slot"] else policy["same_league"]
            C[i, j] = C[j, i] = rho
    return C


def evaluate(legs: list[dict], policy: dict, stake: float, seed: int) -> dict:
    probs = [l["p_est"] for l in legs]
    # deviazione standard dalla semi-ampiezza dell'intervallo 90% (≈ 1.645 σ)
    sds = [max(0.01, (l["p_est_range_90"][1] - l["p_est_range_90"][0]) / (2 * 1.645)) for l in legs]
    C = corr_matrix(legs, policy)
    j = accumulator_joint(probs, sds, C, seed=seed)
    total_odds = math.prod(l["odds"] for l in legs)
    return {
        "legs_count": len(legs),
        "total_odds": round(total_odds, 2),
        "stake_eur": stake,
        "potential_gross_return_eur": round(stake * total_odds, 2),
        "joint_probability": round(j["joint_prob"], 4),
        "joint_probability_range_90": [round(j["joint_prob_p5"], 4), round(j["joint_prob_p95"], 4)],
        "independent_product": round(j["independent_product"], 4),
        "expected_value": round(j["joint_prob"] * total_odds - 1, 3),
        "probability_method": f"Monte Carlo ({j['simulations']:,} simulazioni), copula gaussiana con correlazioni + incertezza Beta sulle probabilità",
        "push_note": "Le gambe Draw No Bet sono contate come vincenti solo in caso di vittoria (stima prudente: il rimborso riduce la quota)" if any(l["key"].startswith("DNB") for l in legs) else None,
    }


def best_per_match(selections: list[dict], key=lambda s: (s["score"]["final"], s["ev"])) -> list[dict]:
    best: dict[str, dict] = {}
    for s in selections:
        cur = best.get(s["event_id"])
        if cur is None or key(s) > key(cur):
            best[s["event_id"]] = s
    return list(best.values())


def build_main(eligible: list[dict], settings, policy: dict) -> dict:
    pool = sorted(best_per_match(eligible), key=lambda s: (-s["score"]["final"], -s["ev"]))
    if len(pool) < settings.acca_min_legs:
        return {"status": "not_possible", "eligible_count": len(pool),
                "reason": f"solo {len(pool)} selezioni superano tutti i controlli di qualità (ne servono almeno {settings.acca_min_legs}). "
                          "I criteri non vengono abbassati: oggi non è possibile costruire una multipla conforme."}
    legs = pool[: settings.acca_min_legs]
    ev_ = evaluate(legs, policy, settings.stake_eur, settings.random_seed)
    if ev_["joint_probability"] < settings.acca_min_joint_prob:
        return {"status": "not_possible", "eligible_count": len(pool),
                "reason": f"probabilità congiunta stimata {ev_['joint_probability']:.1%} < {settings.acca_min_joint_prob:.0%}: multipla troppo improbabile"}
    for cand in pool[settings.acca_min_legs:]:
        if len(legs) >= settings.acca_max_legs or cand["score"]["final"] < settings.acca_extra_leg_min_score:
            break
        trial = legs + [cand]
        tv = evaluate(trial, policy, settings.stake_eur, settings.random_seed)
        if tv["joint_probability"] < settings.acca_min_joint_prob:
            break
        legs, ev_ = trial, tv
    ev_["status"] = "ok"
    ev_["legs"] = legs
    ev_["selection_rule"] = (f"migliore selezione per partita, ordinate per punteggio; prime {settings.acca_min_legs} obbligatorie, "
                             f"ulteriori solo con punteggio ≥ {settings.acca_extra_leg_min_score} e probabilità congiunta ≥ {settings.acca_min_joint_prob:.0%}")
    return ev_


def build_prudent(eligible: list[dict], settings, policy: dict) -> dict:
    pool = [s for s in eligible if s["p_est"] >= settings.prudent_min_leg_prob]
    pool = sorted(best_per_match(pool, key=lambda s: (s["p_est"], s["score"]["final"])), key=lambda s: (-s["p_est"], -s["score"]["final"]))
    n = settings.prudent_legs
    if len(pool) < n:
        return {"status": "not_possible", "eligible_count": len(pool),
                "reason": f"solo {len(pool)} selezioni conformi con probabilità stimata ≥ {settings.prudent_min_leg_prob:.0%} (ne servono {n})"}
    legs = pool[:n]
    out = evaluate(legs, policy, settings.stake_eur, settings.random_seed)
    out.update({"status": "ok", "legs": legs,
                "selection_rule": f"le {n} selezioni conformi con la probabilità stimata più alta (≥ {settings.prudent_min_leg_prob:.0%}), una per partita"})
    return out
