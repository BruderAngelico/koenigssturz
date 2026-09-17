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
from kingfall_va_store import (
    AUDIO_EXT,
    AUDIO_PREP,
    HUGE_FIELDS,
    KINDS,
    LIST_SELECT,
    PUBLIC_FULL_SELECT,
    SAFE_ID,
    UI_DROP,
    VA_DIRNAME,
    JobCancelled,
    _atomic_json,
    _file_ok,
    convert_pending,
    delete_item,
    delete_items,
    find_audio,
    folie_media,
    folie_path,
    is_complete,
    item_folder,
    item_for_ui,
    item_json_path,
    item_meta_path,
    kind_dir,
    list_folien,
    list_local,
    load_local_json,
    meta_from_row,
    needs_audio_convert,
    open_local_file,
    playback_audio,
    prepare_playback,
    sanitize_filename,
    sanitize_id,
    save_row,
    start_convert_pending,
    strip_huge,
    va_root,
)

VA_ORIGIN = "https://vereinsarchiv-vorstand.pages.dev"

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


DEFAULT_FOLIEN_NAMES = (
    "folie.pdf",
    "folien.pdf",
    "praesentation.pdf",
    "presentation.pdf",
    "slides.pdf",
    "folie.pptx",
    "folien.pptx",
)


def _folien_paths(folien: Any) -> list:
    paths = []

    def _take(value: Any) -> None:
        if not isinstance(value, str):
            return
        text = value.strip()
        if not text:
            return
        if text in paths:
            return
        paths.append(text)

    if isinstance(folien, str):
        _take(folien)
    elif isinstance(folien, list):
        for item in folien:
            if isinstance(item, str):
                _take(item)
            elif isinstance(item, dict):
                for key in ("pfad", "path", "url", "file", "datei", "filename", "name"):
                    if item.get(key):
                        _take(item.get(key))
                        break
    elif isinstance(folien, dict):
        for key in ("pfad", "path", "url", "file", "datei", "filename"):
            if folien.get(key):
                _take(folien.get(key))
                break
    return paths


def _folien_missing(kind: str, item_id: str, folien: Any, root: str, audio_pfad: Any = None) -> bool:
    wanted = _folien_paths(folien)
    have = {name.lower() for name in list_folien(kind, item_id, root)}
    if wanted:
        if not have:
            return True
        for path in wanted:
            base = os.path.basename(path.replace("\\", "/").split("?")[0]).lower()
            if base and base in have:
                continue
            return True
        return False
    if not audio_pfad and not have:
        return True
    return False


def _compare_row(kind: str, row: dict, root: str) -> dict:
    item_id = str(row.get("id") or "")
    audio_pfad = row.get("audio_pfad") or None
    json_ok = _file_ok(item_json_path(kind, item_id, root)) if item_id else False
    audio_ok = find_audio(kind, item_id, root) is not None if item_id else False
    local = load_local_json(kind, item_id, root) if json_ok else None
    src = local or row
    folien_missing = _folien_missing(kind, item_id, src.get("folien"), root, audio_pfad) if item_id else False
    folien_have = bool(list_folien(kind, item_id, root)) if item_id else False
    if not item_id or not SAFE_ID.match(item_id):
        stand = "fehler"
        need = False
    elif not json_ok:
        stand = "neu"
        need = True
    elif audio_pfad and not audio_ok:
        stand = "audio"
        need = True
    elif folien_missing:
        stand = "folien"
        need = True
    else:
        stand = "ok"
        need = False
    return {
        "id": item_id,
        "kind": kind,
        "datum": row.get("datum") or (local or {}).get("datum") or "",
        "titel": row.get("titel") or (local or {}).get("titel") or item_id,
        "ort": row.get("ort") or (local or {}).get("ort") or "",
        "dauer_sek": row.get("dauer_sek") if row.get("dauer_sek") is not None else (local or {}).get("dauer_sek"),
        "has_audio": audio_ok,
        "has_folien": folien_have,
        "remote_audio": bool(audio_pfad),
        "stand": stand,
        "need": need,
    }


