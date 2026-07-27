# CloudProbe — Configuration Reference

> **Scope.** Every YAML key CloudProbe accepts: its type, default, effect, and validation rules.
> **Audience.** Operators tuning CloudProbe. Not architecture — see [`architecture.md`](architecture.md) for how these values flow through the pipeline.
> **Status.** Phase 2 of the roadmap.  Only the configuration layer is implemented; keys documented here take effect as later phases land.

---

## Table of Contents

1. [How configuration is loaded](#1-how-configuration-is-loaded)
2. [Top-level structure](#2-top-level-structure)
3. [`targets`](#3-targets)
4. [`thresholds`](#4-thresholds)
5. [`schedules`](#5-schedules)
6. [`alert_rules`](#6-alert_rules)
7. [`probe`](#7-probe)
8. [Cross-inventory validation rules](#8-cross-inventory-validation-rules)
9. [Errors](#9-errors)

---

## 1. How configuration is loaded

The loader accepts either a single YAML file or a directory of YAML files. Both are validated identically; only the on-disk layout differs.

**Single-file mode.** One YAML document containing every section:

```yaml
targets: [...]
thresholds: [...]
schedules: [...]
alert_rules: [...]
probe: { ... }
```

**Directory mode.** The following filenames are recognized under the directory passed to the loader. Each file must be a YAML mapping whose top-level key matches its section — the loader rejects any file whose top-level key is unexpected, so a typo in the wrapping key surfaces as a clear error rather than a silent misinterpretation.

| File | Required top-level key |
|---|---|
| `inventory.yaml` | `targets` |
| `thresholds.yaml` | `thresholds` |
| `schedules.yaml` | `schedules` |
| `alert_rules.yaml` | `alert_rules` |
| `probe.yaml` | `probe` |

Missing files are treated as an absent section (subject to the [cross-inventory validation rules](#8-cross-inventory-validation-rules)).

The example inventory shipped in the repository is named `inventory.example.yaml` and does not match the loader's default filename recognition; use it as a template by copying it to `inventory.yaml` alongside `thresholds.yaml` and `schedules.yaml`, or load it directly as a single-file config in tests and demos.

---

## 2. Top-level structure

A valid configuration must contain at least one `targets` entry. Every other section is optional at the type-system level, but the [cross-inventory validation rules](#8-cross-inventory-validation-rules) make `thresholds` and `schedules` effectively required in practice: any probe type referenced by a target must have a matching threshold and schedule.

```yaml
targets:      [...]   # required, non-empty
thresholds:   [...]   # required in practice (see §8)
schedules:    [...]   # required in practice (see §8)
alert_rules:  [...]   # optional
probe:        { ... } # optional; uses defaults if omitted
```

Every model is **frozen** after loading — attempting to mutate a loaded configuration raises `ValidationError`.

---

## 3. `targets`

Each entry describes a single host or service to be probed.

| Key | Type | Required | Default | Effect |
|---|---|---|---|---|
| `target_id` | string, 1–64 chars, no whitespace | yes | — | Stable identifier used in metrics dimensions and reports. Must be unique across the entire inventory. |
| `host` | IPv4 / IPv6 address or DNS hostname | yes | — | Network address probes connect to. |
| `port` | integer 1–65535 | no | *unset* | Destination port. Required by TCP/HTTP/SSH probes; optional for ICMP. |
| `probe_types` | list of `tcp`, `icmp`, `udp`, `http`, `ssh` | yes | — | Probe types to run against this target. Must be non-empty and contain no duplicates. |
| `label` | string | no | *unset* | Human-readable label used in reports. |
| `vpc_id` | string | no | *unset* | AWS VPC ID; populated by discovery (Phase 4) or set explicitly. |
| `subnet_id` | string | no | *unset* | AWS subnet ID. |
| `instance_id` | string | no | *unset* | AWS EC2 instance ID. |
| `tags` | mapping of string → string | no | `{}` | Arbitrary tags. Forwarded to metrics as dimensions and matched by `alert_rules.target_tag_filter`. |

**Host validation.** A string is treated as an IP address if it contains only digits and dots, or if it contains a `:` (IPv6). Anything else must match a loose RFC 1123 hostname pattern. Bogus IPs (`999.999.999.999`, `10.0.0.256`) are rejected with a clear error message.

**Example:**

```yaml
targets:
  - target_id: vpca-web-01
    label: Web frontend A-01
    host: 10.20.1.10
    port: 443
    probe_types: [tcp, http, ssh]
    vpc_id: vpc-0aaa0000
    subnet_id: subnet-0aaa01
    instance_id: i-0aaa010001
    tags: {tier: web, env: lab}
```

---

## 4. `thresholds`

Acceptable performance bounds for each probe type. A result that exceeds any of these values is a candidate breach; whether that breach fires an alarm is decided by `alert_rules` (Phase 4).

| Key | Type | Required | Default | Effect |
|---|---|---|---|---|
| `probe_type` | enum | yes | — | One of `tcp`, `icmp`, `udp`, `http`, `ssh`. |
| `max_latency_ms` | positive integer | no | `1000` | Maximum acceptable connect / round-trip latency. |
| `min_success_ratio` | float in `[0.0, 1.0]` | no | `0.9` | Minimum success ratio over the evaluation window. |
| `consecutive_failures` | positive integer | no | `3` | Consecutive failures required to declare a breach. |

**Example:**

```yaml
thresholds:
  - probe_type: tcp
    max_latency_ms: 500
    min_success_ratio: 0.99
    consecutive_failures: 3
```

---

## 5. `schedules`

Cron-style cadence for each probe type. APScheduler (Phase 5) reads these values.

| Key | Type | Required | Default | Effect |
|---|---|---|---|---|
| `probe_type` | enum | yes | — | One of `tcp`, `icmp`, `udp`, `http`, `ssh`. |
| `cron_expression` | string, exactly 5 fields | yes | — | Standard `minute hour dom month dow` cron string. Validated for field count here; semantic validation happens when the scheduler starts. |
| `timeout_seconds` | positive integer | no | `10` | Maximum wall time for a single probe execution. |
| `max_concurrency` | positive integer | no | `10` | Maximum concurrent probes of this type. |

**Example:**

```yaml
schedules:
  - probe_type: tcp
    cron_expression: "*/1 * * * *"
    timeout_seconds: 5
    max_concurrency: 20
```

---

## 6. `alert_rules`

Declares when a breach should produce an alarm. `rule_id` is used as the CloudWatch alarm name prefix (Phase 4).

| Key | Type | Required | Default | Effect |
|---|---|---|---|---|
| `rule_id` | string, 1–64 chars, no whitespace | yes | — | Unique identifier across all rules. |
| `probe_type` | enum | yes | — | One of `tcp`, `icmp`, `udp`, `http`, `ssh`. |
| `target_tag_filter` | mapping of string → string | no | `{}` | Rule applies only to targets whose tags are a superset of these key/value pairs. |
| `severity` | `info`, `warning`, or `critical` | no | `warning` | Used by notification sinks to decide routing / paging. |
| `evaluation_window_seconds` | positive integer | no | `300` | Window over which `min_success_ratio` is measured. |
| `notify_sns` | boolean | no | `true` | Publish to SNS when this rule fires. |

**Example:**

```yaml
alert_rules:
  - rule_id: web-http-degraded
    probe_type: http
    target_tag_filter: {tier: web}
    severity: warning
    evaluation_window_seconds: 300
    notify_sns: true
```

---

## 7. `probe`

Global probe execution settings shared across all probe types.

| Key | Type | Required | Default | Effect |
|---|---|---|---|---|
| `default_timeout_seconds` | positive integer | no | `10` | Fallback timeout when a schedule does not specify one. |
| `retry_attempts` | integer in `[0, 5]` | no | `1` | Retries before a probe records a failure. |
| `retry_backoff_seconds` | non-negative float | no | `1.0` | Base delay between retries; exponential backoff applied. |
| `http_user_agent` | string | no | `cloudprobe/<version>` | User-Agent sent by the HTTP probe. |

---

## 8. Cross-inventory validation rules

These checks run once every individual model has been validated. They exist to catch silent gaps that field-level validation cannot see.

| Rule | Error message contains |
|---|---|
| Every target's `target_id` must be unique across the inventory. | `duplicate target_id(s): ...` |
| Every alert rule's `rule_id` must be unique. | `duplicate rule_id(s): ...` |
| Every probe type referenced by any target must have a matching `Threshold`. | `missing threshold(s) for probe type(s): ...` |
| Every probe type referenced by any target must have a matching `Schedule`. | `missing schedule(s) for probe type(s): ...` |

The last two rules protect against declaring a target and then never probing it (missing schedule) or probing it but having no way to evaluate results (missing threshold).

---

## 9. Errors

The loader raises exactly one of the following on failure. All inherit from `cloudprobe.config.ConfigError`.

| Exception | Meaning |
|---|---|
| `ConfigNotFoundError` | The path passed to `load()` does not exist. |
| `ConfigParseError` | The file exists but contains invalid YAML. |
| `ConfigValidationError` | The file parsed as YAML but violates the schema — includes empty file, non-mapping top-level, missing/mismatched wrapping key in a directory file, and any Pydantic validation failure. |

Every exception exposes the offending `path`, so error messages can be traced back to the file that produced them.

Validation errors from Pydantic are wrapped in `ConfigValidationError` with the original message preserved in the exception text — no information is lost, and downstream code does not need to import Pydantic to catch a config failure.
