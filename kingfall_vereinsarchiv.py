# -*- coding: utf-8 -*-
"""Vereinsarchiv: Stammtische (öffentlich + intern) inkl. Audio lokal sichern."""
from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Optional
from urllib.parse import quote, urlparse

import requests

import kingfall as kf

VA_ORIGIN = "https://vereinsarchiv-vorstand.pages.dev"
VA_DIRNAME = "vereinsarchiv"
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
AUDIO_EXT = (".aac", ".m4a", ".mp3", ".ogg", ".wav", ".webm")
HUGE_FIELDS = ("embedding", "fts", "suche", "wellenform")
UI_DROP = HUGE_FIELDS + ("volltext",)

LIST_SELECT = (
    "id,datum,titel,ort,dauer_sek,sprecher,themen,status,"
    "audio_pfad,freigabe_status,folien"
)
# wellenform/embedding nicht ziehen – das sprengt intern oft den Request.
DETAIL_SELECT = (
    "id,datum,titel,ort,dauer_sek,sprecher,themen,status,zusammenfassung,"
    "transkript,folien,audio_pfad,erstellt_am,kapitel,verarbeitung,folientext,"
    "nachverfolgung,freigabe_status,freigegeben_von,freigegeben_am,"
    "aufbewahrung_markiert,markiert_am,markiert_grund"
)
SLIM_DETAIL_SELECT = (
    "id,datum,titel,ort,dauer_sek,sprecher,themen,zusammenfassung,"
    "transkript,audio_pfad,folien,nachverfolgung"
)

KINDS = {
    "public": {
        "table": "stammtische",
        "tables": ("stammtische",),
        "folder": "stammtische",
        "label": "Öffentlich",
        "list_select": LIST_SELECT,
        "full_select": DETAIL_SELECT,
        "page": 200,
        "audio_bucket": "audio",
        "folien_bucket": "folien",
    },
    "intern": {
        "table": "stammtische_intern",
        "tables": ("stammtische_intern", "stammtisch_intern"),
        "folder": "stammtische_intern",
        "label": "Intern",
        "list_select": LIST_SELECT,
        "full_select": DETAIL_SELECT,
        "page": 80,
        "audio_bucket": "audio-intern",
        "folien_bucket": "folien-intern",
    },
}

CANDIDATE_BUCKETS = (
    "audio",
    "audio-intern",
    "audio-mitglieder",
    "folien",
    "folien-intern",
    "stammtische",
    "stammtische-intern",
    "stammtische_intern",
    "stammtisch-audio",
    "recordings",
    "archiv",
    "vereinsarchiv",
    "media",
    "public",
    "intern",
)

ProgressCb = Optional[Callable[[dict], None]]
StopFn = Optional[Callable[[], bool]]


def va_root(cwd: Optional[str] = None) -> str:
    return os.path.join(cwd or os.getcwd(), VA_DIRNAME)


def kind_dir(kind: str, root: Optional[str] = None) -> str:
    spec = KINDS[kind]
    return os.path.join(root or va_root(), spec["folder"])


def sanitize_id(item_id: str) -> str:
    raw = (item_id or "").strip()
    if not SAFE_ID.match(raw):
        raise ValueError("Ungültige Stammtisch-ID.")
    return raw


def item_json_path(kind: str, item_id: str, root: Optional[str] = None) -> str:
    return os.path.join(kind_dir(kind, root), sanitize_id(item_id) + ".json")


def item_meta_path(kind: str, item_id: str, root: Optional[str] = None) -> str:
    return os.path.join(kind_dir(kind, root), sanitize_id(item_id) + ".meta.json")


def item_folder(kind: str, item_id: str, root: Optional[str] = None) -> str:
    return os.path.join(kind_dir(kind, root), sanitize_id(item_id))


def base_from_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Keine Supabase-URL im cURL.")
    return "%s://%s" % (parsed.scheme, parsed.netloc)


