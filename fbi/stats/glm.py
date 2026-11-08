"""Regressione di Poisson log-lineare (modello di Maher / Dixon–Coles senza tau):

    log λ = μ + h·casa + attacco_squadra − difesa_avversario

stimata con Newton–Raphson, penalità ridge (prior gaussiano: le forze tendono a 0
quando i dati sono pochi) e pesi temporali con emivita configurabile.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np


class PoissonGLM:
    def __init__(self, ridge: float = 3.0, half_life_days: float = 180.0, max_iter: int = 50):
        self.ridge, self.half_life, self.max_iter = ridge, half_life_days, max_iter
        self.teams: dict[str, int] = {}
        self.beta: np.ndarray | None = None
        self.n_obs = 0
        self.games_per_team: dict[str, int] = {}

    def fit(self, matches: list[dict], ref_date: datetime | None = None) -> "PoissonGLM":
        ref = ref_date or datetime.now(timezone.utc)
        uniq = {m["event_id"]: m for m in matches}.values()
        ids = sorted({m["home_id"] for m in uniq} | {m["away_id"] for m in uniq})
        self.teams = {t: k for k, t in enumerate(ids)}
        T = len(ids)
        if T < 2:
            return self
        rows, y, w = [], [], []
        for m in uniq:
            age = (ref - datetime.fromisoformat(m["date"].replace("Z", "+00:00"))).days
            wt = 0.5 ** (max(age, 0) / self.half_life)
            hi, ai = self.teams[m["home_id"]], self.teams[m["away_id"]]
            home_flag = 0.0 if m.get("neutral") else 1.0
            for att, dfn, goals, hf in ((hi, ai, m["home_goals"], home_flag), (ai, hi, m["away_goals"], 0.0)):
                x = np.zeros(2 + 2 * T)
                x[0], x[1], x[2 + att], x[2 + T + dfn] = 1.0, hf, 1.0, -1.0
                rows.append(x)
                y.append(goals)
                w.append(wt)
            for t in (m["home_id"], m["away_id"]):
                self.games_per_team[t] = self.games_per_team.get(t, 0) + 1
        X, Y, W = np.array(rows), np.array(y, float), np.array(w)
        self.n_obs = len(Y)
        P = np.full(X.shape[1], self.ridge)
        P[:2] = 1e-6  # intercetta e vantaggio casa non penalizzati
        beta = np.zeros(X.shape[1])
        beta[0] = np.log(max(Y.mean(), 0.1))
        for _ in range(self.max_iter):
            mu = np.exp(np.clip(X @ beta, -5, 3))
            grad = X.T @ (W * (Y - mu)) - P * beta
            H = (X * (W * mu)[:, None]).T @ X + np.diag(P)
            step = np.linalg.solve(H, grad)
            beta += step
            if np.max(np.abs(step)) < 1e-7:
                break
        self.beta = beta
        return self

    @property
    def fitted(self) -> bool:
        return self.beta is not None

    def predict(self, home_id: str, away_id: str, neutral: bool = False) -> tuple[float, float] | None:
        if self.beta is None or home_id not in self.teams or away_id not in self.teams:
            return None
        T = len(self.teams)
        b = self.beta
        hi, ai = self.teams[home_id], self.teams[away_id]
        lh = np.exp(b[0] + (0 if neutral else b[1]) + b[2 + hi] - b[2 + T + ai])
        la = np.exp(b[0] + b[2 + ai] - b[2 + T + hi])
        return float(lh), float(la)

    def home_advantage(self) -> float | None:
        return float(np.exp(self.beta[1])) if self.beta is not None else None
