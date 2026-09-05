# Cloud Watchtower — Log Archive S3 + IAM Deployment Plan

Status: **PLAN — not yet implemented in CDK.** This is the concrete, named
build plan that turns [`iam-s3-design.md`](./iam-s3-design.md) into a
deployable CDK app, following the cluster-cauldron CDK style
(`/home/govardha/repos/cluster-cauldron/infra/eks/cdk`).

Scope of this plan (in order):

1. CDK-bootstrap the **deployment** account and establish cross-account trust
   into logarchive / development / sandbox / production — in **us-east-1 AND
   us-east-2**, under a **fresh, watchtower-dedicated bootstrap qualifier**
   (`watchtwr26`) isolated from the existing `govjuly25`/`security` bootstraps.
2. Create the logging bucket in **us-east-1 and us-east-2** (two independent
   regional buckets, named `watchtower-logarchive-<region>-766997230140`).
3. Reader side: S3 → SNS → SQS fan-out, a logarchive reader role, and the
   Cribl IAM user story (reuse the existing audit-account service account —
   see §6).
4. Deploy stacks manually (`make deploy`), but design for CodePipeline (§8).

Everything below uses **real** org values discovered from the live accounts,
not placeholders.

---

## 0. Ground truth (discovered from the live org)

### Accounts (`aws organizations list-accounts`, org `o-x82cglkqhs`)

| Account     | ID             | SSO profile         | Role in this design                          |
| ----------- | -------------- | ------------------- | -------------------------------------------- |
| Management  | `634946559451` | `admin-management`  | org root (not a deploy target)               |
| Deployment  | `766789219588` | `admin-deployment`  | **CDK deployer** — trusted into all targets  |
| LogArchive  | `766997230140` | `admin-logarchive`  | owns buckets, SNS, SQS, reader role          |
| Audit       | `698777852125` | `admin-audit`       | owns the existing `cribl-servicaccount` user |
| Sandbox     | `621648307412` | `admin-sandbox`     | workload — writer role                        |
| Development | `304232106942` | `admin-development` | workload — writer role                        |
| Production  | `953293104741` | `admin-production`  | workload — writer role                        |

- **Org ID** (bucket-policy condition `aws:PrincipalOrgID`): `o-x82cglkqhs`
  (replaces the `o-abc123xyz` placeholder in the design doc §5).
- CDK bootstrap **qualifier for this project**: **`watchtwr26`** (fresh,
  dedicated shortcode — verified free in all accounts/regions; valid per the
  `[a-z0-9]{1,10}` rule). Toolkit stack name: `CDKToolkit-watchtwr26`.
  - This is **deliberately separate** from the existing `govjuly25`
    (cluster-cauldron / EKS) and `security` qualifiers. A fresh qualifier gets
    its own isolated bootstrap roles + assets bucket + ECR repo in every
    account, so watchtower's deploy blast-radius and cross-account trust are
    scoped to this project alone and never entangle the EKS bootstrap.
  - **Consequence:** the "us-east-1 already bootstrapped/trusted" state below
    belongs to `govjuly25` and does **not** carry over. Under `watchtwr26`,
    **every** account × **both** regions must be bootstrapped fresh (§2).

### Bootstrap + trust — existing `govjuly25` state (reference only)

`cdk-govjuly25-*` roles inspected in each account; `/cdk-bootstrap/govjuly25/version`
SSM param checked per region. **This is the EXISTING (cluster-cauldron)
bootstrap — shown to explain why a fresh qualifier is clean, NOT what
watchtower uses.**

| Account     | us-east-1 (`govjuly25`) | trusts `766789219588`? | us-east-2 (`govjuly25`) |
| ----------- | ----------------------- | ---------------------- | ----------------------- |
| Deployment  | v28 ✅                  | (self)                 | ❌ none                 |
| LogArchive  | v28 ✅                  | ✅ yes                  | ❌ none                 |
| Sandbox     | v31 ✅                  | ✅ yes                  | ❌ none                 |
| Development | v28 ✅                  | ✅ yes                  | ❌ none                 |
| Production  | v28 ✅                  | ✅ yes                  | ❌ none                 |

### Bootstrap + trust — watchtower's `watchtwr26` qualifier (what we build)

Verified free (`/cdk-bootstrap/watchtwr26/version` absent) in the deployment
account for both regions. A fresh qualifier shares nothing with `govjuly25`, so
**everything below must be created**:

| Account     | us-east-1 (`watchtwr26`) | us-east-2 (`watchtwr26`) |
| ----------- | ------------------------ | ------------------------ |
| Deployment  | bootstrap (no trust)     | bootstrap (no trust)     |
| LogArchive  | bootstrap + `--trust`    | bootstrap + `--trust`    |
| Sandbox     | bootstrap + `--trust`    | bootstrap + `--trust`    |
| Development | bootstrap + `--trust`    | bootstrap + `--trust`    |
| Production  | bootstrap + `--trust`    | bootstrap + `--trust`    |