def _is_supabase_host(host: str) -> bool:
    h = (host or "").lower()
    return "supabase.co" in h or "supabase.in" in h or h.endswith(".supabase.com")


def resolve_base(url: str, headers: Optional[dict] = None) -> str:
    """API-Host: Supabase aus der Request-URL oder, bei pages.dev-cURL, aus dem JWT."""
    parsed = urlparse(url or "")
    if parsed.scheme and parsed.netloc and _is_supabase_host(parsed.netloc):
        return "%s://%s" % (parsed.scheme, parsed.netloc)
    auth = ""
    if headers:
        auth = kf.header_value(headers, "Authorization") or ""
    payload = {}
    if auth:
        try:
            payload = kf.decode_jwt_payload(auth)
        except Exception:
            payload = {}
    iss = payload.get("iss") if isinstance(payload, dict) else None
    if isinstance(iss, str) and iss.startswith("http"):
        iss_parsed = urlparse(iss)
        if iss_parsed.scheme and iss_parsed.netloc:
            return "%s://%s" % (iss_parsed.scheme, iss_parsed.netloc)
    ref = payload.get("ref") if isinstance(payload, dict) else None
    if isinstance(ref, str) and re.match(r"^[a-z0-9]+$", ref, re.I):
        return "https://%s.supabase.co" % ref
    if parsed.scheme and parsed.netloc and _is_supabase_host(parsed.netloc):
        return "%s://%s" % (parsed.scheme, parsed.netloc)
    raise ValueError(
        "Im cURL steckt keine Supabase-API. Einen Network-Request zu rest/v1 oder storage kopieren "
        "(nicht nur zur Seite vereinsarchiv-vorstand.pages.dev)."
    )


def ensure_apikey(headers: dict, access_token: Optional[str] = None) -> dict:
    out = dict(headers)
    if not kf.header_value(out, "apikey") and access_token:
        out["apikey"] = access_token
    return out


def absolute_signed_url(base: str, signed: str) -> str:
    signed = (signed or "").strip()
    if not signed:
        return ""
    if signed.startswith("http://") or signed.startswith("https://"):
        return signed
    if not signed.startswith("/"):
        signed = "/" + signed
    origin = base.rstrip("/")
    if signed.startswith("/storage/v1"):
        return origin + signed
    return origin + "/storage/v1" + signed


def rest_headers(headers: dict) -> dict:
    out = {}
    for key, value in headers.items():
        if key.lower() in {"range", "prefer", "content-type", "content-length"}:
            continue
        out[key] = value
    out["Accept"] = "application/json"
    out.setdefault("Accept-Profile", "public")
    if not kf.header_value(out, "origin"):
        out["Origin"] = VA_ORIGIN
    return out


def storage_headers(headers: dict) -> dict:
    out = rest_headers(headers)
    out["Accept"] = "*/*"
    return out


def strip_huge(row: dict, for_ui: bool = False) -> dict:
    drop = UI_DROP if for_ui else HUGE_FIELDS
    return {k: v for k, v in row.items() if k not in drop}


def meta_from_row(kind: str, row: dict, has_audio: bool, detail: bool = False) -> dict:
    return {
        "id": row.get("id"),
        "kind": kind,
        "datum": row.get("datum"),
        "titel": row.get("titel"),
        "ort": row.get("ort") or "",
        "dauer_sek": row.get("dauer_sek"),
        "sprecher": row.get("sprecher") or [],
        "themen": row.get("themen") or [],
        "status": row.get("status"),
        "audio_pfad": row.get("audio_pfad"),
        "freigabe_status": row.get("freigabe_status"),
        "has_audio": bool(has_audio),
        "detail": bool(detail or row.get("transkript") or row.get("zusammenfassung")),
    }


def _file_ok(path: str) -> bool:
    return os.path.isfile(path) and os.path.getsize(path) > 2


