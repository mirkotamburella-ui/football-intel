"""Matematica delle quote: probabilità implicite, margine del bookmaker, rimozione del
margine (metodo "power", che corregge parzialmente il favourite–longshot bias) e EV."""
from __future__ import annotations

from statistics import mean


def implied(decimal: float) -> float:
    return 1.0 / decimal


def overround(prices: list[float]) -> float:
    """Somma delle probabilità implicite − 1 (es. 0.05 = margine del 5%)."""
    return sum(1.0 / p for p in prices) - 1.0


def devig_proportional(prices: list[float]) -> list[float]:
    imp = [1.0 / p for p in prices]
    s = sum(imp)
    return [x / s for x in imp]


def devig_power(prices: list[float], tol: float = 1e-10) -> list[float]:
    """Trova k tale che Σ (1/d_i)^k = 1. Per k>1 riduce di più le quote lunghe."""
    imp = [1.0 / p for p in prices]
    if abs(sum(imp) - 1) < 1e-9:
        return imp
    lo, hi = 0.2, 5.0
    for _ in range(200):
        k = (lo + hi) / 2
        s = sum(x**k for x in imp)
        if abs(s - 1) < tol:
            break
        if s > 1:
            lo = k
        else:
            hi = k
    out = [x**k for x in imp]
    t = sum(out)
    return [x / t for x in out]


def ev(prob: float, decimal: float) -> float:
    """Expected Value = (Probabilità stimata × Quota) − 1."""
    return prob * decimal - 1.0


def ev_dnb(p_win: float, p_draw: float, decimal: float) -> float:
    """Draw No Bet: in caso di pareggio la puntata è rimborsata."""
    return p_win * decimal + p_draw - 1.0


def fair_from_books(books: dict[str, list[float]]) -> tuple[list[float], dict]:
    """Probabilità eque di consenso: media delle probabilità de-vig per bookmaker."""
    per_book = {b: devig_power(p) for b, p in books.items() if p and all(x > 1 for x in p)}
    if not per_book:
        return [], {}
    n = len(next(iter(per_book.values())))
    cons = [mean(v[i] for v in per_book.values()) for i in range(n)]
    s = sum(cons)
    return [c / s for c in cons], {b: round(overround(books[b]), 4) for b in per_book}
