#!/usr/bin/env python3
"""CDK app entrypoint for cloud-watchtower (log archive S3 + IAM).

Account-driven: ONE account/side is selected via the ``account`` context flag
(``-c account=<name>``) or the ``WATCHTOWER_ACCOUNT`` env var. Super-fiesta
config style (account IDs + org id live in cdk.json context, hydrated through
configs/config.py).

  account=logarchive
      -> two per-region LogArchiveStacks (buckets, SNS, SQS, and — in the
         primary region only — the global Cribl reader role):
           watchtower-logarchive-ue1  (primary, us-east-1)
           watchtower-logarchive-ue2  (us-east-2)

  account=sandbox|development|production
      -> one WorkloadWriterStack (shared writer role):
           watchtower-writer-<account>

  account=homelab
      -> one HomelabWriterStack (a single IAM user in the logarchive account;
         no role, no cross-account trust — the k3s home cluster authenticates
         with a long-lived key minted post-deploy):
           watchtower-writer-homelab

Deploy path is ``make deploy`` ONLY — no pipeline, no CDK Stage (home lab; see
docs/plan.md §8). Every stack pins an explicit ``stack_name`` so the
CloudFormation names are fixed and readable.
"""

import os

import aws_cdk as cdk

from configs.config import AppConfigs
from stacks.homelab_writer_stack import HomelabWriterStack
from stacks.log_archive_stack import LogArchiveStack
from stacks.workload_writer_stack import WorkloadWriterStack

# The primary region owns the global (IAM) reader role.
PRIMARY_REGION = "us-east-1"

app = cdk.App()

# Account resolution: CDK context flag wins, else env var, else logarchive.
account_name = app.node.try_get_context("account") or os.getenv(
    "WATCHTOWER_ACCOUNT", "logarchive"
)

spec = AppConfigs().get_infrastructure_info(account_name)


def _short(region: str) -> str:
    if spec.log_archive is not None:
        return spec.log_archive.region_short_codes.get(region, region)
    return region


if account_name == "logarchive":
    # One stack per region, each pinned to its own account+region environment.
    for region in spec.log_archive.regions:
        short = _short(region)
        LogArchiveStack(
            app,
            f"LogArchive{short.upper()}",  # construct id (internal)
            stack_name=f"watchtower-logarchive-{short}",  # pinned CFN name
            spec=spec,
            region=region,
            is_primary=(region == PRIMARY_REGION),
            env=cdk.Environment(account=spec.account, region=region),
        )
elif account_name == "homelab":
    # Home-lab (k3s) writer: a single same-account IAM user in the logarchive
    # account. No role, no cross-account trust — see stack docstring.
    HomelabWriterStack(
        app,
        "HomelabWriter",  # construct id (internal)
        stack_name="watchtower-writer-homelab",  # pinned CFN name
        spec=spec,
        env=cdk.Environment(account=spec.account, region=spec.region),
    )
else:
    # Workload account: the shared writer role is global (IAM), so a single
    # stack in the account's home region is enough.
    WorkloadWriterStack(
        app,
        f"WorkloadWriter{account_name.title()}",  # construct id (internal)
        stack_name=f"watchtower-writer-{account_name}",  # pinned CFN name
        spec=spec,
        env=cdk.Environment(account=spec.account, region=spec.region),
    )

# Cost visibility + easy cleanup filtering.
cdk.Tags.of(app).add("project", "cloud-watchtower")
cdk.Tags.of(app).add("component", "log-archive")
cdk.Tags.of(app).add("watchtower-account", account_name)

app.synth()
