# CloudProbe

> Hybrid cloud network observability and QA pipeline for AWS.
> **Status:** Phase 1 — development environment established. See [`ROADMAP.md`](ROADMAP.md) for the full plan and [`docs/architecture.md`](docs/architecture.md) for the runtime design.

This README currently documents only the **development environment**. User-facing documentation and deployment examples are completed in later phases (Phase 8) of the roadmap.

---

## Prerequisites

| Tool | Version | Why |
|---|---|---|
| Python | **3.11 or 3.12** | CloudProbe targets `>=3.11,<3.13` (see `pyproject.toml`). |
| `make` | GNU Make | The developer command surface (`Makefile`) assumes GNU make. Ships with macOS and every Linux distro. |
| Git | Any recent | Repository operations and pre-commit hooks. |

Optional but recommended:

- **`pyenv`** or **`asdf`** to select Python 3.11 without touching the system interpreter. A `.python-version` file is committed so both tools pick the right one automatically.
- **Docker** — only required later (ROADMAP Phase 6). Not needed for local development.
- **AWS credentials** — only required for real-AWS demonstration. The full test suite runs offline against `moto`.

---

## Bootstrapping the environment

The `make bootstrap` target is the single command a new contributor runs. It:

1. Creates a virtual environment in `.venv/`.
2. Upgrades `pip`, `setuptools`, and `wheel`.
3. Installs the runtime dependency graph from `requirements.txt`.
4. Installs development dependencies from `requirements-dev.txt`.
5. Installs `pre-commit` hooks so every commit is linted, formatted, and type-checked before it lands.

```bash
git clone <repo-url> cloudprobe
cd cloudprobe
make bootstrap
source .venv/bin/activate    # activation is optional; make targets already use .venv
```

After `make bootstrap`, verify the environment:

```bash
make lint         # Ruff + Black --check + yamllint
make typecheck    # mypy in strict mode
make test         # pytest (passes even before any tests exist)
```

If any of the above fails on a clean clone, that is a bug in the environment — please open an issue.

---

## Platform-specific setup

### macOS

```bash
# Python 3.11 (via Homebrew)
brew install python@3.11 make git

# Optional: pyenv for multi-version management
brew install pyenv
pyenv install 3.11.9
pyenv local 3.11.9

make bootstrap
```

macOS ships GNU make as `/usr/bin/make` — no separate install needed. The `xcode-select --install` command may be required the first time you build `cryptography` if a wheel is unavailable for your architecture.

### Linux (Debian / Ubuntu)

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip make git \
                    build-essential libssl-dev libffi-dev

