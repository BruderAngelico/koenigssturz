# -*- coding: utf-8 -*-
"""Lokale Auswertung der Beweissicherungs-Ordner über DuckDB."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Optional

PAGE_SIZE = 50
EXPORT_MAX = 100000
COMPARE_MAX = 50000
SEARCH_MIN = 2
DISTINCT_LIMIT = 60
BACKUP_PREFIX = "beweissicherung_"
DATA_PREFIX = "beweissicherung_"
SCHEMA_PREFIX = "schema_"
JSONL_SUFFIX = ".jsonl"
JSON_SUFFIX = ".json"

FILTER_OPS = (
    "eq",
    "ne",
    "contains",
    "not_contains",
    "starts",
    "empty",
    "not_empty",
    "gt",
    "lt",
)
AGG_FNS = ("count", "sum", "avg", "min", "max")
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SQL_START_RE = re.compile(r"^\s*(WITH|SELECT)\b", re.I | re.S)
SQL_BAD_RE = re.compile(
    r"(read_json|read_csv|read_parquet|read_ndjson|read_blob|read_text|"
    r"\bcopy\s|\battach\b|\bdetach\b|\binstall\s|\bload\s|\bpragma\b|"
    r"\bexport\b|\bunload\b)",
    re.I,
)

_LOCK = threading.Lock()
_CACHE: dict[tuple[str, str], dict[str, Any]] = {}
_CACHE_LIMIT = 8


class AnalyzeError(ValueError):
    pass


def quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _duckdb():
    try:
        import duckdb
    except ImportError as exc:
        raise AnalyzeError(
            "DuckDB fehlt. Einmalig: python -m pip install duckdb"
        ) from exc
    return duckdb


def backups_root(root: Optional[str] = None) -> str:
    return os.path.abspath(root or os.getcwd())


def list_backups(root: Optional[str] = None) -> list[dict[str, Any]]:
    base = backups_root(root)
    items = []
    try:
        names = os.listdir(base)
    except OSError:
        return []
    for name in names:
        if not name.startswith(BACKUP_PREFIX):
            continue
        if os.sep in name or "/" in name or "\\" in name or ".." in name:
            continue
        path = os.path.join(base, name)
        if not os.path.isdir(path):
            continue
        tables = 0
        try:
            stems = set()
            for fname in os.listdir(path):
                stem = _stem_from_filename(fname)
                if stem:
                    stems.add(stem)
            tables = len(stems)
        except OSError:
            tables = 0
        stamp = name[len(BACKUP_PREFIX) :]
        items.append(
            {
                "name": name,
                "label": stamp,
                "mtime": os.path.getmtime(path),
                "tables": tables,
            }
        )
    items.sort(key=lambda x: (x["mtime"], x["name"]), reverse=True)
    for item in items:
        item.pop("mtime", None)
    return items


def resolve_backup(folder: str, root: Optional[str] = None) -> str:
    base = backups_root(root)
    name = os.path.basename((folder or "").strip())
    if not name.startswith(BACKUP_PREFIX) or name != (folder or "").strip():
        raise AnalyzeError("Ungültiger Backup-Ordner.")
    path = os.path.abspath(os.path.join(base, name))
    if os.path.commonpath([base, path]) != base or not os.path.isdir(path):
        raise AnalyzeError("Backup-Ordner nicht gefunden: %s" % name)
    return path


def list_tables(folder: str, root: Optional[str] = None) -> list[dict[str, Any]]:
    path = resolve_backup(folder, root)
    by_stem: dict[str, dict[str, Any]] = {}
    try:
        files = os.listdir(path)
    except OSError as exc:
        raise AnalyzeError("Ordner nicht lesbar: %s" % exc) from exc
    for fname in files:
        stem = _stem_from_filename(fname)
        if not stem:
            continue
        info = by_stem.setdefault(
            stem,
            {
                "stem": stem,
                "schema": "",
                "name": stem,
                "display": stem,
                "columns": [],
                "row_count": None,
                "primary_key": [],
            },
        )
        full = os.path.join(path, fname)
        if fname.startswith(SCHEMA_PREFIX) and fname.endswith(JSON_SUFFIX) and not fname.endswith(".sql"):
            schema = _read_schema(full)
            if schema:
                info["schema"] = schema.get("schema") or info["schema"]
                info["name"] = schema.get("table") or info["name"]
                info["columns"] = _schema_columns(schema)
                counted = schema.get("row_count")
                if counted is not None:
                    info["row_count"] = counted
                pk = schema.get("primary_key") or []
                if isinstance(pk, list):
                    info["primary_key"] = [str(x).strip() for x in pk if str(x).strip()]
                info["display"] = (
                    "%s.%s" % (info["schema"], info["name"]) if info["schema"] else info["name"]
                )
        elif fname.endswith(JSONL_SUFFIX) or _is_data_json(fname):
            if info["row_count"] is None:
                info["row_count"] = _count_file_rows(full, fname.endswith(JSONL_SUFFIX))
    tables = []
    for stem, info in by_stem.items():
        if not info["schema"] or info["name"] == stem:
            schema_name, table_name = _split_stem(stem)
            if not info["schema"]:
                info["schema"] = schema_name
            if info["name"] == stem:
                info["name"] = table_name
            info["display"] = (
                "%s.%s" % (info["schema"], info["name"]) if info["schema"] else info["name"]
            )
        if not info["columns"]:
            info["columns"] = [{"name": c, "data_type": ""} for c in _peek_columns(path, stem)]
        tables.append(info)
    tables.sort(key=lambda t: t["display"].lower())
    return tables


def table_meta(folder: str, stem: str, root: Optional[str] = None) -> dict[str, Any]:
    for item in list_tables(folder, root):
        if item["stem"] == stem or item["display"] == stem or item["name"] == stem:
            return item
    raise AnalyzeError("Tabelle nicht gefunden: %s" % stem)


def build_sql(
    relation: str,
    columns: list[str],
    search: str = "",
    filters: Optional[list[dict[str, Any]]] = None,
    group_by: Optional[list[str]] = None,
    aggregations: Optional[list[dict[str, Any]]] = None,
    order: Optional[dict[str, str]] = None,
) -> str:
    relation_sql = quote_ident(relation)
    col_set = set(columns)
    where_sql = _where_sql(columns, search, filters or [])
    groups = [_require_column(c, col_set) for c in (group_by or []) if str(c).strip()]
    aggs = [a for a in (aggregations or []) if a]
    select_sql, grouped = _select_sql(columns, groups, aggs)
    sql = "SELECT %s\nFROM %s" % (select_sql, relation_sql)
    if where_sql:
        sql += "\nWHERE %s" % where_sql
    if grouped and groups:
        sql += "\nGROUP BY %s" % ", ".join(str(i) for i in range(1, len(groups) + 1))
    order_sql = _order_sql(order, col_set, groups, aggs, grouped)
    if order_sql:
        sql += "\nORDER BY %s" % order_sql
    return sql


def run_query(
    folder: str,
    table: str,
    search: str = "",
    filters: Optional[list[dict[str, Any]]] = None,
    group_by: Optional[list[str]] = None,
    aggregations: Optional[list[dict[str, Any]]] = None,
    order: Optional[dict[str, str]] = None,
    sql: Optional[str] = None,
    page: int = 0,
    page_size: int = PAGE_SIZE,
    all_rows: bool = False,
    root: Optional[str] = None,
) -> dict[str, Any]:
    meta = table_meta(folder, table, root)
    folder_path = resolve_backup(folder, root)
    source = _source_file(folder_path, meta["stem"])
    relation = _relation_name(meta)
    columns = [c["name"] for c in meta["columns"] if IDENT_RE.match(c["name"])]
    page = max(0, int(page or 0))
    if all_rows:
        page = 0
        page_size = max(1, min(EXPORT_MAX, int(page_size or EXPORT_MAX)))
    else:
        page_size = max(1, min(500, int(page_size or PAGE_SIZE)))
    user_sql = (sql or "").strip()
    if user_sql:
        inner = _assert_safe_select(user_sql)
        generated = inner
    else:
        if not columns and source:
            columns = _peek_columns(folder_path, meta["stem"])
        generated = build_sql(
            relation, columns, search, filters, group_by, aggregations, order
        )
        inner = generated
    offset = page * page_size
    count_sql = "SELECT COUNT(*) FROM (%s) AS _q" % inner
    page_sql = "SELECT * FROM (%s) AS _q LIMIT %s OFFSET %s" % (
        inner,
        page_size,
        offset,
    )
    with _LOCK:
        con = _cached_connection(folder_path, meta["stem"], relation, source, columns)
        try:
            total = int(con.execute(count_sql).fetchone()[0] or 0)
            result = con.execute(page_sql)
            out_cols = [d[0] for d in result.description]
            rows = [_serialize_row(out_cols, row) for row in result.fetchall()]
            totals = None
            if group_by or aggregations:
                totals = _totals_row(con, inner, out_cols)
        except AnalyzeError:
            raise
        except Exception as exc:
            raise AnalyzeError("Abfrage fehlgeschlagen: %s" % exc) from exc
    return {
        "folder": os.path.basename(folder_path),
        "table": meta["display"],
        "stem": meta["stem"],
        "relation": relation,
        "columns": out_cols,
        "column_meta": meta["columns"],
        "rows": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "sql": generated,
        "totals": totals,
        "grouped": bool(group_by or aggregations),
    }


def preview(folder: str, table: str, page: int = 0, root: Optional[str] = None) -> dict[str, Any]:
    return run_query(folder, table, page=page, root=root)


def export_bytes(
    folder: str,
    table: str,
    fmt: str = "json",
    search: str = "",
    filters: Optional[list[dict[str, Any]]] = None,
    group_by: Optional[list[str]] = None,
    aggregations: Optional[list[dict[str, Any]]] = None,
    order: Optional[dict[str, str]] = None,
    sql: Optional[str] = None,
    root: Optional[str] = None,
) -> tuple[bytes, str, str]:
    kind = (fmt or "json").strip().lower()
    if kind not in ("json", "csv"):
        raise AnalyzeError("Format muss json oder csv sein.")
    data = run_query(
        folder,
        table,
        search=search,
        filters=filters,
        group_by=group_by,
        aggregations=aggregations,
        order=order,
        sql=sql,
        all_rows=True,
        page_size=EXPORT_MAX,
        root=root,
    )
    stem = re.sub(r"[^\w.\-]+", "_", data.get("table") or data.get("stem") or "export")
    if data["total"] > len(data["rows"]):
        stem += "_teil"
    columns = data["columns"]
    rows = data["rows"]
    if kind == "csv":
        return _to_csv(columns, rows), "%s.csv" % stem, "text/csv; charset=utf-8"
    payload = json.dumps(rows, ensure_ascii=False, indent=2, default=str)
    return payload.encode("utf-8"), "%s.json" % stem, "application/json; charset=utf-8"


def distinct_values(
    folder: str,
    table: str,
    column: str,
    limit: int = DISTINCT_LIMIT,
    root: Optional[str] = None,
) -> dict[str, Any]:
    meta = table_meta(folder, table, root)
    folder_path = resolve_backup(folder, root)
    source = _source_file(folder_path, meta["stem"])
    relation = _relation_name(meta)
    columns = [c["name"] for c in meta["columns"] if IDENT_RE.match(c["name"])]
    if not columns and source:
        columns = _peek_columns(folder_path, meta["stem"])
    col = _require_column(column, set(columns))
    limit = max(1, min(200, int(limit or DISTINCT_LIMIT)))
    qcol = quote_ident(col)
    rel = quote_ident(relation)
    with _LOCK:
        con = _cached_connection(folder_path, meta["stem"], relation, source, columns)
        try:
            stats = con.execute(
                "SELECT COUNT(*) AS n, COUNT(DISTINCT CAST(%s AS VARCHAR)) AS u FROM %s"
                % (qcol, rel)
            ).fetchone()
            ntotal = int(stats[0] or 0)
            unique = int(stats[1] or 0)
            result = con.execute(
                "SELECT CAST(%s AS VARCHAR) AS v, COUNT(*) AS n FROM %s "
                "GROUP BY 1 ORDER BY n DESC, v LIMIT %s"
                % (qcol, rel, limit)
            )
            raw = result.fetchall()
        except Exception as exc:
            raise AnalyzeError("Werte konnten nicht gelesen werden: %s" % exc) from exc
    values = []
    for value, count in raw:
        text = "" if value is None else str(value)
        if len(text) > 180:
            continue
        values.append({"value": text, "n": int(count or 0), "empty": value is None or text == ""})
    useful = bool(values) and (values[0]["n"] >= 2 or unique <= limit)
    return {
        "folder": os.path.basename(folder_path),
        "table": meta["display"],
        "column": col,
        "unique": unique,
        "rows": ntotal,
        "useful": useful,
        "values": values if useful else [],
    }


def search_all(folder: str, term: str, root: Optional[str] = None) -> dict[str, Any]:
    needle = (term or "").strip()
    if len(needle) < SEARCH_MIN:
        raise AnalyzeError("Mindestens %s Zeichen suchen." % SEARCH_MIN)
    folder_path = resolve_backup(folder, root)
    low = needle.lower()
    hits = []
    for meta in list_tables(folder, root):
        source = _source_file(folder_path, meta["stem"])
        count, samples = _scan_source(source, low)
        if count:
            hits.append(
                {
                    "stem": meta["stem"],
                    "display": meta["display"],
                    "hits": count,
                    "samples": samples,
                }
            )
    hits.sort(key=lambda x: (-x["hits"], x["display"].lower()))
    return {
        "folder": os.path.basename(folder_path),
        "term": needle,
        "tables": hits,
        "table_count": len(hits),
        "hit_count": sum(h["hits"] for h in hits),
    }


def compare_tables(
    folder: str,
    other: str,
    table: str,
    page: int = 0,
    page_size: int = PAGE_SIZE,
    kinds: Optional[list[str]] = None,
    root: Optional[str] = None,
) -> dict[str, Any]:
    if (folder or "").strip() == (other or "").strip():
        raise AnalyzeError("Zwei verschiedene Ordner wählen.")
    left = _all_compare_rows(folder, table, root)
    right = _all_compare_rows(other, table, root)
    pk = _compare_pk(left["meta"], left["columns"], right["columns"])
    wanted = _compare_kinds(kinds)
    diffs = _diff_rows(left["rows"], right["rows"], pk, left["columns"], right["columns"])
    summary = {
        "neu": 0,
        "gelöscht": 0,
        "geändert": 0,
        "gleich": 0,
    }
    for row in diffs:
        summary[row["Änderung"]] = summary.get(row["Änderung"], 0) + 1
    filtered = [row for row in diffs if row["Änderung"] in wanted]
    page = max(0, int(page or 0))
    page_size = max(1, min(500, int(page_size or PAGE_SIZE)))
    total = len(filtered)
    start = page * page_size
    slice_rows = filtered[start : start + page_size]
    columns = ["Änderung", "Schlüssel", "Diff"] + _union_columns(left["columns"], right["columns"])
    return {
        "folder": left["folder"],
        "other": right["folder"],
        "table": left["meta"]["display"],
        "stem": left["meta"]["stem"],
        "columns": columns,
        "column_meta": left["meta"]["columns"],
        "rows": slice_rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "sql": "",
        "totals": None,
        "grouped": False,
        "compare": {
            "folder": left["folder"],
            "other": right["folder"],
            "pk": pk,
            "kinds": sorted(wanted),
            "neu": summary["neu"],
            "gelöscht": summary["gelöscht"],
            "geändert": summary["geändert"],
            "gleich": summary["gleich"],
        },
    }


def export_compare_bytes(
    folder: str,
    other: str,
    table: str,
    fmt: str = "json",
    kinds: Optional[list[str]] = None,
    root: Optional[str] = None,
) -> tuple[bytes, str, str]:
    kind = (fmt or "json").strip().lower()
    if kind not in ("json", "csv"):
        raise AnalyzeError("Format muss json oder csv sein.")
    data = compare_tables(
        folder, other, table, page=0, page_size=EXPORT_MAX, kinds=kinds, root=root
    )
    stem = re.sub(r"[^\w.\-]+", "_", data.get("table") or "vergleich")
    if data["total"] > len(data["rows"]):
        stem += "_teil"
    columns = data["columns"]
    rows = data["rows"]
    if kind == "csv":
        return _to_csv(columns, rows), "%s_vergleich.csv" % stem, "text/csv; charset=utf-8"
    payload = json.dumps(
        {"compare": data.get("compare"), "rows": rows},
        ensure_ascii=False,
        indent=2,
        default=str,
    )
    return payload.encode("utf-8"), "%s_vergleich.json" % stem, "application/json; charset=utf-8"


def _to_csv(columns: list[str], rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_csv_cell(row.get(col)) for col in columns])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _is_data_json(fname: str) -> bool:
    return (
        fname.startswith(DATA_PREFIX)
        and fname.endswith(JSON_SUFFIX)
        and not fname.startswith(SCHEMA_PREFIX)
    )


def _stem_from_filename(fname: str) -> str:
    if fname.endswith(".sql"):
        return ""
    if fname.startswith(SCHEMA_PREFIX) and fname.endswith(JSON_SUFFIX):
        return fname[len(SCHEMA_PREFIX) : -len(JSON_SUFFIX)]
    if fname.endswith(JSONL_SUFFIX):
        return fname[: -len(JSONL_SUFFIX)]
    if _is_data_json(fname):
        return fname[len(DATA_PREFIX) : -len(JSON_SUFFIX)]
    return ""


def _split_stem(stem: str) -> tuple[str, str]:
    if "_" not in stem:
        return "", stem
    schema, name = stem.split("_", 1)
    return schema, name


def _read_schema(path: str) -> Optional[dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _schema_columns(schema: dict[str, Any]) -> list[dict[str, Any]]:
    cols = []
    for col in schema.get("columns") or []:
        name = str(col.get("name") or "").strip()
        if not name:
            continue
        cols.append(
            {
                "name": name,
                "data_type": col.get("data_type") or col.get("udt_name") or "",
            }
        )
    return cols


def _count_file_rows(path: str, jsonl: bool) -> Optional[int]:
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size <= 2:
        return 0
    if not jsonl:
        return None
    n = 0
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    n += 1
    except OSError:
        return None
    return n


def _peek_columns(folder_path: str, stem: str) -> list[str]:
    jsonl = os.path.join(folder_path, stem + JSONL_SUFFIX)
    data_json = os.path.join(folder_path, DATA_PREFIX + stem + JSON_SUFFIX)
    path = jsonl if os.path.isfile(jsonl) and os.path.getsize(jsonl) > 0 else data_json
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            if path.endswith(JSONL_SUFFIX):
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    if isinstance(row, dict):
                        return [str(k) for k in row.keys()]
                    break
            else:
                data = json.load(handle)
                if isinstance(data, list) and data and isinstance(data[0], dict):
                    return [str(k) for k in data[0].keys()]
    except (OSError, ValueError):
        return []
    return []


def _source_file(folder_path: str, stem: str) -> Optional[str]:
    jsonl = os.path.join(folder_path, stem + JSONL_SUFFIX)
    data_json = os.path.join(folder_path, DATA_PREFIX + stem + JSON_SUFFIX)
    if os.path.isfile(jsonl) and os.path.getsize(jsonl) > 0:
        return jsonl
    if os.path.isfile(data_json) and os.path.getsize(data_json) > 0:
        return data_json
    if os.path.isfile(jsonl):
        return jsonl
    if os.path.isfile(data_json):
        return data_json
    return None


def _relation_name(meta: dict[str, Any]) -> str:
    schema = meta.get("schema") or ""
    name = meta.get("name") or meta.get("stem") or "tabelle"
    if schema:
        return "%s.%s" % (schema, name)
    return name


def _require_column(name: Any, col_set: set[str]) -> str:
    col = str(name or "").strip()
    if col not in col_set or not IDENT_RE.match(col):
        raise AnalyzeError("Unbekannte Spalte: %s" % name)
    return col


def _where_sql(columns: list[str], search: str, filters: list[dict[str, Any]]) -> str:
    parts = []
    term = (search or "").strip()
    if term:
        like = sql_literal("%" + _escape_like(term) + "%")
        ors = []
        for col in columns:
            if not IDENT_RE.match(col):
                continue
            ors.append("CAST(%s AS VARCHAR) ILIKE %s ESCAPE '\\'" % (quote_ident(col), like))
        if ors:
            parts.append("(%s)" % " OR ".join(ors))
    col_set = set(columns)
    for item in filters:
        op = str(item.get("op") or "").strip()
        if op not in FILTER_OPS:
            raise AnalyzeError("Unbekannter Vergleich: %s" % op)
        col = _require_column(item.get("column"), col_set)
        qcol = quote_ident(col)
        value = item.get("value")
        if op == "empty":
            parts.append("(%s IS NULL OR CAST(%s AS VARCHAR) = '')" % (qcol, qcol))
            continue
        if op == "not_empty":
            parts.append("(%s IS NOT NULL AND CAST(%s AS VARCHAR) <> '')" % (qcol, qcol))
            continue
        text = "" if value is None else str(value)
        if op == "eq":
            parts.append("CAST(%s AS VARCHAR) = %s" % (qcol, sql_literal(text)))
        elif op == "ne":
            parts.append("CAST(%s AS VARCHAR) <> %s" % (qcol, sql_literal(text)))
        elif op == "contains":
            parts.append(
                "CAST(%s AS VARCHAR) ILIKE %s ESCAPE '\\'"
                % (qcol, sql_literal("%" + _escape_like(text) + "%"))
            )
        elif op == "not_contains":
            parts.append(
                "CAST(%s AS VARCHAR) NOT ILIKE %s ESCAPE '\\'"
                % (qcol, sql_literal("%" + _escape_like(text) + "%"))
            )
        elif op == "starts":
            parts.append(
                "CAST(%s AS VARCHAR) ILIKE %s ESCAPE '\\'"
                % (qcol, sql_literal(_escape_like(text) + "%"))
            )
        elif op in ("gt", "lt"):
            cmp_op = ">" if op == "gt" else "<"
            lit = sql_literal(text)
            num = "TRY_CAST(%s AS DOUBLE)" % qcol
            nval = "TRY_CAST(%s AS DOUBLE)" % lit
            parts.append(
                "(CASE WHEN %s IS NOT NULL AND %s IS NOT NULL THEN %s %s %s "
                "ELSE CAST(%s AS VARCHAR) %s %s END)"
                % (num, nval, num, cmp_op, nval, qcol, cmp_op, lit)
            )
    return " AND ".join(parts)


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _select_sql(
    columns: list[str], groups: list[str], aggs: list[dict[str, Any]]
) -> tuple[str, bool]:
    if not groups and not aggs:
        if columns:
            return ", ".join(quote_ident(c) for c in columns), False
        return "*", False
    pieces = [quote_ident(c) for c in groups]
    if not aggs:
        pieces.append("COUNT(*) AS %s" % quote_ident("anzahl"))
        return ", ".join(pieces), True
    col_set = set(columns)
    used = set(groups)
    for agg in aggs:
        fn = str(agg.get("fn") or "").strip().lower()
        if fn not in AGG_FNS:
            raise AnalyzeError("Unbekannte Berechnung: %s" % fn)
        col = str(agg.get("column") or "").strip()
        if fn == "count" and (not col or col == "*"):
            alias = "anzahl"
            expr = "COUNT(*)"
        else:
            col = _require_column(col, col_set)
            alias = _agg_alias(fn, col)
            qcol = quote_ident(col)
            if fn == "count":
                expr = "COUNT(%s)" % qcol
            elif fn == "sum":
                expr = "SUM(TRY_CAST(%s AS DOUBLE))" % qcol
            elif fn == "avg":
                expr = "AVG(TRY_CAST(%s AS DOUBLE))" % qcol
            elif fn == "min":
                expr = "MIN(%s)" % qcol
            else:
                expr = "MAX(%s)" % qcol
        n = 2
        base = alias
        while alias in used:
            alias = "%s_%s" % (base, n)
            n += 1
        used.add(alias)
        pieces.append("%s AS %s" % (expr, quote_ident(alias)))
    return ", ".join(pieces), True


def _agg_alias(fn: str, col: str) -> str:
    if fn == "count":
        return "anzahl_%s" % col
    if fn == "sum":
        return "summe_%s" % col
    if fn == "avg":
        return "durchschnitt_%s" % col
    if fn == "min":
        return "min_%s" % col
    return "max_%s" % col


def _order_sql(
    order: Optional[dict[str, str]],
    col_set: set[str],
    groups: list[str],
    aggs: list[dict[str, Any]],
    grouped: bool,
) -> str:
    if not order or not order.get("column"):
        return ""
    col = str(order.get("column") or "").strip()
    direction = "DESC" if str(order.get("dir") or "").lower() == "desc" else "ASC"
    allowed = set(col_set)
    if grouped:
        allowed = set(groups)
        if not aggs:
            allowed.add("anzahl")
        for agg in aggs:
            fn = str(agg.get("fn") or "").strip().lower()
            ac = str(agg.get("column") or "").strip()
            if fn == "count" and (not ac or ac == "*"):
                allowed.add("anzahl")
            elif fn in AGG_FNS and ac:
                allowed.add(_agg_alias(fn, ac))
    if col not in allowed or not IDENT_RE.match(col):
        raise AnalyzeError("Unbekannte Sortierspalte: %s" % col)
    return "%s %s" % (quote_ident(col), direction)


def _assert_safe_select(sql: str) -> str:
    text = (sql or "").strip()
    if text.endswith(";"):
        text = text[:-1].strip()
    if not text:
        raise AnalyzeError("SQL ist leer.")
    if ";" in text:
        raise AnalyzeError("Nur eine SQL-Anweisung erlaubt.")
    if "--" in text or "/*" in text:
        raise AnalyzeError("SQL-Kommentare sind nicht erlaubt.")
    if not SQL_START_RE.match(text):
        raise AnalyzeError("Nur SELECT-Abfragen sind erlaubt.")
    if SQL_BAD_RE.search(text):
        raise AnalyzeError("Diese SQL-Funktion ist nicht erlaubt.")
    return text


def _cached_connection(
    folder_path: str,
    stem: str,
    relation: str,
    source: Optional[str],
    columns: list[str],
):
    duckdb = _duckdb()
    mtime = 0.0
    if source and os.path.isfile(source):
        mtime = os.path.getmtime(source)
    key = (folder_path, stem)
    hit = _CACHE.get(key)
    if hit and hit["mtime"] == mtime and hit["relation"] == relation:
        return hit["con"]
    if hit:
        try:
            hit["con"].close()
        except Exception:
            pass
        _CACHE.pop(key, None)
    con = duckdb.connect(config={"enable_external_access": True})
    _load_table(con, relation, source, columns)
    try:
        con.execute("SET enable_external_access=false")
    except Exception:
        pass
    _CACHE[key] = {"con": con, "mtime": mtime, "relation": relation}
    while len(_CACHE) > _CACHE_LIMIT:
        old_key = next(iter(_CACHE))
        if old_key == key:
            break
        old = _CACHE.pop(old_key)
        try:
            old["con"].close()
        except Exception:
            pass
    return con


def _load_table(con, relation: str, source: Optional[str], columns: list[str]) -> None:
    rel = quote_ident(relation)
    con.execute("DROP TABLE IF EXISTS %s" % rel)
    empty_sql = _empty_table_sql(relation, columns)
    if not source or not os.path.isfile(source) or os.path.getsize(source) <= 0:
        con.execute(empty_sql)
        return
    fmt = "newline_delimited" if source.endswith(JSONL_SUFFIX) else "array"
    path_sql = sql_literal(source.replace("\\", "/"))
    load_sql = (
        "CREATE TABLE %s AS SELECT * FROM read_json_auto(%s, format=%s, ignore_errors=true)"
        % (rel, path_sql, sql_literal(fmt))
    )
    try:
        con.execute(load_sql)
    except Exception:
        con.execute(empty_sql)
        return
    n = con.execute("SELECT COUNT(*) FROM %s" % rel).fetchone()[0]
    if n == 0 and columns:
        con.execute("DROP TABLE IF EXISTS %s" % rel)
        con.execute(empty_sql)


def _empty_table_sql(relation: str, columns: list[str]) -> str:
    rel = quote_ident(relation)
    if not columns:
        return "CREATE TABLE %s (_leer VARCHAR)" % rel
    defs = ", ".join("%s VARCHAR" % quote_ident(c) for c in columns if IDENT_RE.match(c))
    if not defs:
        return "CREATE TABLE %s (_leer VARCHAR)" % rel
    return "CREATE TABLE %s (%s)" % (rel, defs)


def _totals_row(con, inner_sql: str, columns: list[str]) -> Optional[dict[str, Any]]:
    numeric = []
    for col in columns:
        if not IDENT_RE.match(col):
            continue
        low = col.lower()
        if low == "anzahl" or low.startswith("anzahl_") or low.startswith("summe_") or low.startswith(
            "durchschnitt_"
        ):
            numeric.append(col)
    if not numeric:
        return None
    parts = ["SUM(TRY_CAST(%s AS DOUBLE)) AS %s" % (quote_ident(c), quote_ident(c)) for c in numeric]
    try:
        result = con.execute("SELECT %s FROM (%s) AS _t" % (", ".join(parts), inner_sql))
        row = result.fetchone()
        names = [d[0] for d in result.description]
        data = _serialize_row(names, row)
        data["_label"] = "Summe"
        return data
    except Exception:
        return None


def _serialize_row(columns: list[str], row: Optional[tuple]) -> dict[str, Any]:
    out = {}
    if row is None:
        return out
    for name, value in zip(columns, row):
        out[name] = _cell(value)
    return out


def _cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, (list, dict, tuple)):
        try:
            return json.loads(json.dumps(value, default=str))
        except TypeError:
            return str(value)
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return value


def _scan_source(source: Optional[str], needle: str) -> tuple[int, list[str]]:
    if not source or not os.path.isfile(source):
        return 0, []
    try:
        size = os.path.getsize(source)
    except OSError:
        return 0, []
    if size <= 0:
        return 0, []
    samples: list[str] = []
    count = 0
    try:
        if source.endswith(JSONL_SUFFIX):
            with open(source, encoding="utf-8") as handle:
                for line in handle:
                    if needle not in line.lower():
                        continue
                    count += 1
                    if len(samples) < 3:
                        samples.append(_sample_from_line(line, needle))
        else:
            if size > 40 * 1024 * 1024:
                return 0, []
            with open(source, encoding="utf-8") as handle:
                data = json.load(handle)
            rows = data if isinstance(data, list) else [data]
            for row in rows:
                blob = json.dumps(row, ensure_ascii=False, default=str)
                if needle not in blob.lower():
                    continue
                count += 1
                if len(samples) < 3:
                    samples.append(_matching_preview(row, needle))
    except (OSError, ValueError):
        return count, samples
    return count, samples


def _sample_from_line(line: str, needle: str) -> str:
    try:
        row = json.loads(line)
    except ValueError:
        text = line.strip()
        return text[:180]
    return _matching_preview(row, needle)


def _matching_preview(row: Any, needle: str, limit: int = 180) -> str:
    if not isinstance(row, dict):
        return str(row)[:limit]
    parts = []
    for key, value in row.items():
        if isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False, default=str)
        elif value is None:
            text = ""
        else:
            text = str(value)
        if needle in text.lower() or needle in str(key).lower():
            parts.append("%s=%s" % (key, text[:80]))
        if len(parts) >= 4:
            break
    return "; ".join(parts)[:limit] or str(row)[:limit]


def _all_compare_rows(folder: str, table: str, root: Optional[str]) -> dict[str, Any]:
    data = run_query(
        folder,
        table,
        all_rows=True,
        page_size=COMPARE_MAX,
        root=root,
    )
    if data["total"] > len(data["rows"]):
        raise AnalyzeError(
            "Tabelle %s hat mehr als %s Zeilen – Vergleich nicht möglich."
            % (data.get("table") or table, COMPARE_MAX)
        )
    return {
        "folder": data["folder"],
        "meta": table_meta(folder, table, root),
        "columns": list(data["columns"] or []),
        "rows": list(data["rows"] or []),
    }


def _compare_pk(meta: dict[str, Any], left_cols: list[str], right_cols: list[str]) -> list[str]:
    both = set(left_cols) & set(right_cols)
    pk = [c for c in (meta.get("primary_key") or []) if c in both]
    if pk:
        return pk
    for name in ("id", "uuid", "pk"):
        if name in both:
            return [name]
    return []


def _compare_kinds(kinds: Optional[list[str]]) -> set[str]:
    allowed = {"neu", "gelöscht", "geändert"}
    picked = {str(k).strip() for k in (kinds or []) if str(k).strip()}
    picked &= allowed
    return picked or set(allowed)


def _union_columns(left: list[str], right: list[str]) -> list[str]:
    out = []
    seen = set()
    for col in list(left) + list(right):
        if col in seen or col in ("Änderung", "Schlüssel", "Diff"):
            continue
        seen.add(col)
        out.append(col)
    return out


def _key_of(row: dict[str, Any], pk: list[str]) -> tuple:
    if pk:
        return tuple(_norm_key(row.get(c)) for c in pk)
    return (_norm_key(row),)


def _norm_key(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, tuple):
        return json.dumps([_norm_key(x) for x in value], ensure_ascii=False)
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return str(value)


def _norm_val(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return str(value)


def _fmt_key(key: tuple, pk: list[str]) -> str:
    if not pk:
        return "(ganze Zeile)"
    if len(pk) == 1:
        return "%s=%s" % (pk[0], key[0] if key else "")
    return ", ".join("%s=%s" % (name, val) for name, val in zip(pk, key))


def _diff_rows(
    left_rows: list[dict[str, Any]],
    right_rows: list[dict[str, Any]],
    pk: list[str],
    left_cols: list[str],
    right_cols: list[str],
) -> list[dict[str, Any]]:
    cols = _union_columns(left_cols, right_cols)
    left_map = {}
    right_map = {}
    for row in left_rows:
        left_map[_key_of(row, pk)] = row
    for row in right_rows:
        right_map[_key_of(row, pk)] = row
    out = []
    for key in sorted(set(left_map) | set(right_map), key=lambda k: _norm_key(k)):
        old = left_map.get(key)
        new = right_map.get(key)
        if old is None:
            row = _compare_view("neu", key, pk, None, new, cols)
        elif new is None:
            row = _compare_view("gelöscht", key, pk, old, None, cols)
        elif _rows_equal(old, new, cols):
            row = _compare_view("gleich", key, pk, old, new, cols)
        else:
            row = _compare_view("geändert", key, pk, old, new, cols)
        out.append(row)
    return out


def _rows_equal(left: dict[str, Any], right: dict[str, Any], cols: list[str]) -> bool:
    for col in cols:
        if _norm_val(left.get(col)) != _norm_val(right.get(col)):
            return False
    return True


def _compare_view(
    kind: str,
    key: tuple,
    pk: list[str],
    old: Optional[dict[str, Any]],
    new: Optional[dict[str, Any]],
    cols: list[str],
) -> dict[str, Any]:
    src = new if new is not None else (old or {})
    row = dict(src)
    row["Änderung"] = kind
    row["Schlüssel"] = _fmt_key(key, pk)
    if kind == "geändert":
        parts = []
        for col in cols:
            a = (old or {}).get(col)
            b = (new or {}).get(col)
            if _norm_val(a) != _norm_val(b):
                parts.append("%s: %s → %s" % (col, fmt_short(a), fmt_short(b)))
        row["Diff"] = "; ".join(parts)
    elif kind == "neu":
        row["Diff"] = "nur in neuerer Sicherung"
    elif kind == "gelöscht":
        row["Diff"] = "nur in älterer Sicherung"
    else:
        row["Diff"] = ""
    return row


def fmt_short(value: Any, limit: int = 80) -> str:
    text = "–" if value is None or value == "" else _norm_val(value)
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text
