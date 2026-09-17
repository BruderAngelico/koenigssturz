# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

import kingfall_va_store as va
import kingfall_va_pack as pack


def _item(root, kind, item_id, titel, datum, text="hallo", audio=True):
    os.makedirs(va.kind_dir(kind, root), exist_ok=True)
    row = {"id": item_id, "titel": titel, "datum": datum, "transkript": [{"t": 0, "sp": 0, "text": text}]}
    va.save_row(kind, row, root, audio)
    if audio:
        folder = va.item_folder(kind, item_id, root)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "audio.aac"), "wb") as handle:
            handle.write(b"\xff\xf1" + text.encode("utf-8") + b"\x00" * 20)


class PackMergeTests(unittest.TestCase):
    def test_date_and_kind_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            _item(tmp, "public", "a1", "Eins", "2026-01-01")
            _item(tmp, "intern", "b1", "Zwei", "2026-03-01")
            _item(tmp, "public", "c1", "Drei", "2026-06-01")
            got = pack.filter_local(tmp, kinds=["public"], date_from="2026-02-01", date_to="2026-12-31")
            ids = [x["id"] for x in got]
            self.assertEqual(ids, ["c1"])

    def test_skip_identical_on_overlap(self):
        with tempfile.TemporaryDirectory() as src:
            with tempfile.TemporaryDirectory() as dest:
                _item(src, "public", "a1", "Eins", "2026-01-01", "gleich")
                _item(dest, "public", "a1", "Eins", "2026-01-01", "gleich")
                zpath = os.path.join(src, "p.zip")
                pack.build_zip(zpath, root=src, kinds=["public"])
                result = pack.apply_import(zpath, dest_root=dest, on_conflict="abort")
                self.assertTrue(result["ok"])
                self.assertEqual(result["skipped"], 1)
                self.assertEqual(result["added"], 0)

    def test_conflict_abort_changes_nothing(self):
        with tempfile.TemporaryDirectory() as src:
            with tempfile.TemporaryDirectory() as dest:
                _item(src, "public", "a1", "Neu", "2026-01-01", "neu")
                _item(dest, "public", "a1", "Alt", "2026-01-01", "alt")
                zpath = os.path.join(src, "p.zip")
                pack.build_zip(zpath, root=src, kinds=["public"])
                result = pack.apply_import(zpath, dest_root=dest, on_conflict="abort")
                self.assertTrue(result.get("aborted"))
                row = va.load_local_json("public", "a1", dest)
                self.assertEqual(row["titel"], "Alt")

    def test_conflict_keeps_both_as_copy(self):
        with tempfile.TemporaryDirectory() as src:
            with tempfile.TemporaryDirectory() as dest:
                _item(src, "public", "a1", "Neu", "2026-01-01", "neu")
                _item(dest, "public", "a1", "Alt", "2026-01-01", "alt")
                zpath = os.path.join(src, "p.zip")
                pack.build_zip(zpath, root=src, kinds=["public"])
                result = pack.apply_import(zpath, dest_root=dest, on_conflict="copy")
                self.assertTrue(result["ok"])
                self.assertEqual(result["copies"], 1)
                names = [n for n in os.listdir(va.kind_dir("public", dest)) if n.endswith(".json") and not n.endswith(".meta.json")]
                self.assertEqual(len(names), 2)
                listed = va.list_local(dest)
                hints = [x for x in listed if x.get("hinweis")]
                self.assertEqual(len(hints), 1)

    def test_fill_missing_audio_not_conflict(self):
        with tempfile.TemporaryDirectory() as src:
            with tempfile.TemporaryDirectory() as dest:
                _item(src, "public", "a1", "Eins", "2026-01-01", "gleich", audio=True)
                _item(dest, "public", "a1", "Eins", "2026-01-01", "gleich", audio=False)
                zpath = os.path.join(src, "p.zip")
                pack.build_zip(zpath, root=src, kinds=["public"])
                result = pack.apply_import(zpath, dest_root=dest, on_conflict="abort")
                self.assertTrue(result["ok"])
                self.assertEqual(result["updated_audio"], 1)
                self.assertTrue(va.find_audio("public", "a1", dest))


if __name__ == "__main__":
    unittest.main()
