import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, mock_open, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import github  # noqa: E402
import updater  # noqa: E402

SNAPSHOT = {"from": "2026-11-01", "to": "2026-11-03", "total": 3, "counts": [0, 1, 2]}


class Clock:
    def __init__(self):
        self.ms = 0

    def ticks(self):
        return self.ms % (1 << 30)

    @staticmethod
    def diff(end, start):
        return (end - start + (1 << 29)) % (1 << 30) - (1 << 29)

    def advance(self, seconds):
        self.ms += seconds * 1000


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cache = str(Path(self.directory.name) / "contributions.json")
        self.clock = Clock()
        for target, value in (
            ("ticks_ms", self.clock.ticks),
            ("ticks_diff", self.clock.diff),
        ):
            context = patch.object(updater, target, value)
            context.start()
            self.addCleanup(context.stop)
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        self.fetch = Mock(return_value=(copy.deepcopy(SNAPSHOT), 0))

    def create(self, username="test-user"):
        return updater.Updater(self.fetch, username, days=3, cache_path=self.cache)

    def test_minute_cadence_and_unchanged_data_does_not_write(self):
        state = self.create()
        with patch.object(state, "_save", wraps=state._save) as save:
            self.assertTrue(state.step())
            self.clock.advance(59)
            self.assertFalse(state.step())
            self.clock.advance(1)
            self.assertTrue(state.step())
            self.assertEqual(self.fetch.call_count, 2)
            self.assertEqual(save.call_count, 1)
        restored = self.create()
        self.assertEqual(restored.snapshot, SNAPSHOT)
        self.assertTrue(restored.stale)
        self.assertIsNone(restored.last_success)

    def test_request_duration_is_included_in_minute_interval(self):

        def fetch():
            self.clock.advance(10)
            return (copy.deepcopy(SNAPSHOT), 0)

        self.fetch.side_effect = fetch
        state = self.create()
        state.step()
        self.clock.advance(49)
        self.assertFalse(state.step())
        self.clock.advance(1)
        self.assertTrue(state.step())

    def test_failure_retains_snapshot_staleness_and_success_recovers(self):
        state = self.create()
        state.step()
        self.fetch.side_effect = OSError("failure text must not be logged")
        for seconds in (60, 60, 120):
            self.clock.advance(seconds)
            state.step()
        self.clock.advance(60)
        self.assertTrue(state.stale)
        self.assertEqual(state.snapshot, SNAPSHOT)
        self.fetch.side_effect = None
        self.clock.advance(180)
        self.assertTrue(state.step())
        self.assertFalse(state.stale)
        self.clock.advance(60)
        self.assertTrue(state.step())

    def test_backoff_caps_at_fifteen_minutes(self):
        self.fetch.side_effect = OSError()
        state = self.create()
        for delay in (60, 120, 240, 480, 900, 900):
            state.step()
            calls = self.fetch.call_count
            self.clock.advance(delay - 1)
            state.step()
            self.assertEqual(self.fetch.call_count, calls)
            self.clock.advance(1)

    def test_server_delay_is_respected_on_failure_and_success(self):
        self.fetch.side_effect = github.RequestError("rate limited", 1800)
        state = self.create()
        state.step()
        self.clock.advance(1799)
        self.assertFalse(state.step())
        self.clock.advance(1)
        self.fetch.side_effect = None
        self.fetch.return_value = (copy.deepcopy(SNAPSHOT), 3600)
        self.assertTrue(state.step())
        self.clock.advance(3599)
        self.assertFalse(state.step())
        self.clock.advance(1)
        self.assertTrue(state.step())

    def test_clock_wrap_and_wall_clock_correction_do_not_break_schedule(self):
        self.clock.ms = (1 << 30) - 30000
        state = self.create()
        state.step()
        with patch.object(updater.time, "time", return_value=0):
            self.clock.advance(60)
            self.assertTrue(state.step())
        self.clock.advance(300)
        self.assertTrue(state.stale)
        self.fetch.side_effect = OSError()
        for _ in range(24 * 30):
            self.clock.advance(3600)
            state.step()
        self.assertTrue(state.stale)

    def test_cache_failure_keeps_old_file_and_retries_save(self):
        state = self.create()
        state.step()
        changed = copy.deepcopy(SNAPSHOT)
        changed["counts"][0] = 5
        changed["total"] += 5
        self.fetch.return_value = (changed, 0)
        self.clock.advance(60)
        with patch.object(updater.os, "rename", side_effect=OSError()):
            self.assertTrue(state.step())
        self.assertEqual(state.snapshot, changed)
        self.assertFalse(state.stale)
        self.assertEqual(self.create().snapshot, SNAPSHOT)
        self.assertFalse(Path(self.cache + ".tmp").exists())
        self.clock.advance(60)
        state.step()
        self.assertEqual(self.create().snapshot, changed)

    def test_bad_cache_and_other_user_cache_are_ignored(self):
        for content in (b"broken", b"[1,2]", b"x" * 8193):
            Path(self.cache).write_bytes(content)
            self.assertIsNone(self.create().snapshot)
        Path(self.cache).unlink()
        state = self.create()
        state.step()
        self.assertIsNone(self.create("other-user").snapshot)
        record = json.loads(Path(self.cache).read_text())
        record["snapshot"]["to"] = "2026-12-03"
        Path(self.cache).write_text(json.dumps(record))
        self.assertIsNone(self.create().snapshot)

    def test_invalid_response_preserves_previous_snapshot(self):
        state = self.create()
        state.step()
        bad = copy.deepcopy(SNAPSHOT)
        bad["counts"] = [True, 1, 2]
        self.fetch.return_value = (bad, 0)
        self.clock.advance(60)
        self.assertFalse(state.step())
        self.assertEqual(state.snapshot, SNAPSHOT)

    def test_raw_failure_text_is_not_logged(self):
        self.fetch.side_effect = ValueError("private-response-text")
        state = self.create()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            state.step()
        self.assertNotIn("private-response-text", output.getvalue())


