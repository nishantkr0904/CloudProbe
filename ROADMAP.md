# CloudProbe — Development Roadmap

> **Project:** Hybrid Cloud Network Observability & QA Pipeline
> **Stack:** Python 3.11+, AWS (EC2, VPC, CloudWatch, IAM, S3), Boto3, Paramiko, Docker, pytest, Shell Scripting
> **Constraint:** AWS Free-Tier only. No paid services, no NAT Gateway, no ALB, no managed Prometheus.
> **Goal:** Ship a portfolio-grade repository that reads like a real infrastructure engineering project — every commit purposeful, every module documented, every claim on the résumé verifiable in code.

---

## 0. Guiding Principles

These principles govern every phase. Any commit that violates them should be rejected in review.

1. **Free-tier discipline.** t2.micro / t3.micro only, single region (`us-east-1`), no cross-AZ data transfer where avoidable, teardown scripts mandatory for every provisioning script.
2. **Reproducibility over cleverness.** A reviewer must be able to clone, `make bootstrap`, and run the complete test suite locally using `moto` without requiring an AWS account. Real AWS Free Tier is used only for demonstration and deployment validation.
3. **Every module has a contract.** Public functions carry type hints, docstrings (Google style), and a corresponding test module. No untested code path in `src/`.
4. **Config over code.** Instance inventories, thresholds, cron schedules, and alert rules live in YAML under `configs/` — never hardcoded.
5. **Observability of the observer.** CloudProbe logs its own runs to structured JSON, emits its own CloudWatch metrics, and fails loudly when it cannot reach a target.
6. **Commit hygiene.** Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`, `ci:`, `refactor:`). One logical change per commit. No commits that mix scaffolding with feature work.
7. **Documentation is a deliverable, not an afterthought.** Every phase closes with a docs update. A phase is not "done" until its `docs/` entry is written.

---

## 1. Repository Layout (Target End-State)

This is the shape the repository will hold by end of Phase 8. Directories are created empty when needed and populated in the phase that owns them.

```
CloudProbe/
├── README.md                    # Marketing-quality overview, architecture diagram, quickstart
├── ROADMAP.md                   # This file — living document, updated as phases close
├── LICENSE                      # MIT
├── Makefile                     # bootstrap, lint, test, docker-build, deploy-lab, teardown
├── pyproject.toml               # PEP 621 project metadata
├── requirements.txt             # Frozen deps for Docker layer caching
├── requirements-dev.txt         # pytest, ruff, mypy, moto, coverage, pre-commit
├── .pre-commit-config.yaml      # ruff + mypy + trailing-whitespace + yaml-lint
├── .env.example                 # All required env vars documented
├── .dockerignore
├── .gitignore
│
├── src/cloudprobe/
│   ├── __init__.py
│   ├── __main__.py              # `python -m cloudprobe` entrypoint
│   ├── cli.py                   # argparse/click CLI: probe, report, discover, alert
│   ├── config/                  # Config loading + validation (pydantic models)
│   ├── discovery/               # Boto3-driven inventory of EC2/VPC targets
│   ├── probes/                  # TCP, ICMP, UDP, HTTP, SSH reachability checks
│   ├── ssh/                     # Paramiko wrappers for on-host diagnostics
│   ├── metrics/                 # CloudWatch PutMetricData + local JSON emitters
│   ├── alerting/                # Threshold engine + SNS/CloudWatch alarm binders
│   ├── reporting/               # Structured diagnostic reports (JSON + HTML + CSV)
│   ├── scheduler/               # APScheduler-based cron; also runs as one-shot
│   ├── storage/                 # S3 + local filesystem persistence adapters
│   └── util/                    # Logging, retries, backoff, timing decorators
│
├── tests/
│   ├── unit/                    # Pure-function tests, no network
│   ├── integration/             # moto-backed tests for Boto3 flows
│   ├── regression/              # Golden-output tests for report formats + CLI
│   ├── failure_scenarios/       # Simulated hybrid-cloud failure suites
│   ├── conftest.py              # Shared fixtures: fake AWS, fake SSH, fake sockets
│   └── fixtures/                # YAML inventories, canned CloudWatch responses
│
├── configs/
│   ├── inventory.example.yaml   # 50+ synthetic EC2/VPC targets for demo
│   ├── thresholds.yaml          # Latency, packet-loss, TCP handshake budgets
│   ├── schedules.yaml           # Per-probe cron expressions
│   └── logging.yaml             # Python logging dictConfig
│
├── infra/
│   ├── cloudformation/          # Free-tier VPC + 2 EC2 + SG stack
│   └── policies/                # Least-privilege IAM JSON for the probe role
│
├── docker/
│   ├── Dockerfile               # Multi-stage: builder → slim runtime
│   ├── docker-compose.yml       # cloudprobe
│   └── entrypoint.sh
│
├── scripts/
│   ├── bootstrap.sh             # Local dev env: venv, deps, pre-commit install
│   ├── provision-lab.sh         # Spin up free-tier lab via CloudFormation
│   ├── teardown-lab.sh          # Destroy everything the provisioner created
│   ├── seed-inventory.py        # Generate 50+ synthetic targets for demo mode
│   └── run-probe-suite.sh       # One-shot pipeline execution for CI
│
├── docs/
│   ├── architecture.md          # C4-style diagrams (context / container / component)
│   ├── networking-primer.md     # TCP/UDP/ICMP refresher tied to probe design
│   ├── aws-setup.md             # Account prep, IAM, free-tier guardrails
│   ├── configuration.md         # Every YAML key documented with defaults
│   ├── probes.md                # How each probe works, false-positive modes
│   ├── alerting.md              # Threshold model, CloudWatch alarm mapping
│   ├── testing.md               # How to run each test tier locally + in CI
│   ├── docker.md                # Container design, entrypoint contract
│   ├── operations.md            # Runbook: common failures + recovery
│   └── contributing.md
│   └── adr/
│       ├── 0001-python.md
│       ├── 0002-cloudformation.md
│       ├── 0003-docker.md
│       └── 0004-testing.md
│
├── assets/
│   ├── architecture.svg         # Rendered diagram for README
│   ├── sample-report.png        # Screenshot of an HTML diagnostic report
│   └── demo.gif                 # 30-second terminal capture of a probe run
│
├── logs/                        # .gitkeep only — runtime output
├── reports/                     # .gitkeep only — generated diagnostics
├── examples/
│   ├── example_report.json
│   ├── example_report.html
│   └── sample_inventory.yaml
│
└── .github/
    ├── workflows/
    │   ├── ci.yml               # lint → typecheck → unit → integration → coverage
    │   ├── docker.yml           # build + smoke-test image on PR
    │   └── nightly.yml          # Full regression + failure-scenario suite
    ├── ISSUE_TEMPLATE/
    ├── PULL_REQUEST_TEMPLATE.md
    └── CODEOWNERS
