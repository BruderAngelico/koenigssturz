# -*- coding: utf-8 -*-
"""Lokale Browser-UI für Königssturz. Sicherung bleibt Python auf Disk, nicht im Browser."""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import socket
import ssl
import sys
import tempfile
import threading
import time
import webbrowser
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Dict

import imaplib
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from starlette.background import BackgroundTask

import kingfall as kf
import kingfall_analyze as ka
import kingfall_va_pack as vapack
import kingfall_vereinsarchiv as va

HOST = "127.0.0.1"
PORT_CANDIDATES = (18765, 18080, 19000, 8088, 8000, 8765)
PACK_LOCK = threading.Lock()
PACK_FILES: Dict[str, Dict[str, str]] = {}
VA_AUDIO_TYPES = {
    ".aac": "audio/aac",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
}


def _port_from_args():
    args = sys.argv
    if "--port" in args:
        idx = args.index("--port")
        if idx + 1 < len(args):
            return int(args[idx + 1])
    env = os.environ.get("KINGFALL_PORT")
    if env:
        return int(env)
    return None


def _can_bind(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _os_free_port(host):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def pick_port(host=HOST):
    wanted = _port_from_args()
    tried = []
    if wanted:
        tried.append(wanted)
    for port in PORT_CANDIDATES:
        if port not in tried:
            tried.append(port)
    for port in tried:
        if _can_bind(host, port):
            return port
    return _os_free_port(host)


class WebState:
    def __init__(self):
        self.lock = threading.Lock()
        self.db_status = "Bereit"
        self.db_error = ""
        self.db_running = False
        self.db_paused = False
        self.login_status = "Kein Auto-Refresh"
        self.output_dir = ""
        self.mail_output = ""
        self.mail_status = "Bereit"
        self.mail_error = ""
        self.mail_running = False
        self.schemas = []
        self.selected = []
        self.tables = []
        self.folders = []
        self.browse = []
        self.browse_page = 0
        self.phase = "idle"
        self.curl = ""
        self.base_url = ""
        self.headers = {}
        self.auth = kf.AuthSession()
        self.page_size = kf.PAGE_SIZE
        self.test_run = False
        self.then_export = False
        self.current_table_index = 0
        self.count_table_index = 0
        self.stop = False
        self.mail_stop = False
        self.va_curl = ""
        self.va_headers = {}
        self.va_auth = kf.AuthSession()
        self.va_base = ""
        self.va_status = "Bereit"
        self.va_error = ""
        self.va_running = False
        self.va_stop = False
        self.va_progress = {}
        self.va_preview = []
        self.va_preview_counts = {}
        self.va_local_rev = 0
        self.pack_running = False
        self.pack_stop = False
        self.pack_progress = {}
        self.pack_error = ""
        self.pack_url = ""
        self.pack_saved_path = ""
        threading.Thread(target=self._watchdog, daemon=True).start()

    def snapshot(self):
        with self.lock:
            total_browse = len(self.browse)
            max_page = 0 if total_browse <= 0 else (total_browse - 1) // kf.MAIL_BROWSE_PAGE
            if self.browse_page > max_page:
                self.browse_page = max_page
            start = self.browse_page * kf.MAIL_BROWSE_PAGE
            page = self.browse[start : start + kf.MAIL_BROWSE_PAGE]
            if total_browse <= 0:
                browse_label = "Keine Mails"
            else:
                browse_label = "Seite %s / %s · %s–%s von %s" % (
                    self.browse_page + 1,
                    max_page + 1,
                    kf.format_de_int(start + 1),
                    kf.format_de_int(min(total_browse, start + kf.MAIL_BROWSE_PAGE)),
                    kf.format_de_int(total_browse),
                )
            return {
                "db_status": self.db_status,
                "db_error": self.db_error,
                "db_running": self.db_running,
                "db_paused": self.db_paused,
                "login_status": self.login_status,
                "output_dir": self.output_dir,
                "mail_output": self.mail_output,
                "mail_status": self.mail_status,
                "mail_error": self.mail_error,
                "mail_running": self.mail_running,
                "schemas": list(self.schemas),
                "selected": list(self.selected),
                "schema_hint": (
                    "%s Schemas – %s vorausgewählt" % (len(self.schemas), len(self.selected))
                    if self.schemas
                    else "Zuerst „Hole Tables“ – dann Schemas ankreuzen."
                ),
                "tables": [self._table_view(t) for t in self.tables],
                "folders": [self._folder_view(f) for f in self.folders],
                "browse": page,
                "browse_label": browse_label,
                "browse_page": self.browse_page,
                "phase": self.phase,
                "va_status": self.va_status,
                "va_error": self.va_error,
                "va_running": self.va_running,
                "va_progress": dict(self.va_progress),
                "va_preview": list(self.va_preview),
                "va_preview_counts": dict(self.va_preview_counts),
                "va_token_status": self._va_token_status(),
                "va_local_rev": self.va_local_rev,
                "audio_prep": va.AUDIO_PREP.snapshot(),
                "pack_running": self.pack_running,
                "pack_progress": dict(self.pack_progress),
                "pack_error": self.pack_error,
                "pack_url": self.pack_url,
                "pack_saved_path": self.pack_saved_path,
            }

    def _bar(self, exported, total, status):
        exported = exported or 0
        if total and total > 0:
            return min(100, int(round(100.0 * exported / float(total))))
        if status and str(status).startswith("Fertig"):
            return 100
        return 0

    def _table_view(self, t):
        exported = t.get("exported") or 0
        est = t.get("est_rows")
        status = t.get("status") or ""
        return {
            "schema": t.get("schema"),
            "name": t.get("name"),
            "strategy": t.get("strategy"),
            "exported": exported,
            "est_rows": est,
            "status": status,
            "progress": self._bar(exported, est, status),
        }

    def _folder_view(self, f):
        exported = f.get("exported") or 0
        total = f.get("total")
        status = f.get("status") or ""
        return {
            "display": f.get("display"),
            "imap_name": f.get("imap_name"),
            "exported": exported,
            "total": total,
            "status": status,
            "selected": bool(f.get("selected", True)),
            "progress": self._bar(exported, total, status),
        }

    def set_browse_page(self, delta=0, page=None):
        total = len(self.browse)
        max_page = 0 if total <= 0 else (total - 1) // kf.MAIL_BROWSE_PAGE
        if page is not None:
            self.browse_page = page
        else:
            self.browse_page += int(delta)
        if self.browse_page < 0:
            self.browse_page = 0
        if self.browse_page > max_page:
            self.browse_page = max_page

    def _watchdog(self):
        while True:
            time.sleep(15)
            if not self.auth.refresh_token or self.auth.seconds_left() > 90:
                continue
            if self.auth.refresh():
                with self.lock:
                    self.auth.apply_to_headers(self.headers)
                    self._login_label()

    def _login_label(self):
        if self.auth.refresh_token:
            left = int(max(0, self.auth.seconds_left()))
            stamp = (
                datetime.fromtimestamp(self.auth.expires_at).strftime("%H:%M:%S")
                if self.auth.expires_at
                else "?"
            )
            self.login_status = "Auto-Refresh an · gültig bis %s (%s min)" % (stamp, left // 60)
        elif self.auth.access_token:
            left = int(max(0, self.auth.seconds_left()))
            self.login_status = "Token aus cURL · %s min, ohne Refresh" % (left // 60)
        else:
            self.login_status = "Kein Auto-Refresh"

    def set_curl(self, curl):
        url, headers = kf.parse_curl(curl)
        self.curl = curl
        self.auth.ingest_headers(headers)
        if self.auth.access_token:
            self.auth.apply_to_headers(headers)
        self.base_url = url
        self.headers = headers
        self._login_label()
        self.db_error = ""
        left = int(max(0, self.auth.seconds_left()))
        self.db_status = "cURL übernommen · Token noch ca. %s min" % max(1, left // 60) if left else "cURL übernommen"

    def _va_token_status(self):
        if not self.va_auth.access_token:
            return "Kein Token"
        left = self.va_auth.seconds_left()
        if left < 20:
            return "Token abgelaufen"
        return "Token noch ca. %s min" % max(1, int(left) // 60)

    def set_va_curl(self, curl):
        url, headers = kf.parse_curl(curl)
        self.va_curl = curl
        auth = kf.AuthSession()
        auth.ingest_headers(headers)
        if not auth.access_token:
            raise RuntimeError("Im cURL steckt kein Authorization-Token.")
        left = auth.seconds_left()
        if left < 20:
            raise RuntimeError(
                "Der Token ist abgelaufen. Bitte in den DevTools einen frischen Request kopieren."
            )
        auth.apply_to_headers(headers)
        if not kf.header_value(headers, "origin"):
            headers["Origin"] = va.VA_ORIGIN
        self.va_auth = auth
        self.va_headers = headers
        self.va_base = va.base_from_url(url)
        self.va_error = ""
        self.va_status = "Token übernommen · noch ca. %s min gültig" % max(1, int(left) // 60)

    def start_va_download(self, kinds, curl=""):
        if curl and str(curl).strip():
            self.set_va_curl(curl)
        with self.lock:
            if self.va_running:
                raise RuntimeError("Es läuft bereits ein Vereinsarchiv-Download.")
            if not self.va_auth.access_token:
                raise RuntimeError("Zuerst einen cURL mit Token einfügen.")
            if self.va_auth.seconds_left() < 20:
                raise RuntimeError(
                    "Der Token ist abgelaufen. Bitte in den DevTools einen frischen Request kopieren."
                )
            selected = [k for k in (kinds or []) if k in va.KINDS]
            if not selected:
                raise RuntimeError("Bitte öffentlich und/oder intern ankreuzen.")
            self.va_stop = False
            self.va_error = ""
            self.va_progress = {}
            self.va_running = True
            self.va_status = "Starte Download …"
        threading.Thread(target=self._va_job, args=(selected,), daemon=True).start()

    def start_va_preview(self, kinds, curl=""):
        if curl and str(curl).strip():
            self.set_va_curl(curl)
        with self.lock:
            if self.va_running:
                raise RuntimeError("Es läuft bereits ein Vereinsarchiv-Download.")
            if self.va_auth.seconds_left() < 20:
                raise RuntimeError(
                    "Der Token ist abgelaufen. Bitte in den DevTools einen frischen Request kopieren."
                )
            selected = [k for k in (kinds or []) if k in va.KINDS]
            if not selected:
                raise RuntimeError("Bitte öffentlich und/oder intern ankreuzen.")
            self.va_stop = False
            self.va_error = ""
            self.va_progress = {}
            self.va_running = True
            self.va_status = "Lade Online-Liste …"
        threading.Thread(target=self._va_preview_job, args=(selected,), daemon=True).start()

    def _va_preview_job(self, kinds):
        try:
            with self.lock:
                headers = dict(self.va_headers)
                base = self.va_base
                self.va_auth.apply_to_headers(headers)

            def should_stop():
                return self.va_stop

            def on_progress(info):
                with self.lock:
                    self.va_progress = dict(info)
                    self.va_status = self._va_status_from_progress(info)

            plan = va.compare_kinds(
                base,
                headers,
                kinds,
                should_stop=should_stop,
                progress_cb=on_progress,
            )
            counts = plan.get("counts") or {}
            with self.lock:
                self.va_preview = list(plan.get("items") or [])
                self.va_preview_counts = dict(counts)
                if self.va_stop:
                    self.va_status = "Abgleich abgebrochen"
                else:
                    self.va_status = (
                        "Abgleich · %s online · %s neu · %s Audio fehlt · %s Folien fehlen · %s vollständig"
                        % (
                            counts.get("remote") or 0,
                            counts.get("neu") or 0,
                            counts.get("audio") or 0,
                            counts.get("folien") or 0,
                            counts.get("ok") or 0,
                        )
                    )
                self.va_error = ""
                self.va_local_rev += 1
        except Exception as exc:
            with self.lock:
                self.va_status = "Fehler"
                self.va_error = str(exc)
        finally:
            with self.lock:
                self.va_running = False
                self.va_local_rev += 1

    def stop_va(self):
        self.va_stop = True
        self.va_status = "Stoppe …"

    def _va_status_from_progress(self, info):
        phase = info.get("phase")
        current = info.get("current") or ""
        if phase == "listing":
            return current
        if phase == "plan":
            return current or "Abgleich fertig"
        return "Download %s/%s · %s (übersprungen %s, Fehler %s)" % (
            info.get("done") or 0,
            info.get("total") or 0,
            current,
            info.get("skipped") or 0,
            info.get("failed") or 0,
        )

    def _va_job(self, kinds):
        try:
            with self.lock:
                headers = dict(self.va_headers)
                base = self.va_base
                self.va_auth.apply_to_headers(headers)

            def should_stop():
                return self.va_stop

            def on_progress(info):
                with self.lock:
                    self.va_progress = dict(info)
                    self.va_status = self._va_status_from_progress(info)
                    preview = info.get("preview")
                    if preview:
                        self.va_preview = list(preview)
                        self.va_local_rev += 1
                    if info.get("saved"):
                        self.va_local_rev += 1

            stats = va.download_kinds(
                base,
                headers,
                kinds,
                should_stop=should_stop,
                progress_cb=on_progress,
            )
            saved = (stats.get("done") or 0) - (stats.get("skipped") or 0)
            errors = stats.get("errors") or []
            with self.lock:
                if self.va_stop:
                    self.va_status = "Abgebrochen · %s/%s" % (
                        stats.get("done") or 0,
                        stats.get("total") or 0,
                    )
                else:
                    self.va_status = "Fertig · %s neu, %s schon da, %s Fehler" % (
                        saved,
                        stats.get("skipped") or 0,
                        stats.get("failed") or 0,
                    )
                self.va_error = "\n".join(errors[:8])
                self.va_progress = dict(stats)
                self.va_progress.pop("errors", None)
            if not self.va_stop:
                def on_convert(info):
                    with self.lock:
                        self.va_status = info.get("status") or "Wandle Audio …"
                        self.va_progress = dict(info)

                va.convert_pending(stop_fn=should_stop, progress_cb=on_convert)
                with self.lock:
                    snap = va.AUDIO_PREP.snapshot()
                    if not self.va_stop:
                        self.va_status = (self.va_status or "Fertig") + " · " + (snap.get("status") or "Audio bereit")
        except Exception as exc:
            with self.lock:
                self.va_status = "Fehler"
                self.va_error = str(exc)
        finally:
            with self.lock:
                self.va_running = False
                self.va_local_rev += 1

    def start_pack(self, kinds, ids, date_from, date_to):
        with self.lock:
            if self.pack_running:
                raise RuntimeError("Paket läuft bereits.")
            self.pack_running = True
            self.pack_stop = False
            self.pack_error = ""
            self.pack_url = ""
            self.pack_saved_path = ""
            self.pack_progress = {"status": "Starte …", "pct": 0, "eta_sec": None}
        threading.Thread(
            target=self._pack_worker,
            args=(kinds, ids, date_from, date_to),
            daemon=True,
        ).start()

    def stop_pack(self):
        with self.lock:
            self.pack_stop = True
            self.pack_progress = dict(self.pack_progress)
            self.pack_progress["status"] = "Stoppe …"

    def _pack_worker(self, kinds, ids, date_from, date_to):
        handle = tempfile.NamedTemporaryFile(prefix="va-paket-", suffix=".zip", delete=False)
        handle.close()

        def progress(info):
            with self.lock:
                self.pack_progress = dict(info)

        def stop():
            with self.lock:
                return self.pack_stop

        try:
            vapack.build_zip(
                handle.name,
                kinds=kinds,
                date_from=date_from,
                date_to=date_to,
                ids=ids,
                progress=progress,
                stop=stop,
            )
            stamp = datetime.now().strftime("%Y-%m-%d")
            tags = "-".join(kinds) if kinds else "va"
            filename = "va-paket_%s_%s.zip" % (stamp, tags)
            out_dir = os.path.join(os.path.expanduser("~"), "Documents", "Königssturz", "Pakete")
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, filename)
            try:
                if os.path.abspath(handle.name) != os.path.abspath(out_path):
                    shutil.move(handle.name, out_path)
                final_path = out_path
            except OSError:
                final_path = handle.name
            size_mb = 0
            try:
                size_mb = int(round(os.path.getsize(final_path) / (1024 * 1024.0)))
            except OSError:
                pass
            token = secrets.token_hex(16)
            with PACK_LOCK:
                PACK_FILES[token] = {"path": final_path, "filename": filename, "keep": True}
            with self.lock:
                self.pack_saved_path = final_path if final_path == out_path else ""
                self.pack_url = "/api/va/pack/file/%s" % token
                self.pack_progress = {
                    "status": "Fertig (%s MB) – %s" % (size_mb, out_path if final_path == out_path else filename),
                    "pct": 100,
                }
            if final_path == out_path and sys.platform == "darwin":
                subprocess.Popen(["/usr/bin/open", "-R", out_path], close_fds=True)
        except va.JobCancelled:
            try:
                os.remove(handle.name)
            except OSError:
                pass
            with self.lock:
                self.pack_error = ""
                self.pack_progress = {"status": "Abgebrochen", "pct": 0}
        except Exception as exc:
            try:
                os.remove(handle.name)
            except OSError:
                pass
            with self.lock:
                self.pack_error = str(exc)
                self.pack_progress = {"status": "Fehler", "pct": 0}
        finally:
            with self.lock:
                self.pack_running = False

    def login(self, email, password, totp):
        if self.curl.strip():
            self.set_curl(self.curl)
        self.auth.login(email, password, totp or None)
        self.auth.apply_to_headers(self.headers)
        self._login_label()
        self.db_status = "Angemeldet"

    def recommended(self, name):
        if name in kf.DEFAULT_UNCHECKED_SCHEMAS:
            return False
        return True

    def run_sql(self, sql, kind="meta"):
        payload = {"query": sql, "disable_statement_timeout": True}
        timeout = 120 if kind == "data" else 60

        def post_once():
            self.auth.apply_to_headers(self.headers)
            return requests.post(self.base_url, headers=self.headers, json=payload, timeout=timeout)

        response = None
        for attempt in range(kf.SQL_RETRY_429 + 1):
            kf.pace_sql_request(kind)
            response = post_once()
            if kf.is_auth_error(response) and self.auth.refresh_token:
                if self.auth.refresh():
                    self.auth.apply_to_headers(self.headers)
                    self._login_label()
                    kf.pace_sql_request(kind)
                    response = post_once()
            if kf.is_auth_error(response):
                raise kf.TokenExpired()
            if kf.is_throttle_error(response):
                kf.note_sql_throttle()
                delay = kf.throttle_delay(response, attempt)
                self.db_status = "Rate-Limit, warte %s s (Versuch %s) …" % (int(delay), attempt + 1)
                time.sleep(delay)
                continue
            if response.status_code not in (200, 201):
                raise RuntimeError("HTTP %s: %s" % (response.status_code, response.text[:500]))
            data = response.json()
            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(str(data.get("error")))
            return response
        raise RuntimeError("HTTP 429: zu viele Anfragen, auch nach Warten. Später den Test-Run/Export fortsetzen.")

    def fetch_schemas(self):
        if self.db_running:
            raise RuntimeError("Es läuft bereits ein Job.")
        self.db_running = True
        self.db_error = ""
        self.db_status = "Lese Schema-Liste …"
        threading.Thread(target=self._fetch_schemas_job, daemon=True).start()

    def _fetch_schemas_job(self):
        try:
            rows = kf.extract_rows(self.run_sql(kf.SCHEMAS_SQL))
            names = []
            for row in rows:
                if isinstance(row, dict):
                    name = row.get("schema_name") or row.get("nspname")
                else:
                    name = str(row)
                if name:
                    names.append(name)
            if not names:
                raise RuntimeError("Keine Schemas gefunden.")
            with self.lock:
                self.schemas = names
                self.selected = [n for n in names if self.recommended(n)]
                self.db_status = "%s Schemas geladen. Auswahl prüfen, dann Tabellen laden." % len(names)
        except kf.TokenExpired:
            self.db_paused = True
            self.db_status = "Token abgelaufen – neuen cURL einfügen, dann Fortfahren"
        except Exception as exc:
            self.db_error = str(exc)
            self.db_status = "Fehler"
        finally:
            self.db_running = False

    def fetch_tables(self):
        if self.db_running:
            raise RuntimeError("Es läuft bereits ein Job.")
        self.db_running = True
        self.then_export = False
        self.db_error = ""
        self.db_status = "Hole Tabellen …"
        threading.Thread(target=self._fetch_tables_job, daemon=True).start()

    def start_export(self, test_run):
        if self.db_running:
            raise RuntimeError("Es läuft bereits ein Job.")
        self.test_run = bool(test_run)
        self.then_export = True
        self.page_size = kf.TEST_PAGE_SIZE if test_run else kf.PAGE_SIZE
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        folder = "beweissicherung_testrun_%s" % stamp if test_run else "beweissicherung_%s" % stamp
        self.output_dir = os.path.join(os.getcwd(), folder)
        os.makedirs(self.output_dir, exist_ok=True)
        self.db_running = True
        self.db_paused = False
        self.db_error = ""
        if self.tables:
            self.db_status = "Starte Export …"
            threading.Thread(target=self._export_from_loaded, daemon=True).start()
            return
        self.db_status = "Lese Tabellenliste …"
        threading.Thread(target=self._fetch_tables_job, daemon=True).start()

    def resume(self, curl):
        if not (curl and curl.strip()):
            raise RuntimeError("Bitte den neuen cURL ins Textfeld einfügen, dann Fortfahren.")
        self.set_curl(curl)
        left = self.auth.seconds_left()
        if not self.auth.access_token:
            raise RuntimeError("Im neuen cURL steckt kein Authorization-Token.")
        if left < 20:
            raise RuntimeError(
                "Der Token im eingefügten cURL ist schon abgelaufen "
                "(oder es steht noch der alte cURL im Feld). "
                "Bitte in den DevTools einen frischen Request kopieren."
            )
        self.db_paused = False
        self.db_running = True
        mins = max(1, int(left // 60))
        self.db_status = "Fortsetzung mit neuem Token · noch ca. %s min gültig …" % mins
        if self.phase == "listing_schemas":
            target = self._fetch_schemas_job
        elif self.phase == "listing":
            target = self._fetch_tables_job
        elif self.phase == "counting":
            target = self._count_job
        else:
            target = self._export_job
        threading.Thread(target=target, daemon=True).start()

    def _fetch_tables_job(self):
        self.phase = "listing"
        selected = list(self.selected) or ["public"]
        rows = None
        last_error = None
        try:
            for sql in (kf.tables_sql_pg(selected), kf.tables_sql_info(selected)):
                try:
                    rows = kf.extract_rows(self.run_sql(sql))
                    if rows:
                        break
                except kf.TokenExpired:
                    self.db_paused = True
                    self.db_status = "Token abgelaufen – neuen cURL einfügen, dann Fortfahren"
                    return
                except Exception as exc:
                    last_error = exc
                    rows = None
            if not rows:
                msg = "Keine Tabellen in den gewählten Schemas."
                if last_error:
                    msg = "%s %s" % (msg, last_error)
                raise RuntimeError(msg)
            tables = []
            for row in rows:
                name = row.get("table_name")
                schema = row.get("table_schema") or "public"
                if not name:
                    continue
                pk_cols = kf.parse_pk_cols(row.get("pk_cols"))
                est = kf.parse_int(row.get("est_rows"))
                strategy = ", ".join(pk_cols) if pk_cols else "kein PK → ctid"
                display_rows = kf.TEST_PAGE_SIZE if self.test_run else est
                tables.append(
                    {
                        "schema": schema,
                        "name": name,
                        "pk_cols": pk_cols,
                        "strategy": strategy,
                        "row_count": None if self.test_run else est,
                        "est_rows": display_rows if display_rows and display_rows > 0 else None,
                        "exported": 0,
                        "offset": 0,
                        "last_ctid": None,
                        "status": "Wartend",
                        "writer": None,
                        "resume_writer": False,
                        "schema_written": False,
                    }
                )
            self.tables = tables
            self.count_table_index = 0
            if self.test_run:
                for table in tables:
                    table["est_rows"] = kf.TEST_PAGE_SIZE
                    table["status"] = "Bereit"
                if self.then_export:
                    self.phase = "exporting"
                    self.current_table_index = 0
                    self.db_status = "Test-Run – %s Tabellen …" % len(tables)
                    self._export_job()
                    return
            else:
                self._count_job()
                return
            self.db_status = "%s Tabellen geladen." % len(tables)
        except kf.TokenExpired:
            self.db_paused = True
            self.db_status = "Token abgelaufen – neuen cURL einfügen, dann Fortfahren"
        except Exception as exc:
            self.db_error = str(exc)
            self.db_status = "Fehler"
        finally:
            if not self.db_paused and self.phase != "exporting" and self.phase != "counting":
                self.db_running = False

    def _count_job(self):
        self.phase = "counting"
        try:
            while self.count_table_index < len(self.tables):
                if self.db_paused or not self.db_running:
                    return
                table = self.tables[self.count_table_index]
                table["status"] = "Zähle Zeilen …"
                quoted = kf.quote_table(table.get("schema") or "public", table["name"])
                try:
                    n = kf.extract_count(kf.extract_rows(self.run_sql("SELECT COUNT(*)::bigint AS n FROM %s;" % quoted)))
                    if n is not None:
                        table["row_count"] = n
                        table["est_rows"] = n
                    table["status"] = "Bereit"
                except kf.TokenExpired:
                    table["status"] = "Token abgelaufen – Pause"
                    self.db_paused = True
                    self.db_status = "Token abgelaufen – neuen cURL einfügen, dann Fortfahren"
                    return
                except Exception as exc:
                    table["status"] = "COUNT fehlgeschlagen (%s)" % exc
                self.count_table_index += 1
                self.db_status = "Zeilen gezählt (%s/%s) …" % (self.count_table_index, len(self.tables))
            if self.then_export:
                self.phase = "exporting"
                self.current_table_index = 0
                self.db_status = "Export läuft …"
                self._export_job()
                return
            self.db_status = "%s Tabellen geladen – bereit zum Export." % len(self.tables)
        finally:
            if not self.db_paused and self.phase != "exporting":
                self.db_running = False

    def _export_from_loaded(self):
        self.phase = "exporting"
        self.current_table_index = 0
        for table in self.tables:
            table["exported"] = 0
            table["offset"] = 0
            table["last_ctid"] = None
            table["writer"] = None
            table["resume_writer"] = False
            table["schema_written"] = False
            table["status"] = "Wartend"
            if self.test_run:
                table["est_rows"] = kf.TEST_PAGE_SIZE
            else:
                table["est_rows"] = table.get("row_count") or table.get("est_rows")
        self._export_job()

    def _export_job(self):
        self.phase = "exporting"
        try:
            while self.current_table_index < len(self.tables):
                if self.db_paused or not self.db_running:
                    return
                table = self.tables[self.current_table_index]
                table["status"] = "Exportiere …"
                try:
                    self._export_one(table)
                except kf.TokenExpired:
                    table["status"] = "Token abgelaufen – Pause"
                    if table["writer"]:
                        table["writer"].close()
                        table["writer"] = None
                        table["resume_writer"] = True
                    self.db_paused = True
                    self.db_status = "Token abgelaufen – neuen cURL einfügen, dann Fortfahren"
                    return
                except Exception as exc:
                    table["status"] = "Fehler: %s" % exc
                    if table["writer"]:
                        table["writer"].close()
                        table["writer"] = None
                self.current_table_index += 1
            if self.db_running and not self.db_paused:
                self.db_status = "Fertig. Dateien in %s" % self.output_dir
        finally:
            if not self.db_paused:
                self.db_running = False
                self.phase = "idle"

    def _export_one(self, table):
        t_name = table["name"]
        schema_name = table.get("schema") or "public"
        pk_cols = table["pk_cols"]
        quoted_table = kf.quote_table(schema_name, t_name)
        safe_file = kf.table_file_stem(schema_name, t_name)
        jsonl_path = os.path.join(self.output_dir, "%s.jsonl" % safe_file)
        final_path = os.path.join(self.output_dir, "beweissicherung_%s.json" % safe_file)
        schema_path = os.path.join(self.output_dir, "schema_%s.json" % safe_file)
        create_sql_path = os.path.join(self.output_dir, "schema_%s.sql" % safe_file)
        if not table.get("schema_written"):
            table["status"] = "Sichere Schema …"
            schema = self._fetch_table_schema(table)
            kf.write_json_file(schema_path, schema)
            with open(create_sql_path, "w", encoding="utf-8") as handle:
                handle.write(schema["create_table_sql"])
                handle.flush()
                os.fsync(handle.fileno())
            table["schema_written"] = True
        if table.get("row_count") == 0:
            kf.write_json_file(final_path, [])
            table["status"] = "Fertig (0 Zeilen, leer)"
            table["exported"] = 0
            table["est_rows"] = 0
            return
        if table["writer"] is None:
            table["writer"] = kf.JsonlWriter(jsonl_path, resume=bool(table.get("resume_writer")))
            table["resume_writer"] = False
        while self.db_running and not self.db_paused:
            limit = self.page_size
            if pk_cols:
                order = ", ".join(kf.quote_ident(c) for c in pk_cols)
                query = "SELECT * FROM %s ORDER BY %s ASC LIMIT %s OFFSET %s;" % (
                    quoted_table,
                    order,
                    limit,
                    table["offset"],
                )
            elif table["last_ctid"]:
                query = (
                    "SELECT ctid, * FROM %s WHERE ctid > '%s'::tid ORDER BY ctid ASC LIMIT %s;"
                    % (quoted_table, table["last_ctid"], limit)
                )
            else:
                query = "SELECT ctid, * FROM %s ORDER BY ctid ASC LIMIT %s;" % (quoted_table, limit)
            response = self.run_sql(query, kind="data")
            records = kf.extract_rows(response)
            if pk_cols is None or not pk_cols:
                records = [kf.strip_ctid(r) for r in records]
            if not records:
                table["writer"].finalize_json_array(final_path)
                table["writer"] = None
                table["status"] = "Fertig (%s Zeilen)" % table["exported"]
                return
            table["writer"].append(records)
            table["exported"] += len(records)
            if pk_cols:
                table["offset"] += limit
            else:
                raw_last = kf.extract_rows(response)[-1]
                table["last_ctid"] = raw_last.get("ctid") if isinstance(raw_last, dict) else None
                if not table["last_ctid"]:
                    table["offset"] += limit
            if table["est_rows"] and table["exported"] > table["est_rows"]:
                table["est_rows"] = table["exported"]
            table["status"] = "Exportiere …"
            self.db_status = "%s.%s – %s Zeilen" % (schema_name, t_name, kf.format_de_int(table["exported"]))
            if self.test_run or len(records) < limit:
                table["writer"].finalize_json_array(final_path)
                table["writer"] = None
                table["status"] = "Fertig (%s Zeilen)" % table["exported"]
                return

    def _fetch_table_schema(self, table):
        schema_name = table.get("schema") or "public"
        name = table["name"]
        packed = kf.extract_rows(self.run_sql(kf.table_schema_sql(schema_name, name), kind="meta"))
        row = packed[0] if packed and isinstance(packed[0], dict) else {}
        columns = kf.json_list(row.get("columns"))
        constraints = kf.json_list(row.get("constraints"))
        indexes = kf.json_list(row.get("indexes"))
        schema = {
            "schema": schema_name,
            "table": name,
            "row_count": table.get("row_count"),
            "primary_key": table.get("pk_cols") or [],
            "columns": kf.normalize_columns(columns),
            "constraints": kf.normalize_constraints(constraints),
            "indexes": kf.normalize_indexes(indexes),
        }
        schema["create_table_sql"] = kf.build_create_table_sql(schema)
        return schema

    def mail_list(self, user, password, host, port):
        if self.mail_running:
            raise RuntimeError("Mail-Job läuft bereits.")
        self.mail_running = True
        self.mail_error = ""
        self.mail_status = "Verbinde mit IMAP …"
        threading.Thread(
            target=self._mail_list_job, args=(user, password, host, port), daemon=True
        ).start()

    def mail_save(self, user, password, host, port, selected=None):
        if self.mail_running:
            raise RuntimeError("Mail-Job läuft bereits.")
        if not self.folders:
            raise RuntimeError("Zuerst Ordner holen.")
        self._apply_mail_selection(selected)
        chosen = [f for f in self.folders if f.get("selected", True)]
        if not chosen:
            raise RuntimeError("Mindestens einen Ordner ankreuzen.")
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.mail_output = os.path.join(os.getcwd(), "email_sicherung_%s" % stamp)
        os.makedirs(self.mail_output, exist_ok=True)
        self.mail_running = True
        self.mail_stop = False
        self.browse = []
        self.browse_page = 0
        self.mail_error = ""
        self.mail_status = "Sichere %s von %s Ordnern …" % (len(chosen), len(self.folders))
        for folder in self.folders:
            folder["exported"] = 0
            if folder.get("selected", True):
                folder["status"] = "Wartend"
            else:
                folder["status"] = "Übersprungen"
        threading.Thread(
            target=self._mail_save_job, args=(user, password, host, port), daemon=True
        ).start()

    def _apply_mail_selection(self, selected):
        if selected is None:
            return
        wanted = set(str(x) for x in selected)
        for folder in self.folders:
            folder["selected"] = folder.get("imap_name") in wanted

    def _mail_connect(self, user, password, host, port):
        context = ssl.create_default_context()
        client = imaplib.IMAP4_SSL(host or kf.IONOS_IMAP_HOST, int(port or kf.IONOS_IMAP_PORT), ssl_context=context)
        try:
            client.sock.settimeout(180)
        except Exception:
            pass
        client.login(user, password)
        try:
            client.enable("UTF8=ACCEPT")
        except Exception:
            pass
        return client

    def _mail_disconnect(self, client):
        if not client:
            return
        try:
            client.logout()
        except Exception:
            try:
                client.shutdown()
            except Exception:
                pass

    def _mail_list_job(self, user, password, host, port):
        client = None
        try:
            client = self._mail_connect(user, password, host, port)
            typ, data = client.list()
            if typ != "OK" or not data:
                raise RuntimeError("IMAP LIST fehlgeschlagen: %s" % typ)
            folders = []
            for line in data:
                parsed = kf.parse_imap_list_line(line)
                if not parsed or parsed["noselect"]:
                    continue
                folders.append(
                    {
                        "imap_name": parsed["imap_name"],
                        "display": parsed["display"],
                        "exported": 0,
                        "total": None,
                        "status": "Wartend",
                        "selected": True,
                    }
                )
            self.folders = folders
            self.mail_status = (
                "%s Ordner gefunden. Fertige Ordner abwählen, dann den Rest sichern." % len(folders)
            )
        except Exception as exc:
            self.mail_error = str(exc)
            self.mail_status = "Fehler"
        finally:
            self._mail_disconnect(client)
            self.mail_running = False

    def _mail_save_job(self, user, password, host, port):
        client = None
        try:
            client = self._mail_connect(user, password, host, port)
            index_path = os.path.join(self.mail_output, "_index.jsonl")
            fetched_since_connect = 0
            for folder in self.folders:
                if self.mail_stop:
                    break
                if not folder.get("selected", True):
                    folder["status"] = "Übersprungen"
                    continue
                fetched_since_connect, client = self._export_folder(
                    client, folder, index_path, fetched_since_connect, user, password, host, port
                )
                if fetched_since_connect >= kf.MAIL_RECONNECT_EVERY:
                    self._mail_disconnect(client)
                    client = self._mail_connect(user, password, host, port)
                    fetched_since_connect = 0
            self.mail_status = "Fertig. Dateien in %s" % self.mail_output
        except Exception as exc:
            self.mail_error = str(exc)
            self.mail_status = "Fehler"
        finally:
            self._mail_disconnect(client)
            self.mail_running = False

    def _reconnect_folder(self, client, imap_name, user, password, host, port):
        self._mail_disconnect(client)
        client = self._mail_connect(user, password, host, port)
        typ, _sel = client.select('"%s"' % imap_name.replace('"', '\\"'), readonly=True)
        if typ != "OK":
            client.select(imap_name, readonly=True)
        return client

    def _export_folder(self, client, folder, index_path, fetched_since_connect, user, password, host, port):
        folder["status"] = "Öffne …"
        imap_name = folder["imap_name"]
        typ, data = client.select('"%s"' % imap_name.replace('"', '\\"'), readonly=True)
        if typ != "OK":
            typ, data = client.select(imap_name, readonly=True)
        if typ != "OK":
            folder["status"] = "Fehler beim Öffnen"
            return fetched_since_connect, client
        try:
            exists = int(data[0]) if data and data[0] is not None else 0
        except (TypeError, ValueError):
            exists = 0
        folder["total"] = exists
        folder["exported"] = 0
        folder_dir = os.path.join(self.mail_output, kf.folder_dir_name(folder["display"], imap_name))
        os.makedirs(folder_dir, exist_ok=True)
        if exists == 0:
            folder["status"] = "Fertig (0 Mails, leer)"
            return fetched_since_connect, client
        folder["status"] = "Sichere …"
        for start in range(1, exists + 1, kf.MAIL_FETCH_BATCH):
            if self.mail_stop:
                return fetched_since_connect, client
            end = min(start + kf.MAIL_FETCH_BATCH - 1, exists)
            if fetched_since_connect >= kf.MAIL_RECONNECT_EVERY:
                client = self._reconnect_folder(client, imap_name, user, password, host, port)
                fetched_since_connect = 0
            seq_set = "%s:%s" % (start, end)
            typ, fetched = client.fetch(seq_set, "(UID RFC822.SIZE BODY.PEEK[])")
            if typ != "OK" or not fetched:
                client = self._reconnect_folder(client, imap_name, user, password, host, port)
                fetched_since_connect = 0
                typ, fetched = client.fetch(seq_set, "(UID RFC822.SIZE BODY.PEEK[])")
                if typ != "OK" or not fetched:
                    continue
            batch_metas = []
            for msg in self._parse_fetch(fetched):
                folder["exported"] += 1
                meta = self._save_message(folder, folder_dir, msg, folder["exported"])
                batch_metas.append(meta)
                fetched_since_connect += 1
            if batch_metas:
                with open(index_path, "a", encoding="utf-8") as handle:
                    for meta in batch_metas:
                        json.dump(meta, handle, ensure_ascii=False)
                        handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                on_last = True
                total_before = len(self.browse)
                if total_before > 0:
                    max_before = (total_before - 1) // kf.MAIL_BROWSE_PAGE
                    on_last = self.browse_page >= max_before
                self.browse.extend(
                    {
                        "folder": m.get("folder") or "",
                        "from": m.get("from") or "",
                        "to": m.get("to") or "",
                        "subject": m.get("subject") or "(kein Betreff)",
                        "date": m.get("date") or "",
                        "size": m.get("size") or 0,
                    }
                    for m in batch_metas
                )
                if on_last and self.browse:
                    self.browse_page = (len(self.browse) - 1) // kf.MAIL_BROWSE_PAGE
            folder["status"] = "Batch %s–%s / %s" % (
                kf.format_de_int(start),
                kf.format_de_int(end),
                kf.format_de_int(exists),
            )
            self.mail_status = "%s – %s / %s Mails" % (
                folder["display"],
                kf.format_de_int(folder["exported"]),
                kf.format_de_int(folder["total"] or 0),
            )
        folder["status"] = "Fertig (%s Mails)" % kf.format_de_int(folder["exported"])
        return fetched_since_connect, client

    def _parse_fetch(self, fetched):
        messages = []
        for part in fetched:
            if isinstance(part, tuple) and len(part) >= 2:
                header = part[0]
                body = part[1]
                uid = None
                size = None
                if isinstance(header, bytes):
                    um = re.search(br"UID\s+(\d+)", header)
                    sm = re.search(br"RFC822.SIZE\s+(\d+)", header)
                    if um:
                        uid = um.group(1).decode("ascii")
                    if sm:
                        size = int(sm.group(1))
                if isinstance(body, bytes) and body:
                    messages.append({"uid": uid, "size": size, "raw": body})
        return messages

    def _save_message(self, folder, folder_dir, msg, seq):
        raw = msg["raw"]
        uid = msg.get("uid") or str(seq)
        parsed = kf.parse_headers_only(raw)
        subject = kf.decode_mime_header(parsed.get("Subject"))
        from_addr = kf.decode_mime_header(parsed.get("From"))
        to_addr = kf.decode_mime_header(parsed.get("To"))
        date_raw = parsed.get("Date") or ""
        date_iso = date_raw
        try:
            date_iso = parsedate_to_datetime(date_raw).isoformat()
        except Exception:
            pass
        batch_dir = os.path.join(folder_dir, "b%04d" % ((seq - 1) // kf.MAIL_FILES_PER_DIR))
        os.makedirs(batch_dir, exist_ok=True)
        filename = "%07d_%s.eml" % (seq, kf.sanitize_filename(uid))
        path = os.path.join(batch_dir, filename)
        with open(path, "wb") as handle:
            handle.write(raw)
            handle.flush()
        rel = os.path.join(os.path.basename(folder_dir), os.path.basename(batch_dir), filename)
        return {
            "folder": folder["display"],
            "imap_folder": folder["imap_name"],
            "uid": uid,
            "from": from_addr,
            "to": to_addr,
            "subject": subject,
            "date": date_iso,
            "size": msg.get("size") or len(raw),
            "file": rel,
        }


STATE = WebState()

PAGE = r"""<!DOCTYPE html>
<html lang="de"><head>
<meta charset="utf-8"/>
<title>Königssturz – Beweissicherung</title>
<style>
:root{--bg:#e8e4dc;--btn:#d4cfc4;--btn2:#c4bfb4;--field:#fff;--fg:#111;--bar:#3d6b99;--hover:#c5def5;}
*{box-sizing:border-box;}
html,body{height:100%;}
body{margin:0;background:var(--bg);color:var(--fg);font:13px/1.35 system-ui,Helvetica,Arial,sans-serif;display:flex;flex-direction:column;}
.top{display:flex;align-items:center;justify-content:space-between;padding:10px 12px 0;}
h1{margin:0;font-size:18px;}
.menu button{font:inherit;background:var(--btn);border:1px solid #b8b3a8;padding:4px 10px;cursor:pointer;}
.tabs{display:flex;gap:4px;padding:8px 12px 0;border-bottom:1px solid #c4bfb4;}
.tabs button{font:inherit;font-weight:700;background:var(--btn);border:1px solid #c4bfb4;border-bottom:none;padding:8px 16px;cursor:pointer;}
.tabs button.on{background:var(--bg);position:relative;top:1px;}
.pane{display:none;flex:1;overflow:auto;padding:10px;}
.pane.on{display:flex;flex-direction:column;min-height:0;}
#va.pane.on{overflow:hidden;}
fieldset{border:1px solid #c4bfb4;margin:0 0 8px;padding:10px;background:var(--bg);}
legend{font-weight:700;padding:0 6px;}
.hint{margin:0 0 8px;color:#333;}
.inline{display:flex;flex-wrap:wrap;align-items:center;gap:8px 10px;margin:6px 0;}
.inline label{font-weight:600;}
input[type=text],input[type=password],textarea{font:inherit;border:1px solid #c4bfb4;background:var(--field);color:var(--fg);padding:4px 6px;}
textarea{width:100%;min-height:88px;font-family:ui-monospace,Menlo,Consolas,monospace;}
.inline input[type=text],.inline input[type=password],.inline input[type=date]{width:180px;}
#vapack .head,#vapack .rowl{grid-template-columns:28px 88px 1.6fr 72px 44px 52px;}
#vapack{flex:1;min-height:72px;max-height:none;overflow:auto;}
#totp{width:70px;}
#mport{width:60px;}
button.act{font:inherit;font-weight:700;background:var(--btn);border:1px solid #b8b3a8;padding:6px 12px;cursor:pointer;}
button.act:disabled{opacity:.55;cursor:not-allowed;}
button.act:not(:disabled):active{background:var(--btn2);}
.grow{flex:1;}
.status{font-style:italic;margin-left:auto;}
.err{color:#8a1c1c;font-weight:700;min-height:1.2em;}
textarea.paused{background:#fff4cc;}
.schemas{display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:8px;}
.schemas label{font-weight:400;min-width:140px;}
.list{flex:1;overflow:auto;border:1px solid #c4bfb4;background:#f7f4ee;min-height:120px;}
.head,.rowl{display:grid;gap:8px;align-items:center;padding:6px 8px;font-size:12px;}
#tables .head,#tables .rowl{grid-template-columns:2.2fr 1.4fr 220px 1.2fr 1.6fr;}
#folders .head,#folders .rowl{grid-template-columns:2.4fr 1.4fr 220px 1.1fr 1.6fr;}
#folderrows label{font-weight:400;display:flex;align-items:center;gap:6px;}
#mails .head,#mails .rowl{grid-template-columns:1.1fr 1.4fr 1.4fr 2fr 1.2fr .7fr;}
.head{font-weight:700;border-bottom:1px solid #c4bfb4;background:#efeae2;position:sticky;top:0;}
.rowl{border-bottom:1px solid #ddd;}
.rowl:hover{background:var(--hover);}
.bar{height:14px;background:var(--btn);border:1px solid #c4bfb4;width:220px;}
.bar i{display:block;height:100%;background:var(--bar);width:0;}
.pager{display:flex;align-items:center;gap:12px;padding-top:8px;}
#mail{min-height:0;}
#db{min-height:0;}
#va{min-height:0;}
#analyze{min-height:0;}
#vacurl{min-height:90px;}
#valist{--va-cols:28px 80px 180px 64px 44px 44px 72px 48px;flex:1;min-height:0;overflow:auto;}
#valist .head,#valist .rowl,#valist div.rowl{grid-template-columns:var(--va-cols)!important;gap:6px;width:max-content;min-width:100%;box-sizing:border-box;}
#valist .head{display:grid;position:sticky;top:0;z-index:3;}
#valist .rowl span.vadatum,#valist .rowl span.vaquelle,#valist .rowl span.vaaudio,#valist .rowl span.vadocs,#valist .rowl span.vastand,#valist .rowl span.vadauer,#valist .vatitle .t{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;}
#valist .vasel{overflow:visible;display:flex;align-items:center;justify-content:center;}
#valist .vatitle{display:flex;align-items:center;gap:6px;min-width:0;overflow:hidden;}
#valist .vatitle .t{flex:1;min-width:0;}
.vaconv{flex:0 0 auto;font-weight:700;color:#3d6b99;white-space:nowrap;font-size:11px;}
#valist .rowl.converting{background:#d5e6f6;}
#valist .head .vacol{position:relative;padding-right:8px;overflow:visible;min-width:0;display:flex;align-items:center;}
#valist .head .vacol .vacol-lab{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;flex:1;}
#valist .head .colres{position:absolute;right:-4px;top:0;bottom:0;width:9px;cursor:col-resize;z-index:5;background:transparent;}
#valist .head .colres::before{content:"";position:absolute;left:50%;top:3px;bottom:3px;width:1px;background:#8a847a;transform:translateX(-50%);}
#valist .head .colres:hover::before,#valist .head .colres.drag::before{width:2px;background:#3d6b99;}
#valist .head .colres:hover,#valist .head .colres.drag{background:rgba(61,107,153,.14);}
#valist .rowl > span:not(:last-child),#valist .head .vacol:not(:last-child){box-shadow:inset -1px 0 0 #d9d3c8;}
#valist div.rowl{cursor:pointer;border:none;background:transparent;text-align:left;font:inherit;color:inherit;display:grid;align-items:center;padding:6px 8px;font-size:12px;border-bottom:1px solid #ddd;}
#valist div.rowl:hover{background:var(--hover);}
#valist div.rowl.on{background:var(--hover);font-weight:700;}
#vabody h2{margin:0 0 4px;font-size:16px;}
#vabody h3{margin:14px 0 6px;font-size:13px;}
#va .agrid .list{flex:1;min-height:0;}
#va fieldset.bottom{flex:1 1 0;min-height:0;overflow:hidden;display:flex;flex-direction:column;}
#va .split{flex:1;min-height:0;overflow:hidden;}
#va .tlist{flex:0 0 760px;width:760px;min-width:280px;max-width:75%;}
#vapack{--pack-cols:28px 88px 180px 64px 44px 44px 48px;flex:1;min-height:72px;max-height:none;overflow:auto;}
#vapack .head,#vapack .rowl,#vapack label.rowl{grid-template-columns:var(--pack-cols)!important;gap:6px;width:max-content;min-width:100%;box-sizing:border-box;}
#vapack .head{display:grid;position:sticky;top:0;z-index:3;}
#vapack .head .vacol{position:relative;padding-right:8px;overflow:visible;min-width:0;display:flex;align-items:center;}
#vapack .head .vacol .vacol-lab{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;flex:1;}
#vapack .head .colres{position:absolute;right:-4px;top:0;bottom:0;width:9px;cursor:col-resize;z-index:5;background:transparent;}
#vapack .head .colres::before{content:"";position:absolute;left:50%;top:3px;bottom:3px;width:1px;background:#8a847a;transform:translateX(-50%);}
#vapack .head .colres:hover::before,#vapack .head .colres.drag::before{width:2px;background:#3d6b99;}
#vapack .head .colres:hover,#vapack .head .colres.drag{background:rgba(61,107,153,.14);}
#vapack .rowl > span:not(:last-child),#vapack .head .vacol:not(:last-child){box-shadow:inset -1px 0 0 #d9d3c8;}
#vapack label.rowl{display:grid;align-items:center;padding:6px 8px;font-size:12px;border-bottom:1px solid #ddd;cursor:pointer;}
#vapack .rowl span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;}
#vapackbox{flex:0 0 auto;max-height:min(260px,34vh);overflow:hidden;display:flex;flex-direction:column;min-height:0;}
.vaplayer{flex:0 0 auto;background:var(--bg);border-top:1px solid #c4bfb4;padding:2px 0 0;}
.vaplayer audio{width:100%;height:32px;display:block;}
.vaplayer .err{min-height:0;margin:2px 0 0;}
.vaplayer .err:empty{display:none;}
.job{display:none;margin:8px 0;max-width:520px;}
.job.on{display:block;}
.job .bar{height:12px;width:100%;background:var(--btn);border:1px solid #c4bfb4;}
.job .bar i{display:block;height:100%;background:var(--bar);width:0;}
.job .meta{margin-top:4px;font-style:italic;}
#vatx .tx{display:grid;grid-template-columns:64px 130px 1fr;gap:6px 10px;font-size:12px;padding:4px 0;border-bottom:1px solid #eee;cursor:pointer;}
#vatx .tx:hover{background:var(--hover);}
.bottom{flex:1;display:flex;flex-direction:column;min-height:180px;}
.split{display:flex;flex:1;min-height:0;gap:0;}
.tlist{flex:0 0 280px;width:280px;min-width:180px;max-width:70%;display:flex;flex-direction:column;}
.tlist .list{flex:1;min-height:0;}
#tlistbox .head,#tlistbox .rowl{grid-template-columns:1fr auto;}
.tlist button.rowl{cursor:pointer;border:none;background:transparent;width:100%;text-align:left;font:inherit;color:inherit;}
#tlistbox .rowl span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.tlist .rowl.on{background:var(--hover);font-weight:700;}
.splitbar{flex:0 0 6px;width:6px;cursor:col-resize;background:#c4bfb4;align-self:stretch;margin:0 4px;}
.splitbar:hover,.splitbar.drag{background:var(--bar);}
.agrid{flex:1;display:flex;flex-direction:column;min-width:0;min-height:0;}
.agrid fieldset{min-height:0;}
.filters{display:flex;flex-direction:column;gap:4px;margin:4px 0;}
.filterrow{display:flex;flex-wrap:wrap;align-items:center;gap:6px;}
.filterrow select,.filterrow input{font:inherit;border:1px solid #c4bfb4;background:var(--field);padding:3px 6px;}
.filterrow input{width:160px;}
.filterrow .fsep{font-weight:400;color:#5c584f;}
.gridwrap{flex:1;overflow:auto;border:1px solid #c4bfb4;background:#f7f4ee;min-height:140px;}
table.data{border-collapse:collapse;width:max-content;min-width:100%;font-size:12px;}
table.data th,table.data td{border-bottom:1px solid #ddd;padding:4px 8px;white-space:nowrap;max-width:280px;overflow:hidden;text-overflow:ellipsis;vertical-align:top;}
table.data th{position:sticky;top:0;background:#efeae2;text-align:left;cursor:pointer;font-weight:700;}
table.data th.sort{text-decoration:underline;}
table.data tr.totals td{font-weight:700;background:#efeae2;border-top:1px solid #c4bfb4;}
table.data td.null{color:#888;}
table.data td.rich{cursor:pointer;text-decoration:underline dotted;}
table.data td.rich:hover{background:#fff;}
#asql{min-height:110px;}
.sqlbox{display:none;margin-top:6px;}
.sqlbox.on{display:block;}
#afolder{min-width:280px;}
#tsearch{width:100%;}
.pop{display:none;position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:40;align-items:center;justify-content:center;padding:20px;}
.pop.on{display:flex;}
.popbox{background:var(--bg);border:1px solid #c4bfb4;width:min(920px,94vw);max-height:86vh;display:flex;flex-direction:column;padding:10px;}
.popbox pre{flex:1;overflow:auto;background:#fff;border:1px solid #c4bfb4;padding:8px;white-space:pre-wrap;word-break:break-word;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;margin:8px 0 0;min-height:120px;}
.popframe{display:none;flex:1;min-height:220px;border:1px solid #c4bfb4;background:#fff;margin-top:8px;}
.popframe.on{display:block;}
#poppre.off{display:none;}
#gsearch{width:240px;}
#acompare,#asaved{min-width:200px;}
.globbox{display:none;max-height:200px;margin-top:8px;}
.globbox.on{display:block;}
.colpick{display:none;flex-wrap:wrap;gap:4px 14px;margin:6px 0;padding:8px;border:1px solid #c4bfb4;background:#f7f4ee;}
.colpick.on{display:flex;}
.colpick label{font-weight:400;min-width:140px;}
table.data td.chg-neu{background:#dcecdc;}
table.data td.chg-del{background:#f3d6d6;}
table.data td.chg-chg{background:#f3e6c8;}
table.data td.chg-field{box-shadow:inset 0 -2px 0 #9a7b2f;font-weight:700;}
table.data td.jump{cursor:pointer;text-decoration:underline dotted;}
.chartbox{display:none;border:1px solid #c4bfb4;background:#f7f4ee;padding:8px 10px;margin:6px 0;min-height:120px;}
.chartbox.on{display:block;}
#achartcv{width:100%;height:140px;}
#anote{min-height:52px;width:100%;font-family:inherit;}
#adatefrom,#adateto{width:148px;}
#adatecol,#adategrain{min-width:120px;}
#poplinks{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px;}
</style></head><body>
<div class="top">
  <h1>Königssturz – Beweissicherung</h1>
  <div class="menu"><button class="act" onclick="post('/api/export',{curl:gv('curl'),test_run:true})">Datei: Test-Run</button></div>
</div>
<div class="tabs">
  <button id="bdb" class="on" onclick="tab('db')">Supabase</button>
  <button id="bmail" onclick="tab('mail')">E-Mail (IONOS)</button>
  <button id="bva" onclick="tab('va')">Vereinsarchiv</button>
  <button id="banalyze" onclick="tab('analyze')">Auswertung</button>
</div>

<section class="pane on" id="db">
  <fieldset>
    <legend> 1. cURL einfügen </legend>
    <p class="hint">cURL aus den DevTools einfügen. Danach optional E-Mail/Passwort/TOTP – dann wird der Token automatisch erneuert.</p>
    <textarea id="curl" placeholder="cURL hier einfügen"></textarea>
    <div class="inline">
      <label>E-Mail</label><input id="email" type="text"/>
      <label>Passwort</label><input id="password" type="password"/>
      <label>TOTP</label><input id="totp" type="text"/>
      <button class="act" onclick="post('/api/login',{curl:gv('curl'),email:gv('email'),password:gv('password'),totp:gv('totp')})">Anmelden</button>
      <span class="status" id="loginstatus">Kein Auto-Refresh</span>
    </div>
    <div class="inline">
      <button class="act" id="btnFetch" onclick="post('/api/schemas',{curl:gv('curl')})">Hole Tables</button>
      <button class="act" id="btnExport" onclick="post('/api/export',{curl:gv('curl'),test_run:false})">Der König ist tot, lang lebe der König</button>
      <button class="act" id="btnResume" onclick="post('/api/resume',{curl:gv('curl')})" disabled>Fortfahren</button>
      <span class="status" id="dbstatus">Status: Bereit</span>
    </div>
    <div class="err" id="dberr"></div>
  </fieldset>
  <fieldset>
    <legend> 2. Schemas auswählen </legend>
    <div class="inline">
      <button class="act" onclick="checkSchemas('empfohlen')">Empfohlen</button>
      <button class="act" onclick="checkSchemas('alle')">Alle</button>
      <button class="act" onclick="checkSchemas('keine')">Keine</button>
      <button class="act" id="btnLoad" onclick="loadTables()" disabled>Tabellen laden</button>
      <span class="status" id="schemahint">Zuerst „Hole Tables“ – dann Schemas ankreuzen.</span>
    </div>
    <div class="schemas" id="schemas"></div>
  </fieldset>
  <fieldset class="bottom">
    <legend> 3. Tabellen </legend>
    <div class="list" id="tables">
      <div class="head"><span>Tabelle</span><span>Primary Key</span><span>Fortschritt</span><span>Zeilen</span><span>Status</span></div>
      <div id="tablerows"></div>
    </div>
  </fieldset>
</section>

<section class="pane" id="mail">
  <fieldset>
    <legend> 1. IONOS IMAP-Login </legend>
    <p class="hint">Große Postfächer werden in 25er-Batches geholt. Nach Timeout: Ordner neu holen, fertige Ordner abwählen, nur den Rest sichern. Jeder Lauf schreibt in einen neuen Ordner.</p>
    <div class="inline">
      <label>E-Mail</label><input id="muser" type="text"/>
      <label>Passwort</label><input id="mpass" type="password"/>
      <label>Server</label><input id="mhost" type="text" value="imap.ionos.de"/>
      <label>Port</label><input id="mport" type="text" value="993"/>
    </div>
    <div class="inline">
      <button class="act" id="btnList" onclick="post('/api/mail/list',mailCreds())">Ordner holen</button>
      <button class="act" id="btnSave" onclick="saveMail()" disabled>Ordner sichern</button>
      <span class="status" id="mailstatus">Status: Bereit</span>
    </div>
    <div class="err" id="mailerr"></div>
  </fieldset>
  <fieldset>
    <legend> 2. Ordner auswählen </legend>
    <div class="inline">
      <button class="act" onclick="checkFolders(true)">Alle</button>
      <button class="act" onclick="checkFolders(false)">Keine</button>
      <span class="status" id="folderhint">Zuerst „Ordner holen“, dann ankreuzen.</span>
    </div>
    <div class="list" id="folders" style="max-height:280px;">
      <div class="head"><span>Ordner</span><span>IMAP</span><span>Fortschritt</span><span>Mails</span><span>Status</span></div>
      <div id="folderrows"></div>
    </div>
  </fieldset>
  <fieldset class="bottom">
    <legend> 3. Gesicherte Mails (nur Übersicht, nicht öffenbar) </legend>
    <div class="list" id="mails">
      <div class="head"><span>Ordner</span><span>Von</span><span>An</span><span>Betreff</span><span>Datum</span><span>Größe</span></div>
      <div id="mailrows"></div>
    </div>
    <div class="pager">
      <button class="act" onclick="post('/api/mail/page',{delta:-1})">Zurück</button>
      <span id="browselabel">Keine Mails</span>
      <button class="act" onclick="post('/api/mail/page',{delta:1})">Weiter</button>
    </div>
  </fieldset>
</section>

<section class="pane" id="va">
  <fieldset>
    <legend> 1. Token (cURL) </legend>
    <p class="hint">Login geht nur per Magic-Link. In den DevTools einen Request von vereinsarchiv-vorstand.pages.dev als cURL kopieren und hier einfügen. Der Token steht unter Authorization. Nicht den Königssturz-cURL verwenden.</p>
    <textarea id="vacurl" placeholder="cURL hier einfügen"></textarea>
    <div class="inline">
      <button class="act" onclick="post('/api/va/curl',{curl:gv('vacurl')})">Token übernehmen</button>
      <span class="status" id="vatoken">Kein Token</span>
    </div>
  </fieldset>
  <fieldset>
    <legend> 2. Stammtische sichern </legend>
    <p class="hint">Zuerst Abgleich: Online-Liste holen und mit dem lokalen Ordner vergleichen. Download lädt nur, was fehlt (JSON, Audio, Folien).</p>
    <div class="inline">
      <label><input type="checkbox" id="vakindpublic" checked> Öffentlich</label>
      <label><input type="checkbox" id="vakindintern" checked> Intern</label>
      <button class="act" id="btnVaPrev" onclick="vaPreview()">Abgleich</button>
      <button class="act" id="btnVaDl" onclick="vaDownload()">Download</button>
      <button class="act" id="btnVaStop" onclick="post('/api/va/stop',{})">Stop</button>
      <span class="status" id="vastatus">Status: Bereit</span>
    </div>
    <div class="err" id="vaerr"></div>
  </fieldset>
  <fieldset class="bottom">
    <legend> 3. Lokal browsen </legend>
    <div class="inline">
      <label for="vafilter">Quelle</label>
      <select id="vafilter" onchange="renderVaList()">
        <option value="all">Alle</option>
        <option value="public">Öffentlich</option>
        <option value="intern">Intern</option>
      </select>
      <label for="vasearch">Suche</label>
      <input id="vasearch" type="text" oninput="renderVaList()" placeholder="Titel, Thema, Datum"/>
      <button class="act" type="button" onclick="vaSelAllVisible(true)">Alle</button>
      <button class="act" type="button" onclick="vaSelAllVisible(false)">Keine</button>
      <button class="act" type="button" id="btnVaDel" onclick="deleteVaSelected()">Auswahl löschen</button>
      <span class="status" id="vahint">Noch keine Stammtische lokal</span>
    </div>
    <div class="split">
      <div class="tlist" id="valistbox">
        <div class="list" id="valist">
          <div class="head" id="vahead"></div>
          <div id="varows"></div>
        </div>
      </div>
      <div class="splitbar" id="vasplitbar"></div>
      <div class="agrid">
        <div class="list" id="vabody"></div>
        <div class="vaplayer" id="vaplayer" style="display:none">
          <audio id="vaaudio" controls></audio>
          <div class="job" id="vaaudiojob"><div class="bar"><i id="vaaudiobar"></i></div><div class="meta" id="vaaudiometa"></div></div>
          <p class="err" id="vaaudioerr"></p>
        </div>
      </div>
    </div>
  </fieldset>
  <fieldset id="vapackbox">
    <legend> 4. Paket für Reader </legend>
    <p class="hint">Nur was lokal schon geladen ist. Öffentlich und intern kommen zusammen in eine Zip, wenn beides angehakt ist. Datum und Häkchen schränken ein. Die Zip ist unverschlüsselt – privat weitergeben.</p>
    <div class="inline">
      <label><input type="checkbox" id="vapackpublic" checked> Öffentlich</label>
      <label><input type="checkbox" id="vapackintern" checked> Intern</label>
      <label for="vapackfrom">von</label>
      <input id="vapackfrom" type="date"/>
      <label for="vapackto">bis</label>
      <input id="vapackto" type="date"/>
      <button class="act" type="button" onclick="vaPackCheck(true)">Alle</button>
      <button class="act" type="button" onclick="vaPackCheck(false)">Keine</button>
      <button class="act" type="button" id="btnPack" onclick="packVa()">Paket erzeugen</button>
      <button class="act" type="button" id="btnPackStop" onclick="stopPack()" disabled>Abbrechen</button>
      <span class="status" id="vapackhint">Keine lokale Auswahl</span>
    </div>
    <div class="job" id="packjob"><div class="bar"><i id="packbar"></i></div><div class="meta" id="packmeta"></div></div>
    <div class="list" id="vapack">
      <div class="head" id="vapackhead"></div>
      <div id="vapackrows"></div>
    </div>
    <div class="err" id="vapackerr"></div>
  </fieldset>
</section>

<section class="pane" id="analyze">
  <fieldset>
    <legend> 1. Backup-Ordner </legend>
    <p class="hint">Sicherung wählen (Standard: der neueste Ordner). Tabelle links anklicken. „Überall“ sucht in allen Tabellen. „Ordner vergleichen“ stellt die Tabellenlisten zweier Sicherungen gegenüber, „Tabelle vergleichen“ die Zeilen einer Tabelle.</p>
    <div class="inline">
      <label for="afolder">Ordner</label>
      <select id="afolder" onchange="onBackupChange()"></select>
      <button class="act" onclick="loadBackups(true)">Aktualisieren</button>
      <span class="status" id="astatus">Kein Ordner geladen</span>
    </div>
    <div class="inline">
      <label for="gsearch">Überall</label>
      <input id="gsearch" type="text" placeholder="E-Mail, ID, Text …" onkeydown="if(event.key==='Enter')searchAllTables()"/>
      <button class="act" onclick="searchAllTables()">In allen Tabellen</button>
      <label for="acompare">Vergleich mit</label>
      <select id="acompare"></select>
      <button class="act" onclick="runCompare(0)">Tabelle vergleichen</button>
      <button class="act" onclick="runFolderCompare()">Ordner vergleichen</button>
    </div>
    <div class="list globbox" id="globbox">
      <div class="head"><span>Tabelle mit Treffer</span><span>Treffer</span></div>
      <div id="globrows"></div>
    </div>
    <div class="err" id="aerr"></div>
  </fieldset>
  <div class="split">
    <fieldset class="tlist" id="tlistbox">
      <legend> 2. Tabellen </legend>
      <input id="tsearch" type="text" placeholder="Tabelle suchen …" oninput="renderTableList()"/>
      <div class="list" id="tlist">
        <div class="head"><span>Tabelle</span><span>Zeilen</span></div>
        <div id="trows"></div>
      </div>
    </fieldset>
    <div class="splitbar" id="splitbar" title="Ziehen: Breite der Tabellenliste"></div>
    <fieldset class="agrid bottom">
      <legend id="agridlegend"> 3. Daten </legend>
      <div class="inline">
        <label for="asearch">Suche</label>
        <input id="asearch" type="text" placeholder="in allen Spalten …" style="width:220px" onkeydown="if(event.key==='Enter')applyAnalyze(0)"/>
        <button class="act" onclick="applyAnalyze(0)">Anwenden</button>
        <button class="act" onclick="resetAnalyze()">Zurücksetzen</button>
        <button class="act" onclick="toggleSql()">SQL anzeigen/bearbeiten</button>
        <button class="act" onclick="exportAnalyze('json')">JSON</button>
        <button class="act" onclick="exportAnalyze('csv')">CSV</button>
        <button class="act" onclick="exportAnalyze('png')" title="Nur wenn die aktuelle Seite komplett auf ein Bild passt (max. 40 Zeilen, 16 Spalten)">PNG</button>
        <button class="act" onclick="exportAnalyze('befund')" title="Markdown mit Notiz, Abfrage und aktueller Tabelle, plus Akte">Befund</button>
        <button class="act" onclick="addToAkte()" title="Aktuelle Ansicht in die Befund-Akte legen">Zur Akte</button>
        <span class="status" id="aktehint">Akte leer</span>
        <button class="act" onclick="toggleCols()">Spalten</button>
        <label for="asaved">Abfrage</label>
        <select id="asaved" onchange="onSavedPick()"></select>
        <button class="act" onclick="saveCurrentQuery()">Speichern</button>
        <button class="act" onclick="deleteSavedQuery()">Löschen</button>
        <span class="status" id="aresult">Keine Tabelle gewählt</span>
      </div>
      <div>
        <strong>Nur Zeilen wo</strong>
        <div class="filters" id="afilters"></div>
        <button class="act" type="button" onclick="addFilter()">+ Bedingung</button>
        <span class="hint" style="margin-left:8px">Bei Datum/Zeit: „zwischen“ mit Monat (z. B. 2026-01 bis 2026-03). Taggenau: größer gleich / kleiner gleich.</span>
      </div>
      <div class="inline">
        <label for="adatecol">Zeitraum</label>
        <select id="adatecol" title="Welche Datumsspalte gefiltert wird"></select>
        <select id="adategrain" onchange="syncDateGrain()" title="Taggenau oder ganzer Monat">
          <option value="day">Tag</option>
          <option value="month">Monat</option>
        </select>
        <label for="adatefrom">von</label>
        <input id="adatefrom" type="date" onkeydown="if(event.key==='Enter')applyAnalyze(0)"/>
        <label for="adateto">bis</label>
        <input id="adateto" type="date" onkeydown="if(event.key==='Enter')applyAnalyze(0)"/>
        <button class="act" type="button" onclick="sumInPeriod()" title="Anzahl oder Summe je Monat der gewählten Datumsspalte">Summe</button>
        <span class="hint" style="margin:0">einschließlich; Summe nutzt total_amount falls vorhanden</span>
      </div>
      <div>
        <strong>Notiz zum Befund</strong>
        <textarea id="anote" placeholder="Was hast du gefunden? Steht oben in PNG und im Befund-Export."></textarea>
      </div>
      <div class="inline" style="align-items:flex-start;">
        <div>
          <strong>Gruppiere nach</strong>
          <div class="filters" id="agroups"></div>
          <button class="act" type="button" onclick="addGroup()">+ Spalte</button>
        </div>
        <div>
          <strong>Berechne</strong>
          <div class="filters" id="aaggs"></div>
          <button class="act" type="button" onclick="addAgg()">+ Summe / Anzahl</button>
        </div>
      </div>
      <div class="sqlbox" id="sqlbox">
        <div class="inline">
          <span class="hint" style="margin:0;">Wer den Text ändert, führt genau dieses SELECT aus.</span>
          <button class="act" type="button" onclick="sqlFromBuilder()">Aus Baukasten erzeugen</button>
        </div>
        <textarea id="asql" oninput="sqlDirty=true" placeholder="SELECT …"></textarea>
      </div>
      <div class="colpick" id="colpick"></div>
      <div class="inline" id="cmpkinds" style="display:none">
        <strong>Im Vergleich</strong>
        <label><input type="checkbox" class="ckind" value="neu" checked onchange="onCompareKind()"/> neu</label>
        <label><input type="checkbox" class="ckind" value="gelöscht" checked onchange="onCompareKind()"/> gelöscht</label>
        <label><input type="checkbox" class="ckind" value="geändert" checked onchange="onCompareKind()"/> geändert</label>
        <label id="ckindgleichlab"><input type="checkbox" class="ckind" value="gleich" onchange="onCompareKind()"/> gleich</label>
      </div>
      <div class="chartbox" id="achart"><canvas id="achartcv"></canvas></div>
      <div class="gridwrap" id="agrid">
        <table class="data" id="atable"><thead></thead><tbody></tbody></table>
      </div>
      <div class="pager">
        <button class="act" onclick="analyzePage(-1)">Zurück</button>
        <span id="apageln">Keine Daten</span>
        <button class="act" onclick="analyzePage(1)">Weiter</button>
      </div>
    </fieldset>
  </div>
</section>
<div class="pop" id="cellpop" onclick="if(event.target===this)closeCell()">
  <div class="popbox">
    <div class="inline">
      <strong id="poptitle">Zelle</strong>
      <span class="status" id="popkind"></span>
      <button class="act" type="button" id="pophtmlbtn" onclick="toggleHtmlPreview()" style="display:none">HTML-Vorschau</button>
      <button class="act" type="button" onclick="copyCell()">Kopieren</button>
      <button class="act" type="button" id="popfilterbtn" onclick="filterOpenCell()" style="display:none">Als Filter</button>
      <button class="act" type="button" onclick="closeCell()">Schließen</button>
    </div>
    <div id="poplinks"></div>
    <pre id="poppre"></pre>
    <iframe id="popframe" class="popframe" sandbox title="HTML-Vorschau"></iframe>
  </div>
</div>
<script>
function tab(name){
  document.getElementById('db').className='pane'+(name==='db'?' on':'');
  document.getElementById('mail').className='pane'+(name==='mail'?' on':'');
  document.getElementById('va').className='pane'+(name==='va'?' on':'');
  document.getElementById('analyze').className='pane'+(name==='analyze'?' on':'');
  document.getElementById('bdb').className=name==='db'?'on':'';
  document.getElementById('bmail').className=name==='mail'?'on':'';
  document.getElementById('bva').className=name==='va'?'on':'';
  document.getElementById('banalyze').className=name==='analyze'?'on':'';
  if(name==='analyze') loadBackups(false);
  if(name==='va') loadVaLocal(false);
}
function gv(id){return document.getElementById(id).value;}
function mailCreds(){return {user:gv('muser'),password:gv('mpass'),host:gv('mhost'),port:gv('mport')};}
async function post(url, body){
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
  const d=await r.json().catch(function(){return {};});
  if(!r.ok){alert(d.detail||d.error||'Fehler');}
  tick();
}
function checkSchemas(mode){
  document.querySelectorAll('#schemas input').forEach(function(box){
    if(mode==='alle') box.checked=true;
    else if(mode==='keine') box.checked=false;
    else box.checked=box.getAttribute('data-rec')==='1';
  });
}
function loadTables(){
  const selected=[].slice.call(document.querySelectorAll('#schemas input:checked')).map(function(x){return x.value;});
  post('/api/tables',{selected:selected});
}
function checkFolders(on){
  document.querySelectorAll('#folderrows .fsel').forEach(function(box){ box.checked=!!on; });
}
function selectedFolders(){
  return [].slice.call(document.querySelectorAll('#folderrows .fsel:checked')).map(function(x){return x.value;});
}
function saveMail(){
  const selected=selectedFolders();
  if(!selected.length){ alert('Mindestens einen Ordner ankreuzen.'); return; }
  const body=mailCreds();
  body.selected=selected;
  post('/api/mail/save', body);
}
function esc(s){return (s||'').toString().replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}
function escAttr(s){return esc(s).replace(/"/g,'&quot;');}
function sizeTxt(n){
  n=n||0;
  if(n>=1048576) return (n/1048576).toFixed(1)+' MB';
  if(n>=1024) return (n/1024).toFixed(1)+' KB';
  return n+' B';
}
function fmt(n){return (n===null||n===undefined||n==='?')?'?':String(n);}
let lastSchemaNames='';
let lastFolderNames='';
let rec={};
let lastVaRev=-1;
let vaItems=[];
let vaOpen=null;
let vaAbort=null;
let vaSelected={};
let vaColsFitted=false;
let vaColWidths=null;
const VA_COL_LABELS=['','Datum','Titel','Quelle','Audio','Docs','Stand','Dauer'];
const VA_COL_MIN=[28,72,140,58,44,44,52,48];
const VA_COL_MAX=[40,110,240,90,56,56,80,64];
let vaConv={running:false,kind:'',id:'',pct:0,file_pct:0,title:''};
function vaKey(it){ return (it.kind||'')+'/'+(it.id||''); }
function vaYes(v){ return v?'ja':'–'; }
function vaConvTitle(st){
  const raw=(st&&st.status)||'';
  const m=raw.match(/:\s*(.+)$/);
  return m?m[1].trim():'';
}
function vaConvMatch(it){
  if(!vaConv.running) return false;
  if(vaConv.id&&it.id===vaConv.id) return !vaConv.kind||vaConv.kind===it.kind;
  if(vaConv.title){
    const t=(it.titel||it.id||'').toLowerCase();
    if(t&&vaConv.title.toLowerCase().indexOf(t)>=0) return true;
    if(t&&t.indexOf(vaConv.title.toLowerCase())>=0) return true;
  }
  return false;
}
function vaConvPct(){
  return Math.max(0, Math.min(99, vaConv.file_pct||vaConv.pct||0));
}
function vaRing(it){
  if(!vaConvMatch(it)) return '';
  return '<span class="vaconv">umwandeln '+vaConvPct()+'%</span>';
}
function vaApplyCols(){
  const list=document.getElementById('valist');
  if(!list) return;
  if(!vaColWidths||vaColWidths.length!==VA_COL_MIN.length) vaColWidths=VA_COL_MIN.slice();
  const parts=vaColWidths.map(function(w,i){
    const lo=VA_COL_MIN[i];
    const hi=VA_COL_MAX[i]||Math.max(lo*4, 320);
    const n=Math.max(lo, Math.min(hi, w||lo));
    vaColWidths[i]=n;
    return n+'px';
  });
  list.style.setProperty('--va-cols', parts.join(' '));
}
function vaRenderHead(){
  const head=document.getElementById('vahead');
  if(!head) return;
  head.innerHTML=VA_COL_LABELS.map(function(label,i){
    const grip=i<VA_COL_LABELS.length-1?'<i class="colres" data-col="'+i+'" title="Breite ziehen"></i>':'';
    if(i===0) return '<span class="vacol vasel"><input type="checkbox" id="vaSelAll" title="Sichtbare auswählen">'+grip+'</span>';
    return '<span class="vacol"><span class="vacol-lab">'+esc(label)+'</span>'+grip+'</span>';
  }).join('');
  const all=document.getElementById('vaSelAll');
  if(all){
    all.onchange=function(){ vaSelAllVisible(!!all.checked); };
  }
  head.querySelectorAll('.colres').forEach(function(grip){
    grip.onmousedown=function(e){
      e.preventDefault();
      e.stopPropagation();
      const idx=parseInt(grip.getAttribute('data-col'),10);
      if(isNaN(idx)) return;
      if(!vaColWidths||vaColWidths.length!==VA_COL_MIN.length) vaFitCols(true);
      const startX=e.clientX;
      const startW=vaColWidths[idx];
      grip.classList.add('drag');
      function move(ev){
        const hi=VA_COL_MAX[idx]||Math.max(VA_COL_MIN[idx]*4, 480);
        vaColWidths[idx]=Math.max(VA_COL_MIN[idx], Math.min(hi, startW+(ev.clientX-startX)));
        vaApplyCols();
      }
      function up(){
        grip.classList.remove('drag');
        document.removeEventListener('mousemove', move);
        document.removeEventListener('mouseup', up);
      }
      document.addEventListener('mousemove', move);
      document.addEventListener('mouseup', up);
    };
  });
}
function vaFitCols(force){
  if(vaColsFitted && !force) return;
  const list=document.getElementById('valist');
  if(!list) return;
  const widths=VA_COL_MIN.slice();
  const canvas=document.createElement('canvas');
  const ctx=canvas.getContext('2d');
  function grow(i, text, bold){
    const hi=VA_COL_MAX[i]||320;
    if(!ctx){ widths[i]=Math.max(widths[i], Math.min(hi, 12*((text||'').length)+18)); return; }
    ctx.font=(bold?'700 ':'')+'12px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
    widths[i]=Math.max(widths[i], Math.min(hi, Math.ceil(ctx.measureText(text||'').width)+18));
  }
  VA_COL_LABELS.forEach(function(label,i){ if(i) grow(i, label, true); });
  const filter=document.getElementById('vafilter').value;
  const q=(document.getElementById('vasearch').value||'').toLowerCase().trim();
  vaItems.filter(function(it){
    if(filter!=='all' && it.kind!==filter) return false;
    if(!q) return true;
    const hay=((it.titel||'')+' '+(it.datum||'')+' '+(it.ort||'')+' '+((it.themen||[]).join(' '))).toLowerCase();
    return hay.indexOf(q)>=0;
  }).slice(0,200).forEach(function(it){
    grow(1, it.datum||'');
    grow(2, (it.titel||it.id||'')+(vaConvMatch(it)?' umwandeln 99%':''));
    grow(3, vaKindLabel(it.kind));
    grow(4, vaYes(it.has_audio));
    grow(5, vaYes(it.has_folien));
    grow(6, vaStand(it));
    grow(7, vaDur(it.dauer_sek));
  });
  vaColWidths=widths;
  vaColsFitted=true;
  vaApplyCols();
}
function applyVaConv(st){
  st=st||{};
  const next={
    running:!!st.running,
    kind:st.kind||'',
    id:st.id||st.item_id||'',
    pct:st.pct||0,
    file_pct:st.file_pct||0,
    title:vaConvTitle(st)
  };
  const moved=vaConv.running!==next.running||vaConv.kind!==next.kind||vaConv.id!==next.id||vaConv.title!==next.title;
  vaConv=next;
  if(moved){
    renderVaList();
    const row=document.querySelector('#varows .rowl.converting');
    if(row&&row.scrollIntoView) row.scrollIntoView({block:'nearest'});
    return;
  }
  const lab=document.querySelector('#varows .rowl.converting .vaconv');
  if(lab) lab.textContent='umwandeln '+vaConvPct()+'%';
}
async function tick(){
  const s=await (await fetch('/api/state')).json();
  document.getElementById('loginstatus').textContent=s.login_status||'Kein Auto-Refresh';
  document.getElementById('dbstatus').textContent='Status: '+(s.db_status||'Bereit')+(s.output_dir?' · '+s.output_dir:'');
  document.getElementById('dberr').textContent=s.db_error||'';
  document.getElementById('mailstatus').textContent='Status: '+(s.mail_status||'Bereit')+(s.mail_output?' · '+s.mail_output:'');
  document.getElementById('mailerr').textContent=s.mail_error||'';
  document.getElementById('schemahint').textContent=s.schema_hint||'';
  document.getElementById('browselabel').textContent=s.browse_label||'Keine Mails';
  document.getElementById('curl').className=s.db_paused?'paused':'';
  const busy=!!s.db_running && !s.db_paused;
  document.getElementById('btnFetch').disabled=busy;
  document.getElementById('btnExport').disabled=busy;
  document.getElementById('btnLoad').disabled=busy||!(s.schemas&&s.schemas.length);
  document.getElementById('btnResume').disabled=!s.db_paused;
  document.getElementById('btnList').disabled=!!s.mail_running;
  document.getElementById('btnSave').disabled=!!s.mail_running||!(s.folders&&s.folders.length);
  const names=(s.schemas||[]).join('\0');
  if(names!==lastSchemaNames){
    lastSchemaNames=names;
    rec={};
    document.getElementById('schemas').innerHTML=(s.schemas||[]).map(function(n){
      const on=s.selected.indexOf(n)>=0;
      rec[n]=on?1:0;
      return '<label><input type="checkbox" value="'+esc(n)+'" data-rec="'+(on?1:0)+'"'+(on?' checked':'')+'> '+esc(n)+'</label>';
    }).join('');
  }
  document.getElementById('tablerows').innerHTML=(s.tables||[]).map(function(t){
    return '<div class="rowl"><span>'+esc((t.schema||'')+'.'+(t.name||''))+'</span><span>'+esc(t.strategy)+'</span><span><div class="bar"><i style="width:'+(t.progress||0)+'%"></i></div></span><span>'+(t.exported||0)+' / '+fmt(t.est_rows)+'</span><span>'+esc(t.status)+'</span></div>';
  }).join('');
  const folderKey=(s.folders||[]).map(function(f){return f.imap_name;}).join('\0');
  if(folderKey!==lastFolderNames){
    lastFolderNames=folderKey;
    document.getElementById('folderrows').innerHTML=(s.folders||[]).map(function(f,i){
      const on=f.selected!==false;
      return '<div class="rowl" data-idx="'+i+'"><span><label><input type="checkbox" class="fsel" value="'+esc(f.imap_name)+'"'+(on?' checked':'')+'> '+esc(f.display)+'</label></span><span>'+esc(f.imap_name)+'</span><span><div class="bar"><i style="width:'+(f.progress||0)+'%"></i></div></span><span class="fcnt">'+(f.exported||0)+' / '+fmt(f.total)+'</span><span class="fst">'+esc(f.status)+'</span></div>';
    }).join('');
  } else {
    (s.folders||[]).forEach(function(f,i){
      const row=document.querySelector('#folderrows .rowl[data-idx="'+i+'"]');
      if(!row) return;
      const bar=row.querySelector('.bar i');
      if(bar) bar.style.width=(f.progress||0)+'%';
      const cnt=row.querySelector('.fcnt');
      if(cnt) cnt.textContent=(f.exported||0)+' / '+fmt(f.total);
      const st=row.querySelector('.fst');
      if(st) st.textContent=f.status||'';
    });
  }
  const nFold=(s.folders||[]).length;
  document.getElementById('folderhint').textContent=nFold?(nFold+' Ordner – ankreuzen, welche gesichert werden'):'Zuerst „Ordner holen“, dann ankreuzen.';
  document.querySelectorAll('#folderrows .fsel').forEach(function(box){ box.disabled=!!s.mail_running; });
  document.getElementById('mailrows').innerHTML=(s.browse||[]).map(function(m){
    return '<div class="rowl"><span>'+esc(m.folder)+'</span><span>'+esc(m.from)+'</span><span>'+esc(m.to)+'</span><span>'+esc(m.subject)+'</span><span>'+esc(m.date)+'</span><span>'+sizeTxt(m.size)+'</span></div>';
  }).join('');
  document.getElementById('vatoken').textContent=s.va_token_status||'Kein Token';
  document.getElementById('vastatus').textContent='Status: '+(s.va_status||'Bereit');
  document.getElementById('vaerr').textContent=s.va_error||'';
  document.getElementById('btnVaDl').disabled=!!s.va_running;
  document.getElementById('btnVaStop').disabled=!s.va_running;
  const prevBtn=document.getElementById('btnVaPrev');
  if(prevBtn) prevBtn.disabled=!!s.va_running;
  if(Array.isArray(s.va_preview)&&s.va_preview.length){
    vaItems=s.va_preview;
    lastVaRev=s.va_local_rev||0;
    renderVaList();
    renderVaPack();
  }else if((s.va_local_rev||0)!==lastVaRev){
    lastVaRev=s.va_local_rev||0;
    loadVaLocal(true);
  }
  applyVaConv(s.audio_prep);
  updatePackJob(s);
}
setInterval(tick,1000); tick();
setInterval(async function(){
  const st=await (await fetch('/api/va/audio/prepare')).json().catch(function(){return {};});
  applyVaConv(st);
}, 500);

function vaKinds(){
  const kinds=[];
  if(document.getElementById('vakindpublic').checked) kinds.push('public');
  if(document.getElementById('vakindintern').checked) kinds.push('intern');
  return kinds;
}
function vaPreview(){
  const kinds=vaKinds();
  if(!kinds.length){ alert('Bitte öffentlich und/oder intern ankreuzen.'); return; }
  post('/api/va/preview',{kinds:kinds,curl:gv('vacurl')});
}
function vaDownload(){
  const kinds=vaKinds();
  if(!kinds.length){ alert('Bitte öffentlich und/oder intern ankreuzen.'); return; }
  post('/api/va/download',{kinds:kinds,curl:gv('vacurl')});
}
function vaStand(it){
  const s=it.stand||'';
  if(s==='neu') return 'neu';
  if(s==='audio') return 'Audio fehlt';
  if(s==='folien') return 'Folien fehlen';
  if(s==='ok') return 'vollständig';
  if(s==='fehler') return 'Fehler';
  if(it.has_folien&&it.has_audio) return 'lokal';
  if(it.has_folien) return 'lokal, PDF';
  if(it.has_audio) return 'lokal';
  return 'lokal, ohne Audio';
}
function vaDur(n){
  n=parseInt(n,10);
  if(isNaN(n)||n<0) return '–';
  const h=Math.floor(n/3600);
  const m=Math.floor((n%3600)/60);
  const s=n%60;
  const ss=String(s).padStart(2,'0');
  if(h) return h+':'+String(m).padStart(2,'0')+':'+ss;
  return m+':'+ss;
}
function vaKindLabel(kind){
  return kind==='intern'?'Intern':'Öffentlich';
}
async function loadVaLocal(quiet){
  try{
    const r=await fetch('/api/va/local');
    const d=await r.json().catch(function(){return [];});
    vaItems=Array.isArray(d)?d:[];
    vaColsFitted=false;
    renderVaList();
    renderVaPack();
  }catch(e){
    if(!quiet) alert('Lokale Liste nicht lesbar.');
  }
}
function renderVaList(){
  if(!document.getElementById('vahead')||!document.getElementById('vahead').children.length) vaRenderHead();
  const filter=document.getElementById('vafilter').value;
  const q=(document.getElementById('vasearch').value||'').toLowerCase().trim();
  const rows=vaItems.filter(function(it){
    if(filter!=='all' && it.kind!==filter) return false;
    if(!q) return true;
    const hay=((it.titel||'')+' '+(it.datum||'')+' '+(it.ort||'')+' '+((it.themen||[]).join(' '))).toLowerCase();
    return hay.indexOf(q)>=0;
  });
  const nSel=rows.filter(function(it){return !!vaSelected[vaKey(it)];}).length;
  document.getElementById('vahint').textContent=rows.length?(rows.length+' Stammtische'+(nSel?' · '+nSel+' gewählt':'')):'Noch keine Stammtische lokal';
  document.getElementById('varows').innerHTML=rows.map(function(it){
    const key=vaKey(it);
    const on=vaOpen&&vaOpen.kind===it.kind&&vaOpen.id===it.id?' on':'';
    const conv=vaConvMatch(it)?' converting':'';
    const checked=vaSelected[key]?' checked':'';
    return '<div class="rowl'+on+conv+'" data-kind="'+escAttr(it.kind)+'" data-id="'+escAttr(it.id)+'"><span class="vasel"><input type="checkbox" class="vaselbox"'+checked+'></span><span class="vadatum">'+esc(it.datum||'')+'</span><span class="vatitle"><span class="t">'+esc(it.titel||it.id||'')+'</span>'+vaRing(it)+'</span><span class="vaquelle">'+esc(vaKindLabel(it.kind))+'</span><span class="vaaudio">'+vaYes(it.has_audio)+'</span><span class="vadocs">'+vaYes(it.has_folien)+'</span><span class="vastand">'+esc(vaStand(it))+'</span><span class="vadauer">'+vaDur(it.dauer_sek)+'</span></div>';
  }).join('');
  const all=document.getElementById('vaSelAll');
  if(all) all.checked=rows.length>0 && nSel===rows.length;
  if(!vaColsFitted) vaFitCols(false);
}
function vaSelAllVisible(on){
  const filter=document.getElementById('vafilter').value;
  const q=(document.getElementById('vasearch').value||'').toLowerCase().trim();
  vaItems.forEach(function(it){
    if(filter!=='all' && it.kind!==filter) return;
    if(q){
      const hay=((it.titel||'')+' '+(it.datum||'')+' '+(it.ort||'')+' '+((it.themen||[]).join(' '))).toLowerCase();
      if(hay.indexOf(q)<0) return;
    }
    if(on) vaSelected[vaKey(it)]=true;
    else delete vaSelected[vaKey(it)];
  });
  renderVaList();
}
function vaSelectedItems(){
  return vaItems.filter(function(it){ return !!vaSelected[vaKey(it)]; });
}
async function openVa(kind, id){
  if(!kind||!id) return;
  if(vaAbort) vaAbort.abort();
  vaAbort=new AbortController();
  const token=vaAbort;
  vaOpen={kind:kind,id:id};
  renderVaList();
  try{
    const r=await fetch('/api/va/item?kind='+encodeURIComponent(kind)+'&id='+encodeURIComponent(id),{signal:token.signal});
    const d=await r.json().catch(function(){return {};});
    if(token!==vaAbort) return;
    if(!r.ok){ alert(d.detail||d.error||'Nicht gefunden'); return; }
    renderVaDetail(d);
  }catch(e){
    if(e&&e.name==='AbortError') return;
  }
}
async function showVaTranscript(){
  if(!vaOpen) return;
  const box=document.getElementById('vatxmount');
  const btn=document.getElementById('showtx');
  if(!box) return;
  if(btn) btn.disabled=true;
  try{
    const r=await fetch('/api/va/item?kind='+encodeURIComponent(vaOpen.kind)+'&id='+encodeURIComponent(vaOpen.id)+'&full=1');
    const d=await r.json().catch(function(){return {};});
    if(!r.ok){ alert(d.detail||'Transkript nicht lesbar'); return; }
    box.innerHTML=vaTxLines(d.transkript||[], d.sprecher||[]);
    if(btn) btn.style.display='none';
  }catch(e){
    if(btn) btn.disabled=false;
  }
}
function vaBlock(title, html){
  if(!html) return '';
  return '<h3>'+esc(title)+'</h3>'+html;
}
function vaStamp(v){
  if(v==null||v==='') return null;
  if(typeof v==='number') return v>100000?v/1000:v;
  const t=parseFloat(v);
  return isNaN(t)?null:t;
}
function vaTxLines(list, sprecher){
  if(!list||!list.length||!list.map) return '';
  return '<div id="vatx">'+list.map(function(t){
    const n=vaStamp(t.t!=null?t.t:(t.start!=null?t.start:t.zeit));
    const name=sprecher[t.sp]!=null?sprecher[t.sp]:(t.sprecher||t.speaker||('Sprecher '+(t.sp==null?'?':t.sp)));
    return '<div class="tx" data-t="'+(n||0)+'"><span>'+vaDur(n)+'</span><span>'+esc(name)+'</span><span>'+esc(t.text||t.titel||t.title||'')+'</span></div>';
  }).join('')+'</div>';
}
function renderVaDetail(item){
  const z=item.zusammenfassung&&typeof item.zusammenfassung==='object'?item.zusammenfassung:{};
  const sprecher=item.sprecher||[];
  const themen=(item.themen||[]).join? (item.themen||[]).join(', ') : '';
  let html='<h2>'+esc(item.titel||item.id||'')+'</h2>';
  html+='<p class="hint">'+esc(vaKindLabel(item.kind))+' · '+esc(item.datum||'')+(item.ort?' · '+esc(item.ort):'')+' · '+vaDur(item.dauer_sek)+(themen?' · '+esc(themen):'')+'</p>';
  if(item.hinweis) html+='<p class="hint">'+esc(item.hinweis)+'</p>';
  if(z.lead) html+=vaBlock('Kurzfassung','<p>'+esc(z.lead)+'</p>');
  if(z.fragen&&z.fragen.length){
    html+=vaBlock('Fragen', z.fragen.map(function(q){
      return '<p><strong>'+esc(q.frage||'')+'</strong><br>'+esc(q.antwort||'')+'</p>';
    }).join(''));
  }
  if(z.aufgaben&&z.aufgaben.length){
    html+=vaBlock('Aufgaben', z.aufgaben.map(function(a){
      return '<p><strong>'+esc(a.wer||'')+':</strong> '+esc(a.text||'')+'</p>';
    }).join(''));
  }
  if(z.sachstand&&z.sachstand.length){
    html+=vaBlock('Sachstand','<ul>'+z.sachstand.map(function(s){return '<li>'+esc(s)+'</li>';}).join('')+'</ul>');
  }
  if(z.offene_fragen&&z.offene_fragen.length){
    html+=vaBlock('Offene Fragen','<ul>'+z.offene_fragen.map(function(s){return '<li>'+esc(s)+'</li>';}).join('')+'</ul>');
  }
  if(item.kapitel&&item.kapitel.length&&item.kapitel.map){
    html+=vaBlock('Kapitel', vaTxLines(item.kapitel.map(function(c){
      if(typeof c==='string') return {t:0,text:c};
      return {t:c.t!=null?c.t:c.start, sp:c.sp, text:c.titel||c.title||c.name||c.text||''};
    }), sprecher));
  }
  if(item.folien_dateien&&item.folien_dateien.length){
    html+=vaBlock('Folien / Präsentation', item.folien_dateien.map(function(f){
      const low=(f||'').toLowerCase();
      const kind=low.indexOf('.pdf')>=0?'PDF':(low.indexOf('.ppt')>=0||low.indexOf('.key')>=0||low.indexOf('.odp')>=0?'Präsentation':'Datei');
      return '<p><button class="act" type="button" data-kind="'+escAttr(item.kind)+'" data-id="'+escAttr(item.id)+'" data-folie="'+escAttr(f)+'">'+esc(kind)+' öffnen: '+esc(f)+'</button></p>';
    }).join(''));
  }
  const nv=item.nachverfolgung;
  if(nv&&typeof nv==='string'&&nv.trim()) html+=vaBlock('Nachverfolgung','<p>'+esc(nv)+'</p>');
  if((item.transkript&&item.transkript.length)||item.transkript_n){
    const n=(item.transkript&&item.transkript.length)?item.transkript.length:(item.transkript_n||0);
    html+=vaBlock('Transkript', '<p><button class="act" type="button" id="showtx">Transkript zeigen ('+n+' Absätze)</button></p><div id="vatxmount"></div>');
  }
  document.getElementById('vabody').innerHTML=html;
  const txBtn=document.getElementById('showtx');
  if(txBtn) txBtn.onclick=showVaTranscript;
  armVaAudio(item);
}
async function deleteVaSelected(){
  const picked=vaSelectedItems();
  const hint=document.getElementById('vahint');
  if(!picked.length){
    if(hint) hint.textContent='Keine Stammtische markiert.';
    alert('Keine Stammtische markiert.');
    return;
  }
  if(!confirm(picked.length+' Stammtisch'+(picked.length===1?'':'e')+' lokal löschen?\n\nAudio, Folien und Text werden entfernt. Beim nächsten Download können sie neu geladen werden.')) return;
  const payload={items:picked.map(function(it){return {kind:it.kind,id:it.id};})};
  const r=await fetch('/api/va/item/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const d=await r.json().catch(function(){return {};});
  if(!r.ok){ alert(d.detail||'Löschen fehlgeschlagen'); return; }
  const gone={};
  (d.deleted||payload.items).forEach(function(it){ gone[vaKey(it)]=true; });
  if(vaOpen && gone[vaKey(vaOpen)]){
    vaOpen=null;
    document.getElementById('vabody').innerHTML='';
    armVaAudio({});
  }
  Object.keys(gone).forEach(function(k){ delete vaSelected[k]; });
  vaItems=vaItems.map(function(it){
    if(!gone[vaKey(it)]) return it;
    if(it.stand && it.stand!=='lokal'){
      return Object.assign({},it,{has_audio:false,has_folien:false,stand:'neu',need:true});
    }
    return null;
  }).filter(Boolean);
  vaColsFitted=false;
  renderVaList();
  renderVaPack();
  if(hint) hint.textContent=(d.deleted||[]).length+' gelöscht';
}
function fmtEta(sec){
  if(sec==null||sec==='') return '';
  sec=Math.max(0,parseInt(sec,10)||0);
  if(sec<=0) return '';
  if(sec<60) return 'noch ca. '+sec+' s';
  return 'noch ca. '+Math.floor(sec/60)+' min '+ (sec%60)+' s';
}
function setJob(id, on, pct, text){
  const box=document.getElementById(id);
  if(!box) return;
  box.className=on?'job on':'job';
  const bar=box.querySelector('i');
  if(bar) bar.style.width=(pct||0)+'%';
  const meta=box.querySelector('.meta');
  if(meta) meta.textContent=text||'';
}
let vaAudioPoll=null;
function armVaAudio(item){
  const player=document.getElementById('vaaudio');
  const wrap=document.getElementById('vaplayer');
  const err=document.getElementById('vaaudioerr');
  if(vaAudioPoll){ clearInterval(vaAudioPoll); vaAudioPoll=null; }
  if(player){ player.onerror=null; player.oncanplay=null; }
  if(!item.has_audio){
    wrap.style.display='none';
    if(err) err.textContent='';
    setJob('vaaudiojob', false, 0, '');
    if(player){ player.removeAttribute('src'); player.load(); }
    return;
  }
  wrap.style.display='block';
  if(err) err.textContent='';
  player.oncanplay=function(){ if(err) err.textContent=''; setJob('vaaudiojob', false, 0, ''); };
  player.onerror=function(){
    const code=player.error&&player.error.code;
    if(!code || code===1) return;
    if(err) err.textContent='Diese Datei kann der Player nicht lesen.';
  };
  const src='/api/va/audio/'+encodeURIComponent(item.kind)+'/'+encodeURIComponent(item.id);
  const opened=item.kind+'/'+item.id;
  if(item.audio_ready){
    player.src=src;
    player.setAttribute('data-id', opened);
  }else{
    player.removeAttribute('src');
    player.removeAttribute('data-id');
    player.load();
    setJob('vaaudiojob', true, 0, 'Audio wird nach Import/Download für den Player vorbereitet …');
  }
  async function tickAudio(){
    if(!vaOpen||(vaOpen.kind+'/'+vaOpen.id)!==opened) return;
    const meta=await (await fetch('/api/va/audio/meta?kind='+encodeURIComponent(item.kind)+'&id='+encodeURIComponent(item.id))).json().catch(function(){return {};});
    const st=await (await fetch('/api/va/audio/prepare')).json().catch(function(){return {};});
    applyVaConv(st);
    if(!vaOpen||(vaOpen.kind+'/'+vaOpen.id)!==opened) return;
    if(meta.ready){
      if(player.getAttribute('data-id')!==opened){
        player.src=src;
        player.setAttribute('data-id', opened);
      }
    }
    if(st.running){
      setJob('vaaudiojob', true, st.pct||0, (st.status||'Wandle Audio …')+(st.eta_sec!=null?(' · '+fmtEta(st.eta_sec)):''));
    }else if(!meta.ready){
      setJob('vaaudiojob', true, 0, 'Audio wird nach Import/Download für den Player vorbereitet …');
    }else{
      setJob('vaaudiojob', false, 0, '');
    }
    if(meta.ready && !st.running && vaAudioPoll){
      clearInterval(vaAudioPoll);
      vaAudioPoll=null;
    }
  }
  tickAudio();
  if(!item.audio_ready){
    vaAudioPoll=setInterval(tickAudio, 1200);
  }
}
function seekVa(t){
  const player=document.getElementById('vaaudio');
  const n=parseFloat(t);
  if(!player||isNaN(n)) return;
  try{ player.currentTime=n; }catch(e){}
  if(player.paused) player.play().catch(function(){});
}
function vaPackKinds(){
  const kinds=[];
  if(document.getElementById('vapackpublic').checked) kinds.push('public');
  if(document.getElementById('vapackintern').checked) kinds.push('intern');
  return kinds;
}
let packColWidths=null;
const PACK_COL_LABELS=['','Datum','Titel','Quelle','Audio','Docs','Dauer'];
const PACK_COL_MIN=[28,72,140,58,44,44,48];
const PACK_COL_MAX=[40,110,320,90,56,56,64];
function packApplyCols(){
  const list=document.getElementById('vapack');
  if(!list) return;
  if(!packColWidths||packColWidths.length!==PACK_COL_MIN.length) packColWidths=PACK_COL_MIN.slice();
  const parts=packColWidths.map(function(w,i){
    const lo=PACK_COL_MIN[i];
    const hi=PACK_COL_MAX[i]||Math.max(lo*4, 320);
    const n=Math.max(lo, Math.min(hi, w||lo));
    packColWidths[i]=n;
    return n+'px';
  });
  list.style.setProperty('--pack-cols', parts.join(' '));
}
function packRenderHead(){
  const head=document.getElementById('vapackhead');
  if(!head) return;
  head.innerHTML=PACK_COL_LABELS.map(function(label,i){
    const grip=i<PACK_COL_LABELS.length-1?'<i class="colres" data-col="'+i+'" title="Breite ziehen"></i>':'';
    if(i===0) return '<span class="vacol vasel">'+grip+'</span>';
    return '<span class="vacol"><span class="vacol-lab">'+esc(label)+'</span>'+grip+'</span>';
  }).join('');
  head.querySelectorAll('.colres').forEach(function(grip){
    grip.onmousedown=function(e){
      e.preventDefault();
      e.stopPropagation();
      const idx=parseInt(grip.getAttribute('data-col'),10);
      if(isNaN(idx)) return;
      if(!packColWidths||packColWidths.length!==PACK_COL_MIN.length) packApplyCols();
      const startX=e.clientX;
      const startW=packColWidths[idx];
      grip.classList.add('drag');
      function move(ev){
        const hi=PACK_COL_MAX[idx]||Math.max(PACK_COL_MIN[idx]*4, 480);
        packColWidths[idx]=Math.max(PACK_COL_MIN[idx], Math.min(hi, startW+(ev.clientX-startX)));
        packApplyCols();
      }
      function up(){
        grip.classList.remove('drag');
        document.removeEventListener('mousemove', move);
        document.removeEventListener('mouseup', up);
      }
      document.addEventListener('mousemove', move);
      document.addEventListener('mouseup', up);
    };
  });
  packApplyCols();
}
function vaPackRows(){
  const kinds=vaPackKinds();
  const from=document.getElementById('vapackfrom').value||'';
  const to=document.getElementById('vapackto').value||'';
  return vaItems.filter(function(it){
    if(kinds.indexOf(it.kind)<0) return false;
    const day=(it.datum||'').toString().slice(0,10);
    if(from && day && day<from) return false;
    if(to && day && day>to) return false;
    return true;
  });
}
function renderVaPack(){
  if(!document.getElementById('vapackhead')||!document.getElementById('vapackhead').children.length) packRenderHead();
  const rows=vaPackRows();
  const box=document.getElementById('vapackrows');
  if(!box) return;
  document.getElementById('vapackhint').textContent=rows.length?(rows.length+' lokal passend'):'Nichts lokal für diese Auswahl';
  box.innerHTML=rows.map(function(it){
    const hint=it.hinweis?' title="'+escAttr(it.hinweis)+'"':'';
    return '<label class="rowl"'+hint+'><span class="vasel"><input type="checkbox" class="vapackcb" data-kind="'+escAttr(it.kind)+'" data-id="'+escAttr(it.id)+'" checked></span><span class="vadatum">'+esc(it.datum||'')+'</span><span class="vatitle">'+esc(it.titel||it.id||'')+'</span><span class="vaquelle">'+esc(vaKindLabel(it.kind))+'</span><span class="vaaudio">'+vaYes(it.has_audio)+'</span><span class="vadocs">'+vaYes(it.has_folien)+'</span><span class="vadauer">'+vaDur(it.dauer_sek)+'</span></label>';
  }).join('');
  packApplyCols();
}
function vaPackCheck(on){
  document.querySelectorAll('.vapackcb').forEach(function(el){ el.checked=!!on; });
}
function updatePackJob(s){
  const running=!!s.pack_running;
  const p=s.pack_progress||{};
  const btn=document.getElementById('btnPack');
  const stop=document.getElementById('btnPackStop');
  if(btn) btn.disabled=running;
  if(stop) stop.disabled=!running;
  const eta=(p.eta_sec!=null && running)?fmtEta(p.eta_sec):'';
  const text=(p.status||'')+(eta?(' · '+eta):'');
  setJob('packjob', running, running ? (p.pct || 0) : 0, running ? text : '');
  const err=document.getElementById('vapackerr');
  if(err && s.pack_error) err.textContent=s.pack_error;
  const hint=document.getElementById('vapackhint');
  if(hint){
    if(running){
      hint.textContent=p.status||'Paket …';
    }else if(s.pack_saved_path){
      hint.textContent='Gespeichert: '+s.pack_saved_path;
    }else if(p.status){
      hint.textContent=p.status;
    }
  }
}
async function packVa(){
  const err=document.getElementById('vapackerr');
  err.textContent='';
  const kinds=vaPackKinds();
  if(!kinds.length){ err.textContent='Öffentlich und/oder intern ankreuzen.'; return; }
  const ids=[];
  document.querySelectorAll('.vapackcb:checked').forEach(function(el){
    ids.push({kind:el.getAttribute('data-kind'),id:el.getAttribute('data-id')});
  });
  if(!ids.length){ err.textContent='Mindestens einen Stammtisch ankreuzen.'; return; }
  const body={kinds:kinds,date_from:document.getElementById('vapackfrom').value||'',date_to:document.getElementById('vapackto').value||'',ids:ids};
  const r=await fetch('/api/va/pack/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d=await r.json().catch(function(){return {};});
  if(!r.ok){
    const x=d.detail||d.error||'Paket fehlgeschlagen';
    err.textContent=typeof x==='string'?x:JSON.stringify(x);
  }
}
function stopPack(){ fetch('/api/va/pack/stop',{method:'POST'}); }

const FILTER_OPS=[
  {id:'eq',label:'ist'},
  {id:'ne',label:'ist nicht'},
  {id:'in',label:'ist einer von'},
  {id:'not_in',label:'ist nicht einer von'},
  {id:'contains',label:'enthält'},
  {id:'not_contains',label:'enthält nicht'},
  {id:'starts',label:'beginnt mit'},
  {id:'empty',label:'ist leer'},
  {id:'not_empty',label:'ist nicht leer'},
  {id:'gt',label:'größer als'},
  {id:'gte',label:'größer gleich'},
  {id:'lt',label:'kleiner als'},
  {id:'lte',label:'kleiner gleich'},
  {id:'between',label:'zwischen'}
];
const AGG_FNS=[
  {id:'count',label:'Anzahl'},
  {id:'sum',label:'Summe'},
  {id:'avg',label:'Durchschnitt'},
  {id:'min',label:'Minimum'},
  {id:'max',label:'Maximum'}
];
let backups=[];
let analyzeTables=[];
let selectedTable=null;
let columnMeta=[];
let sqlDirty=false;
let analyzePageNo=0;
let analyzeTotal=0;
let analyzePageSize=50;
let analyzeOrder=null;
let analyzeLoaded=false;
let analyzeBusy=false;
let analyzeSeq=0;
let lastAnalyze=null;
let popText='';
let popHtmlOn=false;
let analyzeMode='query';
let hiddenCols={};
let distinctCache={};
let popLinkList=[];
let pendingCompare=false;
let popFilterRi=null;
let popFilterCol='';

function colOptions(selected){
  return '<option value="">Spalte …</option>'+columnMeta.map(function(c){
    const n=c.name||c;
    return '<option value="'+esc(n)+'"'+(selected===n?' selected':'')+'>'+esc(n)+'</option>';
  }).join('');
}
function groupColOptions(selected){
  let html=colOptions(selected);
  columnMeta.forEach(function(c){
    const n=c.name||c;
    if(!isDateishCol(n)) return;
    const val=n+':month';
    html+='<option value="'+esc(val)+'"'+(selected===val?' selected':'')+'>'+esc(n)+' (Monat)</option>';
  });
  return html;
}
function groupLabel(g){
  if(g && g.length>6 && g.slice(-6)===':month') return g.slice(0,-6)+' (Monat)';
  return g||'';
}
function needsValue(op){ return op!=='empty' && op!=='not_empty'; }
function needsTwoValues(op){ return op==='between'; }
function isDateishCol(name){
  const n=(name||'').toLowerCase();
  if(!n) return false;
  const meta=columnMeta.filter(function(c){ return (c.name||c)===name; })[0];
  const t=((meta&&meta.data_type)||'').toLowerCase();
  if(/date|time|timestamp/.test(t)) return true;
  if(/(_at|_date|_time)$/.test(n)) return true;
  if(n==='date'||n==='timestamp') return true;
  if(n.indexOf('period')>=0) return true;
  return false;
}
function isCompareOp(op){ return op==='gt'||op==='gte'||op==='lt'||op==='lte'||op==='between'; }
function fillDateCols(){
  const sel=document.getElementById('adatecol');
  if(!sel) return;
  const prev=sel.value;
  const cols=columnMeta.map(function(c){ return c.name||c; }).filter(isDateishCol);
  sel.innerHTML='<option value="">automatisch</option>'+cols.map(function(n){
    return '<option value="'+esc(n)+'">'+esc(n)+'</option>';
  }).join('');
  if(prev && cols.indexOf(prev)>=0) sel.value=prev;
}
function syncDateGrain(){
  const grain=(document.getElementById('adategrain')&&document.getElementById('adategrain').value)||'day';
  const next=grain==='month'?'month':'date';
  ['adatefrom','adateto'].forEach(function(id){
    const el=document.getElementById(id);
    if(!el) return;
    const keep=el.value;
    if(el.type!==next){
      el.type=next;
      if(keep){
        if(next==='month' && /^\d{4}-\d{2}/.test(keep)) el.value=keep.slice(0,7);
        else if(next==='date' && /^\d{4}-\d{2}$/.test(keep)) el.value=keep+'-01';
        else if(!el.value) el.value=keep;
      }
    }
  });
}
function guessDateCol(){
  const sel=document.getElementById('adatecol');
  if(sel && sel.value) return sel.value;
  if(lastAnalyze&&lastAnalyze.date_column) return lastAnalyze.date_column;
  const cols=columnMeta.map(function(c){ return c.name||c; });
  const prefer=['created_at','updated_at','createdAt','updatedAt'];
  for(let i=0;i<prefer.length;i++){ if(cols.indexOf(prefer[i])>=0) return prefer[i]; }
  return cols.filter(isDateishCol)[0]||'';
}
function pickAmountCol(){
  const cols=columnMeta.map(function(c){ return c.name||c; });
  const prefer=['total_amount','amount','betrag','brutto','netto','summe'];
  for(let i=0;i<prefer.length;i++){ if(cols.indexOf(prefer[i])>=0) return prefer[i]; }
  for(let i=0;i<cols.length;i++){
    const n=String(cols[i]||'');
    if(/amount|betrag|summe|preis|total/i.test(n) && !/_id$/i.test(n)) return n;
  }
  return '';
}
function sumInPeriod(){
  if(!selectedTable){ alert('Zuerst eine Tabelle wählen.'); return; }
  const dateCol=guessDateCol();
  const amount=pickAmountCol();
  const cols=columnMeta.map(function(c){ return c.name||c; });
  const hasStatus=cols.indexOf('status')>=0;
  document.getElementById('agroups').innerHTML='';
  document.getElementById('aaggs').innerHTML='';
  sqlDirty=false;
  if(dateCol) addGroup(dateCol+':month');
  else if(hasStatus) addGroup('status');
  else { alert('Keine Datumsspalte und kein Status zum Gruppieren.'); return; }
  if(amount) addAgg('sum', amount);
  else addAgg('count');
  applyAnalyze(0);
}
function currentFolder(){ return document.getElementById('afolder').value; }
function setAnalyzeErr(msg){ document.getElementById('aerr').textContent=msg||''; }
async function analyzeGet(url){
  const r=await fetch(url);
  const d=await r.json().catch(function(){return {};});
  if(!r.ok) throw new Error(d.detail||d.error||'Fehler');
  return d;
}
async function loadBackups(force){
  if(analyzeLoaded && !force) return;
  try{
    setAnalyzeErr('');
    const d=await analyzeGet('/api/analyze/backups');
    backups=d.backups||[];
    const sel=document.getElementById('afolder');
    const prev=sel.value;
    sel.innerHTML=backups.length?backups.map(function(b){
      return '<option value="'+esc(b.name)+'">'+esc(b.name)+' ('+fmt(b.tables)+' Tabellen)</option>';
    }).join(''):'<option value="">Keine Sicherungen gefunden</option>';
    if(prev && backups.some(function(b){return b.name===prev;})) sel.value=prev;
    else if(backups.length) sel.selectedIndex=0;
    analyzeLoaded=true;
    document.getElementById('astatus').textContent=backups.length?(backups.length+' Ordner'):'Keine Sicherungen';
    fillCompareSelect();
    if(sel.value) await loadAnalyzeTables();
  }catch(e){
    setAnalyzeErr(e.message||String(e));
  }
}
async function onBackupChange(){
  selectedTable=null;
  columnMeta=[];
  analyzeMode='query';
  document.getElementById('globbox').className='list globbox';
  fillCompareSelect();
  resetBuilder(true);
  await loadAnalyzeTables();
}
async function loadAnalyzeTables(){
  const folder=currentFolder();
  if(!folder){
    analyzeTables=[];
    renderTableList();
    return;
  }
  try{
    setAnalyzeErr('');
    document.getElementById('astatus').textContent='Lade Tabellen …';
    const d=await analyzeGet('/api/analyze/tables?folder='+encodeURIComponent(folder));
    analyzeTables=d.tables||[];
    document.getElementById('astatus').textContent=analyzeTables.length+' Tabellen in '+folder;
    renderTableList();
    if(selectedTable && !analyzeTables.some(function(t){return t.stem===selectedTable.stem;})) selectedTable=null;
    if(!selectedTable && analyzeTables.length) selectTable(analyzeTables[0].stem);
  }catch(e){
    setAnalyzeErr(e.message||String(e));
  }
}
function renderTableList(){
  const q=(document.getElementById('tsearch').value||'').toLowerCase();
  const stem=selectedTable&&selectedTable.stem;
  document.getElementById('trows').innerHTML=analyzeTables.filter(function(t){
    return !q || (t.display||'').toLowerCase().indexOf(q)>=0 || (t.stem||'').toLowerCase().indexOf(q)>=0;
  }).map(function(t){
    const on=t.stem===stem?' on':'';
    const n=t.row_count===null||t.row_count===undefined?'?':fmt(t.row_count);
    const tip=t.display+' · '+n+' Zeilen';
    return '<button type="button" class="rowl'+on+'" title="'+escAttr(tip)+'" onclick="selectTable(\''+esc(t.stem)+'\')"><span>'+esc(t.display)+'</span><span>'+n+'</span></button>';
  }).join('')||'<div class="rowl"><span>Keine Tabellen</span><span></span></div>';
}
function selectTable(stem, preset){
  const t=analyzeTables.filter(function(x){return x.stem===stem;})[0];
  if(!t) return;
  selectedTable=t;
  columnMeta=t.columns||[];
  analyzeMode='query';
  document.getElementById('cmpkinds').style.display='none';
  resetBuilder(true);
  fillDateCols();
  renderTableList();
  renderColPick();
  fillSavedSelect();
  document.getElementById('agridlegend').textContent=' 3. '+t.display;
  if(pendingCompare){
    pendingCompare=false;
    runCompare(0);
    return;
  }
  if(preset) applyPreset(preset);
  else applyAnalyze(0);
}
function resetBuilder(clearSql){
  document.getElementById('asearch').value='';
  document.getElementById('afilters').innerHTML='';
  document.getElementById('agroups').innerHTML='';
  document.getElementById('aaggs').innerHTML='';
  document.getElementById('adatefrom').value='';
  document.getElementById('adateto').value='';
  const dcol=document.getElementById('adatecol');
  if(dcol) dcol.value='';
  analyzeOrder=null;
  analyzePageNo=0;
  sqlDirty=false;
  if(clearSql) document.getElementById('asql').value='';
}
function resetAnalyze(){
  sqlDirty=false;
  resetBuilder(true);
  document.getElementById('anote').value='';
  if(selectedTable) applyAnalyze(0);
}
function addFilter(col,op,value,value2){
  const wrap=document.createElement('div');
  wrap.className='filterrow';
  const listId='fdl-'+Math.random().toString(36).slice(2,8);
  wrap.innerHTML='<select class="fcol">'+colOptions(col||'')+'</select>'
    +'<select class="fop">'+FILTER_OPS.map(function(o){return '<option value="'+o.id+'"'+(op===o.id?' selected':'')+'>'+o.label+'</option>';}).join('')+'</select>'
    +'<input class="fval" type="text" list="'+listId+'" value="'+esc(value||'')+'"/>'
    +'<datalist id="'+listId+'"></datalist>'
    +'<span class="fsep">bis</span>'
    +'<input class="fval2" type="text" value="'+esc(value2||'')+'"/>'
    +'<button class="act" type="button">Entfernen</button>';
  wrap.querySelector('button').onclick=function(){ wrap.remove(); };
  wrap.querySelector('.fop').onchange=function(){ syncFilterValue(wrap); };
  wrap.querySelector('.fcol').onchange=function(){ syncFilterValue(wrap); loadFilterSuggest(wrap); };
  wrap.querySelector('.fval').onfocus=function(){ loadFilterSuggest(wrap); };
  document.getElementById('afilters').appendChild(wrap);
  syncFilterValue(wrap);
  if(col) loadFilterSuggest(wrap);
}
function syncFilterValue(wrap){
  const op=wrap.querySelector('.fop').value;
  const col=wrap.querySelector('.fcol').value;
  const a=wrap.querySelector('.fval');
  const b=wrap.querySelector('.fval2');
  const sep=wrap.querySelector('.fsep');
  const list=wrap.querySelector('datalist');
  const keepA=a.value;
  const keepB=b.value;
  const two=needsTwoValues(op);
  const show=needsValue(op);
  const dateish=isDateishCol(col) && isCompareOp(op);
  a.style.display=show?'':'none';
  b.style.display=two?'':'none';
  sep.style.display=two?'':'none';
  const listed=op==='in'||op==='not_in';
  if(dateish && two){
    if(a.type!=='month') a.type='month';
    if(b.type!=='month') b.type='month';
    a.placeholder='von Monat'; b.placeholder='bis Monat';
    a.title='Monat von, z. B. 2026-01';
    b.title='Monat bis, z. B. 2026-03 – der ganze Monat zählt mit';
    a.removeAttribute('list');
  }else if(dateish){
    if(a.type!=='text') a.type='text';
    if(b.type!=='text') b.type='text';
    a.placeholder='2026-01 oder 2026-01-15';
    a.title='Monat (2026-01, ganzer Monat) oder Tag (2026-01-15, der ganze Tag). Mehrere Monate: „zwischen“.';
    if(list) a.setAttribute('list', list.id);
  }else{
    if(a.type!=='text') a.type='text';
    if(b.type!=='text') b.type='text';
    a.placeholder=listed?'Wert, Wert, …':(two?'von':'');
    b.placeholder=two?'bis':'';
    a.title=listed?'Mehrere Werte mit Komma oder Zeilenumbruch.':'';
    b.title='';
    if(list) a.setAttribute('list', list.id);
  }
  if(keepA && !a.value) a.value=keepA;
  if(keepB && !b.value) b.value=keepB;
  a.style.width=(listed && !dateish)?'240px':'';
}
function addGroup(col){
  const wrap=document.createElement('div');
  wrap.className='filterrow';
  wrap.innerHTML='<select class="gcol">'+groupColOptions(col||'')+'</select><button class="act" type="button">Entfernen</button>';
  wrap.querySelector('button').onclick=function(){ wrap.remove(); };
  document.getElementById('agroups').appendChild(wrap);
}
function addAgg(fn,col){
  const wrap=document.createElement('div');
  wrap.className='filterrow';
  wrap.innerHTML='<select class="afn">'+AGG_FNS.map(function(o){return '<option value="'+o.id+'"'+(fn===o.id?' selected':'')+'>'+o.label+'</option>';}).join('')+'</select>'
    +'<select class="acol">'+colOptions(col||'')+'</select>'
    +'<button class="act" type="button">Entfernen</button>';
  wrap.querySelector('button').onclick=function(){ wrap.remove(); };
  wrap.querySelector('.afn').onchange=function(){ syncAggCol(wrap); };
  document.getElementById('aaggs').appendChild(wrap);
  syncAggCol(wrap);
}
function syncAggCol(wrap){
  const fn=wrap.querySelector('.afn').value;
  wrap.querySelector('.acol').style.display=fn==='count'?'none':'';
}
function readFilters(){
  return [].slice.call(document.querySelectorAll('#afilters .filterrow')).map(function(row){
    const item={
      column:row.querySelector('.fcol').value,
      op:row.querySelector('.fop').value,
      value:row.querySelector('.fval').value
    };
    const v2=row.querySelector('.fval2');
    if(v2 && v2.value) item.value2=v2.value;
    return item;
  }).filter(function(f){ return f.column && f.op; });
}
function readGroups(){
  return [].slice.call(document.querySelectorAll('#agroups .gcol')).map(function(el){ return el.value; }).filter(Boolean);
}
function readAggs(){
  return [].slice.call(document.querySelectorAll('#aaggs .filterrow')).map(function(row){
    const fn=row.querySelector('.afn').value;
    const column=row.querySelector('.acol').value;
    if(fn==='count') return {fn:fn, column:'*'};
    return {fn:fn, column:column};
  }).filter(function(a){ return a.fn && (a.fn==='count' || a.column); });
}
function toggleSql(){
  document.getElementById('sqlbox').classList.toggle('on');
}
function sqlFromBuilder(){
  sqlDirty=false;
  applyAnalyze(analyzePageNo);
}
function analyzePage(delta){
  if(analyzeMode==='folders') return;
  const max=analyzeTotal<=0?0:Math.floor((analyzeTotal-1)/analyzePageSize);
  const next=Math.max(0, Math.min(max, analyzePageNo+delta));
  if(next===analyzePageNo && delta) return;
  if(analyzeMode==='compare') runCompare(next);
  else applyAnalyze(next);
}
function sortAnalyze(col){
  if(analyzeMode==='compare') return;
  if(analyzeOrder && analyzeOrder.column===col && analyzeOrder.dir==='asc') analyzeOrder={column:col, dir:'desc'};
  else analyzeOrder={column:col, dir:'asc'};
  applyAnalyze(0);
}
function cellTxt(v){
  if(v===null||v===undefined) return '';
  if(typeof v==='object') return JSON.stringify(v);
  return String(v);
}
function fmtVal(v){
  if(typeof v==='number' && Number.isFinite(v)){
    if(Math.abs(v-Math.round(v))<1e-9) return String(Math.round(v));
    return (Math.round(v*100)/100).toString();
  }
  return cellTxt(v);
}
function tryJson(s){
  try{ return JSON.parse(s); }catch(e){ return null; }
}
function cellKind(v){
  if(v===null||v===undefined) return '';
  if(typeof v==='object') return 'json';
  const s=String(v).trim();
  if(!s) return '';
  if((s.charAt(0)==='{'||s.charAt(0)==='[') && tryJson(s)!==null) return 'json';
  if(/^<\?xml/i.test(s)) return 'xml';
  if(/<\s*\/?\s*[a-zA-Z!][^>]{0,120}>/.test(s)) return 'html';
  if(s.indexOf('\n')>=0 || s.length>120) return 'text';
  return '';
}
function prettyJson(v){
  if(typeof v==='string'){
    const p=tryJson(v);
    if(p!==null) v=p;
  }
  return JSON.stringify(v, null, 2);
}
function prettyMarkup(html){
  const raw=String(html||'');
  const tokens=raw.replace(/>\s*</g,'>\n<').split('\n');
  let pad=0, out=[];
  const voidRe=/^<(area|base|br|col|embed|hr|img|input|link|meta|param|source|track|wbr)\b/i;
  tokens.forEach(function(line){
    line=line.trim();
    if(!line) return;
    if(/^<\//.test(line)) pad=Math.max(0,pad-1);
    out.push(new Array(pad+1).join('  ')+line);
    if(/^<[a-zA-Z]/.test(line) && !/\/>$/.test(line) && !voidRe.test(line) && !/^<!(doctype|--)/i.test(line)) pad++;
  });
  return out.join('\n');
}
function renderGrid(d){
  lastAnalyze=d;
  const all=d.columns||[];
  const cols=visibleCols(all);
  const head=document.querySelector('#atable thead');
  const body=document.querySelector('#atable tbody');
  const orderCol=analyzeOrder&&analyzeOrder.column;
  const orderDir=analyzeOrder&&analyzeOrder.dir;
  const cmp=!!d.compare;
  document.getElementById('cmpkinds').style.display=cmp?'':'none';
  head.innerHTML='<tr>'+cols.map(function(c){
    const mark=orderCol===c?(orderDir==='desc'?' ↓':' ↑'):'';
    const sortOff=cmp && (c==='Änderung'||c==='Schlüssel'||c==='Diff');
    const click=sortOff?'':' onclick="sortAnalyze(\''+esc(c)+'\')"';
    return '<th class="'+(orderCol===c?'sort':'')+'"'+click+'>'+esc(c)+mark+'</th>';
  }).join('')+'</tr>';
  const rows=(d.rows||[]).map(function(row,ri){
    const chg=row['Änderung']||'';
    const chgCls=chg==='neu'?' chg-neu':chg==='gelöscht'?' chg-del':chg==='geändert'?' chg-chg':'';
    const chgMap=row._chg||{};
    return '<tr>'+cols.map(function(c){
      const v=row[c];
      const field=chgMap[c];
      const fieldCls=field?' chg-field':'';
      if(v===null||v===undefined) return '<td class="null'+chgCls+fieldCls+'">–</td>';
      const kind=cellKind(v);
  const jump=(cellLinks(c, v).length || (d.folder_compare && c==='Tabelle'))?' jump':'';
      const cls=' class="'+(kind?'rich ':'')+(chgCls+fieldCls).trim()+jump+'"'+(kind?' data-kind="'+kind+'"':'');
      const t=fmtVal(v);
      const tip=field?(field.alt+' → '+field.neu):cellTxt(v);
      return '<td'+cls+' data-r="'+ri+'" data-c="'+escAttr(c)+'" title="'+escAttr(tip)+'">'+esc(t)+'</td>';
    }).join('')+'</tr>';
  }).join('');
  let totals='';
  if(d.totals){
    totals='<tr class="totals">'+cols.map(function(c,i){
      if(i===0 && (d.totals[c]===null||d.totals[c]===undefined)) return '<td>'+esc(d.totals._label||'Summe')+'</td>';
      const v=d.totals[c];
      if(v===null||v===undefined) return '<td></td>';
      return '<td>'+esc(fmtVal(v))+'</td>';
    }).join('')+'</tr>';
  }
  body.innerHTML=rows+totals||'<tr><td colspan="'+(cols.length||1)+'">Keine Zeilen</td></tr>';
  analyzeTotal=d.total||0;
  analyzePageNo=d.page||0;
  analyzePageSize=d.page_size||50;
  const from=analyzeTotal?analyzePageNo*analyzePageSize+1:0;
  const to=Math.min(analyzeTotal, (analyzePageNo+1)*analyzePageSize);
  document.getElementById('apageln').textContent=analyzeTotal?(from+'–'+to+' von '+analyzeTotal):'Keine Daten';
  if(cmp){
    const s=d.compare;
    document.getElementById('aresult').textContent='Vergleich: '+fmt(s.neu)+' neu · '+fmt(s['gelöscht'])+' gelöscht · '+fmt(s['geändert'])+' geändert · '+fmt(s.gleich)+' gleich';
  }else{
    document.getElementById('aresult').textContent=(d.grouped?'Gruppen: ':'Zeilen: ')+fmt(analyzeTotal);
  }
  if(!sqlDirty && !cmp) document.getElementById('asql').value=d.sql||'';
  if(d.column_meta&&d.column_meta.length){
    columnMeta=d.column_meta;
    fillDateCols();
  }
  drawChart(d);
}
function queryBody(page){
  const body={
    folder:currentFolder(),
    table:selectedTable.stem,
    search:document.getElementById('asearch').value||'',
    filters:readFilters(),
    group_by:readGroups(),
    aggregations:readAggs(),
    page:page||0,
    date_from:document.getElementById('adatefrom').value||'',
    date_to:document.getElementById('adateto').value||'',
    date_column:document.getElementById('adatecol').value||''
  };
  if(analyzeOrder) body.order=analyzeOrder;
  if(sqlDirty) body.sql=document.getElementById('asql').value||'';
  return body;
}
document.getElementById('atable').addEventListener('click', function(ev){
  const td=ev.target.closest('td');
  if(!td || !lastAnalyze || td.closest('tr.totals')) return;
  const col=td.getAttribute('data-c');
  if(col===null) return;
  const ri=parseInt(td.getAttribute('data-r'),10);
  if(lastAnalyze.folder_compare && col==='Tabelle'){
    openFolderHit(ri);
    return;
  }
  if(ev.shiftKey){
    filterFromCell(ri, col);
    return;
  }
  openCell(ri, col);
});
function openCell(ri, col){
  const row=(lastAnalyze.rows||[])[ri];
  if(!row) return;
  const v=row[col];
  const kind=cellKind(v)||'text';
  let text=cellTxt(v);
  if(kind==='json') text=prettyJson(typeof v==='string'?v:v);
  else if(kind==='html'||kind==='xml') text=prettyMarkup(cellTxt(v));
  popText=text;
  popHtmlOn=false;
  document.getElementById('poptitle').textContent=col;
  document.getElementById('popkind').textContent=kind==='json'?'JSON':kind==='html'?'HTML':kind==='xml'?'XML':'Text';
  document.getElementById('poppre').textContent=text;
  document.getElementById('poppre').className='';
  document.getElementById('popframe').className='popframe';
  document.getElementById('popframe').removeAttribute('srcdoc');
  document.getElementById('pophtmlbtn').style.display=kind==='html'?'':'none';
  const links=cellLinks(col, v);
  popLinkList=links;
  popFilterRi=ri;
  popFilterCol=col;
  const box=document.getElementById('poplinks');
  box.innerHTML=links.map(function(l,i){
    return '<button class="act" type="button" onclick="jumpLink('+ri+','+i+')">Öffnen: '+esc(l.label)+'</button>';
  }).join('');
  const fbtn=document.getElementById('popfilterbtn');
  if(fbtn) fbtn.style.display=canFilterCell(col,v)?'':'none';
  document.getElementById('cellpop').className='pop on';
}
function canFilterCell(col, v){
  if(!col || col==='Änderung'||col==='Schlüssel'||col==='Diff'||col==='Tabelle') return false;
  if(lastAnalyze&&lastAnalyze.folder_compare) return false;
  const kind=cellKind(v);
  if(kind==='json'||kind==='html'||kind==='xml') return false;
  return true;
}
function filterOpenCell(){
  if(popFilterRi===null || !popFilterCol) return;
  filterFromCell(popFilterRi, popFilterCol);
}
function filterFromCell(ri, col){
  if(!selectedTable) return;
  const row=(lastAnalyze&&lastAnalyze.rows||[])[ri];
  if(!row || !canFilterCell(col, row[col])) return;
  const v=row[col];
  closeCell();
  sqlDirty=false;
  if(v===null||v===undefined||v===''){
    addFilter(col,'empty');
    applyAnalyze(0);
    return;
  }
  const text=typeof v==='object'?JSON.stringify(v):String(v);
  const rows=document.querySelectorAll('#afilters .filterrow');
  for(let i=0;i<rows.length;i++){
    const wrap=rows[i];
    if(wrap.querySelector('.fcol').value!==col) continue;
    const op=wrap.querySelector('.fop').value;
    const inp=wrap.querySelector('.fval');
    if(op==='eq'||op==='in'){
      const cur=(inp.value||'').trim();
      if(op==='eq' && cur===text){ applyAnalyze(0); return; }
      if(op==='eq'){
        wrap.querySelector('.fop').value='in';
        inp.value=cur?(cur+', '+text):text;
        syncFilterValue(wrap);
      }else{
        const parts=cur.split(/[\n,;]+/).map(function(s){return s.trim();}).filter(Boolean);
        if(parts.indexOf(text)<0) parts.push(text);
        inp.value=parts.join(', ');
      }
      applyAnalyze(0);
      return;
    }
  }
  addFilter(col,'eq',text);
  applyAnalyze(0);
}
function toggleHtmlPreview(){
  popHtmlOn=!popHtmlOn;
  document.getElementById('poppre').className=popHtmlOn?'off':'';
  const frame=document.getElementById('popframe');
  frame.className='popframe'+(popHtmlOn?' on':'');
  if(popHtmlOn) frame.srcdoc=popText;
}
function copyCell(){
  if(navigator.clipboard) navigator.clipboard.writeText(popText);
}
function closeCell(){
  document.getElementById('cellpop').className='pop';
  document.getElementById('popframe').removeAttribute('srcdoc');
}
document.addEventListener('keydown', function(ev){
  if(ev.key==='Escape') closeCell();
});
async function applyAnalyze(page){
  if(!selectedTable){ alert('Zuerst eine Tabelle wählen.'); return; }
  analyzeMode='query';
  document.getElementById('cmpkinds').style.display='none';
  const seq=++analyzeSeq;
  analyzeBusy=true;
  document.getElementById('aresult').textContent='Lade …';
  try{
    const r=await fetch('/api/analyze/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(queryBody(page))});
    const d=await r.json().catch(function(){return {};});
    if(seq!==analyzeSeq) return;
    if(!r.ok) throw new Error(d.detail||d.error||'Fehler');
    setAnalyzeErr('');
    renderGrid(d);
  }catch(e){
    if(seq!==analyzeSeq) return;
    setAnalyzeErr(e.message||String(e));
    document.getElementById('aresult').textContent='Fehler';
  }finally{
    if(seq===analyzeSeq) analyzeBusy=false;
  }
}
async function exportAnalyze(fmt){
  if(fmt==='png'){ exportAnalyzePng(); return; }
  if(fmt==='befund'){ exportBefund(); return; }
  if(!selectedTable){ alert('Zuerst eine Tabelle wählen.'); return; }
  try{
    let url='/api/analyze/export';
    let body;
    if(analyzeMode==='compare' || (lastAnalyze&&lastAnalyze.compare)){
      url='/api/analyze/compare/export';
      body=compareBody(0);
      body.format=fmt;
      if(lastAnalyze&&lastAnalyze.folder_compare) body.folder_compare=true;
    }else{
      body=queryBody(0);
      body.format=fmt;
    }
    const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(!r.ok){
      const d=await r.json().catch(function(){return {};});
      throw new Error(d.detail||d.error||'Export fehlgeschlagen');
    }
    const blob=await r.blob();
    let name=(selectedTable.display||selectedTable.stem||'export').replace(/[^\w.\-]+/g,'_')+'.'+fmt;
    const cd=r.headers.get('Content-Disposition')||'';
    const m=cd.match(/filename="?([^"]+)"?/i);
    if(m) name=m[1];
    downloadBlob(blob, name);
  }catch(e){
    setAnalyzeErr(e.message||String(e));
  }
}
function downloadBlob(blob, name){
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download=name;
  a.click();
  setTimeout(function(){ URL.revokeObjectURL(a.href); }, 2000);
}
function opLabel(id){
  const o=FILTER_OPS.filter(function(x){return x.id===id;})[0];
  return o?o.label:id;
}
function aggLabel(id){
  const o=AGG_FNS.filter(function(x){return x.id===id;})[0];
  return o?o.label:id;
}
function queryCaptionLines(){
  const lines=[];
  lines.push('Königssturz – Auswertung');
  lines.push('Ordner: '+(currentFolder()||'–'));
  lines.push('Tabelle: '+(selectedTable&&selectedTable.display||'–'));
  if(lastAnalyze&&lastAnalyze.compare){
    lines.push('Vergleich: '+(lastAnalyze.compare.folder||'')+' → '+(lastAnalyze.compare.other||''));
    lines.push('Schlüssel: '+(lastAnalyze.compare.pk&&lastAnalyze.compare.pk.length?lastAnalyze.compare.pk.join(', '):'ganze Zeile'));
  }
  const df=document.getElementById('adatefrom').value;
  const dt=document.getElementById('adateto').value;
  if(df||dt){
    const col=document.getElementById('adatecol').value||(lastAnalyze&&lastAnalyze.date_column)||'created_at';
    lines.push('Zeitraum ('+col+'): '+(df||'…')+' bis '+(dt||'…'));
  }
  const note=(document.getElementById('anote').value||'').trim();
  if(note) lines.push('Notiz: '+note);
  const search=(document.getElementById('asearch').value||'').trim();
  if(search) lines.push('Suche: '+search);
  readFilters().forEach(function(f){
    let s='Nur Zeilen wo: '+f.column+' '+opLabel(f.op);
    if(f.op==='between' && (f.value||f.value2)) s+=' „'+(f.value||'')+'“ bis „'+(f.value2||'')+'“';
    else if((f.op==='in'||f.op==='not_in') && f.value) s+=' '+f.value;
    else if(needsValue(f.op) && f.value) s+=' „'+f.value+'“';
    lines.push(s);
  });
  const groups=readGroups();
  if(groups.length) lines.push('Gruppiert nach: '+groups.map(groupLabel).join(', '));
  readAggs().forEach(function(a){
    if(a.fn==='count' && (!a.column || a.column==='*')) lines.push('Berechnet: Anzahl');
    else lines.push('Berechnet: '+aggLabel(a.fn)+' von '+a.column);
  });
  if(analyzeOrder&&analyzeOrder.column) lines.push('Sortiert: '+analyzeOrder.column+' '+(analyzeOrder.dir==='desc'?'absteigend':'aufsteigend'));
  if(sqlDirty){
    const sql=(document.getElementById('asql').value||'').trim().split('\n').filter(Boolean);
    if(sql.length){
      lines.push('SQL: '+sql[0]);
      sql.slice(1,5).forEach(function(l){ lines.push('      '+l); });
      if(sql.length>5) lines.push('      …');
    }
  }
  const n=lastAnalyze?lastAnalyze.total:0;
  lines.push('Treffer: '+n+(lastAnalyze&&lastAnalyze.grouped?' Gruppen':' Zeilen'));
  lines.push('Exportiert: '+new Date().toLocaleString('de-DE'));
  return lines;
}
function wrapCanvasText(ctx, text, maxW){
  const s=String(text||'');
  if(ctx.measureText(s).width<=maxW) return [s];
  const out=[];
  let cur='';
  const parts=s.split(/(\s+)/);
  parts.forEach(function(w){
    const t=cur+w;
    if(ctx.measureText(t).width<=maxW) cur=t;
    else{
      if(cur.trim()) out.push(cur.replace(/\s+$/,'') );
      if(ctx.measureText(w).width<=maxW) cur=w.replace(/^\s+/,'');
      else{
        let chunk='';
        for(let i=0;i<w.length;i++){
          if(ctx.measureText(chunk+w[i]).width>maxW && chunk){ out.push(chunk); chunk=w[i]; }
          else chunk+=w[i];
        }
        cur=chunk;
      }
    }
  });
  if(cur.trim()) out.push(cur.replace(/\s+$/,''));
  return out.length?out:[''];
}
function ellipsizeCanvas(ctx, text, maxW){
  let s=String(text==null?'':text);
  if(ctx.measureText(s).width<=maxW) return s;
  while(s.length && ctx.measureText(s+'…').width>maxW) s=s.slice(0,-1);
  return s+'…';
}
function exportAnalyzePng(){
  if(!selectedTable){ alert('Zuerst eine Tabelle wählen.'); return; }
  if(!lastAnalyze){ alert('Keine Ergebnisse zum Export. Zuerst Anwenden.'); return; }
  const cols=visibleCols(lastAnalyze.columns||[]);
  const rows=lastAnalyze.rows||[];
  const total=lastAnalyze.total||0;
  if(total>40){
    alert('PNG nur bis 40 Treffer (aktuell '+total+'). Filter enger setzen oder JSON/CSV nutzen.');
    return;
  }
  if(cols.length>16){
    alert('PNG nur bis 16 Spalten (aktuell '+cols.length+').');
    return;
  }
  if(total>rows.length){
    alert('Nicht alle Treffer sind auf dieser Seite. Filter so setzen, dass alles ohne Blättern sichtbar ist.');
    return;
  }
  const pad=28, lineH=18, headH=26, cellH=22, gap=10, maxCol=240, minCol=72;
  const canvas=document.createElement('canvas');
  const ctx=canvas.getContext('2d');
  ctx.font='13px system-ui, Helvetica, Arial, sans-serif';
  const captions=queryCaptionLines();
  const innerW=Math.min(1600, Math.max(720, 90*Math.max(cols.length,1)));
  const wrapped=[];
  captions.forEach(function(line,i){
    ctx.font=i===0?'bold 16px system-ui, Helvetica, Arial, sans-serif':'13px system-ui, Helvetica, Arial, sans-serif';
    wrapCanvasText(ctx, line, innerW).forEach(function(w){ wrapped.push({text:w, title:i===0}); });
  });
  ctx.font='12px system-ui, Helvetica, Arial, sans-serif';
  const widths=cols.map(function(c){
    let w=ctx.measureText(String(c)).width+16;
    rows.forEach(function(row){ w=Math.max(w, ctx.measureText(fmtVal(row[c])).width+16); });
    if(lastAnalyze.totals && lastAnalyze.totals[c]!=null) w=Math.max(w, ctx.measureText(fmtVal(lastAnalyze.totals[c])).width+16);
    return Math.max(minCol, Math.min(maxCol, Math.ceil(w)));
  });
  const tableW=widths.reduce(function(a,b){return a+b;},0);
  const tableRows=rows.length+(lastAnalyze.totals?1:0);
  const captionH=wrapped.reduce(function(h,l){return h+(l.title?22:lineH);},0);
  const width=Math.ceil(pad*2+Math.max(innerW, tableW));
  const height=Math.ceil(pad+captionH+gap+headH+tableRows*cellH+pad);
  if(width>4200 || height>4200){
    alert('Das Bild wäre zu groß ('+width+'×'+height+' px). Weniger Spalten oder Zeilen wählen.');
    return;
  }
  const dpr=Math.min(2, window.devicePixelRatio||1);
  canvas.width=Math.ceil(width*dpr);
  canvas.height=Math.ceil(height*dpr);
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.fillStyle='#e8e4dc';
  ctx.fillRect(0,0,width,height);
  ctx.fillStyle='#111';
  ctx.textBaseline='top';
  let y=pad;
  wrapped.forEach(function(l){
    ctx.font=l.title?'bold 16px system-ui, Helvetica, Arial, sans-serif':'13px system-ui, Helvetica, Arial, sans-serif';
    ctx.fillText(l.text, pad, y);
    y+=l.title?22:lineH;
  });
  y+=gap;
  const tableX=pad;
  const drawRow=function(values, header, totals){
    let x=tableX;
    ctx.fillStyle=header||totals?'#efeae2':'#fff';
    ctx.fillRect(x, y, tableW, header?headH:cellH);
    ctx.strokeStyle='#c4bfb4';
    ctx.strokeRect(x, y, tableW, header?headH:cellH);
    ctx.fillStyle='#111';
    ctx.font=(header||totals)?'bold 12px system-ui, Helvetica, Arial, sans-serif':'12px system-ui, Helvetica, Arial, sans-serif';
    ctx.textBaseline='middle';
    cols.forEach(function(c,i){
      const txt=ellipsizeCanvas(ctx, values[i], widths[i]-10);
      ctx.fillText(txt, x+6, y+(header?headH:cellH)/2);
      x+=widths[i];
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x, y+(header?headH:cellH));
      ctx.stroke();
    });
    y+=header?headH:cellH;
  };
  drawRow(cols, true, false);
  rows.forEach(function(row){
    drawRow(cols.map(function(c){ return fmtVal(row[c]); }), false, false);
  });
  if(lastAnalyze.totals){
    drawRow(cols.map(function(c,i){
      const v=lastAnalyze.totals[c];
      if(i===0 && (v===null||v===undefined)) return lastAnalyze.totals._label||'Summe';
      if(v===null||v===undefined) return '';
      return fmtVal(v);
    }), false, true);
  }
  const stamp=new Date().toISOString().slice(0,19).replace(/[:T]/g,'-');
  const name=(selectedTable.display||'tabelle').replace(/[^\w.\-]+/g,'_')+'_'+stamp+'.png';
  canvas.toBlob(function(blob){
    if(!blob){ setAnalyzeErr('PNG konnte nicht erzeugt werden.'); return; }
    downloadBlob(blob, name);
  }, 'image/png');
}
function fillCompareSelect(){
  const sel=document.getElementById('acompare');
  const cur=currentFolder();
  const others=backups.filter(function(b){ return b.name!==cur; });
  const prev=sel.value;
  if(!others.length){
    sel.innerHTML='<option value="">Weitere Sicherung nötig</option>';
    return;
  }
  sel.innerHTML=others.map(function(b){
    return '<option value="'+esc(b.name)+'">'+esc(b.name)+'</option>';
  }).join('');
  if(prev && others.some(function(b){return b.name===prev;})) sel.value=prev;
}
function readCompareKinds(){
  const on=[].slice.call(document.querySelectorAll('#cmpkinds .ckind:checked')).map(function(x){return x.value;});
  return on.length?on:['neu','gelöscht','geändert'];
}
function compareBody(page){
  return {
    folder:currentFolder(),
    other:document.getElementById('acompare').value||'',
    table:selectedTable.stem,
    page:page||0,
    kinds:readCompareKinds()
  };
}
async function runCompare(page){
  if(!selectedTable){ alert('Zuerst eine Tabelle wählen.'); return; }
  const other=document.getElementById('acompare').value;
  if(!other){ alert('Eine zweite Sicherung unter „Vergleich mit“ wählen.'); return; }
  analyzeMode='compare';
  const seq=++analyzeSeq;
  analyzeBusy=true;
  document.getElementById('aresult').textContent='Vergleiche …';
  try{
    const r=await fetch('/api/analyze/compare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(compareBody(page))});
    const d=await r.json().catch(function(){return {};});
    if(seq!==analyzeSeq) return;
    if(!r.ok) throw new Error(d.detail||d.error||'Fehler');
    setAnalyzeErr('');
    renderGrid(d);
  }catch(e){
    if(seq!==analyzeSeq) return;
    setAnalyzeErr(e.message||String(e));
    document.getElementById('aresult').textContent='Fehler';
    analyzeMode='query';
  }finally{
    if(seq===analyzeSeq) analyzeBusy=false;
  }
}
async function searchAllTables(){
  const folder=currentFolder();
  const q=(document.getElementById('gsearch').value||'').trim();
  if(!folder){ alert('Zuerst einen Ordner wählen.'); return; }
  if(q.length<2){ alert('Mindestens 2 Zeichen suchen.'); return; }
  const box=document.getElementById('globbox');
  const rows=document.getElementById('globrows');
  box.className='list globbox on';
  rows.innerHTML='<div class="rowl"><span>Suche …</span><span></span></div>';
  try{
    const d=await analyzeGet('/api/analyze/search?folder='+encodeURIComponent(folder)+'&q='+encodeURIComponent(q));
    const hits=d.tables||[];
    document.getElementById('astatus').textContent=hits.length?(d.hit_count+' Treffer in '+hits.length+' Tabellen'):'Keine Treffer für „'+q+'“';
    rows.innerHTML=hits.map(function(t){
      const sample=(t.samples&&t.samples[0])?t.samples[0]:'';
      return '<button type="button" class="rowl" title="'+escAttr(sample)+'" onclick="openGlobalHit(\''+esc(t.stem)+'\')"><span>'+esc(t.display)+'</span><span>'+fmt(t.hits)+'</span></button>';
    }).join('')||'<div class="rowl"><span>Keine Treffer</span><span></span></div>';
  }catch(e){
    setAnalyzeErr(e.message||String(e));
    rows.innerHTML='<div class="rowl"><span>Fehler</span><span></span></div>';
  }
}
function openGlobalHit(stem){
  const q=(document.getElementById('gsearch').value||'').trim();
  selectTable(stem, {search:q, filters:[], groups:[], aggs:[], order:null, sql:''});
}
function hiddenFor(){
  const stem=selectedTable&&selectedTable.stem;
  return (hiddenCols[stem]||[]).slice();
}
function isHidden(col){
  if(col==='Änderung'||col==='Schlüssel'||col==='Diff') return false;
  return hiddenFor().indexOf(col)>=0;
}
function visibleCols(cols){
  const vis=(cols||[]).filter(function(c){ return !isHidden(c); });
  return vis.length?vis:cols;
}
function persistHidden(){
  try{ localStorage.setItem('analyzeHiddenCols', JSON.stringify(hiddenCols)); }catch(e){}
}
function toggleCols(){
  document.getElementById('colpick').classList.toggle('on');
  renderColPick();
}
function renderColPick(){
  const box=document.getElementById('colpick');
  const cols=(lastAnalyze&&lastAnalyze.columns)||columnMeta.map(function(c){return c.name||c;});
  if(!cols.length){ box.innerHTML=''; return; }
  box.innerHTML='<button class="act" type="button" onclick="setAllCols(true)">Alle</button>'
    +'<button class="act" type="button" onclick="setAllCols(false)">Keine</button>'
    +cols.map(function(c){
      if(c==='Änderung'||c==='Schlüssel'||c==='Diff') return '';
      return '<label><input type="checkbox" '+(isHidden(c)?'':'checked')+' onchange="toggleCol(\''+esc(c)+'\',this.checked)"/> '+esc(c)+'</label>';
    }).join('');
}
function toggleCol(col, on){
  if(!selectedTable) return;
  const stem=selectedTable.stem;
  let list=hiddenFor();
  list=list.filter(function(c){ return c!==col; });
  if(!on) list.push(col);
  hiddenCols[stem]=list;
  persistHidden();
  if(lastAnalyze) renderGrid(lastAnalyze);
  renderColPick();
}
function setAllCols(on){
  if(!selectedTable) return;
  const cols=(lastAnalyze&&lastAnalyze.columns)||columnMeta.map(function(c){return c.name||c;});
  hiddenCols[selectedTable.stem]=on?[]:cols.filter(function(c){ return c!=='Änderung'&&c!=='Schlüssel'&&c!=='Diff'; });
  persistHidden();
  if(lastAnalyze) renderGrid(lastAnalyze);
  renderColPick();
}
async function loadFilterSuggest(wrap){
  const col=wrap.querySelector('.fcol').value;
  const list=wrap.querySelector('datalist');
  if(!list||!col||!selectedTable) return;
  const key=currentFolder()+'|'+selectedTable.stem+'|'+col;
  try{
    if(!distinctCache[key]){
      distinctCache[key]=await analyzeGet('/api/analyze/distinct?folder='+encodeURIComponent(currentFolder())+'&table='+encodeURIComponent(selectedTable.stem)+'&column='+encodeURIComponent(col));
    }
    const d=distinctCache[key];
    const vals=(d.values||[]);
    list.innerHTML=vals.map(function(v){
      const label=(v.empty?'(leer)':v.value)+' · '+v.n;
      return '<option value="'+escAttr(v.value)+'" label="'+escAttr(label)+'"></option>';
    }).join('');
    wrap.querySelector('.fval').title=d.useful?(d.unique+' verschiedene Werte, häufigste vorgeschlagen'):'Keine häufigen Werte zum Vorschlagen';
  }catch(e){
    list.innerHTML='';
  }
}
function readSaved(){
  try{ return JSON.parse(localStorage.getItem('analyzeSavedQueries')||'[]')||[]; }
  catch(e){ return []; }
}
function writeSaved(items){
  try{ localStorage.setItem('analyzeSavedQueries', JSON.stringify(items)); }catch(e){}
}
function suggestedQueries(){
  const cols=columnMeta.map(function(c){ return c.name||c; });
  const out=[];
  if(cols.indexOf('status')>=0){
    out.push({name:'Status Offen', search:'', filters:[{column:'status',op:'eq',value:'Offen'}], groups:[], aggs:[]});
    out.push({name:'Status Gebucht', search:'', filters:[{column:'status',op:'eq',value:'Gebucht'}], groups:[], aggs:[]});
  }
  if(cols.indexOf('recipient_email')>=0){
    out.push({name:'Mit E-Mail', search:'', filters:[{column:'recipient_email',op:'not_empty',value:''}], groups:[], aggs:[]});
  }
  if(cols.indexOf('mitgliedsnummer')>=0){
    out.push({name:'Nach Mitgliedsnummer gruppiert', search:'', filters:[], groups:['mitgliedsnummer'], aggs:[{fn:'count',column:'*'}]});
  }
  const amount=pickAmountCol();
  if(amount && cols.indexOf('status')>=0){
    out.push({name:'Summe nach Status', search:'', filters:[], groups:['status'], aggs:[{fn:'sum',column:amount}]});
  }
  const dateCol=cols.indexOf('created_at')>=0?'created_at':cols.filter(isDateishCol)[0];
  if(dateCol){
    out.push({name:'Summe nach Monat', search:'', filters:[], groups:[dateCol+':month'], aggs:amount?[{fn:'sum',column:amount}]:[{fn:'count',column:'*'}]});
  }
  return out;
}
function fillSavedSelect(){
  const sel=document.getElementById('asaved');
  if(!sel) return;
  const stem=selectedTable&&selectedTable.stem;
  const saved=readSaved().filter(function(q){ return !q.table || q.table===stem; });
  const sug=suggestedQueries();
  let html='<option value="">Abfrage wählen …</option>';
  if(saved.length){
    html+='<option disabled>— Gespeichert —</option>';
    saved.forEach(function(q,i){
      html+='<option value="s'+i+'">'+esc(q.name||'Ohne Namen')+'</option>';
    });
  }
  if(sug.length){
    html+='<option disabled>— Vorschläge —</option>';
    sug.forEach(function(q,i){
      html+='<option value="p'+i+'">'+esc(q.name)+'</option>';
    });
  }
  sel.innerHTML=html;
}
function onSavedPick(){
  const sel=document.getElementById('asaved');
  const v=sel.value;
  if(!v) return;
  const stem=selectedTable&&selectedTable.stem;
  if(v.charAt(0)==='s'){
    const saved=readSaved().filter(function(q){ return !q.table || q.table===stem; });
    const q=saved[parseInt(v.slice(1),10)];
    if(q) applyPreset(q);
  }else if(v.charAt(0)==='p'){
    const q=suggestedQueries()[parseInt(v.slice(1),10)];
    if(q) applyPreset(q);
  }
}
function snapshotQuery(){
  return {
    name:'',
    table:selectedTable&&selectedTable.stem,
    display:selectedTable&&selectedTable.display,
    search:document.getElementById('asearch').value||'',
    filters:readFilters(),
    groups:readGroups(),
    aggs:readAggs(),
    order:analyzeOrder,
    sql:sqlDirty?(document.getElementById('asql').value||''):'',
    date_from:document.getElementById('adatefrom').value||'',
    date_to:document.getElementById('adateto').value||'',
    date_column:document.getElementById('adatecol').value||'',
    date_grain:(document.getElementById('adategrain')&&document.getElementById('adategrain').value)||'day',
    note:document.getElementById('anote').value||''
  };
}
function applyPreset(preset){
  if(!preset) return;
  if(preset.table && selectedTable && preset.table!==selectedTable.stem){
    selectTable(preset.table, preset);
    return;
  }
  resetBuilder(true);
  fillDateCols();
  if(preset.date_grain && document.getElementById('adategrain')){
    document.getElementById('adategrain').value=preset.date_grain;
  }
  syncDateGrain();
  document.getElementById('asearch').value=preset.search||'';
  document.getElementById('adatefrom').value=preset.date_from||'';
  document.getElementById('adateto').value=preset.date_to||'';
  if(preset.date_column) document.getElementById('adatecol').value=preset.date_column;
  if(preset.note) document.getElementById('anote').value=preset.note;
  (preset.filters||[]).forEach(function(f){ addFilter(f.column, f.op, f.value, f.value2); });
  (preset.groups||[]).forEach(function(c){ addGroup(c); });
  (preset.aggs||[]).forEach(function(a){ addAgg(a.fn, a.column==='*'?'':a.column); });
  analyzeOrder=preset.order||null;
  if(preset.sql){
    sqlDirty=true;
    document.getElementById('asql').value=preset.sql;
    document.getElementById('sqlbox').className='sqlbox on';
  }
  applyAnalyze(0);
}
function saveCurrentQuery(){
  if(!selectedTable){ alert('Zuerst eine Tabelle wählen.'); return; }
  const name=prompt('Name der Abfrage:','');
  if(!name) return;
  const item=snapshotQuery();
  item.name=name.trim();
  const items=readSaved();
  const idx=items.findIndex(function(q){ return q.name===item.name && q.table===item.table; });
  if(idx>=0) items[idx]=item;
  else items.push(item);
  writeSaved(items);
  fillSavedSelect();
}
function deleteSavedQuery(){
  const sel=document.getElementById('asaved');
  const v=sel.value;
  if(!v || v.charAt(0)!=='s'){ alert('Zuerst eine gespeicherte Abfrage wählen.'); return; }
  const stem=selectedTable&&selectedTable.stem;
  const saved=readSaved();
  const visible=saved.filter(function(q){ return !q.table || q.table===stem; });
  const item=visible[parseInt(v.slice(1),10)];
  if(!item) return;
  writeSaved(saved.filter(function(q){ return q!==item; }));
  fillSavedSelect();
}
try{ hiddenCols=JSON.parse(localStorage.getItem('analyzeHiddenCols')||'{}')||{}; }
catch(e){ hiddenCols={}; }
function onCompareKind(){
  if(analyzeMode==='folders') runFolderCompare();
  else if(analyzeMode==='compare') runCompare(0);
}
async function runFolderCompare(){
  const other=document.getElementById('acompare').value;
  if(!currentFolder()){ alert('Zuerst einen Ordner wählen.'); return; }
  if(!other){ alert('Eine zweite Sicherung unter „Vergleich mit“ wählen.'); return; }
  analyzeMode='folders';
  const seq=++analyzeSeq;
  analyzeBusy=true;
  document.getElementById('aresult').textContent='Vergleiche Ordner …';
  document.getElementById('agridlegend').textContent=' 3. Ordnervergleich';
  try{
    const r=await fetch('/api/analyze/compare-folders',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folder:currentFolder(), other:other, kinds:readCompareKinds()})});
    const d=await r.json().catch(function(){return {};});
    if(seq!==analyzeSeq) return;
    if(!r.ok) throw new Error(d.detail||d.error||'Fehler');
    setAnalyzeErr('');
    renderGrid(d);
  }catch(e){
    if(seq!==analyzeSeq) return;
    setAnalyzeErr(e.message||String(e));
    document.getElementById('aresult').textContent='Fehler';
    analyzeMode='query';
  }finally{
    if(seq===analyzeSeq) analyzeBusy=false;
  }
}
function openFolderHit(ri){
  const row=(lastAnalyze.rows||[])[ri];
  if(!row||!row.stem) return;
  const t=analyzeTables.filter(function(x){return x.stem===row.stem;})[0];
  if(!t){ alert('Diese Tabelle gibt es in der aktuellen Sicherung nicht.'); return; }
  pendingCompare=true;
  selectTable(row.stem);
}
function findLinkTable(name, schema){
  const low=String(name||'').toLowerCase();
  const schemaLow=String(schema||'').toLowerCase();
  let hit=analyzeTables.filter(function(t){
    return (t.name||'').toLowerCase()===low && (!schemaLow || (t.schema||'').toLowerCase()===schemaLow);
  })[0];
  if(hit) return hit;
  return analyzeTables.filter(function(t){
    return (t.stem||'').toLowerCase().indexOf(low)>=0 || (t.display||'').toLowerCase().indexOf('.'+low)>=0;
  })[0];
}
function cellLinks(col, value){
  if(value===null||value===undefined||value==='') return [];
  if(typeof value==='object') return [];
  if(analyzeMode==='compare'||analyzeMode==='folders'||(lastAnalyze&&lastAnalyze.folder_compare)) return [];
  const text=String(value);
  if(!text) return [];
  const out=[];
  const seen={};
  function add(stem, column, label){
    const k=stem+'|'+column;
    if(seen[k]) return;
    if(selectedTable && stem===selectedTable.stem && column===col) return;
    seen[k]=1;
    out.push({stem:stem, column:column, value:text, label:label});
  }
  ((selectedTable&&selectedTable.foreign_keys)||[]).forEach(function(fk){
    if(fk.column!==col) return;
    const t=findLinkTable(fk.ref_table, fk.ref_schema);
    if(t) add(t.stem, fk.ref_column||'id', t.display+' · '+(fk.ref_column||'id'));
  });
  const interesting=col==='mitgliedsnummer'||col==='invoice_id'||col==='invoice_number'||col==='id'||/_id$/.test(col);
  if(interesting){
    analyzeTables.forEach(function(t){
      if(selectedTable && t.stem===selectedTable.stem) return;
      const names=(t.columns||[]).map(function(c){ return c.name||c; });
      if(names.indexOf(col)>=0) add(t.stem, col, t.display+' · '+col);
    });
  }
  if(/_id$/.test(col) && col!=='id'){
    const base=col.slice(0,-3).toLowerCase();
    analyzeTables.forEach(function(t){
      const n=(t.name||'').toLowerCase();
      if(n===base||n===base+'s'||n.slice(-base.length-1)==='_'+base||n.indexOf(base)>=0){
        const pk=(t.primary_key&&t.primary_key[0])||'id';
        add(t.stem, pk, t.display+' · '+pk);
      }
    });
  }
  return out.slice(0,6);
}
function jumpLink(ri, idx){
  const row=(lastAnalyze.rows||[])[ri];
  const link=popLinkList[idx];
  if(!row||!link) return;
  closeCell();
  selectTable(link.stem, {search:'', filters:[{column:link.column, op:'eq', value:link.value}], groups:[], aggs:[]});
}
function drawChart(d){
  const box=document.getElementById('achart');
  const cv=document.getElementById('achartcv');
  if(!box||!cv) return;
  if(!d || !d.grouped || !(d.rows||[]).length){ box.className='chartbox'; return; }
  const cols=d.columns||[];
  const num=cols.filter(function(c){
    const low=String(c).toLowerCase();
    return low==='anzahl'||low.indexOf('anzahl_')===0||low.indexOf('summe_')===0;
  });
  const cat=cols.filter(function(c){ return num.indexOf(c)<0; })[0];
  const val=num[0];
  if(!cat||!val){ box.className='chartbox'; return; }
  const rows=(d.rows||[]).slice(0,24).map(function(r){
    return {label:String(r[cat]==null?'–':r[cat]), n:Number(r[val])||0};
  });
  const max=Math.max.apply(null, rows.map(function(r){return r.n;}).concat([1]));
  box.className='chartbox on';
  const w=Math.max(box.clientWidth||640, 320);
  const h=140;
  const dpr=Math.min(2, window.devicePixelRatio||1);
  cv.width=Math.ceil(w*dpr);
  cv.height=Math.ceil(h*dpr);
  cv.style.width=w+'px';
  cv.style.height=h+'px';
  const ctx=cv.getContext('2d');
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.fillStyle='#f7f4ee';
  ctx.fillRect(0,0,w,h);
  const gap=6;
  const barW=Math.max(8,(w-24)/rows.length-gap);
  ctx.font='11px system-ui, Helvetica, Arial, sans-serif';
  rows.forEach(function(r,i){
    const bh=Math.max(2,(h-36)*r.n/max);
    const x=12+i*(barW+gap);
    ctx.fillStyle='#3d6b99';
    ctx.fillRect(x, h-20-bh, barW, bh);
    ctx.fillStyle='#111';
    ctx.textAlign='center';
    ctx.fillText(String(r.n), x+barW/2, h-24-bh);
    ctx.fillText(ellipsizeCanvas(ctx, r.label, barW+8), x+barW/2, h-6);
  });
}
function mdTable(cols, rows){
  if(!cols||!cols.length) return [];
  const out=['| '+cols.join(' | ')+' |','| '+cols.map(function(){return '---';}).join(' | ')+' |'];
  (rows||[]).forEach(function(row){
    out.push('| '+cols.map(function(c){
      return String(fmtVal(row[c])).replace(/\|/g,'\\|').replace(/\n/g,' ');
    }).join(' | ')+' |');
  });
  return out;
}
function readAkte(){
  try{ return JSON.parse(localStorage.getItem('analyzeAkte')||'[]')||[]; }
  catch(e){ return []; }
}
function writeAkte(items){
  try{ localStorage.setItem('analyzeAkte', JSON.stringify(items)); }catch(e){}
  renderAkteHint();
}
function renderAkteHint(){
  const el=document.getElementById('aktehint');
  if(!el) return;
  const n=readAkte().length;
  if(!n){ el.textContent='Akte leer'; return; }
  el.innerHTML=n+' in der Akte <button class="act" type="button" onclick="clearAkte()">Leeren</button>';
}
function addToAkte(){
  if(!lastAnalyze){ alert('Keine Ergebnisse. Zuerst Anwenden.'); return; }
  const cols=visibleCols(lastAnalyze.columns||[]);
  const rows=(lastAnalyze.rows||[]).slice(0,40).map(function(row){
    const slim={};
    cols.forEach(function(c){ slim[c]=row[c]; });
    return slim;
  });
  const items=readAkte();
  items.push({
    t:Date.now(),
    folder:currentFolder(),
    table:selectedTable&&selectedTable.display,
    note:(document.getElementById('anote').value||'').trim(),
    lines:queryCaptionLines(),
    cols:cols,
    rows:rows,
    total:lastAnalyze.total||0,
    more:(lastAnalyze.total||0)>rows.length
  });
  if(items.length>30) items.splice(0, items.length-30);
  writeAkte(items);
}
function clearAkte(){
  if(!readAkte().length) return;
  if(!confirm('Befund-Akte leeren?')) return;
  writeAkte([]);
}
function exportBefund(){
  if(!lastAnalyze && !readAkte().length){ alert('Keine Ergebnisse zum Export. Zuerst Anwenden oder etwas zur Akte legen.'); return; }
  const md=['# Königssturz – Befund',''];
  const akte=readAkte();
  if(akte.length){
    md.push('Akte mit '+akte.length+(akte.length===1?' Eintrag.':' Einträgen.'),'');
    akte.forEach(function(item,i){
      const when=item.t?new Date(item.t).toLocaleString('de-DE'):'';
      md.push('## '+(i+1)+'. '+(item.table||'Tabelle')+(when?' ('+when+')':''),'');
      (item.lines||[]).forEach(function(l){ md.push('- '+l); });
      if(item.note) md.push('- Notiz: '+item.note);
      md.push('');
      mdTable(item.cols||[], item.rows||[]).forEach(function(l){ md.push(l); });
      if(item.more) md.push('','Nur die gespeicherte Seite. Alle Zeilen: JSON oder CSV exportieren.');
      md.push('');
    });
  }
  if(lastAnalyze){
    const cols=visibleCols(lastAnalyze.columns||[]);
    const rows=lastAnalyze.rows||[];
    md.push(akte.length?'## Aktuelle Ansicht':'## Daten','');
    queryCaptionLines().forEach(function(l){ md.push('- '+l); });
    md.push('');
    mdTable(cols, rows).forEach(function(l){ md.push(l); });
    if((lastAnalyze.total||0)>rows.length){
      md.push('','Nur die aktuelle Seite. Alle Zeilen: JSON oder CSV exportieren.');
    }
  }
  const stamp=new Date().toISOString().slice(0,19).replace(/[:T]/g,'-');
  const name=(selectedTable&&selectedTable.display||'befund').replace(/[^\w.\-]+/g,'_')+'_befund_'+stamp+'.md';
  downloadBlob(new Blob([md.join('\n')],{type:'text/markdown;charset=utf-8'}), name);
}
function bindSplit(barId, boxId, storageKey){
  const bar=document.getElementById(barId);
  const box=document.getElementById(boxId);
  if(!bar||!box) return;
  const saved=parseInt(localStorage.getItem(storageKey)||'',10);
  if(saved>=180){
    box.style.flexBasis=saved+'px';
    box.style.width=saved+'px';
  }
  bar.addEventListener('mousedown', function(e){
    const startX=e.clientX;
    const startW=box.getBoundingClientRect().width;
    bar.classList.add('drag');
    function move(ev){
      const w=Math.max(180, Math.min(window.innerWidth*0.7, startW+(ev.clientX-startX)));
      box.style.flexBasis=w+'px';
      box.style.width=w+'px';
    }
    function up(){
      bar.classList.remove('drag');
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
      localStorage.setItem(storageKey, String(parseInt(box.style.flexBasis,10)||startW));
    }
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
    e.preventDefault();
  });
}
function initSplit(){
  bindSplit('splitbar','tlistbox','analyzeSplit');
  bindSplit('vasplitbar','valistbox','vaSplit');
}
document.getElementById('varows').addEventListener('click', function(ev){
  if(ev.target.closest('.vaselbox')||ev.target.closest('.vasel')){
    const box=ev.target.closest('.rowl');
    if(!box) return;
    const cb=box.querySelector('.vaselbox');
    if(!cb) return;
    if(ev.target!==cb) cb.checked=!cb.checked;
    const key=box.getAttribute('data-kind')+'/'+box.getAttribute('data-id');
    if(cb.checked) vaSelected[key]=true;
    else delete vaSelected[key];
    renderVaList();
    return;
  }
  const row=ev.target.closest('div.rowl');
  if(!row) return;
  openVa(row.getAttribute('data-kind'), row.getAttribute('data-id'));
});
document.getElementById('vabody').addEventListener('click', function(ev){
  const btn=ev.target.closest('button[data-folie]');
  if(btn){
    ev.preventDefault();
    post('/api/va/file/open',{kind:btn.getAttribute('data-kind'),id:btn.getAttribute('data-id'),name:btn.getAttribute('data-folie')});
    return;
  }
  const row=ev.target.closest('.tx');
  if(!row) return;
  const t=row.getAttribute('data-t');
  if(t==null||t==='') return;
  seekVa(t);
});
vaRenderHead();
packRenderHead();
initSplit();
renderAkteHint();
['vapackpublic','vapackintern','vapackfrom','vapackto'].forEach(function(id){
  const el=document.getElementById(id);
  if(el) el.addEventListener('change', renderVaPack);
});
</script>
</body></html>
"""


app = FastAPI(title="Königssturz – Beweissicherung")


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(PAGE, headers={"Cache-Control": "no-store, no-cache, must-revalidate"})


@app.get("/api/state")
def api_state():
    return STATE.snapshot()


def _run(fn):
    try:
        fn()
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/curl")
def api_curl(body: Dict[str, Any]):
    return _run(lambda: STATE.set_curl(body.get("curl") or ""))


@app.post("/api/login")
def api_login(body: Dict[str, Any]):
    def job():
        if body.get("curl"):
            STATE.set_curl(body.get("curl") or "")
        STATE.login(body.get("email") or "", body.get("password") or "", body.get("totp") or "")

    return _run(job)


@app.post("/api/schemas")
def api_schemas(body: Dict[str, Any]):
    def job():
        if body.get("curl") or not STATE.base_url:
            STATE.set_curl(body.get("curl") or STATE.curl)
        STATE.fetch_schemas()

    return _run(job)


@app.post("/api/tables")
def api_tables(body: Dict[str, Any]):
    def job():
        selected = body.get("selected") or []
        if selected:
            STATE.selected = [str(x) for x in selected]
        STATE.fetch_tables()

    return _run(job)


@app.post("/api/export")
def api_export(body: Dict[str, Any]):
    def job():
        if body.get("curl") or not STATE.base_url:
            STATE.set_curl(body.get("curl") or STATE.curl)
        STATE.start_export(body.get("test_run"))

    return _run(job)


@app.post("/api/resume")
def api_resume(body: Dict[str, Any]):
    return _run(lambda: STATE.resume(body.get("curl") or ""))


@app.post("/api/mail/list")
def api_mail_list(body: Dict[str, Any]):
    return _run(
        lambda: STATE.mail_list(
            body.get("user") or "",
            body.get("password") or "",
            body.get("host") or kf.IONOS_IMAP_HOST,
            body.get("port") or kf.IONOS_IMAP_PORT,
        )
    )


@app.post("/api/mail/save")
def api_mail_save(body: Dict[str, Any]):
    return _run(
        lambda: STATE.mail_save(
            body.get("user") or "",
            body.get("password") or "",
            body.get("host") or kf.IONOS_IMAP_HOST,
            body.get("port") or kf.IONOS_IMAP_PORT,
            selected=body.get("selected"),
        )
    )


@app.post("/api/mail/page")
def api_mail_page(body: Dict[str, Any]):
    return _run(lambda: STATE.set_browse_page(delta=body.get("delta") or 0, page=body.get("page")))


def _analyze(fn):
    try:
        return fn()
    except ka.AnalyzeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/analyze/backups")
def api_analyze_backups():
    return {"backups": ka.list_backups()}


@app.get("/api/analyze/tables")
def api_analyze_tables(folder: str = Query(...)):
    return _analyze(lambda: {"folder": folder, "tables": ka.list_tables(folder)})


@app.get("/api/analyze/preview")
def api_analyze_preview(
    folder: str = Query(...), table: str = Query(...), page: int = 0
):
    return _analyze(lambda: ka.preview(folder, table, page=page))


@app.get("/api/analyze/distinct")
def api_analyze_distinct(
    folder: str = Query(...), table: str = Query(...), column: str = Query(...), limit: int = 60
):
    return _analyze(lambda: ka.distinct_values(folder, table, column, limit=limit))


@app.get("/api/analyze/search")
def api_analyze_search(folder: str = Query(...), q: str = Query(...)):
    return _analyze(lambda: ka.search_all(folder, q))


@app.post("/api/analyze/query")
def api_analyze_query(body: Dict[str, Any]):
    def job():
        return ka.run_query(
            folder=body.get("folder") or "",
            table=body.get("table") or "",
            search=body.get("search") or "",
            filters=body.get("filters") or [],
            group_by=body.get("group_by") or [],
            aggregations=body.get("aggregations") or [],
            order=body.get("order"),
            sql=body.get("sql") or "",
            page=body.get("page") or 0,
            page_size=body.get("page_size") or ka.PAGE_SIZE,
            date_from=body.get("date_from") or "",
            date_to=body.get("date_to") or "",
            date_column=body.get("date_column") or "",
        )

    return _analyze(job)


@app.post("/api/analyze/export")
def api_analyze_export(body: Dict[str, Any]):
    try:
        payload, filename, media = ka.export_bytes(
            folder=body.get("folder") or "",
            table=body.get("table") or "",
            fmt=body.get("format") or "json",
            search=body.get("search") or "",
            filters=body.get("filters") or [],
            group_by=body.get("group_by") or [],
            aggregations=body.get("aggregations") or [],
            order=body.get("order"),
            sql=body.get("sql") or "",
            date_from=body.get("date_from") or "",
            date_to=body.get("date_to") or "",
            date_column=body.get("date_column") or "",
        )
    except ka.AnalyzeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return Response(
        content=payload,
        media_type=media,
        headers={"Content-Disposition": 'attachment; filename="%s"' % filename},
    )


@app.post("/api/analyze/compare")
def api_analyze_compare(body: Dict[str, Any]):
    def job():
        return ka.compare_tables(
            folder=body.get("folder") or "",
            other=body.get("other") or "",
            table=body.get("table") or "",
            page=body.get("page") or 0,
            page_size=body.get("page_size") or ka.PAGE_SIZE,
            kinds=body.get("kinds") or [],
        )

    return _analyze(job)


@app.post("/api/analyze/compare-folders")
def api_analyze_compare_folders(body: Dict[str, Any]):
    def job():
        return ka.compare_folders(
            folder=body.get("folder") or "",
            other=body.get("other") or "",
            kinds=body.get("kinds") or [],
        )

    return _analyze(job)


@app.post("/api/analyze/compare/export")
def api_analyze_compare_export(body: Dict[str, Any]):
    try:
        if body.get("folder_compare"):
            payload, filename, media = ka.export_folder_compare_bytes(
                folder=body.get("folder") or "",
                other=body.get("other") or "",
                fmt=body.get("format") or "json",
                kinds=body.get("kinds") or [],
            )
        else:
            payload, filename, media = ka.export_compare_bytes(
                folder=body.get("folder") or "",
                other=body.get("other") or "",
                table=body.get("table") or "",
                fmt=body.get("format") or "json",
                kinds=body.get("kinds") or [],
            )
    except ka.AnalyzeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return Response(
        content=payload,
        media_type=media,
        headers={"Content-Disposition": 'attachment; filename="%s"' % filename},
    )


@app.post("/api/va/curl")
def api_va_curl(body: Dict[str, Any]):
    return _run(lambda: STATE.set_va_curl(body.get("curl") or ""))


@app.post("/api/va/preview")
def api_va_preview(body: Dict[str, Any]):
    return _run(
        lambda: STATE.start_va_preview(body.get("kinds") or [], body.get("curl") or "")
    )


@app.post("/api/va/download")
def api_va_download(body: Dict[str, Any]):
    return _run(
        lambda: STATE.start_va_download(body.get("kinds") or [], body.get("curl") or "")
    )


@app.post("/api/va/stop")
def api_va_stop():
    return _run(STATE.stop_va)


@app.get("/api/va/local")
def api_va_local():
    return va.list_local()


@app.get("/api/va/item")
def api_va_item(kind: str, item_id: str = Query(..., alias="id"), full: bool = Query(False)):
    if kind not in va.KINDS:
        raise HTTPException(status_code=400, detail="Unbekannte Quelle.")
    try:
        return va.item_for_ui(kind, item_id, include_transcript=full)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/va/item/delete")
def api_va_item_delete(body: Dict[str, Any]):
    raw_items = body.get("items")
    if isinstance(raw_items, list) and raw_items:
        items = [{"kind": str(it.get("kind") or ""), "id": str(it.get("id") or "")} for it in raw_items if isinstance(it, dict)]
    else:
        items = [{"kind": str(body.get("kind") or ""), "id": str(body.get("id") or "")}]
    if not items:
        raise HTTPException(status_code=400, detail="Keine Einträge.")
    out = va.delete_items(items)
    gone = {(d.get("kind"), d.get("id")) for d in out.get("deleted") or []}
    with STATE.lock:
        STATE.va_local_rev += 1
        if STATE.va_preview and gone:
            updated = []
            for it in STATE.va_preview:
                key = (it.get("kind"), it.get("id"))
                if key in gone:
                    row = dict(it)
                    row["has_audio"] = False
                    row["has_folien"] = False
                    row["stand"] = "neu"
                    row["need"] = True
                    updated.append(row)
                else:
                    updated.append(it)
            STATE.va_preview = updated
    if not out.get("deleted") and out.get("errors"):
        raise HTTPException(status_code=400, detail=(out["errors"][0] or {}).get("error") or "Löschen fehlgeschlagen")
    return out


@app.get("/api/va/audio/{kind}/{item_id}")
def api_va_audio(kind: str, item_id: str):
    if kind not in va.KINDS:
        raise HTTPException(status_code=400, detail="Unbekannte Quelle.")
    try:
        safe = va.sanitize_id(item_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungültige ID.")
    path = va.find_audio(kind, safe)
    if not path:
        raise HTTPException(status_code=404, detail="Keine Audiodatei.")
    path, media = va.playback_audio(path)
    return FileResponse(
        path,
        media_type=media,
        filename=os.path.basename(path),
        content_disposition_type="inline",
    )


@app.get("/api/va/file/{kind}/{item_id}/{name}")
def api_va_file(kind: str, item_id: str, name: str):
    if kind not in va.KINDS:
        raise HTTPException(status_code=400, detail="Unbekannte Quelle.")
    try:
        path = va.folie_path(kind, item_id, name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    media = va.folie_media(path)
    return FileResponse(
        path,
        media_type=media,
        filename=os.path.basename(path),
        content_disposition_type="inline" if media == "application/pdf" else "attachment",
    )


@app.post("/api/va/file/open")
def api_va_file_open(body: Dict[str, Any]):
    kind = str(body.get("kind") or "")
    item_id = str(body.get("id") or "")
    name = str(body.get("name") or "")
    if kind not in va.KINDS:
        raise HTTPException(status_code=400, detail="Unbekannte Quelle.")
    try:
        path = va.folie_path(kind, item_id, name)
        va.open_local_file(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@app.post("/api/va/pack/start")
def api_va_pack_start(body: Dict[str, Any]):
    kinds = [k for k in (body.get("kinds") or []) if k in va.KINDS]
    ids = body.get("ids") or None
    date_from = str(body.get("date_from") or "")
    date_to = str(body.get("date_to") or "")
    try:
        STATE.start_pack(kinds, ids, date_from, date_to)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@app.post("/api/va/pack/stop")
def api_va_pack_stop():
    STATE.stop_pack()
    return {"ok": True}


@app.get("/api/va/audio/meta")
def api_va_audio_meta(kind: str, item_id: str = Query(..., alias="id")):
    if kind not in va.KINDS:
        raise HTTPException(status_code=400, detail="Unbekannte Quelle.")
    path = va.find_audio(kind, va.sanitize_id(item_id))
    if not path:
        raise HTTPException(status_code=404, detail="Keine Audiodatei.")
    return {"ready": not va.needs_audio_convert(path)}


@app.get("/api/va/audio/prepare")
def api_va_audio_prepare_status():
    return va.AUDIO_PREP.snapshot()


@app.post("/api/va/audio/prepare")
def api_va_audio_prepare(body: Dict[str, Any]):
    kind = str((body or {}).get("kind") or "")
    item_id = str((body or {}).get("id") or "")
    if kind not in va.KINDS:
        raise HTTPException(status_code=400, detail="Unbekannte Quelle.")
    path = va.find_audio(kind, va.sanitize_id(item_id))
    if not path:
        raise HTTPException(status_code=404, detail="Keine Audiodatei.")
    with va.AUDIO_PREP.lock:
        va.AUDIO_PREP.kind = kind
        va.AUDIO_PREP.item_id = item_id
    threading.Thread(target=va.prepare_playback, args=(path,), daemon=True).start()
    return {"ok": True}


@app.post("/api/va/audio/prepare/stop")
def api_va_audio_prepare_stop():
    va.AUDIO_PREP.request_stop()
    return {"ok": True}


@app.post("/api/va/pack")
def api_va_pack(body: Dict[str, Any]):
    kinds = [k for k in (body.get("kinds") or []) if k in va.KINDS]
    ids = body.get("ids") or None
    date_from = str(body.get("date_from") or "")
    date_to = str(body.get("date_to") or "")
    try:
        STATE.start_pack(kinds, ids, date_from, date_to)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "async": True}


@app.get("/api/va/pack/file/{token}")
def api_va_pack_file(token: str):
    with PACK_LOCK:
        info = PACK_FILES.pop(token, None)
    if not info:
        raise HTTPException(status_code=404, detail="Paket nicht mehr da. Noch einmal erzeugen.")
    path = info["path"]
    filename = info["filename"]
    keep = bool(info.get("keep"))

    def _cleanup():
        if keep:
            return
        try:
            os.remove(path)
        except OSError:
            pass

    return FileResponse(
        path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(_cleanup),
    )


def _no_browser():
    if "--no-browser" in sys.argv:
        return True
    return os.environ.get("KINGFALL_NO_BROWSER", "").strip().lower() in ("1", "true", "yes")


def _apply_data_cwd():
    root = os.environ.get("KINGFALL_CWD", "").strip()
    if not root:
        return
    os.makedirs(root, exist_ok=True)
    os.chdir(root)


def serve():
    _apply_data_cwd()
    try:
        import uvicorn
    except ImportError:
        print("FastAPI/Uvicorn fehlen. Einmalig:")
        print("  python3 -m pip install fastapi uvicorn")
        sys.exit(1)
    try:
        port = pick_port(HOST)
    except OSError as exc:
        print("Kein lokaler Port frei (%s)." % exc)
        print("Windows sperrt oft ganze Port-Bereiche (Hyper-V/WSL). Test:")
        print("  netsh interface ipv4 show excludedportrange protocol=tcp")
        sys.exit(1)
    url = "http://%s:%s/" % (HOST, port)
    print("Königssturz: %s" % url)
    print("KINGFALL_READY url=%s" % url)
    print("Nur localhost. Terminal offen lassen, bis die Sicherung fertig ist.")
    print("Dateien landen in: %s" % os.getcwd())
    sys.stdout.flush()
    va.start_convert_pending()

    if not _no_browser():
        def _open():
            time.sleep(0.8)
            webbrowser.open(url)

        threading.Thread(target=_open, daemon=True).start()
    uvicorn.run(app, host=HOST, port=port, log_level="warning")


if __name__ == "__main__":
    serve()
