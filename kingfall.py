from __future__ import annotations

import base64
import email
import imaplib
import json
import os
import re
import ssl
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from typing import Optional
from tkinter import messagebox, scrolledtext
from tkinter import font as tkfont
from urllib.parse import urlparse

import requests


PAGE_SIZE = 2500
TEST_PAGE_SIZE = 10
SQL_META_INTERVAL = 3.0
SQL_DATA_INTERVAL = 0.75
SQL_MAX_META_INTERVAL = 8.0
SQL_MAX_DATA_INTERVAL = 4.0
SQL_RETRY_429 = 8
AUTH_MARKERS = (
    "jwt expired",
    "invalid jwt",
    "token expired",
    "session expired",
    "not authenticated",
    "invalid claim",
    "pgrst301",
)

SCHEMAS_SQL = """
SELECT n.nspname AS schema_name
FROM pg_catalog.pg_namespace n
WHERE n.nspname NOT LIKE 'pg_%'
  AND n.nspname <> 'information_schema'
ORDER BY n.nspname;
"""

DEFAULT_CHECKED_SCHEMAS = {"public", "auth", "storage"}
DEFAULT_UNCHECKED_SCHEMAS = {
    "cron",
    "extensions",
    "graphql",
    "graphql_public",
    "net",
    "pgsodium",
    "realtime",
    "supabase_functions",
    "supabase_migrations",
    "vault",
    "_analytics",
    "_realtime",
    "pgbouncer",
}


def tables_sql_pg(schemas: list[str]) -> str:
    in_list = sql_in_list(schemas)
    return f"""
SELECT
    n.nspname AS table_schema,
    c.relname AS table_name,
    (
        SELECT string_agg(a.attname, ',' ORDER BY u.ord)
        FROM pg_index i
        CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS u(attnum, ord)
        JOIN pg_attribute a
          ON a.attrelid = i.indrelid
         AND a.attnum = u.attnum
        WHERE i.indrelid = c.oid
          AND i.indisprimary
          AND u.attnum > 0
    ) AS pk_cols,
    COALESCE(s.n_live_tup, c.reltuples::bigint) AS est_rows
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_stat_all_tables s ON s.relid = c.oid
WHERE n.nspname IN ({in_list})
  AND c.relkind = 'r'
ORDER BY n.nspname, c.relname;
"""


def tables_sql_info(schemas: list[str]) -> str:
    in_list = sql_in_list(schemas)
    return f"""
SELECT
    t.table_schema,
    t.table_name,
    (
        SELECT string_agg(ku.column_name, ',' ORDER BY ku.ordinal_position)
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage ku
          ON tc.constraint_name = ku.constraint_name
         AND tc.table_schema = ku.table_schema
         AND tc.table_name = ku.table_name
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema = t.table_schema
          AND tc.table_name = t.table_name
    ) AS pk_cols,
    (
        SELECT cl.reltuples::bigint
        FROM pg_class cl
        JOIN pg_namespace ns ON ns.oid = cl.relnamespace
        WHERE ns.nspname = t.table_schema
          AND cl.relname = t.table_name
          AND cl.relkind = 'r'
    ) AS est_rows
FROM information_schema.tables t
WHERE t.table_schema IN ({in_list})
  AND t.table_type = 'BASE TABLE'
ORDER BY t.table_schema, t.table_name;
"""


UI_BG = "#e8e4dc"
UI_FG = "#000000"
UI_FIELD = "#ffffff"
UI_BUTTON = "#d4cfc4"
NORMAL_ROW_BG = "#f4f4f4"
HOVER_ROW_BG = "#c5def5"
UI_FONT_FAMILY = "Helvetica"
UI_MONO_FAMILY = "Courier"


def ui_font(size, *styles):
    return (UI_FONT_FAMILY, size) + styles


def ui_mono(size, *styles):
    return (UI_MONO_FAMILY, size) + styles


def _pick_font_family(root, candidates, named):
    try:
        available = set(tkfont.families(root))
    except tk.TclError:
        available = set()
    for name in candidates:
        if name in available:
            return name
    try:
        return tkfont.nametofont(named).actual()["family"]
    except tk.TclError:
        return candidates[-1]


def resolve_ui_fonts(root):
    global UI_FONT_FAMILY, UI_MONO_FAMILY
    if sys.platform == "darwin":
        UI_FONT_FAMILY = _pick_font_family(
            root, ("Lucida Grande", "Helvetica Neue", "Helvetica", "Arial"), "TkDefaultFont"
        )
        UI_MONO_FAMILY = _pick_font_family(root, ("Menlo", "Monaco", "Courier"), "TkFixedFont")
    elif sys.platform == "win32":
        UI_FONT_FAMILY = _pick_font_family(root, ("Segoe UI", "Tahoma", "Arial"), "TkDefaultFont")
        UI_MONO_FAMILY = _pick_font_family(
            root, ("Consolas", "Cascadia Mono", "Courier New"), "TkFixedFont"
        )
    else:
        UI_FONT_FAMILY = _pick_font_family(
            root, ("DejaVu Sans", "Ubuntu", "Noto Sans", "Arial"), "TkDefaultFont"
        )
        UI_MONO_FAMILY = _pick_font_family(
            root, ("DejaVu Sans Mono", "Ubuntu Mono", "Noto Sans Mono"), "TkFixedFont"
        )


def apply_light_ui(root: tk.Tk) -> None:
    """Nur klassische Tk-Widgets – ttk/clam bleibt auf macOS oft leer (nur Scrollbars)."""
    resolve_ui_fonts(root)
    root.configure(bg=UI_BG)
    root.option_add("*Background", UI_BG)
    root.option_add("*Foreground", UI_FG)
    root.option_add("*selectBackground", HOVER_ROW_BG)
    root.option_add("*selectForeground", UI_FG)
    root.option_add("*Text.Background", UI_FIELD)
    root.option_add("*Text.Foreground", UI_FG)
    root.option_add("*Text.insertBackground", UI_FG)
    root.option_add("*Entry.Background", UI_FIELD)
    root.option_add("*Entry.Foreground", UI_FG)
    root.option_add("*Canvas.Background", UI_BG)
    root.option_add("*Listbox.Background", UI_FIELD)
    root.option_add("*Listbox.Foreground", UI_FG)
    root.option_add("*Button.Background", UI_BUTTON)
    root.option_add("*Button.Foreground", UI_FG)
    root.option_add("*Label.Background", UI_BG)
    root.option_add("*Label.Foreground", UI_FG)


def ui_frame(parent, **kw):
    kw.setdefault("bg", UI_BG)
    return tk.Frame(parent, **kw)


def ui_labelframe(parent, text, **kw):
    kw.setdefault("bg", UI_BG)
    kw.setdefault("fg", UI_FG)
    kw.setdefault("font", ui_font(12, "bold"))
    kw.setdefault("padx", 8)
    kw.setdefault("pady", 8)
    return tk.LabelFrame(parent, text=text, **kw)


def ui_label(parent, text="", **kw):
    kw.setdefault("bg", UI_BG)
    kw.setdefault("fg", UI_FG)
    kw.setdefault("font", ui_font(12))
    kw.setdefault("anchor", "w")
    return tk.Label(parent, text=text, **kw)


def ui_button(parent, text, command=None, **kw):
    kw.setdefault("bg", UI_BUTTON)
    kw.setdefault("fg", UI_FG)
    kw.setdefault("font", ui_font(12, "bold"))
    kw.setdefault("activebackground", "#c4bfb4")
    kw.setdefault("activeforeground", UI_FG)
    kw.setdefault("disabledforeground", "#666666")
    kw.setdefault("relief", tk.RAISED)
    kw.setdefault("bd", 1)
    kw.setdefault("padx", 8)
    kw.setdefault("pady", 4)
    if command is not None:
        kw["command"] = command
    return tk.Button(parent, text=text, **kw)


def ui_entry(parent, **kw):
    kw.setdefault("bg", UI_FIELD)
    kw.setdefault("fg", UI_FG)
    kw.setdefault("insertbackground", UI_FG)
    kw.setdefault("font", ui_font(12))
    kw.setdefault("relief", tk.SUNKEN)
    kw.setdefault("bd", 1)
    return tk.Entry(parent, **kw)


def ui_check(parent, **kw):
    kw.setdefault("bg", UI_BG)
    kw.setdefault("fg", UI_FG)
    kw.setdefault("activebackground", UI_BG)
    kw.setdefault("activeforeground", UI_FG)
    kw.setdefault("selectcolor", UI_FIELD)
    kw.setdefault("font", ui_font(12))
    kw.setdefault("anchor", "w")
    return tk.Checkbutton(parent, **kw)


def ui_scroll(parent, **kw):
    kw.setdefault("bg", UI_BUTTON)
    kw.setdefault("troughcolor", "#c4bfb4")
    kw.setdefault("activebackground", "#b8b3a8")
    return tk.Scrollbar(parent, **kw)


def format_de_int(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def quote_table(schema: str, name: str) -> str:
    return f"{quote_ident(schema)}.{quote_ident(name)}"


def display_table(schema: str, name: str) -> str:
    return f"{schema}.{name}"


def table_file_stem(schema: str, name: str) -> str:
    return sanitize_filename(f"{schema}_{name}")


def sql_in_list(values: list[str]) -> str:
    if not values:
        raise ValueError("Keine Schemas ausgewählt.")
    return ", ".join(sql_literal(v) for v in values)


def sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def json_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def table_schema_sql(schema: str, name: str) -> str:
    quoted_schema = sql_literal(schema)
    quoted_name = sql_literal(name)
    return f"""
SELECT
  COALESCE((
    SELECT json_agg(q)
    FROM (
      SELECT
          a.attname AS column_name,
          a.attnum AS ordinal_position,
          pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
          typ.typname AS udt_name,
          NOT a.attnotnull AS is_nullable,
          pg_get_expr(ad.adbin, ad.adrelid) AS column_default,
          COALESCE(a.attidentity, '') AS identity,
          COALESCE(a.attgenerated, '') AS generated,
          col_description(cls.oid, a.attnum) AS comment
      FROM pg_attribute a
      JOIN pg_class cls ON cls.oid = a.attrelid
      JOIN pg_namespace n ON n.oid = cls.relnamespace
      JOIN pg_type typ ON typ.oid = a.atttypid
      LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
      WHERE n.nspname = {quoted_schema}
        AND cls.relname = {quoted_name}
        AND a.attnum > 0
        AND NOT a.attisdropped
      ORDER BY a.attnum
    ) q
  ), '[]'::json) AS columns,
  COALESCE((
    SELECT json_agg(q)
    FROM (
      SELECT
          con.conname AS name,
          con.contype AS type,
          pg_get_constraintdef(con.oid) AS definition
      FROM pg_constraint con
      JOIN pg_class cls ON cls.oid = con.conrelid
      JOIN pg_namespace n ON n.oid = cls.relnamespace
      WHERE n.nspname = {quoted_schema}
        AND cls.relname = {quoted_name}
      ORDER BY con.contype, con.conname
    ) q
  ), '[]'::json) AS constraints,
  COALESCE((
    SELECT json_agg(q)
    FROM (
      SELECT
          idx.relname AS index_name,
          ix.indisunique AS is_unique,
          ix.indisprimary AS is_primary,
          pg_get_indexdef(ix.indexrelid) AS definition
      FROM pg_index ix
      JOIN pg_class tbl ON tbl.oid = ix.indrelid
      JOIN pg_class idx ON idx.oid = ix.indexrelid
      JOIN pg_namespace n ON n.oid = tbl.relnamespace
      WHERE n.nspname = {quoted_schema}
        AND tbl.relname = {quoted_name}
        AND NOT EXISTS (
            SELECT 1 FROM pg_constraint con WHERE con.conindid = ix.indexrelid
        )
      ORDER BY idx.relname
    ) q
  ), '[]'::json) AS indexes;
"""


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w.\-]+", "_", name, flags=re.UNICODE).strip("._")
    return cleaned or "tabelle"


