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


if __name__ == "__main__":
    unittest.main()
