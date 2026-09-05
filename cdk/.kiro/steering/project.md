---
inclusion: always
---

# cloud-watchtower — Project Overview

CDK (Python) app that provisions the AWS-side plumbing for shipping
CloudTrail / workload logs into a central **log archive** and exposing them to
**Cribl** for downstream processing. It ports a proven, already-running
CloudTrail → Cribl pattern (SNS → SQS fan-out + an ExternalId-gated reader
role) into reusable, account-driven CDK.

This is a **home-lab** deployment: there is **no CI/CD pipeline and no CDK
Stage**. The only deploy path is `make deploy` (see `docs/plan.md` §8).

## Two sides of the pipeline

One account/side is selected at synth time via `-c account=<name>` or the
`WATCHTOWER_ACCOUNT` env var. Exactly one side is produced:

| account | stack(s) | what it owns |
| --- | --- | --- |
| `logarchive` | `LogArchiveStack` × 2 (one per region) | regional S3 buckets, bucket policy, SNS topic, SQS queue + DLQ, and (primary region only) the global `watchtower-cribl-reader` IAM role |
| `sandbox` / `development` / `production` | `WorkloadWriterStack` × 1 | one shared, write-only IAM writer role for all compute (EKS Pod Identity / EC2 / ECS / Lambda) |

IAM is global, so the Cribl reader role is created exactly **once**, in the
primary region's stack (`us-east-1`). Bucket names are deterministic
(`watchtower-logarchive-<region>-<account_id>`), so no cross-region/stack CDK
references are needed.

## Layout

```
cdk/
  app.py                      # entrypoint; resolves account -> stacks
  cdk.json                    # cdk app cmd + context (account IDs, org id, bootstrap qualifier)
  Makefile                    # the ONLY deploy path; guardrails baked in
  configs/
    infrastructure.yaml       # globals + per-account overrides (deep-merged, account wins)
    config.py                 # loader: ${ENV} + cdk.json context substitution -> typed spec
    models.py                 # dataclasses hydrated by dacite
  stacks/
    log_archive_stack.py      # logarchive side (per region)
    workload_writer_stack.py  # workload side (per account)
  utils/
    converters.py             # deep-merge helper
    logger.py                 # structured logging
```

## Config model

- `configs/infrastructure.yaml` has a `globals:` block and an `accounts:` list.
  The selected account is **deep-merged on top of globals** (account wins;
  scalars override individually, lists replace wholesale).
- Account IDs + org id are the **source of truth in `cdk.json` context**
  (super-fiesta style). `.env` is optional and only for local overrides.
  Account IDs are **not secrets** (they come from
  `aws organizations list-accounts`).
- `${ENV_VAR}` tokens in the YAML resolve from real env vars first, then
  `cdk.json` context.

## Key facts (don't re-derive)

- Regions: `us-east-1` (primary, `ue1`) and `us-east-2` (`ue2`).
- Bootstrap qualifier: `watchtwr26`; toolkit stack `CDKToolkit-watchtwr26`.
- Deployment (deployer) account: `766789219588`; logarchive account:
  `766997230140`; audit (Cribl user) account: `698777852125`.
- Buckets are `RETAIN` on stack delete, block-public, force-HTTPS, SSE-S3,
  30-day object expiry, 7-day incomplete-multipart abort.
- Bucket policy: org-wide write-into-own-`{PrincipalAccount}`-prefix + an
  explicit delete deny for everyone.
- SSE-KMS is a deliberate **future** upgrade, not yet implemented (plan §10).

## Reference docs

- `docs/plan.md` — the deployment plan and open items (section-numbered).
- `docs/iam-s3-design.md` — the IAM + S3 design writeup.

When a task touches design/intent, read those before changing behavior.
