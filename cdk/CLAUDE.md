# CLAUDE.md

Guidance for AI coding agents (Claude Code, Kiro, etc.) working in this
directory. Read this first, then `docs/plan.md` and `docs/iam-s3-design.md`
before changing behavior.

## What this is

A CDK (Python) app that provisions the AWS side of a **log archive → Cribl**
pipeline for an AWS Organization. It ports an already-running CloudTrail →
Cribl pattern (S3 → SNS → SQS fan-out + an ExternalId-gated cross-account
reader role) into reusable, account-driven CDK.

**Home lab: there is no CI/CD pipeline and no CDK `Stage`.** The only deploy
path is `make deploy`. Do not add a pipeline.

## Architecture in one screen

One account/side is chosen at synth time (`-c account=<name>` or
`WATCHTOWER_ACCOUNT`). `app.py` resolves it to stacks:

- `account=logarchive` → two `LogArchiveStack`s (one per region:
  `us-east-1`/`ue1` primary, `us-east-2`/`ue2`). Each owns a regional S3
  bucket, bucket policy, SNS topic, SQS queue + DLQ, and the S3→SNS→SQS
  notification wiring. The **primary region only** also creates the global
  `watchtower-cribl-reader` IAM role.
- `account=sandbox|development|production` → one `WorkloadWriterStack`: a
  single shared, **write-only** IAM role for all compute (EKS Pod Identity,
  EC2, ECS, Lambda) to write logs into its own account prefix (cross-account,
  `<account_id>/` prefix).
- `account=homelab` → one `HomelabWriterStack`: a single write-only IAM
  **user** (`watchtower-writer-home`) in the **logarchive** account, for the
  k3s home cluster (`cauldron`). No role, no cross-account trust — the cluster
  has no AWS account, so it authenticates as a same-account user whose identity
  policy grants write-only into a **fixed literal** `homelab/cauldron/` prefix.
  The access key is minted post-deploy, never by CDK.

IAM is global → the reader role is created once (primary region). Bucket and
queue names are deterministic patterns, so no cross-stack/region CDK refs.

## File map

| Path | Role |
| --- | --- |
| `app.py` | Entrypoint. Account → stacks. Pins explicit `stack_name`s. |
| `cdk.json` | `app` cmd + `context` (account IDs, org id, bootstrap qualifier `watchtwr26`). Source of truth for IDs. |
| `Makefile` | The only deploy path. Guardrails baked in. |
| `configs/infrastructure.yaml` | `globals:` + `accounts:` list, deep-merged (account wins). |
| `configs/config.py` | Loader: `${ENV}` + `cdk.json` context substitution → typed `InfrastructureSpec`. |
| `configs/models.py` | Dataclasses (`LogArchiveConfig`, `WriterConfig`, `HomelabWriterConfig`, `ReaderConfig`, `InfrastructureSpec`), hydrated by dacite. |
| `stacks/log_archive_stack.py` | Logarchive side, per region. |
| `stacks/workload_writer_stack.py` | EKS workload side, per account (cross-account role). |
| `stacks/homelab_writer_stack.py` | Home-lab side: IAM user in the logarchive account (no role). |
| `utils/converters.py` | `update()` deep-merge helper. |
| `utils/logger.py` | Structured logging (`configure_logger`). |
| `docs/plan.md`, `docs/iam-s3-design.md` | Design + plan (section-numbered). |

## Commands

Set `ACCOUNT=logarchive|sandbox|development|production|homelab` (default
`logarchive`). The matching AWS SSO profile is `admin-<ACCOUNT>` — **except
`homelab`, which deploys via `admin-logarchive`** (its IAM user lives in the
logarchive account; the Makefile special-cases the profile).

```bash
make venv                 # create .venv and install requirements.txt
make synth   ACCOUNT=logarchive          # local synth, safe, no AWS calls
make diff    ACCOUNT=logarchive          # runs whoami first, then cdk diff
make deploy  ACCOUNT=logarchive          # gates on whoami + diff, then deploy --all
make bootstrap ACCOUNT=logarchive        # one-time, both regions, qualifier watchtwr26
make destroy ACCOUNT=logarchive CONFIRM=logarchive   # scope restatement required

make deploy  ACCOUNT=homelab             # creates the home-lab IAM user (via admin-logarchive)
```

After `make deploy ACCOUNT=homelab`, mint the access key once (CDK never
creates it):

```bash
aws iam create-access-key --user-name watchtower-writer-home --profile admin-logarchive
```

Direct CDK (rarely needed): `cdk -c account=<name> synth`.

## Guardrails (do not weaken these)

- `make deploy` runs `aws sts get-caller-identity` and `cdk diff` **before**
  applying. Keep identity + diff gates.
- `make destroy` refuses unless `CONFIRM=<account>` restates the scope.
- Never deploy to prod directly — dev → staging → prod with validation.
- Buckets are `RemovalPolicy.RETAIN`, block-public, force-HTTPS.

## Conventions

- **IAM least privilege:** explicit actions (no wildcards), explicit resource
  ARNs. Writer role is write-only (`s3:PutObject`, `s3:AbortMultipartUpload`),
  no delete anywhere. Trust-policy `Sid`s must be **alphanumeric**. The
  home-lab writer is an IAM **user** (not a role), same rules: write-only,
  explicit resources, no delete.
- **Config, not literals:** new tunables go in `models.py` + `infrastructure.yaml`.
  Account IDs / org id live in `cdk.json` context — never hardcode in stacks.
- **Python style:** ruff (Black-compatible), 88-char lines, double quotes,
  trailing commas, type hints, `logging` (never `print`), no bare `except:`.
- **Explicit over magic:** no `latest`, no hidden defaults; pin CFN names.

## Facts not to re-derive

- Regions `us-east-1` (primary) / `us-east-2`; short codes `ue1` / `ue2`.
- Deployer account `766789219588`, logarchive `766997230140`, audit (Cribl
  user) `698777852125`. Account IDs are **not secrets**.
- SSE is currently SSE-S3 (AES256). SSE-KMS is a planned future upgrade
  (`docs/plan.md` §10), not yet implemented.

## Verify before claiming done

Run `make synth` for both a logarchive and a workload account after changes;
both must synth cleanly. Use `make diff` to inspect changesets. Synth is local
and safe; never run `deploy`/`destroy` on your own initiative.

## Never

- Add a pipeline / CDK `Stage`.
- Commit secrets, `.env`, or credentials (`.env` is gitignored; `.env.example`
  is tracked). Account IDs are fine (public within the org). **Never create an
  IAM access key in CDK** — mint the home-lab key out of band, post-deploy.
- Commit directly to `main` — use `feature/*` or `fix/*` branches + PR.
- Weaken the identity/diff/destroy guardrails or flip stateful resources to
  `DESTROY`.
