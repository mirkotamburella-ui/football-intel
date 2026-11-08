"""AGENT 12 – FINAL BETTING STRATEGIST: confronta i report, applica scoring e controllo
qualità, classifica le opportunità e costruisce multipla principale, alternativa e singole."""
from __future__ import annotations

from ..accumulator import best_per_match, build_main, build_prudent, diversify
from ..quality import qc_selection, round_prob
from ..scoring import score_selection
from .base import Agent


def rationale(sel: dict, a: dict) -> list[str]:
    eid, key = sel["event_id"], sel["key"]
    form, offdef, mth, mot = (a[k]["matches"].get(eid, {}) for k in ("A02", "A03", "A08", "A06"))
    out = []
    for sd in ("home", "away"):
        f = form.get(sd, {})
        if f.get("last5_league"):
            r = f["last5_league"]
            out.append(f"{f['team']}: ultime 5 di campionato {r['sequence']} ({r['ppg']} pt/partita, gol {r['gf']}-{r['ga']})")
    if mth.get("available"):
        out.append(f"Modelli ({', '.join(mth['models_used'])}): gol attesi {mth['lambda_home']:.2f}–{mth['lambda_away']:.2f}, "
                   f"p(modello)={sel['p_model']:.0%} vs p(mercato equa)={sel['p_market_fair']:.0%}")
    if key.startswith(("OU", "BTTS")):
        for sd in ("home", "away"):
            o = offdef.get(sd, {})
            if o.get("games"):
                out.append(f"{o['team']}: {o['gf_pg']} gol fatti e {o['ga_pg']} subiti a partita, Over 2.5 nel {o['over25_pct']:.0%}, BTTS nel {o['btts_pct']:.0%}")
    for sd in ("home", "away"):
        m = mot.get(sd, {})
        if m.get("rank"):
            out.append(f"{m['team']}: {m['rank']}° con {m['points']} punti" + (f" – {', '.join(m['objectives'])}" if m.get("objectives") else ""))
    out.append(f"Probabilità stimata {sel['p_est']:.0%} (intervallo 90%: {sel['p_est_range_90'][0]:.0%}–{sel['p_est_range_90'][1]:.0%}), "
               f"EV {sel['ev']:+.1%} alla quota {sel['odds']}")
    return out


class FinalBettingStrategist(Agent):
    id = "A12"
    name = "Final Betting Strategist"
    depends_on = ("A01", "A02", "A03", "A04", "A05", "A06", "A07", "A08", "A09", "A10", "A11")
    description = "Confronta le analisi, assegna il punteggio 0–100, applica il controllo qualità, classifica e costruisce la multipla (5–10 eventi), l'alternativa prudente e le 5 migliori singole."

    def analyze(self, ctx, inputs):
        s = ctx.settings
        fixtures = {fx["event_id"]: fx for fx in inputs["A01"]["summary"]["fixtures"]}
        policy = inputs["A10"]["summary"]["correlation_policy"]
        all_sel, cards = [], {}
        for eid, fx in fixtures.items():
            risk = inputs["A10"]["matches"].get(eid, {})
            rsel = {r["key"]: r for r in risk.get("selections", [])}
            model = inputs["A08"]["matches"].get(eid, {})
            contra = inputs["A11"]["matches"].get(eid, {}).get("selections", {})
            sels = []
            for c in inputs["A09"]["matches"].get(eid, {}).get("candidates", []):
                sc = score_selection(c, eid, inputs)
                fails = qc_selection(ctx, c, sc, risk, rsel.get(c["key"], {}), fx, model)
                sel = {
                    "event_id": eid, "match": f"{fx['home']['name']} - {fx['away']['name']}",
                    "home": fx["home"]["name"], "away": fx["away"]["name"], "competition": fx["competition"],
                    "league_slug": fx.get("league_slug"), "kickoff_local": fx["kickoff_local"], "kickoff_utc": fx["kickoff_utc"],
                    **{k: c[k] for k in ("key", "label", "market", "odds", "bookmaker", "preferred_bookmaker", "odds_source", "odds_fetched_at",
                                         "best_odds", "best_bookmaker", "p_model", "p_market_fair", "p_est", "p_est_range_90", "ev", "edge_pp", "p_push")},
                    "p_display": round_prob(c["p_est"]),
                    "score": sc,
                    "risk_level": rsel.get(c["key"], {}).get("risk_level"),
                    "reliability_level": risk.get("reliability_level"), "data_quality": risk.get("data_quality"),
                    "risks": [o["objection"] for o in contra.get(c["key"], {}).get("objections", [])],
                    "qc_passed": not fails, "qc_failures": fails,
                    "correlation_tags": risk.get("correlation_tags"),
                }
                sel["rationale"] = rationale(sel, inputs)
                sels.append(sel)
                all_sel.append(sel)
            sels.sort(key=lambda x: (-x["qc_passed"], -x["score"]["final"]))
            cards[eid] = {
                "event_id": eid, "match": f"{fx['home']['name']} - {fx['away']['name']}", "competition": fx["competition"],
                "region": fx.get("region"), "kickoff_local": fx["kickoff_local"],
                "recommended": sels[0] if sels and sels[0]["qc_passed"] else None,
                "best_candidate": sels[0] if sels else None,
                "candidates_count": len(sels), "qc_passed_count": sum(x["qc_passed"] for x in sels),
                "reliability_level": risk.get("reliability_level"), "data_quality": risk.get("data_quality"),
                "data_notes": risk.get("data_notes", []), "match_exclusions": risk.get("match_exclusions", []),
                "selections": sels[:12],
            }
        eligible = [x for x in all_sel if x["qc_passed"]]
        main = build_main(eligible, s, policy)
        prudent = build_prudent(eligible, s, policy)
        if main.get("status") == "ok" and prudent.get("status") == "ok":
            mk = {(l["event_id"], l["key"]) for l in main["legs"]}
            pk = {(l["event_id"], l["key"]) for l in prudent["legs"]}
            prudent["comparison_with_main"] = {
                "shared_legs": len(mk & pk),
                "joint_probability_ratio": round(prudent["joint_probability"] / max(main["joint_probability"], 1e-9), 2),
                "odds_ratio": round(prudent["total_odds"] / main["total_odds"], 2),
                "summary": f"La prudente ha probabilità stimata {prudent['joint_probability']:.1%} contro {main['joint_probability']:.1%} della principale, "
                           f"con quota {prudent['total_odds']} contro {main['total_odds']}.",
            }
        singles = sorted({(x["event_id"], x["key"]): x for x in eligible}.values(), key=lambda x: (-x["score"]["final"], -x["ev"]))
        top = diversify(best_per_match(singles), s.max_per_league)
        top = sorted(top, key=lambda x: (-x["score"]["final"], -x["ev"]))[:5]
        summary = {
            "selections_evaluated": len(all_sel),
            "selections_qc_passed": len(eligible),
            "main_accumulator": main,
            "prudent_accumulator": prudent,
            "top_singles": top,
            "top_singles_note": None if len(top) == 5 else f"solo {len(top)} selezioni superano il controllo qualità",
            "qc_failure_stats": _failure_stats(all_sel),
        }
        return summary, cards


def _failure_stats(sels: list[dict]) -> list[tuple[str, int]]:
    from collections import Counter
    import re
    c = Counter()
    for x in sels:
        for f in x["qc_failures"]:
            c[re.sub(r"[-+]?\d+(\.\d+)?%?", "#", f)] += 1
    return c.most_common(12)
