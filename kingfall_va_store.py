# -*- coding: utf-8 -*-
"""Lokales Vereinsarchiv auf Disk. Kein Netz, kein Königssturz-GUI."""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

VA_DIRNAME = "vereinsarchiv"
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
SAFE_FILE = re.compile(r"^[A-Za-z0-9._-]+$")
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

VA_AUDIO_TYPES = {
    ".aac": "audio/aac",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
}


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


def sanitize_filename(name: str) -> str:
    raw = os.path.basename((name or "").strip())
    if not SAFE_FILE.match(raw):
        raise ValueError("Ungültiger Dateiname.")
    return raw


def item_json_path(kind: str, item_id: str, root: Optional[str] = None) -> str:
    return os.path.join(kind_dir(kind, root), sanitize_id(item_id) + ".json")


def item_meta_path(kind: str, item_id: str, root: Optional[str] = None) -> str:
    return os.path.join(kind_dir(kind, root), sanitize_id(item_id) + ".meta.json")


def item_folder(kind: str, item_id: str, root: Optional[str] = None) -> str:
    return os.path.join(kind_dir(kind, root), sanitize_id(item_id))


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


def _atomic_json(path: str, data: Any, indent: Optional[int] = None) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=indent, separators=(",", ":"))
    os.replace(tmp, path)


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


def list_folien(kind: str, item_id: str, root: Optional[str] = None) -> list:
    folder = os.path.join(item_folder(kind, item_id, root), "folien")
    if not os.path.isdir(folder):
        return []
    names = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and os.path.getsize(path) > 0 and SAFE_FILE.match(name):
            names.append(name)
    return names


def folie_path(kind: str, item_id: str, name: str, root: Optional[str] = None) -> str:
    safe_id = sanitize_id(item_id)
    safe_name = sanitize_filename(name)
    path = os.path.join(item_folder(kind, safe_id, root), "folien", safe_name)
    folder = os.path.abspath(os.path.join(item_folder(kind, safe_id, root), "folien"))
    full = os.path.abspath(path)
    if not full.startswith(folder + os.sep):
        raise ValueError("Ungültiger Pfad.")
    if not os.path.isfile(full):
        raise FileNotFoundError("Folie nicht gefunden.")
    return full


def is_complete(kind: str, item_id: str, audio_pfad: Optional[str], root: Optional[str] = None) -> bool:
    if not _file_ok(item_json_path(kind, item_id, root)):
        return False
    if not audio_pfad:
        return True
    return find_audio(kind, item_id, root) is not None


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
    out["folien_dateien"] = list_folien(kind, item_id, root)
    meta_path = item_meta_path(kind, item_id, root)
    if _file_ok(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as handle:
                meta = json.load(handle)
            if isinstance(meta, dict) and meta.get("hinweis"):
                out["hinweis"] = meta.get("hinweis")
            if isinstance(meta, dict) and meta.get("titel"):
                out["titel"] = meta.get("titel")
        except Exception:
            pass
    return out
