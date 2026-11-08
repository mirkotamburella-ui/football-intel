"""Sistema di scoring 0–100 trasparente e verificabile.

| Componente                          | Max | Come si calcola                                                     |
|-------------------------------------|-----|---------------------------------------------------------------------|
| Forma recente (A02)                 | 15  | 15 × probabilità della selezione implicata dalla forma              |
| Statistiche offensive/difensive (A03)| 20 | 20 × probabilità Poisson dai tassi gol osservati (casa/trasferta)   |
| xG e modelli previsionali (A08)     | 20  | 20 × (prob. ensemble − penalità per disaccordo tra modelli)         |
| Formazioni e indisponibili (A05)    | 10  | 5 se dati assenti (neutro); 8 ± assenze; +2 formazioni ufficiali    |
| Motivazioni (A06)                   | 10  | 5 + 5 × (indice motivazione selezione − avversario), turnover       |
| Valore della quota (A09)            | 15  | 15 × clip((EV + 5%) / 15%): EV −5% → 0 punti, EV +10% → 15 punti    |
| Affidabilità dei dati (A10)         | 10  | 10 × qualità dati (0–1)                                             |

Punteggio finale = base − penalità Contrarian (A11, max 10). Un punteggio alto NON è
una garanzia di vincita: misura la coerenza tra fonti indipendenti di evidenza.
"""
from __future__ import annotations

import math

from .agents.a11_contrarian import side_of


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _poisson_over(mean_goals: float, line: float) -> float:
    k_max = int(math.floor(line))
    cdf = sum(math.exp(-mean_goals) * mean_goals**k / math.factorial(k) for k in range(k_max + 1))
    return 1 - cdf


def _form_probs(edge: float) -> dict[str, float]:
    e = 1 / (1 + 10 ** (-edge / 3))
    pd = 0.26 * math.exp(-((e - 0.5) ** 2) / 0.08)
    pd = min(pd, 2 * min(e, 1 - e) * 0.95)
    ph, pa = max(0.0, e - pd / 2), max(0.0, 1 - e - pd / 2)
    s = ph + pd + pa
    ph, pd, pa = ph / s, pd / s, pa / s
    return {"1X2|HOME": ph, "1X2|DRAW": pd, "1X2|AWAY": pa, "DC|1X": ph + pd, "DC|X2": pa + pd, "DC|12": ph + pa,
            "DNB|HOME": ph / (ph + pa), "DNB|AWAY": pa / (ph + pa)}


def form_component(key: str, form: dict) -> tuple[float | None, str]:
    if key.startswith(("1X2", "DC", "DNB")):
        edge = form.get("form_edge_home")
        if edge is None:
            return None, "forma non disponibile"
        return _form_probs(edge)[key], f"differenza di forma (ppg pesati) casa−trasferta = {edge:+.2f}"
    l10 = [form.get(sd, {}).get("last10_league") for sd in ("home", "away")]
    if not all(l10):
        return None, "forma gol recente non disponibile"
    mean_goals = sum((r["gf"] + r["ga"]) / r["n"] for r in l10) / 2
    if key.startswith("OU|"):
        _, sel, line = key.split("|")
        p_over = _poisson_over(mean_goals, float(line))
        p = p_over if sel == "OVER" else 1 - p_over
    else:  # BTTS: tasso di gol medio delle due squadre nelle ultime 10
        rates = [(r["gf"] / r["n"], r["ga"] / r["n"]) for r in l10]
        lh = (rates[0][0] + rates[1][1]) / 2
        la = (rates[1][0] + rates[0][1]) / 2
        p_yes = (1 - math.exp(-lh)) * (1 - math.exp(-la))
        p = p_yes if key == "BTTS|YES" else 1 - p_yes
    return p, f"media gol totali nelle ultime 10 delle due squadre = {mean_goals:.2f}"


def _p(probs: dict | None, key: str) -> float | None:
    """Probabilità di "successo" della selezione; per il DNB quella condizionata al non-pareggio."""
    if not probs or key not in probs:
        return None
    if key.startswith("DNB|") and probs.get("DNB|HOME") is not None and probs.get("DNB|AWAY") is not None:
        tot = probs["DNB|HOME"] + probs["DNB|AWAY"]
        return probs[key] / tot if tot else None
    return probs[key]