**Conclusion:** bootstrap all 5 accounts × 2 regions under `watchtwr26`. You'll
mostly deploy in us-east-1, but us-east-2 is bootstrapped too (cost is
minimal — a few KMS/S3/role resources, effectively free until used) so the
second region is ready on demand.

### The proven CloudTrail → Cribl pattern (reference to port, not redesign)

Discovered in the **logarchive** account (`766997230140`, us-east-1), deployed
by a CDK stack named `Cribl-CriblLogArchive`:

- **Bucket** `gov-aws-cloudtrail-766997230140`
  - SSE-S3 (`AES256`), `BucketKeyEnabled: false`, SSE-C blocked.
  - Lifecycle: expire objects at **60 days** (prefix-wide).
  - Bucket policy: CloudTrail service ACL check + write, plus a
    `Deny * when aws:SecureTransport=false` (force-HTTPS) statement.
  - S3 notification: `s3:ObjectCreated:*` → SNS topic below.
- **SNS topic** `cloudtrail-notifications`
  - Topic policy allows `s3.amazonaws.com` `sns:Publish`, scoped by BOTH
    `aws:SourceAccount = 766997230140` and `aws:SourceArn = <bucket ARN>`.
  - One confirmed SQS subscription (the queue below).
- **SQS queue** `cribl-servicaccountQueue`
  - Queue policy allows `sns.amazonaws.com` `sqs:SendMessage`, scoped by
    `aws:SourceArn = <topic ARN>`.
  - VisibilityTimeout `300`s, MessageRetentionPeriod `1209600`s (14 days).
- **Reader role** `IAMCriblLogProcessingRole` (in logarchive)
  - **Trusts** `arn:aws:iam::698777852125:user/cribl-servicaccount` (the AUDIT
    account user) for `sts:AssumeRole`, gated by
    `sts:ExternalId = cribl-47f9f8c5-ce91-458c-aab0-f02f7329fd95`.
  - Inline policy `CriblS3Access`:
    - `s3:GetObject`, `s3:GetObjectTagging`, `s3:ListBucket` on the bucket.
    - `sqs:ChangeMessageVisibility`, `sqs:DeleteMessage`,
      `sqs:GetQueueAttributes`, `sqs:GetQueueUrl`, `sqs:ReceiveMessage` on
      `arn:aws:sqs:us-east-1:766997230140:cribl*Queue` (note the **`cribl*Queue`
      wildcard** — already forward-compatible with new queues).
