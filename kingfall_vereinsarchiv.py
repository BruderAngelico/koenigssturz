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
PUBLIC_FULL_SELECT = (
    "id,datum,titel,ort,dauer_sek,sprecher,themen,status,zusammenfassung,"
    "transkript,folien,audio_pfad,erstellt_am,kapitel,verarbeitung,folientext,"
    "nachverfolgung,wellenform,freigabe_status,freigegeben_von,freigegeben_am,"
    "aufbewahrung_markiert,markiert_am,markiert_grund"
)

KINDS = {
    "public": {
        "table": "stammtische",
        "folder": "stammtische",
        "label": "Öffentlich",
        "list_select": LIST_SELECT,
        "full_select": PUBLIC_FULL_SELECT,
        "page": 200,
        "audio_bucket": "audio",
        "folien_bucket": "folien",
    },
    "intern": {
        "table": "stammtische_intern",
        "folder": "stammtische_intern",
        "label": "Intern",
        "list_select": LIST_SELECT,
        "full_select": "*",
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


def meta_from_row(kind: str, row: dict, has_audio: bool) -> dict:
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


def fetch_list(base: str, headers: dict, kind: str, should_stop: StopFn = None) -> list:
    spec = KINDS[kind]
    selects = [spec["list_select"], "id,datum,titel,audio_pfad", "id,audio_pfad"]
    last_error = None
    for select in selects:
        try:
            return fetch_rows(
                base,
                headers,
                spec["table"],
                select,
                "datum.desc",
                spec["page"],
                should_stop,
            )
        except RuntimeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    return []


def fetch_full_row(base: str, headers: dict, kind: str, item_id: str) -> dict:
    spec = KINDS[kind]
    timeout = 300 if kind == "intern" else 120
    selects = [spec["full_select"]]
    if spec["full_select"] != "*":
        selects.append("*")
    last_error = None
    for select in selects:
        req = rest_headers(headers)
        url = "%s/rest/v1/%s?id=eq.%s&select=%s" % (
            base.rstrip("/"),
            quote(spec["table"], safe=""),
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
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            last_error = RuntimeError("Kein Datensatz für %s." % item_id)
            continue
        row = rows[0]
        if not isinstance(row, dict):
            last_error = RuntimeError("Ungültiger Datensatz für %s." % item_id)
            continue
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
    if signed.startswith("http"):
        full = signed
    else:
        full = base.rstrip("/") + "/storage/v1" + (signed if signed.startswith("/") else "/" + signed)
    got = requests.get(full, timeout=180, stream=True)
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
    name = os.path.basename(audio_pfad.replace("\\", "/")) or "audio.aac"
    if "." not in name:
        name = "audio.aac"
    return os.path.join(item_folder(kind, item_id, root), name)


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
    dest = _audio_dest(kind, item_id, audio_pfad, root)
    listed = listed_buckets
    if listed is None:
        listed = list_storage_buckets(base, headers)
    last_error = None
    for bucket in _bucket_order(kind, cache, "audio", listed):
        try:
            size = download_object(
                base,
                headers,
                bucket,
                audio_pfad,
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
    raise RuntimeError("Audio nicht gefunden: %s (%s)" % (audio_pfad, item_id))


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
    _atomic_json(item_meta_path(kind, item_id, root), meta_from_row(kind, slim, has_audio), indent=2)


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


def item_for_ui(kind: str, item_id: str, root: Optional[str] = None) -> dict:
    row = load_local_json(kind, item_id, root)
    if not row:
        raise FileNotFoundError("Stammtisch %s ist lokal nicht vorhanden." % item_id)
    out = strip_huge(row, for_ui=True)
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
            audio_pfad = row.get("audio_pfad") or None
            json_ok = _file_ok(item_json_path(kind, item_id, root))
            audio_ok = find_audio(kind, item_id, root) is not None
            if json_ok and (not audio_pfad or audio_ok):
                stats["skipped"] += 1
                stats["done"] += 1
                if progress_cb:
                    progress_cb(_progress_payload(stats, kind, "download", item_id))
                continue
            if progress_cb:
                progress_cb(_progress_payload(stats, kind, "download", item_id))
            try:
                full = load_local_json(kind, item_id, root) if json_ok else None
                if full is None:
                    full = fetch_full_row(base, headers, kind, item_id)
                audio_pfad = full.get("audio_pfad") or audio_pfad
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
