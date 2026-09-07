# IAM Trust — Verified Reference

Status: verified against **live AWS** on 2026-09-06 (both roles present, trust
and permission policies match the CDK source exactly). This doc explains the
trust model for the two ends of the log pipeline and records the verification.

Source of truth in code:

- Writer role: `cdk/stacks/workload_writer_stack.py`
- Reader role: `cdk/stacks/log_archive_stack.py` (created only in the primary region)
- Config: `cdk/configs/infrastructure.yaml`

## 1. The two roles at a glance

| ARN | Account | Side | Created by |
| --- | --- | --- | --- |
| `arn:aws:iam::766997230140:role/watchtower-cribl-reader` | `766997230140` (logarchive) | READ | `LogArchiveStack` (primary region only) |
| `arn:aws:iam::304232106942:role/watchtower-writer-development` | `304232106942` (development) | WRITE | `WorkloadWriterStack` (per workload account) |

These are opposite ends of one pipeline: workload compute **writes** log objects
into the logarchive S3 buckets; Cribl **reads** them back out. Two deliberately
different trust models.

## 2. Accounts referenced

| Account ID | Name | Role in the pipeline |
| --- | --- | --- |
| `766997230140` | logarchive | Owns the buckets, bucket policy, SNS/SQS, and the Cribl reader role. |
| `304232106942` | development | A workload account; owns its shared writer role. |
| `698777852125` | audit / Cribl | Home of the `cribl-servicaccount` IAM user that assumes the reader role. |

Org ID gating the bucket policy: `o-x82cglkqhs`.

## 3. Writer trust — `watchtower-writer-development`

Trust is **intra-account, service-principal based**. No human and no external
account is trusted. Four AWS service principals in account `304232106942` may
assume it:

| Sid | Principal (Service) | Actions | Compute type |
| --- | --- | --- | --- |
| (seed, no Sid) | `ec2.amazonaws.com` | `sts:AssumeRole` | EC2 instance profile |
| `EksPodIdentity` | `pods.eks.amazonaws.com` | `sts:AssumeRole`, `sts:TagSession` | EKS Pod Identity |
| `EcsTaskRole` | `ecs-tasks.amazonaws.com` | `sts:AssumeRole` | ECS task role |
| `LambdaExecutionRole` | `lambda.amazonaws.com` | `sts:AssumeRole` | Lambda execution role |

`sts:TagSession` is present only on the EKS statement because Pod Identity
requires it.

Permission policy (`WriterRoleDefaultPolicyE203178C`) — write-only, own prefix,
both regional buckets:

- `WriteOwnAccountPrefix`: `s3:PutObject`, `s3:AbortMultipartUpload` on
  - `arn:aws:s3:::watchtower-logarchive-us-east-1-766997230140/304232106942/*`
  - `arn:aws:s3:::watchtower-logarchive-us-east-2-766997230140/304232106942/*`
- `GetBucketLocation`: `s3:GetBucketLocation` on both bucket ARNs.

No delete anywhere. The `304232106942/*` prefix confinement is enforced twice:
here on the identity side, and on the resource side by the bucket policy (see §5).

## 4. Reader trust — `watchtower-cribl-reader`

Trust is a genuine **cross-account AssumeRole with an ExternalId gate**, for a
single named third-party IAM user:

- Trusted principal: `arn:aws:iam::698777852125:user/cribl-servicaccount`
  (one specific user in the audit/Cribl account — not the whole account, not a service).
- Condition: `StringEquals { sts:ExternalId = watchtower-<redacted> }`
  (literal value lives in `cdk/configs/infrastructure.yaml` under `reader.external_id`).
- Action: `sts:AssumeRole`.

The ExternalId is the confused-deputy guard standard for SaaS assuming your role.
It is enforced correctly as a **single conditioned statement** — the principal
itself carries the condition, so there is no unconditioned allow to bypass it.
(Trust statements are OR'd; an unconditioned `assumed_by` plus a separate
conditioned statement would NOT enforce the ExternalId. The code avoids that.)

