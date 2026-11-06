"""Offline contract tests: only the requested successful Pages commit is verified."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT / "scripts"))
import request_pages_build as pages

COMMIT = "a" * 40
OLD_COMMIT = "b" * 40


class RequestPagesBuildTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.waits = []
        self.verifications = 0
        self.settings = {"source": {"branch": "main", "path": "/"},
                         "html_url": pages.PAGES_ORIGIN + "/", "build_type": "legacy"}
        self.ref = {"object": {"sha": COMMIT}}
        self.submission = {"status": "queued", "url": pages.API_ROOT + "/pages/builds/latest"}
        self.polls = [{"status": "built", "commit": COMMIT, "error": {"message": None}}]

    def api(self, method, path):
        self.calls.append((method, path))
        if (method, path) == ("GET", "/pages"):
            return deepcopy(self.settings)
        if (method, path) == ("GET", "/git/ref/heads/main"):
            return deepcopy(self.ref)
        if (method, path) == ("POST", "/pages/builds"):
            return deepcopy(self.submission)
        if (method, path) == ("GET", "/pages/builds/latest"):
            if not self.polls:
                self.fail("Implementation polled past the configured attempt budget")
            return deepcopy(self.polls.pop(0))
        self.fail("Unexpected API request: " + repr((method, path)))

    def verify_public(self):
        self.assertEqual(self.calls[-1], ("GET", "/pages/builds/latest"))
        self.verifications += 1
        return {"verified_files": 6}

    def publish(self, attempts=3):
        return pages.publish(COMMIT, api=self.api, verify_public=self.verify_public,
                             sleep=self.waits.append, attempts=attempts)

    def assert_not_submitted(self):
        self.assertNotIn(("POST", "/pages/builds"), self.calls)
        self.assertEqual(self.verifications, 0)

    def test_matching_success_checks_all_six_public_files(self):
        result = self.publish()
        self.assertEqual(result["commit"], COMMIT)
        self.assertEqual(result["status"], "built")
        self.assertEqual(result["verified_files"], 6)
        self.assertEqual(self.verifications, 1)
        self.assertEqual(self.calls, [("GET", "/pages"), ("GET", "/git/ref/heads/main"),
                                     ("POST", "/pages/builds"), ("GET", "/pages/builds/latest")])

    def test_missing_optional_build_type_and_url_without_trailing_slash_are_accepted(self):
        self.settings.pop("build_type")
        self.settings["html_url"] = pages.PAGES_ORIGIN.rstrip("/")
        self.assertEqual(self.publish()["status"], "built")
        self.assertEqual(self.verifications, 1)

    def test_wrong_pages_configuration_aborts_before_post(self):
        original = deepcopy(self.settings)
        variants = [
            {"source": {"branch": "gh-pages", "path": "/"}},
            {"source": {"branch": "main", "path": "/docs"}},
            {"source": None},
            {"html_url": "https://other.github.io/kaunertal-medien/"},
            {"html_url": pages.PAGES_ORIGIN + "/other/"},
            {"build_type": "workflow"},
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                self.settings = {**deepcopy(original), **variant}
                self.calls.clear()
                with self.assertRaises((ValueError, RuntimeError)):
                    self.publish()
                self.assert_not_submitted()

    def test_changed_main_branch_sha_aborts_before_post(self):
        self.ref = {"object": {"sha": OLD_COMMIT}}
        with self.assertRaises((ValueError, RuntimeError)):
            self.publish()
        self.assert_not_submitted()

    def test_old_completed_build_is_not_success_for_requested_commit(self):
        self.polls = [{"status": "built", "commit": OLD_COMMIT},
                      {"status": "building", "commit": COMMIT},
                      {"status": "built", "commit": COMMIT}]
        self.assertEqual(self.publish(attempts=3)["commit"], COMMIT)
        self.assertEqual(self.calls.count(("GET", "/pages/builds/latest")), 3)
        self.assertEqual(len(self.waits), 2)
        self.assertEqual(self.verifications, 1)

    def test_queued_and_building_are_polled_before_success(self):
        self.polls = [{"status": "queued", "commit": COMMIT},
                      {"status": "building", "commit": COMMIT},
                      {"status": "built", "commit": COMMIT}]
        self.assertEqual(self.publish(attempts=3)["status"], "built")
        self.assertEqual(len(self.waits), 2)
        self.assertEqual(self.verifications, 1)

    def test_matching_error_status_or_error_message_prevents_public_verification(self):
        variants = [
            {"status": "errored", "commit": COMMIT, "error": {"message": "Build failed"}},
            {"status": "errored", "commit": COMMIT},
            {"status": "built", "commit": COMMIT, "error": {"message": "Reported failure despite status"}},
            {"status": "building", "commit": COMMIT, "error": {"message": "Build failed"}},
        ]
        for value in variants:
            with self.subTest(value=value):
                self.polls = [value]
                self.calls.clear()
                with self.assertRaises(RuntimeError):
                    self.publish()
                self.assertEqual(self.verifications, 0)

    def test_unexpected_poll_status_prevents_public_verification(self):
        self.polls = [{"status": "cancelled", "commit": COMMIT}]
        with self.assertRaises(RuntimeError):
            self.publish()
        self.assertEqual(self.verifications, 0)

    def test_timeout_for_pending_or_unrelated_commit_never_verifies_public_files(self):
        for value in ({"status": "building", "commit": COMMIT},
                      {"status": "built", "commit": OLD_COMMIT}):
            with self.subTest(value=value):
                self.calls.clear()
                self.waits.clear()
                self.polls = [deepcopy(value), deepcopy(value)]
                with self.assertRaises(RuntimeError):
                    self.publish(attempts=2)
                self.assertEqual(self.calls.count(("GET", "/pages/builds/latest")), 2)
                self.assertEqual(self.verifications, 0)

    def test_unsafe_submission_url_is_not_followed(self):
        for url in ("https://other.example/credential-capture", "/pages/builds/latest",
                    pages.API_ROOT + "/pages/builds/latest?redirect=other",
                    pages.API_ROOT + "/pages/builds/123"):
            with self.subTest(url=url):
                self.submission["url"] = url
                self.calls.clear()
                with self.assertRaises(RuntimeError):
                    self.publish()
                self.assertEqual(self.verifications, 0)
                self.assertNotIn(("GET", "/pages/builds/latest"), self.calls)

    def test_invalid_submission_status_is_rejected_without_polling(self):
        self.submission["status"] = "failed"
        with self.assertRaises(RuntimeError):
            self.publish()
        self.assertNotIn(("GET", "/pages/builds/latest"), self.calls)
        self.assertEqual(self.verifications, 0)

    def test_submission_building_or_built_still_requires_matching_latest_commit(self):
        for status in ("building", "built"):
            with self.subTest(status=status):
                self.submission["status"] = status
                self.calls.clear()
                self.polls = [{"status": "built", "commit": COMMIT}]
                result = self.publish()
                self.assertEqual(result["commit"], COMMIT)
                self.assertIn(("GET", "/pages/builds/latest"), self.calls)

    def test_fewer_than_six_verified_public_files_is_not_success(self):
        with self.assertRaises(RuntimeError):
            pages.publish(COMMIT, api=self.api, verify_public=lambda: {"verified_files": 5},
                          sleep=self.waits.append, attempts=3)


if __name__ == "__main__":
    unittest.main()