def find_audio(kind: str, item_id: str, root: Optional[str] = None) -> Optional[str]:
    folder = item_folder(kind, item_id, root)
    if not os.path.isdir(folder):
        return None
    preferred = []
    others = []
    for name in os.listdir(folder):
        lower = name.lower()
        if lower.endswith(".part"):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            continue
        if lower.startswith("audio.") or lower.endswith(AUDIO_EXT):
            if lower.startswith("audio."):
                preferred.append(path)
            else:
                others.append(path)
    hits = preferred or others
    return hits[0] if hits else None


def is_complete(kind: str, item_id: str, audio_pfad: Optional[str], root: Optional[str] = None) -> bool:
    if not _file_ok(item_json_path(kind, item_id, root)):
        return False
    if not audio_pfad:
        return True
    return find_audio(kind, item_id, root) is not None


def _atomic_json(path: str, data: Any, indent: Optional[int] = None) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=indent, separators=(",", ":"))
    os.replace(tmp, path)


def _write_stream(response: requests.Response, dest: str) -> int:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    tmp = dest + ".part"
    size = 0
    try:
        with open(tmp, "wb") as handle:
            for chunk in response.iter_content(65536):
                if chunk:
                    handle.write(chunk)
                    size += len(chunk)
        if size <= 0:
            raise RuntimeError("Leere Datei")
        os.replace(tmp, dest)
        return size
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _parse_total(content_range: str) -> Optional[int]:
    if not content_range or "/" not in content_range:
        return None
    tail = content_range.rsplit("/", 1)[-1].strip()
    if tail.isdigit():
        return int(tail)
    return None


def fetch_rows(
    base: str,
    headers: dict,
    table: str,
    select: str,
    order: str,
    page_size: int,
    should_stop: StopFn = None,
    timeout: int = 180,
) -> list:
    offset = 0
    total = None
    rows: list = []
    while True:
        if should_stop and should_stop():
            break
        req = rest_headers(headers)
        req["Prefer"] = "count=exact"
        req["Range"] = "%s-%s" % (offset, offset + page_size - 1)
        url = "%s/rest/v1/%s?select=%s&order=%s" % (
            base.rstrip("/"),
            quote(table, safe=""),
            quote(select, safe=",.*()"),
            quote(order, safe=".,"),
        )
        response = requests.get(url, headers=req, timeout=timeout)
        if response.status_code == 416:
            break
        if response.status_code >= 400:
            raise RuntimeError(
                "Stammtische %s: HTTP %s – %s"
                % (table, response.status_code, (response.text or "")[:400])
            )
        batch = response.json()
        if not isinstance(batch, list):
            raise RuntimeError("Unerwartete Antwort von %s." % table)
        parsed_total = _parse_total(response.headers.get("Content-Range") or "")
        if parsed_total is not None:
            total = parsed_total
        rows.extend(batch)
        if not batch or len(batch) < page_size:
            break
        offset += len(batch)
        if total is not None and offset >= total:
            break
    return rows


def _kind_tables(kind: str) -> list:
    spec = KINDS[kind]
    names = []
    for name in list(spec.get("tables") or []) + [spec.get("table")]:
        if name and name not in names:
            names.append(name)
    return names


def fetch_list(base: str, headers: dict, kind: str, should_stop: StopFn = None) -> list:
    spec = KINDS[kind]
    selects = [spec["list_select"], "id,datum,titel,audio_pfad", "id,audio_pfad"]
    last_error = None
    for table in _kind_tables(kind):
        for select in selects:
            try:
                rows = fetch_rows(
                    base,
                    headers,
                    table,
                    select,
                    "datum.desc",
                    spec["page"],
                    should_stop,
                )
                spec["table"] = table
                return rows
            except RuntimeError as exc:
                last_error = exc
    if last_error:
        raise last_error
    return []


