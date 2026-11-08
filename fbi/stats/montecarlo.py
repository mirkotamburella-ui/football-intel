"""Simulazioni Monte Carlo.

1. `match_intervals`: propaga l'incertezza sui parametri (λ) campionandoli da una
   Gamma con coefficiente di variazione legato alla dimensione del campione →
   intervallo 5°–95° per ogni probabilità di mercato (contro la falsa precisione).
2. `simulate_scorelines`: simulazione diretta dei risultati (verifica del modello analitico).
3. `accumulator_joint`: probabilità congiunta della multipla con copula gaussiana
   (correlazioni tra gambe) e incertezza sulle probabilità di ogni gamba (Beta).
"""
from __future__ import annotations

import math

import numpy as np

from .poisson import market_probs, score_matrix

_erf = np.frompyfunc(math.erf, 1, 1)


def _phi(z: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + _erf(z / math.sqrt(2.0)).astype(float))


def match_intervals(lh: float, la: float, cv: float, draws: int = 300, seed: int = 0) -> dict[str, tuple[float, float]]:
    rng = np.random.default_rng(seed)
    shape = 1.0 / max(cv, 1e-3) ** 2
    lhs = rng.gamma(shape, lh / shape, draws)
    las = rng.gamma(shape, la / shape, draws)
    acc: dict[str, list[float]] = {}
    for a, b in zip(lhs, las):
        for k, v in market_probs(score_matrix(float(a), float(b))).items():
            acc.setdefault(k, []).append(v)
    return {k: (float(np.percentile(v, 5)), float(np.percentile(v, 95))) for k, v in acc.items()}


def simulate_scorelines(lh: float, la: float, n: int = 20000, seed: int = 0) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    h, a = rng.poisson(lh, n), rng.poisson(la, n)
    t = h + a
    return {
        "1X2|HOME": float((h > a).mean()), "1X2|DRAW": float((h == a).mean()), "1X2|AWAY": float((h < a).mean()),
        "OU|OVER|1.5": float((t > 1.5).mean()), "OU|OVER|2.5": float((t > 2.5).mean()), "OU|UNDER|3.5": float((t < 3.5).mean()),
        "BTTS|YES": float(((h > 0) & (a > 0)).mean()),
    }


def accumulator_joint(
    probs: list[float],
    prob_sd: list[float],
    corr: np.ndarray | None = None,
    outer: int = 200,
    inner: int = 2000,
    seed: int = 0,
) -> dict:
    """Ritorna probabilità congiunta (media) e intervallo 5–95%, più il confronto con
    il semplice prodotto (ipotesi di indipendenza)."""
    rng = np.random.default_rng(seed)
    n = len(probs)
    p = np.clip(np.array(probs, float), 1e-4, 1 - 1e-4)
    sd = np.clip(np.array(prob_sd, float), 1e-4, 0.25)
    # parametri Beta con media p e deviazione sd
    k = np.maximum(p * (1 - p) / sd**2 - 1, 2.0)
    alpha, beta = p * k, (1 - p) * k
    C = np.eye(n) if corr is None else np.array(corr, float)
    L = np.linalg.cholesky(C + 1e-9 * np.eye(n))
    joint = np.empty(outer)
    for o in range(outer):
        pp = rng.beta(alpha, beta)
        z = rng.standard_normal((inner, n)) @ L.T
        u = _phi(z)
        joint[o] = float(np.all(u < pp, axis=1).mean())
    indep = float(np.prod(p))
    return {
        "joint_prob": float(joint.mean()),
        "joint_prob_p5": float(np.percentile(joint, 5)),
        "joint_prob_p95": float(np.percentile(joint, 95)),
        "independent_product": indep,
        "simulations": outer * inner,
    }
