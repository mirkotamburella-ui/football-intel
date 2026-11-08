"""Modello di Poisson bivariato indipendente con correzione Dixon–Coles sui punteggi bassi.

Dato (λ_casa, λ_trasferta) produce la matrice dei risultati esatti e da essa le
probabilità di tutti i mercati supportati.
"""
from __future__ import annotations

import math

import numpy as np

MAX_GOALS = 10
DC_RHO = -0.06  # correzione Dixon–Coles (valore tipico in letteratura, ipotesi di modello)


def _pmf(lam: float, n: int) -> np.ndarray:
    k = np.arange(n + 1)
    logp = -lam + k * math.log(max(lam, 1e-9)) - np.array([math.lgamma(i + 1) for i in k])
    return np.exp(logp)


def score_matrix(lh: float, la: float, rho: float = DC_RHO, max_goals: int = MAX_GOALS) -> np.ndarray:
    m = np.outer(_pmf(lh, max_goals), _pmf(la, max_goals))
    # tau di Dixon–Coles sui risultati 0-0, 1-0, 0-1, 1-1
    m[0, 0] *= max(0.0, 1 - lh * la * rho)
    m[0, 1] *= max(0.0, 1 + lh * rho)
    m[1, 0] *= max(0.0, 1 + la * rho)
    m[1, 1] *= max(0.0, 1 - rho)
    return m / m.sum()


OU_LINES = (0.5, 1.5, 2.5, 3.5, 4.5)


def market_probs(m: np.ndarray) -> dict[str, float]:
    """Chiavi: '1X2|HOME', 'DC|1X', 'OU|OVER|2.5', 'BTTS|YES', 'DNB|HOME' (prob. assoluta di vittoria)."""
    i, j = np.indices(m.shape)
    ph, pd, pa = float(m[i > j].sum()), float(np.trace(m)), float(m[i < j].sum())
    tot = i + j
    out = {
        "1X2|HOME": ph, "1X2|DRAW": pd, "1X2|AWAY": pa,
        "DC|1X": ph + pd, "DC|X2": pa + pd, "DC|12": ph + pa,
        "DNB|HOME": ph, "DNB|AWAY": pa,  # per il DNB l'EV usa anche p(pareggio)=rimborso
        "BTTS|YES": float(m[1:, 1:].sum()), "BTTS|NO": 1 - float(m[1:, 1:].sum()),
        "EXP_GOALS": float((tot * m).sum()),
    }
    for line in OU_LINES:
        over = float(m[tot > line].sum())
        out[f"OU|OVER|{line}"] = over
        out[f"OU|UNDER|{line}"] = 1 - over
    return out


def most_likely_scores(m: np.ndarray, k: int = 3) -> list[tuple[str, float]]:
    flat = np.argsort(m, axis=None)[::-1][:k]
    return [(f"{a}-{b}", round(float(m[a, b]), 3)) for a, b in (np.unravel_index(x, m.shape) for x in flat)]
