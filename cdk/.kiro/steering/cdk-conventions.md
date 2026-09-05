---
inclusion: fileMatch
fileMatchPattern: "**/*.py"
---

# CDK / Python Conventions (cloud-watchtower)

Follow these when writing or changing Python/CDK code in this repo.

## Deploy discipline

- **`make deploy` is the only deploy path.** No pipeline, no CDK `Stage`.
  Do not add a pipeline construct or CodePipeline stack.
- `cdk diff` gates every deploy; identity (`aws sts get-caller-identity`) is
  checked first. The Makefile already enforces this — keep it that way.
- `make destroy` requires scope restatement (`CONFIRM=<account>`). Never remove
  that guard.

## Stacks

- Every stack pins an explicit `stack_name` so CloudFormation names are fixed
  and readable (`watchtower-logarchive-<short>`, `watchtower-writer-<account>`).
- One `LogArchiveStack` **per region**; one `WorkloadWriterStack` **per
  account**. Keep that shape.
- `HomelabWriterStack` is the non-EKS path: a single IAM **user** in the
  logarchive account (no role, no cross-account trust), write-only into a fixed
  literal prefix (`homelab/cauldron`). Never create its access key in CDK.
- The Cribl reader role is created **only in the primary region** stack
  (`is_primary`). IAM is global — do not duplicate it per region.
- Prefer deterministic ARNs (bucket/queue names are patterned) over
  cross-stack / cross-region CDK references.

## IAM — least privilege, always explicit

- **No wildcard IAM actions.** List every action explicitly.
- Scope resources to specific ARNs. The account-wide `watchtower-cribl-*` SQS
  wildcard is intentional (spans both regions, same account) — but S3 and role
  resources stay explicit.
- Writer role is **write-only** (`s3:PutObject`, `s3:AbortMultipartUpload`).
  No delete actions anywhere on the writer side; the bucket policy has an
  explicit delete deny as backstop.
- Trust-policy `Sid`s must be **alphanumeric** (CloudFormation rejects others —
  see the fix commit on the scaffold branch).

## Stateful resources

- S3 buckets use `RemovalPolicy.RETAIN` and `auto_delete_objects=False`.
  Never switch a prod stateful resource to `DESTROY`.

## Config

- Add new tunables to `configs/models.py` (typed dataclass) **and**
  `configs/infrastructure.yaml`, not as inline literals in stacks.
- Account IDs / org id belong in `cdk.json` context; never hardcode them in
  stack code. Read them through the resolved `InfrastructureSpec`.
- Keep everything explicit — no `latest`, no magic defaults that hide intent.

## Python style

- ruff (Black-compatible), 88-char lines, 4-space indent, double quotes,
  trailing commas on multi-line structures.
- Type hints on function signatures. `logging` module (via `utils.logger`),
  never `print`. Explicit exception handling — no bare `except:`.
- Imports sorted (stdlib / third-party / local, blank-line separated).

## Verify before claiming done

- `make synth ACCOUNT=logarchive` (and a workload account) must succeed after
  changes. Run `make diff` to review changesets. Synth is local and safe.
