# Development

- Read [README.md](README.md) for environment setup, validation commands, and the `mpremote` workflow for running and deploying code on the board. Run the validation commands after Python changes.
- Application code targets MicroPython on the Raspberry Pi Pico 2 W. Choose APIs available in the target firmware; host CPython and installed stubs alone cannot establish runtime compatibility.
- Manage development dependencies with `uv`; keep `pyproject.toml` and `uv.lock` in sync.
- For hardware-dependent changes, report which behavior was checked on a board and which remains unverified.
- Keep Markdown paragraphs and list items on single lines; use soft wrapping in the editor.
- Size documentation to the complexity of the task; link to existing instructions instead of duplicating them.
