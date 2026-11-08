"""AGENT 7 – ODDS INTELLIGENCE: quote reali, confronto bookmaker, movimenti, probabilità implicite e de-vig."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from ..context import src
from ..http_client import HttpError
from ..leagues import tier
from ..providers import espn as E
from ..providers.odds_api import SPORT_KEYS, find_event
from ..stats.odds_math import fair_from_books
from .a01_fixture_scout import fixtures_from
from .base import Agent

GROUPS = {"1X2": ["HOME", "DRAW", "AWAY"], "BTTS": ["YES", "NO"], "DNB": ["HOME", "AWAY"]}


def mkey(q: dict) -> str:
    return f"{q['market']}|{q['selection']}" + (f"|{float(q['line'])}" if q.get("line") is not None else "")


def analyze_quotes(quotes: list[dict], preferred: list[str]) -> dict:
    by_key: dict[str, dict[str, dict]] = defaultdict(dict)
    for q in quotes:
        k = mkey(q)
        prev = by_key[k].get(q["bookmaker"])
        if not prev or q["fetched_at"] >= prev["fetched_at"]:
            by_key[k][q["bookmaker"]] = q
    markets = {}
    for k, books in by_key.items():
        best = max(books.values(), key=lambda q: q["decimal"])
        pref = [books[b] for b in preferred if b in books]
        playable = max(pref, key=lambda q: q["decimal"]) if pref else None  # migliore quota tra i bookmaker preferiti
        is_pref = playable is not None
        playable = playable or best
        opens = {b: q["open_decimal"] for b, q in books.items() if q.get("open_decimal")}
        mov = None
        if playable.get("open_decimal"):
            mov = round((playable["decimal"] / playable["open_decimal"] - 1) * 100, 1)
        imps = [1 / q["decimal"] for q in books.values()]
        markets[k] = {
            "books": {b: q["decimal"] for b, q in books.items()},
            "best": {"decimal": best["decimal"], "bookmaker": best["bookmaker"]},
            "playable": {"decimal": playable["decimal"], "bookmaker": playable["bookmaker"], "preferred_bookmaker": is_pref,
                         "source": playable["source"], "fetched_at": playable["fetched_at"]},
            "open": opens or None,
            "movement_pct": mov,
            "implied_playable": round(1 / playable["decimal"], 4),
            "books_disagreement_pp": round((max(imps) - min(imps)) * 100, 1) if len(imps) > 1 else None,
        }
    # probabilità eque (de-vig, metodo power) per gruppo di mercato
    fair, ovr = {}, {}
    for g, sels in GROUPS.items():
        books = defaultdict(list)
        for b in {b for s in sels for b in by_key.get(f"{g}|{s}", {})}:
            prices = [by_key.get(f"{g}|{s}", {}).get(b, {}).get("decimal") for s in sels]
            if all(prices):
                books[b] = prices
        if books:
            cons, o = fair_from_books(dict(books))
            for s, p in zip(sels, cons):
                fair[f"{g}|{s}"] = round(p, 4)
            ovr[g] = o
    lines = {float(k.split("|")[2]) for k in by_key if k.startswith("OU|")}
    for line in lines:
        ko, ku = f"OU|OVER|{line}", f"OU|UNDER|{line}"
        books = {b: [by_key[ko][b]["decimal"], by_key[ku][b]["decimal"]] for b in by_key.get(ko, {}) if b in by_key.get(ku, {})}
        if books:
            cons, o = fair_from_books(books)
            fair[ko], fair[ku] = round(cons[0], 4), round(cons[1], 4)
            ovr[f"OU {line}"] = o
    # Doppia chance: probabilità eque derivate dall'1X2 de-vig (il mercato DC somma ~200%)
    # Draw No Bet: in tutto il sistema "DNB|X" è la probabilità ASSOLUTA di vittoria (il pareggio
    # è un rimborso gestito nell'EV). Il mercato DNB a due vie dà invece la probabilità condizionata
    # al non-pareggio: la si riporta in assoluto con p(X) dell'1X2; senza 1X2 non è confrontabile.
    if all(f"1X2|{s}" in fair for s in ("HOME", "DRAW", "AWAY")):
        h, d, a = fair["1X2|HOME"], fair["1X2|DRAW"], fair["1X2|AWAY"]
        fair.update({"DC|1X": round(h + d, 4), "DC|X2": round(a + d, 4), "DC|12": round(h + a, 4)})
        if "DNB|HOME" in fair:
            fair["DNB|HOME"], fair["DNB|AWAY"] = round(fair["DNB|HOME"] * (1 - d), 4), round(fair["DNB|AWAY"] * (1 - d), 4)
        else:
            fair["DNB|HOME"], fair["DNB|AWAY"] = h, a
    else:
        fair.pop("DNB|HOME", None)
        fair.pop("DNB|AWAY", None)
    return {"markets": markets, "fair": fair, "overround": ovr,
            "bookmakers": sorted({q["bookmaker"] for q in quotes}),
            "significant_moves": [{"market": k, "movement_pct": m["movement_pct"]} for k, m in markets.items()
                                  if m["movement_pct"] is not None and abs(m["movement_pct"]) >= 5]}


class OddsIntelligence(Agent):
    id = "A07"
    name = "Odds Intelligence"
    depends_on = ("A01",)
    description = "Quote reali per 1X2, doppia chance, Over 1.5/2.5, Under 3.5, Goal/No Goal, Draw No Bet; confronto bookmaker, movimenti apertura→attuale, probabilità implicite e corrette per il margine."

    def analyze(self, ctx, inputs):
        fixtures = fixtures_from(inputs)
        s = ctx.settings
        oa = ctx.odds_api
        day_start = ctx.now_utc
        day_end = ctx.now_utc + timedelta(hours=30)

        def league_events(slug):
            return ctx.memo(f"oddsapi:{slug}", lambda: oa.league_odds(slug, day_start, day_end)) or []

        # priorità per i mercati extra a pagamento: prima i campionati più affidabili
        order = sorted(fixtures, key=lambda f: (tier(f.get("league_slug")), f["kickoff_utc"]))
        extra_allowed = {f["event_id"] for f in order[: max(0, s.odds_api_max_credits // 6)]} if oa.enabled else set()

        def one(fx):
            quotes = []
            slug = fx.get("league_slug")
            try:
                f = ctx.espn.core_odds(slug, fx["event_id"])
                self.add_source(src(f))
                quotes += E.parse_core_odds(f)
            except HttpError as e:
                self.log.info("quote ESPN non disponibili per %s: %s", fx["event_id"], e)
            matched = None
            if oa.enabled and slug in SPORT_KEYS:
                matched = find_event(league_events(slug), fx["home"]["name"], fx["away"]["name"], fx["kickoff_utc"])
                if matched:
                    quotes += matched["quotes"]
                    if fx["event_id"] in extra_allowed:
                        quotes += oa.event_extra_markets(matched["sport"], matched["odds_event_id"])
            for q in quotes:
                self.add_source({"source": q["source"], "fetched_at": q["fetched_at"]})
            if not quotes:
                return {"available": False, "note": "nessuna quota reale disponibile: partita non valutabile"}
            res = analyze_quotes(quotes, s.preferred_bookmakers)
            res["available"] = True
            res["odds_api_matched"] = bool(matched)
            res["markets_not_available"] = [m for m in ("OU|OVER|1.5", "OU|UNDER|3.5", "BTTS|YES", "DNB|HOME")
                                            if m not in res["markets"]]
            return res

        matches = self.per_match(ctx, fixtures, one)
        if not oa.enabled:
            self.warn("ODDS_API_KEY non configurata: quote solo da ESPN (DraftKings/Bet365); Over 1.5, Under 3.5, Goal/No Goal e DNB spesso non quotati")
        return {
            "matches_analyzed": len(matches),
            "with_odds": sum(1 for m in matches.values() if m.get("available")),
            "odds_api_enabled": oa.enabled,
            "odds_api_credits_used": oa.credits_used,
            "odds_api_remaining": oa.remaining,
            "devig_method": "power (Σ p_i^k = 1), consenso = media tra bookmaker",
        }, matches