class HTTPTests(unittest.TestCase):
    def body(self, raw):
        stream = io.BytesIO(raw)
        return github.read_body(stream, github.read_headers(stream))

    def test_body_framing(self):
        self.assertEqual(self.body(b"Content-Length: 2\r\n\r\n{}"), b"{}")
        self.assertEqual(self.body(b"Connection: close\r\n\r\n{}"), b"{}")
        self.assertEqual(
            self.body(
                b"Transfer-Encoding: chunked\r\n\r\n1;ext=x\r\n{\r\n1\r\n}\r\n0\r\n\r\n"
            ),
            b"{}",
        )

    def test_bounds_and_truncation(self):
        for raw in (
            b"Content-Length: 10\r\n\r\n{}",
            b"Content-Length: 8193\r\n\r\n",
            b"Transfer-Encoding: chunked\r\n\r\n2001\r\n",
            b"Transfer-Encoding: chunked\r\n\r\n2\r\n{}xx",
            b"Content-Encoding: gzip\r\n\r\n",
            b"Transfer-Encoding: other\r\n\r\n",
            b"X: " + b"x" * 1024 + b"\r\n\r\n",
            b"X: y\r\n" * 1400 + b"\r\n",
            b"\r\n" + b"x" * 8193,
        ):
            with self.subTest(size=len(raw)):
                with self.assertRaises(github.RequestError):
                    self.body(raw)

    def test_rate_limit_headers(self):
        headers = {
            b"retry-after": b"90",
            b"x-ratelimit-remaining": b"0",
            b"x-ratelimit-reset": b"1200",
        }
        self.assertEqual(github.retry_delay(headers, 1000), 201)
        self.assertEqual(github.retry_delay(headers, 1300), 90)

    def test_date_alignment_and_leap_day(self):
        leap = {"from": "2024-02-28", "to": "2024-03-01", "counts": [0, 1, 2], "total": 3}
        self.assertTrue(github.valid_snapshot(leap, 3))
        leap["from"] = "2024-02-30"
        self.assertFalse(github.valid_snapshot(leap, 3))


