# -*- coding: utf-8 -*-
"""Pakete aus lokalem Vereinsarchiv bauen und im Reader mergen."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime
from typing import Any, Optional

import kingfall_va_store as va

MANIFEST_NAME = "va-paket.json"
KIND_LABEL = {"public": "Öffentlich", "intern": "Intern"}


def _canon(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_file(path: Optional[str]) -> Optional[str]:
    if not path or not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _day(value: Any) -> str:
    text = ("" if value is None else str(value)).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return text


def fingerprint(kind: str, item_id: str, root: Optional[str] = None) -> dict:
    row = va.strip_huge(va.load_local_json(kind, item_id, root) or {})
    audio = va.find_audio(kind, item_id, root)
    return {
        "json": _hash_text(_canon(row)) if row else "",
        "audio": _hash_file(audio),
        "has_audio": audio is not None,
    }


def filter_local(
    root: Optional[str] = None,
    kinds: Optional[list] = None,
    date_from: str = "",
    date_to: str = "",
    ids: Optional[list] = None,
) -> list:
    wanted_kinds = [k for k in (kinds or list(va.KINDS)) if k in va.KINDS]
    id_set = None
    if ids:
        id_set = set()
        for item in ids:
            if isinstance(item, dict):
                id_set.add("%s/%s" % (item.get("kind"), item.get("id")))
            else:
                id_set.add(str(item))
    start = _day(date_from)
    end = _day(date_to)
    out = []
    for item in va.list_local(root):
        if item.get("kind") not in wanted_kinds:
            continue
        day = _day(item.get("datum"))
        if start and day and day < start:
            continue
        if end and day and day > end:
            continue
        if id_set is not None:
            key = "%s/%s" % (item.get("kind"), item.get("id"))
            if key not in id_set and str(item.get("id")) not in id_set:
                continue
        out.append(item)
    return out


def _copy_item(src_root: str, dest_root: str, kind: str, src_id: str, dest_id: str) -> None:
    src_id = va.sanitize_id(src_id)
    dest_id = va.sanitize_id(dest_id)
    src_json = va.item_json_path(kind, src_id, src_root)
    if not va._file_ok(src_json):
        raise FileNotFoundError("Kein JSON für %s/%s" % (kind, src_id))
    os.makedirs(va.kind_dir(kind, dest_root), exist_ok=True)
    row = va.load_local_json(kind, src_id, src_root) or {}
    row["id"] = dest_id
    va._atomic_json(va.item_json_path(kind, dest_id, dest_root), va.strip_huge(row))
    meta_path = va.item_meta_path(kind, src_id, src_root)
    meta = {}
    if va._file_ok(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                meta = loaded
        except Exception:
            meta = {}
    if not meta:
        meta = va.meta_from_row(kind, row, va.find_audio(kind, src_id, src_root) is not None)
    meta["id"] = dest_id
    meta["kind"] = kind
    meta["has_audio"] = va.find_audio(kind, src_id, src_root) is not None
    va._atomic_json(va.item_meta_path(kind, dest_id, dest_root), meta, indent=2)
    src_folder = va.item_folder(kind, src_id, src_root)
    dest_folder = va.item_folder(kind, dest_id, dest_root)
    if os.path.isdir(src_folder):
        if os.path.isdir(dest_folder):
            shutil.rmtree(dest_folder)
        shutil.copytree(src_folder, dest_folder)


def _unique_copy_id(kind: str, item_id: str, root: str) -> str:
    base = va.sanitize_id(item_id)
    stamp = datetime.now().strftime("%Y%m%d")
    n = 1
    while True:
        candidate = "%s__kopie_%s" % (base, stamp) if n == 1 else "%s__kopie_%s_%s" % (base, stamp, n)
        if not va._file_ok(va.item_json_path(kind, candidate, root)):
            return candidate
        n += 1


def build_zip(
    dest_zip: str,
    root: Optional[str] = None,
    kinds: Optional[list] = None,
    date_from: str = "",
    date_to: str = "",
    ids: Optional[list] = None,
) -> dict:
    src = root or va.va_root()
    items = filter_local(src, kinds, date_from, date_to, ids)
    if not items:
        raise ValueError("Nichts zum Verpacken. Nur lokal geladene Stammtische können ins Paket.")
    tmp = tempfile.mkdtemp(prefix="va-paket-")
    try:
        bundle_root = os.path.join(tmp, "vereinsarchiv")
        os.makedirs(bundle_root, exist_ok=True)
        packed = []
        for item in items:
            kind = item["kind"]
            item_id = item["id"]
            _copy_item(src, bundle_root, kind, item_id, item_id)
            packed.append(
                {
                    "kind": kind,
                    "id": item_id,
                    "datum": item.get("datum"),
                    "titel": item.get("titel"),
                }
            )
        manifest = {
            "format": "koenigssturz-va-paket",
            "version": 1,
            "created": datetime.now().isoformat(timespec="seconds"),
            "kinds": sorted({p["kind"] for p in packed}),
            "count": len(packed),
            "items": packed,
        }
        with open(os.path.join(tmp, MANIFEST_NAME), "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
        os.makedirs(os.path.dirname(dest_zip) or ".", exist_ok=True)
        with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for dirpath, _, filenames in os.walk(tmp):
                for name in filenames:
                    full = os.path.join(dirpath, name)
                    rel = os.path.relpath(full, tmp)
                    zf.write(full, rel.replace("\\", "/"))
        return {"ok": True, "count": len(packed), "path": dest_zip, "items": packed}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _extract_zip(zip_path: str) -> str:
    tmp = tempfile.mkdtemp(prefix="va-import-")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(tmp)
    root = os.path.join(tmp, "vereinsarchiv")
    if os.path.isdir(root):
        return tmp
    # Zip nur mit stammtische/ oben
    for name in ("stammtische", "stammtische_intern"):
        if os.path.isdir(os.path.join(tmp, name)):
            alt = os.path.join(tmp, "vereinsarchiv")
            os.makedirs(alt, exist_ok=True)
            shutil.move(os.path.join(tmp, name), os.path.join(alt, name))
    if os.path.isdir(os.path.join(tmp, "vereinsarchiv")):
        return tmp
    shutil.rmtree(tmp, ignore_errors=True)
    raise ValueError("Die Zip ist kein Vereinsarchiv-Paket.")


def incoming_root(extract_dir: str) -> str:
    return os.path.join(extract_dir, "vereinsarchiv")


def classify_item(kind: str, item_id: str, src_root: str, dest_root: str) -> str:
    """new | skip | audio | conflict"""
    dest_json = va.item_json_path(kind, item_id, dest_root)
    if not va._file_ok(dest_json):
        return "new"
    src_fp = fingerprint(kind, item_id, src_root)
    dest_fp = fingerprint(kind, item_id, dest_root)
    if src_fp["json"] == dest_fp["json"] and src_fp["audio"] == dest_fp["audio"]:
        return "skip"
    if src_fp["json"] == dest_fp["json"] and dest_fp["audio"] is None and src_fp["audio"]:
        return "audio"
    if src_fp["json"] != dest_fp["json"] or (src_fp["audio"] and dest_fp["audio"] and src_fp["audio"] != dest_fp["audio"]):
        return "conflict"
    if src_fp["json"] == dest_fp["json"]:
        return "skip"
    return "conflict"


def preview_import(zip_path: str, dest_root: Optional[str] = None) -> dict:
    dest = dest_root or va.va_root()
    extract_dir = _extract_zip(zip_path)
    try:
        src = incoming_root(extract_dir)
        stats = {"new": [], "skip": [], "audio": [], "conflict": []}
        for item in va.list_local(src):
            kind = item["kind"]
            item_id = item["id"]
            bucket = classify_item(kind, item_id, src, dest)
            stats[bucket].append(
                {
                    "kind": kind,
                    "id": item_id,
                    "titel": item.get("titel") or item_id,
                    "datum": item.get("datum"),
                    "label": KIND_LABEL.get(kind, kind),
                }
            )
        stats["counts"] = {k: len(stats[k]) for k in ("new", "skip", "audio", "conflict")}
        return stats
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


def apply_import(zip_path: str, dest_root: Optional[str] = None, on_conflict: str = "abort") -> dict:
    if on_conflict not in {"abort", "copy"}:
        raise ValueError("on_conflict muss abort oder copy sein.")
    dest = dest_root or va.va_root()
    os.makedirs(dest, mode=0o700, exist_ok=True)
    try:
        os.chmod(dest, 0o700)
    except OSError:
        pass
    extract_dir = _extract_zip(zip_path)
    try:
        src = incoming_root(extract_dir)
        preview = {"new": [], "skip": [], "audio": [], "conflict": []}
        for item in va.list_local(src):
            kind = item["kind"]
            item_id = item["id"]
            bucket = classify_item(kind, item_id, src, dest)
            preview[bucket].append(item)
        if preview["conflict"] and on_conflict == "abort":
            return {
                "ok": False,
                "aborted": True,
                "message": "Inhaltliche Unterschiede. Abgebrochen, nichts geändert.",
                "conflicts": [
                    {
                        "kind": i["kind"],
                        "id": i["id"],
                        "titel": i.get("titel") or i["id"],
                    }
                    for i in preview["conflict"]
                ],
            }
        added = 0
        skipped = 0
        updated_audio = 0
        copies = 0
        for item in preview["new"]:
            _copy_item(src, dest, item["kind"], item["id"], item["id"])
            added += 1
        for item in preview["skip"]:
            skipped += 1
        for item in preview["audio"]:
            _copy_item(src, dest, item["kind"], item["id"], item["id"])
            updated_audio += 1
        if on_conflict == "copy":
            for item in preview["conflict"]:
                new_id = _unique_copy_id(item["kind"], item["id"], dest)
                _copy_item(src, dest, item["kind"], item["id"], new_id)
                meta_path = va.item_meta_path(item["kind"], new_id, dest)
                meta = {}
                if va._file_ok(meta_path):
                    with open(meta_path, encoding="utf-8") as handle:
                        meta = json.load(handle)
                if not isinstance(meta, dict):
                    meta = {}
                meta["hinweis"] = "Kopie aus Update – Original war schon da, Inhalt unterschied sich."
                meta["quelle_id"] = item["id"]
                titel = meta.get("titel") or item.get("titel") or new_id
                if "(Kopie" not in str(titel):
                    meta["titel"] = "%s (Kopie aus Update)" % titel
                va._atomic_json(meta_path, meta, indent=2)
                copies += 1
        return {
            "ok": True,
            "added": added,
            "skipped": skipped,
            "updated_audio": updated_audio,
            "copies": copies,
            "conflicts": len(preview["conflict"]),
        }
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


def default_data_dir() -> str:
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        path = os.path.join(home, "Library", "Application Support", "KoenigssturzVAReader")
    else:
        path = os.path.join(home, ".local", "share", "koenigssturz-va-reader")
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return os.path.join(path, "vereinsarchiv")