def fetch_full_row(base: str, headers: dict, kind: str, item_id: str) -> dict:
    spec = KINDS[kind]
    timeout = 300 if kind == "intern" else 120
    selects = [spec["full_select"], SLIM_DETAIL_SELECT]
    if spec["full_select"] != "*":
        selects.append("*")
    last_error = None
    for table in _kind_tables(kind):
        for select in selects:
            req = rest_headers(headers)
            url = "%s/rest/v1/%s?id=eq.%s&select=%s" % (
                base.rstrip("/"),
                quote(table, safe=""),
                quote(item_id, safe=""),
                quote(select, safe=",.*()"),
            )
            response = requests.get(url, headers=req, timeout=timeout)
            if response.status_code >= 400:
                last_error = RuntimeError(
                    "Detail %s: HTTP %s – %s"
                    % (item_id, response.status_code, (response.text or "")[:400])
                )
                continue
            try:
                rows = response.json()
            except ValueError:
                last_error = RuntimeError("Ungültige JSON-Antwort für %s." % item_id)
                continue
            row_list = rows if isinstance(rows, list) else None
            if not row_list:
                last_error = RuntimeError("Kein Datensatz für %s." % item_id)
                continue
            row = row_list[0]
            if not isinstance(row, dict):
                last_error = RuntimeError("Ungültiger Datensatz für %s." % item_id)
                continue
            spec["table"] = table
            return row
    if last_error:
        raise last_error
    raise RuntimeError("Kein Datensatz für %s." % item_id)


def _bucket_cache_path(root: str) -> str:
    return os.path.join(root, ".buckets.json")


def load_bucket_cache(root: str) -> dict:
    path = _bucket_cache_path(root)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_bucket_cache(root: str, cache: dict) -> None:
    try:
        _atomic_json(_bucket_cache_path(root), cache, indent=2)
    except OSError:
        pass


def list_storage_buckets(base: str, headers: dict) -> list:
    url = "%s/storage/v1/bucket" % base.rstrip("/")
    try:
        response = requests.get(url, headers=storage_headers(headers), timeout=30)
        if response.status_code >= 400:
            return []
        data = response.json()
    except Exception:
        return []
    names = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                name = item.get("name") or item.get("id")
                if name:
                    names.append(str(name))
            elif isinstance(item, str):
                names.append(item)
    return names


def encode_object_path(path: str) -> str:
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    return "/".join(quote(part, safe="") for part in parts)


def _object_urls(base: str, bucket: str, obj_path: str) -> list:
    encoded_bucket = quote(bucket, safe="")
    encoded = encode_object_path(obj_path)
    root = base.rstrip("/")
    return [
        "%s/storage/v1/object/%s/%s" % (root, encoded_bucket, encoded),
        "%s/storage/v1/object/authenticated/%s/%s" % (root, encoded_bucket, encoded),
        "%s/storage/v1/object/public/%s/%s" % (root, encoded_bucket, encoded),
    ]


def _try_signed(base: str, headers: dict, bucket: str, obj_path: str, dest: str) -> int:
    url = "%s/storage/v1/object/sign/%s/%s" % (
        base.rstrip("/"),
        quote(bucket, safe=""),
        encode_object_path(obj_path),
    )
    req = storage_headers(headers)
    req["Content-Type"] = "application/json"
    response = requests.post(url, headers=req, json={"expiresIn": 3600}, timeout=30)
    if response.status_code >= 400:
        return 0
    try:
        data = response.json()
    except ValueError:
        return 0
    signed = data.get("signedURL") or data.get("signedUrl") or ""
    if not signed:
        return 0
    full = absolute_signed_url(base, signed)
    if not full:
        return 0
    got = requests.get(full, headers=storage_headers(headers), timeout=180, stream=True)
    try:
        if got.status_code in (200, 206):
            return _write_stream(got, dest)
    finally:
        got.close()
    return 0


def download_object(
    base: str,
    headers: dict,
    bucket: str,
    obj_path: str,
    dest: str,
    prefer_signed: bool = False,
) -> int:
    if prefer_signed:
        size = _try_signed(base, headers, bucket, obj_path, dest)
        if size > 0:
            return size
    req = storage_headers(headers)
    for url in _object_urls(base, bucket, obj_path):
        response = requests.get(url, headers=req, timeout=180, stream=True)
        try:
            if response.status_code in (200, 206):
                return _write_stream(response, dest)
        finally:
            response.close()
    if prefer_signed:
        return 0
    return _try_signed(base, headers, bucket, obj_path, dest)


