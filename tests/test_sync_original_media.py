"""Offline fixtures for pinned original-media synchronization."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlsplit

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sync_original_media", PACKAGE_ROOT / "scripts/sync_original_media.py")
sync_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync_module)

EXPECTED_FILES = (
    "kaunertal-trailer-v6.mp4",
    "kaunertal-trailer-poster.jpg",
    "web_audio/neoncholy_1up_2_few.mp3",
    "web_audio/neoncholy_legacy_of_crosbo.mp3",
    "web_audio/neoncholy_sunday_glide.mp3",
    "web_audio/neoncholy_punch_up.mp3",
)
EXPECTED_REPOSITORY = "7y8yfpf64t-oss/kaunertal-medien"
EXPECTED_ORIGIN = "https://kaunertal.cc"


class SyncOriginalMediaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="media-sync-fixture-", dir=PACKAGE_ROOT.parent)
        self.parent = Path(self.temporary.name)
        self.root = self.parent / "repository"
        self.root.mkdir()
        self.payloads = {name: ("Complete original media fixture: " + name).encode() for name in EXPECTED_FILES}
        self.manifest = {
            "schema": 1,
            "repository": EXPECTED_REPOSITORY,
            "source_origin": EXPECTED_ORIGIN,
            "files": {name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                      for name, data in self.payloads.items()},
        }
        self.calls = []
        self.write_manifest()

    def tearDown(self):
        self.temporary.cleanup()

    def write_manifest(self):
        (self.root / "media-sync.json").write_text(json.dumps(self.manifest))

    def write_target(self, name, payload):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    def fetch(self, url, destination, expected):
        parsed = urlsplit(url)
        self.assertEqual(parsed.scheme + "://" + parsed.netloc, EXPECTED_ORIGIN)
        name = parsed.path.removeprefix("/")
        self.assertIn(name, self.payloads)
        self.assertEqual(expected, self.manifest["files"][name])
        self.assertNotEqual(destination, self.root / name, "Downloads must not write directly over live targets")
        self.calls.append((name, destination))
        destination.write_bytes(self.payloads[name])

    def target_contents(self):
        return {name: (self.root / name).read_bytes() for name in EXPECTED_FILES if (self.root / name).is_file()}

    def test_repository_origin_and_exact_six_file_contract(self):
        self.assertEqual(sync_module.REPOSITORY, EXPECTED_REPOSITORY)
        self.assertEqual(sync_module.SOURCE_ORIGIN, EXPECTED_ORIGIN)
        self.assertEqual(set(sync_module.FILES), set(EXPECTED_FILES))
        self.assertEqual(len(sync_module.FILES), 6)

    def test_all_six_originals_are_installed_and_valid_existing_targets_are_skipped(self):
        result = sync_module.sync(self.root, fetch=self.fetch)
        self.assertEqual(set(result["changed"]), set(EXPECTED_FILES))
        self.assertEqual(result["skipped"], [])
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(self.target_contents(), self.payloads)
        before = {name: (self.root / name).stat().st_mtime_ns for name in EXPECTED_FILES}
        self.calls.clear()
        again = sync_module.sync(self.root, fetch=self.fetch)
        self.assertEqual(again["changed"], [])
        self.assertEqual(set(again["skipped"]), set(EXPECTED_FILES))
        self.assertEqual(self.calls, [])
        self.assertEqual({name: (self.root / name).stat().st_mtime_ns for name in EXPECTED_FILES}, before)

    def test_only_missing_or_corrupt_targets_are_downloaded(self):
        for name in EXPECTED_FILES[:-2]:
            self.write_target(name, self.payloads[name])
        self.write_target(EXPECTED_FILES[-2], b"outdated recording")
        unrelated = self.root / "README.md"
        unrelated.write_text("Existing repository documentation")
        result = sync_module.sync(self.root, fetch=self.fetch)
        self.assertEqual(set(result["changed"]), set(EXPECTED_FILES[-2:]))
        self.assertEqual(set(result["skipped"]), set(EXPECTED_FILES[:-2]))
        self.assertEqual({name for name, _ in self.calls}, set(EXPECTED_FILES[-2:]))
        self.assertEqual(self.target_contents(), self.payloads)
        self.assertEqual(unrelated.read_text(), "Existing repository documentation")

    def test_last_corrupt_download_preserves_every_existing_target(self):
        for name in EXPECTED_FILES:
            self.write_target(name, ("previous version " + name).encode())
        before = self.target_contents()
        def corrupt_last(url, destination, expected):
            self.fetch(url, destination, expected)
            if len(self.calls) == len(EXPECTED_FILES):
                payload = destination.read_bytes()
                destination.write_bytes(b"X" + payload[1:])
        with self.assertRaises(ValueError):
            sync_module.sync(self.root, fetch=corrupt_last)
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(self.target_contents(), before)

    def test_injected_fetch_cannot_bypass_size_or_sha_verification(self):
        for damage in (b"truncated", None):
            with self.subTest(damage=damage):
                self.calls.clear()
                def unverified(url, destination, expected):
                    self.fetch(url, destination, expected)
                    if damage is None:
                        data = destination.read_bytes()
                        destination.write_bytes(b"X" + data[1:])
                    else:
                        destination.write_bytes(damage)
                with self.assertRaises(ValueError):
                    sync_module.sync(self.root, fetch=unverified)
                self.assertEqual(self.target_contents(), {})

    def test_wrong_repository_origin_or_schema_is_rejected_without_fetch(self):
        variants = [
            ("repository", "another-owner/kaunertal-medien"),
            ("source_origin", "http://kaunertal.cc"),
            ("source_origin", "https://different.example"),
            ("source_origin", "https://kaunertal.cc/other"),
            ("source_origin", "https://kaunertal.cc?token=fixture"),
            ("schema", 2),
        ]
        original = json.loads(json.dumps(self.manifest))
        for key, value in variants:
            with self.subTest(key=key, value=value):
                self.manifest = json.loads(json.dumps(original))
                self.manifest[key] = value
                self.write_manifest()
                with self.assertRaises(ValueError):
                    sync_module.sync(self.root, fetch=self.fetch)
                self.assertEqual(self.calls, [])
                self.assertEqual(self.target_contents(), {})

    def test_missing_extra_and_traversal_inventory_are_rejected_without_fetch(self):
        original = json.loads(json.dumps(self.manifest))
        for kind in ("missing", "extra", "../escape.mp3", "/absolute.mp3", "web_audio\\escape.mp3"):
            with self.subTest(kind=kind):
                self.manifest = json.loads(json.dumps(original))
                if kind == "missing":
                    del self.manifest["files"][EXPECTED_FILES[0]]
                else:
                    self.manifest["files"]["extra.mp3" if kind == "extra" else kind] = {"size": 1, "sha256": "0" * 64}
                self.write_manifest()
                with self.assertRaises(ValueError):
                    sync_module.sync(self.root, fetch=self.fetch)
                self.assertEqual(self.calls, [])
                self.assertEqual(self.target_contents(), {})
                self.assertFalse((self.parent / "escape.mp3").exists())

    def test_invalid_size_and_hash_pins_are_rejected_without_fetch(self):
        original = json.loads(json.dumps(self.manifest))
        for key, value in (("size", -1), ("size", True), ("size", "12"), ("sha256", "wrong"), ("sha256", "G" * 64)):
            with self.subTest(key=key, value=value):
                self.manifest = json.loads(json.dumps(original))
                self.manifest["files"][EXPECTED_FILES[0]][key] = value
                self.write_manifest()
                with self.assertRaises(ValueError):
                    sync_module.sync(self.root, fetch=self.fetch)
                self.assertEqual(self.calls, [])
                self.assertEqual(self.target_contents(), {})

    def test_symlink_target_is_rejected_without_touching_link_destination(self):
        outside = self.parent / "personal.bin"
        outside.write_bytes(b"outside original")
        target = self.root / EXPECTED_FILES[0]
        target.symlink_to(outside)
        with self.assertRaises(ValueError):
            sync_module.sync(self.root, fetch=self.fetch)
        self.assertTrue(target.is_symlink())
        self.assertEqual(outside.read_bytes(), b"outside original")
        self.assertEqual(self.calls, [])

    def test_symlink_parent_is_rejected_without_touching_outside_directory(self):
        outside = self.parent / "personal-audio"
        outside.mkdir()
        sentinel = outside / "keep.txt"
        sentinel.write_bytes(b"keep")
        (self.root / "web_audio").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            sync_module.sync(self.root, fetch=self.fetch)
        self.assertEqual(set(p.name for p in outside.iterdir()), {"keep.txt"})
        self.assertEqual(sentinel.read_bytes(), b"keep")
        self.assertEqual(self.calls, [])

    def test_dangling_symlink_target_is_rejected(self):
        outside = self.parent / "must-not-be-created.bin"
        (self.root / EXPECTED_FILES[0]).symlink_to(outside)
        with self.assertRaises(ValueError):
            sync_module.sync(self.root, fetch=self.fetch)
        self.assertFalse(outside.exists())
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
