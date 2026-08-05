# CloudProbe — Project Structure

> **Status:** Authoritative reference. Any deviation between this document and the working repository is a bug in one of the two — resolve it in the same PR that introduces the drift.
> **Scope:** This document explains *why the repository is shaped the way it is*. For *what* to build and *when*, see [`ROADMAP.md`](../ROADMAP.md). For *how* to run the system, see [`docs/operations.md`](operations.md).
> **Audience:** Engineers extending CloudProbe, reviewers evaluating it, and future maintainers deciding where new code belongs.

---

## Table of Contents

1. [Purpose of this Document](#1-purpose-of-this-document)
2. [Design Principles](#2-design-principles)
3. [Layered Architecture](#3-layered-architecture)
4. [Repository Top-Level Layout](#4-repository-top-level-layout)
5. [Root Files](#5-root-files)
6. [`src/cloudprobe/` — Application Packages](#6-srccloudprobe--application-packages)
7. [`tests/` — Four-Tier QA Layout](#7-tests--four-tier-qa-layout)
8. [`configs/` — Runtime Configuration Surface](#8-configs--runtime-configuration-surface)
9. [`infra/` — Infrastructure Definitions](#9-infra--infrastructure-definitions)
10. [`docker/` — Container Packaging](#10-docker--container-packaging)
11. [`scripts/` — Operational Automation](#11-scripts--operational-automation)
12. [`docs/` — Written Knowledge](#12-docs--written-knowledge)
13. [`assets/` — Media & Diagrams](#13-assets--media--diagrams)
14. [`examples/` — Reference Artifacts](#14-examples--reference-artifacts)
15. [`logs/` and `reports/` — Generated Artifacts](#15-logs-and-reports--generated-artifacts)
16. [`.github/` — CI, Templates, Ownership](#16-github--ci-templates-ownership)
17. [Dependency Flow](#17-dependency-flow)
18. [Future Scalability](#18-future-scalability)
19. [Where Does New Code Belong? (Decision Tree)](#19-where-does-new-code-belong-decision-tree)

---

## 1. Purpose of this Document

The repository layout is a design artifact. It encodes decisions about **boundaries**, **dependencies**, and **evolution**. Getting the shape right early is cheap; correcting it after ten contributors have taken shortcuts is expensive.

This document exists to:

- Give every file and folder a **stated reason to exist**, so nothing accumulates by accident.
- Define **what each directory must never contain**, so contributors can reject misplaced code by pointing at a rule rather than a taste.
- Describe **inbound and outbound dependencies** for each package, so architectural drift (a low-level module importing a high-level one, for instance) is visible in review.
- Explain the **layering model** so that when a new feature arrives, its home is obvious.
- Establish **evolution paths** so that reasonable growth (a new probe type, a new cloud provider, a new report format) can be absorbed without restructuring the tree.

If you cannot map a proposed change onto this document, either the change is misdesigned or the document is out of date. Both are worth pausing to fix.

---

## 2. Design Principles

These five principles precede every layout decision below. They are load-bearing — every subsequent section is a consequence of one or more of them.

### 2.1 Modularity

Each package under `src/cloudprobe/` owns a **single, nameable responsibility**. A package's name is a promise: `probes/` performs probes, `metrics/` emits metrics, `alerting/` decides when to alarm. When a package's name stops describing its contents, split the package.

Modularity is enforced negatively as well as positively: for every package, this document lists what it *must never contain*. A `probes/` module that reaches into `boto3` is a modularity violation even if the code works.

### 2.2 Separation of Concerns

Concerns are separated **by lifecycle**, not just by topic. Configuration is loaded once; discovery runs periodically; probes run frequently; reporting runs at the end of a cycle. Mixing lifecycles — for example, letting a probe re-read YAML on every invocation — couples layers that should be independent and destroys testability.

Concretely: the probe engine does not know how targets were discovered, and discovery does not know how probes will consume targets. Both meet at the `Target` data model owned by `config/`.

### 2.3 Dependency Direction

Dependencies flow **downward through the layer stack** described in §3 and **never upward**. The CLI depends on scheduling; scheduling depends on probes; probes depend on the config model. The config model depends on nothing internal.

This is the single rule that keeps the architecture honest. Every import statement in the codebase should be verifiable against §17 (Dependency Flow). Cycles are not "resolved" — they are refactored.

### 2.4 Configuration-First Architecture

Behavior is described in YAML under `configs/` before it is coded in Python under `src/`. Adding a new target does not require a code change; adding a new threshold does not require a code change; changing a schedule does not require a code change. The Python code is a **runtime for the configuration**, not the other way around.

This principle is what allows the résumé claim of "50+ EC2 instances and VPC subnets" to be verifiable without provisioning 50 machines: the configuration surface, not the code, defines the fleet.

### 2.5 Testability

Every module is written so that it can be exercised without touching the network, AWS, or a live SSH endpoint. This is achieved by:

- Passing collaborators (AWS clients, socket factories, SSH transports) as arguments rather than constructing them inside functions.
- Isolating side-effectful code (I/O, subprocess, time) into thin adapters at the edges of packages.
- Treating `moto`, `pytest-socket`, and Paramiko's in-memory transport as first-class dependencies, not afterthoughts.

If a module cannot be unit-tested without patching, its design is wrong — not the test's.

---

## 3. Layered Architecture

CloudProbe is organized as a strict downward-flowing stack. Each layer consumes only the layer(s) beneath it and exposes a narrow interface to the layer above.

```
┌───────────────────────────────────────────────────────────────┐
│  Presentation Layer  —  src/cloudprobe/cli.py, __main__.py    │
│  Human/OS entrypoint. Argument parsing, exit codes, signals.  │
└───────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────────┐
│  Configuration Layer  —  src/cloudprobe/config/               │
│  Loads, validates, and freezes the runtime contract.          │
└───────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────────┐
│  Discovery Layer  —  src/cloudprobe/discovery/                │
│  Resolves static + dynamic targets into a canonical inventory.│
└───────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────────┐
│  Probe Engine  —  src/cloudprobe/probes/, ssh/                │
│  Executes reachability checks. Emits ProbeResult stream.      │
└───────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────────┐
│  Metrics & Alerting  —  src/cloudprobe/metrics/, alerting/    │
│  Publishes measurements; evaluates thresholds; binds alarms.  │
└───────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────────┐
│  Reporting  —  src/cloudprobe/reporting/                      │
│  Materializes JSON / CSV / HTML diagnostic artifacts.         │
└───────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────────┐
│  Storage  —  src/cloudprobe/storage/                          │
│  Persists reports to local FS or S3 via a common interface.   │
└───────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────────┐
│  Infrastructure Integrations  —  Boto3, Paramiko, sockets     │
│  External world. Wrapped by thin adapters, never called       │
│  directly from layers above the ones that own the adapter.    │
└───────────────────────────────────────────────────────────────┘
```

The `scheduler/` package is orthogonal to this stack — it is a **driver** that sits alongside the CLI and repeatedly invokes the discovery → probe → metrics → reporting pipeline on a cadence. It does not add a layer; it re-enters the stack.

The `util/` package is orthogonal in a different way — it provides cross-cutting primitives (structured logging, retry/backoff, timing) that any layer may import. To prevent `util/` from becoming a dumping ground, it may not import from any other `cloudprobe` package.

### Why this layering improves maintainability

- **Local reasoning.** A change in reporting cannot break discovery. A change in probe internals cannot ripple into config validation. The blast radius of any edit is bounded by the layer it lives in.
- **Substitutability.** Any layer's downward dependency can be swapped: local filesystem storage can be replaced by S3 without touching reporting; the alerting sink can be replaced from CloudWatch to Prometheus without touching probes.
- **Testability by construction.** Because layers only depend downward, a layer can be tested by supplying fakes for the layer beneath it. No layer needs to mock its callers.
- **Predictable onboarding.** A new contributor reads the layer diagram and knows, within minutes, where a given class of change belongs.
- **Failure isolation.** Errors have a natural owner: a discovery error is a discovery-layer concern; a report-write failure is a storage-layer concern. Diagnostic reports can pinpoint the responsible layer.

---

## 4. Repository Top-Level Layout

For the full tree, see §1 of `ROADMAP.md`. Summarizing the top-level directories and their **single-sentence charter**:

| Path | Charter |
|---|---|
| `src/cloudprobe/` | All production Python. The only place runtime behavior is defined. |
| `tests/` | All test code, organized by test tier. Never imported by `src/`. |
| `configs/` | Declarative runtime inputs (YAML). Read by `src/`, never written by it at runtime. |
| `infra/` | Cloud infrastructure definitions (CloudFormation, IAM policies). Deployed by `scripts/`. |
| `docker/` | Container packaging: image, entrypoint, compose file. |
| `scripts/` | Operator-facing shell and Python utilities. Not called from `src/`. |
| `docs/` | Written documentation. The written half of the deliverable. |
| `assets/` | Diagrams, screenshots, and recordings referenced from docs/README. |
| `examples/` | Frozen sample outputs and inventories used for onboarding and regression fixtures. |
| `logs/` | Runtime log output. Empty in git; `.gitkeep` only. |
| `reports/` | Runtime report output. Empty in git; `.gitkeep` only. |
| `.github/` | Repository-level GitHub metadata: workflows, templates, ownership. |
| `.venv/` | Developer's local virtualenv. **Never committed.** |

---

## 5. Root Files

Each root-level file has a single, non-overlapping responsibility. If a proposed change would blur two of these files, split the change instead.

### `README.md`
- **Why it exists.** Front door of the project. First artifact a reviewer sees.
- **Owns.** Marketing summary, architecture diagram, quickstart (local and Docker), résumé claim → code table (copied in from `ROADMAP.md` §4 at Phase 8), links to `ROADMAP.md` and `docs/`.
- **Never contains.** Implementation details, long-form design rationale, or anything that duplicates `docs/`. It links; it does not restate.
- **Depended on by.** Every human reader; GitHub's project display.
- **Depends on.** `assets/` (for diagram and demo GIF), `docs/` (for deep links), `ROADMAP.md`.

### `ROADMAP.md`
- **Why it exists.** Authoritative sequencing of work from empty repo to `v0.1.0`. Living document.
- **Owns.** Phases, deliverables, exit criteria, milestones, résumé claim traceability, changelog.
- **Never contains.** Design detail (belongs in `docs/`) or implementation snippets.
- **Evolution.** Updated in the same PR that closes a phase; never batched.

### `LICENSE`
- **Why it exists.** Legal permission for reuse. Required for portfolio credibility.
- **Owns.** MIT license text.
- **Never contains.** Anything else. Do not append copyright manifests.

### `Makefile`
- **Why it exists.** Uniform interface for common developer actions. Removes shell-command variance across machines.
- **Owns.** `bootstrap`, `lint`, `format`, `typecheck`, `test`, `test-unit`, `test-integration`, `coverage`, `docker-build`, `deploy-lab`, `teardown`, `clean`.
- **Never contains.** Business logic. Targets are one-liners that shell out to scripts or tools.
- **Depends on.** `scripts/` for anything non-trivial, `pyproject.toml` for tool configuration.

### `pyproject.toml`
- **Why it exists.** PEP 621 canonical project metadata: package name, version, Python requirement, project metadata, tool configuration (ruff, mypy, pytest, coverage).
- **Owns.** Package identity, project metadata, tool configuration for development tools.
- **Never contains.** Environment-specific values (those belong in `.env.example` or `configs/`).
- **Depended on by.** `pip install`, `pytest`, `ruff`, `mypy`, `coverage`, IDEs.

### `requirements.txt` and `requirements-dev.txt`
- **Why they exist.** Frozen dependency pins for reproducible Docker builds and CI. `pyproject.toml` declares *what*; `requirements*.txt` pins *exactly which version*.
- **Owns.** Fully resolved dependency graph with pinned versions.
- **Never contains.** Unpinned specifiers or comments describing intent (that belongs in `pyproject.toml`).
- **Evolution.** Maintained alongside pyproject.toml and updated whenever project dependencies change.

### `.pre-commit-config.yaml`
- **Why it exists.** Local guardrail that catches format, lint, and type issues before CI does.
- **Owns.** ruff (lint + format), mypy, yamllint, whitespace normalizers.
- **Never contains.** Slow hooks that discourage developers from committing. If a check takes longer than a couple of seconds, move it to CI.

### `.env.example`
- **Why it exists.** Documents every environment variable CloudProbe reads. A contributor copies it to `.env` and fills in values.
- **Owns.** Variable names, safe example values, one-line descriptions.
- **Never contains.** Real credentials. Ever.

### `.dockerignore` and `.gitignore`
- **Why they exist.** Prevent secrets, virtualenvs, caches, and generated artifacts from being shipped in images or committed.
- **Owns.** Exclusion patterns.
- **Never contains.** Application logic.

---

## 6. `src/cloudprobe/` — Application Packages

This is the only directory whose contents run in production. Every module here is subject to the layering rules of §3 and the dependency-direction rule of §2.3.

**Top-level `src/cloudprobe/` files:**

### `__init__.py`
- Publishes the package version and re-exports a minimal public surface (`Target`, `ProbeResult`, `run_once`).
- Must not perform I/O, initialize logging, or read configuration at import time.

### `__main__.py`
- Enables `python -m cloudprobe`. Delegates immediately to `cli.py`.
- Contains no logic beyond the delegation.

### `cli.py` — **Presentation Layer**
- **Purpose.** Single command-line entrypoint. Parses arguments, resolves config paths, wires up the pipeline, translates exceptions into exit codes.
- **Expected commands (subcommands).** `probe` (one-off single-target probe), `run` (one full cycle of the pipeline), `discover` (dry-run inventory dump), `config validate`, `report render`, `healthcheck`.
- **Public interface.** The command-line surface itself.
- **Depends on.** All other packages, but only via their public interfaces.
- **Depended on by.** Users, Docker entrypoint, CI smoke tests.
- **Must never contain.** Business logic. If a subcommand needs more than a dozen lines, that logic belongs in the package it calls.
- **Evolution.** New subcommands are added by delegating to existing packages, not by adding capability to the CLI itself.

---

### 6.1 `config/` — **Configuration Layer**

- **Purpose.** Load YAML from `configs/`, validate it with pydantic, and expose immutable, typed configuration objects to every other layer.
- **Expected modules.**
  - `models.py` — pydantic definitions for `Target`, `Threshold`, `Schedule`, `ProbeConfig`, `AlertRule`, and the top-level `CloudProbeConfig` aggregate.
  - `loader.py` — YAML → model conversion with helpful error messages.
  - `defaults.py` — the constants any missing field should fall back to.
- **Public interface.** `load(path: Path) -> CloudProbeConfig`; the model classes themselves; a small set of well-named exceptions (`ConfigNotFoundError`, `ConfigValidationError`).
- **Internal responsibilities.** File I/O for YAML, schema validation, defaulting, and helpful error rendering.
- **Depends on.** Standard library, pydantic, PyYAML. **No internal dependencies.**
- **Depended on by.** Every other `cloudprobe` package.
- **Must never contain.** AWS calls, network I/O, probe execution logic, or knowledge of how targets will be used downstream.
- **Future extensibility.** New target fields, thresholds, or alert rule shapes are additive: add fields to the pydantic model, extend defaults, document them in `docs/configuration.md`. Old YAML remains loadable because unknown fields default rather than error where appropriate.

### 6.2 `discovery/` — **Discovery Layer**

- **Purpose.** Produce a canonical list of `Target` objects for a run by merging (a) static inventory from `configs/` and (b) dynamic inventory pulled from AWS.
- **Expected modules.**
  - `static.py` — inventory drawn from validated config.
  - `aws.py` — Boto3-backed EC2/VPC/subnet/SG discovery.
  - `merge.py` — deduplication and precedence rules when static and dynamic disagree.
- **Public interface.** `resolve(config: CloudProbeConfig, aws_client_factory) -> Inventory`.
- **Internal responsibilities.** Paginating EC2 describes, filtering by tag, converting AWS shapes into `Target` instances, applying merge policy.
- **Depends on.** `config/`, `util/`, Boto3 (through an injected client factory).
- **Depended on by.** `scheduler/`, `cli.py`.
- **Must never contain.** Probe execution, metric emission, or report generation. Discovery ends where reachability testing begins.
- **Future extensibility.** Additional cloud providers (Azure, GCP) each get a sibling module (`azure.py`, `gcp.py`) that produces the same `Target` shape. `merge.py` remains provider-agnostic.

### 6.3 `probes/` — **Probe Engine**

- **Purpose.** Execute reachability and protocol validation against a target and return a `ProbeResult`.
- **Expected modules.**
  - `base.py` — the `Probe` protocol/abstract base and `ProbeResult` dataclass.
  - `tcp.py` — TCP handshake, connect latency.
  - `icmp.py` — ping via subprocess with rtt/loss parsing.
  - `udp.py` — payload send with optional response validation.
  - `http.py` — status, TLS validity, TTFB.
  - `ssh.py` — thin adapter over `ssh/` for authentication + optional whitelisted remote command.
  - `registry.py` — probe-type → implementation mapping consumed by the CLI/scheduler.
- **Public interface.** `Probe.run(target: Target) -> ProbeResult`; the `ProbeResult` dataclass; `registry.get(probe_type)`.
- **Internal responsibilities.** Network I/O for its protocol, timeout handling, structured error classification (a TCP RST is not the same failure as a timeout).
- **Depends on.** `config/` (for `Target`), `util/` (retry, timing, logging), `ssh/` (for the SSH probe only), and its protocol's transport library.
- **Depended on by.** `scheduler/`, `alerting/` (consumes `ProbeResult`), `reporting/` (consumes `ProbeResult`), `cli.py`.
- **Must never contain.** AWS calls, report formatting, or threshold logic. A probe reports facts; it does not judge them.
- **Future extensibility.** A new probe type is a new module implementing the `Probe` protocol and a registry entry. No other package changes.

### 6.4 `ssh/` — **SSH Adapter**

- **Purpose.** Isolate all Paramiko usage behind a small, testable surface. The SSH probe and any future on-host diagnostic consumer talk to this package, never to Paramiko directly.
- **Expected modules.**
  - `client.py` — connection setup, key-based auth, timeout handling, connection pooling.
  - `commands.py` — the whitelist of remote commands CloudProbe is permitted to run.
- **Public interface.** `connect(target: Target) -> SSHSession`; `SSHSession.run(command: str) -> CommandResult` where `command` must appear in the whitelist.
- **Depends on.** `config/`, `util/`, Paramiko.
- **Depended on by.** `probes/ssh.py`, potentially future diagnostic collectors.
- **Must never contain.** Probe result construction (that belongs in `probes/ssh.py`), arbitrary command execution, or password-based auth.
- **Future extensibility.** Additional transports (SSM Session Manager, WinRM) become sibling modules exposing the same `Session` shape.

### 6.5 `metrics/` — **Metrics Emitter**

- **Purpose.** Publish `ProbeResult` measurements as CloudWatch custom metrics and, in parallel, as local JSON for offline analysis.
- **Expected modules.**
  - `cloudwatch.py` — `PutMetricData` batching (20-metric chunks), dimension construction, namespace management.
  - `local.py` — append-only JSONL emitter for local development and Docker runs without AWS credentials.
  - `dispatcher.py` — fans a `ProbeResult` out to every configured sink.
- **Public interface.** `emit(result: ProbeResult) -> None`; `flush() -> None`.
- **Depends on.** `config/`, `util/`, Boto3 (injected client).
- **Depended on by.** `scheduler/`, `alerting/` (for state before deciding to alarm).
- **Must never contain.** Threshold evaluation or alarm creation logic — those live in `alerting/`.
- **Future extensibility.** New sinks (Prometheus pushgateway, StatsD, OpenTelemetry) are new modules registered with the dispatcher.

### 6.6 `alerting/` — **Threshold & Alarm Engine**

- **Purpose.** Turn a stream of `ProbeResult` values into (a) breach decisions and (b) CloudWatch alarms bound to the correct metric and dimensions.
- **Expected modules.**
  - `rules.py` — evaluation of `AlertRule` against a `ProbeResult` (or a windowed sequence of them).
  - `binder.py` — creates/updates `PutMetricAlarm` definitions to match declared rules.
  - `sinks.py` — pluggable delivery: CloudWatch alarm state, SNS topic publish, local log record.
- **Public interface.** `evaluate(result: ProbeResult, rules: list[AlertRule]) -> list[Breach]`; `sync_alarms(rules, aws_client) -> None`.
- **Depends on.** `config/`, `metrics/`, `util/`, Boto3.
- **Depended on by.** `scheduler/`, `reporting/` (breach summaries appear in reports).
- **Must never contain.** Report rendering, probe I/O, or long-term storage. Alerting decides; storage remembers.
- **Future extensibility.** New notification channels (Slack, PagerDuty, email) are new sinks. New rule shapes (rate-of-change, quantile) are new evaluators in `rules.py`.

### 6.7 `reporting/` — **Diagnostic Reports**

- **Purpose.** Materialize the outcome of a run into artifacts a human or a machine can read: JSON, CSV, and self-contained HTML.
- **Expected modules.**
  - `model.py` — the `Report` aggregate (run metadata, inventory summary, per-probe results, breach list).
  - `renderers/json.py`, `renderers/csv.py`, `renderers/html.py` — one renderer per output format.
  - `assembler.py` — builds the `Report` from probe results and breach records.
- **Public interface.** `assemble(...) -> Report`; `render(report: Report, format: Literal["json","csv","html"]) -> bytes`.
- **Depends on.** `config/`, `probes/` (`ProbeResult`), `alerting/` (`Breach`), `util/`.
- **Depended on by.** `storage/`, `cli.py`, `scheduler/`.
- **Must never contain.** Persistence logic (belongs in `storage/`), threshold logic (belongs in `alerting/`), or AWS calls.
- **Future extensibility.** A new format (Markdown, PDF, Slack blocks) is a new renderer. The `Report` model does not change.

### 6.8 `storage/` — **Persistence**

- **Purpose.** Persist rendered reports and structured logs behind a common interface so that the caller doesn't care whether the destination is local disk or S3.
- **Expected modules.**
  - `base.py` — the `Storage` protocol (`put(key, bytes)`, `get(key)`, `list(prefix)`).
  - `filesystem.py` — writes under `reports/YYYY-MM-DD/`.
  - `s3.py` — writes to a configured S3 bucket with optional lifecycle policy.
- **Public interface.** The `Storage` protocol and a factory (`from_config(config) -> Storage`).
- **Depends on.** `config/`, `util/`, Boto3 (for S3).
- **Depended on by.** `reporting/`, `scheduler/`.
- **Must never contain.** Report formatting, alerting decisions, or business logic. It is a bag with a lid.
- **Future extensibility.** New backends (GCS, Azure Blob, Postgres) implement the `Storage` protocol; nothing above changes.

### 6.9 `scheduler/` — **Pipeline Driver**

- **Purpose.** Repeatedly execute the discovery → probe → metrics → alerting → reporting → storage pipeline on a cadence. Also supports one-shot mode for CI and Docker.
- **Expected modules.**
  - `runner.py` — the pipeline itself, expressible as a single function.
  - `cron.py` — APScheduler integration reading `configs/schedules.yaml`.
  - `oneshot.py` — single-pass invocation used by `--once` and Docker `oneshot` mode.
- **Public interface.** `run_once(config) -> RunSummary`; `start_scheduler(config) -> None`; `stop_scheduler() -> None`.
- **Depends on.** Every layer beneath it in §3.
- **Depended on by.** `cli.py`, Docker entrypoint.
- **Must never contain.** Probe implementations, metric formatting, or storage details. It orchestrates; it does not perform.
- **Future extensibility.** Alternative schedulers (host `cron` invoking `--once`, Kubernetes CronJob) already work by construction because `oneshot.py` is the whole contract.

### 6.10 `util/` — **Cross-Cutting Primitives**

- **Purpose.** House the small, dependency-free utilities that every layer legitimately needs: structured logging, retry-with-backoff, timing context managers.
- **Expected modules.**
  - `logging.py` — `structlog` configuration and logger factory.
  - `retry.py` — decorator with jitter, honoring per-call budget.
  - `timing.py` — `Timer` context manager producing `latency_ms` values used by every probe.
- **Public interface.** `get_logger(name)`, `retry(...)`, `Timer()`.
- **Depends on.** Standard library only.
- **Depended on by.** Every other `cloudprobe` package.
- **Must never contain.** Domain concepts (`Target`, `ProbeResult`, `Breach`), AWS calls, or anything that would import from another `cloudprobe` package. If a utility needs a domain concept, it is not a utility.
- **Evolution.** Grows slowly. When it grows quickly, it is a warning sign that a real package is trying to be born inside `util/`.

---

## 7. `tests/` — Four-Tier QA Layout

Tests are organized by **cost and confidence**, not by module. A test's tier tells the reader (and CI) how expensive it is, what it depends on, and what class of bug it catches.

```
tests/
├── unit/                 # Pure logic. No I/O. Milliseconds per test.
├── integration/          # Fakes and simulators (moto, pytest-socket, in-memory SSH).
├── regression/           # Golden-file comparisons. Report and CLI snapshots.
├── failure_scenarios/    # Parameterized simulations of hybrid-cloud failure modes.
├── conftest.py           # Shared fixtures.
└── fixtures/             # Static data: canned AWS responses, sample inventories.
```

### `tests/unit/`
- **Why it exists.** Fast, deterministic verification of pure logic (config validation, threshold arithmetic, report assembly, retry math).
- **Rule.** No network. No filesystem writes outside `tmp_path`. No `sleep` beyond microseconds. If a test needs Boto3, it belongs in `integration/`.
- **Runs.** On every pre-commit hook (fast subset) and on every PR.

### `tests/integration/`
- **Why it exists.** Verify that CloudProbe's adapters correctly speak to their external interfaces — but with those interfaces faked: `moto` for AWS, `pytest-socket` for TCP/UDP/ICMP, Paramiko's `MockTransport` for SSH.
- **Rule.** No real cloud accounts, no real network egress. If the CI runner is offline, integration tests still pass.
- **Runs.** On every PR.

### `tests/regression/`
- **Why it exists.** Freeze the exact shape of user-visible outputs: report JSON keys, CSV columns, HTML structure, CLI help text. Any accidental change to these surfaces breaks a golden-file test loudly.
- **Rule.** Golden files live under `tests/fixtures/golden/`. Updates require an explicit `--update-goldens` flag and a reviewer's sign-off.
- **Runs.** On every PR (fast) and in the nightly workflow (full).

### `tests/failure_scenarios/`
- **Why it exists.** Substantiate the résumé claim of "simulated hybrid cloud failure scenarios." Each scenario is a parameterized simulation that asserts CloudProbe **distinguishes** failure modes rather than lumping them together (e.g., SSH auth failure ≠ SSH timeout ≠ TCP RST).
- **Rule.** Every failure listed in `ROADMAP.md` Phase 7 has a corresponding test case here. Adding a new failure mode is a two-step change: add the case, then add the code that distinguishes it.
- **Runs.** In the nightly workflow.

### `tests/conftest.py` and `tests/fixtures/`
- **Why they exist.** Shared fixtures (fake AWS clients, sample configs, temp storage backends) live in one place so tests remain small and readable. `fixtures/` holds static data that would clutter test files if inlined.
- **Rule.** No behavior lives here — only setup. Assertions belong in test modules.

---

## 8. `configs/` — Runtime Configuration Surface

- **Why it exists.** Every runtime input CloudProbe accepts is a YAML file here. This is the "config" in "configuration-first."
- **Expected files.** `inventory.example.yaml`, `thresholds.yaml`, `schedules.yaml`, `logging.yaml`.
- **Owns.** Declarative descriptions of *what* CloudProbe should probe, *when*, and *with what tolerances*.
- **Never contains.** Secrets (they live in environment variables), Python, or executable content.
- **Depended on by.** `src/cloudprobe/config/` (at load time), Docker container (mounted at `/etc/cloudprobe/configs`).
- **Depends on.** Nothing. It is a leaf of the source tree.
- **Evolution.** New fields are added first here (with a concrete example), then reflected in `src/cloudprobe/config/models.py`, then documented in `docs/configuration.md`. This order is deliberate: the user-facing shape leads the implementation.

---

## 9. `infra/` — Infrastructure Definitions

- **Why it exists.** Cloud infrastructure that CloudProbe deploys (its own lab targets) or requires (its IAM policy) is defined as code, not clicked into the AWS console.
- **Subdirectories.**
  - `cloudformation/` — free-tier lab stack (VPC, 2 subnets, IGW, 2 t2.micro/t3.micro EC2, SG). CloudFormation chosen over Terraform to avoid a state backend requirement and to stay entirely within free-tier tooling.
  - `policies/` — least-privilege IAM JSON for the probe's execution role (`ec2:Describe*`, `cloudwatch:PutMetricData`, `cloudwatch:PutMetricAlarm`, `sns:Publish` scoped to one topic).
- **Owns.** Deployable, human-readable descriptions of cloud resources.
- **Never contains.** Application code, generated ARNs, or account-specific values (those go in parameter files not committed to git).
- **Depended on by.** `scripts/provision-lab.sh`, `scripts/teardown-lab.sh`, `docs/aws-setup.md`.
- **Evolution.** If future phases need a state backend or multi-region topology, a `terraform/` sibling can be added — but only if CloudFormation demonstrably cannot express the requirement.

---

## 10. `docker/` — Container Packaging

- **Why it exists.** CloudProbe must be runnable by a reviewer who has only Docker installed. This directory owns everything that makes that true.
- **Files.**
  - `Dockerfile` — multi-stage build. Builder installs deps into a venv; runtime stage copies venv + `src/` into a slim base and runs as non-root UID/GID 10001. Final image target: < 200 MB.
  - `entrypoint.sh` — respects `CLOUDPROBE_MODE=oneshot|scheduler`, forwards signals so `docker stop` is clean.
  - `docker-compose.yml` — single service (`cloudprobe`) mounting `configs/` and `reports/`. Intentionally minimal: no bundled LocalStack or Grafana, so the container image reflects what would actually deploy.
- **Owns.** Image definition, runtime contract (env vars, mount points, healthcheck).
- **Never contains.** Application code, tests, docs, or build artifacts. The image is a *packaging* of `src/` and `configs/`, not their home.
- **Depended on by.** `docs/docker.md`, `.github/workflows/docker.yml`, human operators.
- **Depends on.** `src/`, `requirements.txt`, `configs/` (at runtime via mount).
- **Evolution.** Additional runtime modes are added as `CLOUDPROBE_MODE` values, not as new images. Sidecar services (a Prometheus exporter, say) become new compose services rather than new base images.

---

## 11. `scripts/` — Operational Automation

- **Why it exists.** Operator-facing automation that a human runs at the terminal: environment setup, lab provisioning, teardown, demo-data generation.
- **Files.**
  - `bootstrap.sh` — creates virtualenv, installs deps, installs pre-commit hooks. Idempotent.
  - `provision-lab.sh` — deploys the CloudFormation stack in `infra/cloudformation/`.
  - `teardown-lab.sh` — destroys the stack. Mandatory counterpart to any provisioning script; enforces the free-tier discipline principle.
  - `seed-inventory.py` — generates `configs/inventory.example.yaml` with 50+ synthetic targets, making the résumé claim reproducible.
  - `run-probe-suite.sh` — one-shot pipeline execution used by CI and by human operators for a quick sanity run.
- **Owns.** Shell wrappers around AWS CLI, CloudFormation, and Python one-liners.
- **Never contains.** Business logic that belongs in `src/`. If a script grows past a screenful of shell, its guts should move into a Python module in `src/` and the script should call it.
- **Depended on by.** `Makefile`, humans, CI.
- **Depends on.** `infra/`, `src/`, external CLIs (`aws`, `python`).
- **Evolution.** New operator tasks become new scripts, not new subcommands of existing scripts. Each script does one thing.

---

## 12. `docs/` — Written Knowledge

Documentation is a first-class deliverable. Each file has a clear reader in mind and does not duplicate any other.

| File | For whom | Contains |
|---|---|---|
| `architecture.md` | Reviewers, new maintainers | C4-style diagrams (context / container / component), the layering rationale, the sub-minute detection design. |
| `networking-primer.md` | Readers whose networking is rusty | TCP handshake, ICMP semantics, UDP statelessness, SG vs NACL evaluation, DNS failure modes. Tied to the probes that exercise each concept. |
| `aws-setup.md` | Operators bringing up the lab | Account prep, IAM role creation, free-tier guardrails (billing alarm, budget), credential handling for local dev. |
| `configuration.md` | Operators tuning CloudProbe | Every YAML key: type, default, example, effect. Machine-readable enough that a linter could check it. |
| `probes.md` | Anyone writing or extending a probe | What each probe verifies, what it cannot verify, known false-positive modes, timeout guidance. |
| `alerting.md` | Operators wiring alarms | Threshold model, alarm state machine, SNS wiring, quiet hours. |
| `testing.md` | Contributors adding tests | The four-tier model, how to run each tier, how to add a new failure scenario, what "90%" is measured against. |
| `docker.md` | Users of the container | Image contract, env vars, volume mounts, compose walkthrough. |
| `operations.md` | On-call operators | Runbook: how to interpret a report, what each threshold means, how to silence a noisy probe, how to recover from common failures. |
| `contributing.md` | New contributors | Dev environment, code style, commit conventions, review checklist. |
| `project-structure.md` | Everyone (this document) | Design rationale for the repository layout itself. |

### `docs/adr/` — Architecture Decision Records

- **Why it exists.** Preserve *why* significant technical choices were made, not just *what* was chosen. An ADR is a short, dated Markdown file: context, decision, alternatives, consequences.
- **Expected entries.**
  - `0001-python.md` — why Python 3.11+ over Go/Rust/Node for this project.
  - `0002-cloudformation.md` — why CloudFormation over Terraform for lab IaC.
  - `0003-docker.md` — why a multi-stage image with a non-root user is the target contract.
  - `0004-testing.md` — why four tiers, why `moto`, why the 90% coverage floor.
- **Rule.** ADRs are immutable once merged. A change in decision becomes a *new* ADR that supersedes the old one; the old one is marked "Superseded by NNNN" but not deleted.
- **Evolution.** Numbered sequentially. Any architectural decision worth arguing about deserves an ADR.

---

## 13. `assets/` — Media & Diagrams

- **Why it exists.** README and docs reference images and recordings; those binaries need a home.
- **Contents.** `architecture.svg` (rendered from draw.io / excalidraw), `sample-report.png` (screenshot of an HTML diagnostic report), `demo.gif` (30-second asciinema recording of a probe run).
- **Never contains.** Source code, editable proprietary formats without an accompanying export, or anything not referenced from a Markdown file in the repo.
- **Rule.** Every asset must be linked from at least one document. Orphaned assets are removed in cleanup passes.

---

## 14. `examples/` — Reference Artifacts

- **Why it exists.** Frozen, human-readable examples of what CloudProbe produces and consumes. Serves onboarding ("this is what a report looks like") and doubles as fixture data for regression tests.
- **Contents.** `example_report.json`, `example_report.html`, `sample_inventory.yaml`.
- **Depended on by.** `README.md` (for links), `tests/regression/` (as golden inputs or comparison anchors), `docs/configuration.md` (for illustration).
- **Never contains.** Real credentials, real account IDs, or real customer data. Every value is synthetic.
- **Evolution.** When a public output format changes, its example here is regenerated in the same PR — not later.

---

## 15. `logs/` and `reports/` — Generated Artifacts

- **Why they exist.** CloudProbe writes runtime output somewhere. These directories are that somewhere by default.
- **Contents in git.** `.gitkeep` only. Nothing else is ever committed.
- **Contents at runtime.** Structured JSON logs (`logs/`), and dated diagnostic reports (`reports/YYYY-MM-DD/`).
- **Rule.** These directories are configuration-controlled — the operator may point CloudProbe at any writable path. The committed directories are convenience, not law.
- **Docker mapping.** Mounted from `/var/cloudprobe/reports` and `/var/cloudprobe/logs` respectively.

---

## 16. `.github/` — CI, Templates, Ownership

### `.github/workflows/`

| Workflow | Trigger | Purpose |
|---|---|---|
| `ci.yml` | Every PR, every push to `main` | Fast feedback: ruff → mypy → unit → integration → coverage upload. Matrix on Python 3.11/3.12. Target runtime under three minutes. |
| `docker.yml` | PRs touching `docker/`, `src/`, or `requirements*.txt` | Builds the image and runs a smoke test (`docker run --rm cloudprobe --version`). |
| `nightly.yml` | Scheduled | Full regression + failure-scenario suite. Opens a GitHub Issue if it fails, so overnight breakage is visible the next morning. |

Design intent: **PR feedback stays fast; the expensive work runs nightly.** Contributors are never blocked on tests that take 20 minutes.

### `.github/ISSUE_TEMPLATE/` and `PULL_REQUEST_TEMPLATE.md`
- Standardize what reporters and reviewers must provide: reproduction steps for bugs, checklist for PRs (tests added, docs updated, no new lint warnings, résumé-claim table still accurate).

### `.github/CODEOWNERS`
- Declares who reviews changes in each area. In a portfolio project this is a single author, but the file exists so multi-contributor evolution is a config change, not a process change.

---

## 17. Dependency Flow

The following diagram is the **enforceable form** of the layering in §3. Every arrow is an allowed import direction; the absence of an arrow is an intentional prohibition.

```
                       ┌─────────┐
                       │   CLI   │
                       └────┬────┘
                            │
                 ┌──────────┴──────────┐
                 │                     │
                 ▼                     ▼
          ┌───────────┐         ┌──────────────┐
          │ scheduler │────────▶│  reporting   │
          └─────┬─────┘         └──────┬───────┘
                │                      │
   ┌────────────┼───────────┐          │
   ▼            ▼           ▼          ▼
┌──────────┐┌────────┐┌──────────┐┌─────────┐
│discovery ││ probes ││ metrics  ││ storage │
└─────┬────┘└───┬────┘└─────┬────┘└────┬────┘
      │        │            │          │
      │        ▼            │          │
      │    ┌──────┐         │          │
      │    │ ssh  │         │          │
      │    └──┬───┘         │          │
      │       │             │          │
      ▼       ▼             ▼          ▼
  ┌─────────────────────────────────────────┐
  │              config  (models)           │
  └────────────────────┬────────────────────┘
                       ▼
  ┌─────────────────────────────────────────┐
  │        util  (no internal imports)      │
  └─────────────────────────────────────────┘
                       │
                       ▼
       ┌──────────────────────────────────┐
       │  External: Boto3, Paramiko, OS   │
       └──────────────────────────────────┘

                   alerting
              (consumes probes,
               emits via metrics,
               consumed by reporting)
```

Notes:

- `alerting/` is drawn separately because it consumes `probes/` output, uses `metrics/` for state, and is consumed by `reporting/`. It sits *between* those layers rather than beneath them.
- `util/` is depended on by everyone and depends on no `cloudprobe` package. This is enforced by a lint rule in CI.
- The CLI depends on `scheduler/` and `reporting/` directly for their public entrypoints; it never reaches around them to lower layers.
- Every arrow crosses **exactly one** layer boundary. Skipping layers (e.g., CLI reaching directly into `probes/`) is prohibited.

---

## 18. Future Scalability

The layout is designed so that the following common extensions can be absorbed without moving code between directories.

### New probe type
1. Add `src/cloudprobe/probes/<name>.py` implementing the `Probe` protocol.
2. Register it in `probes/registry.py`.
3. Extend `ProbeConfig` in `src/cloudprobe/config/models.py` if the probe accepts new parameters.
4. Document in `docs/probes.md`.
5. Add unit tests to `tests/unit/probes/` and a failure-mode case to `tests/failure_scenarios/`.

No other package changes. This is the definition of "does not require restructuring."

### New cloud provider
1. Add `src/cloudprobe/discovery/<provider>.py` producing the same `Target` shape.
2. Extend `discovery/merge.py` if precedence rules need refinement.
3. Add provider-specific IAM/permission docs under `infra/policies/`.
4. Add moto-equivalent fake to `tests/conftest.py` for that provider (or a hand-rolled fake if none exists).

`probes/`, `alerting/`, `reporting/`, and `storage/` remain unchanged — they operate on `Target` and `ProbeResult`, not on provider concepts.

### New storage backend
1. Add `src/cloudprobe/storage/<backend>.py` implementing the `Storage` protocol.
2. Extend the storage factory in `storage/__init__.py` to recognize the config key.
3. Document the config key in `docs/configuration.md`.

`reporting/` is unaffected because it depends on the protocol, not the implementation.

### New report format
1. Add `src/cloudprobe/reporting/renderers/<format>.py`.
2. Extend the `render()` dispatcher's `format` literal type.
3. Add a golden file under `tests/regression/`.

`Report` model, storage, and everything upstream are unaffected.

### New notification channel
1. Add `src/cloudprobe/alerting/sinks.py` entry (or a sibling module if the sink is large).
2. Extend `AlertRule` schema in `config/models.py` if new fields are needed (a Slack webhook URL, for example).
3. Document the sink in `docs/alerting.md`.

Threshold evaluation and alarm binding are unaffected.

### Scaling target count beyond current demo
The `Target` model and inventory loader are already streaming-friendly. Growth from 50 targets to 500 requires no code change, only YAML. Growth from 500 to 5000 would motivate:
- Concurrency in the scheduler (a worker pool per probe type).
- Batched CloudWatch emission (already implemented for the 20-metric-per-call limit).
- Optional inventory sourced from an external database (a new `discovery/` module).

None of these require moving code between packages.

---

## 19. Where Does New Code Belong? (Decision Tree)

When a contributor is unsure where to place a new piece of code, walking this list top-to-bottom yields the answer.

1. **Is it a shape or a validation rule for user-facing configuration?** → `src/cloudprobe/config/`.
2. **Does it turn AWS API responses into `Target` objects?** → `src/cloudprobe/discovery/`.
3. **Does it execute a network probe and return a `ProbeResult`?** → `src/cloudprobe/probes/`.
4. **Does it wrap Paramiko?** → `src/cloudprobe/ssh/`.
5. **Does it publish a measurement to a sink?** → `src/cloudprobe/metrics/`.
6. **Does it decide whether a measurement breaches a rule, or create/update an alarm?** → `src/cloudprobe/alerting/`.
7. **Does it turn results and breaches into a human- or machine-readable artifact?** → `src/cloudprobe/reporting/`.
8. **Does it persist an artifact somewhere?** → `src/cloudprobe/storage/`.
9. **Does it orchestrate the pipeline over time?** → `src/cloudprobe/scheduler/`.
10. **Is it a small, domain-agnostic utility usable by any of the above?** → `src/cloudprobe/util/`.
11. **Is it a user-facing command?** → `src/cloudprobe/cli.py`.
12. **Is it an operator command run from a terminal?** → `scripts/`.
13. **Is it a description of cloud infrastructure?** → `infra/`.
14. **Is it a description of runtime behavior in YAML?** → `configs/`.
15. **Is it a test?** → the tier that matches its cost: `tests/{unit,integration,regression,failure_scenarios}/`.
16. **Is it prose explaining any of the above?** → `docs/`.

If none of the above apply, the change may not belong in this repository — or this document needs an update. Either outcome is worth surfacing in review.

---

*Last reviewed against `ROADMAP.md` at the time of authorship. This document is updated in the same PR as any change that alters the repository shape.*
