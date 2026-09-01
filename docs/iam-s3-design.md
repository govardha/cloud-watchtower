# Log Archive S3 + IAM Design

Status: pinned for the reader/writer IAM workflow; not yet implemented in CDK.
Scope: how the logarchive bucket, its object key layout, and the IAM
roles/policies in both the logarchive account and the workload accounts fit
together. Cribl's reader-side setup is intentionally left light — that
pattern is already proven against CloudTrail and will be ported over, not
redesigned, when we get to it.

## 1. Accounts involved

| Account | Role in this design |
| --- | --- |
| logarchive | Owns the S3 bucket, its lifecycle policy, and the bucket policy. Also owns the (future) Cribl reader role. |
| sandbox / development / production (workload accounts) | Each owns exactly one shared writer IAM role, used by every EKS cluster, EC2 instance, ECS task, and Lambda function in that account that needs to write logs. |

Workload accounts today are `sandbox` (621648307412), `development`
(304232106942), `production` (953293104741), per
`cluster-cauldron/infra/eks/cdk/README.md`. More can be added later without
touching the logarchive side at all (see §6).

## 2. S3 bucket (logarchive account)

- Region: `us-east-1`.
- Name: `watchtower-logarchive` (placeholder — confirm final name before
  CDK).
- Versioning / lifecycle transitions: **not yet decided** — flagged as an
  open item in §8. Nothing in this design depends on the specific lifecycle
  rule values; rules apply at the `<account_id>/<source_type>/*` prefix
  level and key off object age (`LastModified`), not off the date segment
  embedded in the key name, so lifecycle can be layered in later without
  reshaping the key layout.
- No public access, SSE enabled (KMS or SSE-S3 — TBD, doesn't affect the
  IAM design below).

## 3. Object key naming convention

The EKS shape already exists in code —
`cluster-cauldron/apps/app-of-apps/children/splunk-otel/fluentbit/fluent-bit.conf`
has it commented out, pending a real bucket:

```
s3_key_format  /eks/$TAG[1]/%Y/%m/%d/$TAG[2]/$UUID.gz
```

where the Fluent Bit tag is composed as `eks.$fb_cluster.$fb_namespace`
(`otlp_k8s_tag.lua` lifts `k8s.cluster.name` / `k8s.namespace.name` off the
OTLP record at runtime — this is per-object, dynamic, and requires no
config change when a new cluster or namespace shows up).

Adding the account ID as the leading segment — so the bucket layout matches
what the bucket policy enforces (§5) and lets you tell at a glance which
account any given object came from:

```
/<account_id>/eks/<cluster>/%Y/%m/%d/<namespace>/<uuid>.gz
```

