"""Test end-to-end OFFLINE: i 12 agenti girano su risposte reali ESPN catturate l'8/11/2026
(ridotte con scripts/build_test_fixture.py), senza rete."""
import json
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from fbi.agents.base import validate_report
from fbi.config import Settings
from fbi.context import build_context
from fbi.history import load as load_history
from fbi.orchestrator import AGENT_CLASSES, persist, run_analysis
from fbi.quality import qc_report

FIX = Path(__file__).parent / "fixtures" / "e2e"


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(FIX / "cache", cls.tmp / "cache")
        meta = json.loads((FIX / "meta.json").read_text())
        s = Settings()
        s.data_dir = cls.tmp
        s.max_matches = 3
        s.min_minutes_before_kickoff = 10  # regola in vigore quando la fixture è stata catturata
        cls.ctx = build_context(s, offline=True, now_utc=datetime.fromisoformat(meta["now_utc"].replace("Z", "+00:00")))
        cls.report, cls.agent_reports = run_analysis(cls.ctx)
        cls.meta = meta

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_twelve_independent_agents(self):
        self.assertEqual(len(AGENT_CLASSES), 12)
        self.assertEqual(len({c.id for c in AGENT_CLASSES}), 12)
        self.assertEqual(self.report["agents_total"], 12)
        for aid, r in self.agent_reports.items():
            self.assertEqual(validate_report(r), [], aid)
            self.assertEqual(r["status"], "ok", (aid, r["errors"]))
            json.dumps(r)  # report JSON serializzabile

    def test_parallel_stage(self):
        tl = {t["agent_id"]: t for t in self.report["timeline"]}
        # A02..A08 partono tutti dopo A01 e prima che il più lento di loro finisca
        first_end = min(tl[a]["end_s"] for a in ("A02", "A03", "A04", "A05", "A06", "A07", "A08"))
        starts = [tl[a]["start_s"] for a in ("A02", "A03", "A04", "A05", "A06", "A07", "A08")]
        self.assertTrue(all(s <= first_end for s in starts), "gli agenti di analisi devono girare in parallelo")
        self.assertGreaterEqual(min(starts), tl["A01"]["end_s"] - 1e-6)
        self.assertGreaterEqual(tl["A12"]["start_s"], max(tl["A10"]["end_s"], tl["A11"]["end_s"]) - 1e-6)

    def test_matches_and_sources(self):
        self.assertEqual(self.report["matches_analyzed"], 3)
        self.assertEqual({c["event_id"] for c in self.report["match_cards"]}, set(self.meta["events"]))
        for c in self.report["match_cards"]:
            for s in c["selections"]:
                self.assertTrue(s["odds_source"].startswith("https://"))
                self.assertTrue(s["odds_fetched_at"].endswith("Z"))
                self.assertTrue(0 < s["p_est"] < 1)
                self.assertLessEqual(s["p_est_range_90"][0], s["p_est"] + 1e-9)
                self.assertEqual(len(s["score"]["components"]), 7)
                self.assertEqual(sum(k["max"] for k in s["score"]["components"]), 100)
                self.assertTrue(0 <= s["score"]["final"] <= 100)

    def test_quality_and_honesty(self):
        self.assertEqual(qc_report(self.report), [])
        main = self.report["main_accumulator"]
        # con 3 partite non può esistere una multipla conforme: deve essere dichiarato
        self.assertEqual(main["status"], "not_possible")
        self.assertTrue(main["reason"])
        # dati mancanti dichiarati, non inventati
        a03 = self.agent_reports["A03"]["matches"]
        for m in a03.values():
            self.assertIsNone(m["home"]["xg_for_pg"])
            self.assertIn("non disponibili", m["home"]["xg_note"])
        self.assertTrue(any("Telefono Verde" in d for d in self.report["disclaimer"]))

    def test_persist_and_history(self):
        persist(self.ctx, self.report, self.agent_reports)
        self.assertTrue((self.tmp / "latest.json").exists())
        self.assertTrue((self.tmp / "reports" / self.report["analysis_date"] / "agents" / "A07.json").exists())
        h = load_history(self.ctx.settings)
        self.assertIn(self.report["analysis_date"], h["days"])


if __name__ == "__main__":
    unittest.main()
