# Work Deployment Runbook: Replicating cloud-watchtower

Status: written for a **from-scratch redeploy in a different AWS
Organization** (work), copied out of this home-lab repo. Not itself
deployed anywhere — a checklist for the new repo.

Scope difference from the home lab: this deploys **both `us-east-1` and
`us-east-2` for logarchive from day one** (the home lab only ever
bootstrapped `us-east-1` and narrowed with `DEPLOY_REGIONS`). Everything
else below is the same shape, new account IDs.

## 0. Decide before you copy anything

These are judgment calls specific to the new org — get them settled first,
because they touch account IDs and naming baked into `cdk.json` and
`configs/infrastructure.yaml`.

1. **Do you have (or want) a dedicated CDK deployer account?** This repo's
   pattern is one `deployment` account trusted cross-account into every
   target (`--trust <deployment_account_id>` at bootstrap). If work doesn't
   already separate "who deploys" from "what gets deployed," decide now
   whether to introduce that account or just deploy each account from its
   own `admin-<account>` credentials (drop the deployer account and the
   `--trust` argument in `make bootstrap`).
2. **Do you need the `homelab` stack at all?** `HomelabWriterStack`
   (`account=homelab`) exists solely because the home k3s cluster
   (`cauldron`) has no AWS account behind it, so it authenticates as a
   same-account IAM **user** instead of a cross-account role. If every
   writer at work is EKS/EC2/ECS/Lambda inside a real AWS account, **skip
   this stack entirely** — don't just leave it unused, remove
   `homelab_writer_stack.py`, the `homelab:` block in
   `infrastructure.yaml`, and the `ACCOUNT=homelab` special-case in the
   Makefile, or a future reader will assume it's meaningful.
3. **Bootstrap qualifier.** Home lab uses `watchtwr26`, isolated from the
   org's other (`govjuly25`) qualifier. Pick a new one for work — it's a
   different AWS Organization so collision isn't the concern, but a
   qualifier should still say what it's for. Update
   `@aws-cdk/core:bootstrapQualifier` in `cdk.json` and `QUALIFIER` in the
   Makefile together; a mismatch fails at deploy with "unable to resolve
   bootstrap role."
4. **Naming.** `watchtower-*` (bucket pattern, role/queue/topic names) is
   fine to keep verbatim, or rename to match work's conventions — just do
   it once, consistently, across `infrastructure.yaml`'s `bucket_name_pattern`
   / `role_name_pattern`, before the first deploy. Renaming after buckets
   exist means a second bucket, not a rename (`RemovalPolicy.RETAIN` won't
   let CDK replace it silently, but don't count on that as your safety net —
   confirm with `cdk diff` either way).

## 1. The Cribl IAM user — not managed by this repo, do this first

**This is the one piece the CDK never creates, and the reader role is
useless without it existing first.** In the home lab, `cribl-servicaccount`
already existed in the **audit** account (`698777852125`) from an
already-running CloudTrail → Cribl pipeline, and this repo's reader role
just points at it — see `README.md` §4.4 and `docs/iam-s3-design.md` §5.

At work, before you touch `cdk bootstrap`:

1. Find out if an equivalent Cribl service-account IAM user already exists
   in whatever account holds your audit/security-tooling identities. If
   Cribl already reads CloudTrail or anything else from this org, it almost
   certainly does.
2. If it exists: get its ARN
   (`arn:aws:iam::<audit_account_id>:user/<name>`). Reuse it — don't create
   a second Cribl identity for no reason.
3. If it doesn't exist: create it out of band (console or one-off CLI, not
   CDK — this repo's own rule is **never mint an IAM access key in CDK**,
   and the same logic applies to creating the user):
   - Zero attached permissions. Its only job is `sts:AssumeRole`.
   - One access key, handed to whoever administers Cribl, never committed
     anywhere.
4. Either way, you need **its ARN** and **a fresh ExternalId you invent**
   (a UUID is fine, doesn't need to match the home lab's) before deploying
   `LogArchiveStack` — both go into `infrastructure.yaml`'s `reader:` block
   (`cribl_user_arn`, `external_id`). Deliberately don't reuse the home
   lab's ExternalId even if you reuse the same Cribl user — keep the two
   pipelines independently revocable (this repo's own reasoning, see
   README §4.4).