The account ID has no Kubernetes-resource-attribute equivalent, so it can't
be lifted dynamically the way cluster/namespace are — it has to be injected
as a static env var (`AWS_ACCOUNT_ID`) on the Fluent Bit Deployment per
cluster, interpolated into `s3_key_format` as `${AWS_ACCOUNT_ID}`. Since a
cluster lives in exactly one account for its whole life, this is a one-time
per-cluster value, not something that needs touching per app or per
namespace. (This is a cluster-cauldron-side wiring change, not something
cloud-watchtower's CDK owns — noted here for completeness.)

Extending the same `<account_id>/<source_type>/...` shape to the other
three source types, mirroring the EKS ordering (primary grouping, then
date, then finer grouping) where an equivalent exists:

| Source | Key format |
| --- | --- |
| EKS | `/<account_id>/eks/<cluster>/%Y/%m/%d/<namespace>/<uuid>.gz` |
| ECS | `/<account_id>/ecs/<cluster>/%Y/%m/%d/<service>/<uuid>.gz` |
| Lambda | `/<account_id>/lambda/<function_name>/%Y/%m/%d/<uuid>.gz` |
| EC2 (bare metal) | `/<account_id>/ec2/<app_name>/%Y/%m/%d/<instance_id>/<uuid>.gz` |

ECS mirrors EKS directly (cluster → date → service, in place of
cluster → date → namespace). Lambda and EC2 have no natural "cluster"
grouping, so the primary grouping is the function name / app name
directly, with date immediately after.

**Not yet decided**: which shipper handles ECS, Lambda, and EC2 log
delivery (Fluent Bit sidecar / `awsfirelens`, a Lambda extension, Kinesis
Firehose off a CloudWatch Logs subscription filter, etc). Only the EKS path
has a concrete implementation today. The key formats above are the target
shape regardless of shipper — whichever tool is picked needs to be able to
produce (or be configured to produce) this layout.

## 4. Naming collisions

S3 has no "create this prefix, fail if taken" primitive. Prefix collisions
are prevented above AWS, at config/synth time: a registry
(`configs/log_sources.yaml` in the future CDK app) will list every
`(source_type, workload_account, grouping_name)` tuple the pipeline knows
about, and the config loader raises before synth if two entries would
resolve to the same key prefix. This is a soft guarantee — something
writing outside this pipeline could still collide — acceptable for a
controlled, GitOps-driven workflow.

## 5. IAM in the logarchive account

**Bucket policy** — one permanent statement, using the `${aws:PrincipalAccount}`
policy variable so no per-account or per-app statement is ever added:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "org-write-own-account-prefix-only",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::watchtower-logarchive/${aws:PrincipalAccount}/*",
      "Condition": {
        "StringEquals": { "aws:PrincipalOrgID": "o-abc123xyz" }
      }
    },
    {
      "Sid": "deny-delete-everyone",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:DeleteObject*",
      "Resource": "arn:aws:s3:::watchtower-logarchive/*"
    }
  ]
}
```

`${aws:PrincipalAccount}` resolves per-request to the calling principal's
own AWS account ID, so a role in account `621648307412` can only ever
`PutObject` under `621648307412/*` — enforced by AWS on the resource side,
not just trusted from the identity-policy side. Any AWS Organizations
member account can write, but only into its own prefix. No statement here
is ever touched when a workload account, app, or cluster is added or
removed.

**Cribl reader role** — deferred. High-level shape only (already proven
against CloudTrail, will be reused, not redesigned): an IAM user in
logarchive whose only permission is `sts:AssumeRole` on a dedicated
`watchtower-cribl-reader` role, which carries `s3:GetObject` /
`s3:ListBucket` on the bucket plus SQS consumer permissions on the S3
event-notification queue. Static keys never touch S3 directly — only used
to assume the role. Full detail to follow once the writer side is built and
we're ready to wire up ingestion.

## 6. IAM in the workload accounts

One shared IAM role per workload account — not per app, not per cluster.
Every EKS pod (via Pod Identity), EC2 instance (via instance profile), ECS
task (via task role), and Lambda function (via execution role) in that
account uses the *same* role.

```
sandbox (621648307412):     watchtower-writer-sandbox
development (304232106942): watchtower-writer-development
production (953293104741):  watchtower-writer-production
```

**Trust policy** (identical shape in every workload account — trusts every
compute type that might need to write logs from that account):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "eks-pod-identity",
      "Effect": "Allow",
      "Principal": { "Service": "pods.eks.amazonaws.com" },
      "Action": ["sts:AssumeRole", "sts:TagSession"]
    },
    {
      "Sid": "ec2-instance-profile",
      "Effect": "Allow",
      "Principal": { "Service": "ec2.amazonaws.com" },
      "Action": "sts:AssumeRole"
    },
    {
      "Sid": "ecs-task-role",
      "Effect": "Allow",
      "Principal": { "Service": "ecs-tasks.amazonaws.com" },
      "Action": "sts:AssumeRole"
    },
    {
      "Sid": "lambda-execution-role",
      "Effect": "Allow",
      "Principal": { "Service": "lambda.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

**Identity (permissions) policy** — same shape in every account, only the
account ID in the Resource ARN changes:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "write-own-account-prefix",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:AbortMultipartUpload",
        "s3:GetBucketLocation"
      ],
      "Resource": "arn:aws:s3:::watchtower-logarchive/621648307412/*"
    }
  ]
}
```

No `s3:DeleteObject*` anywhere in this policy (and the bucket-side explicit
deny covers it even if that were ever added by mistake). No per-app, per-
cluster, or per-namespace scoping — the account-level prefix is the only
boundary, matching the "not too granular, don't want to redeploy for every
app" requirement.

## 7. How EKS Pod Identity actually adopts the role

Pod Identity is a *binding*, separate from the role and separate from the
pod's own manifest:

1. The `eks-pod-identity-agent` EKS addon runs as a DaemonSet on every
   node (already part of the addon set in `eks/cdk`'s Phase 1 scaffold).
2. `watchtower-writer-sandbox` (etc.) is created once per account, per §6
   above — this does not change per cluster.
3. For each (cluster, namespace, service account) that needs to write logs,
   a **Pod Identity Association** is created — an EKS API object
   (`CreatePodIdentityAssociation`, or the CDK `CfnPodIdentityAssociation`
   construct) binding `(cluster_name, namespace, service_account_name)` →
   `watchtower-writer-sandbox` role ARN. For the current bootstrap that's
   one association: cluster `eks-sandbox` (or whichever future EKS
   cluster), namespace `splunk-otel`, service account
   `splunk-otel-s3-writer` (already scaffolded in
   `fluentbit/serviceaccount.yaml`, currently unused since the sink is
   `out_null`).
4. At pod start, the agent DaemonSet on that node detects the pod's service
   account matches an association, and injects short-lived credentials
   (via a local credential endpoint the AWS SDK's default chain checks
   automatically) — no static keys, no OIDC/IRSA annotation on the service
   account, no code change in Fluent Bit itself. The SDK's "Auto"-equivalent
   credential resolution just finds them.
5. Since the role is shared per account (not per cluster), adding a second
   EKS cluster in the same account only requires **one more Pod Identity
   Association** — the IAM role, trust policy, and identity policy are
   already in place and untouched.

**EC2 / ECS / Lambda** are simpler — no association step. The same
`watchtower-writer-<account>` role is attached directly as the EC2 instance
profile, the ECS task role, or the Lambda execution role, respectively.
Nothing else changes.

## 8. Deployment mechanics — what triggers a deploy

| Event | Logarchive redeployed? | Workload account redeployed? |
| --- | --- | --- |
| New app/service in an existing cluster/account | No | No |
| New EKS cluster in an already-onboarded account | No | No — one Pod Identity Association added (cheap, not a role/policy change) |
| New workload account onboarded | No (bucket policy already covers any org member via `${aws:PrincipalAccount}`) | Yes — one `WorkloadWriterStack` deploy, one time |
| Bucket lifecycle/retention change | Yes | No |

Two stacks: `LogArchiveStack` (bucket, bucket policy, lifecycle — deployed
once to logarchive) and `WorkloadWriterStack` (one shared role + trust +
identity policy — deployed once per workload account). Since account IDs
and the key-prefix scheme are static/known ahead of time, neither stack
needs cross-stack references or SSM parameter exports to synth correctly.

## 9. Open items (explicitly deferred, not blocking the writer-side build)

- Final bucket name and lifecycle policy values (retention days, storage
  class transitions).
- SSE choice (SSE-S3 vs SSE-KMS) and, if KMS, key policy implications for
  the writer roles (`kms:GenerateDataKey` would need to be added to the
  identity policy in §6 if a CMK is used).
- Cribl reader role — full detail, SQS/event-notification config,
  cross-account AssumeRole chain specifics (pattern already proven on
  CloudTrail; port, don't redesign).
- ECS / Lambda / EC2 log-shipping mechanism (which tool produces the §3 key
  layout for those three source types — none are built yet, only EKS via
  Fluent Bit).
- `AWS_ACCOUNT_ID` injection into the Fluent Bit Deployment per cluster
  (cluster-cauldron-side change, not cloud-watchtower CDK).
- Org ID (`o-abc123xyz` above is a placeholder) needed for the bucket
  policy condition.
