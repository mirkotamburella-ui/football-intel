"""Classe base degli agenti e schema del report JSON.

Ogni agente è un modulo indipendente con un compito specifico. Riceve solo:
  - il `RunContext` (configurazione + client dati, in sola lettura)
  - i report JSON degli agenti da cui dichiara di dipendere (`depends_on`)
e produce un report JSON con questa struttura:

{
  "agent_id": "A07", "agent_name": "...", "version": "1.0",
  "status": "ok" | "partial" | "failed",
  "started_at": ISO, "finished_at": ISO, "duration_s": float,
  "summary": {...},            # sintesi leggibile
  "matches": {event_id: {...}},# analisi per partita (con fonti e timestamp)
  "sources": [{"source", "fetched_at"}],
  "warnings": [...], "errors": [...]
}
"""
from __future__ import annotations

import json
import logging
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..context import RunContext, utc_iso

REQUIRED_KEYS = {"agent_id", "agent_name", "version", "status", "started_at", "finished_at", "summary", "matches", "sources", "warnings", "errors"}


class Agent:
    id = "A00"
    name = "Base"
    version = "1.0"
    depends_on: tuple[str, ...] = ()
    description = ""

    def __init__(self) -> None:
        self.log = logging.getLogger(f"fbi.agent.{self.id}")
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self.sources: dict[str, dict] = {}

    # da implementare
    def analyze(self, ctx: RunContext, inputs: dict[str, dict]) -> tuple[dict, dict]:
        raise NotImplementedError

    # helper
    def add_source(self, s: dict | None) -> None:
        if s and s.get("source"):
            self.sources[s["source"]] = s

    def warn(self, msg: str) -> None:
        self.log.warning(msg)
        self.warnings.append(msg)

    def per_match(self, ctx: RunContext, fixtures: list[dict], fn, workers: int = 6) -> dict[str, Any]:
        """Esegue `fn(fixture)` per ogni partita in parallelo; un errore su una partita non blocca le altre."""
        out: dict[str, Any] = {}

        def safe(fx):
            try:
                return fx["event_id"], fn(fx)
            except Exception as ex:  # noqa: BLE001
                self.errors.append(f"{fx['event_id']}: {type(ex).__name__}: {ex}")
                self.log.debug(traceback.format_exc())
                return fx["event_id"], {"error": f"{type(ex).__name__}: {ex}"}

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=self.id) as pool:
            for eid, res in pool.map(safe, fixtures):
                if res is not None:
                    out[eid] = res
        return out

    def execute(self, ctx: RunContext, inputs: dict[str, dict]) -> dict:
        started = time.time()
        t0 = utc_iso(ctx.now_utc.fromtimestamp(started, ctx.now_utc.tzinfo))
        status = "ok"
        summary, matches = {}, {}
        try:
            summary, matches = self.analyze(ctx, inputs)
            if self.errors:
                status = "partial"
        except Exception as ex:  # noqa: BLE001
            status = "failed"
            self.errors.append(f"{type(ex).__name__}: {ex}")
            self.log.error("agente fallito: %s\n%s", ex, traceback.format_exc())
        ended = time.time()
        report = {
            "agent_id": self.id,
            "agent_name": self.name,
            "version": self.version,
            "description": self.description,
            "depends_on": list(self.depends_on),
            "status": status,
            "started_at": t0,
            "finished_at": utc_iso(ctx.now_utc.fromtimestamp(ended, ctx.now_utc.tzinfo)),
            "duration_s": round(ended - started, 3),
            "summary": summary,
            "matches": matches,
            "sources": sorted(self.sources.values(), key=lambda s: s["source"])[:400],
            "warnings": self.warnings[:200],
            "errors": self.errors[:200],
        }
        json.dumps(report)  # garantisce che il report sia JSON serializzabile
        return report


def validate_report(r: dict) -> list[str]:
    problems = [f"chiave mancante: {k}" for k in REQUIRED_KEYS - set(r)]
    if r.get("status") not in ("ok", "partial", "failed"):
        problems.append("status non valido")
    return problems
