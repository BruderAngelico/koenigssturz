# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

import kingfall_vereinsarchiv as va


def _jwt(payload_b64: str) -> str:
    return "eyJhbGciOiJub25lIn0." + payload_b64 + ".x"


class ResolveBaseTests(unittest.TestCase):
    def test_supabase_url(self):
        url = "https://abcd.supabase.co/rest/v1/stammtische?select=id"
        self.assertEqual(va.resolve_base(url, {}), "https://abcd.supabase.co")

    def test_pages_dev_uses_jwt_iss(self):
        # {"iss":"https://abcd.supabase.co/auth/v1","ref":"abcd"} without padding issues
        import base64
        import json

        payload = base64.urlsafe_b64encode(
            json.dumps({"iss": "https://abcd.supabase.co/auth/v1", "ref": "abcd"}).encode()
        ).decode().rstrip("=")
        headers = {"Authorization": "Bearer " + _jwt(payload)}
        base = va.resolve_base("https://vereinsarchiv-vorstand.pages.dev/api/x", headers)
        self.assertEqual(base, "https://abcd.supabase.co")

    def test_pages_dev_without_jwt_fails(self):
        with self.assertRaises(ValueError):
            va.resolve_base("https://vereinsarchiv-vorstand.pages.dev/", {})


class SignedUrlTests(unittest.TestCase):
    def test_object_relative(self):
        self.assertEqual(
            va.absolute_signed_url("https://x.supabase.co", "/object/sign/audio/a.aac?token=1"),
            "https://x.supabase.co/storage/v1/object/sign/audio/a.aac?token=1",
        )

    def test_storage_prefixed(self):
        self.assertEqual(
            va.absolute_signed_url(
                "https://x.supabase.co", "/storage/v1/object/sign/audio-intern/a.aac?token=1"
            ),
            "https://x.supabase.co/storage/v1/object/sign/audio-intern/a.aac?token=1",
        )

    def test_absolute(self):
        url = "https://x.supabase.co/storage/v1/object/sign/a?token=1"
        self.assertEqual(va.absolute_signed_url("https://x.supabase.co", url), url)


class StoragePathTests(unittest.TestCase):
    def test_strips_bucket_prefix(self):
        parts = va.split_storage_path("audio-intern/ordner/a.aac", ["audio", "audio-intern"])
        self.assertIn(("audio-intern", "ordner/a.aac"), parts)

    def test_plain_path(self):
        parts = va.split_storage_path("ordner/a.aac", ["audio"])
        self.assertEqual(parts[-1], (None, "ordner/a.aac"))


class UiNormalizeTests(unittest.TestCase):
    def test_transcript_alt_keys(self):
        row = va.normalize_item_for_ui(
            {
                "sprecher": ["Anna"],
                "transkript": [{"start": "1:02", "speaker": "Anna", "text": "Hallo"}],
                "zusammenfassung": "Kurz.",
            }
        )
        self.assertEqual(row["transkript"][0]["t"], 62.0)
        self.assertEqual(row["transkript"][0]["sp"], 0)
        self.assertEqual(row["zusammenfassung"]["lead"], "Kurz.")

    def test_transcript_string(self):
        row = va.normalize_item_for_ui({"transkript": "Nur Text"})
        self.assertEqual(row["transkript"][0]["text"], "Nur Text")


class LocalCompleteTests(unittest.TestCase):
    def test_needs_detail_without_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(va.kind_dir("intern", tmp))
            path = va.item_json_path("intern", "abc-1", tmp)
            va._atomic_json(path, {"id": "abc-1", "titel": "x"})
            self.assertTrue(va.needs_detail_fetch("intern", "abc-1", tmp))
            va.save_row("intern", {"id": "abc-1", "titel": "x", "transkript": []}, tmp, False)
            self.assertFalse(va.needs_detail_fetch("intern", "abc-1", tmp))


class AudioSniffTests(unittest.TestCase):
    def _write(self, folder, name, data):
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_m4a_named_aac_is_mp4(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "clip.aac", b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 8)
            self.assertEqual(va.sniff_audio_kind(path), "mp4")
            self.assertEqual(va.audio_media_type(path), "audio/mp4")
            play, mime = va.playback_audio(path)
            self.assertEqual(play, path)
            self.assertEqual(mime, "audio/mp4")

    def test_json_named_aac_is_not_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = va.item_folder("public", "id1", tmp)
            os.makedirs(folder)
            self._write(folder, "talk.aac", b'{"statusCode":400,"message":"error"}\n')
            self.assertIsNone(va.sniff_audio_kind(os.path.join(folder, "talk.aac")))
            self.assertIsNone(va.find_audio("public", "id1", tmp))

    def test_adts_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "raw.aac", bytes([0xFF, 0xF1, 0x50, 0x80, 0x01, 0x3F, 0xFC]) + b"\x00" * 16)
            self.assertEqual(va.sniff_audio_kind(path), "adts")
            self.assertEqual(va.audio_media_type(path), "audio/aac")
