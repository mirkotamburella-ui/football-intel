"""Costruisce la fixture del test end-to-end a partire da una cache HTTP reale.

Uso:
  FBI_DATA_DIR=/tmp/cap python -m fbi run --max-matches 3
  python scripts/build_test_fixture.py /tmp/cap tests/fixtures/e2e

Le risposte vengono RIDOTTE (solo i campi letti dal codice, scoreboard limitato alle
partite analizzate) ma non alterate: i valori sono quelli reali restituiti dalle API.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def trim_summary(d):
    keep = {"header", "lastFiveGames", "seasonseries", "rosters", "injuries"}
    out = {k: v for k, v in d.items() if k in keep}
    h = out.get("header") or {}
    out["header"] = {"league": {k: (h.get("league") or {}).get(k) for k in ("slug", "name", "isTournament")},
                     "competitions": [{"status": c.get("status"), "competitors": [{k: x.get(k) for k in ("homeAway", "score")} for x in c.get("competitors", [])]}
                                      for c in h.get("competitions", [])]}
    for b in out.get("lastFiveGames") or []:
        b["team"] = {"id": b["team"]["id"], "displayName": b["team"].get("displayName")}
        b["events"] = [{k: e.get(k) for k in ("id", "gameDate", "homeTeamId", "awayTeamId", "homeTeamScore", "awayTeamScore", "gameResult", "competitionName", "leagueName")}
                       | {"opponent": {"id": e.get("opponent", {}).get("id"), "displayName": e.get("opponent", {}).get("displayName")}} for e in b.get("events", [])]
    for s in out.get("seasonseries") or []:
        s["events"] = [{"id": e["id"], "date": e["date"], "statusType": {"completed": e.get("statusType", {}).get("completed")},
                        "competitors": [{"homeAway": c["homeAway"], "score": c.get("score"), "team": {"id": c["team"]["id"]}} for c in e.get("competitors", [])]}
                       for e in s.get("events", [])]
    return out


def trim_schedule(d):
    evs = []
    for e in d.get("events", []):
        c = e["competitions"][0]
        evs.append({"id": e["id"], "date": e["date"], "competitions": [{
            "status": {"type": {"completed": c["status"]["type"].get("completed")}}, "neutralSite": c.get("neutralSite", False),
            "competitors": [{"homeAway": t["homeAway"], "team": {"id": t["team"]["id"], "displayName": t["team"]["displayName"]},
                             "score": {"value": (t.get("score") or {}).get("value")} if isinstance(t.get("score"), dict) else t.get("score")} for t in c["competitors"]]}]})
    return {"events": evs, "requestedSeason": d.get("requestedSeason")}


def trim_standings(d):
    ch = []
    for c in d.get("children", []):
        ch.append({"name": c.get("name"), "standings": {"entries": [
            {"team": {"id": e["team"]["id"], "displayName": e["team"]["displayName"]}, "note": e.get("note"),
             "stats": [{"name": s["name"], "value": s.get("value")} for s in e.get("stats", [])]} for e in c["standings"]["entries"]]}})
    return {"name": d.get("name"), "season": d.get("season"), "children": ch}


def trim_odds(d):
    items = []
    for it in d.get("items", []):
        x = {k: it.get(k) for k in ("provider", "overUnder", "overOdds", "underOdds", "homeTeamOdds", "awayTeamOdds", "drawOdds", "current", "open")}
        if "bettingOdds" in it:
            x["bettingOdds"] = {"teamOdds": it["bettingOdds"].get("teamOdds")}
        items.append(x)
    return {"items": items}


def main(src: str, dst: str, keep_events: set[str]) -> None:
    src_dir, dst_dir = Path(src) / "cache", Path(dst) / "cache"
    dst_dir.mkdir(parents=True, exist_ok=True)
    for p in src_dir.glob("*.json"):
        blob = json.loads(p.read_text(encoding="utf-8"))
        url, d = blob["source"], blob["data"]
        if "/scoreboard" in url:
            d = {"events": [e for e in d.get("events", []) if e["id"] in keep_events]}
        elif "/summary" in url:
            if not any(f"event={e}" in url for e in keep_events):
                continue
            d = trim_summary(d)
        elif "/schedule" in url:
            d = trim_schedule(d)
        elif "/standings" in url:
            d = trim_standings(d)
        elif "/odds" in url:
            d = trim_odds(d)
        blob["data"] = d
        (dst_dir / p.name).write_text(json.dumps(blob, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    latest = json.loads((Path(src) / "latest.json").read_text(encoding="utf-8"))
    events = {c["event_id"] for c in latest["match_cards"]}
    main(src, dst, events)
    meta = {"now_utc": latest["generated_at_utc"], "events": sorted(events), "captured_from": "API reali ESPN (risposte ridotte ai campi usati)"}
    (Path(dst) / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print("fixture scritta in", dst, meta)
