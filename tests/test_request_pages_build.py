"""Offline contract tests: only the requested successful Pages commit is verified."""
from copy import deepcopy
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

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

    def test_own_numeric_submission_url_is_accepted_but_never_followed(self):
        self.submission["url"] = pages.API_ROOT + "/pages/builds/123"
        result = self.publish()
        self.assertEqual(result["commit"], COMMIT)
        self.assertEqual(result["status"], "built")
        self.assertEqual(self.verifications, 1)
        self.assertEqual(self.calls, [("GET", "/pages"), ("GET", "/git/ref/heads/main"),
                                     ("POST", "/pages/builds"), ("GET", "/pages/builds/latest")])

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
                    pages.API_ROOT + "/pages/builds/123?redirect=other",
                    pages.API_ROOT + "/pages/builds/123#fragment",
                    pages.API_ROOT + "/pages/builds/123/other",
                    pages.API_ROOT + "/pages/builds/%31%32%33",
                    pages.API_ROOT + "/pages/builds/١٢٣",
                    pages.API_ROOT + "/pages/builds/１２３",
                    "https://api.github.com/repos/another-owner/kaunertal-medien/pages/builds/123"):
            with self.subTest(url=url):
                self.submission["url"] = url
                self.calls.clear()
                self.polls = [{"status": "built", "commit": COMMIT}]
                self.assertEqual(self.publish()["commit"], COMMIT)
                self.assertEqual(self.calls, [("GET", "/pages"), ("GET", "/git/ref/heads/main"),
                                             ("POST", "/pages/builds"), ("GET", "/pages/builds/latest")])

    def test_post_status_is_diagnostic_and_never_proves_build_success(self):
        for status in ("failed", "unknown", None):
            with self.subTest(status=status):
                self.submission["status"] = status
                self.submission["url"] = pages.API_ROOT + "/pages/builds/123"
                self.calls.clear()
                self.polls = [{"status": "built", "commit": OLD_COMMIT}]
                with self.assertRaises(RuntimeError):
                    self.publish(attempts=1)
                self.assertIn(("GET", "/pages/builds/latest"), self.calls)
                self.assertEqual(self.verifications, 0)

    def test_empty_or_variant_post_body_does_not_replace_fixed_commit_verification(self):
        for value in (None, {}, {"status": None, "url": None}):
            with self.subTest(value=value):
                self.submission = value
                self.calls.clear()
                self.polls = [{"status": "built", "commit": COMMIT}]
                self.assertEqual(self.publish()["commit"], COMMIT)
                self.assertIn(("GET", "/pages/builds/latest"), self.calls)

    def test_post_api_failure_prevents_polling_and_public_verification(self):
        def failing_api(method, path):
            if method == "POST":
                raise RuntimeError("HTTP 403")
            return self.api(method, path)
        with self.assertRaises(RuntimeError):
            pages.publish(COMMIT, api=failing_api, verify_public=self.verify_public,
                          sleep=self.waits.append, attempts=1)
        self.assertNotIn(("GET", "/pages/builds/latest"), self.calls)
        self.assertEqual(self.verifications, 0)

    def test_concrete_api_requires_http_201_for_post(self):
        for status in (200, 201, 202):
            with self.subTest(status=status):
                response = io.BytesIO(b'{"status":null}')
                response.status = status
                opener = MagicMock()
                opener.open.return_value = response
                with patch.dict(pages.os.environ, {"GH_TOKEN": "offline-fixture"}), \
                        patch.object(pages, "build_opener", return_value=opener):
                    if status == 201:
                        self.assertEqual(pages.api_request("POST", "/pages/builds"), {"status": None})
                    else:
                        with self.assertRaises(RuntimeError):
                            pages.api_request("POST", "/pages/builds")

    def test_confirmation_diagnostic_does_not_print_unrecognized_payload_or_query(self):
        payload = {"status": "queued", "url": pages.API_ROOT + "/pages/builds/latest?credential=PRIVATE",
                   "authorization": "PRIVATE"}
        self.assertNotIn("PRIVATE", str(pages.submission_summary(payload)))
        self.assertTrue(pages.submission_summary(payload)["url_has_query_or_fragment"])

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
