# -*- coding: utf-8 -*-
"""Lokales Vereinsarchiv auf Disk. Kein Netz, kein Königssturz-GUI."""
from __future__ import annotations

import json
import os
import re
import select
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Optional, Tuple

VA_DIRNAME = "vereinsarchiv"
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
SAFE_FILE = re.compile(r"^[A-Za-z0-9._-]+$")
AUDIO_EXT = (".aac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".webm")
DOC_EXT = (".pdf", ".ppt", ".pptx", ".key", ".odp", ".pages", ".doc", ".docx")
DOC_MEDIA = {
    ".pdf": "application/pdf",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".key": "application/x-iwork-keynote-sffkey",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".pages": "application/x-iwork-pages-sffpages",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
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
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
}
# HTML5 in Safari/WKWebView: MP3/M4A/WAV. Opus/OGG/WebM und ADTS-AAC werden nach M4A gewandelt.
WEBKIT_SAFE_EXT = {".m4a", ".mp3", ".wav"}
NEED_CONVERT_EXT = {".opus", ".ogg", ".webm", ".aac"}
PLAY_CACHE_NAME = "audio.play.m4a"


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


def audio_media_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return VA_AUDIO_TYPES.get(ext, "application/octet-stream")


class JobCancelled(Exception):
    pass


def _which(name: str) -> Optional[str]:
    extra = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]
    found = shutil.which(name)
    if found:
        return found
    for folder in extra:
        candidate = os.path.join(folder, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def ffmpeg_exe() -> Optional[str]:
    found = _which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _cache_fresh(src: str, cache: str) -> bool:
    try:
        return (
            os.path.isfile(cache)
            and os.path.getsize(cache) > 2
            and os.path.getmtime(cache) >= os.path.getmtime(src)
        )
    except OSError:
        return False


def play_cache_path(src: str) -> str:
    return os.path.join(os.path.dirname(src), PLAY_CACHE_NAME)


def needs_audio_convert(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    cache = play_cache_path(path)
    if _cache_fresh(path, cache):
        return False
    return ext not in WEBKIT_SAFE_EXT


class AudioPrep:
    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.stop = False
        self.running = False
        self.kind = ""
        self.item_id = ""
        self.status = ""
        self.error = ""
        self.pct = 0
        self.eta_sec = None
        self.ready = False
        self.total = 0
        self.done = 0
        self.file_pct = 0
        self.index = 0

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "running": self.running,
                "status": self.status,
                "error": self.error,
                "pct": self.pct,
                "file_pct": self.file_pct,
                "eta_sec": self.eta_sec,
                "ready": self.ready,
                "kind": self.kind,
                "id": self.item_id,
                "item_id": self.item_id,
                "total": self.total,
                "done": self.done,
                "index": self.index,
            }

    def request_stop(self) -> None:
        with self.lock:
            self.stop = True
            proc = self.proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass


AUDIO_PREP = AudioPrep()


def _stop_proc(proc: Optional[subprocess.Popen]) -> None:
    if not proc or proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        pass
    try:
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            pass


def _convert_timeout(src: str) -> int:
    try:
        size = os.path.getsize(src)
    except OSError:
        size = 0
    return min(1800, max(120, int(size / 50000) + 90))


def _parse_out_time(line: str) -> float:
    line = (line or "").strip()
    if line.startswith("out_time_ms="):
        try:
            return max(0.0, int(line.split("=", 1)[1]) / 1e6)
        except ValueError:
            return -1.0
    if line.startswith("out_time="):
        raw = line.split("=", 1)[1].strip()
        match = re.match(r"(\d+):(\d+):(\d+(?:\.\d+)?)", raw)
        if match:
            return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
    return -1.0


def _mark_file_pct(prep: Optional[AudioPrep], file_pct: int) -> None:
    if not prep:
        return
    file_pct = max(0, min(99, int(file_pct)))
    with prep.lock:
        prep.file_pct = file_pct
        total = prep.total or 1
        index = max(1, prep.index or 1)
        prep.pct = min(99, int(round(100.0 * ((index - 1) + file_pct / 100.0) / total)))


def _to_m4a(
    src: str,
    dest: str,
    prep: Optional[AudioPrep] = None,
    duration_sec: Optional[float] = None,
) -> bool:
    ffmpeg = ffmpeg_exe()
    tmp = dest + ".part.m4a"
    try:
        if os.path.isfile(tmp):
            os.remove(tmp)
    except OSError:
        pass
    timeout = _convert_timeout(src)
    if ffmpeg:
        cmd = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            src,
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-progress",
            "pipe:1",
            "-nostats",
            "-f",
            "mp4",
            tmp,
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
        except OSError:
            proc = None
        if proc:
            if prep:
                with prep.lock:
                    prep.proc = proc
                    prep.file_pct = 0
            duration = float(duration_sec or 0)
            deadline = time.time() + timeout
            buf = b""
            ended = False
            try:
                while True:
                    if prep and prep.stop:
                        _stop_proc(proc)
                        raise JobCancelled()
                    if time.time() > deadline:
                        _stop_proc(proc)
                        proc = None
                        break
                    if proc.stdout is None:
                        break
                    ready, _, _ = select.select([proc.stdout], [], [], 0.4)
                    if not ready:
                        if proc.poll() is not None:
                            break
                        continue
                    chunk = proc.stdout.read(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        line = raw.decode("utf-8", "replace").strip()
                        spent = _parse_out_time(line)
                        if spent >= 0 and duration > 0:
                            _mark_file_pct(prep, int(100.0 * spent / duration))
                        if line == "progress=end":
                            ended = True
                    if ended:
                        break
                if proc is not None and proc.poll() is None:
                    try:
                        proc.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        _stop_proc(proc)
            except JobCancelled:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                return False
            finally:
                if prep:
                    with prep.lock:
                        prep.proc = None
            if proc is not None and proc.returncode == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 2:
                os.replace(tmp, dest)
                if prep:
                    _mark_file_pct(prep, 100)
                return True
            try:
                os.remove(tmp)
            except OSError:
                pass
    afconvert = _which("afconvert")
    ext = os.path.splitext(src)[1].lower()
    if afconvert and ext in {".aac", ".m4a", ".mp3", ".wav", ".aif", ".aiff", ".caf"}:
        ok = False
        try:
            proc = subprocess.run(
                [afconvert, "-f", "m4af", "-d", "aac", src, tmp],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
            ok = proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if ok and os.path.isfile(tmp) and os.path.getsize(tmp) > 2:
            os.replace(tmp, dest)
            return True
        try:
            os.remove(tmp)
        except OSError:
            pass
    return False


def playback_audio(path: str) -> Tuple[str, str]:
    """Datei + MIME für Safari/WKWebView. Keine Wandlung im Request."""
    cache = play_cache_path(path)
    if _cache_fresh(path, cache):
        return cache, "audio/mp4"
    ext = os.path.splitext(path)[1].lower()
    if ext in WEBKIT_SAFE_EXT:
        return path, audio_media_type(path)
    return path, audio_media_type(path)


def convert_pending(
    root: Optional[str] = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
    stop_fn: Optional[Callable[[], bool]] = None,
) -> dict:
    prep = AUDIO_PREP
    waited = 0.0
    while waited < 3600:
        with prep.lock:
            busy = prep.running
        if not busy:
            break
        if stop_fn and stop_fn():
            return {"ok": False, "aborted": True}
        time.sleep(0.4)
        waited += 0.4
    with prep.lock:
        if prep.running:
            return prep.snapshot()
        prep.stop = False
        prep.running = True
        prep.error = ""
        prep.ready = False
        prep.pct = 0
        prep.eta_sec = None
        prep.total = 0
        prep.done = 0
        prep.file_pct = 0
        prep.index = 0
        prep.status = "Suche Audio zum Wandeln …"
    try:
        jobs = []
        for item in list_local(root):
            if (stop_fn and stop_fn()) or prep.stop:
                raise JobCancelled()
            path = find_audio(item["kind"], item["id"], root)
            if path and needs_audio_convert(path):
                jobs.append((item, path))
        total = len(jobs)
        done = 0
        failed = 0
        with prep.lock:
            prep.total = total
            prep.status = "Nichts zu wandeln" if total == 0 else "%s Audio-Dateien werden gewandelt …" % total
            if total == 0:
                prep.running = False
                prep.ready = True
                prep.pct = 100
                return {"ok": True, "done": 0, "failed": 0, "total": 0}
        started = time.time()
        for index, (item, path) in enumerate(jobs, start=1):
            if (stop_fn and stop_fn()) or prep.stop:
                raise JobCancelled()
            elapsed = time.time() - started
            eta = None
            if index > 1:
                eta = max(0, int(elapsed / (index - 1) * (total - index + 1)))
            with prep.lock:
                prep.kind = item["kind"]
                prep.item_id = item["id"]
                prep.index = index
                prep.done = index - 1
                prep.file_pct = 0
                prep.pct = int(round(100.0 * (index - 1) / total))
                prep.eta_sec = eta
                prep.status = "Wandle Audio %s/%s: %s" % (
                    index,
                    total,
                    item.get("titel") or item.get("id"),
                )
            if progress_cb:
                progress_cb(
                    {
                        "status": prep.status,
                        "pct": prep.pct,
                        "eta_sec": eta,
                        "current": index,
                        "total": total,
                    }
                )
            dur = item.get("dauer_sek")
            try:
                dur_f = float(dur) if dur is not None else 0.0
            except (TypeError, ValueError):
                dur_f = 0.0
            if _to_m4a(path, play_cache_path(path), prep, duration_sec=dur_f):
                done += 1
            else:
                failed += 1
        with prep.lock:
            prep.running = False
            prep.done = done
            prep.pct = 100
            prep.eta_sec = 0
            prep.ready = failed == 0
            if failed:
                prep.status = "Audio: %s gewandelt, %s fehlgeschlagen" % (done, failed)
                prep.error = "%s Dateien ließen sich nicht wandeln." % failed
            else:
                prep.status = "Audio bereit (%s)" % done
                prep.error = ""
            return {
                "ok": failed == 0,
                "done": done,
                "failed": failed,
                "total": total,
            }
    except JobCancelled:
        with prep.lock:
            prep.running = False
            prep.status = "Abgebrochen"
            prep.error = ""
        return {"ok": False, "aborted": True}
    except Exception as exc:
        with prep.lock:
            prep.running = False
            prep.error = str(exc)
            prep.status = "Fehler"
        return {"ok": False, "error": str(exc)}


def start_convert_pending(root: Optional[str] = None) -> None:
    threading.Thread(target=convert_pending, kwargs={"root": root}, daemon=True).start()


def prepare_playback(path: str) -> dict:
    cache = play_cache_path(path)
    if _cache_fresh(path, cache) or os.path.splitext(path)[1].lower() in WEBKIT_SAFE_EXT:
        return {"ok": True, "ready": True, "pct": 100}
    start_convert_pending()
    return AUDIO_PREP.snapshot()



def find_audio(kind: str, item_id: str, root: Optional[str] = None) -> Optional[str]:
    folder = item_folder(kind, item_id, root)
    if not os.path.isdir(folder):
        return None
    preferred = []
    others = []
    for name in os.listdir(folder):
        lower = name.lower()
        if lower.endswith(".part") or lower.endswith(".part.m4a") or lower == PLAY_CACHE_NAME:
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


def convert_if_needed(
    kind: str,
    item_id: str,
    root: Optional[str] = None,
    duration_sec: Optional[float] = None,
) -> bool:
    path = find_audio(kind, item_id, root)
    if not path:
        return True
    if not needs_audio_convert(path):
        return True
    return _to_m4a(path, play_cache_path(path), duration_sec=duration_sec)


def list_folien(kind: str, item_id: str, root: Optional[str] = None) -> list:
    seen = set()
    names = []

    def _add(name: str) -> None:
        lower = name.lower()
        if lower in seen or lower.endswith(".part"):
            return
        if not SAFE_FILE.match(name):
            return
        seen.add(lower)
        names.append(name)

    folder = os.path.join(item_folder(kind, item_id, root), "folien")
    if os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                _add(name)
    item_dir = item_folder(kind, item_id, root)
    if os.path.isdir(item_dir):
        for name in sorted(os.listdir(item_dir)):
            path = os.path.join(item_dir, name)
            if not os.path.isfile(path) or os.path.getsize(path) <= 0:
                continue
            lower = name.lower()
            if lower.startswith("audio.") or lower == PLAY_CACHE_NAME:
                continue
            if lower.endswith(DOC_EXT):
                _add(name)
    return names


def folie_path(kind: str, item_id: str, name: str, root: Optional[str] = None) -> str:
    safe_id = sanitize_id(item_id)
    safe_name = sanitize_filename(name)
    item_dir = os.path.abspath(item_folder(kind, safe_id, root))
    candidates = [
        os.path.join(item_dir, "folien", safe_name),
        os.path.join(item_dir, safe_name),
    ]
    for path in candidates:
        full = os.path.abspath(path)
        if not full.startswith(item_dir + os.sep):
            continue
        if os.path.isfile(full):
            return full
    raise FileNotFoundError("Folie nicht gefunden.")


def folie_media(path: str) -> str:
    ext = os.path.splitext(path or "")[1].lower()
    return DOC_MEDIA.get(ext, "application/octet-stream")


def open_local_file(path: str) -> None:
    if not os.path.isfile(path):
        raise FileNotFoundError("Datei nicht gefunden.")
    if sys.platform == "darwin":
        subprocess.Popen(["/usr/bin/open", path], close_fds=True)
    elif sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", path], close_fds=True)


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


def delete_item(kind: str, item_id: str, root: Optional[str] = None) -> dict:
    """Lokalen Stammtisch komplett entfernen (JSON, Meta, Audio, Folien)."""
    if kind not in KINDS:
        raise ValueError("Unbekannte Quelle.")
    safe = sanitize_id(item_id)
    base = root or va_root()
    folder = item_folder(kind, safe, base)
    paths = [
        item_json_path(kind, safe, base),
        item_meta_path(kind, safe, base),
    ]
    removed = []
    if os.path.isdir(folder):
        shutil.rmtree(folder)
        removed.append("folder")
    for path in paths:
        if os.path.isfile(path):
            os.remove(path)
            removed.append(os.path.basename(path))
    if not removed:
        raise FileNotFoundError("Stammtisch %s ist lokal nicht vorhanden." % safe)
    return {"ok": True, "id": safe, "kind": kind, "removed": removed}


def delete_items(items: list, root: Optional[str] = None) -> dict:
    deleted = []
    errors = []
    for raw in items or []:
        if not isinstance(raw, dict):
            errors.append({"error": "Ungültiger Eintrag."})
            continue
        kind = str(raw.get("kind") or "")
        item_id = str(raw.get("id") or "")
        try:
            delete_item(kind, item_id, root)
            deleted.append({"kind": kind, "id": item_id})
        except FileNotFoundError:
            deleted.append({"kind": kind, "id": item_id, "missing": True})
        except ValueError as exc:
            errors.append({"kind": kind, "id": item_id, "error": str(exc)})
    return {"ok": not errors, "deleted": deleted, "errors": errors}


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
            audio_ok = find_audio(kind, item_id, base) is not None
            items.append(
                {
                    "id": item_id,
                    "kind": kind,
                    "datum": meta.get("datum") or "",
                    "titel": meta.get("titel") or item_id,
                    "ort": meta.get("ort") or "",
                    "dauer_sek": meta.get("dauer_sek"),
                    "has_audio": audio_ok,
                    "has_folien": bool(list_folien(kind, item_id, base)),
                    "stand": "lokal",
                    "need": False,
                }
            )
    items.sort(key=lambda x: (x.get("datum") or "", x.get("id") or ""), reverse=True)
    return items


def item_for_ui(
    kind: str,
    item_id: str,
    root: Optional[str] = None,
    include_transcript: bool = False,
) -> dict:
    row = load_local_json(kind, item_id, root)
    if not row:
        raise FileNotFoundError("Stammtisch %s ist lokal nicht vorhanden." % item_id)
    out = strip_huge(row, for_ui=True)
    out["kind"] = kind
    audio_path = find_audio(kind, item_id, root)
    out["has_audio"] = audio_path is not None
    out["audio_ready"] = bool(audio_path) and not needs_audio_convert(audio_path)
    out["folien_dateien"] = list_folien(kind, item_id, root)
    transcript = out.get("transkript") if isinstance(out.get("transkript"), list) else []
    out["transkript_n"] = len(transcript)
    if not include_transcript:
        out["transkript"] = []
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