- **The Cribl user itself** (`arn:aws:iam::698777852125:user/cribl-servicaccount`,
  audit account): has **zero** inline/managed policies and one active access
  key. It is *only* an STS launch-pad — all real permission lives in the
  logarchive role it assumes. This is exactly the design-doc §5 shape ("static
  keys never touch S3 directly — only used to assume the role"), except the
  user lives in Audit, not LogArchive.

This is the template every part of the writer/reader plan below mirrors.

---

## 1. Naming scheme (all concrete names)

Region short-codes: **`ue1`** = us-east-1, **`ue2`** = us-east-2.

### Buckets (logarchive account, one per region)

Account-suffixed, full region name (mirrors the existing
`gov-aws-cloudtrail-766997230140` convention and sidesteps global-uniqueness
collisions):

| Region    | Bucket name                                     |
| --------- | ----------------------------------------------- |
| us-east-1 | `watchtower-logarchive-us-east-1-766997230140`  |
| us-east-2 | `watchtower-logarchive-us-east-2-766997230140`  |

> Name pattern: `watchtower-logarchive-<region>-766997230140`, where
> `<region>` is the full region string (`us-east-1`, not the `ue1` shortcode).
> The `ue1`/`ue2` shortcodes are still used for the shorter SNS/SQS/stack
> resource names below.

### Reader side (logarchive account, one set per region)

| Resource      | us-east-1                                | us-east-2                                |
| ------------- | ---------------------------------------- | ---------------------------------------- |
| SNS topic     | `watchtower-logarchive-notifications-ue1`| `watchtower-logarchive-notifications-ue2`|
| SQS queue     | `watchtower-cribl-reader-ue1`            | `watchtower-cribl-reader-ue2`            |
| SQS DLQ       | `watchtower-cribl-reader-dlq-ue1`        | `watchtower-cribl-reader-dlq-ue2`        |
| (future) Loki | `watchtower-loki-reader-ue1`             | `watchtower-loki-reader-ue2`             |

> The SQS queue names deliberately start with `watchtower-cribl-` so that a
> single reader-role resource wildcard (`watchtower-cribl-*` or `watchtower-*-reader-*`)
> covers both regions and future queues — same trick as the existing
> `cribl*Queue` wildcard.

### Reader role + user (logarchive + audit)

| Resource                    | Name / ARN                                                              |
| --------------------------- | ----------------------------------------------------------------------- |
| Reader role (logarchive)    | `watchtower-cribl-reader` (`arn:aws:iam::766997230140:role/watchtower-cribl-reader`) |
| Cribl user (audit, reused)  | `arn:aws:iam::698777852125:user/cribl-servicaccount`                    |
| External ID (new, this app) | `watchtower-2675e497-b719-409b-b81a-57e9ca59976e` (distinct from the CloudTrail one) |

> **DECIDED:** reuse the existing audit-account `cribl-servicaccount` user (no
> new IAM user), with a **new dedicated role** `watchtower-cribl-reader` and a
> **new, distinct ExternalId** (above) — separate from the CloudTrail role's
> `cribl-47f9f8c5-…`. See §6.

### Writer roles (workload accounts, one shared role per account)

Per design doc §6, unchanged names:

| Account     | Writer role name                | Role ARN                                                 |
| ----------- | ------------------------------- | -------------------------------------------------------- |
| Sandbox     | `watchtower-writer-sandbox`     | `arn:aws:iam::621648307412:role/watchtower-writer-sandbox`     |
| Development | `watchtower-writer-development` | `arn:aws:iam::304232106942:role/watchtower-writer-development` |
| Production  | `watchtower-writer-production`  | `arn:aws:iam::953293104741:role/watchtower-writer-production`  |

> The writer role is **account-scoped, not region-scoped** — one role per
> account writes to both regional buckets (Resource lists both bucket ARNs).

### Object key layout (unchanged from design doc §3)

```
/<account_id>/eks/<cluster>/%Y/%m/%d/<namespace>/<uuid>.gz
/<account_id>/ecs/<cluster>/%Y/%m/%d/<service>/<uuid>.gz
/<account_id>/lambda/<function_name>/%Y/%m/%d/<uuid>.gz
/<account_id>/ec2/<app_name>/%Y/%m/%d/<instance_id>/<uuid>.gz
```

---

## 2. Bootstrap plan (deployment account + trust)

Everything uses the **fresh qualifier `watchtwr26`** (toolkit stack
`CDKToolkit-watchtwr26`). Because it's brand new, **all 5 accounts × both
regions** get bootstrapped — nothing from `govjuly25` carries over.

CDK bootstrap trust is expressed with `--trust <deployment-acct>` and
`--cloudformation-execution-policies`. The deployment account `766789219588` is
the trusted deployer for every target.

Order (run from the `cdk` app dir; `make bootstrap` wraps this — see §7). Do
**both** regions for each account:

```bash
# 1. Deployment account itself — both regions (no --trust; it's the deployer)
for R in us-east-1 us-east-2; do
  cdk bootstrap "aws://766789219588/${R}" \
    --profile admin-deployment \
    --qualifier watchtwr26 \
    --toolkit-stack-name CDKToolkit-watchtwr26
done

# 2. Each TARGET account — both regions, trusting the deployment account.
#    Repeat this block for: logarchive / sandbox / development / production,
#    swapping the profile + account id.
#      logarchive  766997230140  admin-logarchive
#      sandbox     621648307412  admin-sandbox
#      development 304232106942  admin-development
#      production  953293104741  admin-production
for R in us-east-1 us-east-2; do
  cdk bootstrap "aws://766997230140/${R}" \
    --profile admin-logarchive \
    --qualifier watchtwr26 \
    --toolkit-stack-name CDKToolkit-watchtwr26 \
    --trust 766789219588 \
    --cloudformation-execution-policies arn:aws:iam::aws:policy/AdministratorAccess
done
```

Notes:
- **Qualifier must match the app.** `cdk.json` sets
  `@aws-cdk/core:bootstrapQualifier: watchtwr26`; every `cdk deploy` then
  targets the `cdk-watchtwr26-*` roles. A mismatch = "unable to resolve
  bootstrap role" at deploy time.
- **`--cloudformation-execution-policies`**: `AdministratorAccess` is the CDK
  default and matches the existing `govjuly25` bootstrap. Tighten later to a
  scoped policy if desired (**OPEN ITEM**), but keep it consistent across all
  accounts so behavior is uniform.
- **Cost of the idle second region**: a bootstrap is just an S3 assets bucket
  (empty), an ECR repo (empty), a handful of IAM roles, and one SSM param — no
  standing compute, effectively $0 until an asset is published. Bootstrapping
  us-east-2 now (per your call) just makes it deploy-ready with no ongoing cost.

Bootstrap targets summary (all fresh under `watchtwr26`):

| Account     | us-east-1             | us-east-2             |
| ----------- | --------------------- | --------------------- |
| Deployment  | bootstrap (no trust)  | bootstrap (no trust)  |
| LogArchive  | bootstrap + `--trust` | bootstrap + `--trust` |
| Sandbox     | bootstrap + `--trust` | bootstrap + `--trust` |
| Development | bootstrap + `--trust` | bootstrap + `--trust` |
| Production  | bootstrap + `--trust` | bootstrap + `--trust` |

---

## 3. Stacks & multi-region model

Two logical stacks (design doc §8), each instantiated **per region** so the
two regional buckets are fully independent (no CRR, per your decision):

| Stack                 | Account(s)        | Regions        | Explicit stack name                                      |
| --------------------- | ----------------- | -------------- | -------------------------------------------------------- |
| `LogArchiveStack`     | logarchive        | ue1, ue2       | `watchtower-logarchive-ue1`, `watchtower-logarchive-ue2` |
| `WorkloadWriterStack` | sandbox/dev/prod  | (account-wide) | `watchtower-writer-<account>`                            |

- `LogArchiveStack` owns, **per region**: the bucket + bucket policy +
  lifecycle, the SNS topic, the SQS queue (+ DLQ), the topic→queue
  subscription, the S3→SNS notification, and (region-agnostic, created once)
  the `watchtower-cribl-reader` role.
  - **The reader IAM role is global** (IAM is a global service). **Decision:**
    create it in the `ue1` instance; the `ue2` instance references it by ARN in
    the queue policy / grants. (Role's SQS resource wildcard already spans both
    regions.)
- `WorkloadWriterStack` owns the one shared writer role + trust policy +
  identity policy for that account. Its identity policy `Resource` lists
  **both** bucket ARNs (`watchtower-logarchive-us-east-1-766997230140/<acct>/*`
  and `…-us-east-2-766997230140/<acct>/*`).

Because account IDs, bucket names, and the org ID are all static/known, no
cross-stack refs or SSM exports are needed (design doc §8 holds).

### Stack names — pin them explicitly

Every stack passes an explicit **`stack_name=`** so the CloudFormation name is
fixed and readable, independent of construct id:

```python
LogArchiveStack(
    app,
    "LogArchiveUe1",                          # construct id (internal)
    stack_name="watchtower-logarchive-ue1",   # pinned CFN name
    ...
)
```

- Fixed names: `watchtower-logarchive-ue1`, `watchtower-logarchive-ue2`,
  `watchtower-writer-sandbox`, `watchtower-writer-development`,
  `watchtower-writer-production`.
- A tiny shared helper (`make_stack_name(component, scope_key)`) is the single
  source of truth for these names.

> **Lab: one deploy path only — `make deploy`.** No pipeline, no CDK `Stage`
> wrappers (see §8 for why, and for the work-time approach). Because there is
> exactly one way these stacks are ever synthesized, the whole "stack name /
> logical-id changes depending on how it's deployed" problem simply **does not
> exist** here. `stack_name=` is pinned purely for readable, predictable CFN
> names — not to reconcile two deploy paths.

### App wiring (`app.py`)

- Account-driven via the config loader (`get_infrastructure_info(account_name)`,
  super-fiesta style — see §7). The target is chosen by context
  (`-c account=<name>`) or the `WATCHTOWER_ACCOUNT` env var.
  - `account=logarchive` → synth `watchtower-logarchive-ue1` +
    `watchtower-logarchive-ue2`.
  - `account=sandbox|development|production` → synth
    `watchtower-writer-<account>`.
- Every stack is created **directly under the `App`** (no `Stage`), passes an
  explicit `stack_name=`, and is deployed with `make deploy`.
- `cdk.Tags.of(app)`: `project=cloud-watchtower`.

---

## 4. LogArchiveStack — resource detail (per region)

Mirrors the proven CloudTrail pattern from §0.

**Bucket** `watchtower-logarchive-<region>-766997230140`
(`…-us-east-1-…` / `…-us-east-2-…`):
- `block_public_access = BLOCK_ALL`, `enforce_ssl = True` (adds the
  force-HTTPS deny — matches the CloudTrail bucket).
- Encryption: **SSE-S3 (AES256)** to match the existing bucket. (SSE-KMS is an
  open item — design doc §9; if chosen, writer policy needs
  `kms:GenerateDataKey`.)
- `removal_policy = RETAIN` + `auto_delete_objects = False` (steering:
  RETAIN on stateful prod resources — the bucket container is retained; the
  30-day lifecycle handles object turnover).
- Versioning: default **OFF** (matches CloudTrail bucket; lifecycle keys off
  `LastModified` regardless).
- **Lifecycle: expire ALL objects at 30 days** (`Expiration = Duration.days(30)`,
  prefix-wide, age-based on `LastModified`) — **DECIDED**. No storage-class
  transitions; data is simply tossed after 30 days. This is the confirmed
  retention value (was an open item in the design doc §9).
- **Also abort incomplete multipart uploads after 7 days**
  (`abort_incomplete_multipart_upload_after = Duration.days(7)`). Failed/partial
  Fluent Bit multipart PUTs otherwise leave orphaned parts that are **billed
  indefinitely and don't appear in normal object listings** — a silent
  cost-creep source. One rule, closes it off. (Cheap-lab hygiene; keep it at
  work too.)

**Bucket policy** (design doc §5, with the real org ID):
- `Allow s3:PutObject` on
  `arn:aws:s3:::watchtower-logarchive-<region>-766997230140/${aws:PrincipalAccount}/*`
  for `Principal: *` with `Condition StringEquals aws:PrincipalOrgID = o-x82cglkqhs`.
- `Deny s3:DeleteObject*` on
  `arn:aws:s3:::watchtower-logarchive-<region>-766997230140/*` for
  `Principal: *`.
- (enforce_ssl adds the SecureTransport deny automatically.)

**SNS topic** `watchtower-logarchive-notifications-<r>`:
- Topic policy: allow `s3.amazonaws.com` `sns:Publish` scoped by
  `aws:SourceAccount = 766997230140` AND `aws:SourceArn = <bucket ARN>`.

**SQS queue** `watchtower-cribl-reader-<r>` (+ DLQ `…-dlq-<r>`):
- VisibilityTimeout `300`s, retention `1209600`s (14 days) — matches proven.
- SSE: SQS-managed. `enforce_ssl = True`.
- Redrive to DLQ, `maxReceiveCount` ~5.
- Queue policy: allow `sns.amazonaws.com` `sqs:SendMessage` scoped by
  `aws:SourceArn = <topic ARN>`.

**Wiring**:
- S3 `s3:ObjectCreated:*` notification → SNS topic.
- SNS → SQS subscription (`raw_message_delivery = True` recommended for
  Cribl's S3 source event shape; confirm against Cribl S3 source expectations).

**Reader role** `watchtower-cribl-reader` (created in ue1 instance only):
- Trust: `Principal AWS = arn:aws:iam::698777852125:user/cribl-servicaccount`,
  `Action sts:AssumeRole`, `Condition StringEquals sts:ExternalId =
  watchtower-2675e497-b719-409b-b81a-57e9ca59976e`.
- Inline policy:
  - `s3:GetObject`, `s3:GetObjectTagging`, `s3:ListBucket` on **both** bucket
    ARNs (+ `/*`) — `watchtower-logarchive-us-east-1-766997230140` and
    `…-us-east-2-766997230140`.
  - `sqs:ChangeMessageVisibility`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes`,
    `sqs:GetQueueUrl`, `sqs:ReceiveMessage` on
    `arn:aws:sqs:*:766997230140:watchtower-cribl-*` (spans both regions).

**Outputs** (per region): bucket name/ARN, topic ARN, queue ARN + URL, and
(ue1) the reader role ARN + the ExternalId hint — the exact values to paste
into Cribl's S3 Source (Queue ARN, Region, AssumeRole ARN, External ID).

---

## 5. WorkloadWriterStack — resource detail (per workload account)

Design doc §6, unchanged shape; both bucket ARNs in the identity policy.

**Role** `watchtower-writer-<profile>`:
- Trust policy — four service principals (design doc §6):
  - `pods.eks.amazonaws.com` → `sts:AssumeRole`, `sts:TagSession` (Pod Identity)
  - `ec2.amazonaws.com` → `sts:AssumeRole`
  - `ecs-tasks.amazonaws.com` → `sts:AssumeRole`
  - `lambda.amazonaws.com` → `sts:AssumeRole`
- Identity policy — `s3:PutObject`, `s3:AbortMultipartUpload`,
  `s3:GetBucketLocation` on:
  - `arn:aws:s3:::watchtower-logarchive-us-east-1-766997230140/<account_id>/*`
  - `arn:aws:s3:::watchtower-logarchive-us-east-2-766997230140/<account_id>/*`
  - (+ bucket-level ARNs for `GetBucketLocation`)
  - **No** `s3:DeleteObject*` anywhere (bucket-side deny is the backstop).

**Pod Identity association** (EKS) is a cluster-cauldron-side concern (design
doc §7), NOT owned here — noted for completeness. EC2/ECS/Lambda attach the
role directly (instance profile / task role / execution role).

---

## 6. The Cribl IAM user question — DECIDED: reuse audit user + new role

You flagged whether to follow the same audit-account IAM-user workflow.
**Decision: reuse the existing audit user, add a NEW dedicated reader role with
a NEW ExternalId.**

**What exists:** `arn:aws:iam::698777852125:user/cribl-servicaccount` (audit),
zero own-permissions, one active access key, used purely to `sts:AssumeRole`
into logarchive's `IAMCriblLogProcessingRole` (ExternalId-gated).

**Decision detail:**

- **Reuse the user** — Cribl already holds its access key; one service-account
  identity for "Cribl reads AWS logs" is cleaner than minting a second.
- **New role, new ExternalId** — do NOT overload `IAMCriblLogProcessingRole`
  (that's CloudTrail-scoped). Create `watchtower-cribl-reader` with its own
  ExternalId (`watchtower-2675e497-b719-409b-b81a-57e9ca59976e`) so the two log
  domains are independently revocable and auditable.
- **Cross-account trust already works** — the audit→logarchive AssumeRole path
  is proven; we're adding one more target role, not a new trust topology.

**Alternatives (if you decide otherwise):**
- **New user in logarchive** (matches design-doc §5 literal wording: "an IAM
  user in logarchive"). Cleaner blast-radius (reader identity co-located with
  the bucket) but means a second Cribl credential to manage/rotate. Pick this
  only if you want watchtower fully decoupled from the audit account.
- **Extend `IAMCriblLogProcessingRole`** to also read the watchtower buckets —
  least new infra, but couples CloudTrail and app-log access into one role
  (worse least-privilege / revocability). Not recommended.

All the material decisions are now made (§1 names, §4 lifecycle, §6 reader
identity). What remains before implementation are the smaller open items in §9
(SSE choice, `raw_message_delivery`, CFN exec policy) — none block scaffolding.

---

## 7. Repo layout, config & Makefile (super-fiesta config style)

**Config style: super-fiesta, not the cluster-cauldron `.env`-first workflow.**
You prefer super-fiesta's layout, where **account IDs and cross-account
constants live in `cdk.json` `context`** (committed, not secrets) and the YAML
carries the per-domain config, deep-merged globals→account. `.env` is optional
(only for values you'd rather not commit, e.g. a notification email). The
loader is `AppConfigs.get_infrastructure_info(<account_name>)`.

```
cdk/
  app.py                  account-driven; every stack pins explicit stack_name=,
                          created directly under App (no Stage wrappers)
  cdk.json                app=python3 app.py; context holds bootstrapQualifier
                          (watchtwr26) + ALL account IDs + org id (super-fiesta style)
  Makefile                whoami / synth / diff / deploy / bootstrap gates
  requirements.txt        aws-cdk-lib, constructs, dacite, pyyaml, python-dotenv
  configs/
    __init__.py
    infrastructure.yaml   globals: + accounts:  (deep-merge, account wins) —
                          bucket names, regions, retention(30d), org id,
                          external id, reader/writer settings
    models.py             dacite dataclasses: LogArchiveConfig, ReaderConfig,
                          WriterConfig, InfrastructureSpec (optional blocks -> None)
    config.py             AppConfigs.get_infrastructure_info(account_name):
                          ${ENV}+context substitution, deep-merge, from_dict
    log_sources.yaml      (future) prefix-collision registry (design doc §4)
  utils/
    __init__.py
    converters.py         to_dict + recursive update() merge (ported verbatim)
  stacks/
    __init__.py
    log_archive_stack.py  bucket + policy + 30d lifecycle + SNS + SQS + reader role
    workload_writer_stack.py  shared writer role + trust + identity policy
```

> **No `stages/`, no `pipeline/`, no `constants.py`.** The lab deploys with
> `make deploy` only. When you build the pipeline at work, add those then — see
> §8 for the recommended work-time approach.

**`cdk.json` context (super-fiesta pattern — account IDs are committed, not
secrets):**

```json
{
  "app": "python3 app.py",
  "context": {
    "@aws-cdk/core:bootstrapQualifier": "watchtwr26",
    "@aws-cdk/aws-iam:minimizePolicies": true,
    "deployment_account_id": "766789219588",
    "deployment_account_region": "us-east-1",
    "logarchive_account_id": "766997230140",
    "audit_account_id": "698777852125",
    "sandbox_account_id": "621648307412",
    "development_account_id": "304232106942",
    "production_account_id": "953293104741",
    "org_id": "o-x82cglkqhs"
  }
}
```

**`configs/infrastructure.yaml` (globals + per-account, deep-merged):**

```yaml
globals:
  log_archive:
    bucket_name_pattern: "watchtower-logarchive-{region}-766997230140"
    regions: ["us-east-1", "us-east-2"]
    lifecycle_expiration_days: 30      # toss ALL objects at 30 days
    sse: "S3"                          # AES256 (SSE-KMS is an open item)
    org_id: "o-x82cglkqhs"
    reader:
      role_name: "watchtower-cribl-reader"
      cribl_user_arn: "arn:aws:iam::698777852125:user/cribl-servicaccount"
      external_id: "watchtower-2675e497-b719-409b-b81a-57e9ca59976e"
      queue_visibility_seconds: 300
      queue_retention_seconds: 1209600 # 14 days
  writer:
    role_name_pattern: "watchtower-writer-{account}"

accounts:
  - name: logarchive
    account: "${LOGARCHIVE_ACCOUNT_ID}"   # or read from cdk.json context
    region: "us-east-1"
  - name: sandbox
    account: "${SANDBOX_ACCOUNT_ID}"
    region: "us-east-1"
  - name: development
    account: "${DEVELOPMENT_ACCOUNT_ID}"
    region: "us-east-1"
  - name: production
    account: "${PRODUCTION_ACCOUNT_ID}"
    region: "us-east-1"
```

> `${ENV}` tokens still resolve (super-fiesta keeps the `string.Template`
> substitution), but since the IDs are also in `cdk.json` context, the loader
> can read them from context first and fall back to env — your call at
> implementation. The point is **the config lives in YAML + `cdk.json`, not a
> mandatory `.env`**.

**Makefile targets (cluster-cauldron guardrails, super-fiesta config):**
- `whoami` — `aws sts get-caller-identity` before any mutating op.
- `synth` — pure local.
- `diff` — gated on `whoami` (steering: `cdk diff` before deploy).
- `bootstrap` — bootstraps `watchtwr26` across accounts × both regions (§2),
  gated on `whoami`.
- `deploy` — gated on `whoami` + `diff`, `--require-approval any-change`.
- Account selection: `make deploy ACCOUNT=logarchive`; `AWS_PROFILE :=
  admin-$(ACCOUNT)`. Stack names are pinned via `stack_name=` (§3) for readable,
  predictable CFN names.
- No prod direct-deploy without the staging gate (steering / design doc §8:
  prod goes through the gitops PR flow).

---

## 8. Deployment: `make deploy` only (lab) — pipelines deferred to work

**Lab decision: NO pipeline. `make deploy` is the only deploy path.** There is
no CodePipeline, no CDK `Stage`, no `stages/` or `pipeline/` code in this repo.

### Why no pipeline in the lab (cost)

A self-mutating CDK Pipeline is **not free at rest**, which cuts against the
"minimal recurring spend" goal:

- **CodePipeline V2** (your `cdk.json` sets `defaultPipelineTypeToV2: true`)
  bills per action-execution-minute with a small standing floor — roughly
  **~$1/mo** for a lightly-used pipeline.
- **Cross-account CDK Pipelines require `cross_account_keys=True`**, which
  provisions a **customer-managed KMS key (~$1/mo)** for the artifact bucket —
  the exact KMS cost being deferred elsewhere, sneaking back in.
- Plus the artifact S3 bucket + CodeBuild minutes per run.

Net **~$2-3/mo standing even when idle** — small, but *recurring*, and
avoidable. Everything else in this design is $0-at-rest (S3 empty, SNS/SQS
free-tier, IAM free, bootstrap buckets/ECR empty), so the pipeline would be the
single largest standing line item. Not worth it for a home lab.

### The manual-vs-pipeline confusion — resolved

The concern was: does a stack's identity change depending on whether it's
deployed manually vs via a pipeline? Here's the precise answer, so it's not
confusing later.

CDK derives **two** independent things from a stack's position in the tree:

1. **Stack name** — the CloudFormation deployment name. Fixed by passing
   `stack_name=` (we do). *Not* affected by manual-vs-pipeline once pinned.
2. **Logical IDs** — the ids of every *resource inside* the stack, derived from
   the **construct path**. A stack under `App` has path `Stack/Bucket`; the same
   stack wrapped in a `Stage` has path `Stage/Stack/Bucket`. **Different path →
   different logical IDs**, even with the same `stack_name`.

The trap is #2, not #1. If you deploy a stack **manually** and later deploy the
**same-named** stack **through a Stage-wrapped pipeline**, CloudFormation sees
the *same stack* but *different logical IDs* for its resources, and tries to
**replace** them — for a RETAIN bucket that means orphaning the old bucket and
colliding on the fixed bucket name. That is the real hazard, and it is bigger
than the stack-name question.

**The clean resolution (and why the lab is trivially safe):** never deploy the
same stack via two different paths. Pick **one owner per stack**. In the lab
that owner is `make deploy`, full stop — so #2 can never trigger, because there
is only one construct path these stacks are ever synthesized under.

### When you go to work — recommended pipeline approach

Spend isn't the constraint there; correctness and repeatability are. Do this:

1. **Make the pipeline the *sole* deployer of these stacks.** Stop hand-running
   `make deploy` against pipeline-owned stacks. One owner = the logical-ID
   problem never exists. `make deploy` stays available for bootstrap/dev
   scratch only, not for the managed stacks.

2. **Wrap stacks in a `cdk.Stage` and accept the Stage-prefixed construct path
   as the canonical one — from day one.** Do NOT deploy the stacks bare under
   `App` first and *then* move them into a Stage later: that migration is
   exactly the logical-ID-churn event. If the pipeline is the owner from the
   first deploy, the Stage path is simply *the* path and there's nothing to
   reconcile.

3. **Keep `stack_name=` pinned** (readable names, and it decouples the CFN name
   from the Stage prefix). Names stay `watchtower-logarchive-ue1` etc.

4. **Use CDK Pipelines (`pipelines.CodePipeline`)** in the deployment account:
   - Source via CodeConnections (add `configs/constants.py` with the connection
     ARN + repo — the super-fiesta pattern).
   - One **wave per region**, a **stage per target account** within each wave.
   - `cross_account_keys=True` (the KMS key you now accept), synth step passes
     account ids as env, grants `sts:AssumeRole` on `cdk-watchtwr26-*` in each
     target (already trusted from §2).
   - **Manual approval gate before production** (steering: dev → staging → prod).
   - Self-mutation on, behind that approval.

5. **If you ever must migrate an already-manually-deployed stack into the
   pipeline** (the thing to avoid, but sometimes unavoidable): either
   `cdk import` / resource-import the retained resources into the new
   logical-id shape, or override logical ids (`resource.overrideLogicalId(...)`)
   to match the pre-Stage ids so CloudFormation sees no change. Both are
   fiddly — which is why "pipeline owns it from the first deploy" is the
   recommendation.

**One-line summary:** the manual-vs-pipeline hazard is about *logical IDs from
the construct path*, not stack names; you avoid it entirely by giving each
stack a single deploy owner. Lab → `make deploy` owns everything. Work →
the pipeline owns everything, from the first deploy.

---

## 9. Decisions made vs open items

**Decided (baked into the plan above):**

- ✅ **Bucket names** — `watchtower-logarchive-<region>-766997230140` (§1),
  account-suffixed so global-uniqueness is a non-issue.
- ✅ **Lifecycle/retention** — expire ALL objects at **30 days**, no
  storage-class transitions (§4).
- ✅ **Versioning** — OFF (§4).
- ✅ **Cribl reader identity** — reuse the audit `cribl-servicaccount` user +
  new `watchtower-cribl-reader` role + new ExternalId
  `watchtower-2675e497-b719-409b-b81a-57e9ca59976e` (§6).
- ✅ **Bootstrap qualifier** — fresh `watchtwr26`, all accounts × both regions
  (§2).
- ✅ **Stack names** — explicit `stack_name=` on every stack (readable CFN
  names); single deploy path (`make deploy`) so the logical-id hazard can't
  arise (§3, §8).
- ✅ **Config style** — super-fiesta (`cdk.json` context + `infrastructure.yaml`,
  not `.env`-first) (§7).
- ✅ **No pipeline in the lab** — `make deploy` only; pipeline deferred to the
  work build to avoid ~$2-3/mo standing (CodePipeline V2 + cross-account KMS
  key) (§8).
- ✅ **Cost hygiene** — SSE-S3 (not KMS), 30-day expiry, 7-day multipart-abort,
  no NAT/VPC endpoints, empty bootstrap buckets/ECR → ~$0 at rest (§4, §10).

**Still open (do NOT block scaffolding):**

1. **`--cloudformation-execution-policies`** for the `watchtwr26` bootstrap —
   defaults to `AdministratorAccess` (CDK default, matches `govjuly25`). Decide
   whether to tighten; keep uniform across accounts.
2. **SNS→SQS `raw_message_delivery`** — confirm against Cribl's S3 source
   notification-envelope expectation before flipping it on.
3. **Loki reader** — fan-out topology + queue name reserved; build deferred.

---

## 10. Lab vs work — the "enterprise-ready" seam

This build is tuned for **minimal spend / minimal recurring spend** (home lab).
When it moves to work, the following knobs flip. They are deliberately isolated
so the change is mechanical, not a redesign:

| Concern            | Lab (now)                                   | Work (later)                                                        |
| ------------------ | ------------------------------------------- | ------------------------------------------------------------------- |
| Bucket encryption  | **SSE-S3 (AES256)** — free                  | **SSE-KMS (CMK)** — add `kms:GenerateDataKey` to writer policy + key policy for the reader role |
| Object deletion    | bucket-side `Deny s3:DeleteObject*` + no delete in writer policy | same, plus possibly **Object Lock / compliance retention** |
| Bucket removal     | `RETAIN` (container) + 30-day object expiry | `RETAIN` + longer retention + lifecycle transitions (IA/Glacier)    |
| Retention          | **30 days**, hard expiry, no transitions    | tiered: e.g. IA at 30d → Glacier at 90d → expire at N — per policy  |
| Deploy path        | **`make deploy` only** (one owner)          | **CDK Pipeline owns everything** from first deploy (one owner)      |
| Pipeline cost      | avoided (~$2-3/mo standing)                  | accepted (CodePipeline V2 + cross-account KMS key)                  |
| CFN exec policy    | `AdministratorAccess` (bootstrap default)   | scoped, least-privilege execution policy                            |
| VPC access to S3   | public S3 endpoint (no endpoint cost)       | S3 gateway endpoint (free) and/or interface endpoints if required   |
| Regions active     | us-east-1 primary; us-east-2 bootstrapped but idle | both active as needed                                        |

The **code shape does not change** across this seam — stacks are plain `Stack`
subclasses, config is data (`infrastructure.yaml`). What changes is: config
values (SSE, retention), one `RemovalPolicy`/`encryption` argument, and *who
deploys* (Makefile → pipeline). See §8 for the critical rule when introducing
the pipeline: **make the pipeline the sole owner from the first deploy** so the
construct-path/logical-id churn never happens.