Permission policy (`CriblReaderRoleDefaultPolicyE40D9762`) — read + queue-drain:

- `ReadWatchtowerBuckets`: `s3:GetObject`, `s3:GetObjectTagging`, `s3:ListBucket` on
  both regional buckets and their `/*` contents.
- `ConsumeWatchtowerCriblQueues`: `sqs:ReceiveMessage`, `sqs:DeleteMessage`,
  `sqs:ChangeMessageVisibility`, `sqs:GetQueueAttributes`, `sqs:GetQueueUrl` on
  `arn:aws:sqs:*:766997230140:watchtower-cribl-*` (wildcard region + name covers
  both regions' queues).

Cribl's static keys never touch S3 directly — they are only used to assume this
role, which is where the S3/SQS permissions live.

## 5. End-to-end trust flow

```
 dev account (304232106942)                 logarchive account (766997230140)
 ┌───────────────────────────┐              ┌──────────────────────────────────────┐
 │ EKS pod / EC2 / ECS /      │              │  S3: watchtower-logarchive-ue1/ue2     │
 │ Lambda                     │  PutObject   │   bucket policy: Allow PutObject only   │
 │   │ assumes (service       │─────────────▶│   into ${aws:PrincipalAccount}/* for    │
 │   ▼  principal trust)      │  .../304232..│   org o-x82cglkqhs; explicit Deny on    │
 │ watchtower-writer-         │   /*         │   all DeleteObject*                     │
 │ development                │              │                                        │
 └───────────────────────────┘              │  S3 event -> SNS -> SQS (cribl queue)   │
                                            │                                        │
 audit / Cribl (698777852125)               │  watchtower-cribl-reader                │
 ┌───────────────────────────┐              │   trusts user cribl-servicaccount       │
 │ IAM user                   │  AssumeRole  │   @698777852125 + ExternalId gate       │
 │ cribl-servicaccount        │─────────────▶│   -> GetObject/ListBucket + SQS consume │
 │  (keys only assume role,   │  +ExternalId │                                        │
 │   never S3 directly)       │              │                                        │
 └───────────────────────────┘              └──────────────────────────────────────┘
```

Why two models:

1. **Write side** — the bucket doesn't trust the writer role by name. The bucket
   policy allows any org member (`aws:PrincipalOrgID = o-x82cglkqhs`) to
   `PutObject`, but only into `${aws:PrincipalAccount}/*`, so a role in
   `304232106942` is confined to `304232106942/*` on the resource side by AWS —
   independent of its identity policy. Onboarding a new workload account needs no
   bucket-policy change.
2. **Read side** — a specific external user assumes one role, ExternalId-gated.
   Permissions live on the role, not on the external user.

## 6. Verification record (2026-09-06)

Authenticated via AWS SSO (AdministratorAccess) per account:

- `admin-development` → `304232106942`
- `admin-logarchive` → `766997230140`

Checked with `iam get-role`, `iam list-role-policies`, `iam get-role-policy`:

| Item | Result |
| --- | --- |
| `watchtower-writer-development` trust policy | Matches CDK (4 service-principal statements; `TagSession` on EKS only). |
| `watchtower-writer-development` permission policy | Matches CDK (PutObject/AbortMultipartUpload on `304232106942/*` both regions; GetBucketLocation; no delete). |
| `watchtower-cribl-reader` trust policy | Matches CDK (single conditioned statement: user `cribl-servicaccount`@`698777852125` + ExternalId). |
| `watchtower-cribl-reader` permission policy | Matches CDK (S3 read on both buckets; SQS consume on `watchtower-cribl-*`). |

Both roles carry tags `project=cloud-watchtower`, `component=log-archive`, and
`watchtower-account=<development|logarchive>`. `MaxSessionDuration` is 3600s on
both. `RoleLastUsed` was empty on both at verification time (not yet exercised).

## 7. Note on the older design doc

`docs/iam-s3-design.md` §5 still describes the Cribl reader role as *"deferred —
high-level shape only."* That is stale: the reader role is fully implemented in
`log_archive_stack.py` and confirmed live here. Treat this file as the current
state for the reader side.