def parse_curl(raw_curl: str) -> tuple[str, dict]:
    raw = raw_curl.strip()
    if not raw:
        raise ValueError("Das cURL-Textfeld ist leer.")

    raw = raw.replace("curl.exe", "curl")
    raw = re.sub(r"\^\s*[\r\n]+", " ", raw)
    raw = re.sub(r"\\\s*[\r\n]+", " ", raw)
    raw = raw.replace('^"', '"').replace("^'", "'")
    raw = re.sub(r"\^([^\s])", r"\1", raw)
    raw = re.sub(r"[\r\n]+", " ", raw)

    url_match = re.search(r"""(?:'(https?://[^']+)'|"(https?://[^"]+)"|(https?://[^\s\\]+))""", raw)
    if not url_match:
        raise ValueError("Konnte keine URL aus dem cURL-Befehl lesen.")

    url = next(g for g in url_match.groups() if g)
    url = url.strip().rstrip("\\").strip("'\"")
    url = normalize_query_url(url)

    headers = {}
    for match in re.finditer(
        r"""(?:-H|--header)\s+(?:\$?'([^']+)'|\$?"([^"]+)"|'([^']+)'|"([^"]+)"|(\S+))""",
        raw,
        re.IGNORECASE,
    ):
        header_line = next(g for g in match.groups() if g)
        if ":" not in header_line:
            continue
        key, value = header_line.split(":", 1)
        headers[key.strip()] = value.strip()

    cookie_match = re.search(
        r"""(?:-b|--cookie)\s+(?:'([^']+)'|"([^"]+)"|(\S+))""",
        raw,
        re.IGNORECASE,
    )
    if cookie_match:
        cookie_val = next(g for g in cookie_match.groups() if g)
        headers.setdefault("Cookie", cookie_val.strip())

    drop = {
        "content-length",
        "host",
        "connection",
        "accept-encoding",
        "if-none-match",
        "if-modified-since",
    }
    headers = {k: v for k, v in headers.items() if k.lower() not in drop}

    auth_key = next((k for k in headers if k.lower() == "authorization"), None)
    if auth_key and auth_key != "Authorization":
        headers["Authorization"] = headers.pop(auth_key)

    headers.setdefault("Content-Type", "application/json")
    headers.setdefault("Accept", "application/json")
    return url, headers


def normalize_query_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    pg_meta = re.search(r"(/platform/pg-meta/[^/]+)", path)
    if pg_meta:
        return f"{parsed.scheme}://{parsed.netloc}{pg_meta.group(1)}/query?key="
    if path.endswith("/query"):
        return f"{parsed.scheme}://{parsed.netloc}{path}?key="
    return url.split("#")[0]


_sql_pace = {
    "last": 0.0,
    "meta": SQL_META_INTERVAL,
    "data": SQL_DATA_INTERVAL,
    "lock": threading.Lock(),
}


def is_auth_error(response: requests.Response) -> bool:
    if response.status_code in (401, 403):
        return True
    text = (response.text or "").lower()
    return any(marker in text for marker in AUTH_MARKERS)


def is_throttle_error(response) -> bool:
    if response is None:
        return False
    if response.status_code == 429:
        return True
    text = (response.text or "").lower()
    return "throttlerexception" in text or "too many requests" in text


def throttle_delay(response, attempt: int) -> float:
    header = None
    if response is not None:
        header = response.headers.get("Retry-After") or response.headers.get("retry-after")
    if header:
        try:
            return float(min(90, max(2, int(float(str(header).strip().split()[0])))))
        except (TypeError, ValueError):
            pass
    return float(min(60, 3 * (2 ** attempt)))


def pace_sql_request(kind: str = "meta") -> None:
    key = "data" if kind == "data" else "meta"
    with _sql_pace["lock"]:
        interval = _sql_pace[key]
        last = _sql_pace["last"]
        now = time.time()
        wait = interval - (now - last)
        if wait < 0:
            wait = 0
        _sql_pace["last"] = now + wait
    if wait > 0:
        time.sleep(wait)


def note_sql_throttle() -> None:
    with _sql_pace["lock"]:
        _sql_pace["meta"] = min(SQL_MAX_META_INTERVAL, max(5.0, _sql_pace["meta"] * 1.4))
        _sql_pace["data"] = min(SQL_MAX_DATA_INTERVAL, max(1.5, _sql_pace["data"] * 1.4))


def header_value(headers: dict, name: str) -> Optional[str]:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def decode_jwt_payload(token: str) -> dict:
    raw = token.strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    parts = raw.split(".")
    if len(parts) < 2:
        raise ValueError("Kein JWT")
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))


class AuthSession:
    """Dashboard-Login + Refresh-Token. Passwort bleibt nur im RAM."""

    def __init__(self):
        self.access_token = None  # type: Optional[str]
        self.refresh_token = None  # type: Optional[str]
        self.expires_at = 0.0
        self.apikey = None  # type: Optional[str]
        self.auth_base = None  # type: Optional[str]
        self.lock = threading.Lock()

    def ingest_headers(self, headers: dict) -> None:
        apikey = header_value(headers, "apikey")
        if apikey:
            self.apikey = apikey
            if not self.auth_base:
                self.auth_base = "https://alt.supabase.io/auth/v1"
        auth = header_value(headers, "Authorization")
        if not auth:
            return
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else auth.strip()
        payload = {}
        try:
            payload = decode_jwt_payload(auth)
        except Exception:
            payload = {}
        iss = payload.get("iss")
        if isinstance(iss, str) and iss.startswith("http"):
            self.auth_base = iss.rstrip("/")
        elif not self.auth_base:
            self.auth_base = "https://alt.supabase.io/auth/v1"
        # Immer den Token aus dem (neuen) cURL übernehmen – sonst überschreibt
        # apply_to_headers den frischen Authorization-Header mit dem abgelaufenen.
        self.access_token = token
        exp = payload.get("exp")
        if isinstance(exp, (int, float)):
            self.expires_at = float(exp)
        elif token:
            self.expires_at = time.time() + 1800

    def apply_to_headers(self, headers: dict) -> None:
        if not self.access_token:
            return
        for key in list(headers):
            if key.lower() == "authorization":
                del headers[key]
        headers["Authorization"] = f"Bearer {self.access_token}"

    def _request(self, method, path, payload=None, bearer=None):
        if not self.auth_base:
            raise RuntimeError("Auth-URL unbekannt. Zuerst einen cURL einfügen (iss/apikey).")
        if not self.apikey:
            raise RuntimeError("Kein apikey im cURL. Einen Request aus den DevTools kopieren, der den Header apikey enthält.")
        token = bearer or self.access_token or self.apikey
        headers = {
            "Content-Type": "application/json",
            "apikey": self.apikey,
            "Authorization": f"Bearer {token}",
        }
        url = self.auth_base.rstrip("/") + path
        response = requests.request(method, url, headers=headers, json=payload, timeout=30)
        try:
            data = response.json()
        except ValueError:
            data = {"error": response.text}
        if response.status_code >= 400:
            msg = (
                data.get("error_description")
                or data.get("msg")
                or data.get("message")
                or data.get("error")
                or response.text
            )
            raise RuntimeError(str(msg))
        if not isinstance(data, dict):
            raise RuntimeError("Unerwartete Auth-Antwort")
        return data

    def _store_session(self, data: dict) -> None:
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        if not access:
            raise RuntimeError("Login lieferte keinen access_token.")
        self.access_token = access
        if refresh:
            self.refresh_token = refresh
        expires_in = data.get("expires_in")
        try:
            payload = decode_jwt_payload(access)
            exp = payload.get("exp")
            if isinstance(exp, (int, float)):
                self.expires_at = float(exp)
                return
        except Exception:
            pass
        self.expires_at = time.time() + (int(expires_in) if expires_in else 1800)

    def login(self, email, password, totp=None):
        data = self._request(
            "POST",
            "/token?grant_type=password",
            {"email": email.strip(), "password": password},
            bearer=self.apikey,
        )
        self._store_session(data)
        totp = (totp or "").strip()
        if self._needs_mfa():
            if not totp:
                raise RuntimeError("Dieses Konto hat 2FA. Bitte den aktuellen TOTP-Code eintragen und erneut anmelden.")
            self._verify_totp(totp)

    def _needs_mfa(self) -> bool:
        try:
            payload = decode_jwt_payload(self.access_token or "")
        except Exception:
            return False
        aal = str(payload.get("aal") or "aal1")
        if aal == "aal2":
            return False
        try:
            factors = self._request("GET", "/factors")
        except Exception:
            return False
        return bool(self._verified_totp_id(factors))

    def _verified_totp_id(self, factors):
        items = []
        if isinstance(factors, dict) and isinstance(factors.get("data"), dict):
            factors = factors["data"]
        if isinstance(factors, dict):
            if isinstance(factors.get("totp"), list):
                items.extend(factors["totp"])
            if isinstance(factors.get("factors"), list):
                items.extend(factors["factors"])
            if isinstance(factors.get("all"), list):
                items.extend(factors["all"])
        elif isinstance(factors, list):
            items = factors
        for item in items:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").lower()
            ftype = str(item.get("factor_type") or item.get("type") or "totp").lower()
            if status in {"", "verified"} and ftype in {"totp", "app", "authenticator"}:
                factor_id = item.get("id")
                if factor_id:
                    return str(factor_id)
        return None

    def _verify_totp(self, code: str) -> None:
        factors = self._request("GET", "/factors")
        factor_id = self._verified_totp_id(factors)
        if not factor_id:
            raise RuntimeError("Kein verifiziertes TOTP-Gerät gefunden.")
        challenge = self._request("POST", f"/factors/{factor_id}/challenge", {})
        challenge_id = challenge.get("id") or challenge.get("challenge_id")
        if not challenge_id:
            raise RuntimeError("TOTP-Challenge fehlgeschlagen.")
        verified = self._request(
            "POST",
            f"/factors/{factor_id}/verify",
            {"challenge_id": challenge_id, "code": code},
        )
        session = verified.get("access_token") and verified or verified.get("data") or verified
        if not isinstance(session, dict) or not session.get("access_token"):
            raise RuntimeError("TOTP-Bestätigung lieferte keine Session.")
        self._store_session(session)

    def refresh(self) -> bool:
        with self.lock:
            if not self.refresh_token:
                return False
            try:
                data = self._request(
                    "POST",
                    "/token?grant_type=refresh_token",
                    {"refresh_token": self.refresh_token},
                    bearer=self.apikey,
                )
                self._store_session(data)
                return True
            except Exception:
                return False

    def seconds_left(self) -> float:
        return self.expires_at - time.time() if self.expires_at else 0


