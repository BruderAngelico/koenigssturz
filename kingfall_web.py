# -*- coding: utf-8 -*-
"""Lokale Browser-UI für Königssturz. Sicherung bleibt Python auf Disk, nicht im Browser."""
from __future__ import annotations

import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import webbrowser
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Dict

import imaplib
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

import kingfall as kf
import kingfall_analyze as ka

HOST = "127.0.0.1"
PORT_CANDIDATES = (18765, 18080, 19000, 8088, 8000, 8765)


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
.pane.on{display:flex;flex-direction:column;}
fieldset{border:1px solid #c4bfb4;margin:0 0 8px;padding:10px;background:var(--bg);}
legend{font-weight:700;padding:0 6px;}
.hint{margin:0 0 8px;color:#333;}
.inline{display:flex;flex-wrap:wrap;align-items:center;gap:8px 10px;margin:6px 0;}
.inline label{font-weight:600;}
input[type=text],input[type=password],textarea{font:inherit;border:1px solid #c4bfb4;background:var(--field);color:var(--fg);padding:4px 6px;}
textarea{width:100%;min-height:88px;font-family:ui-monospace,Menlo,Consolas,monospace;}
.inline input[type=text],.inline input[type=password]{width:180px;}
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
#analyze{min-height:0;}
.bottom{flex:1;display:flex;flex-direction:column;min-height:180px;}
.split{display:flex;flex:1;min-height:0;gap:0;}
.tlist{flex:0 0 280px;width:280px;min-width:180px;max-width:70%;display:flex;flex-direction:column;}
.tlist .list{flex:1;min-height:0;}
.tlist .head,.tlist .rowl{grid-template-columns:1fr auto;}
.tlist button.rowl{cursor:pointer;border:none;background:transparent;width:100%;text-align:left;font:inherit;color:inherit;}
.tlist .rowl span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.tlist .rowl.on{background:var(--hover);font-weight:700;}
.splitbar{flex:0 0 6px;width:6px;cursor:col-resize;background:#c4bfb4;align-self:stretch;margin:0 4px;}
.splitbar:hover,.splitbar.drag{background:var(--bar);}
.agrid{flex:1;display:flex;flex-direction:column;min-width:0;min-height:0;}
.agrid fieldset{min-height:0;}
.filters{display:flex;flex-direction:column;gap:4px;margin:4px 0;}
.filterrow{display:flex;flex-wrap:wrap;align-items:center;gap:6px;}
.filterrow select,.filterrow input[type=text]{font:inherit;border:1px solid #c4bfb4;background:var(--field);padding:3px 6px;}
.filterrow input[type=text]{width:160px;}
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
</style></head><body>
<div class="top">
  <h1>Königssturz – Beweissicherung</h1>
  <div class="menu"><button class="act" onclick="post('/api/export',{curl:gv('curl'),test_run:true})">Datei: Test-Run</button></div>
</div>
<div class="tabs">
  <button id="bdb" class="on" onclick="tab('db')">Supabase</button>
  <button id="bmail" onclick="tab('mail')">E-Mail (IONOS)</button>
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

<section class="pane" id="analyze">
  <fieldset>
    <legend> 1. Backup-Ordner </legend>
    <p class="hint">Sicherung wählen. Standard ist der neueste Ordner. Danach eine Tabelle links anklicken. „Überall“ sucht den Text in allen Tabellen dieses Ordners.</p>
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
      <button class="act" onclick="runCompare(0)">Vergleichen</button>
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
        <label><input type="checkbox" class="ckind" value="neu" checked onchange="runCompare(0)"/> neu</label>
        <label><input type="checkbox" class="ckind" value="gelöscht" checked onchange="runCompare(0)"/> gelöscht</label>
        <label><input type="checkbox" class="ckind" value="geändert" checked onchange="runCompare(0)"/> geändert</label>
      </div>
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
      <button class="act" type="button" onclick="closeCell()">Schließen</button>
    </div>
    <pre id="poppre"></pre>
    <iframe id="popframe" class="popframe" sandbox title="HTML-Vorschau"></iframe>
  </div>
</div>
<script>
function tab(name){
  document.getElementById('db').className='pane'+(name==='db'?' on':'');
  document.getElementById('mail').className='pane'+(name==='mail'?' on':'');
  document.getElementById('analyze').className='pane'+(name==='analyze'?' on':'');
  document.getElementById('bdb').className=name==='db'?'on':'';
  document.getElementById('bmail').className=name==='mail'?'on':'';
  document.getElementById('banalyze').className=name==='analyze'?'on':'';
  if(name==='analyze') loadBackups(false);
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
}
setInterval(tick,1000); tick();

const FILTER_OPS=[
  {id:'eq',label:'ist'},
  {id:'ne',label:'ist nicht'},
  {id:'contains',label:'enthält'},
  {id:'not_contains',label:'enthält nicht'},
  {id:'starts',label:'beginnt mit'},
  {id:'empty',label:'ist leer'},
  {id:'not_empty',label:'ist nicht leer'},
  {id:'gt',label:'größer als'},
  {id:'lt',label:'kleiner als'}
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

function colOptions(selected){
  return '<option value="">Spalte …</option>'+columnMeta.map(function(c){
    const n=c.name||c;
    return '<option value="'+esc(n)+'"'+(selected===n?' selected':'')+'>'+esc(n)+'</option>';
  }).join('');
}
function needsValue(op){ return op!=='empty' && op!=='not_empty'; }
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
  renderTableList();
  renderColPick();
  fillSavedSelect();
  document.getElementById('agridlegend').textContent=' 3. '+t.display;
  if(preset) applyPreset(preset);
  else applyAnalyze(0);
}
function resetBuilder(clearSql){
  document.getElementById('asearch').value='';
  document.getElementById('afilters').innerHTML='';
  document.getElementById('agroups').innerHTML='';
  document.getElementById('aaggs').innerHTML='';
  analyzeOrder=null;
  analyzePageNo=0;
  sqlDirty=false;
  if(clearSql) document.getElementById('asql').value='';
}
function resetAnalyze(){
  sqlDirty=false;
  resetBuilder(true);
  if(selectedTable) applyAnalyze(0);
}
function addFilter(col,op,value){
  const wrap=document.createElement('div');
  wrap.className='filterrow';
  const listId='fdl-'+Math.random().toString(36).slice(2,8);
  wrap.innerHTML='<select class="fcol">'+colOptions(col||'')+'</select>'
    +'<select class="fop">'+FILTER_OPS.map(function(o){return '<option value="'+o.id+'"'+(op===o.id?' selected':'')+'>'+o.label+'</option>';}).join('')+'</select>'
    +'<input class="fval" type="text" list="'+listId+'" value="'+esc(value||'')+'"/>'
    +'<datalist id="'+listId+'"></datalist>'
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
  wrap.querySelector('.fval').style.display=needsValue(op)?'':'none';
}
function addGroup(col){
  const wrap=document.createElement('div');
  wrap.className='filterrow';
  wrap.innerHTML='<select class="gcol">'+colOptions(col||'')+'</select><button class="act" type="button">Entfernen</button>';
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
    return {column:row.querySelector('.fcol').value, op:row.querySelector('.fop').value, value:row.querySelector('.fval').value};
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
    return '<tr>'+cols.map(function(c){
      const v=row[c];
      if(v===null||v===undefined) return '<td class="null'+chgCls+'">–</td>';
      const kind=cellKind(v);
      const cls=' class="'+(kind?'rich ':'')+chgCls.trim()+'"'+(kind?' data-kind="'+kind+'"':'');
      const t=fmtVal(v);
      return '<td'+cls+' data-r="'+ri+'" data-c="'+escAttr(c)+'" title="'+escAttr(cellTxt(v))+'">'+esc(t)+'</td>';
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
  if(d.column_meta&&d.column_meta.length) columnMeta=d.column_meta;
}
function queryBody(page){
  const body={
    folder:currentFolder(),
    table:selectedTable.stem,
    search:document.getElementById('asearch').value||'',
    filters:readFilters(),
    group_by:readGroups(),
    aggregations:readAggs(),
    page:page||0
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
  document.getElementById('cellpop').className='pop on';
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
  if(!selectedTable){ alert('Zuerst eine Tabelle wählen.'); return; }
  try{
    let url='/api/analyze/export';
    let body;
    if(analyzeMode==='compare' || (lastAnalyze&&lastAnalyze.compare)){
      url='/api/analyze/compare/export';
      body=compareBody(0);
      body.format=fmt;
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
  const search=(document.getElementById('asearch').value||'').trim();
  if(search) lines.push('Suche: '+search);
  readFilters().forEach(function(f){
    let s='Nur Zeilen wo: '+f.column+' '+opLabel(f.op);
    if(needsValue(f.op) && f.value) s+=' „'+f.value+'“';
    lines.push(s);
  });
  const groups=readGroups();
  if(groups.length) lines.push('Gruppiert nach: '+groups.join(', '));
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
    sql:sqlDirty?(document.getElementById('asql').value||''):''
  };
}
function applyPreset(preset){
  if(!preset) return;
  if(preset.table && selectedTable && preset.table!==selectedTable.stem){
    selectTable(preset.table, preset);
    return;
  }
  resetBuilder(true);
  document.getElementById('asearch').value=preset.search||'';
  (preset.filters||[]).forEach(function(f){ addFilter(f.column, f.op, f.value); });
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
function initSplit(){
  const bar=document.getElementById('splitbar');
  const box=document.getElementById('tlistbox');
  const saved=parseInt(localStorage.getItem('analyzeSplit')||'',10);
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
      localStorage.setItem('analyzeSplit', String(parseInt(box.style.flexBasis,10)||startW));
    }
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
    e.preventDefault();
  });
}
initSplit();
</script>
</body></html>
"""


app = FastAPI(title="Königssturz – Beweissicherung")


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE


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


@app.post("/api/analyze/compare/export")
def api_analyze_compare_export(body: Dict[str, Any]):
    try:
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


def serve():
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
    print("Nur localhost. Terminal offen lassen, bis die Sicherung fertig ist.")
    print("Dateien landen in: %s" % os.getcwd())

    def _open():
        time.sleep(0.8)
        webbrowser.open(url)

    threading.Thread(target=_open, daemon=True).start()
    uvicorn.run(app, host=HOST, port=port, log_level="warning")


if __name__ == "__main__":
    serve()