def _audio_dest(kind: str, item_id: str, audio_pfad: str, root: str) -> str:
    name = os.path.basename((audio_pfad or "").replace("\\", "/").split("?")[0]) or "audio.aac"
    if "." not in name:
        name = "audio.aac"
    return os.path.join(item_folder(kind, item_id, root), name)


def _audio_path_text(audio_pfad: Any) -> str:
    if isinstance(audio_pfad, str):
        return audio_pfad.strip()
    if isinstance(audio_pfad, dict):
        for key in ("pfad", "path", "url", "audio_pfad"):
            value = audio_pfad.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def split_storage_path(path: str, known_buckets: Optional[list] = None) -> list:
    """(bucket_or_None, object_path) – inkl. Variante mit abgetrenntem Bucket-Präfix."""
    raw = (path or "").strip().lstrip("/")
    if not raw:
        return []
    raw = raw.split("?")[0]
    for prefix in (
        "storage/v1/object/authenticated/",
        "storage/v1/object/public/",
        "storage/v1/object/sign/",
        "storage/v1/object/",
        "object/authenticated/",
        "object/public/",
        "object/sign/",
    ):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
            break
    known = [b for b in (known_buckets or []) if b]
    out = []
    first, sep, rest = raw.partition("/")
    if sep and first in known:
        out.append((first, rest))
    out.append((None, raw))
    return out


def _download_http(url: str, headers: dict, dest: str) -> int:
    req = storage_headers(headers)
    response = requests.get(url, headers=req, timeout=180, stream=True)
    try:
        if response.status_code in (200, 206):
            return _write_stream(response, dest)
    finally:
        response.close()
    return 0


def _bucket_order(kind: str, cache: dict, role: str, listed: Optional[list] = None) -> list:
    buckets = []
    preferred = KINDS.get(kind, {}).get("%s_bucket" % role)
    if preferred:
        buckets.append(preferred)
    cached = cache.get("%s_%s" % (kind, role)) or (cache.get(kind) if role == "audio" else None)
    if cached and cached not in buckets:
        buckets.append(cached)
    for name in list(listed or []) + list(CANDIDATE_BUCKETS):
        if name and name not in buckets:
            buckets.append(name)
    return buckets


def download_audio(
    base: str,
    headers: dict,
    kind: str,
    item_id: str,
    audio_pfad: str,
    root: str,
    cache: dict,
    listed_buckets: Optional[list] = None,
) -> int:
    path_text = _audio_path_text(audio_pfad)
    if not path_text:
        raise RuntimeError("Kein audio_pfad für %s." % item_id)
    dest = _audio_dest(kind, item_id, path_text, root)
    listed = listed_buckets
    if listed is None:
        listed = list_storage_buckets(base, headers)
    if path_text.startswith("http://") or path_text.startswith("https://"):
        size = _download_http(path_text, headers, dest)
        if size > 0:
            return size
        parsed = urlparse(path_text)
        for prefix in ("/storage/v1/object/public/", "/storage/v1/object/authenticated/", "/storage/v1/object/sign/", "/storage/v1/object/"):
            if parsed.path.startswith(prefix):
                path_text = parsed.path[len(prefix) :]
                break
        else:
            raise RuntimeError("Audio-URL nicht ladbar: %s" % item_id)
    buckets = _bucket_order(kind, cache, "audio", listed)
    last_error = None
    tried = set()
    for hinted_bucket, obj_path in split_storage_path(path_text, buckets):
        order = []
        if hinted_bucket:
            order.append(hinted_bucket)
        for bucket in buckets:
            if bucket not in order:
                order.append(bucket)
        for bucket in order:
            key = (bucket, obj_path)
            if key in tried:
                continue
            tried.add(key)
            try:
                size = download_object(
                    base,
                    headers,
                    bucket,
                    obj_path,
                    dest,
                    prefer_signed=(kind != "public"),
                )
                if size > 0:
                    cache[kind] = bucket
                    cache["%s_audio" % kind] = bucket
                    save_bucket_cache(root, cache)
                    return size
            except Exception as exc:
                last_error = exc
    if last_error:
        raise RuntimeError("Audio %s: %s" % (item_id, last_error))
    raise RuntimeError("Audio nicht gefunden: %s (%s)" % (path_text, item_id))