def compare_kinds(
    base: str,
    headers: dict,
    kinds: list,
    should_stop: StopFn = None,
    progress_cb: ProgressCb = None,
    cwd: Optional[str] = None,
) -> dict:
    root = va_root(cwd)
    selected = [k for k in kinds if k in KINDS]
    lists = {}
    items = []
    counts = {"remote": 0, "neu": 0, "audio": 0, "folien": 0, "ok": 0, "fehler": 0, "need": 0}
    for kind in selected:
        if should_stop and should_stop():
            break
        if progress_cb:
            progress_cb(
                _progress_payload(
                    {"total": 0, "done": 0, "skipped": 0, "failed": 0, "audio_bytes": 0},
                    kind,
                    "listing",
                    "Lade Liste (%s) …" % KINDS[kind]["label"],
                )
            )
        lists[kind] = fetch_list(base, headers, kind, should_stop)
    for kind in selected:
        for row in lists.get(kind) or []:
            if should_stop and should_stop():
                break
            item = _compare_row(kind, row if isinstance(row, dict) else {}, root)
            items.append(item)
            counts["remote"] += 1
            key = item.get("stand") or "fehler"
            if key in counts:
                counts[key] += 1
            if item.get("need"):
                counts["need"] += 1
    items.sort(key=lambda x: (x.get("datum") or "", x.get("id") or ""), reverse=True)
    return {"items": items, "counts": counts, "lists": lists}


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
    item_dir = item_folder(kind, item_id, root)
    buckets = _bucket_order(kind, cache, "folien", listed_buckets)
    paths = _folien_paths(folien)
    if not paths:
        paths = list(DEFAULT_FOLIEN_NAMES)
        paths.append("%s.pdf" % item_id)
    for path in paths:
        raw_name = os.path.basename(path.replace("\\", "/").split("?")[0]) or "folie.pdf"
        try:
            name = sanitize_filename(raw_name)
        except ValueError:
            name = "folie.pdf"
        dest = os.path.join(folder, name)
        if _file_ok(dest) or _file_ok(os.path.join(item_dir, name)):
            continue
        os.makedirs(folder, exist_ok=True)
        if path.startswith("http://") or path.startswith("https://"):
            try:
                response = requests.get(path, headers=storage_headers(headers), timeout=180, stream=True)
                try:
                    if response.status_code in (200, 206):
                        _write_stream(response, dest)
                        continue
                finally:
                    response.close()
            except Exception:
                continue
            continue
        candidates = []
        for obj in (
            path,
            name,
            "%s/%s" % (item_id, name),
            "folien/%s" % name,
            "%s/folien/%s" % (item_id, name),
        ):
            if obj and obj not in candidates:
                candidates.append(obj)
        got = False
        for bucket in buckets:
            if got:
                break
            for obj in candidates:
                try:
                    size = download_object(
                        base,
                        headers,
                        bucket,
                        obj,
                        dest,
                        prefer_signed=(kind != "public"),
                    )
                    if size > 0:
                        cache["%s_folien" % kind] = bucket
                        save_bucket_cache(root, cache)
                        got = True
                        break
                except Exception:
                    continue


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

    preview = []
    for kind in selected:
        for row in lists.get(kind) or []:
            if isinstance(row, dict):
                preview.append(_compare_row(kind, row, root))
    if progress_cb and preview:
        payload = _progress_payload(stats, selected[0] if selected else "", "plan", "Abgleich fertig")
        payload["preview"] = preview
        progress_cb(payload)

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
            local = load_local_json(kind, item_id, root) if json_ok else None
            folien_src = (local or row).get("folien")
            folien_ok = not _folien_missing(kind, item_id, folien_src, root, audio_pfad)
            if json_ok and (not audio_pfad or audio_ok) and folien_ok:
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
                if full.get("folien") or not audio_pfad:
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
