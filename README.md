# Machines

MicroPython development for a Raspberry Pi Pico 2 W.

Install the development tools and board stubs into the project `.venv`:

```sh
uv sync
```

Select `.venv/bin/python` as your editor's Python interpreter. Pyright uses this environment to find the Pico 2 W stubs; no separate `typings` directory is needed. The stubs target MicroPython 1.29; match this version to the firmware on your board. Host Python runs the development tools; application code runs on the Pico.

```sh
uv run ruff format .
uv run ruff check .
uv run pyright
```

Ruff formats code and checks line lengths at 90 characters.

## Run on the Pico

Connect the Pico over USB with MicroPython installed. After editing and running the checks above, run a local script on the board without saving it there:

```sh
uv run mpremote run main.py
```

Replace `main.py` with your script. For an interactive REPL, use `uv run mpremote repl`; Ctrl-C interrupts running code and Ctrl-] exits.

When ready to run automatically at boot, copy the script to the board and reset:

```sh
uv run mpremote fs cp main.py :main.py
uv run mpremote reset
```

Copy any imported modules too. Commands select the first available USB serial device; with multiple boards, use `uv run mpremote connect list`, then insert `connect <port>` before the command to select one.

See the [mpremote reference](https://docs.micropython.org/en/latest/reference/mpremote.html) for more commands.