def _folien_paths(folien: Any) -> list:
    paths = []
    if isinstance(folien, str) and "/" in folien and " " not in folien.strip():
        paths.append(folien.strip())
    elif isinstance(folien, list):
        for item in folien:
            if isinstance(item, str) and "/" in item:
                paths.append(item.strip())
            elif isinstance(item, dict):
                for key in ("pfad", "path", "audio_pfad", "url"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        paths.append(value.strip())
                        break
    elif isinstance(folien, dict):
        for key in ("pfad", "path", "url"):
            value = folien.get(key)
            if isinstance(value, str) and value.strip():
                paths.append(value.strip())
                break
    return paths


def download_folien(
    base: str,
    headers: dict,
    kind: str,
    item_id: str,
    folien: Any,
    root: str,
    cache: dict,
    listed_buckets: Optional[list] = None,
) -> None:
    folder = os.path.join(item_folder(kind, item_id, root), "folien")
    buckets = _bucket_order(kind, cache, "folien", listed_buckets)
    for path in _folien_paths(folien):
        if path.startswith("http"):
            continue
        dest = os.path.join(folder, os.path.basename(path.replace("\\", "/")) or "folie")
        if _file_ok(dest):
            continue
        for bucket in buckets:
            try:
                size = download_object(
                    base,
                    headers,
                    bucket,
                    path,
                    dest,
                    prefer_signed=(kind != "public"),
                )
                if size > 0:
                    cache["%s_folien" % kind] = bucket
                    save_bucket_cache(root, cache)
                    break
            except Exception:
                continue


def save_row(kind: str, row: dict, root: str, has_audio: bool) -> None:
    item_id = sanitize_id(str(row.get("id") or ""))
    slim = strip_huge(row)
    _atomic_json(item_json_path(kind, item_id, root), slim)
    _atomic_json(
        item_meta_path(kind, item_id, root),
        meta_from_row(kind, slim, has_audio, detail=True),
        indent=2,
    )


def load_local_json(kind: str, item_id: str, root: Optional[str] = None) -> Optional[dict]:
    path = item_json_path(kind, item_id, root)
    if not _file_ok(path):
        return None
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else None


def list_local(root: Optional[str] = None) -> list:
    base = root or va_root()
    items = []
    for kind in KINDS:
        folder = kind_dir(kind, base)
        if not os.path.isdir(folder):
            continue
        seen = set()
        names = os.listdir(folder)
        names.sort(key=lambda n: (0 if n.endswith(".meta.json") else 1, n))
        for name in names:
            item_id = None
            meta = None
            if name.endswith(".meta.json"):
                item_id = name[: -len(".meta.json")]
                path = os.path.join(folder, name)
                try:
                    with open(path, encoding="utf-8") as handle:
                        meta = json.load(handle)
                except Exception:
                    meta = None
            elif name.endswith(".json") and not name.endswith(".meta.json"):
                item_id = name[: -len(".json")]
            if not item_id or not SAFE_ID.match(item_id) or item_id in seen:
                continue
            seen.add(item_id)
            if not isinstance(meta, dict):
                row = load_local_json(kind, item_id, base) or {}
                meta = meta_from_row(kind, row, find_audio(kind, item_id, base) is not None)
            meta["kind"] = kind
            meta["id"] = item_id
            meta["has_audio"] = find_audio(kind, item_id, base) is not None
            items.append(meta)
    items.sort(key=lambda x: (x.get("datum") or "", x.get("id") or ""), reverse=True)
    return items


def _as_float(value: Any) -> Optional[float]:
    if value is None or value is False:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if ":" in text:
            parts = text.split(":")
            try:
                nums = [float(p) for p in parts]
            except ValueError:
                return None
            if len(nums) == 3:
                return nums[0] * 3600 + nums[1] * 60 + nums[2]
            if len(nums) == 2:
                return nums[0] * 60 + nums[1]
        try:
            return float(text.replace(",", "."))
        except ValueError:
            return None
    return None


def _norm_transcript_item(item: Any, sprecher: list) -> Optional[dict]:
    if isinstance(item, str):
        text = item.strip()
        return {"t": 0, "sp": None, "text": text} if text else None
    if not isinstance(item, dict):
        return None
    text = item.get("text") or item.get("utterance") or item.get("inhalt") or item.get("zeile") or ""
    if not isinstance(text, str):
        text = str(text) if text is not None else ""
    t = None
    for key in ("t", "start", "start_sek", "zeit", "from"):
        if item.get(key) is not None:
            t = _as_float(item.get(key))
            if t is not None:
                break
    if t is None:
        t = 0.0
    if t > 100000:
        t = t / 1000.0
    sp = item.get("sp")
    if sp is None:
        sp = item.get("speaker")
    if sp is None:
        sp = item.get("sprecher")
    if isinstance(sp, str):
        name = sp.strip()
        if name in sprecher:
            sp = sprecher.index(name)
        else:
            sprecher.append(name)
            sp = len(sprecher) - 1
    return {"t": t, "sp": sp, "text": text}


def _norm_zusammenfassung(raw: Any, nach: Any) -> dict:
    data = raw
    if not data and isinstance(nach, dict):
        data = nach
    if isinstance(data, str):
        text = data.strip()
        return {"lead": text} if text else {}
    if not isinstance(data, dict):
        return {}
    out = dict(data)
    if not out.get("lead"):
        for key in ("kurzfassung", "summary", "text", "einleitung"):
            if isinstance(out.get(key), str) and out.get(key).strip():
                out["lead"] = out[key]
                break
    return out


def normalize_item_for_ui(row: dict) -> dict:
    out = dict(row)
    sprecher = out.get("sprecher")
    if isinstance(sprecher, str):
        sprecher = [s.strip() for s in sprecher.replace(";", ",").split(",") if s.strip()]
    if not isinstance(sprecher, list):
        sprecher = []
    names = []
    for item in sprecher:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        elif isinstance(item, dict):
            label = item.get("name") or item.get("sprecher") or item.get("label")
            if isinstance(label, str) and label.strip():
                names.append(label.strip())
    trans = out.get("transkript")
    lines = []
    if isinstance(trans, str) and trans.strip():
        lines = [{"t": 0, "sp": None, "text": trans.strip()}]
    elif isinstance(trans, list):
        for item in trans:
            norm = _norm_transcript_item(item, names)
            if norm:
                lines.append(norm)
    out["sprecher"] = names
    out["transkript"] = lines
    out["zusammenfassung"] = _norm_zusammenfassung(out.get("zusammenfassung"), out.get("nachverfolgung"))
    themen = out.get("themen")
    if isinstance(themen, str):
        out["themen"] = [t.strip() for t in themen.replace(";", ",").split(",") if t.strip()]
    elif not isinstance(themen, list):
        out["themen"] = []
    return out


def load_meta(kind: str, item_id: str, root: Optional[str] = None) -> Optional[dict]:
    path = item_meta_path(kind, item_id, root)
    if not _file_ok(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def needs_detail_fetch(kind: str, item_id: str, root: Optional[str] = None) -> bool:
    row = load_local_json(kind, item_id, root)
    if not row:
        return True
    meta = load_meta(kind, item_id, root) or {}
    if meta.get("detail"):
        return False
    if row.get("transkript") or row.get("zusammenfassung"):
        return False
    return True


def item_for_ui(kind: str, item_id: str, root: Optional[str] = None) -> dict:
    row = load_local_json(kind, item_id, root)
    if not row:
        raise FileNotFoundError("Stammtisch %s ist lokal nicht vorhanden." % item_id)
    out = strip_huge(row, for_ui=True)
    out = normalize_item_for_ui(out)
    out["kind"] = kind
    out["has_audio"] = find_audio(kind, item_id, root) is not None
    return out


def _progress_payload(stats: dict, kind: str, phase: str, current: str, saved: bool = False) -> dict:
    payload = {
        "phase": phase,
        "kind": kind,
        "current": current,
        "total": stats["total"],
        "done": stats["done"],
        "skipped": stats["skipped"],
        "failed": stats["failed"],
        "audio_bytes": stats["audio_bytes"],
    }
    if saved:
        payload["saved"] = True
    return payload


def download_kinds(
    base: str,
    headers: dict,
    kinds: list,
    should_stop: StopFn = None,
    progress_cb: ProgressCb = None,
    cwd: Optional[str] = None,
) -> dict:
    root = va_root(cwd)
    os.makedirs(root, exist_ok=True)
    cache = load_bucket_cache(root)
    listed_buckets = list_storage_buckets(base, headers)
    stats = {"total": 0, "done": 0, "skipped": 0, "failed": 0, "audio_bytes": 0, "errors": []}
    selected = [k for k in kinds if k in KINDS]
    lists = {}
    for kind in selected:
        if should_stop and should_stop():
            break
        if progress_cb:
            progress_cb(
                _progress_payload(
                    stats,
                    kind,
                    "listing",
                    "Lade Liste (%s) …" % KINDS[kind]["label"],
                )
            )
        lists[kind] = fetch_list(base, headers, kind, should_stop)
        stats["total"] += len(lists[kind])

    for kind in selected:
        for row in lists.get(kind) or []:
            if should_stop and should_stop():
                break
            item_id = str(row.get("id") or "")
            if not SAFE_ID.match(item_id):
                stats["failed"] += 1
                stats["errors"].append("Ungültige ID übersprungen")
                continue
            audio_pfad = _audio_path_text(row.get("audio_pfad")) or None
            json_ok = _file_ok(item_json_path(kind, item_id, root))
            audio_ok = find_audio(kind, item_id, root) is not None
            want_detail = needs_detail_fetch(kind, item_id, root)
            if json_ok and not want_detail and (not audio_pfad or audio_ok):
                stats["skipped"] += 1
                stats["done"] += 1
                if progress_cb:
                    progress_cb(_progress_payload(stats, kind, "download", item_id))
                continue
            if progress_cb:
                progress_cb(_progress_payload(stats, kind, "download", item_id))
            try:
                full = None
                if json_ok and not want_detail:
                    full = load_local_json(kind, item_id, root)
                if full is None:
                    full = fetch_full_row(base, headers, kind, item_id)
                audio_pfad = _audio_path_text(full.get("audio_pfad")) or audio_pfad
                has_audio = find_audio(kind, item_id, root) is not None
                save_row(kind, full, root, has_audio)
                if audio_pfad and not has_audio:
                    size = download_audio(
                        base,
                        headers,
                        kind,
                        item_id,
                        str(audio_pfad),
                        root,
                        cache,
                        listed_buckets,
                    )
                    stats["audio_bytes"] += size
                    has_audio = True
                    save_row(kind, full, root, True)
                if full.get("folien"):
                    download_folien(
                        base,
                        headers,
                        kind,
                        item_id,
                        full.get("folien"),
                        root,
                        cache,
                        listed_buckets,
                    )
                stats["done"] += 1
                if progress_cb:
                    progress_cb(_progress_payload(stats, kind, "download", item_id, saved=True))
            except Exception as exc:
                stats["failed"] += 1
                stats["errors"].append("%s: %s" % (item_id, exc))
    return stats