class JsonlWriter:
    """Schreibt jede Zeile sofort auf Disk. Bei Token-Pause bleibt die Datei gültiges JSONL."""

    def __init__(self, path: str, resume: bool = False):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        mode = "a" if resume and os.path.exists(path) else "w"
        self._file = open(path, mode, encoding="utf-8")
        self.closed = False

    def append(self, records: list) -> None:
        for record in records:
            json.dump(record, self._file, ensure_ascii=False)
            self._file.write("\n")
        self._file.flush()
        os.fsync(self._file.fileno())

    def close(self) -> None:
        if self.closed:
            return
        try:
            self._file.flush()
            os.fsync(self._file.fileno())
        except OSError:
            pass
        self._file.close()
        self.closed = True

    def finalize_json_array(self, json_path: str) -> None:
        self.close()
        with open(self.path, "r", encoding="utf-8") as src, open(json_path, "w", encoding="utf-8") as dst:
            dst.write("[")
            first = True
            for line in src:
                line = line.strip()
                if not line:
                    continue
                dst.write("\n" if first else ",\n")
                dst.write(line)
                first = False
            dst.write("]\n" if first else "\n]\n")
            dst.flush()
            os.fsync(dst.fileno())


class TableRow:
    def __init__(self, parent, table_name, strategy, unit="Zeilen", selectable=False):
        self.unit = unit
        self.frame = tk.Frame(parent, bg=NORMAL_ROW_BG)
        self.frame.pack(fill=tk.X, pady=1, padx=2, ipady=3)

        self.name_var = tk.StringVar(value=table_name)
        self.strategy_var = tk.StringVar(value=strategy)
        self.count_var = tk.StringVar(value="0 " + unit)
        self.status_var = tk.StringVar(value="Wartend")
        self.selected_var = tk.BooleanVar(value=True)
        self._labels: list[tk.Widget] = []

        col = 0
        if selectable:
            check = tk.Checkbutton(
                self.frame,
                variable=self.selected_var,
                bg=NORMAL_ROW_BG,
                activebackground=NORMAL_ROW_BG,
                highlightthickness=0,
                bd=0,
            )
            check.grid(row=0, column=col, sticky="w", padx=(4, 0))
            self._labels.append(check)
            col += 1

        self._labels.append(
            tk.Label(
                self.frame,
                textvariable=self.name_var,
                width=34 if selectable else 36,
                anchor=tk.W,
                bg=NORMAL_ROW_BG,
                fg=UI_FG,
                font=ui_font(9),
            )
        )
        self._labels[-1].grid(row=0, column=col, sticky="w", padx=(8, 8))
        col += 1

        self._labels.append(
            tk.Label(
                self.frame,
                textvariable=self.strategy_var,
                width=22,
                anchor=tk.W,
                bg=NORMAL_ROW_BG,
                fg=UI_FG,
                font=ui_font(9),
            )
        )
        self._labels[-1].grid(row=0, column=col, sticky="w", padx=(0, 8))
        col += 1

        self.bar = tk.Canvas(
            self.frame,
            width=220,
            height=14,
            bg="#d4cfc4",
            highlightthickness=1,
            highlightbackground="#c4bfb4",
        )
        self.bar_rect = self.bar.create_rectangle(0, 0, 0, 14, fill="#3d6b99", outline="")
        self.bar.grid(row=0, column=col, sticky="ew", padx=(0, 8))
        bar_col = col
        col += 1

        self._labels.append(
            tk.Label(
                self.frame,
                textvariable=self.count_var,
                width=22,
                anchor=tk.W,
                bg=NORMAL_ROW_BG,
                fg=UI_FG,
                font=ui_font(9),
            )
        )
        self._labels[-1].grid(row=0, column=col, sticky="w", padx=(0, 8))
        col += 1

        self._labels.append(
            tk.Label(
                self.frame,
                textvariable=self.status_var,
                width=28,
                anchor=tk.W,
                bg=NORMAL_ROW_BG,
                fg=UI_FG,
                font=ui_font(9),
            )
        )
        self._labels[-1].grid(row=0, column=col, sticky="w", padx=(0, 8))
        self.frame.columnconfigure(bar_col, weight=1)
        self._bind_hover(self.frame)

    def _bind_hover(self, widget) -> None:
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        for child in widget.winfo_children():
            self._bind_hover(child)

    def _on_enter(self, _event=None) -> None:
        self._set_row_bg(HOVER_ROW_BG)

    def _on_leave(self, _event=None) -> None:
        try:
            x, y = self.frame.winfo_pointerxy()
            under = self.frame.winfo_containing(x, y)
        except tk.TclError:
            under = None
        widget = under
        while widget is not None:
            if widget == self.frame:
                return
            widget = getattr(widget, "master", None)
        self._set_row_bg(NORMAL_ROW_BG)

    def _set_row_bg(self, color: str) -> None:
        self.frame.configure(bg=color)
        for label in self._labels:
            try:
                label.configure(bg=color)
            except tk.TclError:
                pass
            try:
                label.configure(activebackground=color)
            except tk.TclError:
                pass

    def set_progress(self, exported, total, status):
        self.status_var.set(status)
        width = 220
        if total and total > 0:
            self.count_var.set(f"{exported} / {total} {self.unit}")
            frac = min(exported, total) / float(max(total, 1))
        else:
            self.count_var.set(f"{exported} {self.unit}")
            frac = 1.0 if exported else 0.0
        self.bar.coords(self.bar_rect, 0, 0, int(width * frac), 14)


IONOS_IMAP_HOST = "imap.ionos.de"
IONOS_IMAP_PORT = 993
MAIL_FETCH_BATCH = 25
MAIL_BROWSE_PAGE = 200
MAIL_FILES_PER_DIR = 1000
MAIL_RECONNECT_EVERY = 2000