make bootstrap
```

For RHEL/Fedora replace the `apt` line with the equivalent `dnf` invocation and install `openssl-devel` and `libffi-devel`.

### Windows

Native Windows is supported through one of the following environments — **not** through cmd.exe or PowerShell alone, because the `Makefile` uses POSIX shell semantics.

**Option A — WSL 2 (recommended).** Install Ubuntu from the Microsoft Store, then follow the Linux instructions above. This is the closest experience to macOS/Linux and is the environment CI mirrors.

**Option B — Git Bash + MSYS2 make.** Install Git for Windows (which bundles Git Bash), then install MSYS2 and add `make` to `PATH`. Run all `make` commands from Git Bash. Filesystem case-sensitivity and line-endings must remain LF — the committed `.editorconfig` enforces this if your editor respects it.

Whichever option you choose:

- Use Python 3.11 or 3.12 from `python.org` or via `pyenv-win`.
- Set `git config core.autocrlf input` before cloning, so the repository does not accumulate `\r\n` diffs.

---

## Directory-level orientation for new contributors

The repository is documented in depth by `docs/project-structure.md`. For environment purposes the important facts are:

| Path | Meaning |
|---|---|
| `pyproject.toml` | PEP 621 project metadata and configuration for `ruff`, `black`, `mypy`, `pytest`, `coverage`. |
| `requirements.txt` | Pinned runtime dependencies (installed into the container). |
| `requirements-dev.txt` | Pinned development dependencies (installed locally, never into the container). |
| `.pre-commit-config.yaml` | Hooks that run on every commit. |
| `.editorconfig` | Cross-editor whitespace and EOL discipline. |
| `.python-version` | Interpreter selector for `pyenv` / `asdf`. |
| `Makefile` | Developer command surface. Every target is described by `make help`. |
| `src/cloudprobe/` | Production Python. Empty in Phase 1; populated by later phases. |
| `tests/` | Four-tier test taxonomy (unit / integration / regression / failure_scenarios). |

---

## Common developer workflows

```bash
make help              # List every target with its description
make format            # Auto-format with Black + apply Ruff auto-fixes
make lint              # Check formatting and lint rules without changing files
make typecheck         # Strict mypy
make test              # Run all test tiers
make test-unit         # Run only unit tests
make test-integration  # Run only integration tests (moto, pytest-socket)
make coverage          # Run all tests with coverage; enforces the 90% floor
make precommit         # Run every pre-commit hook against every file
make clean             # Remove caches and build artefacts (keeps the venv)
make distclean         # clean + remove the venv
```

Pre-commit runs automatically on `git commit`. To skip a hook in an emergency use `git commit --no-verify`, then open an issue describing why — bypassing hooks is a debt, not a solution.

---

## Development tooling — what each tool does

| Tool | Role | Configured in |
|---|---|---|
| **Ruff** | Fast lint + import ordering. Formatter deliberately not enabled — Black owns formatting. | `pyproject.toml` `[tool.ruff]` |
| **Black** | The one and only code formatter. | `pyproject.toml` `[tool.black]` |
| **mypy** | Strict static type-checking on `src/`, lenient on `tests/`. | `pyproject.toml` `[tool.mypy]` |
| **pytest** | Test runner. Four markers (`unit`, `integration`, `regression`, `failure_scenarios`) map to the taxonomy in [`docs/architecture.md`](docs/architecture.md) §10. | `pyproject.toml` `[tool.pytest.ini_options]` |
| **pytest-cov** + **coverage** | Coverage collection with a 90% hard floor. | `pyproject.toml` `[tool.coverage.*]` |
| **pytest-socket** | Fails any test that opens a real socket. Protects the invariant that CI runs offline. | Enabled per-test as needed. |
| **moto** | AWS API mocking for the integration tier — scoped to the four services CloudProbe actually calls (EC2, CloudWatch, SNS, S3). | `requirements-dev.txt` |
| **pre-commit** | Runs the fast subset of checks locally on every commit. | `.pre-commit-config.yaml` |
| **yamllint** | Catches YAML errors in `configs/` and `docs/` before they reach the loader. | `.pre-commit-config.yaml` |

---

## Compatibility notes

- **GitHub Actions.** The same commands (`make lint`, `make typecheck`, `make coverage`) drive local development and CI. There is no CI-only tooling.
- **Docker.** The production image installs `requirements.txt` only. Dev dependencies never enter the container. Multi-stage build details land in ROADMAP Phase 6.
- **AWS Free Tier.** No development tool in this list makes AWS calls. Real AWS interaction is opt-in via credentials at runtime.
- **moto-based testing.** Every dependency has been checked to work with `moto>=5.0`. `boto3-stubs` is service-scoped to the same four services `moto` mocks.

---

## What comes next

Environment setup unlocks Phase 1 of the roadmap. The next milestones are:

1. **Phase 1 exit.** Stub `src/cloudprobe/__init__.py`, `__main__.py`, and `cli.py` so `python -m cloudprobe --version` prints a version string.
2. **Phase 2.** Configuration models (`src/cloudprobe/config/`).
3. **Phase 3.** Probe engine (`src/cloudprobe/probes/`).

See [`ROADMAP.md`](ROADMAP.md) for the full sequence and [`docs/architecture.md`](docs/architecture.md) for runtime design.
