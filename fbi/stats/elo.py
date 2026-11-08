"""Elo calcolato dai risultati disponibili (formula World Football Elo: K con
moltiplicatore per scarto reti, vantaggio campo in punti Elo).

Le valutazioni partono uguali per tutti: con poche partite l'Elo è poco informativo,
per questo il Math Agent ne riporta il numero di partite e ne riduce il peso.
"""
from __future__ import annotations

import math


class Elo:
    def __init__(self, k: float = 24.0, home_adv: float = 60.0, base: float = 1500.0):
        self.k, self.hfa, self.base = k, home_adv, base
        self.r: dict[str, float] = {}
        self.n: dict[str, int] = {}

    def rating(self, t: str) -> float:
        return self.r.get(t, self.base)

    def expected(self, home: str, away: str, neutral: bool = False) -> float:
        dr = self.rating(home) + (0 if neutral else self.hfa) - self.rating(away)
        return 1.0 / (1.0 + 10 ** (-dr / 400.0))

    def fit(self, matches: list[dict]) -> "Elo":
        seen = set()
        for m in sorted(matches, key=lambda x: x["date"]):
            if m["event_id"] in seen:
                continue
            seen.add(m["event_id"])
            h, a = m["home_id"], m["away_id"]
            e = self.expected(h, a, m.get("neutral", False))
            gd = m["home_goals"] - m["away_goals"]
            s = 1.0 if gd > 0 else 0.5 if gd == 0 else 0.0
            mult = 1.0 if abs(gd) <= 1 else (1.5 if abs(gd) == 2 else (11 + abs(gd)) / 8)
            delta = self.k * mult * (s - e)
            self.r[h] = self.rating(h) + delta
            self.r[a] = self.rating(a) - delta
            self.n[h] = self.n.get(h, 0) + 1
            self.n[a] = self.n.get(a, 0) + 1
        return self

    def probs_1x2(self, home: str, away: str, draw_rate: float = 0.26, neutral: bool = False) -> dict[str, float]:
        """E = p(1) + p(X)/2; p(X) decresce con lo squilibrio tra le squadre."""
        e = self.expected(home, away, neutral)
        pd = draw_rate * math.exp(-((e - 0.5) ** 2) / 0.08)
        pd = min(pd, 2 * min(e, 1 - e) * 0.95)
        ph = max(0.0, e - pd / 2)
        pa = max(0.0, 1 - e - pd / 2)
        s = ph + pd + pa
        return {"1X2|HOME": ph / s, "1X2|DRAW": pd / s, "1X2|AWAY": pa / s}
