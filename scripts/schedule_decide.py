"""Decide cosa fare in un giro automatico orario, in base all'ora italiana e allo stato salvato.

GitHub può avviare i giri programmati con ore di ritardo: invece di legare ogni azione a un orario
fisso, ogni giro (ogni ora) controlla cosa manca e lo fa. Stampa una tra:
  run      analisi completa (oggi + domani) — se dalle 7 in poi non c'è ancora quella di oggi
  refresh  aggiornamento quote/formazioni — una volta tra le 14 e le 20, se non già fatto oggi
  verify   solo verifica risultati (notte, nessun credito)
  none     niente da fare (si ripubblica solo la pagina)
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

data = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
now = datetime.now(ZoneInfo("Europe/Rome"))
today, hour = now.date().isoformat(), now.hour


def load(name):
    try:
        return json.loads((data / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


latest, state = load("latest.json"), load("state.json")
if hour < 7:
    action = "verify"
elif latest.get("analysis_date") != today:
    action = "run"
elif 14 <= hour <= 20 and state.get("refresh") != today:
    action = "refresh"
else:
    action = "none"
print(action)