class GitHubClientTests(unittest.TestCase):
    def setUp(self):
        self.context = Mock()
        self.context_factory = self.patch(
            github.ssl, "SSLContext", return_value=self.context
        )
        self.patch(github, "open", mock_open(read_data=b"test-ca"), create=True)
        self.patch(
            github.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("local", 443))]
        )
        self.socket = Mock()
        self.patch(github.socket, "socket", return_value=self.socket)
        self.client = github.GitHubClient("test-token", "test-user", days=3)

    def patch(self, target, name, *args, **kwargs):
        context = patch.object(target, name, *args, **kwargs)
        result = context.start()
        self.addCleanup(context.stop)
        return result

    def response(self, payload, status=200, headers=b""):
        body = json.dumps(payload).encode()
        raw = (
            "HTTP/1.1 {} result\r\nContent-Length: {}\r\n".format(status, len(body))
        ).encode()
        stream = io.BytesIO(raw + headers + b"\r\n" + body)
        tls = Mock(wraps=stream)
        tls.write = Mock(side_effect=lambda data: len(data))
        return tls

    def data(self):
        return {
            "data": {"u": {"c": {"c": {"w": [{"d": [{"n": n} for n in [0, 1, 2]]}]}}}}
        }

    def test_context_reused_and_connections_closed(self):
        first, second = self.response(self.data()), self.response(self.data())
        self.context.wrap_socket.side_effect = [first, second]
        for _ in range(2):
            snapshot, delay = self.client.fetch()
            self.assertEqual(snapshot["counts"], [0, 1, 2])
            self.assertEqual(delay, 0)
        self.context_factory.assert_called_once_with(github.ssl.PROTOCOL_TLS_CLIENT)
        self.assertEqual(self.context.verify_mode, github.ssl.CERT_REQUIRED)
        self.context.load_verify_locations.assert_called_once_with(cadata=b"test-ca")
        self.context.wrap_socket.assert_called_with(
            self.socket, server_hostname=github.HOST
        )
        first.close.assert_called_once()
        second.close.assert_called_once()
        self.assertEqual(self.socket.close.call_count, 2)

    def test_graphql_rate_limit_with_http_200_is_failure(self):
        tls = self.response(
            {"errors": [{"type": "RATE_LIMITED"}]}, headers=b"Retry-After: 120\r\n"
        )
        self.context.wrap_socket.return_value = tls
        with self.assertRaises(github.RequestError) as failure:
            self.client.fetch()
        self.assertEqual(failure.exception.retry_after, 120)
        tls.close.assert_called_once()

    def test_http_rate_limit_and_last_quota_point(self):
        headers = b"Retry-After: 120\r\n"
        self.context.wrap_socket.return_value = self.response({}, 429, headers)
        with self.assertRaises(github.RequestError) as failure:
            self.client.fetch()
        self.assertEqual(failure.exception.retry_after, 120)
        headers = (
            b"X-Ratelimit-Remaining: 0\r\nX-Ratelimit-Reset: "
            + str(int(github.time.time()) + 600).encode()
            + b"\r\n"
        )
        self.context.wrap_socket.return_value = self.response(
            self.data(), headers=headers
        )
        _, delay = self.client.fetch()
        self.assertTrue(599 <= delay <= 601)

    def test_tls_failure_closes_tcp_socket(self):
        self.context.wrap_socket.side_effect = OSError()
        with self.assertRaises(OSError):
            self.client.fetch()
        self.socket.close.assert_called_once()

    def test_partial_or_missing_user_data_is_rejected(self):
        for payload in ({"data": {"u": None}}, {"data": {"u": {"c": {"c": {"w": []}}}}}):
            tls = self.response(payload)
            self.context.wrap_socket.return_value = tls
            with self.assertRaises(github.RequestError):
                self.client.fetch()
            tls.close.assert_called_once()