## 2. Copy the repo, then edit these files

1. Copy the repo, drop `.venv`, `cdk.out`, and anything under `.env` if it
   exists (home-lab-local values only — check `.env.example` for the
   shape, don't copy real secrets across).
2. **`cdk/cdk.json` → `context`**: replace every account ID with the work
   equivalents (`deployment_account_id`, `logarchive_account_id`,
   `audit_account_id`, and one per workload account), replace `org_id` with
   work's Organization ID (`aws organizations describe-organization`), and
   set the new bootstrap qualifier from step 0.3.
3. **`cdk/configs/infrastructure.yaml`**:
   - `globals.log_archive.org_id` → work's org ID (also referenced from
     `cdk.json`, but the CDK reads it from here — keep them in sync
     manually, this repo has no single source of truth for that
     duplication).
   - `globals.log_archive.regions` — already lists both `us-east-1` and
     `us-east-2`; nothing to change here, just don't narrow with
     `DEPLOY_REGIONS` at deploy time the way the home lab does.
   - `globals.log_archive.admin_delete_principal_arn_pattern` — **do not
     copy the home lab's value verbatim.** It's scoped to
     `arn:aws:iam::766997230140:...` (the home lab's logarchive account).
     Get the real pattern for work's logarchive-equivalent account:
     ```bash
     aws iam list-roles --path-prefix /aws-reserved/sso.amazonaws.com/ \
       --profile <work-admin-profile-for-that-account>
     ```
     Take the `AdministratorAccess` (or whatever your break-glass
     permission set is called) role's ARN, replace the account ID, and
     wildcard everything after `AdministratorAccess_` (the provisioning
     suffix is per-account and can rotate if the permission set is ever
     reprovisioned) — e.g.
     `arn:aws:iam::<work_logarchive_account_id>:role/aws-reserved/sso.amazonaws.com/AWSReservedSSO_AdministratorAccess_*`.
     Remember: `aws:PrincipalArn` for an assumed role is the **IAM role
     ARN**, never the STS assumed-role session ARN — don't try to scope
     this to one login/session, it can't be done with this condition key.
     See `docs/iam-s3-design.md` §5 for the full explanation (this exact
     question came up and was verified against AWS docs and a live role —
     worth reading before you touch this line).
   - `globals.log_archive.reader.cribl_user_arn` / `external_id` — from
     step 1.
   - `accounts:` — replace `sandbox` / `development` / `production` (or
     drop/rename to match work's actual workload account list) and
     `homelab` per your step-0.2 decision.
4. Update every doc that hardcodes the home lab's account IDs/org
   ID/qualifier if you're keeping docs in the new repo at all
   (`README.md`, `cdk/CLAUDE.md`, `cdk/.kiro/steering/*.md`,
   `docs/iam-s3-design.md`, `docs/plan.md`) — or just delete the ones that
   are pure home-lab narrative (§1 Vision, §3 on-prem pipeline in README)
   and keep the ones that are still-accurate design reference (§4 AWS
   build-out, §5 CDK mechanics, IAM design doc).

## 3. Bootstrap

Same mechanics as `cdk/Makefile`'s `bootstrap` / `bootstrap-deployment`
targets, run for **every** account, **both** regions, this time (no
narrowing):

```bash
cd cdk && make venv

# the deployer account itself — no --trust, it IS the deployer
make bootstrap-deployment                      # needs admin-deployment profile

# every target account, both us-east-1 and us-east-2
make bootstrap ACCOUNT=logarchive
make bootstrap ACCOUNT=sandbox                 # or whatever you renamed these to
make bootstrap ACCOUNT=development
make bootstrap ACCOUNT=production
```

If you dropped the separate deployer-account pattern (step 0.1), drop
`--trust $(DEPLOY_ACCT)` from the Makefile's `bootstrap` target and run
`cdk bootstrap` per account under its own admin profile instead.

Confirm each account/region actually has the toolkit stack before moving
on — `aws cloudformation describe-stacks --stack-name CDKToolkit-<qualifier>
--profile admin-<account> --region <region>` in every combination. This is
exactly the kind of thing that's easy to half-do (bootstrap one region,
forget the other) and only surfaces later as a cryptic deploy failure.

## 4. Deploy order

Logarchive first — it owns the bucket the reader role and every writer
role depend on, plus the reader role itself needs to exist before Cribl
can be wired up:

```bash
make synth  ACCOUNT=logarchive     # local, safe — check it before touching AWS
make diff   ACCOUNT=logarchive     # review the changeset
make deploy ACCOUNT=logarchive     # deploys BOTH us-east-1 and us-east-2 stacks
```

Then each workload account:

```bash
make deploy ACCOUNT=sandbox
make deploy ACCOUNT=development
make deploy ACCOUNT=production
```

Then `homelab`, only if you kept it (step 0.2):

```bash
make deploy ACCOUNT=homelab
aws iam create-access-key --user-name watchtower-writer-home --profile admin-logarchive
```

Every `deploy` gates on `whoami` (`aws sts get-caller-identity`) and `cdk
diff` first — don't bypass that by calling `cdk deploy` directly outside
the Makefile.

## 5. Wire up Cribl (per region)

Once `logarchive` is deployed, gather these CDK outputs for **each**
region (`us-east-1` and `us-east-2` are two independent buckets/queues —
no cross-region replication, so this is two separate S3 Sources in Cribl,
not one):

- S3 bucket name/ARN
- SQS queue ARN/URL (`watchtower-cribl-reader-<region>`)
- Region
- `watchtower-cribl-reader` role ARN (created once, in the primary
  region's stack — same role ARN is used for both regions' S3 Sources)
- The ExternalId you chose in step 1

Configure each as an S3 Source in Cribl per `docs/cribl-s3.md` — the
**Queue**, **Region**, and **AssumeRole → External ID** fields under
"Configure Cribl Stream to Receive Data from Amazon S3." Turn on
`raw_message_delivery` on the SNS→SQS subscription (already set in CDK)
since Cribl expects the S3 event shape directly, not SNS-wrapped.

## 6. Verify before calling it done

```bash
make synth ACCOUNT=logarchive
make synth ACCOUNT=development     # or any workload account
make diff  ACCOUNT=logarchive      # should show ~no diff right after deploy
```

Then a live IAM spot-check mirroring `docs/iam-trust-verified.md`'s
pattern — `aws iam get-role` / `get-role-policy` on the reader role and one
writer role, profile-scoped, confirming trust + permission policies match
the CDK source exactly. Finally, an end-to-end test: write a test object
into a workload account's prefix, confirm the S3 → SNS → SQS notification
lands, and confirm Cribl picks it up.

## 7. Things worth reconsidering now that it's not a home lab

The home lab deliberately deferred these for cost/scope reasons (see
README §8). None are required to match "the same thing," but a work
deployment is exactly the context this repo's own roadmap flagged them
for:

- **CDK Pipeline** — `make deploy` from a laptop is fine for one person; a
  team probably wants a `cdk.Stage`-wrapped pipeline. If you add one, it
  must own each stack **from its first deploy** — never migrate an
  already-manually-deployed stack into a `Stage` later (see README §5.3;
  CDK derives logical IDs from construct path, and a path change on a
  `RemovalPolicy.RETAIN` bucket is a real hazard).
- **`--cloudformation-execution-policies`** — currently the CDK default
  (`AdministratorAccess`) on every bootstrapped account. Worth tightening
  to least-privilege for a work environment.
- **SSE-KMS** instead of SSE-S3 — needs a CMK plus a key policy granting
  the reader role `kms:Decrypt` and writer roles `kms:GenerateDataKey`.
- **`admin_delete_principal_arn_pattern`** — re-confirm who work's
  equivalent break-glass identity actually is before deploying; don't
  default to "whoever I am today" without checking whether a team's admin
  access is broader/narrower than a home lab's solo SSO account.
