# GitHub contribution updater

An autonomous MicroPython application for a continuously USB-powered Raspberry Pi Pico 2 W. It refreshes all 112 calendar days (16 weeks, including today, UTC) for `phuber92` every minute, providing a local snapshot for a future GPIO LED graph. The contribution updater runs directly on the device. See the [repository setup](../../README.md), [glossary](GLOSSARY.md), and [architecture decisions](docs/adr/0001-autonomous-local-updater.md).

## Configure and run

Copy `config.example.py` to `config.py` in this directory if needed. Fill in `SSID`, `PASSWORD`, and `GITHUB_TOKEN`. Optional `USERNAME` and `WEEKS` default to `phuber92` and `16`; the maximum contribution window is 366 days. `config.py` is gitignored, imported directly, and never printed. The token must be authorized to read the contribution data you want displayed; changes to permissions or private-contribution settings can affect the counts.

The included `github-ca.der` contains a trusted root certificate. To provision or replace it manually using the development host's verified trust store (host Python 3.13+):

```sh
uv run --python 3.14 python projects/ghcontrib/tools/provision_ca.py
```

Connect the Pico over USB, then run from the repository root:

```sh
TZ=UTC uv run mpremote soft-reset rtc --set mount projects/ghcontrib run projects/ghcontrib/main.py
```

The USB mount provides the project's modules, credentials, and certificate without saving them to board flash. In this mode the cache is written to the project directory on the development host. Serial logs show snapshot changes, freshness transitions, and failures; credentials and raw server errors are not logged. Stop with Ctrl-C.

## Autonomous startup

Copy the application and its required files, then reset:

```sh
uv run mpremote fs cp projects/ghcontrib/main.py :main.py
uv run mpremote fs cp projects/ghcontrib/github.py :github.py
uv run mpremote fs cp projects/ghcontrib/updater.py :updater.py
uv run mpremote fs cp projects/ghcontrib/config.py :config.py
uv run mpremote fs cp projects/ghcontrib/github-ca.der :github-ca.der
uv run mpremote reset
```

This stores credentials and the snapshot cache on board flash. Wi-Fi must reach GitHub on TCP port 443 and `time.cloudflare.com` on UDP port 123 for clock initialization. The clock is checked before HTTPS and resynchronized daily; failure of a daily resync does not stop requests while the RTC remains initialized. Refresh the installed CA file and reset the board if its trust chain needs maintenance. GitHub certificate and hostname verification stay enabled; see [manual trust provisioning](docs/adr/0002-manual-trust-provisioning.md).

## Operation and local interface

`main.create_updater()` returns an `Updater`. Its `snapshot` is either `None` before data is available, or a dictionary containing `from`, `to`, `total`, and `counts`. Counts are ordered oldest to newest, with consecutive UTC dates inferred from `from`. Treat the dictionary and its list as read-only. `last_success` is a UTC epoch timestamp, initially `None`; `stale` is derived from elapsed time since the last successful retrieval. A restored snapshot starts stale until verified; an existing snapshot becomes stale after five minutes without success. Failures retain the last snapshot.

Call `run()` for the normal application loop. A future local consumer can instead call `step()` once per second and read these attributes; each step makes at most one due request. Requests are synchronous, so network activity can delay that consumer. GPIO drivers, display animation, and callbacks are outside the current scope.

Successful requests target a 60-second start-to-start interval, without overlapping or catching up missed requests. Failures retry after 1, 2, 4, 8, then 15 minutes, capped at 15 minutes. A longer GitHub `Retry-After` or exhausted-quota reset takes precedence, including on successful responses that consume the last available point. Successful retrieval resets the backoff. Scheduling and stale detection use the wrapping monotonic counter, independently of RTC corrections. GitHub may delay or revise contribution counts; the one-minute target describes polling cadence, not upstream publication latency.

The client reuses its parsed TLS context, opens and closes one connection per request, and requests only daily counts with short GraphQL aliases. Dates and totals are computed locally. HTTP bodies are capped at 8 KiB, headers at 8 KiB, and individual header lines at 1 KiB. Socket operations have timeouts; DNS timing is controlled by the firmware. A complete refresh can therefore exceed a minute during connection trouble.

The versioned cache is written to `contributions.json.tmp`, closed, then renamed over `contributions.json`. It is saved only when the snapshot changes, with retries after failed writes. Unchanged successful refreshes write nothing. Invalid, truncated, mismatched-user, or mismatched-window caches are ignored. Restored data keeps its original date range until a new complete snapshot is available. The cache contains counts and dates, never credentials. `contributions.json` and its temporary file are gitignored.

## Validation

Run the [repository checks](../../README.md#development), plus host behavior tests:

```sh
TZ=UTC uv run pytest projects/ghcontrib/tests
```

The tests cover scheduling, retry limits and server delays, stale data, clock wraparound, cache recovery and replacement failure, HTTP framing and bounds, and safe logging. UTC is required for host date validation; RP2 MicroPython uses UTC for these date conversions.

Board checks verified 112-day HTTPS retrieval, cold-clock NTP initialization, two real one-minute refreshes, Wi-Fi reconnection, stale cache restoration, cache replacement, and no flash writes for unchanged snapshots. Free heap after both periodic refreshes was 419,936 bytes. Multi-day operation and physical power interruption during cache writes remain unverified.