def score_selection(c: dict, eid: str, a: dict) -> dict:
    form, offdef, lin, mot, mth = (a[k]["matches"].get(eid, {}) for k in ("A02", "A03", "A05", "A06", "A08"))
    risk = a["A10"]["matches"].get(eid, {})
    contra = a["A11"]["matches"].get(eid, {}).get("selections", {}).get(c["key"], {})
    key, side = c["key"], side_of(c["key"])
    comps = []

    # 1. forma (15)
    p, why = form_component(key, form)
    comps.append({"name": "Forma recente", "agent": "A02", "max": 15, "points": round(15 * p, 2) if p is not None else 0.0,
                  "support": round(p, 3) if p is not None else None,
                  "explanation": why if p is not None else why + " → 0 punti"})
    # 2. statistiche offensive/difensive (20)
    obs = _p(offdef.get("observed_rates_probs"), key)
    comps.append({"name": "Statistiche offensive e difensive", "agent": "A03", "max": 20,
                  "points": round(20 * obs, 2) if obs is not None else 0.0, "support": obs,
                  "explanation": f"Poisson su tassi gol casa/trasferta (λ {offdef.get('lambda_home')}–{offdef.get('lambda_away')})" if obs is not None else "tassi osservati non disponibili → 0 punti"})
    # 3. modelli (20)
    ens = _p(mth.get("ensemble"), key)
    dis = mth.get("model_disagreement_sd", 0) or 0
    if ens is not None:
        sup = _clip(ens - max(0.0, dis - 0.03))
        comps.append({"name": "xG e modelli previsionali", "agent": "A08", "max": 20, "points": round(20 * sup, 2), "support": round(sup, 3),
                      "explanation": f"ensemble {', '.join(mth.get('models_used', []))}: p={ens:.2f}, disaccordo σ={dis:.3f}"})
    else:
        comps.append({"name": "xG e modelli previsionali", "agent": "A08", "max": 20, "points": 0.0, "support": None, "explanation": "modello non disponibile → 0 punti"})
    # 4. formazioni e indisponibili (10)
    has_inj = any(lin.get(sd, {}).get("injury_data") for sd in ("home", "away"))
    official = all(lin.get(sd, {}).get("lineup", {}).get("status") == "ufficiale" for sd in ("home", "away"))
    if not has_inj and not official:
        pts, why = 5.0, "dati non disponibili: punteggio neutro (5/10), non un'informazione positiva"
    else:
        pts = 8.0
        why_parts = []
        if side:
            opp = "away" if side == "home" else "home"
            ab_s, ab_o = lin.get(side, {}).get("absences_count", 0), lin.get(opp, {}).get("absences_count", 0)
            db_s = lin.get(side, {}).get("doubtful_count", 0)
            pts += -min(6.0, 1.5 * ab_s) + min(2.0, 0.75 * ab_o) - 0.5 * db_s
            why_parts.append(f"assenti lato selezione {ab_s}, avversario {ab_o}, in dubbio {db_s}")
        else:
            tot = sum(lin.get(sd, {}).get("absences_count", 0) for sd in ("home", "away"))
            pts -= min(4.0, 0.5 * tot)
            why_parts.append(f"assenti totali {tot}")
        if official:
            pts += 2
            why_parts.append("formazioni ufficiali pubblicate")
        pts, why = _clip(pts, 0, 10), "; ".join(why_parts)
    comps.append({"name": "Formazioni e indisponibili", "agent": "A05", "max": 10, "points": round(pts, 2), "explanation": why})
    # 5. motivazioni (10)
    if side and mot.get("home", {}).get("stake_index") is not None and mot.get("away", {}).get("stake_index") is not None:
        opp = "away" if side == "home" else "home"
        diff = mot[side]["stake_index"] - mot[opp]["stake_index"]
        pts = 5 + 5 * diff
        if mot[side].get("turnover_risk") == "alto":
            pts -= 2
        if mot[opp].get("turnover_risk") == "alto":
            pts += 1
        why = f"indice motivazione {mot[side]['stake_index']} vs {mot[opp]['stake_index']}"
    elif key in ("DC|12", "1X2|DRAW") and mot.get("home", {}).get("stake_index") is not None and mot.get("away", {}).get("stake_index") is not None:
        both = (mot["home"]["stake_index"] + mot["away"]["stake_index"]) / 2
        pts = 5 + (4 if key == "DC|12" else -4) * (both - 0.5)
        why = f"motivazione media {both:.2f} (squadre motivate → meno pareggi)"
    else:
        pts, why = 5.0, "neutro: mercato gol o motivazioni non determinabili"
    comps.append({"name": "Motivazioni", "agent": "A06", "max": 10, "points": round(_clip(pts, 0, 10), 2), "explanation": why})
    # 6. valore della quota (15)
    v = _clip((c["ev"] + 0.05) / 0.15)
    comps.append({"name": "Valore della quota", "agent": "A09", "max": 15, "points": round(15 * v, 2),
                  "explanation": f"EV {c['ev']:+.1%} alla quota {c['odds']} ({c['bookmaker']})"})
    # 7. affidabilità dati (10)
    dq = risk.get("data_quality", 0.0)
    comps.append({"name": "Affidabilità dei dati", "agent": "A10", "max": 10, "points": round(10 * dq, 2),
                  "explanation": f"qualità dati {dq:.2f} (tier {risk.get('league_tier')})"})

    base = round(sum(x["points"] for x in comps), 1)
    pen = contra.get("penalty", 0.0)
    return {"components": comps, "base": base, "contrarian_penalty": pen, "final": round(max(0.0, base - pen), 1)}
