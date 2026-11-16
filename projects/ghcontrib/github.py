"""Bounded GitHub GraphQL requests with a reusable, verified TLS context."""

import gc
import json
import socket
import ssl
import time

HOST = "api.github.com"
MAX_RESPONSE_BYTES = 8192
QUERY = (
    "query($u:String!,$f:DateTime!,$t:DateTime!){u:user(login:$u){"
    "c:contributionsCollection(from:$f,to:$t){c:contributionCalendar{"
    "w:weeks{d:contributionDays{n:contributionCount}}}}}}"
)


class RequestError(Exception):
    """A safe-to-log failure, with an optional server-requested retry delay."""

    def __init__(self, reason, retry_after=0):
        super().__init__(reason)
        self.retry_after = retry_after


def iso_date(seconds):
    return "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}Z".format(*time.gmtime(seconds)[:6])


def read_line(stream):
    line = bytearray()
    while len(line) < 1024:
        byte = stream.read(1)
        if not byte:
            raise RequestError("truncated HTTP headers")
        line.extend(byte)
        if byte == b"\n":
            return bytes(line)
    raise RequestError("HTTP line exceeds limit")


def read_exact(stream, size):
    result = bytearray(size)
    view = memoryview(result)
    offset = 0
    while offset < size:
        count = stream.readinto(view[offset:])
        if not count:
            raise RequestError("truncated HTTP body")
        offset += count
    return result


def read_headers(stream):
    headers = {}
    used = 0
    while True:
        line = read_line(stream)
        used += len(line)
        if used > 8192:
            raise RequestError("HTTP headers exceed limit")
        if line == b"\r\n":
            return headers
        name, value = line.split(b":", 1)
        name = name.lower()
        if name in (
            b"content-length",
            b"transfer-encoding",
            b"content-encoding",
            b"retry-after",
            b"x-ratelimit-remaining",
            b"x-ratelimit-reset",
        ):
            headers[name] = value.strip().lower()


def read_body(stream, headers):
    if headers.get(b"content-encoding", b"identity") != b"identity":
        raise RequestError("unsupported content encoding")
    transfer = headers.get(b"transfer-encoding")
    if transfer is not None and transfer != b"chunked":
        raise RequestError("unsupported transfer encoding")
    if transfer == b"chunked":
        body = bytearray()
        while True:
            size = int(read_line(stream).split(b";", 1)[0], 16)
            if size == 0:
                return body
            if size < 0 or len(body) + size > MAX_RESPONSE_BYTES:
                raise RequestError("HTTP body exceeds limit")
            body.extend(read_exact(stream, size))
            if read_exact(stream, 2) != b"\r\n":
                raise RequestError("invalid HTTP chunk")
    if b"content-length" in headers:
        size = int(headers[b"content-length"])
        if not 0 <= size <= MAX_RESPONSE_BYTES:
            raise RequestError("HTTP body exceeds limit")
        return read_exact(stream, size)
    body = bytearray()
    while True:
        chunk = stream.read(min(1024, MAX_RESPONSE_BYTES + 1 - len(body)))
        if not chunk:
            return body
        body.extend(chunk)
        if len(body) > MAX_RESPONSE_BYTES:
            raise RequestError("HTTP body exceeds limit")


def retry_delay(headers, now):
    delay = max(0, int(headers.get(b"retry-after", b"0")))
    if headers.get(b"x-ratelimit-remaining") == b"0":
        delay = max(delay, int(headers.get(b"x-ratelimit-reset", b"0")) - now + 1)
    return delay


def valid_snapshot(snapshot, days):
    if not isinstance(snapshot, dict):
        return False
    counts = snapshot.get("counts")
    if not isinstance(counts, list) or len(counts) != days:
        return False
    if any(type(count) is not int or not 0 <= count <= 2_147_483_647 for count in counts):
        return False
    start, end = snapshot.get("from"), snapshot.get("to")
    if not isinstance(start, str) or not isinstance(end, str):
        return False
    # Check range alignment too, so a corrupted cache cannot shift a display.
    try:
        year, month, day = (int(part) for part in start.split("-"))
        stamp = int(time.mktime((year, month, day, 0, 0, 0, 0, 0, 0)))
        if iso_date(stamp)[:10] != start:
            return False
        if iso_date(stamp + (days - 1) * 86400)[:10] != end:
            return False
    except (ValueError, OverflowError, OSError):
        return False
    return type(snapshot.get("total")) is int and snapshot["total"] == sum(counts)


class GitHubClient:
    def __init__(self, token, username="phuber92", days=112, ca_path="github-ca.der"):
        if not token or not username or not 1 <= days <= 366:
            raise ValueError("invalid GitHub configuration")
        self.token = token
        self.username = username
        self.days = days
        self.ca_path = ca_path
        self.context = None

    def _context(self):
        if self.context is None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.verify_mode = ssl.CERT_REQUIRED
            with open(self.ca_path, "rb") as certificate:
                context.load_verify_locations(cadata=certificate.read())
            self.context = context
        return self.context

    def fetch(self):
        now = int(time.time())
        first_day = now // 86400 * 86400 - (self.days - 1) * 86400
        start, end = iso_date(first_day), iso_date(now)
        body = json.dumps(
            {
                "query": QUERY,
                "variables": {"u": self.username, "f": start, "t": end},
            }
        ).encode()
        headers = (
            (
                "POST /graphql HTTP/1.1\r\nHost: api.github.com\r\n"
                "User-Agent: pico-ghcontrib\r\nAuthorization: Bearer {}\r\n"
                "Accept-Encoding: identity\r\nContent-Type: application/json\r\n"
                "Content-Length: {}\r\nConnection: close\r\n\r\n"
            )
            .format(self.token, len(body))
            .encode()
        )
        gc.collect()
        address = socket.getaddrinfo(HOST, 443, socket.AF_INET, socket.SOCK_STREAM)[0][-1]
        sock = socket.socket()
        tls = None
        try:
            sock.settimeout(20)
            sock.connect(address)
            tls = self._context().wrap_socket(sock, server_hostname=HOST)
            tls.write(headers)
            tls.write(body)
            status = read_line(tls).split()
            response_headers = read_headers(tls)
            delay = retry_delay(response_headers, int(time.time()))
            if len(status) < 2 or status[1] != b"200":
                if len(status) > 1 and status[1] in (b"403", b"429"):
                    raise RequestError("GitHub access or rate limit", max(60, delay))
                raise RequestError("GitHub HTTP request failed", delay)
            response = read_body(tls, response_headers)
        finally:
            if tls is not None:
                tls.close()
            sock.close()
        del headers, body, tls, sock, response_headers
        data = json.loads(response)
        del response
        if data.get("errors"):
            if any(error.get("type") == "RATE_LIMITED" for error in data["errors"]):
                raise RequestError("GitHub rate limit", max(60, delay))
            raise RequestError("GitHub GraphQL query failed", delay)
        user = data["data"]["u"]
        if user is None:
            raise RequestError("GitHub user not found")
        counts = [day["n"] for week in user["c"]["c"]["w"] for day in week["d"]]
        snapshot = {
            "from": start[:10],
            "to": end[:10],
            "total": sum(counts),
            "counts": counts,
        }
        if not valid_snapshot(snapshot, self.days):
            raise RequestError("invalid contribution window")
        return snapshot, delay
