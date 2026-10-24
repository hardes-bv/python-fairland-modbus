# AGENTS.md

Guidance for AI coding agents (and humans) working in this repository. This file
follows the [agents.md](https://agents.md) convention. `CLAUDE.md` is a symlink
to this file, so Claude-compatible tooling reads the same guidance.

## What this project is

python-fairland-modbus is an asynchronous Python client for Fairland inverter
pumps, heat pumps and water treatment devices over
Modbus. It decodes Fairland's proprietary register blocks (inverter, heat
pump, water treatment) into typed values, and can write the control registers.
It is pure Python built on
[`modbus-connection`][modbus-connection], which owns the transport and the field
decoding; this library declares the register layout and detects the device.

The consumer owns the connection: it hands the library a `ModbusUnit`
(`connection.for_unit(id)`), and one `Fairland` instance maps to one unit.

## Project layout

| Path                   | Purpose                                               |
|------------------------|-------------------------------------------------------|
| `src/fairland_modbus/` | The package                                           |
| `  fairland.py`        | The `Fairland` client: probe, update, detection       |
| `  components.py`      | Register field declarations per block, and writes     |
| `  const.py`           | Addresses and enums                                   |
| `  exceptions.py`      | `FairlandError` and its subclasses                    |
| `  heat_pump/`         | Heat pumps with an MWH216 or MWH298 board             |
| `    components.py`    | The fixed read blocks the board's table requires      |
| `    device.py`        | `FairlandHeatPump`: validate, poll, write             |
| `    testing.py`       | A register image and the allowed reads, for tests     |
| `tools/simulator.py`   | A heat pump simulator written from the register table |
| `tests/`               | pytest suite; each test has a one-line docstring      |
| `examples/`            | Runnable examples, exercised by `test_examples.py`    |

## Commands

This is a [uv][uv] project.

During iteration, running a single tool directly is fine and faster:
`poetry run pytest -k ...`, `poetry run ruff check .`, `poetry run ty check src`.
The fuzz tests run more examples under `HYPOTHESIS_PROFILE=ci`.

## Conventions

- The library never leaks a raw exception: reads and writes surface as
  `FairlandConnectionError` (transport) or `FairlandError` (bad value or
  undecodable data). Keep that contract when adding code.
- A point the device does not implement decodes to `None`. Do not turn a genuine
  reading into `None`, and do not let a read failure masquerade as one.
- Coverage is enforced at 100% on the package. New code needs tests.
- Every test carries a one-line docstring describing what it verifies.
- Comments explain the why, not the what. Clarity over cleverness, clear names,
  and blank lines between logical steps.

## Writing and voice

English for all public artifacts (commits, PRs, issues). Held to a high bar:
clear, honest, no filler. Avoid:

- AI cheerleading and marketing speak (leverage, synergize, delight).
- Em-dashes and en-dashes anywhere. Use a period, colon, comma, or parentheses;
  hyphen only for compound words. Restructure a sentence rather than reach for one.
- "e.g.", "i.e.", "etc."; write "like", "for example", "such as".
- CAPS for emphasis (use italics); "click" as a verb (use "select").
- "HA"/"HASS"; write "Home Assistant" in full, and never frame it as fragile.
- "master/slave"; use "client/server", "leader/follower", "main/replica".

See [AI_POLICY.md](AI_POLICY.md) for the contribution policy around AI tooling.

## Gotchas

- Writable control fields carry validators, and a successful write caches the
  value the device would report back (`decode(encode(value))`), not the raw input.
- The MWH216 and MWH298 boards must be read in blocks of exactly 48 bits or 3
  registers, from the starts the table allows, or they may return wrong data.
  Every component claims the
  first and last address of each block so the planner reads it whole.
  `tests/test_heat_pump.py` checks every read against
  `heat_pump.testing.ALLOWED_READS`, and the simulator refuses anything else.

## Where to read next

- `README.md`: install, usage, and the development setup.
- `tests/test_heat_pump.py` and `tests/test_simulator.py`: how the heat pump
  register map is checked against the table, on a mock unit and against the
  simulator.

[modbus-connection]: https://github.com/home-assistant-libs/modbus-connection
[uv]: https://docs.astral.sh/uv/
