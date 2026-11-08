"""AGENT 9 – VALUE BETTING ANALYST.

Probabilità stimata = w · p_modello + (1 − w) · p_mercato_equa
  con w = MODEL_WEIGHT_MAX × qualità_campione × accordo_tra_modelli  (w ≤ 0.40)
Expected Value = (Probabilità stimata × Quota) − 1   (DNB: + p(pareggio) per il rimborso)

Il mercato de-vig è uno stimatore molto forte: il modello può solo correggerlo in
proporzione alla qualità dei dati. Una quota alta senza vantaggio stimato NON è valore.
"""
from __future__ import annotations

from ..stats.odds_math import ev, ev_dnb
from .base import Agent

LABELS = {
    "1X2|HOME": "1 (vittoria casa)", "1X2|DRAW": "X (pareggio)", "1X2|AWAY": "2 (vittoria trasferta)",
    "DC|1X": "Doppia chance 1X", "DC|X2": "Doppia chance X2", "DC|12": "Doppia chance 12",
    "BTTS|YES": "Goal (entrambe segnano)", "BTTS|NO": "No Goal", "DNB|HOME": "Draw No Bet 1", "DNB|AWAY": "Draw No Bet 2",
}


def label(key: str) -> str:
    if key.startswith("OU|"):
        _, sel, line = key.split("|")
        return f"{'Over' if sel == 'OVER' else 'Under'} {line}"
    return LABELS.get(key, key)


class ValueBettingAnalyst(Agent):
    id = "A09"
    name = "Value Betting Analyst"
    depends_on = ("A01", "A07", "A08")
    description = "Confronta probabilità del modello e del mercato, calcola la probabilità stimata combinata e l'Expected Value per ogni quota reale."

    def analyze(self, ctx, inputs):
        odds, mathr = inputs["A07"]["matches"], inputs["A08"]["matches"]
        wmax = ctx.settings.model_weight_max
        matches, n_pos = {}, 0
        for fx in inputs["A01"]["summary"]["fixtures"]:
            eid = fx["event_id"]
            o, m = odds.get(eid) or {}, mathr.get(eid) or {}
            if not o.get("available"):
                matches[eid] = {"candidates": [], "note": "nessuna quota reale"}
                continue
            if not m.get("available"):
                matches[eid] = {"candidates": [], "note": "nessun modello statistico: selezioni non valutabili (no scelte basate sull'intuito)"}
                continue
            n_min = min(m["sample"]["home_games"], m["sample"]["away_games"])
            q_sample = min(1.0, n_min / 15)
            q_agree = max(0.0, 1 - m["model_disagreement_sd"] / 0.10)
            w = round(wmax * q_sample * q_agree, 3)
            fair, ens, iv = o["fair"], m["ensemble"], m.get("intervals_90", {})
            p_draw_est = None
            if "1X2|DRAW" in fair:
                p_draw_est = w * ens["1X2|DRAW"] + (1 - w) * fair["1X2|DRAW"]
            cands = []
            for key, mk in o["markets"].items():
                if key not in ens:
                    continue
                if key not in fair:
                    continue  # senza prezzo equo (manca il lato opposto) l'EV non è stimabile in modo onesto
                pm, pf = ens[key], fair[key]
                pe = w * pm + (1 - w) * pf
                lo, hi = iv.get(key, [pm, pm])
                d = mk["playable"]["decimal"]
                if key.startswith("DNB|") and p_draw_est is not None:
                    e = ev_dnb(pe, p_draw_est, d)
                else:
                    e = ev(pe, d)
                e_best = ev(pe, mk["best"]["decimal"]) if not key.startswith("DNB|") else ev_dnb(pe, p_draw_est or 0, mk["best"]["decimal"])
                cands.append({
                    "key": key, "label": label(key), "market": key.split("|")[0],
                    "odds": d, "bookmaker": mk["playable"]["bookmaker"], "preferred_bookmaker": mk["playable"]["preferred_bookmaker"],
                    "odds_source": mk["playable"]["source"], "odds_fetched_at": mk["playable"]["fetched_at"],
                    "best_odds": mk["best"]["decimal"], "best_bookmaker": mk["best"]["bookmaker"],
                    "p_model": round(pm, 4), "p_market_fair": round(pf, 4), "model_weight": w,
                    "p_est": round(pe, 4),
                    "p_est_range_90": [round(min(pe, w * lo + (1 - w) * pf), 3), round(max(pe, w * hi + (1 - w) * pf), 3)],
                    "p_push": round(p_draw_est, 4) if key.startswith("DNB|") and p_draw_est is not None else None,
                    "edge_pp": round((pm - pf) * 100, 1),
                    "ev": round(e, 4), "ev_best_price": round(e_best, 4),
                    "movement_pct": mk.get("movement_pct"), "books_disagreement_pp": mk.get("books_disagreement_pp"),
                    "market_median_odds": mk.get("market_median"), "odds_vs_median_pct": mk.get("playable_vs_median_pct"),
                    "odds_anomalous": mk.get("anomalous", False), "books_count": len(mk.get("books", {})),
                    "value": e > 0,
                })
            cands.sort(key=lambda c: -c["ev"])
            n_pos += sum(c["value"] for c in cands)
            matches[eid] = {"candidates": cands, "model_weight": w,
                            "weight_basis": {"sample_quality": round(q_sample, 2), "model_agreement": round(q_agree, 2)}}
        return {"matches_analyzed": len(matches), "positive_ev_selections": n_pos,
                "formula": "EV = p_stimata × quota − 1; p_stimata = w·p_modello + (1−w)·p_mercato_equa"}, matches
