"""Log archive stack — one instance PER REGION (logarchive account).

Owns, for a single region:
  * the regional bucket ``watchtower-logarchive-<region>-<account_id>``
    (SSE-S3, block-public, force-HTTPS, 30-day expiry, 7-day multipart abort),
  * a bucket policy: org-wide write-into-own-prefix + explicit delete deny,
  * an SNS topic + an SQS queue (+ DLQ) with the S3 -> SNS -> SQS fan-out,
  * and (PRIMARY region only) the global ``watchtower-cribl-reader`` IAM role
    the audit-account Cribl user assumes (ExternalId-gated).

Mirrors the proven CloudTrail -> Cribl pattern already running in this account
(SNS ``cloudtrail-notifications`` -> SQS ``cribl-servicaccountQueue`` ->
``IAMCriblLogProcessingRole``). Port, don't redesign — see docs/iam-s3-design.md
§5 and docs/plan.md §0/§4.

IAM is global, so the reader role is created exactly once (in the PRIMARY
region's stack) and the non-primary region's stack merely grants that same
role access to its queue via the role's account-wide SQS wildcard — no
cross-region CDK reference needed (the role ARN is deterministic).
"""

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_iam as iam,
    aws_s3 as s3,
    aws_s3_notifications as s3n,
    aws_sns as sns,
    aws_sns_subscriptions as sns_subs,
    aws_sqs as sqs,
)
from constructs import Construct

from configs.models import InfrastructureSpec, LogArchiveConfig

_SSE = {
    "S3": s3.BucketEncryption.S3_MANAGED,
    "KMS": s3.BucketEncryption.KMS_MANAGED,  # work-time upgrade (plan §10)
}


class LogArchiveStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        spec: InfrastructureSpec,
        region: str,
        is_primary: bool,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if spec.log_archive is None:
            raise ValueError("LogArchiveStack requires spec.log_archive")

        self.spec = spec
        cfg: LogArchiveConfig = spec.log_archive
        self.cfg = cfg
        self.region_code = region
        self.is_primary = is_primary

        account_id = spec.account
        short = cfg.region_short_codes.get(region, region)
        bucket_name = cfg.bucket_name_pattern.format(
            region=region, account_id=account_id
        )

        # ------------------------------------------------------------------
        # Bucket
        # ------------------------------------------------------------------
        bucket = s3.Bucket(
            self,
            "LogArchiveBucket",
            bucket_name=bucket_name,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,  # adds the SecureTransport deny
            encryption=_SSE.get(cfg.sse, s3.BucketEncryption.S3_MANAGED),
            versioned=cfg.versioning,
            # Stateful: keep the container on stack delete (steering: RETAIN).
            removal_policy=RemovalPolicy.RETAIN,
            auto_delete_objects=False,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="expire-all-objects",
                    enabled=True,
                    expiration=Duration.days(cfg.lifecycle_expiration_days),
                    abort_incomplete_multipart_upload_after=Duration.days(
                        cfg.abort_incomplete_multipart_days
                    ),
                )
            ],
        )
        self.bucket = bucket

        # ------------------------------------------------------------------
        # Bucket policy — org write-into-own-prefix + explicit delete deny.
        # (design doc §5, with the real org id from config)
        # ------------------------------------------------------------------
        bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="org-write-own-account-prefix-only",
                effect=iam.Effect.ALLOW,
                principals=[iam.AnyPrincipal()],
                actions=["s3:PutObject"],
                resources=[f"{bucket.bucket_arn}/${{aws:PrincipalAccount}}/*"],
                conditions={
                    "StringEquals": {"aws:PrincipalOrgID": cfg.org_id}
                },
            )
        )
        bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="deny-delete-everyone",
                effect=iam.Effect.DENY,
                principals=[iam.AnyPrincipal()],
                actions=["s3:DeleteObject", "s3:DeleteObjectVersion"],
                resources=[f"{bucket.bucket_arn}/*"],
            )
        )

        # ------------------------------------------------------------------
        # SNS topic + SQS queue (+ DLQ), S3 -> SNS -> SQS fan-out.
        # ------------------------------------------------------------------
        topic = sns.Topic(
            self,
            "NotificationsTopic",
            topic_name=f"watchtower-logarchive-notifications-{short}",
        )
        self.topic = topic

        dlq = sqs.Queue(
            self,
            "CriblReaderDlq",
            queue_name=f"watchtower-cribl-reader-dlq-{short}",
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
            retention_period=Duration.seconds(cfg.reader.queue_retention_seconds),
        )
        queue = sqs.Queue(
            self,
            "CriblReaderQueue",
            queue_name=f"watchtower-cribl-reader-{short}",
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
            visibility_timeout=Duration.seconds(
                cfg.reader.queue_visibility_seconds
            ),
            retention_period=Duration.seconds(
                cfg.reader.queue_retention_seconds
            ),
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=cfg.reader.dlq_max_receive_count,
                queue=dlq,
            ),
        )
        self.queue = queue

        # S3 -> SNS notification (ObjectCreated), and SNS -> SQS subscription.
        bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.SnsDestination(topic),
        )
        topic.add_subscription(
            sns_subs.SqsSubscription(
                queue,
                raw_message_delivery=cfg.reader.raw_message_delivery,
            )
        )

        # ------------------------------------------------------------------
        # Cribl reader role — GLOBAL, created only in the PRIMARY region stack.
        # The audit-account Cribl user assumes it (ExternalId-gated). Its SQS
        # permission is an account-wide wildcard (watchtower-cribl-*), so it
        # already covers this region's queue and the other region's queue.
        # ------------------------------------------------------------------
        if is_primary:
            reader_role = iam.Role(
                self,
                "CriblReaderRole",
                role_name=cfg.reader.role_name,
                assumed_by=iam.ArnPrincipal(cfg.reader.cribl_user_arn),
                description=(
                    "Assumed by the audit-account Cribl service account to "
                    "read watchtower log-archive objects + SQS notifications."
                ),
            )
            # Tighten the trust with the ExternalId condition.
            reader_role.assume_role_policy.add_statements(
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    principals=[iam.ArnPrincipal(cfg.reader.cribl_user_arn)],
                    actions=["sts:AssumeRole"],
                    conditions={
                        "StringEquals": {
                            "sts:ExternalId": cfg.reader.external_id
                        }
                    },
                )
            )
            # S3 read on BOTH regional buckets (names are deterministic).
            bucket_arns: list[str] = []
            for r in cfg.regions:
                arn = f"arn:{self.partition}:s3:::" + cfg.bucket_name_pattern.format(
                    region=r, account_id=account_id
                )
                bucket_arns.append(arn)
                bucket_arns.append(f"{arn}/*")
            reader_role.add_to_policy(
                iam.PolicyStatement(
                    sid="ReadWatchtowerBuckets",
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "s3:GetObject",
                        "s3:GetObjectTagging",
                        "s3:ListBucket",
                    ],
                    resources=bucket_arns,
                )
            )
            # SQS consume on all watchtower-cribl-* queues (spans both regions).
            reader_role.add_to_policy(
                iam.PolicyStatement(
                    sid="ConsumeWatchtowerCriblQueues",
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "sqs:ChangeMessageVisibility",
                        "sqs:DeleteMessage",
                        "sqs:GetQueueAttributes",
                        "sqs:GetQueueUrl",
                        "sqs:ReceiveMessage",
                    ],
                    resources=[
                        f"arn:{self.partition}:sqs:*:{account_id}:"
                        f"watchtower-cribl-*"
                    ],
                )
            )
            self.reader_role = reader_role
            CfnOutput(
                self,
                "CriblReaderRoleArn",
                value=reader_role.role_arn,
                description="AssumeRole ARN for the Cribl S3 Source",
            )
            CfnOutput(
                self,
                "CriblExternalId",
                value=cfg.reader.external_id,
                description="External ID for the Cribl S3 Source AssumeRole",
            )

        # ------------------------------------------------------------------
        # Outputs — the values to paste into Cribl's S3 Source (per region).
        # ------------------------------------------------------------------
        CfnOutput(self, "BucketName", value=bucket.bucket_name)
        CfnOutput(self, "TopicArn", value=topic.topic_arn)
        CfnOutput(self, "QueueArn", value=queue.queue_arn)
        CfnOutput(self, "QueueUrl", value=queue.queue_url)
        CfnOutput(self, "Region", value=region)
