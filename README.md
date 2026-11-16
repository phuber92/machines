# Machines

MicroPython projects for a Raspberry Pi Pico 2 W. Each project lives under `projects/` with its own setup, configuration, and deployment instructions.

## Development

Install the development tools and board stubs:

```sh
uv sync
```

Select `.venv/bin/python` as your editor's Python interpreter. The stubs target MicroPython 1.29; match this version to the firmware on your board. Host Python runs development tools; application code runs on the Pico. Stubs and host checks do not establish runtime compatibility, so verify hardware-dependent behavior on the board.

After Python changes, run:

```sh
uv run ruff format .
uv run ruff check .
uv run pyright
TZ=UTC uv run pytest
```

Ruff formats code and checks line lengths at 90 characters. Manage development dependencies with `uv`, keeping `pyproject.toml` and `uv.lock` in sync.

Pytest discovers host tests under `projects/`; run them in UTC for consistent date handling. Organize related tests in `unittest.TestCase` classes, using `setUp` and `addCleanup` for shared setup and cleanup. Use `self.assert*` methods for assertions, including `self.assertRaises` for expected exceptions. Pytest provides discovery, test selection, output capture, and failure reports. Host tests should cover application behavior without requiring a connected board or credentials; check hardware-dependent behavior separately on the Pico.

To run one project's tests or select tests by name:

```sh
TZ=UTC uv run pytest projects/<project>/tests
TZ=UTC uv run pytest -k <name>
```

## Run on the Pico

Connect a Pico over USB with MicroPython installed. Run a local script from RAM without saving it to board flash:

```sh
uv run mpremote run path/to/script.py
```

To make a project's files available over USB, mount its directory before running its entry point:

```sh
uv run mpremote mount projects/<project> run projects/<project>/main.py
```

For an interactive REPL, use `uv run mpremote repl`; Ctrl-C interrupts running code and Ctrl-] exits. To save an application for automatic startup, copy its entry point to `:main.py` and copy its required modules and assets as described in that project's docs, then reset:

```sh
uv run mpremote fs cp path/to/main.py :main.py
uv run mpremote reset
```

Commands select the first available USB serial device. With multiple boards, use `uv run mpremote connect list`, then insert `connect <port>` before the command to select one. Clock-dependent applications should set the RTC to UTC; `TZ=UTC uv run mpremote rtc --set` uses the development host's clock.

See the [mpremote reference](https://docs.micropython.org/en/latest/reference/mpremote.html) for more commands.

## Projects

- [GitHub contribution updater](projects/ghcontrib/README.md)
