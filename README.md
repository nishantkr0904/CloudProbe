# CloudProbe

> Hybrid cloud network observability and QA pipeline for AWS.

CloudProbe probes a fleet of network targets on a schedule, classifies *how* each
one fails (refused vs. timeout vs. unreachable), and turns the results into
metrics, alerts, and reports. It is configuration-first: adding a target,
threshold, or cadence is a YAML change, never a code change.

**Status:** the pipeline layers and the automation around them are implemented and
under test — 563 tests passing at 93.7% line coverage, with `ruff`, `black`, and
`mypy --strict` clean across 89 source files. See [Project status](#project-status)
for exactly what is and is not wired yet, [`ROADMAP.md`](ROADMAP.md) for the plan,
and [`docs/architecture.md`](docs/architecture.md) for the runtime design.

---

## What it does

- **Probes targets at layer 3/4.** TCP connect, ICMP echo, and UDP
  response-validation transports, each returning a structured `ProbeResult` that
  distinguishes a refused connection from a timeout from a DNS failure.
- **Discovers targets from AWS.** A static YAML inventory and tag-filtered EC2/VPC
  discovery merge into one canonical inventory of `Target` records.
- **Reports and alerts.** Threshold evaluation drives CloudWatch metrics and
  alarms, SNS notifications, and JSON/HTML run reports.

Everything runs offline in tests: AWS calls go through `moto`, and the probe
transports are exercised against in-memory fakes rather than the network.

---

## Prerequisites

| Tool | Version | Why |
|---|---|---|
| Python | **3.11 or 3.12** | CloudProbe targets `>=3.11,<3.13` (see `pyproject.toml`). |
| `make` | GNU Make | The developer command surface. Ships with macOS and every Linux distro. |
| Git | Any recent | Repository operations and pre-commit hooks. |

Optional:

- **`pyenv`** or **`asdf`** to select Python 3.11 without touching the system
  interpreter. A `.python-version` file is committed so both tools pick the right one.
- **Docker** — only for the container path below. Not needed for local development.
- **AWS credentials** — only for real-AWS runs. The full test suite runs offline.

> If your system `python3` is newer than 3.12, pass the interpreter explicitly:
> `make bootstrap PYTHON=$(which python3.11)`. The pinned dependency set does not
> build on unsupported interpreters.

---

## Quickstart

```bash
git clone "https://github.com/nishantkr0904/CloudProbe.git" cloudprobe
cd cloudprobe
make bootstrap
```

`make bootstrap` creates `.venv/`, installs `requirements-dev.txt` (which pulls in
the runtime pins from `requirements.txt`), and installs the pre-commit hooks. Every
other `make` target depends on it and invokes `.venv/bin/<tool>` directly, so you
never need to activate the virtualenv.

Verify the checkout:

```bash
make lint         # ruff check + black --check + yamllint
make typecheck    # mypy --strict
make test         # all test tiers
make coverage     # full suite + 90% floor
```

Then assemble a config and validate it. `configs/` ships `thresholds.yaml`,
`schedules.yaml`, and `logging.yaml`; the inventory is a template you copy, because
the loader recognizes `inventory.yaml` and not `inventory.example.yaml`:

```bash
cp configs/inventory.example.yaml configs/inventory.yaml
make install-editable                                  # puts `cloudprobe` on PATH
.venv/bin/cloudprobe config validate configs/
# OK: 53 target(s) validated
```

`make bootstrap` deliberately does not install the package, so without
`make install-editable` invoke the CLI through the source tree instead:

```bash
PYTHONPATH=src .venv/bin/python -m cloudprobe config validate configs/
```

### Running a probe cycle

```bash
.venv/bin/cloudprobe run --once --config configs/       # one pass, then exit
.venv/bin/cloudprobe run --scheduler --config configs/  # cron cadence until SIGINT/SIGTERM
.venv/bin/cloudprobe healthcheck --config configs/      # exit 0 if config is valid
```

A probe that fails is *data*, not a crashed run: individual failures are logged and
the process still exits 0. Configuration errors exit 2. Results are currently
emitted to the log — the metrics/alerting/reporting fan-out is implemented as
libraries but is not yet wired into the CLI (see [Project status](#project-status)).

`--config` accepts either a single YAML document or a directory of per-section
files (`inventory.yaml`, `thresholds.yaml`, `schedules.yaml`, `alert_rules.yaml`,
`probe.yaml`), each of which must carry its matching top-level key. Every key is
documented in [`docs/configuration.md`](docs/configuration.md).

---

## Docker

The production image is multi-stage, runs as non-root UID/GID 10001, and installs
`requirements.txt` only — dev dependencies never enter the container.

```bash
docker build -t cloudprobe:latest -f docker/Dockerfile .
docker run --rm cloudprobe:latest --version
```

Arguments after the image name are forwarded to `python -m cloudprobe`. With no
arguments the entrypoint runs the mode named by `CLOUDPROBE_MODE`:

```bash
docker run --rm \
  -v "$PWD/configs:/etc/cloudprobe/configs:ro" \
  -v "$PWD/reports:/var/cloudprobe/reports" \
  -e CLOUDPROBE_MODE=oneshot \
  cloudprobe:latest
```

| Variable | Default | Effect |
|---|---|---|
| `CLOUDPROBE_MODE` | `oneshot` | `oneshot` runs one cycle and exits; `scheduler` runs until signalled. |
| `CLOUDPROBE_CONFIG` | `/etc/cloudprobe/configs` | Config path inside the container. |
| `AWS_REGION` | `us-east-1` | Region for AWS calls. |
| `LOG_LEVEL` | `INFO` | Root log level. |

Mounts: `/etc/cloudprobe/configs` (read-only), `/var/cloudprobe/reports` and
`/var/cloudprobe/logs` (writable). `HEALTHCHECK` calls `cloudprobe healthcheck`, so
a container with an invalid config reports unhealthy rather than merely "up".
`docker/docker-compose.yml` wires the same contract with a read-only root
filesystem and `no-new-privileges`:

```bash
docker compose -f docker/docker-compose.yml up --build
```

---

## Continuous integration

Three workflows in `.github/workflows/`. Every step shells out to a `make` target,
so CI runs the same commands you run locally — there is no CI-only tooling.

| Workflow | Trigger | What it runs |
|---|---|---|
| `ci.yml` | Every PR, push to `main` | `make lint` → `typecheck` → `test-unit` → `test-integration` → `coverage`, on a Python 3.11 and 3.12 matrix. Uploads `coverage.xml` and the HTML report as an artifact. |
| `docker.yml` | PRs touching `docker/`, `src/`, or `requirements*.txt` | Builds the image and runs the `docker run --rm cloudprobe:test --version` smoke test. |
| `nightly.yml` | Nightly at 02:00 UTC, or manual dispatch | `make test-regression` and `make test-failure`. Opens a GitHub Issue if a scheduled run fails. |

---

## Common developer workflows

```bash
make help              # List every target with its description
make bootstrap         # Create .venv, install dev deps, install pre-commit hooks
make install-editable  # Install cloudprobe in editable mode (adds the CLI to .venv/bin)
make format            # Black, then ruff check --fix
make lint              # ruff check + black --check + yamllint
make typecheck         # mypy --strict
make test              # All tiers
make test-unit         # -m unit
make test-integration  # -m integration
make test-regression   # -m regression
make test-failure      # -m failure_scenarios
make coverage          # Full suite with coverage; fails under 90%
make precommit         # Run every pre-commit hook against every file
make clean             # Remove caches, coverage output, build artifacts (keeps .venv)
make distclean         # clean + remove the venv
```

Run a single test or file with the venv's pytest directly:

```bash
.venv/bin/pytest tests/unit/config/test_models.py
.venv/bin/pytest tests/unit/config/test_models.py::TestValidConfig::test_minimal_valid_config
.venv/bin/pytest -k "duplicate_target"
```

Pre-commit runs automatically on `git commit`. Skipping a hook with
`git commit --no-verify` is debt, not a fix.

---

## Testing

Four tiers, selected by pytest marker and mapped to
[`docs/architecture.md`](docs/architecture.md) §10. `--strict-markers` means every
test must carry exactly one.

| Tier | Marker | Tests | Scope |
|---|---|---|---|
| Unit | `unit` | 504 | Pure logic, no I/O. |
| Integration | `integration` | 15 | Layer seams against `moto` and in-memory fakes. |
| Regression | `regression` | 44 | Golden-file output stability. |
| Failure scenarios | `failure_scenarios` | 0 | Reserved for the fault-injection suite; not yet written. |

Coverage is enforced across the sum of the tiers at a 90% floor with branch
coverage on; the suite currently reports **93.7%**. `filterwarnings = ["error"]`
means any warning raised during a test fails it.

The `failure_scenarios` tier is empty, so `make test-failure` collects nothing.
The Makefile maps pytest's "no tests collected" exit code to success on purpose,
which is what keeps the nightly workflow green until that tier lands.

---

## Repository layout

| Path | Contents |
|---|---|
| `src/cloudprobe/config/` | Frozen Pydantic v2 models, the YAML loader, cross-field validators, and the `ConfigError` hierarchy. |
| `src/cloudprobe/discovery/` | Static inventory plus tag-filtered EC2/VPC discovery, merged into one canonical inventory. |
| `src/cloudprobe/probes/` | TCP, ICMP, and UDP transports behind a common `Probe` interface. |
| `src/cloudprobe/ssh/` | SSH command execution used by host-level checks. |
| `src/cloudprobe/metrics/` | CloudWatch metric dispatch. |
| `src/cloudprobe/alerting/` | Threshold binding, alarm state, and SNS notification. |
| `src/cloudprobe/reporting/` | Report assembly, summaries, and the HTML renderer. |
| `src/cloudprobe/scheduler/` | One-shot and cron-driven execution. |
| `src/cloudprobe/cli.py` | The `python -m cloudprobe` entry point. |
| `configs/` | Example inventory plus thresholds, schedules, and logging config. |
| `docker/` | `Dockerfile`, `entrypoint.sh`, `docker-compose.yml`. |
| `tests/` | The four-tier suite plus golden fixtures. |

Layers flow downward only — CLI → config → discovery → probes → metrics/alerting →
reporting. `docs/project-structure.md` §17 holds the enforceable dependency table
and each package's "must never contain" list.

---

## Documentation

| Document | Contents |
|---|---|
| [`ROADMAP.md`](ROADMAP.md) | Phase plan, deliverables, and exit criteria. |
| [`docs/architecture.md`](docs/architecture.md) | Runtime design, layering rationale, deployment flow, failure handling. |
| [`docs/project-structure.md`](docs/project-structure.md) | Why the repository is shaped the way it is; the dependency table. |
| [`docs/configuration.md`](docs/configuration.md) | Every YAML key: type, default, example, effect. |

Additional guides named in `docs/project-structure.md` §12 — `aws-setup.md`,
`docker.md`, `operations.md`, `probes.md`, `alerting.md`, `testing.md`,
`networking-primer.md`, `contributing.md` — are planned but not yet written.

---

## Development tooling

| Tool | Role | Configured in |
|---|---|---|
| **Ruff** | Lint + import ordering. Its formatter is deliberately disabled — Black owns formatting. | `pyproject.toml` `[tool.ruff]` |
| **Black** | The one and only formatter, line length 100. | `pyproject.toml` `[tool.black]` |
| **mypy** | `strict = true` over `src/`; `tests/` exempt from `disallow_untyped_defs` only. | `pyproject.toml` `[tool.mypy]` |
| **pytest** | Test runner; four markers map to the tier model. | `pyproject.toml` `[tool.pytest.ini_options]` |
| **pytest-cov** + **coverage** | Branch coverage with a 90% hard floor. | `pyproject.toml` `[tool.coverage.*]` |
| **pytest-socket** | Available to block real socket use; applied per-fixture rather than globally. | `requirements-dev.txt` |
| **moto** | AWS mocking for the integration tier, scoped to EC2, CloudWatch, SNS, and S3. | `requirements-dev.txt` |
| **pre-commit** | Runs the fast checks on every commit. | `.pre-commit-config.yaml` |
| **yamllint** | YAML correctness at the project's 100-column standard. | `.yamllint` |

Commits follow Conventional Commits, one logical change per commit. Docstrings are
Google-style.

---

## Platform notes

### macOS

```bash
brew install python@3.11 make git
make bootstrap
```

macOS ships `make` — no separate install needed. `xcode-select --install` may be
required the first time a dependency lacks a wheel for your architecture.

### Linux (Debian / Ubuntu)

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip make git \
                    build-essential libssl-dev libffi-dev
make bootstrap
```

On RHEL/Fedora use `dnf` and install `openssl-devel` and `libffi-devel`.

### Windows

The `Makefile` uses POSIX shell semantics, so use WSL 2 (recommended — it mirrors
CI) or Git Bash with MSYS2's `make`. Use Python 3.11/3.12 from `python.org` or
`pyenv-win`, and set `git config core.autocrlf input` before cloning so the
repository does not accumulate `\r\n` diffs. The committed `.editorconfig` keeps
line endings LF if your editor respects it.

---

## Project status

Implemented and under test:

- Configuration layer, probe transports (TCP/ICMP/UDP), SSH execution, EC2/VPC
  discovery, CloudWatch metrics, alerting, reporting, and the scheduler.
- The `run`, `healthcheck`, and `config validate` CLI commands.
- Docker image, compose file, and the three CI workflows.

Not yet implemented — these are roadmap items, not omissions from this README:

- **End-to-end pipeline wiring.** `run` drives the probe transports against the
  static inventory and logs results; the metrics → alerting → reporting fan-out
  exists as libraries but is not yet joined to the CLI.
- **HTTP and SSH probe types** are accepted in config and skipped at run time; only
  TCP, ICMP, and UDP have transports.
- **AWS ping in `healthcheck`.** It validates configuration only.
- **The `failure_scenarios` test tier**, the AWS lab scripts (`scripts/` is empty),
  Dependabot, container vulnerability scanning, and the release artifacts
  (`assets/`, `v0.1.0` tag).

See [`ROADMAP.md`](ROADMAP.md) for the sequence and exit criteria.
