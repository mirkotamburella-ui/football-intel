"""Server della dashboard (solo libreria standard).

GET  /                         dashboard mobile (web/)
GET  /api/latest               ultimo report
GET  /api/history              storico previsioni + accuratezza
GET  /api/reports              indice dei report
GET  /api/reports/<run_id>     report specifico
GET  /api/agents/<date>/<id>   report JSON completo di un agente
GET  /api/status               stato di un'analisi avviata dal telefono
POST /api/run   /api/refresh   /api/verify   (header X-Token = DASHBOARD_TOKEN; disabilitati se il token non è impostato)
"""
from __future__ import annotations

import hmac
import json
import logging
import re
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import WEB_DIR, Settings

log = logging.getLogger("fbi.server")
_job = {"running": False, "last": None, "error": None}
_job_lock = threading.Lock()


def _start_job(settings: Settings, kind: str) -> bool:
    with _job_lock:
        if _job["running"]:
            return False
        _job.update(running=True, error=None, kind=kind)

    def work():
        try:
            from .cli import diff_reports
            from .context import build_context

            ctx = build_context(settings)
            if kind == "verify":
                from .history import verify
                verify(ctx)
            else:
                from .orchestrator import persist, run_analysis
                old = None
                if kind == "refresh":
                    try:
                        old = json.loads((settings.data_dir / "latest.json").read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        pass
                report, reports = run_analysis(ctx)
                report["run_type"] = kind
                report["changes_since_previous"] = diff_reports(old, report) if old else []
                persist(ctx, report, reports)
            _job["last"] = kind
        except Exception as ex:  # noqa: BLE001
            log.exception("job %s fallito", kind)
            _job["error"] = str(ex)
        finally:
            _job["running"] = False

    threading.Thread(target=work, daemon=True).start()
    return True


def make_handler(settings: Settings):
    data_dir: Path = settings.data_dir

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(WEB_DIR), **kw)

        def log_message(self, fmt, *args):
            log.info("%s %s", self.address_string(), fmt % args)

        def end_headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store" if self.path.startswith("/api/") else "max-age=60")
            super().end_headers()

        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _file_json(self, path: Path):
            try:
                body = path.read_bytes()
            except OSError:
                return self._json({"error": "non disponibile", "hint": "esegui prima: python -m fbi run"}, 404)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            p = self.path.split("?")[0]
            if p == "/api/latest":
                return self._file_json(data_dir / "latest.json")
            if p == "/api/history":
                return self._file_json(data_dir / "history.json")
            if p == "/api/reports":
                return self._file_json(data_dir / "reports" / "index.json")
            if p == "/api/status":
                return self._json({k: _job.get(k) for k in ("running", "last", "error", "kind")} | {"actions_enabled": bool(settings.dashboard_token)})
            m = re.fullmatch(r"/api/reports/(\d{8}T\d{6}Z)", p)
            if m:
                rid = m.group(1)
                day = f"{rid[:4]}-{rid[4:6]}-{rid[6:8]}"
                return self._file_json(data_dir / "reports" / day / f"report_{rid}.json")
            m = re.fullmatch(r"/api/agents/(\d{4}-\d{2}-\d{2})/(A\d{2})", p)
            if m:
                return self._file_json(data_dir / "reports" / m.group(1) / "agents" / f"{m.group(2)}.json")
            if p.startswith("/api/"):
                return self._json({"error": "endpoint sconosciuto"}, 404)
            return super().do_GET()

        def do_POST(self):  # noqa: N802
            p = self.path.split("?")[0]
            kinds = {"/api/run": "run", "/api/refresh": "refresh", "/api/verify": "verify"}
            if p not in kinds:
                return self._json({"error": "endpoint sconosciuto"}, 404)
            token = self.headers.get("X-Token", "")
            if not settings.dashboard_token or not hmac.compare_digest(token, settings.dashboard_token):
                return self._json({"error": "non autorizzato: imposta DASHBOARD_TOKEN e invialo nell'header X-Token"}, 403)
            if not _start_job(settings, kinds[p]):
                return self._json({"error": "un'analisi è già in corso"}, 409)
            return self._json({"started": kinds[p]}, 202)

    return Handler


def serve(settings: Settings, host: str, port: int) -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(settings))
    print(f"Dashboard su http://{host}:{port}  (dal telefono nella stessa rete: http://<IP-del-PC>:{port})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