def decode_mime_header(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def parse_headers_only(raw):
    if not raw:
        return email.message_from_bytes(b"")
    split = raw.find(b"\r\n\r\n")
    if split < 0:
        split = raw.find(b"\n\n")
    header_bytes = raw[:split] if split >= 0 else raw[:16384]
    try:
        return email.message_from_bytes(header_bytes)
    except Exception:
        return email.message_from_bytes(b"")


def decode_imap_folder_name(name):
    raw = name.encode("ascii", "replace") if isinstance(name, str) else name
    try:
        return imaplib.IMAP4._decode_modified_utf7(raw)
    except Exception:
        return name if isinstance(name, str) else name.decode("utf-8", "replace")


def parse_imap_list_line(raw):
    if isinstance(raw, tuple):
        raw = raw[-1]
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", "replace")
    else:
        text = str(raw)
    match = re.match(r'\((?P<flags>.*)\)\s+"(?P<delim>[^"]*)"\s+(?P<name>.*)$', text)
    if not match:
        match = re.match(r'\((?P<flags>.*)\)\s+(?P<delim>\S+)\s+(?P<name>.*)$', text)
    if not match:
        return None
    flags = match.group("flags") or ""
    name = match.group("name").strip()
    if name.startswith('"') and name.endswith('"') and len(name) >= 2:
        name = name[1:-1].replace('\\"', '"')
    noselect = "\\Noselect" in flags or "\\NonExistent" in flags
    return {"imap_name": name, "display": decode_imap_folder_name(name), "noselect": noselect}


def folder_dir_name(display, imap_name):
    source = display or imap_name or "ordner"
    cleaned = re.sub(r"[^\w.\-]+", "_", source, flags=re.UNICODE).strip("._")
    return cleaned or "ordner"


class EmailBackupTab:
    def __init__(self, parent, root):
        self.root = root
        self.parent = parent
        self.imap = None
        self.running = False
        self.output_dir = ""
        self.folders = []
        self.current_index = 0
        self.browse_metas = []
        self.browse_page = 0
        self._build()

    def _ui(self, fn):
        self.root.after(0, fn)

    def _build(self):
        login = ui_labelframe(self.parent, " 1. IONOS IMAP-Login ")
        login.pack(fill=tk.X, padx=10, pady=(10, 6))
        ui_label(
            login,
            text="Große Postfächer werden in 25er-Batches geholt. Nach Timeout: Ordner neu holen, fertige Ordner abwählen, nur den Rest sichern.",
        ).pack(anchor=tk.W)

        row = ui_frame(login)
        row.pack(fill=tk.X, pady=6)
        ui_label(row, text="E-Mail").pack(side=tk.LEFT)
        self.mail_user = tk.StringVar()
        ui_entry(row, textvariable=self.mail_user, width=32).pack(side=tk.LEFT, padx=(4, 10))
        ui_label(row, text="Passwort").pack(side=tk.LEFT)
        self.mail_pass = tk.StringVar()
        ui_entry(row, textvariable=self.mail_pass, width=20, show="*").pack(side=tk.LEFT, padx=(4, 10))
        ui_label(row, text="Server").pack(side=tk.LEFT)
        self.mail_host = tk.StringVar(value=IONOS_IMAP_HOST)
        ui_entry(row, textvariable=self.mail_host, width=18).pack(side=tk.LEFT, padx=(4, 10))
        ui_label(row, text="Port").pack(side=tk.LEFT)
        self.mail_port = tk.StringVar(value=str(IONOS_IMAP_PORT))
        ui_entry(row, textvariable=self.mail_port, width=6).pack(side=tk.LEFT, padx=(4, 0))

        btn_row = ui_frame(login)
        btn_row.pack(fill=tk.X)
        self.list_btn = ui_button(btn_row, "Ordner holen", command=self.start_list_folders)
        self.list_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.save_btn = ui_button(btn_row, "Ordner sichern", command=self.start_save_folders, state=tk.DISABLED)
        self.save_btn.pack(side=tk.LEFT)
        self.mail_status = ui_label(btn_row, text="Status: Bereit", font=ui_font(12, "italic"))
        self.mail_status.pack(side=tk.RIGHT)

        folders_box = ui_labelframe(self.parent, " 2. Ordner auswählen ")
        folders_box.pack(fill=tk.X, padx=10, pady=(0, 6))
        pick_row = ui_frame(folders_box)
        pick_row.pack(fill=tk.X, padx=4, pady=(0, 4))
        ui_button(pick_row, "Alle", command=lambda: self._set_all_folders(True)).pack(side=tk.LEFT, padx=(0, 8))
        ui_button(pick_row, "Keine", command=lambda: self._set_all_folders(False)).pack(side=tk.LEFT)
        header = ui_frame(folders_box)
        header.pack(fill=tk.X, padx=4, pady=(0, 4))
        ui_label(header, text="Ordner", width=36, font=ui_font(12, "bold")).grid(row=0, column=0, sticky="w")
        ui_label(header, text="IMAP", width=22, font=ui_font(12, "bold")).grid(row=0, column=1, sticky="w")
        ui_label(header, text="Fortschritt", width=24, font=ui_font(12, "bold")).grid(row=0, column=2, sticky="w")
        ui_label(header, text="Mails", width=22, font=ui_font(12, "bold")).grid(row=0, column=3, sticky="w")
        ui_label(header, text="Status", width=28, font=ui_font(12, "bold")).grid(row=0, column=4, sticky="w")

        canvas_host = ui_frame(folders_box)
        canvas_host.pack(fill=tk.X)
        self.canvas = tk.Canvas(
            canvas_host, highlightthickness=0, height=160, bg=UI_BG, highlightbackground=UI_BG
        )
        scrollbar = ui_scroll(canvas_host, orient=tk.VERTICAL, command=self.canvas.yview)
        self.rows_frame = ui_frame(self.canvas)
        self.rows_frame.bind(
            "<Configure>",
            lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.rows_window = self.canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.X, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.rows_window, width=e.width))
        self.canvas.bind("<Enter>", lambda _e: self.canvas.bind_all("<MouseWheel>", self._on_mousewheel))
        self.canvas.bind("<Leave>", lambda _e: self.canvas.unbind_all("<MouseWheel>"))

        browse = ui_labelframe(self.parent, " 3. Gesicherte Mails (nur Übersicht, nicht öffenbar) ")
        browse.pack(fill=tk.BOTH, padx=10, pady=(0, 10), expand=True)
        ui_label(
            browse,
            text="Ordner | Von | An | Betreff | Datum | Größe",
            font=ui_font(12, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self.tree = tk.Listbox(
            browse,
            bg=UI_FIELD,
            fg=UI_FG,
            font=ui_mono(11),
            activestyle="none",
            selectbackground=HOVER_ROW_BG,
            selectforeground=UI_FG,
            highlightthickness=1,
            highlightbackground="#c4bfb4",
        )
        yscroll = ui_scroll(browse, orient=tk.VERTICAL, command=self.tree.yview)
        xscroll = ui_scroll(browse, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=1, column=0, sticky="nsew")
        yscroll.grid(row=1, column=1, sticky="ns")
        xscroll.grid(row=2, column=0, sticky="ew")
        browse.rowconfigure(1, weight=1)
        browse.columnconfigure(0, weight=1)
        pager = ui_frame(browse)
        pager.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ui_button(pager, "Zurück", command=self._browse_prev).pack(side=tk.LEFT)
        self.browse_page_label = ui_label(pager, text="Keine Mails")
        self.browse_page_label.pack(side=tk.LEFT, padx=12)
        ui_button(pager, "Weiter", command=self._browse_next).pack(side=tk.LEFT)
        for seq in ("<Double-1>", "<Return>", "<Key-space>"):
            self.tree.bind(seq, self._block_open)

    def _block_open(self, event=None):
        return "break"

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _set_status(self, text):
        self.mail_status.config(text=text)

    def _set_busy(self, busy):
        self.running = busy
        state = tk.DISABLED if busy else tk.NORMAL
        self.list_btn.config(state=state)
        self.save_btn.config(state=tk.DISABLED if busy else (tk.NORMAL if self.folders else tk.DISABLED))

    def start_list_folders(self):
        if self.running:
            return
        if not self.mail_user.get().strip() or not self.mail_pass.get():
            messagebox.showerror("Login", "E-Mail und Passwort für IONOS eintragen.")
            return
        self._set_busy(True)
        self._set_status("Status: Verbinde mit IMAP …")
        threading.Thread(target=self._list_folders_background, daemon=True).start()

    def start_save_folders(self):
        if self.running:
            return
        if not self.folders:
            messagebox.showwarning("Hinweis", "Zuerst Ordner holen.")
            return
        for folder in self.folders:
            widget = folder.get("row_widget")
            folder["selected"] = bool(widget.selected_var.get()) if widget else folder.get("selected", True)
        chosen = [f for f in self.folders if f.get("selected", True)]
        if not chosen:
            messagebox.showwarning("Hinweis", "Mindestens einen Ordner ankreuzen.")
            return
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.output_dir = os.path.join(os.getcwd(), "email_sicherung_%s" % stamp)
        os.makedirs(self.output_dir, exist_ok=True)
        self.tree.delete(0, tk.END)
        self.browse_metas = []
        self.browse_page = 0
        self._update_browse_label()
        self.current_index = 0
        for folder in self.folders:
            folder["exported"] = 0
            if folder.get("selected", True):
                folder["status"] = "Wartend"
            else:
                folder["status"] = "Übersprungen"
            self._refresh_folder_row(folder)
        self._set_busy(True)
        self._set_status("Status: Sichere %s von %s Ordnern …" % (len(chosen), len(self.folders)))
        threading.Thread(target=self._save_folders_background, daemon=True).start()

    def _connect(self):
        host = self.mail_host.get().strip() or IONOS_IMAP_HOST
        try:
            port = int(self.mail_port.get().strip() or IONOS_IMAP_PORT)
        except ValueError:
            port = IONOS_IMAP_PORT
        context = ssl.create_default_context()
        client = imaplib.IMAP4_SSL(host, port, ssl_context=context)
        try:
            client.sock.settimeout(180)
        except Exception:
            pass
        client.login(self.mail_user.get().strip(), self.mail_pass.get())
        try:
            client.enable("UTF8=ACCEPT")
        except Exception:
            pass
        return client

    def _disconnect(self, client):
        if not client:
            return
        try:
            client.logout()
        except Exception:
            try:
                client.shutdown()
            except Exception:
                pass

    def _list_folders_background(self):
        client = None
        try:
            client = self._connect()
            typ, data = client.list()
            if typ != "OK" or not data:
                raise RuntimeError("IMAP LIST fehlgeschlagen: %s" % typ)
            folders = []
            for line in data:
                parsed = parse_imap_list_line(line)
                if not parsed or parsed["noselect"]:
                    continue
                folders.append(
                    {
                        "imap_name": parsed["imap_name"],
                        "display": parsed["display"],
                        "total": 0,
                        "exported": 0,
                        "status": "Wartend",
                        "row_widget": None,
                        "selected": True,
                    }
                )
            if not folders:
                raise RuntimeError("Keine IMAP-Ordner gefunden.")
            self.folders = folders
            self._ui(self._show_folders)
        except Exception as exc:
            self._ui(lambda: messagebox.showerror("IMAP", str(exc)))
            self._ui(lambda: self._set_status("Status: Fehler"))
            self._ui(lambda: self._set_busy(False))
        finally:
            self._disconnect(client)

    def _show_folders(self):
        for child in self.rows_frame.winfo_children():
            child.destroy()
        for folder in self.folders:
            widget = TableRow(
                self.rows_frame, folder["display"], folder["imap_name"], unit="Mails", selectable=True
            )
            folder["row_widget"] = widget
            widget.selected_var.set(True)
            widget.set_progress(0, None, "Wartend")
        self._set_busy(False)
        self.save_btn.config(state=tk.NORMAL)
        self._set_status(
            "Status: %s Ordner gefunden. Fertige Ordner abwählen, dann den Rest sichern." % len(self.folders)
        )

    def _set_all_folders(self, on):
        for folder in self.folders:
            widget = folder.get("row_widget")
            if widget:
                widget.selected_var.set(bool(on))
            folder["selected"] = bool(on)

    def _save_folders_background(self):
        client = None
        try:
            client = self._connect()
            index_path = os.path.join(self.output_dir, "_index.jsonl")
            fetched_since_connect = 0
            while self.current_index < len(self.folders) and self.running:
                folder = self.folders[self.current_index]
                if not folder.get("selected", True):
                    folder["status"] = "Übersprungen"
                    self._refresh_folder_row(folder)
                    self.current_index += 1
                    continue
                fetched_since_connect, client = self._export_folder(
                    client, folder, index_path, fetched_since_connect
                )
                if fetched_since_connect >= MAIL_RECONNECT_EVERY:
                    self._disconnect(client)
                    client = self._connect()
                    fetched_since_connect = 0
                self.current_index += 1
            self._ui(self._finish_save)
        except Exception as exc:
            self._ui(lambda: messagebox.showerror("IMAP", str(exc)))
            self._ui(lambda: self._set_status("Status: Fehler"))
            self._ui(lambda: self._set_busy(False))
        finally:
            self._disconnect(client)

    def _export_folder(self, client, folder, index_path, fetched_since_connect):
        folder["status"] = "Öffne …"
        self._refresh_folder_row(folder)
        imap_name = folder["imap_name"]
        typ, data = client.select('"%s"' % imap_name.replace('"', '\\"'), readonly=True)
        if typ != "OK":
            typ, data = client.select(imap_name, readonly=True)
        if typ != "OK":
            folder["status"] = "Fehler beim Öffnen"
            self._refresh_folder_row(folder)
            return fetched_since_connect, client
        try:
            exists = int(data[0]) if data and data[0] is not None else 0
        except (TypeError, ValueError):
            exists = 0
        folder["total"] = exists
        folder["exported"] = 0
        folder_dir = os.path.join(self.output_dir, folder_dir_name(folder["display"], imap_name))
        os.makedirs(folder_dir, exist_ok=True)
        if exists == 0:
            folder["status"] = "Fertig (0 Mails, leer)"
            self._refresh_folder_row(folder)
            return fetched_since_connect, client

        folder["status"] = "Sichere …"
        self._refresh_folder_row(folder)

        for start in range(1, exists + 1, MAIL_FETCH_BATCH):
            if not self.running:
                return fetched_since_connect, client
            end = min(start + MAIL_FETCH_BATCH - 1, exists)
            if fetched_since_connect >= MAIL_RECONNECT_EVERY:
                client = self._reconnect_folder(client, imap_name)
                fetched_since_connect = 0
            seq_set = "%s:%s" % (start, end)
            typ, fetched = client.fetch(seq_set, "(UID RFC822.SIZE BODY.PEEK[])")
            if typ != "OK" or not fetched:
                client = self._reconnect_folder(client, imap_name)
                fetched_since_connect = 0
                typ, fetched = client.fetch(seq_set, "(UID RFC822.SIZE BODY.PEEK[])")
                if typ != "OK" or not fetched:
                    continue
            batch_metas = []
            for msg in self._parse_fetch_payloads(fetched):
                folder["exported"] += 1
                meta = self._save_message(folder, folder_dir, msg, folder["exported"])
                batch_metas.append(meta)
                fetched_since_connect += 1
            self._append_index_batch(index_path, batch_metas)
            folder["status"] = "Batch %s–%s / %s" % (
                format_de_int(start),
                format_de_int(end),
                format_de_int(exists),
            )
            self._refresh_folder_row(folder)
            self._ui(lambda rows=list(batch_metas): self._add_browse_rows(rows))
            self._ui(
                lambda f=folder: self._set_status(
                    "Status: %s – %s / %s Mails"
                    % (f["display"], format_de_int(f["exported"]), format_de_int(f["total"]))
                )
            )

        folder["status"] = "Fertig (%s Mails)" % format_de_int(folder["exported"])
        self._refresh_folder_row(folder)
        return fetched_since_connect, client

    def _reconnect_folder(self, client, imap_name):
        self._disconnect(client)
        client = self._connect()
        typ, _sel = client.select('"%s"' % imap_name.replace('"', '\\"'), readonly=True)
        if typ != "OK":
            client.select(imap_name, readonly=True)
        return client

    def _parse_fetch_payloads(self, fetched):
        messages = []
        i = 0
        while i < len(fetched):
            part = fetched[i]
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
            i += 1
        return messages

    def _save_message(self, folder, folder_dir, msg, seq):
        raw = msg["raw"]
        uid = msg.get("uid") or str(seq)
        parsed = parse_headers_only(raw)
        subject = decode_mime_header(parsed.get("Subject"))
        from_addr = decode_mime_header(parsed.get("From"))
        to_addr = decode_mime_header(parsed.get("To"))
        date_raw = parsed.get("Date") or ""
        date_iso = date_raw
        try:
            date_iso = parsedate_to_datetime(date_raw).isoformat()
        except Exception:
            pass
        batch_dir = os.path.join(folder_dir, "b%04d" % ((seq - 1) // MAIL_FILES_PER_DIR))
        os.makedirs(batch_dir, exist_ok=True)
        filename = "%07d_%s.eml" % (seq, sanitize_filename(uid))
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

    def _append_index_batch(self, index_path, metas):
        if not metas:
            return
        with open(index_path, "a", encoding="utf-8") as handle:
            for meta in metas:
                json.dump(meta, handle, ensure_ascii=False)
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _add_browse_rows(self, metas):
        if not metas:
            return
        on_last = self._is_last_browse_page()
        self.browse_metas.extend(metas)
        if on_last:
            self.browse_page = self._max_browse_page()
            self._render_browse_page()
        else:
            self._update_browse_label()

    def _max_browse_page(self):
        total = len(self.browse_metas)
        if total <= 0:
            return 0
        return (total - 1) // MAIL_BROWSE_PAGE

    def _is_last_browse_page(self):
        return self.browse_page >= self._max_browse_page()

    def _browse_prev(self):
        if self.browse_page > 0:
            self.browse_page -= 1
            self._render_browse_page()

    def _browse_next(self):
        if self.browse_page < self._max_browse_page():
            self.browse_page += 1
            self._render_browse_page()

    def _update_browse_label(self):
        total = len(self.browse_metas)
        if total <= 0:
            self.browse_page_label.config(text="Keine Mails")
            return
        start = self.browse_page * MAIL_BROWSE_PAGE + 1
        end = min(total, (self.browse_page + 1) * MAIL_BROWSE_PAGE)
        self.browse_page_label.config(
            text="Seite %s / %s · %s–%s von %s"
            % (
                self.browse_page + 1,
                self._max_browse_page() + 1,
                format_de_int(start),
                format_de_int(end),
                format_de_int(total),
            )
        )

    def _render_browse_page(self):
        self.tree.delete(0, tk.END)
        start = self.browse_page * MAIL_BROWSE_PAGE
        page = self.browse_metas[start : start + MAIL_BROWSE_PAGE]
        for meta in page:
            vals = self._browse_values(meta)
            line = " | ".join(str(v).replace("|", "/") for v in vals)
            self.tree.insert(tk.END, line)
        self._update_browse_label()

    def _browse_values(self, meta):
        size = meta.get("size") or 0
        if size >= 1024 * 1024:
            size_txt = "%.1f MB" % (size / (1024.0 * 1024.0))
        elif size >= 1024:
            size_txt = "%.1f KB" % (size / 1024.0)
        else:
            size_txt = "%s B" % size
        return (
            meta.get("folder") or "",
            meta.get("from") or "",
            meta.get("to") or "",
            meta.get("subject") or "(kein Betreff)",
            meta.get("date") or "",
            size_txt,
        )

    def _refresh_folder_row(self, folder):
        widget = folder.get("row_widget")
        if not widget:
            return
        exported = folder["exported"]
        total = folder.get("total") or None
        status = folder["status"]
        self._ui(lambda: widget.set_progress(exported, total, status))

    def _finish_save(self):
        self._set_busy(False)
        self.save_btn.config(state=tk.NORMAL)
        self._set_status("Status: Fertig. Dateien in %s" % self.output_dir)
        messagebox.showinfo("E-Mail-Sicherung", "Ordner gesichert nach:\n%s\n\nDie Liste zeigt nur Metadaten, kein Öffnen." % self.output_dir)

    def shutdown(self):
        self.running = False


class KingFallApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Königssturz – Beweissicherung")
        self.root.geometry("1100x780")
        self.root.minsize(920, 640)

        self.base_url = ""
        self.headers: dict = {}
        self.tables: list[dict] = []
        self.current_table_index = 0
        self.is_running = False
        self.paused_for_token = False
        self.output_dir = ""
        self.test_run = False
        self.page_size = PAGE_SIZE
        self.then_export = False
        self.phase = "idle"
        self.count_table_index = 0
        self.schema_vars: dict[str, tk.BooleanVar] = {}
        self.available_schemas: list[str] = []
        self.auth = AuthSession()
        self._stop_watchdog = threading.Event()

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        threading.Thread(target=self._token_watchdog, daemon=True).start()

    def _build_ui(self) -> None:
        apply_light_ui(self.root)

        menubar = tk.Menu(self.root)
        self.file_menu = tk.Menu(menubar, tearoff=0)
        self.file_menu.add_command(label="Test-Run", command=self.start_test_run)
        menubar.add_cascade(label="Datei", menu=self.file_menu)
        self.root.config(menu=menubar)

        shell = ui_frame(self.root)
        shell.pack(fill=tk.BOTH, expand=True)
        tabs = ui_frame(shell)
        tabs.pack(fill=tk.X, padx=8, pady=(8, 0))
        self._tab_db_btn = ui_button(tabs, "Supabase", command=self._show_db_tab)
        self._tab_mail_btn = ui_button(tabs, "E-Mail (IONOS)", command=self._show_mail_tab)
        self._tab_db_btn.pack(side=tk.LEFT, padx=(0, 4))
        self._tab_mail_btn.pack(side=tk.LEFT)
        self.db_tab = ui_frame(shell)
        self.mail_tab = ui_frame(shell)
        self._build_db_tab(self.db_tab)
        self.email_tab = EmailBackupTab(self.mail_tab, self.root)
        self._show_db_tab()

    def _show_db_tab(self) -> None:
        self.mail_tab.pack_forget()
        self.db_tab.pack(fill=tk.BOTH, expand=True)
        self._tab_db_btn.config(relief=tk.SUNKEN, bg="#c4bfb4")
        self._tab_mail_btn.config(relief=tk.RAISED, bg=UI_BUTTON)

    def _show_mail_tab(self) -> None:
        self.db_tab.pack_forget()
        self.mail_tab.pack(fill=tk.BOTH, expand=True)
        self._tab_mail_btn.config(relief=tk.SUNKEN, bg="#c4bfb4")
        self._tab_db_btn.config(relief=tk.RAISED, bg=UI_BUTTON)

    def _build_db_tab(self, parent) -> None:
        top = ui_labelframe(parent, " 1. cURL einfügen ")
        top.pack(fill=tk.X, padx=10, pady=(10, 6))

        ui_label(
            top,
            text="cURL aus den DevTools einfügen. Danach optional E-Mail/Passwort/TOTP – dann wird der Token automatisch erneuert.",
        ).pack(anchor=tk.W)

        self.curl_text = scrolledtext.ScrolledText(
            top,
            height=5,
            wrap=tk.WORD,
            font=ui_mono(12),
            bg=UI_FIELD,
            fg=UI_FG,
            insertbackground=UI_FG,
            highlightthickness=1,
            highlightbackground="#c4bfb4",
        )
        self.curl_text.pack(fill=tk.BOTH, expand=True, pady=6)
        try:
            self.curl_text.frame.configure(bg=UI_BG)
        except tk.TclError:
            pass

        login_row = ui_frame(top)
        login_row.pack(fill=tk.X, pady=(0, 6))
        ui_label(login_row, text="E-Mail").pack(side=tk.LEFT)
        self.email_var = tk.StringVar()
        ui_entry(login_row, textvariable=self.email_var, width=28).pack(side=tk.LEFT, padx=(4, 10))
        ui_label(login_row, text="Passwort").pack(side=tk.LEFT)
        self.password_var = tk.StringVar()
        ui_entry(login_row, textvariable=self.password_var, width=18, show="*").pack(side=tk.LEFT, padx=(4, 10))
        ui_label(login_row, text="TOTP").pack(side=tk.LEFT)
        self.totp_var = tk.StringVar()
        ui_entry(login_row, textvariable=self.totp_var, width=8).pack(side=tk.LEFT, padx=(4, 10))
        ui_button(login_row, "Anmelden", command=self.login_clicked).pack(side=tk.LEFT)
        self.login_status = ui_label(login_row, text="Kein Auto-Refresh", font=ui_font(12, "italic"))
        self.login_status.pack(side=tk.RIGHT)

        btn_row = ui_frame(top)
        btn_row.pack(fill=tk.X)

        self.fetch_btn = ui_button(btn_row, "Hole Tables", command=self.fetch_tables_only)
        self.fetch_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.start_btn = ui_button(
            btn_row,
            "Der König ist tot, lang lebe der König",
            command=self.start_export_workflow,
        )
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.resume_btn = ui_button(btn_row, "Fortfahren", command=self.resume_workflow, state=tk.DISABLED)
        self.resume_btn.pack(side=tk.LEFT)

        self.status_label = ui_label(btn_row, text="Status: Bereit", font=ui_font(12, "italic"))
        self.status_label.pack(side=tk.RIGHT)

        schema_box = ui_labelframe(parent, " 2. Schemas auswählen ")
        schema_box.pack(fill=tk.X, padx=10, pady=(0, 6))

        schema_btn_row = ui_frame(schema_box)
        schema_btn_row.pack(fill=tk.X, pady=(0, 6))
        ui_button(schema_btn_row, "Empfohlen", command=self._select_recommended_schemas).pack(side=tk.LEFT, padx=(0, 6))
        ui_button(schema_btn_row, "Alle", command=lambda: self._set_all_schemas(True)).pack(side=tk.LEFT, padx=(0, 6))
        ui_button(schema_btn_row, "Keine", command=lambda: self._set_all_schemas(False)).pack(side=tk.LEFT, padx=(0, 6))
        self.load_tables_btn = ui_button(
            schema_btn_row,
            "Tabellen laden",
            command=self.load_tables_from_selection,
            state=tk.DISABLED,
        )
        self.load_tables_btn.pack(side=tk.LEFT, padx=(12, 0))
        self.schema_hint = ui_label(
            schema_btn_row,
            text="Zuerst „Hole Tables“ – dann Schemas ankreuzen.",
            font=ui_font(12, "italic"),
        )
        self.schema_hint.pack(side=tk.RIGHT)

        self.schema_checks = ui_frame(schema_box)
        self.schema_checks.pack(fill=tk.X)

        bottom = ui_labelframe(parent, " 3. Tabellen ")
        bottom.pack(fill=tk.BOTH, padx=10, pady=(0, 10), expand=True)

        header = ui_frame(bottom)
        header.pack(fill=tk.X, padx=4, pady=(0, 4))
        ui_label(header, text="Tabelle", width=36, font=ui_font(12, "bold")).grid(row=0, column=0, sticky="w")
        ui_label(header, text="Primary Key", width=22, font=ui_font(12, "bold")).grid(row=0, column=1, sticky="w")
        ui_label(header, text="Fortschritt", width=24, font=ui_font(12, "bold")).grid(row=0, column=2, sticky="w")
        ui_label(header, text="Zeilen", width=22, font=ui_font(12, "bold")).grid(row=0, column=3, sticky="w")
        ui_label(header, text="Status", width=28, font=ui_font(12, "bold")).grid(row=0, column=4, sticky="w")

        canvas_host = ui_frame(bottom)
        canvas_host.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(canvas_host, highlightthickness=0, bg=UI_BG, highlightbackground=UI_BG)
        scrollbar = ui_scroll(canvas_host, orient=tk.VERTICAL, command=self.canvas.yview)
        self.rows_frame = ui_frame(self.canvas)
        self.rows_frame.bind(
            "<Configure>",
            lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.rows_window = self.canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<Enter>", lambda _e: self.canvas.bind_all("<MouseWheel>", self._on_mousewheel))
        self.canvas.bind("<Leave>", lambda _e: self.canvas.unbind_all("<MouseWheel>"))

    def _on_canvas_resize(self, event) -> None:
        self.canvas.itemconfigure(self.rows_window, width=event.width)

    def _on_mousewheel(self, event) -> None:
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def start_test_run(self) -> None:
        self.start_export_workflow(test_run=True)

    def fetch_tables_only(self) -> None:
        if self.is_running:
            return
        if not self._parse_curl_or_warn():
            return
        self.test_run = False
        self.then_export = False
        self.page_size = PAGE_SIZE
        self.output_dir = ""
        self.tables = []
        self.count_table_index = 0
        self.current_table_index = 0
        self._clear_rows()
        self._set_busy()
        self._set_status("Status: Lese Schema-Liste …")
        threading.Thread(target=self._fetch_schemas_background, daemon=True).start()

    def load_tables_from_selection(self) -> None:
        if self.is_running:
            return
        if not self._parse_curl_or_warn():
            return
        selected = self._selected_schemas()
        if not selected:
            messagebox.showwarning("Keine Auswahl", "Bitte mindestens ein Schema ankreuzen.")
            return
        self.test_run = False
        self.then_export = False
        self.page_size = PAGE_SIZE
        self.output_dir = ""
        self.tables = []
        self.count_table_index = 0
        self.current_table_index = 0
        self._clear_rows()
        self._set_busy()
        self._set_status(
            f"Status: Hole Tabellen aus {len(selected)} Schema(s): {', '.join(selected)}"
        )
        threading.Thread(target=self._fetch_tables_background, daemon=True).start()

    def start_export_workflow(self, test_run: bool = False) -> None:
        if self.is_running:
            return
        if not self._parse_curl_or_warn():
            return

        self.test_run = test_run
        self.then_export = True
        self.page_size = TEST_PAGE_SIZE if test_run else PAGE_SIZE
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        folder = f"beweissicherung_testrun_{stamp}" if test_run else f"beweissicherung_{stamp}"
        self.output_dir = os.path.join(os.getcwd(), folder)
        os.makedirs(self.output_dir, exist_ok=True)
        self._set_busy()

        if self.tables:
            self._set_status("Status: Starte Export mit geladenen Tabellen …")
            self._begin_export_from_loaded_tables()
            return

        selected = self._selected_schemas()
        if not selected:
            if self.schema_vars:
                self._reset_idle()
                messagebox.showwarning("Keine Auswahl", "Bitte mindestens ein Schema ankreuzen.")
                return
            # Noch keine Schema-Liste: public wie bisher.
        self.tables = []
        self.count_table_index = 0
        self.current_table_index = 0
        self._clear_rows()
        if test_run:
            self._set_status("Status: Test-Run – lese Tabellenliste …")
        else:
            self._set_status("Status: Lese Tabellenliste …")
        threading.Thread(target=self._fetch_tables_background, daemon=True).start()

    def _parse_curl_or_warn(self) -> bool:
        try:
            url, headers = parse_curl(self.curl_text.get("1.0", tk.END))
        except Exception as exc:
            messagebox.showerror("Parsing-Fehler", f"cURL konnte nicht gelesen werden:\n{exc}")
            return False
        self.auth.ingest_headers(headers)
        if self.auth.access_token:
            self.auth.apply_to_headers(headers)
        elif not header_value(headers, "Authorization"):
            messagebox.showerror(
                "Kein Token",
                "Weder cURL-Authorization noch Login-Session vorhanden.\n"
                "Bitte cURL einfügen und/oder anmelden.",
            )
            return False
        self.base_url = url
        self.headers = headers
        self._prefill_email_from_token()
        self._update_login_status()
        return True

    def _prefill_email_from_token(self) -> None:
        if self.email_var.get().strip():
            return
        token = self.auth.access_token or header_value(self.headers, "Authorization")
        if not token:
            return
        try:
            email = decode_jwt_payload(token).get("email")
        except Exception:
            return
        if email:
            self.email_var.set(str(email))

    def login_clicked(self) -> None:
        try:
            if self.curl_text.get("1.0", tk.END).strip():
                url, headers = parse_curl(self.curl_text.get("1.0", tk.END))
                self.base_url = url
                self.headers = headers
                self.auth.ingest_headers(headers)
        except Exception as exc:
            messagebox.showerror("Parsing-Fehler", f"cURL zuerst einfügen (für apikey/URL):\n{exc}")
            return
        email = self.email_var.get().strip()
        password = self.password_var.get()
        if not email or not password:
            messagebox.showerror("Login", "E-Mail und Passwort eingeben.")
            return
        self.login_status.config(text="Anmeldung läuft …")
        totp = self.totp_var.get().strip()
        threading.Thread(target=self._login_background, args=(email, password, totp), daemon=True).start()

    def _login_background(self, email: str, password: str, totp: str) -> None:
        try:
            self.auth.login(email, password, totp or None)
            self.auth.apply_to_headers(self.headers)
            self._ui(lambda: self.totp_var.set(""))
            self._ui(self._update_login_status)
            left = int(max(0, self.auth.seconds_left()))
            self._ui(
                lambda: messagebox.showinfo(
                    "Login",
                    "Anmeldung erfolgreich. Der Token wird automatisch erneuert, "
                    f"solange die Session läuft (aktuell ca. {left // 60} min übrig).",
                )
            )
        except Exception as exc:
            self._ui(lambda: self.login_status.config(text="Login fehlgeschlagen"))
            self._ui(lambda: messagebox.showerror("Login", str(exc)))

    def _update_login_status(self) -> None:
        if self.auth.refresh_token:
            left = int(max(0, self.auth.seconds_left()))
            stamp = datetime.fromtimestamp(self.auth.expires_at).strftime("%H:%M:%S") if self.auth.expires_at else "?"
            self.login_status.config(text=f"Auto-Refresh an · gültig bis {stamp} ({left // 60} min)")
        elif self.auth.access_token:
            left = int(max(0, self.auth.seconds_left()))
            self.login_status.config(text=f"Token aus cURL · {left // 60} min, ohne Refresh")
        else:
            self.login_status.config(text="Kein Auto-Refresh")

    def _token_watchdog(self) -> None:
        while not self._stop_watchdog.wait(15):
            if not self.auth.refresh_token or self.auth.seconds_left() > 90:
                continue
            if self.auth.refresh():
                self.auth.apply_to_headers(self.headers)
                self._ui(self._update_login_status)
            else:
                self._ui(lambda: self.login_status.config(text="Refresh fehlgeschlagen – neu anmelden"))

    def _ensure_fresh_token(self) -> bool:
        if self.auth.seconds_left() > 30 and self.auth.access_token:
            self.auth.apply_to_headers(self.headers)
            return True
        if self.auth.refresh():
            self.auth.apply_to_headers(self.headers)
            self._ui(self._update_login_status)
            return True
        return bool(self.auth.access_token)

    def _set_busy(self) -> None:
        self.start_btn.config(state=tk.DISABLED)
        self.fetch_btn.config(state=tk.DISABLED)
        self.load_tables_btn.config(state=tk.DISABLED)
        self.resume_btn.config(state=tk.DISABLED)
        self.file_menu.entryconfig("Test-Run", state=tk.DISABLED)
        self.paused_for_token = False
        self.is_running = True

    def _clear_rows(self) -> None:
        for child in self.rows_frame.winfo_children():
            child.destroy()

    def _selected_schemas(self) -> list[str]:
        return [name for name, var in self.schema_vars.items() if var.get()]

    def _set_all_schemas(self, value: bool) -> None:
        for var in self.schema_vars.values():
            var.set(value)

    def _select_recommended_schemas(self) -> None:
        for name, var in self.schema_vars.items():
            var.set(self._is_recommended_schema(name))

    def _is_recommended_schema(self, name: str) -> bool:
        if name in DEFAULT_UNCHECKED_SCHEMAS:
            return False
        if name in DEFAULT_CHECKED_SCHEMAS:
            return True
        return True

    def _fetch_schemas_background(self) -> None:
        self.phase = "listing_schemas"
        try:
            response = self._run_sql(SCHEMAS_SQL, raise_token=True)
            rows = extract_rows(response)
            names = []
            for row in rows:
                if isinstance(row, dict):
                    name = row.get("schema_name") or row.get("nspname")
                else:
                    name = str(row)
                if name:
                    names.append(name)
            if not names:
                self._ui(lambda: messagebox.showwarning("Hinweis", "Keine Schemas gefunden."))
                self._ui(self._reset_idle)
                return
            self.available_schemas = names
            self._ui(self._show_schema_checkboxes)
        except TokenExpired:
            self._ui(self._enter_token_pause)
        except Exception as exc:
            self._ui(lambda: self._fatal(str(exc)))

    def _show_schema_checkboxes(self) -> None:
        for child in self.schema_checks.winfo_children():
            child.destroy()
        self.schema_vars = {}
        for index, name in enumerate(self.available_schemas):
            var = tk.BooleanVar(value=self._is_recommended_schema(name))
            self.schema_vars[name] = var
            ui_check(self.schema_checks, text=name, variable=var).grid(
                row=index // 6,
                column=index % 6,
                sticky="w",
                padx=6,
                pady=2,
            )
        self._reset_idle()
        self.load_tables_btn.config(state=tk.NORMAL)
        checked = self._selected_schemas()
        self.schema_hint.config(text=f"{len(self.available_schemas)} Schemas – {len(checked)} vorausgewählt")
        self._set_status(
            f"Status: {len(self.available_schemas)} Schemas geladen. Auswahl prüfen, dann „Tabellen laden“."
        )

    def _fetch_tables_background(self) -> None:
        self.phase = "listing"
        selected = self._selected_schemas() or ["public"]
        rows = None
        last_error = None
        for sql in (tables_sql_pg(selected), tables_sql_info(selected)):
            try:
                response = self._run_sql(sql, raise_token=True)
                rows = extract_rows(response)
                if rows:
                    break
            except TokenExpired:
                self._ui(self._enter_token_pause)
                return
            except Exception as exc:
                last_error = exc
                rows = None

        try:
            if not rows:
                msg = f"Keine Tabellen in den gewählten Schemas gefunden ({', '.join(selected)})."
                if last_error:
                    msg = f"{msg}\n{last_error}"
                self._ui(lambda: messagebox.showwarning("Hinweis", msg))
                self._ui(self._reset_idle)
                return

            tables = []
            for row in rows:
                name = row.get("table_name")
                schema = row.get("table_schema") or "public"
                if not name:
                    continue
                pk_cols = parse_pk_cols(row.get("pk_cols"))
                est = parse_int(row.get("est_rows"))
                strategy = ", ".join(pk_cols) if pk_cols else "kein PK → ctid"
                display_rows = TEST_PAGE_SIZE if self.test_run else est
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
                        "row_widget": None,
                    }
                )

            self.tables = tables
            self.count_table_index = 0
            self._ui(self._after_tables_listed)
        except Exception as exc:
            self._ui(lambda: self._fatal(str(exc)))

    def _after_tables_listed(self) -> None:
        self._populate_table_rows()
        if self.test_run and self.then_export:
            self._set_status(
                f"Status: Test-Run – {len(self.tables)} Tabellen, je max. {TEST_PAGE_SIZE} Zeilen …"
            )
            self.phase = "exporting"
            self.current_table_index = 0
            threading.Thread(target=self._export_loop, daemon=True).start()
            return
        self.phase = "counting"
        self._set_status(f"Status: {len(self.tables)} Tabellen gefunden – zähle Zeilen …")
        threading.Thread(target=self._count_rows_loop, daemon=True).start()

    def _populate_table_rows(self) -> None:
        self._clear_rows()
        for table in self.tables:
            widget = TableRow(
                self.rows_frame,
                display_table(table.get("schema") or "public", table["name"]),
                table["strategy"],
            )
            table["row_widget"] = widget
            widget.set_progress(table["exported"], table["est_rows"], table["status"])

    def _count_rows_loop(self) -> None:
        self.phase = "counting"
        while self.count_table_index < len(self.tables):
            if self.paused_for_token or not self.is_running:
                return

            table = self.tables[self.count_table_index]
            table["status"] = "Zähle Zeilen …"
            self._refresh_row(table)
            quoted = quote_table(table.get("schema") or "public", table["name"])
            query = f"SELECT COUNT(*)::bigint AS n FROM {quoted};"
            try:
                response = self._run_sql(query, raise_token=True)
                n = extract_count(extract_rows(response))
                if n is not None:
                    table["row_count"] = n
                    table["est_rows"] = n
                table["status"] = "Bereit"
                self._refresh_row(table)
            except TokenExpired:
                table["status"] = "Token abgelaufen – Pause"
                self._refresh_row(table)
                self._ui(self._enter_token_pause)
                return
            except Exception as exc:
                table["status"] = f"COUNT fehlgeschlagen, Schätzung ({exc})"
                self._refresh_row(table)

            self.count_table_index += 1
            self._ui(
                lambda i=self.count_table_index: self._set_status(
                    f"Status: Zeilen gezählt ({i}/{len(self.tables)}) …"
                )
            )

        if self.then_export:
            self.phase = "exporting"
            self.current_table_index = 0
            self._ui(
                lambda: self._set_status(
                    f"Status: {len(self.tables)} Tabellen, {self._total_rows_label()} – Export läuft …"
                )
            )
            self._export_loop()
            return

        self._ui(self._finish_fetch)

    def _begin_export_from_loaded_tables(self) -> None:
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
                table["est_rows"] = TEST_PAGE_SIZE
            else:
                table["est_rows"] = table.get("row_count") or table.get("est_rows")
            self._refresh_row(table)
        threading.Thread(target=self._export_loop, daemon=True).start()

    def _finish_fetch(self) -> None:
        self.is_running = False
        self.phase = "idle"
        self.start_btn.config(state=tk.NORMAL)
        self.fetch_btn.config(state=tk.NORMAL)
        self.load_tables_btn.config(state=tk.NORMAL if self.schema_vars else tk.DISABLED)
        self.resume_btn.config(state=tk.DISABLED)
        self.file_menu.entryconfig("Test-Run", state=tk.NORMAL)
        pk_n = sum(1 for t in self.tables if t.get("pk_cols"))
        ctid_n = len(self.tables) - pk_n
        self._set_status(
            f"Status: {len(self.tables)} Tabellen geladen – {self._total_rows_label()} – "
            f"{pk_n} mit Primary Key, {ctid_n} per ctid. Bereit zum Export."
        )

    def _total_rows_label(self) -> str:
        total = 0
        missing = 0
        for table in self.tables:
            count = table.get("row_count")
            if count is None:
                missing += 1
                continue
            total += int(count)
        text = f"{format_de_int(total)} Zeilen insgesamt"
        if missing:
            text += f" ({missing} Tabellen ohne COUNT)"
        return text

    def _export_loop(self) -> None:
        while self.current_table_index < len(self.tables):
            if self.paused_for_token or not self.is_running:
                return

            table = self.tables[self.current_table_index]
            table["status"] = "Exportiere …"
            self._refresh_row(table)

            try:
                self._export_one_table(table)
            except TokenExpired:
                table["status"] = "Token abgelaufen – Pause"
                if table["writer"]:
                    table["writer"].close()
                    table["writer"] = None
                    table["resume_writer"] = True
                self._refresh_row(table)
                self._write_checkpoint()
                self._ui(self._enter_token_pause)
                return
            except Exception as exc:
                table["status"] = f"Fehler: {exc}"
                if table["writer"]:
                    table["writer"].close()
                    table["writer"] = None
                self._refresh_row(table)

            self.current_table_index += 1

        if self.is_running and not self.paused_for_token:
            self._ui(self._finish_all)

    def _export_one_table(self, table: dict) -> None:
        t_name = table["name"]
        schema_name = table.get("schema") or "public"
        pk_cols = table["pk_cols"]
        quoted_table = quote_table(schema_name, t_name)
        safe_file = table_file_stem(schema_name, t_name)
        jsonl_path = os.path.join(self.output_dir, f"{safe_file}.jsonl")
        final_path = os.path.join(self.output_dir, f"beweissicherung_{safe_file}.json")
        schema_path = os.path.join(self.output_dir, f"schema_{safe_file}.json")
        create_sql_path = os.path.join(self.output_dir, f"schema_{safe_file}.sql")

        if not table.get("schema_written"):
            table["status"] = "Sichere Schema …"
            self._refresh_row(table)
            schema = self._fetch_table_schema(table)
            write_json_file(schema_path, schema)
            with open(create_sql_path, "w", encoding="utf-8") as handle:
                handle.write(schema["create_table_sql"])
                handle.flush()
                os.fsync(handle.fileno())
            table["schema_written"] = True
            self._write_checkpoint()

        if table.get("row_count") == 0:
            write_json_file(final_path, [])
            if table.get("writer"):
                table["writer"].close()
                table["writer"] = None
            table["status"] = "Fertig (0 Zeilen, leer)"
            table["exported"] = 0
            table["est_rows"] = 0
            self._refresh_row(table)
            self._write_checkpoint()
            return

        if table["writer"] is None:
            table["writer"] = JsonlWriter(jsonl_path, resume=bool(table.get("resume_writer")))
            table["jsonl_path"] = jsonl_path
            table["resume_writer"] = False

        while self.is_running and not self.paused_for_token:
            limit = self.page_size
            if pk_cols:
                order = ", ".join(quote_ident(c) for c in pk_cols)
                query = (
                    f"SELECT * FROM {quoted_table} "
                    f"ORDER BY {order} ASC "
                    f"LIMIT {limit} OFFSET {table['offset']};"
                )
            elif table["last_ctid"]:
                query = (
                    f"SELECT ctid, * FROM {quoted_table} "
                    f"WHERE ctid > '{table['last_ctid']}'::tid "
                    f"ORDER BY ctid ASC LIMIT {limit};"
                )
            else:
                query = (
                    f"SELECT ctid, * FROM {quoted_table} "
                    f"ORDER BY ctid ASC LIMIT {limit};"
                )

            response = self._run_sql(query, raise_token=True, kind="data")
            records = extract_rows(response)
            if pk_cols is None or not pk_cols:
                records = [strip_ctid(r) for r in records]

            if not records:
                self._complete_table_file(table, final_path)
                if table["exported"] == 0:
                    table["status"] = "Fertig (0 Zeilen, leer)"
                else:
                    table["status"] = f"Fertig ({table['exported']} Zeilen)"
                self._refresh_row(table)
                return

            table["writer"].append(records)
            table["exported"] += len(records)

            if pk_cols:
                table["offset"] += limit
            else:
                raw_last = extract_rows(response)[-1]
                table["last_ctid"] = raw_last.get("ctid") if isinstance(raw_last, dict) else None
                if not table["last_ctid"]:
                    table["offset"] += limit

            if table["est_rows"] and table["exported"] > table["est_rows"]:
                table["est_rows"] = table["exported"]

            table["status"] = "Exportiere …"
            self._refresh_row(table)
            self._write_checkpoint()

            # Test-Run: genau eine Seite (10 Zeilen) pro Tabelle, dann weiter zur nächsten.
            if self.test_run or len(records) < limit:
                self._complete_table_file(table, final_path)
                table["status"] = f"Fertig ({table['exported']} Zeilen)"
                self._refresh_row(table)
                return

    def _fetch_table_schema(self, table: dict) -> dict:
        schema_name = table.get("schema") or "public"
        name = table["name"]
        packed = extract_rows(self._run_sql(table_schema_sql(schema_name, name), raise_token=True, kind="meta"))
        row = packed[0] if packed and isinstance(packed[0], dict) else {}
        columns = json_list(row.get("columns"))
        constraints = json_list(row.get("constraints"))
        indexes = json_list(row.get("indexes"))
        schema = {
            "schema": schema_name,
            "table": name,
            "row_count": table.get("row_count"),
            "primary_key": table.get("pk_cols") or [],
            "columns": normalize_columns(columns),
            "constraints": normalize_constraints(constraints),
            "indexes": normalize_indexes(indexes),
        }
        schema["create_table_sql"] = build_create_table_sql(schema)
        return schema

    def _write_checkpoint(self) -> None:
        if not self.output_dir:
            return
        snapshot = {
            "current_table_index": self.current_table_index,
            "tables": [
                {
                    "name": t["name"],
                    "exported": t["exported"],
                    "offset": t["offset"],
                    "last_ctid": t["last_ctid"],
                    "status": t["status"],
                }
                for t in self.tables
            ],
        }
        path = os.path.join(self.output_dir, "_checkpoint.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())

    def _complete_table_file(self, table: dict, final_path: str) -> None:
        writer: JsonlWriter = table["writer"]
        writer.finalize_json_array(final_path)
        table["writer"] = None
        self._write_checkpoint()

    def _run_sql(self, sql, raise_token=False, kind: str = "meta"):
        payload = {"query": sql, "disable_statement_timeout": True}
        timeout = 120 if kind == "data" else 60

        def post_once() -> requests.Response:
            self.auth.apply_to_headers(self.headers)
            return requests.post(self.base_url, headers=self.headers, json=payload, timeout=timeout)

        response = None
        for attempt in range(SQL_RETRY_429 + 1):
            pace_sql_request(kind)
            try:
                response = post_once()
            except requests.RequestException as exc:
                if raise_token:
                    raise RuntimeError(f"Netzwerkfehler: {exc}") from exc
                self._ui(lambda: self._fatal(f"Netzwerkfehler: {exc}"))
                return None

            if is_auth_error(response) and self.auth.refresh_token:
                if self.auth.refresh():
                    self.auth.apply_to_headers(self.headers)
                    self._ui(self._update_login_status)
                    pace_sql_request(kind)
                    try:
                        response = post_once()
                    except requests.RequestException as exc:
                        if raise_token:
                            raise RuntimeError(f"Netzwerkfehler: {exc}") from exc
                        self._ui(lambda: self._fatal(f"Netzwerkfehler: {exc}"))
                        return None

            if is_auth_error(response):
                if raise_token:
                    raise TokenExpired()
                self._ui(lambda: self._auth_failed(response.text))
                return None

            if is_throttle_error(response):
                note_sql_throttle()
                delay = throttle_delay(response, attempt)
                self._ui(
                    lambda d=delay, a=attempt + 1: self._set_status(
                        "Status: Rate-Limit, warte %s s (Versuch %s) …" % (int(d), a)
                    )
                )
                time.sleep(delay)
                continue

            if response.status_code not in (200, 201):
                msg = f"HTTP {response.status_code}: {response.text[:500]}"
                if raise_token:
                    raise RuntimeError(msg)
                self._ui(lambda: self._fatal(msg))
                return None

            data = response.json()
            if isinstance(data, dict) and data.get("error"):
                msg = str(data.get("error"))
                if raise_token:
                    raise RuntimeError(msg)
                self._ui(lambda: self._fatal(msg))
                return None
            return response

        msg = "HTTP 429: zu viele Anfragen, auch nach Warten. Später erneut versuchen."
        if raise_token:
            raise RuntimeError(msg)
        self._ui(lambda: self._fatal(msg))
        return None

    def resume_workflow(self) -> None:
        if not self._parse_curl_or_warn():
            return
        left = self.auth.seconds_left()
        if not self.auth.access_token:
            messagebox.showerror(
                "Kein Token",
                "Im eingefügten cURL steckt kein Authorization-Token.",
            )
            return
        if left < 20:
            messagebox.showerror(
                "Token schon abgelaufen",
                "Der Token im eingefügten cURL ist schon abgelaufen "
                "(oder es steht noch der alte cURL im Feld).\n\n"
                "Bitte in den DevTools einen frischen Request kopieren und hier einfügen.",
            )
            return

        self.paused_for_token = False
        self.is_running = True
        self.resume_btn.config(state=tk.DISABLED)
        self.curl_text.configure(background=UI_FIELD, fg=UI_FG)
        mins = max(1, int(left // 60))
        self._set_status("Status: Fortsetzung mit neuem Token · noch ca. %s min gültig …" % mins)
        if self.phase == "listing_schemas":
            target = self._fetch_schemas_background
        elif self.phase == "listing":
            target = self._fetch_tables_background
        elif self.phase == "counting":
            target = self._count_rows_loop
        else:
            target = self._export_loop
        threading.Thread(target=target, daemon=True).start()

    def _enter_token_pause(self) -> None:
        self.paused_for_token = True
        self.resume_btn.config(state=tk.NORMAL)
        self.start_btn.config(state=tk.DISABLED)
        self.curl_text.configure(background="#fff4cc")
        self.curl_text.focus_set()
        self.status_label.configure(
            fg="#8a1c1c",
            font=ui_font(12, "bold"),
            text="Status: Token abgelaufen – neuen cURL einfügen, dann Fortfahren",
        )
        messagebox.showwarning(
            "Token abgelaufen",
            "Der Zugriffstoken ist abgelaufen.\n\n"
            "Bereits gelesene Zeilen sind zwischengespeichert.\n"
            "Bitte einen frischen cURL aus dem Browser hier einfügen und auf „Fortfahren“ klicken.",
        )

    def _finish_all(self) -> None:
        self.is_running = False
        self.phase = "idle"
        self.start_btn.config(state=tk.NORMAL)
        self.fetch_btn.config(state=tk.NORMAL)
        self.load_tables_btn.config(state=tk.NORMAL if self.schema_vars else tk.DISABLED)
        self.resume_btn.config(state=tk.DISABLED)
        self.file_menu.entryconfig("Test-Run", state=tk.NORMAL)
        self._set_status(f"Status: Fertig. Dateien in {self.output_dir}")
        title = "Test-Run" if self.test_run else "Beweissicherung"
        messagebox.showinfo(title, f"Alle Tabellen wurden gesichert nach:\n{self.output_dir}")

    def _auth_failed(self, detail: str) -> None:
        self._reset_idle()
        self._set_status("Status: Token ungültig")
        messagebox.showerror("Authentifizierungsfehler", f"Der cURL bzw. Token ist ungültig:\n{detail[:800]}")

    def _fatal(self, msg: str) -> None:
        self._reset_idle()
        self._set_status("Status: Fehler")
        messagebox.showerror("Fehler", msg)

    def _reset_idle(self) -> None:
        self.is_running = False
        self.paused_for_token = False
        self.phase = "idle"
        self.start_btn.config(state=tk.NORMAL)
        self.fetch_btn.config(state=tk.NORMAL)
        self.load_tables_btn.config(state=tk.NORMAL if self.schema_vars else tk.DISABLED)
        self.resume_btn.config(state=tk.DISABLED)
        self.file_menu.entryconfig("Test-Run", state=tk.NORMAL)

    def _refresh_row(self, table: dict) -> None:
        widget = table.get("row_widget")
        if not widget:
            return
        exported = table["exported"]
        total = table["est_rows"]
        status = table["status"]
        self._ui(lambda: widget.set_progress(exported, total, status))

    def _set_status(self, text: str) -> None:
        self.status_label.configure(fg=UI_FG, font=ui_font(12, "italic"), text=text)

    def _ui(self, fn) -> None:
        self.root.after(0, fn)

    def _on_close(self) -> None:
        self.is_running = False
        self._stop_watchdog.set()
        if getattr(self, "email_tab", None):
            self.email_tab.shutdown()
        for table in self.tables:
            writer = table.get("writer")
            if writer:
                writer.close()
        self.root.destroy()


class TokenExpired(Exception):
    pass


def write_json_file(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"t", "true", "1", "yes", "y"}


def normalize_columns(rows: list) -> list:
    columns = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        columns.append(
            {
                "name": row.get("column_name"),
                "ordinal_position": parse_int(row.get("ordinal_position")),
                "data_type": row.get("data_type"),
                "udt_name": row.get("udt_name"),
                "nullable": as_bool(row.get("is_nullable")),
                "default": row.get("column_default"),
                "identity": row.get("identity") or "",
                "generated": row.get("generated") or "",
                "comment": row.get("comment"),
            }
        )
    return columns


def normalize_constraints(rows: list) -> list:
    mapping = {"p": "primary_key", "u": "unique", "f": "foreign_key", "c": "check", "x": "exclusion"}
    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ctype = row.get("type") or ""
        items.append(
            {
                "name": row.get("name"),
                "type": mapping.get(str(ctype), str(ctype)),
                "definition": row.get("definition"),
            }
        )
    return items


def normalize_indexes(rows: list) -> list:
    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        items.append(
            {
                "name": row.get("index_name"),
                "unique": as_bool(row.get("is_unique")),
                "primary": as_bool(row.get("is_primary")),
                "definition": row.get("definition"),
            }
        )
    return items


def build_create_table_sql(schema: dict) -> str:
    table_name = schema["table"]
    lines = []
    for col in schema.get("columns") or []:
        name = col.get("name")
        data_type = col.get("data_type") or "text"
        if not name:
            continue
        piece = f"    {quote_ident(name)} {data_type}"
        generated = col.get("generated") or ""
        identity = col.get("identity") or ""
        default = col.get("default")
        if generated in {"s", "S"} and default:
            piece += f" GENERATED ALWAYS AS ({default}) STORED"
        elif identity in {"a", "A"}:
            piece += " GENERATED ALWAYS AS IDENTITY"
        elif identity in {"d", "D"}:
            piece += " GENERATED BY DEFAULT AS IDENTITY"
        elif default:
            piece += f" DEFAULT {default}"
        if not col.get("nullable"):
            piece += " NOT NULL"
        lines.append(piece)

    has_pk = False
    for constraint in schema.get("constraints") or []:
        definition = constraint.get("definition")
        if not definition:
            continue
        if constraint.get("type") == "primary_key":
            has_pk = True
            lines.append(f"    {definition}")
        elif constraint.get("type") in {"unique", "check", "foreign_key", "exclusion"}:
            lines.append(f"    {definition}")

    if not has_pk and schema.get("primary_key"):
        pk = ", ".join(quote_ident(c) for c in schema["primary_key"])
        lines.append(f"    PRIMARY KEY ({pk})")

    schema_name = schema.get("schema") or "public"
    body = ",\n".join(lines) if lines else "    -- keine Spalten gelesen"
    sql = [
        f"CREATE SCHEMA IF NOT EXISTS {quote_ident(schema_name)};\n",
        f"CREATE TABLE {quote_table(schema_name, table_name)} (\n{body}\n);\n",
    ]
    for index in schema.get("indexes") or []:
        if index.get("primary"):
            continue
        definition = (index.get("definition") or "").rstrip(";")
        if definition:
            sql.append(f"{definition};\n")
    return "".join(sql)


def parse_pk_cols(value) -> list:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def parse_int(value):
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def extract_count(rows):
    if not rows:
        return None
    row = rows[0]
    if isinstance(row, dict):
        for key in ("n", "count", "COUNT"):
            if key in row:
                return parse_int(row[key])
        if len(row) == 1:
            return parse_int(next(iter(row.values())))
        return None
    return parse_int(row)


def extract_rows(response: requests.Response) -> list:
    data = response.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("data"), list):
            return data["data"]
        if isinstance(data.get("result"), list):
            return data["result"]
    return []


def strip_ctid(record):
    if not isinstance(record, dict):
        return record
    return {k: v for k, v in record.items() if k != "ctid"}


if __name__ == "__main__":
    if "--tk" in sys.argv:
        if sys.platform == "darwin":
            os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")
        root = tk.Tk()
        root.configure(bg=UI_BG)
        KingFallApp(root)
        root.mainloop()
    else:
        import kingfall_web

        kingfall_web.serve()
