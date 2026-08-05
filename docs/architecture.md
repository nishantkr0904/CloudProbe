# CloudProbe — System Architecture

> **Status:** Authoritative runtime reference.
> **Scope:** How CloudProbe *behaves* at runtime — what runs, in what order, over what network, with what failure semantics. For *where files live*, see [`docs/project-structure.md`](project-structure.md). For *what to build and when*, see [`ROADMAP.md`](../ROADMAP.md).
> **Audience.** Engineers extending CloudProbe, reviewers evaluating its design, and interviewers probing its trade-offs.
> **Non-goal.** This document does not describe repository layout, code organization, or implementation snippets — those belong to `project-structure.md` and the ADRs.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [High-Level System Diagram](#2-high-level-system-diagram)
3. [AWS Architecture](#3-aws-architecture)
4. [End-to-End Execution Flow](#4-end-to-end-execution-flow)
5. [Network Flow](#5-network-flow)
6. [Discovery Flow](#6-discovery-flow)
7. [Monitoring Flow](#7-monitoring-flow)
8. [Alert Flow](#8-alert-flow)
9. [Reporting Flow](#9-reporting-flow)
10. [Testing Architecture](#10-testing-architecture)
11. [Deployment Flow](#11-deployment-flow)
12. [Failure Handling](#12-failure-handling)
13. [Security Architecture](#13-security-architecture)
14. [Scalability Strategy](#14-scalability-strategy)
15. [Architecture Decisions](#15-architecture-decisions)

---

## 1. System Overview

### 1.1 Goals

CloudProbe is a **hybrid cloud network observability and QA pipeline**. Its purpose is to give operators continuous, evidence-based answers to three questions, without human console inspection:

- **Is every declared target reachable** over the protocols it is expected to serve?
- **Which failure modes** are occurring, distinguished from each other with enough precision to point at a fault domain (network, security group, host, application)?
- **Has any signal breached a declared threshold**, and if so, has an alarm been raised through the right channel?

The system is designed to run unattended, produce artifacts a human can read after the fact, and stay within AWS Free Tier limits for demonstration.

### 1.2 Functional Responsibilities

CloudProbe as a running system:

- **Loads a declarative contract** describing the fleet: targets, thresholds, schedules, alert rules.
- **Discovers targets** from static configuration and, optionally, from live AWS APIs.
- **Executes reachability probes** — TCP, ICMP, UDP, HTTP, SSH — against every target on a cadence.
- **Classifies outcomes** into structured `ProbeResult` records that distinguish failure modes rather than collapsing them into "up/down."
- **Publishes measurements** to CloudWatch as custom metrics and, in parallel, to local structured logs.
- **Evaluates thresholds** against the result stream in real time (sub-minute detection latency).
- **Manages CloudWatch alarms** — creates, updates, and binds them to the correct metrics and dimensions.
- **Dispatches notifications** through SNS (or a locally-logged sink when SNS is not configured).
- **Renders diagnostic reports** in JSON, CSV, and self-contained HTML.
- **Persists reports** to a local filesystem or, optionally, to S3.

### 1.3 Non-Functional Requirements

| Requirement | Target | Enforcement |
|---|---|---|
| Anomaly detection latency | < 60 s from probe execution to threshold breach signal | Regression test asserts the bound |
| Free-Tier compliance | Zero paid AWS services in the default deployment | CloudFormation stack review; teardown script mandatory |
| Reproducibility | `make bootstrap` on a clean machine produces a passing test run | CI matrix on Python 3.11/3.12 |
| Testability without AWS | Full pipeline exercisable via `moto` and in-memory fakes | Integration tier runs offline |
| Container footprint | Image < 200 MB, non-root user, no HIGH/CRITICAL vulns | Docker workflow smoke-tests every PR |
| Coverage floor | ≥ 90% combined line coverage | CI hard gate |
| Observability of the observer | CloudProbe emits structured logs and its own metrics; fails loudly on unreachable targets | Log schema validation in regression tests |
| Configuration-first | Adding a target does not require a code change | Enforced by design — see `project-structure.md` §2.4 |

### 1.4 What CloudProbe Is Not

To keep the scope honest:

- **Not a monitoring UI.** CloudProbe emits into CloudWatch and produces reports. It does not run a dashboard server.
- **Not a metrics database.** Retention is CloudWatch's responsibility; long-term storage of reports is S3's.
- **Not an incident manager.** It raises alarms; it does not on-call anyone or run playbooks.
- **Not multi-tenant.** One deployment, one fleet, one set of credentials.

These non-scopes are load-bearing: they explain why the architecture has no web tier, no database, and no auth layer.

---

## 2. High-Level System Diagram

The following diagram shows major runtime components and the data that moves between them. It is the diagram to keep in mind while reading every subsequent section.

```
                     ┌────────────────────────────────┐
                     │        Operator / CI           │
                     │  (docker run, python -m ...)   │
                     └───────────────┬────────────────┘
                                     │  argv, env, config path
                                     ▼
                     ┌────────────────────────────────┐
                     │       CLI  (presentation)      │
                     └───────────────┬────────────────┘
                                     │  CloudProbeConfig
                                     ▼
                     ┌────────────────────────────────┐
                     │   Scheduler / Pipeline Driver  │
                     │  (oneshot or APScheduler cron) │
                     └──┬───────────┬─────────────────┘
                        │           │
              targets   │           │  ProbeResult stream
                        ▼           │
       ┌─────────────────────┐      │
       │      Discovery      │      │
       │  static + AWS merge │      │
       └──────────┬──────────┘      │
                  │ Inventory       │
                  ▼                 ▼
       ┌─────────────────────────────────────┐
       │           Probe Engine              │
       │  TCP · ICMP · UDP · HTTP · SSH      │
       └───┬──────────────┬──────────────┬───┘
           │              │              │
   metrics │       breach │        result│
           ▼              ▼              ▼
   ┌───────────┐   ┌────────────┐   ┌──────────────┐
   │  Metrics  │──▶│  Alerting  │──▶│   Reporting  │
   │ dispatcher│   │  threshold │   │ JSON/CSV/HTML│
   └─────┬─────┘   │   engine   │   └──────┬───────┘
         │         └─────┬──────┘          │
         │               │                 │
         ▼               ▼                 ▼
   ┌──────────┐   ┌──────────────┐   ┌───────────────┐
   │CloudWatch│   │ CloudWatch   │   │    Storage    │
   │ metrics  │   │  Alarms +    │   │ local FS / S3 │
   │          │   │    SNS       │   │               │
   └──────────┘   └──────────────┘   └───────────────┘
                                              │
                                              ▼
                                    ┌──────────────────┐
                                    │   Operator /     │
                                    │  Reviewer reads  │
                                    │  reports/*.html  │
                                    └──────────────────┘
```

**Data movements at a glance:**

- **Config → Scheduler.** Immutable, typed configuration flows from disk once per run.
- **Discovery → Scheduler.** A canonical `Inventory` of `Target` records.
- **Scheduler → Probe Engine.** One `Target` at a time (or in parallel batches), per configured probe type.
- **Probe Engine → Metrics / Alerting / Reporting.** A stream of `ProbeResult` values fanned out to three consumers.
- **Alerting → CloudWatch / SNS.** Alarm state transitions and notifications.
- **Reporting → Storage.** Serialized artifacts persisted for later review.

---

## 3. AWS Architecture

CloudProbe is designed for AWS Free Tier from day one. Every service listed here has a Free-Tier posture; anything that would push the deployment out of Free Tier is either optional or explicitly out of scope.

### 3.1 Component Inventory

```
                             AWS us-east-1
    ┌─────────────────────────────────────────────────────────────┐
    │                                                             │
    │   ┌───────────────────────────────────────────────┐         │
    │   │              CloudProbe VPC                   │         │
    │   │            CIDR: 10.20.0.0/16                 │         │
    │   │                                               │         │
    │   │  ┌───────────────┐        ┌───────────────┐   │         │
    │   │  │ Public Subnet │        │Public Subnet  │   │         │
    │   │  │ 10.20.1.0/24  │        │10.20.2.0/24   │   │         │
    │   │  │   (AZ-a)      │        │  (AZ-b)       │   │         │
    │   │  │               │        │               │   │         │
    │   │  │  ┌─────────┐  │        │  ┌─────────┐  │   │         │
    │   │  │  │  EC2 A  │  │        │  │  EC2 B  │  │   │         │
    │   │  │  │t2.micro │  │        │  │t2.micro │  │   │         │
    │   │  │  └─────────┘  │        │  └─────────┘  │   │         │
    │   │  └───────┬───────┘        └───────┬───────┘   │         │
    │   │          │                        │           │         │
    │   │          └────────┬───────────────┘           │         │
    │   │                   │                           │         │
    │   │             ┌─────┴─────┐                     │         │
    │   │             │    IGW    │                     │         │
    │   │             └─────┬─────┘                     │         │
    │   └───────────────────┼───────────────────────────┘         │
    │                       │                                     │
    │                       ▼                                     │
    │           ┌───────────────────────┐                         │
    │           │  Public Internet /    │                         │
    │           │  CloudProbe runtime   │                         │
    │           │  (local or container) │                         │
    │           └───────────┬───────────┘                         │
    │                       │                                     │
    │             ┌─────────┴─────────┐                           │
    │             │                   │                           │
    │             ▼                   ▼                           │
    │      ┌─────────────┐    ┌───────────────┐                   │
    │      │ CloudWatch  │    │      SNS      │                   │
    │      │  metrics +  │───▶│  topic +      │                   │
    │      │   alarms    │    │  email sub    │                   │
    │      └─────────────┘    └───────────────┘                   │
    │                                                             │
    │      ┌─────────────┐    ┌───────────────┐                   │
    │      │     IAM     │    │    S3         │                   │
    │      │ least-priv  │    │ (optional     │                   │
    │      │    role     │    │  report sink) │                   │
    │      └─────────────┘    └───────────────┘                   │
    │                                                             │
    └─────────────────────────────────────────────────────────────┘
```

### 3.2 Services

**EC2** — the lab fleet. Two `t2.micro` (or `t3.micro`) instances in the lab CloudFormation stack, one per AZ, each running SSH and a small set of services CloudProbe can validate (a listener on a TCP port, an HTTP endpoint, a UDP echo). Free-Tier posture: the stack keeps instance count ≤ 2 and mandates a teardown script.

**VPC** — a purpose-built VPC (`10.20.0.0/16`) isolates the lab from the account's default VPC. Two public subnets across two AZs give the pipeline a cross-AZ reachability signal without incurring NAT Gateway cost.

**Subnets** — public only. A private-subnet design would require a NAT Gateway (not Free Tier) or a VPC endpoint (limited to specific services). The lab is public-subnet-with-restrictive-SG by design; this is documented so no one mistakes it for a production deployment posture.

**Security Groups** — the primary access-control surface. Two SGs are provisioned: `cloudprobe-target-sg` (attached to the EC2 lab hosts, allowing SSH/HTTP/TCP-echo/UDP-echo from CloudProbe's source CIDR only) and `cloudprobe-egress-sg` (used when CloudProbe itself runs on EC2, restricting egress to AWS endpoints). SGs are stateful; return traffic is implicit.

**CloudWatch** — three surfaces:
- **Custom metrics** under namespace `CloudProbe/Network`, dimensioned by `VpcId`, `SubnetId`, `InstanceId`, and `ProbeType`.
- **Alarms** created and updated by the alerting engine, mapped one-to-one to declared `AlertRule` entries.
- **Log group** `cloudprobe/agent` (optional) for the pipeline's own structured logs when CloudProbe runs on EC2.

Free-Tier posture: stay under 10 custom metrics and 10 alarms in the demo configuration.

**SNS** — one topic (`cloudprobe-alerts`) with an email subscription. The alerting engine publishes alarm state changes; email delivery is Free-Tier eligible for the demo volume.

**IAM** — a single execution role (or user, for local dev) holding least-privilege permissions:
- `ec2:Describe*` (discovery, read-only).
- `cloudwatch:PutMetricData` (metrics).
- `cloudwatch:PutMetricAlarm`, `cloudwatch:DescribeAlarms` (alarm management).
- `sns:Publish` scoped to the specific topic ARN.
- `s3:PutObject`, `s3:ListBucket` scoped to a single bucket ARN (only when S3 storage is enabled).

**S3** — optional report archive. When enabled, a single bucket with a 30-day lifecycle rule holds JSON/CSV/HTML reports. Object keys are `reports/YYYY/MM/DD/<run-id>/*`. Free-Tier posture: one bucket, lifecycle-managed to expire cleanly.

**CloudFormation** — a single stack owns the lab (VPC, subnets, IGW, SGs, EC2 instances). Deployment and teardown are two shell scripts that call `aws cloudformation deploy` and `aws cloudformation delete-stack` respectively; the teardown script is mandatory and idempotent so no run can leave orphaned resources.

### 3.3 Free-Tier Assumptions

- Recommended Region: **`us-east-1`** (broadest Free-Tier coverage).
- Instance types: `t2.micro` or `t3.micro` only. No burstable-credit exhaustion planning — the lab is intermittent.
- No NAT Gateway, no ALB/NLB, no managed Prometheus/Grafana, no EKS.
- CloudWatch alarms and custom metrics kept within the Free-Tier allowance by capping demo fleet size.
- S3 optional; when enabled, single bucket with lifecycle expiration to bound cost.
- Teardown is not optional — every provisioning script has a matching, idempotent teardown.

---

## 4. End-to-End Execution Flow

The following sequence describes a single one-shot pipeline invocation (`--once` mode). Scheduler mode is the same sequence repeated on a cadence.

```
Operator      CLI      Config      Discovery   Probes    Metrics   Alerting   Reporting   Storage
   │           │          │           │          │         │          │          │           │
   │  argv →   │          │           │          │         │          │          │           │
   ├──────────▶│          │           │          │         │          │          │           │
   │           │ load ───▶│           │          │         │          │          │           │
   │           │◀── cfg ──┤           │          │         │          │          │           │
   │           │ resolve ─┼──────────▶│          │         │          │          │           │
   │           │          │           │ describe │         │          │          │           │
   │           │          │           │──── AWS ─┼─────────┼──────────┼──────────┼──────────▶│
   │           │          │           │◀─────────┤         │          │          │           │
   │           │          │           │  merge   │         │          │          │           │
   │           │◀── Inventory ────────┤          │         │          │          │           │
   │           │ run ─────┼───────────┼─────────▶│         │          │          │           │
   │           │          │           │          │ probe   │          │          │           │
   │           │          │           │          │─ net ─▶ target     │          │           │
   │           │          │           │          │◀── ProbeResult ──┐ │          │           │
   │           │          │           │          │─────────▶│        │ │          │           │
   │           │          │           │          │         │ emit    │ │          │           │
   │           │          │           │          │─────────┼─────────▶│          │           │
   │           │          │           │          │         │         │  evaluate │           │
   │           │          │           │          │         │         │  breach   │           │
   │           │          │           │          │         │         │──▶ Alarm  │           │
   │           │          │           │          │         │         │──▶ SNS    │           │
   │           │          │           │          │─────────┼─────────┼──────────▶│           │
   │           │          │           │          │         │         │          │  assemble │
   │           │          │           │          │         │         │          │  render   │
   │           │          │           │          │         │         │          │──────────▶│
   │           │          │           │          │         │         │          │           │ put
   │           │◀── RunSummary ───────┼──────────┼─────────┼─────────┼──────────┼───────────┤
   │ exit code │          │           │          │         │         │          │           │
   │◀──────────┤          │           │          │         │         │          │           │
```

**Step-by-step:**

1. **Invocation.** Operator (or CI, or Docker entrypoint) runs the CLI with a config path and mode flag.
2. **Config load.** The Configuration layer reads YAML from `configs/`, validates against pydantic models, and returns an immutable `CloudProbeConfig`. Errors here abort the run with a non-zero exit code and a structured error message.
3. **Discovery.** The Discovery layer materializes an `Inventory` of `Target` records by (a) reading static targets from config and (b) optionally calling `ec2:Describe*`, `ec2:DescribeVpcs`, `ec2:DescribeSubnets`, `ec2:DescribeSecurityGroups`. The two sources are merged with declared precedence rules.
4. **Pipeline entry.** The Scheduler receives the inventory and iterates targets × configured probes. Iteration is bounded per-probe-type by concurrency limits declared in config.
5. **Probe execution.** Each probe runs against its target through a thin transport adapter (socket, subprocess, HTTP client, Paramiko). Every execution produces exactly one `ProbeResult`.
6. **Fan-out.** Each `ProbeResult` is delivered to three consumers **in parallel**:
   - **Metrics dispatcher** emits to CloudWatch (batched, 20 per `PutMetricData` call) and to a local JSONL sink.
   - **Alerting engine** evaluates the result against every `AlertRule` bound to that probe/target; if a breach occurs, it updates the corresponding CloudWatch alarm state and publishes to SNS.
   - **Reporting assembler** accumulates results for the run's `Report` aggregate.
7. **Cycle close.** When all targets and probes have been evaluated, the Reporting layer renders the run's `Report` as JSON, CSV, and HTML.
8. **Persistence.** The Storage layer writes rendered artifacts to `reports/YYYY-MM-DD/<run-id>/` locally, or to S3 if configured.
9. **Exit.** The CLI returns exit code 0 if the run completed cleanly (regardless of probe outcomes; a failed target is not a failed *run*). Non-zero exits are reserved for configuration errors, unrecoverable AWS API failures, and storage failures.

In scheduler mode, steps 3–8 repeat on the cadence declared in `configs/schedules.yaml`; step 2 runs once at startup and again on SIGHUP.

---

## 5. Network Flow

Every probe reports **facts, not judgments**, and classifies failure modes into distinct categories so downstream consumers can distinguish "blocked" from "down" from "misconfigured."

### 5.1 TCP Probe

```
CloudProbe ──── SYN ────▶  Target:port
CloudProbe ◀── SYN/ACK ── Target:port
CloudProbe ──── ACK ────▶  Target:port
CloudProbe ──── RST ────▶  Target:port   (immediate close after handshake)
```

Success is defined as a completed three-way handshake within the configured timeout. Latency recorded is the connect-time delta.

**Failure classes distinguished:**

| Wire observation | Classification | Likely cause |
|---|---|---|
| No response before timeout | `timeout` | Network unreachable, silent drop (typical SG blackhole) |
| `ICMP unreachable` returned | `unreachable` | No route or NACL deny |
| `RST` on SYN | `refused` | Nothing listening on port |
| DNS resolution failure | `dns_failure` | Bad hostname or DNS outage |
| TLS handshake failure (HTTP probe over TLS) | `tls_error` | Cert mismatch, expiry, protocol mismatch |

### 5.2 ICMP Probe

Executed via subprocess (`ping`) for portability across environments where raw sockets require privilege. RTT and packet-loss are parsed from output.

Success is a non-zero echo reply count within the configured probe count and timeout. Because many cloud SGs drop ICMP by default, an ICMP failure is **not** in itself a failure of the target — it is a failure of *ICMP reachability*, and the report presents it that way. Correlating an ICMP failure with a successful TCP probe to the same host tells the operator "the host is up but ICMP is blocked."

### 5.3 UDP Probe

UDP is stateless; a naive "did the packet leave?" check is meaningless. The UDP probe therefore sends a **protocol-shaped payload** (e.g., a DNS query, an NTP request) and validates the response.

**Failure classes distinguished:**

| Observation | Classification |
|---|---|
| Response received and shape valid | `success` |
| Response received but malformed | `protocol_error` |
| No response before timeout | `timeout` |
| ICMP port unreachable | `refused` |

### 5.4 HTTP Probe

Records **three signals**: status code, TLS certificate validity (for `https://`), and time-to-first-byte. Any of these can fail independently. A 200 OK with an expired certificate is a distinct outcome from a 502 with a valid certificate.

### 5.5 SSH Probe

The SSH probe validates **authentication** — not command execution as a default. It attempts a key-based handshake through the `ssh/` adapter and returns immediately after auth succeeds or fails. An optional, whitelisted remote command may be executed for on-host diagnostic collection.

**Failure classes distinguished:**

| Observation | Classification |
|---|---|
| Auth succeeded | `success` |
| TCP connect failed | `network_unreachable` (delegates to TCP semantics) |
| TCP ok, banner exchange failed | `protocol_error` |
| Banner ok, key auth rejected | `auth_failure` |
| Connection dropped mid-handshake | `connection_reset` |
| Handshake timed out | `handshake_timeout` |

Distinguishing `auth_failure` from `handshake_timeout` from `connection_reset` is a stated failure-scenario requirement (see `ROADMAP.md` Phase 7).

### 5.6 Failure Isolation

The probe engine never collapses two distinct failure classes into one. Each `ProbeResult` carries an `error_class` field with an enumerated value drawn from the taxonomy above. Reports group by `error_class`; alerting rules can target a specific class (e.g., "alarm on ≥ 3 `auth_failure` events in 5 minutes without alarming on unrelated timeouts").

This is what "deep observability" means in this project: not more measurements, but **more distinguishable measurements**.

---

## 6. Discovery Flow

Discovery answers the question "what should we probe on this cycle?" It produces a canonical `Inventory` of `Target` records regardless of source.

```
             ┌───────────────────┐
             │ configs/inventory │
             │      .yaml        │
             └────────┬──────────┘
                      │  validated Targets
                      ▼
       ┌──────────────────────────┐
       │   Static Inventory       │
       │  (from Config layer)     │
       └──────────────┬───────────┘
                      │
                      ▼
       ┌──────────────────────────┐        ┌──────────────────────────┐
       │      Merge Engine        │◀───────│    Dynamic Inventory     │
       │  precedence + dedup      │        │  ec2:Describe* via Boto3 │
       └──────────────┬───────────┘        └──────────────────────────┘
                      │
                      ▼
             ┌──────────────────┐
             │    Inventory     │
             │  list[Target]    │
             └──────────────────┘
```

**Static inventory** comes from `configs/inventory.example.yaml` (or a user-provided override). Each entry becomes a validated `Target` at config-load time; discovery does no work beyond passing them through.

**Dynamic inventory** is optional and opt-in per config. When enabled, the AWS discovery module paginates `DescribeInstances`, `DescribeVpcs`, `DescribeSubnets`, and `DescribeSecurityGroups`, filters by a configured tag selector (e.g., `Environment=lab`), and materializes each matching instance as a `Target`. Subnet and SG metadata attach as dimensions used later by metrics and alerting.

**Merge semantics** are declarative:

- **Union by default.** A static target and a dynamic target with disjoint identifiers both appear in the inventory.
- **Static wins on collision.** If a static target and a dynamic target resolve to the same `(host, port, probe_type)` triple, the static definition takes precedence — the operator's explicit statement outranks discovery.
- **Duplicates are logged.** A collision produces a structured warning; downstream, only one target survives.

**Normalization** ensures downstream layers see one shape. Every `Target` carries: an identifier, a network address (IP or hostname), a probe-type list, optional AWS dimensions (`VpcId`, `SubnetId`, `InstanceId`), and any probe-specific parameters (port, SSH key reference, HTTP path).

Dynamic discovery failures do not abort the run. If AWS is unreachable but the static inventory is present, CloudProbe probes the static targets and reports the discovery failure as a first-class event in the run's report.

---

## 7. Monitoring Flow

The monitoring flow is the runtime path that turns probe outcomes into observable signals. Its defining property is **sub-minute detection latency**: from the moment a probe completes to the moment a breach is visible in CloudWatch, less than 60 seconds must elapse.

```
      ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
      │  Probe run   │───▶│  ProbeResult │───▶│  Dispatcher  │
      └──────────────┘    └──────────────┘    └──────┬───────┘
                                                     │ fan-out
                       ┌─────────────────────────────┼─────────────────────────────┐
                       ▼                             ▼                             ▼
              ┌─────────────────┐          ┌──────────────────┐          ┌─────────────────┐
              │  CloudWatch     │          │  Local JSONL     │          │  Alerting       │
              │  PutMetricData  │          │  emitter         │          │  threshold      │
              │  (batched 20)   │          │  (logs/metrics/) │          │  engine         │
              └─────────────────┘          └──────────────────┘          └─────────────────┘
```

### 7.1 Metric Model

Each `ProbeResult` produces one or more metric data points:

- `ProbeLatencyMs` — connect/response latency for successful probes.
- `ProbeSuccess` — 1 for success, 0 for failure. Enables straightforward `Average` and `Sum` statistics.
- `ProbeErrorClass` — dimension used on failure to separate `timeout` from `refused` from `auth_failure`, etc.

**Dimensions** attached to every data point: `ProbeType`, `TargetId`, `VpcId`, `SubnetId`, `InstanceId` (where known). Dimensions are how a single alarm rule ("SSH failures on subnet A") is expressible without proliferating metrics.

### 7.2 Publishing

CloudWatch's `PutMetricData` accepts up to 20 data points per call and up to 40 KB per payload. The dispatcher batches accordingly and flushes on whichever comes first: batch full, time-since-last-flush exceeds 5 s, or end-of-cycle. Batching keeps the sub-minute latency target intact while respecting throttling limits.

### 7.3 Local Emission

In parallel with CloudWatch, every result is appended to `logs/metrics.jsonl`. This exists for two reasons: (a) local development without AWS credentials, and (b) an audit trail independent of CloudWatch retention. The regression tier reads this file to validate that no probe result is lost between emission and persistence.

### 7.4 Scheduling

The scheduler drives repeated invocations. Two modes:

- **One-shot** (`--once`, Docker `oneshot`). One complete cycle, then exit. Used by CI and by host `cron` as an alternative to in-process scheduling.
- **Long-running** (`--scheduler`, Docker `scheduler`). APScheduler drives cadences declared per-probe-type in `configs/schedules.yaml`. Each probe type can have its own interval (e.g., TCP every 30 s, SSH every 5 minutes).

### 7.5 Retries and Health Evaluation

Retries live at the **probe level**, not the pipeline level. If a TCP probe times out, the probe itself may retry once with backoff before returning a failure `ProbeResult` — but the pipeline does not re-run the probe. This keeps semantics clean: one `ProbeResult` corresponds to one health assessment, and retries are internal to that assessment.

Health evaluation is stateless at the probe level: each `ProbeResult` stands alone. Statefulness — "three failures in a row" — is introduced by the alerting engine, where it belongs.

---

## 8. Alert Flow

Alerting turns a stream of `ProbeResult` values into (a) breach decisions and (b) CloudWatch alarms that bind those decisions to durable, human-visible state.

```
    ProbeResult ──▶ ┌────────────────────┐
                    │  Rule Evaluator    │
                    │  (windowed logic)  │
                    └─────────┬──────────┘
                              │  Breach?
                     ┌────────┴────────┐
                    no                yes
                     │                 │
                     ▼                 ▼
              (record only)  ┌──────────────────┐
                             │   Alarm Binder   │
                             │  ensure alarm    │
                             │  state = ALARM   │
                             └────────┬─────────┘
                                      │
                             ┌────────┼────────┐
                             ▼        ▼        ▼
                       ┌─────────┐┌─────────┐┌─────────┐
                       │CloudWatch││   SNS   ││ Report  │
                       │  Alarm  ││  topic  ││  entry  │
                       └─────────┘└─────────┘└─────────┘
```

### 8.1 Rule Evaluation

`AlertRule` entries are declared in configuration. Each rule specifies:

- **Selector.** Which probe/target/dimensions it applies to.
- **Predicate.** What constitutes a breach — a single failure, a success ratio over a window, a latency percentile above a threshold.
- **Severity.** Informational, warning, critical. Used by sinks to decide whether to page.

Evaluation is streaming: rules with windowed predicates keep small ring buffers of recent results per selector. A rule fires the moment its predicate transitions from `false` to `true`.

### 8.2 Alarm Binding

Every declared rule maps to exactly one CloudWatch alarm. The binder ensures alarms exist, are attached to the correct metric with the correct dimensions, and reflect current threshold values. Binding is idempotent: on every run, the binder reconciles declared rules against `DescribeAlarms` output and issues `PutMetricAlarm` only for differences.

This model — declarative rules reconciled at runtime — means an operator can add, remove, or change an alarm by editing YAML. No console clicking, no drift.

### 8.3 Notification Dispatch

When a rule breaches, one or more sinks receive the notification:

- **CloudWatch alarm state.** Always. The alarm becomes the durable record.
- **SNS publish.** When configured. Message body contains rule name, selector, breach detail, and a link back to the CloudWatch alarm.
- **Local log record.** Always, for the run's report.

Sinks are pluggable; adding a Slack or PagerDuty sink is a new module in the alerting package. See §14.

### 8.4 Alert Lifecycle

An alert has three observable states:

1. **OK.** No matching rule is currently in breach.
2. **ALARM.** A rule's predicate is currently true. CloudWatch alarm is in `ALARM` state. SNS notification has been dispatched.
3. **INSUFFICIENT_DATA.** CloudWatch's third state; entered when the metric stream has gaps. CloudProbe does not itself publish `INSUFFICIENT_DATA` — CloudWatch derives it from missing data points.

State transitions are visible in three places: CloudWatch console, SNS delivery log, and the diagnostic report's "Breaches" section.

---

## 9. Reporting Flow

Reporting takes the `ProbeResult` stream and the breach log for a run and produces artifacts a human or machine can consume.

```
   ProbeResult stream ─┐
                       ├──▶ ┌────────────────┐    ┌───────────────┐
   Breach log ─────────┤    │  Report        │───▶│  Renderers    │
                       │    │  Assembler     │    │ JSON│CSV│HTML │
   Run metadata ───────┘    └────────────────┘    └──────┬────────┘
                                                         │
                                                         ▼
                                                ┌──────────────────┐
                                                │  Storage adapter │
                                                │  local FS or S3  │
                                                └────────┬─────────┘
                                                         │
                                                         ▼
                                                 reports/YYYY-MM-DD/
                                                    <run-id>/
                                                    ├── report.json
                                                    ├── report.csv
                                                    └── report.html
```

### 9.1 Aggregation

The assembler consumes:

- **Run metadata** — start time, end time, host, config hash, mode (`oneshot`/`scheduler`).
- **Inventory summary** — target count, unique VPCs/subnets, static-vs-dynamic split.
- **Per-probe results** — every `ProbeResult` grouped by target and probe type.
- **Failure detail** — non-success results with `error_class`, latency, timestamp.
- **Threshold breach list** — every rule that fired, with the triggering result(s).
- **CloudWatch metric URLs** — deep links to the alarm and metric views for each dimension set.

### 9.2 Rendering

Three formats, one shared `Report` model:

- **JSON** — canonical machine format. Ordered keys for stable diffing. Consumed by regression tests as golden files.
- **CSV** — one row per `ProbeResult`. Useful for spreadsheet inspection and ad-hoc analysis.
- **HTML** — self-contained (inline CSS, no external assets, no JavaScript required for content). Renders on any operator's laptop without network access.

Renderers are pure functions of the `Report` model. Adding a Markdown or PDF renderer is a new module (see §14).

### 9.3 Persistence

The Storage layer decides *where* the artifacts go. The default is local `reports/YYYY-MM-DD/<run-id>/`; when S3 is configured, the same three files are put to `s3://<bucket>/reports/YYYY/MM/DD/<run-id>/`. Both destinations may be enabled simultaneously.

Object keys are deterministic and time-ordered so that `aws s3 ls` yields chronological history without additional indexing.

---

## 10. Testing Architecture

Testing is not a gate at the end — it is how each architectural layer proves it can be reasoned about independently. The four-tier taxonomy maps to the layer diagram of §3.

```
┌──────────────────────────────────────────────────────────────────┐
│                          Test Tiers                              │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  UNIT           ──▶  Every layer's pure logic in isolation       │
│                      No network, no filesystem beyond tmp_path   │
│                                                                  │
│  INTEGRATION    ──▶  Adapters against fakes (moto, pytest-socket,│
│                      Paramiko MockTransport). Full pipeline wired│
│                      end-to-end with no real cloud.              │
│                                                                  │
│  REGRESSION     ──▶  Golden-file comparison on user-visible      │
│                      surfaces (report JSON/CSV/HTML, CLI help).  │
│                                                                  │
│  FAILURE        ──▶  Parameterized simulations of every failure  │
│                      class listed in the network flow (§5).      │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### 10.1 What Each Tier Validates

- **Unit** validates the *internal correctness* of a layer. Threshold arithmetic, config validation, report assembly, retry math. Fast, deterministic, run on every commit.
- **Integration** validates the *interface between layers*, including with AWS. `moto` stands in for CloudWatch, EC2, SNS, and S3. `pytest-socket` disables real network egress so no test accidentally reaches the internet. Paramiko's `MockTransport` fakes SSH.
- **Regression** validates that *user-visible outputs do not change silently*. Report JSON keys, CSV columns, HTML structure, and CLI help text are golden files. Updates require an explicit flag and reviewer sign-off.
- **Failure-scenario** validates that *the taxonomy of failures in §5 is preserved*. Every distinguishable class has a parameterized case: SG blackhole (timeout), TCP RST (refused), SSH auth failure (auth_failure), SSH banner timeout (handshake_timeout), clock skew, CloudWatch 429 storm, partial VPC partition.

### 10.2 How the Tiers Cover the Layers

| Layer | Unit | Integration | Regression | Failure |
|---|---|---|---|---|
| CLI | argument parsing, exit codes | subcommand wiring | help-text goldens | invalid config exit codes |
| Config | model validation, defaults | YAML loader roundtrip | error-message goldens | missing/malformed files |
| Discovery | merge precedence | moto EC2 describe | inventory goldens | AWS unreachable during discovery |
| Probes | error classification | pytest-socket, Paramiko fake | ProbeResult schema goldens | every §5 failure class |
| SSH adapter | whitelist enforcement | MockTransport handshake | — | auth failure / reset / timeout distinctions |
| Metrics | batching math | moto CloudWatch | metric shape goldens | 429 throttling |
| Alerting | rule evaluation | moto alarms + SNS | alarm shape goldens | flapping, INSUFFICIENT_DATA |
| Reporting | assembly, rendering | full-pipeline render | report goldens | partial-run report shape |
| Storage | key composition | moto S3 + local FS | filename goldens | write failure mid-run |
| Scheduler | cadence math | oneshot wiring | — | overlapping runs |

The 90% coverage floor is enforced across the sum of these tiers, not on any one of them. See `ROADMAP.md` Phase 7 for the exit criteria.

### 10.3 What Tests Do Not Do

- They do not touch real AWS. Every AWS call goes through `moto` or a hand-rolled fake.
- They do not perform real network I/O. `pytest-socket` fails any test that opens a real socket.
- They do not depend on wall-clock time. Time-dependent code is exercised through injectable clocks.

These constraints are what make the test suite runnable in CI without credentials, in Docker, and offline.

---

## 11. Deployment Flow

CloudProbe has four deployment surfaces, each addressed by the architecture.

### 11.1 Local Execution

```
Developer laptop
   │
   ├─▶ make bootstrap        (venv, deps, pre-commit)
   ├─▶ python -m cloudprobe config validate configs/inventory.example.yaml
   ├─▶ python -m cloudprobe run --once --config configs/...
   └─▶ open reports/YYYY-MM-DD/<run-id>/report.html
```

No AWS credentials are required; the pipeline runs against the static inventory and emits to the local JSONL sink. This is the loop used during development.

### 11.2 Docker Execution

```
docker run \
   -v $PWD/configs:/etc/cloudprobe/configs:ro \
   -v $PWD/reports:/var/cloudprobe/reports \
   -e CLOUDPROBE_MODE=oneshot \
   -e AWS_REGION=us-east-1 \
   cloudprobe
```

The container runs as non-root UID/GID 10001. Config is bind-mounted read-only; reports are bind-mounted writable. `CLOUDPROBE_MODE=scheduler` swaps `oneshot` for long-running. `HEALTHCHECK` calls `python -m cloudprobe healthcheck` which validates config and, when credentials are present, pings the AWS APIs the pipeline will use.

### 11.3 GitHub Actions CI

```
Push / PR
   │
   ├─▶ ci.yml       ── lint → typecheck → unit → integration → coverage upload
   ├─▶ docker.yml   ── build image → smoke test               (touches docker/, src/, or requirements*.txt)
   └─▶ nightly.yml  ── regression + failure scenarios (scheduled)
```

Fast feedback under three minutes on PRs; heavy work runs nightly. Nightly failures open a GitHub Issue so overnight breakage is visible the next morning.

### 11.4 AWS Deployment (Lab)

```
scripts/provision-lab.sh
   │
   ▼
aws cloudformation deploy
   │
   ├─▶ VPC + subnets + IGW + route tables
   ├─▶ Security Groups
   ├─▶ EC2 lab instances (2× t2.micro)
   └─▶ Outputs: SG ids, instance ips, subnet ids
   │
   ▼
CloudProbe run against real targets (locally or containerized)
   │
   ▼
scripts/teardown-lab.sh  (mandatory; idempotent)
```

The IAM role/user described in §3.2 must be provisioned separately — it is not part of the lab stack because the credentials are used *by* the pipeline, not consumed *by* the stack. Provisioning IAM is documented in `docs/aws-setup.md`.

**The teardown script is not optional.** Every provisioning path has a matching teardown, and CI validates that a provision-then-teardown leaves no orphaned resources in a test account. This is what makes Free-Tier compliance enforceable rather than aspirational.

---

## 12. Failure Handling

Failure handling is a design property: every layer knows what to do when the layer beneath it does not answer. The rule is **fail loudly and locally**; do not swallow errors, do not pretend a partial run is a full run.

### 12.1 Network Failures (Probe → Target)

Owned by the Probe Engine. Every probe emits a `ProbeResult` even on failure — success is *never* the absence of a result. The `error_class` field carries the wire-level classification described in §5. Downstream layers treat probe failures as data, not as exceptions.

### 12.2 AWS API Failures

Owned by the layer making the call.

- **Discovery.** A `Describe*` failure downgrades the run to static-inventory-only, records the failure in the report, and continues. Discovery failure is not a fatal error.
- **Metrics.** A `PutMetricData` failure is retried with exponential backoff up to the batch's TTL; if still failing, the metric points are flushed to the local JSONL sink so no data is lost. A structured warning is emitted; the run continues.
- **Alerting.** A `PutMetricAlarm` failure is recorded as a rule-sync error in the report. The corresponding rule is evaluated normally in-process; only the durable CloudWatch alarm may lag reality until the next successful sync.
- **Storage (S3).** A `PutObject` failure falls back to local filesystem storage and records the fallback in the run's summary. The next successful run's storage reconciliation can re-upload if configured.

Throttling (`ThrottlingException`, HTTP 429) is treated as a transient failure — retry with jitter, log the event, do not abort.

### 12.3 SSH Failures

The SSH adapter classifies auth failure, banner timeout, and connection reset as distinct outcomes (see §5.5). Failures here are strictly probe-level; they never propagate as exceptions past the probe boundary.

### 12.4 Invalid Configuration

Configuration validation is the *only* place where a failure aborts the run before it starts. A malformed YAML file, an unknown field, or a required field missing produces a structured error (`ConfigValidationError` or `ConfigNotFoundError`), a non-zero exit code, and no side effects. This is by design: a broken config would produce results that lie, and lying results are worse than no results.

### 12.5 Reporting Failures

If report rendering fails for one format (e.g., HTML template error), the other formats are still produced and the failure is recorded in the run's exit summary. If report *persistence* fails, the rendered artifacts are still on the local filesystem — the failure is a delivery failure, not a data-loss event.

### 12.6 The Overarching Contract

> A `ProbeResult` is never lost between emission and persistence.

This is the one invariant every failure-handling decision above preserves. Every fallback path — local JSONL when CloudWatch fails, local FS when S3 fails, degraded static inventory when discovery fails — exists to keep that invariant true.

---

## 13. Security Architecture

Security is designed in, not bolted on. Every external surface has a stated posture; every credential has a scoped destination.

### 13.1 IAM Least Privilege

CloudProbe's execution identity holds only the permissions listed in §3.2. Wildcards on `Resource` are avoided; SNS `Publish` is scoped to the topic ARN, S3 access is scoped to the report bucket ARN. The IAM policy document is committed to `infra/policies/` so any privilege change is a reviewable diff.

The lab CloudFormation stack does **not** deploy CloudProbe's IAM role — that role is provisioned separately so that the pipeline's identity cannot self-modify by editing the stack it uses.

### 13.2 SSH Key Handling

- **Key material is never committed.** `.gitignore` and `.dockerignore` exclude `.pem`, `.key`, `id_rsa*`, and `known_hosts`.
- **Keys are read from disk paths declared in config**, not embedded. Config declares a *path*; the operator's environment provides the file.
- **Password authentication is prohibited** by the SSH adapter. Key-based auth only.
- **Command execution is whitelisted.** The SSH adapter refuses to run any command not in an allowlist declared in code. This prevents an accidental "run whatever the config says" from becoming a remote code execution vector.
- **Host key policy** is `RejectPolicy` by default; `known_hosts` entries must be present. There is no `AutoAddPolicy` mode in the shipping code.

### 13.3 Environment Variables and Secrets

- **All secrets flow through environment variables**, never through config files or command-line arguments (which appear in `ps` and shell history).
- **`.env.example` documents every variable** the pipeline reads; there is no undocumented environment surface.
- **The Docker image contains no baked-in secrets.** Credentials are provided at `docker run` time via `-e` flags or an env-file mount.
- **AWS credentials** are supplied through the standard Boto3 credential chain (env vars, shared credentials file, or IAM role for EC2/ECS deployments).

### 13.4 Network Isolation

- The lab VPC is isolated from the account's default VPC — different CIDR, dedicated route tables.
- Security Groups implement default-deny; only the specific ports CloudProbe needs to probe are opened, and only from CloudProbe's source CIDR.
- No inbound ports are opened on the CloudProbe runtime itself. It is an outbound-only client.
- When CloudProbe runs on EC2, egress can be restricted to AWS API endpoints and the lab SG using VPC endpoints and SG rules.

### 13.5 Container Security

- **Non-root user.** Runtime UID/GID 10001; the image has no `sudo` and no writable system paths.
- **Minimal base.** `python:3.11-slim` runtime stage with no build toolchain and no shell utilities beyond `bash` for the entrypoint.
- **Read-only root filesystem** is compatible with the design — the container writes only to explicitly-mounted volumes (`/var/cloudprobe/reports`, `/var/cloudprobe/logs`).
- **Vulnerability scanning** runs on every PR that touches the image (`docker scout` or `trivy`), with HIGH/CRITICAL findings blocking merge.
- **HEALTHCHECK** distinguishes "container up" from "pipeline healthy" so orchestrators do not consider a broken pipeline as running.

### 13.6 Supply Chain

- **Dependencies pinned** in `requirements.txt` for reproducible builds.
- **Dependabot** watches pip, Docker base images, and GitHub Actions for known vulnerabilities.
- **No curl-pipe-bash** anywhere in scripts or Dockerfile.

---

## 14. Scalability Strategy

The architecture is designed so that common growth vectors are absorbed by **adding modules**, not by restructuring.

### 14.1 New Probe Type

A new probe (e.g., gRPC health check, TLS-only handshake, database SELECT-1) is added by:

1. Implementing the `Probe` interface (returns a `ProbeResult` with the standard shape).
2. Registering it with the probe registry.
3. Adding a schema extension in the Config layer if it needs new parameters.
4. Adding a failure-scenario case for each distinguishable outcome.

No other layer changes. Metrics, alerting, reporting, and storage operate on `ProbeResult` and remain agnostic to the probe type.

### 14.2 New Cloud Provider

The Discovery layer is provider-partitioned. Adding Azure or GCP means:

1. A new discovery module that turns provider-native inventory calls into `Target` records.
2. Provider-specific credential handling documented in `docs/aws-setup.md` (or a new sibling file).
3. Provider-specific dimensions carried on `ProbeResult` for later attribution.

Probes remain provider-agnostic because they talk to IP addresses and hostnames, not to cloud APIs. Metrics and alerting continue to work; only the dimensions grow.

### 14.3 New Storage Backend

Storage is defined by a small protocol (put, get, list). Adding GCS, Azure Blob, or a Postgres backend is:

1. A new adapter implementing the protocol.
2. A factory extension so config can select the backend.
3. Documentation of the new config key.

Reporting depends on the protocol, not the implementation, so nothing above changes.

### 14.4 New Reporting Format

A new format (Markdown, PDF, Slack blocks) is:

1. A new renderer that consumes the `Report` model.
2. A dispatcher extension.
3. A golden file in the regression tier.

The `Report` model itself is stable — this is what makes new renderers cheap.

### 14.5 New Notification Channel

Alerting sinks are pluggable. Slack, PagerDuty, Microsoft Teams, or an internal webhook are each:

1. A new sink module.
2. A config extension for the sink's credentials/endpoint.
3. An addition to the rule schema if the sink accepts routing keys or severities.

The rule evaluator does not change; only where notifications land does.

### 14.6 Scaling Fleet Size

The pipeline is streaming end-to-end: no layer holds the entire result set before passing it on. Growth from 50 targets to 500 requires no code change — only YAML. From 500 to 5000, the natural pressure points are:

- **Concurrency in the scheduler.** Worker pools per probe type.
- **CloudWatch batching pressure.** Already 20-metric batches; latency of `PutMetricData` becomes the bottleneck and can be alleviated by parallel dispatchers.
- **Discovery pagination.** Already paginated; only cache lifetime tuning is needed.

None of these require moving code between layers.

---

## 15. Architecture Decisions

Significant architectural choices are recorded as ADRs under `docs/adr/`. This document does not restate their content — it points at them.

| Decision | ADR |
|---|---|
| Language and runtime choice (Python 3.11+) | [`docs/adr/0001-python.md`](adr/0001-python.md) |
| Infrastructure-as-code tooling (CloudFormation over Terraform) | [`docs/adr/0002-cloudformation.md`](adr/0002-cloudformation.md) |
| Container packaging (multi-stage, non-root, single-service compose) | [`docs/adr/0003-docker.md`](adr/0003-docker.md) |
| Testing strategy (four-tier taxonomy, 90% floor, moto-first) | [`docs/adr/0004-testing.md`](adr/0004-testing.md) |

When an architectural choice recorded in an ADR is revisited, the outcome is a **new ADR** that supersedes the old — never an in-place edit. This preserves the reasoning trail for future contributors and reviewers.

---

*This document is authoritative for CloudProbe's runtime architecture. Any behavior described here that does not match the running code is a bug in one of the two. Reviewed against `ROADMAP.md` and `docs/project-structure.md` at the time of authorship; updated in the same PR as any change that alters runtime behavior.*