```

---

## 2. Phase Plan

Each phase closes with: (a) code merged, (b) tests passing, (c) `docs/` entry written, (d) `ROADMAP.md` phase checkbox ticked, (e) a demoable artifact (log, report, screenshot, or GIF).

---

### Phase 1 — Foundation & Developer Ergonomics
**Goal:** A contributor can clone the repo and run `make bootstrap && make test` within five minutes on a clean machine.

**Deliverables**
- `pyproject.toml` containing PEP 621 project metadata and Python version constraints.
- `requirements.txt` for runtime dependencies.
- `requirements-dev.txt` for development and testing dependencies managed with pip.
- `Makefile` targets: `bootstrap`, `lint`, `format`, `typecheck`, `test`, `test-unit`, `test-integration`, `coverage`, `clean`.
- `scripts/bootstrap.sh` — creates venv, installs deps, installs pre-commit hooks.
- `.pre-commit-config.yaml` — ruff (lint + format), mypy, yamllint, end-of-file-fixer.
- `.env.example` enumerating every env var (`AWS_REGION`, `CLOUDPROBE_CONFIG_DIR`, `LOG_LEVEL`, ...).
- Fleshed-out `.gitignore` (Python + macOS + AWS creds + local report output).
- MIT `LICENSE` populated.
- `src/cloudprobe/__init__.py` + a stub CLI that prints version — proves the package installs.

**Documentation**
- `docs/contributing.md` (dev environment, style, commit conventions).
- README skeleton with badges (CI status, coverage, license, Python version).

**Exit Criteria**
- `make bootstrap && make lint && make test` passes on a clean checkout with zero real tests.
- Pre-commit blocks a deliberately malformed commit.

---

### Phase 2 — Configuration & Inventory Model
**Goal:** The system's data model is defined before behavior — every probe consumes a validated `Target` object, never raw YAML.

**Deliverables**
- `src/cloudprobe/config/` — pydantic models for `Target`, `Threshold`, `Schedule`, `ProbeConfig`, `AlertRule`.
- Loader that reads `configs/*.yaml`, validates, and raises helpful errors on missing keys.
- `configs/inventory.example.yaml` — 50+ synthetic targets across 3 VPCs, mixed public/private, mixed OS.
- `scripts/seed-inventory.py` — regenerates the example inventory from a template (so the 50+ number is reproducible).
- `configs/thresholds.yaml`, `configs/schedules.yaml`, `configs/logging.yaml` populated with sane defaults.
- Unit tests: happy-path load, each validation failure mode, round-trip YAML.

**Documentation**
- `docs/configuration.md` — every key, type, default, and example.

**Exit Criteria**
- `python -m cloudprobe config validate configs/inventory.example.yaml` returns exit 0 and prints a target summary.
- 100% branch coverage on the config module.

---

### Phase 3 — Probe Engine (Core Feature)
**Goal:** The probes described in the résumé bullet actually exist, are individually tested, and produce structured results.

**Deliverables**
- `src/cloudprobe/probes/` with one module per probe type:
  - `tcp.py` — socket handshake with configurable timeout, records connect latency.
  - `icmp.py` — ping via subprocess (portable) with rtt + loss parsing.
  - `udp.py` — payload send + optional response validation (DNS, NTP shapes).
  - `http.py` — status code, TLS validity, TTFB.
  - `ssh.py` — Paramiko-based auth check + optional remote command.
- Common `ProbeResult` dataclass: target, probe_type, success, latency_ms, error_class, timestamp, raw.
- `src/cloudprobe/ssh/` — Paramiko wrapper with key-based auth, connection pooling, safe command whitelist.
- `src/cloudprobe/util/` — retry-with-backoff decorator, timing context manager, structured logger factory.
- Unit tests for every probe using `pytest-socket` + fakes; no real network calls in the unit tier.

**Documentation**
- `docs/probes.md` — for each probe: what it verifies, what it *cannot* verify, known false-positive modes, timeout guidance.
- `docs/networking-primer.md` — TCP handshake, ICMP semantics, UDP statelessness, why each matters for cloud health.

**Exit Criteria**
- All five probes runnable via `python -m cloudprobe probe --target <t> --type tcp`.
- Unit coverage ≥ 95% on `probes/`.

---

### Phase 4 — AWS Integration (Discovery, Metrics, Alerting)
**Goal:** Boto3 code paths that touch EC2, VPC, and CloudWatch — tested with `moto`, runnable against a real free-tier account.

**Deliverables**
- `src/cloudprobe/discovery/` — `describe_instances`, `describe_vpcs`, `describe_subnets`, `describe_security_groups`; converts AWS responses into `Target` objects; merges with static inventory.
- `src/cloudprobe/metrics/` — `put_metric_data` batching (20-metric chunks), custom namespace `CloudProbe/Network`, dimensions for VPC/subnet/instance.
- `src/cloudprobe/alerting/` — threshold engine that compares `ProbeResult` streams against `AlertRule` and emits CloudWatch alarms via `put_metric_alarm`; optional SNS topic binding.
- `infra/policies/probe-role.json` — least-privilege IAM (ec2:Describe*, cloudwatch:PutMetricData, cloudwatch:PutMetricAlarm, sns:Publish on one topic ARN).
- Integration tests using `moto` for every AWS interaction.

**Documentation**
- `docs/aws-setup.md` — account prep, creating the probe IAM user/role, free-tier guardrails (billing alarm, budget), how to obtain credentials for local dev.
- `docs/alerting.md` — threshold model, alarm state machine, SNS wiring.

**Exit Criteria**
- `pytest tests/integration/ -k aws` runs green with zero real AWS credentials (moto only).
- A dry-run mode logs every intended API call without executing it.

---

### Phase 5 — Scheduler, Reporting & Persistence
**Goal:** The pipeline runs itself on a schedule and produces artifacts a human can read.

**Deliverables**
- `src/cloudprobe/scheduler/` — APScheduler-backed runner that reads `configs/schedules.yaml`; also exposes `--once` for CI/Docker one-shot mode.
- `src/cloudprobe/reporting/` — emits three formats per run: JSON (machine), CSV (spreadsheet), HTML (human, self-contained, no external CSS).
- Report content: run metadata, target inventory summary, per-probe results table, failure detail section, threshold breach list, CloudWatch metric URLs.
- `src/cloudprobe/storage/` — pluggable persistence: local `reports/` directory (default) or S3 bucket (opt-in via config).
- Sub-minute anomaly detection: threshold engine evaluates results as they stream in, not after batch completion — must be demonstrated by a test that measures detection latency.

**Documentation**
- `docs/operations.md` — runbook: how to interpret a report, what each threshold means, how to silence a noisy probe.
- Add "How CloudProbe achieves sub-minute anomaly detection" section to `architecture.md`.

**Exit Criteria**
- `python -m cloudprobe run --once --config configs/inventory.example.yaml` produces JSON + CSV + HTML under `reports/YYYY-MM-DD/`.
- A regression test asserts detection latency < 60s on a simulated stream.

---

### Phase 6 — Dockerization
**Goal:** The pipeline ships as a container that a reviewer can `docker run` without a Python toolchain.

**Deliverables**
- `docker/Dockerfile` — multi-stage: `python:3.11-slim` builder installs deps into a venv, runtime stage copies venv + `src/` only. Non-root user. Final image < 200 MB.
- `docker/entrypoint.sh` — respects `CLOUDPROBE_MODE=oneshot|scheduler`, forwards signals for clean shutdown.
- `docker/docker-compose.yml` — services: `cloudprobe`.
- Health check baked in: `HEALTHCHECK` runs `python -m cloudprobe healthcheck` (validates config + AWS reachability).
- `.dockerignore` scoped tightly.

**Documentation**
- `docs/docker.md` — image contract, env vars, volume mounts (`/etc/cloudprobe/configs`, `/var/cloudprobe/reports`), compose walkthrough.

**Exit Criteria**
- `docker build` produces a working image; `docker run cloudprobe --once` succeeds using a local configuration. AWS functionality is validated through moto during testing and against an AWS Free Tier environment during demonstrations.
- Image passes `docker scout quickview` (or `trivy`) with no HIGH/CRITICAL vulns.

---

### Phase 7 — QA Lifecycle & Coverage Targets
**Goal:** Substantiate the "90%+ integration and regression coverage" bullet with real, categorized tests.

**Deliverables**
- **Unit tier** (`tests/unit/`) — pure logic, no I/O, milliseconds per test.
- **Integration tier** (`tests/integration/`) — Boto3 via `moto`, SSH via fake transport, sockets via `pytest-socket` fakes. Full pipeline wired end-to-end against fakes.
- **Regression tier** (`tests/regression/`) — golden-file comparisons on JSON/CSV report output; CLI snapshot tests; config-migration compatibility tests.
- **Failure-scenario suite** (`tests/failure_scenarios/`) — parameterized simulations:
  - Instance unreachable (SG blackhole).
  - Partial VPC partition (subnet A ↔ subnet B fails, both ↔ subnet C succeeds).
  - CloudWatch API throttling (429 storm).
  - SSH auth failure vs SSH connection reset vs SSH timeout — must be distinguished in reports.
  - Clock skew on target host.
  - DNS resolution failure vs TCP RST vs ICMP unreachable — must be distinguished.
- Coverage enforced in CI: fail build if combined line coverage < 90%.
- `coverage.xml` uploaded as CI artifact; badge in README.

**Documentation**
- `docs/testing.md` — the tier model, how to run each tier, how to add a new failure scenario, what "90%" is measured against.

**Exit Criteria**
- `make coverage` reports ≥ 90% and CI enforces the floor.
- Failure-scenario suite runs in nightly workflow and produces a summary comment on the last commit.

---

### Phase 8 — CI, Automation & Release Polish
**Goal:** The repository looks and behaves like a maintained project.

**Deliverables**
- `.github/workflows/ci.yml` — matrix on Python 3.11/3.12: ruff → mypy → unit → integration → coverage upload. Runs on every PR and push to `main`.
- `.github/workflows/docker.yml` — builds the image on PRs that touch `docker/` or `src/`, runs a smoke test (`docker run --rm cloudprobe --version`).
- `.github/workflows/nightly.yml` — full regression + failure scenarios, opens a GitHub Issue on failure.
- Dependabot config for pip + docker + GH Actions.
- CODEOWNERS, PR template (checklist: tests added, docs updated, no new lint warnings), issue templates.
- `README.md` finalized: hero diagram, "What it does" (three bullets mapping to the résumé claims), quickstart (local + docker), architecture summary, screenshots, tech stack table, roadmap link.
- `assets/architecture.svg` (draw.io / excalidraw export) + `assets/demo.gif` (asciinema → gif).
- Git tag `v0.1.0` and a GitHub Release with changelog.

**Documentation**
- Every `docs/` file reviewed for staleness; broken links checked.
- Add a "How the résumé bullets map to the code" appendix to `README.md` — reviewers should be able to click from claim to implementation.

**Exit Criteria**
- Green CI badge, green coverage badge, green Docker badge.
- A stranger with only the README can run the demo in under ten minutes.

---

## 3. Cross-Cutting Concerns

These are not phases — they are constraints applied throughout.

### Networking Concepts Covered (design intent for `docs/networking-primer.md`)
- OSI L3/L4 distinction as reflected in probe choice.
- TCP three-way handshake vs SYN-only "port open" checks.
- ICMP echo semantics, why cloud SGs often drop it, and how the tool distinguishes "blocked" from "down".
- UDP statelessness and the response-validation pattern used by the UDP probe.
- Cloud-specific: security group vs NACL evaluation order, ephemeral port ranges, VPC route table hops, cross-AZ vs cross-VPC latency envelopes.
- DNS resolution failure modes (NXDOMAIN, SERVFAIL, timeout) distinguished from transport failure.

### AWS Services Used (all free-tier)
| Service | Usage | Free-tier posture |
|---|---|---|
| EC2 | Lab targets (t2.micro/t3.micro) | ≤ 2 instances active in demo; teardown script mandatory |
| VPC | Custom VPC, 2 subnets, 1 IGW | No NAT Gateway (uses public subnet + SG for lab) |
| CloudWatch | Custom metrics, alarms, log groups | Stay under 10 custom metrics, 10 alarms |
| IAM | Least-privilege role/user for probe | Documented in `infra/policies/` |
| SNS | Optional alert delivery | One topic, email subscription |
| S3 | Optional report archive | One bucket, lifecycle rule to expire objects at 30 days |

### Automation Features
- Scheduled runs (APScheduler in-process; documented alternative: host cron invoking `--once`).
- Auto-discovery of EC2 targets by tag filter (opt-in per config).
- Auto-provisioning of the lab environment via CloudFormation (`scripts/provision-lab.sh`).
- Auto-teardown (`scripts/teardown-lab.sh`) — idempotent, safe to re-run.
- Auto-generated inventory for demo (`scripts/seed-inventory.py`) so the "50+ targets" claim is reproducible.
- Auto-report archival to S3 (opt-in).

### QA Plan Summary
- Four test tiers (unit / integration / regression / failure-scenarios), each with a distinct pytest marker.
- ≥ 90% combined coverage, enforced in CI.
- Golden-file regression on all report formats.
- Every failure scenario listed in Phase 7 has a dedicated parameterized test case.
- `moto` for AWS, `pytest-socket` for network isolation, `paramiko`'s in-memory transport for SSH.

### Docker Strategy
- Multi-stage build; runtime image contains no build toolchain.
- Non-root UID/GID 10001.
- Config injected via bind mount; secrets via env vars only (never baked in).
- Two modes via `CLOUDPROBE_MODE`: `oneshot` (exit after one probe cycle) and `scheduler` (long-running).
- `HEALTHCHECK` distinguishes "container up" from "pipeline healthy".

### CI Strategy
- Fast feedback on PRs (lint + unit + integration, < 3 min target).
- Nightly heavy suite (regression + failure scenarios).
- Coverage gate on PRs; nightly failure opens an issue.
- Docker build gate on any change touching `docker/`, `src/`, or `requirements*.txt`.

---

## 4. Résumé Claim → Code Traceability

This section is copied into `README.md` at Phase 8. Every claim must resolve to a specific module or test — no unbacked marketing.

| Claim | Substantiated by |
|---|---|
| "Automated hybrid cloud network health monitoring system" | `src/cloudprobe/scheduler/` + `docker/docker-compose.yml` |
| "50+ AWS EC2 instances and VPC subnets" | `configs/inventory.example.yaml` + `scripts/seed-inventory.py` |
| "Real-time deep observability" | `src/cloudprobe/metrics/` + Phase 5 sub-minute detection test |
| "Boto3 + Paramiko scheduled checks" | `src/cloudprobe/discovery/` + `src/cloudprobe/ssh/` |
| "TCP/IP, ICMP, UDP validation" | `src/cloudprobe/probes/{tcp,icmp,udp}.py` |
| "CloudWatch-integrated threshold alerting" | `src/cloudprobe/alerting/` + `infra/policies/probe-role.json` |
| "Structured diagnostic reports" | `src/cloudprobe/reporting/` + `reports/` samples |
| "Sub-minute anomaly detection latency" | `tests/regression/test_detection_latency.py` |
| "Docker-provisioned pipeline" | `docker/Dockerfile` + `docker/docker-compose.yml` |
| "Pytest-driven QA lifecycle: unit, integration, regression" | `tests/{unit,integration,regression,failure_scenarios}/` |
| "90%+ test coverage" | `make coverage` + CI gate in `.github/workflows/ci.yml` |
| "Simulated hybrid cloud failure scenarios" | `tests/failure_scenarios/` |

---

## 5. Milestones & Sequencing

| Milestone | Closes phases | Demoable artifact |
|---|---|---|
| **M1: Walking skeleton** | 1, 2 | `python -m cloudprobe config validate` prints 50+ targets |
| **M2: Probes work locally** | 3 | Log showing five probe types run against `localhost` and a public host |
| **M3: AWS-aware** | 4 | moto-backed test log showing discovery → metric emission → alarm creation |
| **M4: Full pipeline** | 5 | HTML report screenshot in `assets/` |
| **M5: Containerized** | 6 | `docker run` recording |
| **M6: QA-hardened** | 7 | Coverage report ≥ 90%, failure-scenario matrix output |
| **M7: Release** | 8 | `v0.1.0` tag, polished README, green badges |

---

## 6. Definition of Done (Repository-Level)

The project is "done" for portfolio purposes when **all** of the following are true:

- [ ] All eight phases checked off in this document.
- [ ] `main` is green on CI, Docker, and nightly workflows.
- [ ] Coverage ≥ 90%, enforced by CI.
- [ ] README quickstart works on a machine that has only Docker installed.
- [ ] Every `docs/` file exists and is linked from README.
- [ ] `assets/architecture.svg` and `assets/demo.gif` are current.
- [ ] `v0.1.0` released with a written changelog.
- [ ] Every résumé claim in Section 4 links to code or tests that back it.
- [ ] `scripts/teardown-lab.sh` verified against a real AWS account — no orphaned resources, no surprise bill.

---

## 7. Living Document Protocol

- This file is edited in the **same PR** that closes a phase. Never batch roadmap updates.
- Add a `## Changelog` entry at the bottom when a phase closes: date, phase number, PR link.
- If reality diverges from the plan, update the plan — do not let the roadmap drift into fiction.

---

## Changelog

_(entries appended as phases close)_
