"""Dati per le partite tra NAZIONALI (Nations League, qualificazioni, amichevoli).

ESPN dà per le nazionali solo le partite della singola competizione: troppo poche.
Qui si usano due fonti pubbliche e indipendenti:
  - risultati internazionali dal 1872 (dataset "international_results" su GitHub, CSV)
  - World Football Elo di eloratings.net (TSV), rating calibrato su decenni di partite
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, timedelta

from ..http_client import HttpError
from ..names import similarity

log = logging.getLogger("fbi.intl")

RESULTS_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
ELO_URL = "https://www.eloratings.net/World.tsv"
ELO_TEAMS_URL = "https://www.eloratings.net/en.teams.tsv"
YEARS = 3  # finestra di risultati usata per forma e modelli
ELO_HOME_ADV = 100  # vantaggio campo nella formula World Football Elo

INTL_SLUG_PREFIXES = ("uefa.nations", "fifa.", "concacaf.nations", "uefa.euroq", "conmebol.worldq", "afc.worldq", "caf.worldq",
                      "concacaf.worldq", "uefa.worldq", "fifa.worldq")
INTL_WORDS = ("Nations League", "International Friendly", "World Cup Qualifying", "Euro Qualifying", "WC Qualifying")

# nomi ESPN → nomi del dataset / di eloratings
ALIASES = {"türkiye": "Turkey", "turkiye": "Turkey", "czechia": "Czech Republic", "bosnia-herzegovina": "Bosnia and Herzegovina",
           "korea republic": "South Korea", "côte d'ivoire": "Ivory Coast", "cote d'ivoire": "Ivory Coast", "usa": "United States",
           "cape verde islands": "Cape Verde", "chinese taipei": "Taiwan", "curacao": "Curaçao", "ir iran": "Iran",
           "republic of ireland": "Republic of Ireland", "fyr macedonia": "North Macedonia", "eswatini": "Eswatini"}
ELO_ALIASES = {"Czech Republic": "Czechia", "Republic of Ireland": "Ireland", "United States": "United States"}


def is_international(fx: dict) -> bool:
    slug = (fx.get("league_slug") or "").lower()
    comp = fx.get("competition") or ""
    return slug.startswith(INTL_SLUG_PREFIXES) or any(w in comp for w in INTL_WORDS)


class InternationalData:
    def __init__(self, http, today: date):
        self.http, self.today = http, today
        self.results: list[dict] | None = None
        self.results_src = None
        self.elo: dict[str, float] = {}
        self.elo_src = None
        self.names: set[str] = set()

    def load(self) -> "InternationalData":
        since = (self.today - timedelta(days=365 * YEARS)).isoformat()
        try:
            f = self.http.get_text(RESULTS_URL, ttl=12 * 3600)
            self.results_src = {"source": f.source, "fetched_at": f.fetched_at}
            rows = []
            for i, r in enumerate(csv.DictReader(io.StringIO(f.data))):
                if r["date"] < since or not r["home_score"].isdigit() or not r["away_score"].isdigit():
                    continue
                rows.append({"event_id": f"intl:{r['date']}:{r['home_team']}:{r['away_team']}", "date": r["date"] + "T18:00Z",
                             "home_name": r["home_team"], "away_name": r["away_team"],
                             "home_goals": int(r["home_score"]), "away_goals": int(r["away_score"]),
                             "neutral": r["neutral"].upper() == "TRUE", "tournament": r["tournament"]})
                self.names.update((r["home_team"], r["away_team"]))
            self.results = rows
        except HttpError as e:
            log.warning("risultati internazionali non disponibili: %s", e)
            self.results = []
        try:
            teams = {}
            ft = self.http.get_text(ELO_TEAMS_URL, ttl=7 * 86400)
            for line in ft.data.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    teams[parts[0]] = parts[1]
            fe = self.http.get_text(ELO_URL, ttl=6 * 3600)
            self.elo_src = {"source": fe.source, "fetched_at": fe.fetched_at}
            for line in fe.data.splitlines():
                parts = line.split("\t")
                if len(parts) > 3 and parts[2] in teams:
                    self.elo[teams[parts[2]]] = float(parts[3])
        except (HttpError, ValueError) as e:
            log.warning("Elo nazionali non disponibile: %s", e)
        return self

    def resolve(self, name: str, pool) -> str | None:
        """Nome ESPN → nome nella fonte (corrispondenza esatta, alias o somiglianza ≥ 0.9)."""
        if not name:
            return None
        n = ALIASES.get(name.lower(), name)
        if n in pool:
            return n
        best, score = None, 0.0
        for cand in pool:
            s = similarity(n, cand)
            if s > score:
                best, score = cand, s
        return best if score >= 0.9 else None

    def team_results(self, espn_name: str) -> tuple[str | None, list[dict]]:
        nm = self.resolve(espn_name, self.names)
        if not nm:
            return None, []
        return nm, [r for r in self.results if nm in (r["home_name"], r["away_name"])]

    def elo_rating(self, espn_name: str) -> float | None:
        nm = self.resolve(espn_name, self.elo.keys())
        if not nm:
            nm2 = self.resolve(espn_name, self.names)
            nm = ELO_ALIASES.get(nm2, nm2) if nm2 else None
        return self.elo.get(nm) if nm else None
