"""Config loader: deep-merge globals + account, produce a typed spec.

Ported from the super-fiesta pattern, adapted for cloud-watchtower:
  - ``configs/infrastructure.yaml`` has a ``globals:`` block and an
    ``accounts:`` list.
  - The chosen account's dict is deep-merged ON TOP of globals (account wins).
  - ``${ENV_VAR}`` tokens in the YAML are substituted from the environment
    (loaded from ``.env`` if present) AND from ``cdk.json`` context, so account
    IDs committed to context resolve without a ``.env``.

Which side of the pipeline is produced depends on the account name:
  - ``logarchive``                    -> InfrastructureSpec.log_archive
  - ``sandbox|development|production`` -> InfrastructureSpec.writer
  - ``homelab``                       -> InfrastructureSpec.homelab
"""

import json
import os
import string
from pathlib import Path

import yaml
from dacite import from_dict
from dotenv import load_dotenv

from configs.models import (
    HomelabWriterConfig,
    InfrastructureSpec,
    LogArchiveConfig,
    WriterConfig,
)
from utils.converters import update
from utils.logger import configure_logger

LOGGER = configure_logger(__name__)

_CONFIG_FILE = Path(__file__).parent / "infrastructure.yaml"
_CDK_JSON = Path(__file__).parent.parent / "cdk.json"

# Map profile name -> the ${ENV}/context key holding its account id.
# 'homelab' resolves to the LOGARCHIVE account id: the IAM user is created in
# the logarchive account (same account as the bucket).
_ACCOUNT_ID_KEY = {
    "logarchive": "LOGARCHIVE_ACCOUNT_ID",
    "sandbox": "SANDBOX_ACCOUNT_ID",
    "development": "DEVELOPMENT_ACCOUNT_ID",
    "production": "PRODUCTION_ACCOUNT_ID",
    "homelab": "LOGARCHIVE_ACCOUNT_ID",
}

# cdk.json context keys are lowercase (super-fiesta style); map the ${ENV}
# names used in the YAML to their context equivalents so committed context can
# satisfy the tokens without a .env file.
_ENV_TO_CONTEXT = {
    "LOGARCHIVE_ACCOUNT_ID": "logarchive_account_id",
    "SANDBOX_ACCOUNT_ID": "sandbox_account_id",
    "DEVELOPMENT_ACCOUNT_ID": "development_account_id",
    "PRODUCTION_ACCOUNT_ID": "production_account_id",
    "AUDIT_ACCOUNT_ID": "audit_account_id",
    "DEPLOYMENT_ACCOUNT_ID": "deployment_account_id",
    "ORG_ID": "org_id",
}


class AppConfigs:
    def __init__(self) -> None:
        env_file = Path(".env")
        if env_file.exists():
            load_dotenv(env_file)
            LOGGER.info("Loaded environment variables from .env")
        else:
            LOGGER.info("No .env found; using cdk.json context + system env")
        self._context = self._load_cdk_context()

    def _load_cdk_context(self) -> dict[str, str]:
        """Read the ``context`` block from cdk.json (account IDs, org id)."""
        if not _CDK_JSON.exists():
            return {}
        try:
            with open(_CDK_JSON, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return dict(data.get("context", {}))
        except (json.JSONDecodeError, OSError) as exc:
            LOGGER.warning("Could not read cdk.json context: %s", exc)
            return {}

    def _substitution_map(self, account_name: str) -> dict[str, str]:
        """Build the token substitution map: env > cdk.json context > account."""
        combined: dict[str, str] = {"account": account_name}
        # cdk.json context keys are lowercase; expose their ${ENV} aliases too.
        for env_key, ctx_key in _ENV_TO_CONTEXT.items():
            if ctx_key in self._context:
                combined[env_key] = str(self._context[ctx_key])
        # Real environment variables win over committed context.
        combined.update(
            {k: v for k, v in os.environ.items() if k in _ENV_TO_CONTEXT}
        )
        return combined

    def _load_yaml(self, file, context: dict[str, str]):
        def string_constructor(loader, node):
            template = string.Template(node.value)
            return template.safe_substitute(context)

        loader = yaml.SafeLoader
        loader.add_constructor("tag:yaml.org,2002:str", string_constructor)
        loader.add_implicit_resolver(
            "tag:yaml.org,2002:str", string.Template.pattern, None
        )
        return yaml.load(file, Loader=loader)

    def _from_yaml(self, context: dict[str, str]) -> dict:
        if not _CONFIG_FILE.exists():
            raise FileNotFoundError(f"Could not find config at {_CONFIG_FILE}")
        with open(_CONFIG_FILE, "r", encoding="utf-8") as file:
            data = self._load_yaml(file, context=context)
        return data or {}

    def get_infrastructure_info(self, account_name: str) -> InfrastructureSpec:
        """Resolve globals+account into a typed spec for the account."""
        if account_name not in _ACCOUNT_ID_KEY:
            raise ValueError(
                f"Unknown account '{account_name}'. "
                f"Known: {sorted(_ACCOUNT_ID_KEY)}"
            )

        subs = self._substitution_map(account_name)
        data = self._from_yaml(context=subs)

        globals_config: dict = data.get("globals", {})
        accounts: list = data.get("accounts", [])
        account = next(
            (a for a in accounts if a.get("name") == account_name), None
        )
        if account is None:
            raise ValueError(
                f"Account '{account_name}' not found in infrastructure.yaml"
            )

        account_id = account["account"]
        if account_id.startswith("$") or not account_id.isdigit():
            raise ValueError(
                f"Account id for '{account_name}' did not resolve "
                f"(got {account_id!r}). Set {_ACCOUNT_ID_KEY[account_name]} in "
                f"cdk.json context or .env."
            )
        masked = f"***{account_id[-4:]}"
        LOGGER.info("Account %s -> %s", account_name, masked)

        log_archive_cfg = None
        writer_cfg = None
        homelab_cfg = None
        if account_name == "logarchive":
            merged = update(
                dict(globals_config.get("log_archive", {})),
                dict(account.get("log_archive", {})),
            )
            log_archive_cfg = from_dict(
                data_class=LogArchiveConfig, data=merged
            )
        elif account_name == "homelab":
            merged = update(
                dict(globals_config.get("homelab", {})),
                dict(account.get("homelab", {})),
            )
            homelab_cfg = from_dict(
                data_class=HomelabWriterConfig, data=merged
            )
        else:
            merged = update(
                dict(globals_config.get("writer", {})),
                dict(account.get("writer", {})),
            )
            writer_cfg = from_dict(data_class=WriterConfig, data=merged)

        return InfrastructureSpec(
            account=account_id,
            region=account["region"],
            profile_name=account_name,
            log_archive=log_archive_cfg,
            writer=writer_cfg,
            homelab=homelab_cfg,
        )
