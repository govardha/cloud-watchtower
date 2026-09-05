"""Workload writer stack — one instance PER WORKLOAD ACCOUNT.

Creates the single shared IAM role every compute type in the account uses to
write logs into the logarchive buckets:

  * EKS pods   (via Pod Identity)   -> pods.eks.amazonaws.com
  * EC2        (via instance profile) -> ec2.amazonaws.com
  * ECS tasks  (via task role)      -> ecs-tasks.amazonaws.com
  * Lambda     (via execution role) -> lambda.amazonaws.com

The identity policy grants write-only access (NO delete anywhere; the bucket
side has an explicit delete deny as backstop) into this account's own prefix
in BOTH regional buckets. Account-scoped, not per-app / per-cluster — matches
design doc §6 and docs/plan.md §5.

Pod Identity ASSOCIATIONS (binding cluster/namespace/SA -> this role) are a
cluster-cauldron concern, NOT owned here (design doc §7). EC2/ECS/Lambda attach
this role directly.
"""

from aws_cdk import (
    CfnOutput,
    Stack,
    aws_iam as iam,
)
from constructs import Construct

from configs.models import InfrastructureSpec, WriterConfig


class WorkloadWriterStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        spec: InfrastructureSpec,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if spec.writer is None:
            raise ValueError("WorkloadWriterStack requires spec.writer")

        self.spec = spec
        cfg: WriterConfig = spec.writer
        self.cfg = cfg

        account_id = spec.account
        role_name = cfg.role_name_pattern.format(account=spec.profile_name)

        # ------------------------------------------------------------------
        # Trust policy — every compute type that might write logs.
        # ------------------------------------------------------------------
        role = iam.Role(
            self,
            "WriterRole",
            role_name=role_name,
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            description=(
                "Shared log-archive writer role for this account (EKS Pod "
                "Identity / EC2 / ECS / Lambda). Write-only, no delete."
            ),
        )
        # EKS Pod Identity needs both AssumeRole + TagSession.
        role.assume_role_policy.add_statements(
            iam.PolicyStatement(
                sid="eks-pod-identity",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("pods.eks.amazonaws.com")],
                actions=["sts:AssumeRole", "sts:TagSession"],
            ),
            iam.PolicyStatement(
                sid="ecs-task-role",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("ecs-tasks.amazonaws.com")],
                actions=["sts:AssumeRole"],
            ),
            iam.PolicyStatement(
                sid="lambda-execution-role",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("lambda.amazonaws.com")],
                actions=["sts:AssumeRole"],
            ),
        )
        self.role = role

        # ------------------------------------------------------------------
        # Identity policy — write-only into this account's own prefix in BOTH
        # regional buckets. Bucket names are deterministic.
        # ------------------------------------------------------------------
        object_resources: list[str] = []
        bucket_resources: list[str] = []
        for region in cfg.target_regions:
            bucket = cfg.bucket_name_pattern.format(
                region=region, account_id=cfg.logarchive_account_id
            )
            bucket_arn = f"arn:{self.partition}:s3:::{bucket}"
            # own-account prefix only, mirroring the bucket-policy boundary.
            object_resources.append(f"{bucket_arn}/{account_id}/*")
            bucket_resources.append(bucket_arn)

        role.add_to_policy(
            iam.PolicyStatement(
                sid="WriteOwnAccountPrefix",
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3:PutObject",
                    "s3:AbortMultipartUpload",
                ],
                resources=object_resources,
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="GetBucketLocation",
                effect=iam.Effect.ALLOW,
                actions=["s3:GetBucketLocation"],
                resources=bucket_resources,
            )
        )

        CfnOutput(self, "WriterRoleName", value=role.role_name)
        CfnOutput(self, "WriterRoleArn", value=role.role_arn)
