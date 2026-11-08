"""Configurazione centralizzata.

Le chiavi API arrivano SOLO da variabili d'ambiente o da un file `.env` locale
(mai dal codice). Tutte le soglie di qualità sono qui, documentate e modificabili:
nessuna soglia viene abbassata a runtime per raggiungere il numero minimo di eventi.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"

# Tetto assoluto di budget richiesto dal progetto: non è configurabile verso l'alto.
MAX_STAKE_EUR = 10.0

# Bookmaker con concessione ADM in Italia (da verificare sul sito ADM: l'elenco cambia).
# Chiavi come normalizzate dai provider: ESPN "Bet 365" → bet365; The Odds API usa le sue chiavi.
# DraftKings (quote ESPN) opera solo negli USA: usato per il consenso di mercato, non come quota giocabile.
# Chiavi verificate sull'elenco ufficiale di The Odds API (regione "eu"): unibet_it e codere_it sono le
# versioni con licenza italiana. Sisal, Snai, Eurobet, Lottomatica non sono coperti da nessun provider:
# per loro la dashboard indica la "quota minima" sopra la quale la selezione conviene.
# Le borse scommesse (betfair_ex_*, matchbook) non sono mai "giocabili": servono solo al prezzo di consenso.
DEFAULT_BOOKMAKERS = ("unibet_it", "codere_it", "bet365", "williamhill", "sport888", "betsson", "leovegas_se")


def load_dotenv(path: Path | None = None) -> None:
    """Carica `KEY=VALUE` da .env senza sovrascrivere variabili già presenti."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _f(name: str, default: float) -> float:
    v = os.environ.get(name, "").strip()
    return float(v) if v else default


def _i(name: str, default: int) -> int:
    v = os.environ.get(name, "").strip()
    return int(v) if v else default


def _s(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


@dataclass
class Settings:
    # --- chiavi (opzionali) ---
    odds_api_key: str = ""
    odds_api_regions: str = "eu"
    odds_api_max_credits: int = 40
    api_football_key: str = ""
    api_football_max_requests: int = 60

    # --- generali ---
    stake_eur: float = 10.0
    timezone: str = "Europe/Rome"
    preferred_bookmakers: list[str] = field(default_factory=lambda: list(DEFAULT_BOOKMAKERS))
    # se True, una quota è "giocabile" solo se offerta da un bookmaker preferito
    require_preferred_bookmaker: bool = True
    # verifica partite: "auto" = obbligatoria la conferma da 2 fonti indipendenti quando una
    # seconda fonte (The Odds API / API-Football) è configurata; "always" = sempre; "never" = mai
    fixture_confirmation: str = "auto"
    max_matches: int = 0  # 0 = nessun limite

    # --- soglie di qualità (Quality Control) ---
    min_selection_score: float = 60.0
    min_ev: float = 0.0  # valore atteso minimo stimato per ogni gamba
    min_leg_odds: float = 1.20
    max_odds_age_minutes: int = 90
    min_minutes_before_kickoff: int = 10
    min_team_games: int = 5  # partite di campionato minime per squadra
    min_data_quality: float = 0.55
    # multipla principale
    acca_min_legs: int = 5
    acca_max_legs: int = 10
    acca_extra_leg_min_score: float = 68.0  # oltre la 5a gamba serve un punteggio più alto
    acca_min_joint_prob: float = 0.02  # sotto il 2% la multipla è considerata lotteria
    # diversificazione per campionato (partite della stessa lega sono correlate):
    # fino a `max_per_league` selezioni bastano i controlli normali; dalla successiva fino a
    # `max_per_league_hard` serve un punteggio ≥ `league_extra_min_score`; oltre, mai.
    max_per_league: int = 2
    max_per_league_hard: int = 4
    league_extra_min_score: float = 68.0
    # multipla alternativa prudente
    prudent_legs: int = 5
    prudent_min_leg_prob: float = 0.62

    # --- modello ---
    # peso massimo del modello statistico rispetto al mercato (prob. "de-vigged").
    # Il mercato è un forte stimatore: il modello lo corregge, non lo sostituisce.
    model_weight_max: float = 0.40
    monte_carlo_sims: int = 20000
    same_league_correlation: float = 0.05
    random_seed: int = 365

    # --- infrastruttura ---
    http_concurrency: int = 6
    http_min_interval_s: float = 0.08
    http_timeout_s: float = 20.0
    http_retries: int = 3
    cache_ttl_fixtures_s: int = 600
    cache_ttl_odds_s: int = 600
    cache_ttl_stats_s: int = 6 * 3600

    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 8365
    dashboard_token: str = ""

    data_dir: Path = DATA_DIR

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    def validate(self) -> None:
        if self.stake_eur <= 0 or self.stake_eur > MAX_STAKE_EUR:
            raise ValueError(
                f"STAKE_EUR={self.stake_eur} non valido: il budget massimo è {MAX_STAKE_EUR:.0f} € e non può essere superato"
            )
        if not (5 <= self.acca_min_legs <= self.acca_max_legs <= 10):
            raise ValueError("La multipla deve avere tra 5 e 10 eventi")


def get_settings(load_env_file: bool = True) -> Settings:
    if load_env_file:
        load_dotenv()
    s = Settings(
        odds_api_key=_s("ODDS_API_KEY"),
        odds_api_regions=_s("ODDS_API_REGIONS", "eu"),
        odds_api_max_credits=_i("ODDS_API_MAX_CREDITS_PER_RUN", 40),
        api_football_key=_s("API_FOOTBALL_KEY"),
        api_football_max_requests=_i("API_FOOTBALL_MAX_REQUESTS_PER_RUN", 60),
        stake_eur=_f("STAKE_EUR", 10.0),
        timezone=_s("TIMEZONE", "Europe/Rome"),
        preferred_bookmakers=[b.strip().lower() for b in _s("PREFERRED_BOOKMAKERS", ",".join(DEFAULT_BOOKMAKERS)).split(",") if b.strip()],
        require_preferred_bookmaker=_s("REQUIRE_PREFERRED_BOOKMAKER", "true").lower() in ("1", "true", "yes", "si"),
        fixture_confirmation=_s("FIXTURE_CONFIRMATION", "auto").lower(),
        max_matches=_i("MAX_MATCHES", 0),
        min_selection_score=_f("MIN_SELECTION_SCORE", 60.0),
        min_ev=_f("MIN_EV", 0.0),
        min_leg_odds=_f("MIN_LEG_ODDS", 1.20),
        max_odds_age_minutes=_i("MAX_ODDS_AGE_MINUTES", 90),
        max_per_league=_i("MAX_PER_LEAGUE", 2),
        max_per_league_hard=_i("MAX_PER_LEAGUE_HARD", 4),
        league_extra_min_score=_f("LEAGUE_EXTRA_MIN_SCORE", 68.0),
        dashboard_host=_s("DASHBOARD_HOST", "0.0.0.0"),
        dashboard_port=_i("DASHBOARD_PORT", 8365),
        dashboard_token=_s("DASHBOARD_TOKEN"),
    )
    if os.environ.get("FBI_DATA_DIR"):
        s.data_dir = Path(os.environ["FBI_DATA_DIR"])
    s.validate()
    return s
