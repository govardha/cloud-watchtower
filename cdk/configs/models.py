"""Typed config models for cloud-watchtower.

Mirrors the super-fiesta dataclass/dacite pattern. The YAML deep-merges a
``globals:`` block with the selected ``accounts[]`` entry (account wins), then
dacite hydrates these dataclasses. Everything is explicit — no ``latest`` and
no magic defaults that hide intent.

Sides of the pipeline, selected by account name:
  * ``logarchive``                       -> LogArchiveConfig (bucket/SNS/SQS/reader)
  * ``sandbox|development|production``    -> WriterConfig (shared writer role)
  * ``homelab``                          -> HomelabWriterConfig (IAM user, no role)
"""

from dataclasses import dataclass, field


@dataclass
class ReaderConfig:
    """Cribl reader side: the cross-account role the audit-account Cribl user
    assumes, plus the SNS/SQS fan-out sizing.

    The Cribl IAM *user* is NOT created here — it already exists in the audit
    account (``cribl_user_arn``). This app only creates the logarchive role it
    assumes (ExternalId-gated) and the SNS/SQS plumbing.
    """

    role_name: str = "watchtower-cribl-reader"
    cribl_user_arn: str = (
        "arn:aws:iam::698777852125:user/cribl-servicaccount"
    )
    external_id: str = ""
    # SQS tuning — defaults match the proven CloudTrail->Cribl queue.
    queue_visibility_seconds: int = 300
    queue_retention_seconds: int = 1209600  # 14 days
    dlq_max_receive_count: int = 5
    # SNS->SQS raw message delivery (see plan §9 open item — confirm vs Cribl).
    raw_message_delivery: bool = True


@dataclass
class LogArchiveConfig:
    """The logarchive side: two regional buckets + SNS/SQS + the reader role."""

    # Bucket name is derived per-region from this pattern:
    #   watchtower-logarchive-<region>-<account_id>
    bucket_name_pattern: str = (
        "watchtower-logarchive-{region}-{account_id}"
    )
    regions: list[str] = field(
        default_factory=lambda: ["us-east-1", "us-east-2"]
    )
    # Region short-codes for shorter SNS/SQS/stack names (us-east-1 -> ue1).
    region_short_codes: dict[str, str] = field(
        default_factory=lambda: {"us-east-1": "ue1", "us-east-2": "ue2"}
    )
    lifecycle_expiration_days: int = 30
    abort_incomplete_multipart_days: int = 7
    # "S3" (SSE-S3/AES256, free) — SSE-KMS is the work-time upgrade (plan §10).
    sse: str = "S3"
    versioning: bool = False
    org_id: str = ""
    reader: ReaderConfig = field(default_factory=ReaderConfig)


@dataclass
class WriterConfig:
    """The workload side: one shared writer role per account.

    ``role_name`` is rendered from ``role_name_pattern`` with the account
    profile name. Trust principals cover every compute type that may write
    logs (EKS Pod Identity, EC2, ECS, Lambda) per design doc §6.
    """

    role_name_pattern: str = "watchtower-writer-{account}"
    # The bucket name pattern must match LogArchiveConfig so the writer's
    # identity policy targets the right ARNs.
    bucket_name_pattern: str = (
        "watchtower-logarchive-{region}-{account_id}"
    )
    logarchive_account_id: str = "766997230140"
    target_regions: list[str] = field(
        default_factory=lambda: ["us-east-1", "us-east-2"]
    )


@dataclass
class HomelabWriterConfig:
    """The home-lab writer side: a single IAM *user* in the logarchive account.

    Unlike ``WriterConfig`` (EKS-in-AWS, cross-account, account-id prefix), the
    home-lab cluster has no AWS account. Its k3s workloads authenticate with a
    long-lived access key belonging to a same-account IAM user, whose identity
    policy alone grants write-only access into a FIXED literal prefix (there is
    no ``aws:PrincipalAccount`` to key on). See docs/plan.md §5 (home-lab path).

    The access key is NOT created here — keys in CloudFormation would leak the
    secret into template/state/outputs. Mint it once post-deploy:
        aws iam create-access-key --user-name <user_name> --profile admin-logarchive
    """

    user_name: str = "watchtower-writer-home"
    # Literal S3 key prefix this user may write under (no delete anywhere).
    prefix: str = "homelab/cauldron"
    # Bucket(s) to target; must match LogArchiveConfig.bucket_name_pattern.
    bucket_name_pattern: str = (
        "watchtower-logarchive-{region}-{account_id}"
    )
    # The account that owns BOTH the bucket and this user (same-account grant).
    logarchive_account_id: str = "766997230140"
    target_regions: list[str] = field(
        default_factory=lambda: ["us-east-1"]
    )


@dataclass
class InfrastructureSpec:
    """Resolved (globals + account) spec handed to the stacks.

    Exactly one of ``log_archive`` / ``writer`` / ``homelab`` is populated,
    based on which account was selected (logarchive vs an EKS workload account
    vs the home-lab side).
    """

    account: str
    region: str
    profile_name: str
    log_archive: LogArchiveConfig | None = None
    writer: WriterConfig | None = None
    homelab: HomelabWriterConfig | None = None
