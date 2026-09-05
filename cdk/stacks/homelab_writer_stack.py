"""Home-lab writer stack — a single IAM USER in the logarchive account.

The k3s home cluster (`cauldron`) has no AWS account behind it, so there is no
EKS Pod Identity and no cross-account role to assume. Its Fluent Bit workloads
authenticate with a long-lived access key belonging to this same-account IAM
user; the user's identity policy alone grants write-only access into a FIXED
literal prefix in the logarchive bucket (there is no ``aws:PrincipalAccount``
to key the prefix on, unlike the EKS ``WorkloadWriterStack``).

Contrast with WorkloadWriterStack (EKS-in-AWS):
  * that side: cross-account ROLE, trust policy, account-id prefix.
  * this side: same-account USER, no role, literal ``homelab/cauldron`` prefix.

The access key is deliberately NOT created here — a key materialized by
CloudFormation would leak the secret into the template, state, and stack
outputs. Mint it once, post-deploy, out of band:

    aws iam create-access-key --user-name watchtower-writer-home \\
      --profile admin-logarchive

Write-only: NO delete anywhere (the bucket policy's explicit delete-deny is the
backstop). See docs/plan.md §5 and docs/iam-s3-design.md §6 (home-lab path).
"""

from aws_cdk import (
    CfnOutput,
    Stack,
    aws_iam as iam,
)
from constructs import Construct

from configs.models import HomelabWriterConfig, InfrastructureSpec


class HomelabWriterStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        spec: InfrastructureSpec,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if spec.homelab is None:
            raise ValueError("HomelabWriterStack requires spec.homelab")

        self.spec = spec
        cfg: HomelabWriterConfig = spec.homelab
        self.cfg = cfg

        # ------------------------------------------------------------------
        # The IAM user. No console access, no key here — programmatic only,
        # key minted post-deploy.
        # ------------------------------------------------------------------
        user = iam.User(self, "HomelabWriterUser", user_name=cfg.user_name)
        self.user = user

        # ------------------------------------------------------------------
        # Identity policy — write-only into the FIXED literal prefix, in each
        # target bucket. Bucket names are deterministic; the account id is the
        # logarchive account (bucket owner == user owner, same account).
        # ------------------------------------------------------------------
        object_resources: list[str] = []
        bucket_resources: list[str] = []
        for region in cfg.target_regions:
            bucket = cfg.bucket_name_pattern.format(
                region=region, account_id=cfg.logarchive_account_id
            )
            bucket_arn = f"arn:{self.partition}:s3:::{bucket}"
            object_resources.append(f"{bucket_arn}/{cfg.prefix}/*")
            bucket_resources.append(bucket_arn)

        user.add_to_policy(
            iam.PolicyStatement(
                sid="WriteHomelabPrefixOnly",
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3:PutObject",
                    "s3:AbortMultipartUpload",
                ],
                resources=object_resources,
            )
        )
        user.add_to_policy(
            iam.PolicyStatement(
                sid="GetBucketLocation",
                effect=iam.Effect.ALLOW,
                actions=["s3:GetBucketLocation"],
                resources=bucket_resources,
            )
        )

        CfnOutput(self, "HomelabWriterUserName", value=user.user_name)
        CfnOutput(self, "HomelabWriterUserArn", value=user.user_arn)
        CfnOutput(
            self,
            "HomelabWritePrefix",
            value=cfg.prefix,
            description="S3 key prefix this user may write under",
        )
        CfnOutput(
            self,
            "CreateAccessKeyHint",
            value=(
                f"aws iam create-access-key --user-name {cfg.user_name} "
                f"--profile admin-logarchive"
            ),
            description="Run once post-deploy to mint the access key",
        )
