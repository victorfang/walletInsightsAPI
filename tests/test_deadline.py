from concurrent.futures import ThreadPoolExecutor
import threading
import time
import unittest
from unittest.mock import patch

from eth_wallet_check.deadline import BoundedExecutor, REQUEST_BUDGET_SECONDS, shared_executor
from eth_wallet_check.http import JSONHTTP, SourceError


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, _limit):
        return b'{"ok": true}'


class BoundedExecutorTests(unittest.TestCase):
    def test_saturation_rejects_immediately_and_does_not_queue(self):
        pool = BoundedExecutor(max_workers=1)
        running = threading.Event()
        release = threading.Event()
        queued_called = threading.Event()

        def blocked():
            running.set()
            release.wait(2)

        try:
            first = pool.submit(blocked)
            self.assertTrue(running.wait(1))
            started = time.monotonic()
            self.assertIsNone(pool.submit(queued_called.set))
            self.assertLess(time.monotonic() - started, 0.1)
            release.set()
            first.result(timeout=1)
            self.assertFalse(queued_called.is_set())
            self.assertEqual(pool.submit(lambda: 7).result(timeout=1), 7)
        finally:
            release.set()
            pool.shutdown(wait=True, cancel_futures=True)

    def test_failure_releases_capacity(self):
        pool = BoundedExecutor(max_workers=1)
        try:
            def fail():
                raise ValueError("failed")

            with self.assertRaises(ValueError):
                pool.submit(fail).result(timeout=1)
            self.assertEqual(pool.submit(lambda: 8).result(timeout=1), 8)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def test_pending_work_is_bounded_and_cancelled_slots_are_released(self):
        pool = BoundedExecutor(max_workers=1, max_pending=1)
        running = threading.Event()
        release = threading.Event()

        def blocked():
            running.set()
            release.wait(2)

        try:
            first = pool.submit(blocked)
            self.assertTrue(running.wait(1))
            pending = pool.submit(lambda: 1)
            self.assertIsNone(pool.submit(lambda: 2))
            self.assertTrue(pending.cancel())
            replacement = pool.submit(lambda: 3)
            self.assertIsNotNone(replacement)
            release.set()
            first.result(timeout=1)
            self.assertEqual(replacement.result(timeout=1), 3)
        finally:
            release.set()
            pool.shutdown(wait=True, cancel_futures=True)

    def test_submit_failure_releases_slot(self):
        pool = BoundedExecutor(max_workers=1)
        pool.shutdown(wait=False, cancel_futures=True)
        # Both calls reach ThreadPoolExecutor and reject shutdown, rather than
        # the second looking saturated because the first leaked its slot.
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                pool.submit(lambda: None)

    def test_shared_pool_is_process_wide(self):
        self.assertIs(shared_executor(), shared_executor())
        self.assertLess(REQUEST_BUDGET_SECONDS, 3)


class HTTPDeadlineTests(unittest.TestCase):
    def test_expired_deadline_does_not_open_network(self):
        http = JSONHTTP(timeout=5)
        with patch("eth_wallet_check.http.time.monotonic", return_value=20), \
                patch("eth_wallet_check.http.urlopen") as opening:
            with http.deadline_at(19):
                with self.assertRaisesRegex(SourceError, "Request deadline exceeded"):
                    http("https://example.test")
            opening.assert_not_called()

    def test_deadline_clamps_timeout_and_restores_nested_context(self):
        http = JSONHTTP(timeout=5)
        with patch("eth_wallet_check.http.time.monotonic", return_value=20), \
                patch("eth_wallet_check.http.urlopen", return_value=Response()) as opening:
            with http.deadline_at(22):
                http("https://example.test")
                with http.deadline_at(21):
                    http("https://example.test")
                http("https://example.test")
            http("https://example.test")
        self.assertEqual([call.kwargs["timeout"] for call in opening.call_args_list],
                         [2, 1, 2, 5])

    def test_deadline_never_extends_configured_timeout(self):
        http = JSONHTTP(timeout=0.5)
        with patch("eth_wallet_check.http.time.monotonic", return_value=20), \
                patch("eth_wallet_check.http.urlopen", return_value=Response()) as opening:
            with http.deadline_at(22):
                http("https://example.test")
        self.assertEqual(opening.call_args.kwargs["timeout"], 0.5)

    def test_worker_context_is_restored_after_exception(self):
        http = JSONHTTP(timeout=5)

        def failed_job():
            with http.deadline_at(19):
                raise ValueError("failed")

        with ThreadPoolExecutor(max_workers=1) as pool, \
                patch("eth_wallet_check.http.time.monotonic", return_value=20), \
                patch("eth_wallet_check.http.urlopen", return_value=Response()) as opening:
            with self.assertRaises(ValueError):
                pool.submit(failed_job).result(timeout=1)
            self.assertEqual(pool.submit(http, "https://example.test").result(timeout=1),
                             {"ok": True})
            self.assertEqual(opening.call_args.kwargs["timeout"], 5)

    def test_deadline_context_is_local_to_each_thread(self):
        http = JSONHTTP(timeout=5)
        with patch("eth_wallet_check.http.time.monotonic", return_value=20), \
                patch("eth_wallet_check.http.urlopen", return_value=Response()) as opening:
            with http.deadline_at(19):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(http, "https://example.test").result(timeout=1)
            self.assertEqual(opening.call_args.kwargs["timeout"], 5)


if __name__ == "__main__":
    unittest.main()
