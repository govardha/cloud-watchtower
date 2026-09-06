# ---------------------------------------------------------------------------
# cloud-watchtower — log archive S3 + IAM (CDK).
#
# Guardrails baked in (per steering rules):
#   * `whoami` (aws sts get-caller-identity) before any mutating op
#   * `cdk diff` gate before deploy
#   * destroy requires explicit scope restatement (CONFIRM=<account>)
#
# Pick the target with ACCOUNT (logarchive | sandbox | development | production | homelab).
# The matching AWS SSO profile is admin-<ACCOUNT> in your ~/.aws/config
# (except homelab, which deploys via admin-logarchive — its user lives there).
#
# Deploy path is `make deploy` ONLY — there is no pipeline (home lab; see
# docs/plan.md §8). Fresh bootstrap qualifier: watchtwr26.
#
# Usage:
#   make synth     ACCOUNT=logarchive
#   make diff      ACCOUNT=logarchive
#   make deploy    ACCOUNT=logarchive
#   make bootstrap ACCOUNT=logarchive          # both regions
#   make destroy   ACCOUNT=logarchive CONFIRM=logarchive
# ---------------------------------------------------------------------------

ACCOUNT      ?= logarchive
# The homelab IAM user is created IN the logarchive account, so it deploys via
# the admin-logarchive profile (not a non-existent admin-homelab).
ifeq ($(ACCOUNT),homelab)
AWS_PROFILE  := admin-logarchive
else
AWS_PROFILE  := admin-$(ACCOUNT)
endif
QUALIFIER    := watchtwr26
TOOLKIT      := CDKToolkit-$(QUALIFIER)
DEPLOY_ACCT  := 766789219588
REGIONS      := us-east-1 us-east-2
CDK          := cdk -c account=$(ACCOUNT)
PY           := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

export WATCHTOWER_ACCOUNT = $(ACCOUNT)
export AWS_PROFILE

# Optional: narrow which logarchive regions are synthesized/deployed. Comma-
# separated, must be a subset of infrastructure.yaml's regions. Handy for a
# home-lab env that never bootstraps us-east-2:
#   make deploy ACCOUNT=logarchive DEPLOY_REGIONS=us-east-1
ifdef DEPLOY_REGIONS
export WATCHTOWER_REGIONS = $(DEPLOY_REGIONS)
endif

.PHONY: help whoami venv synth diff deploy destroy bootstrap bootstrap-deployment

help:
	@echo "Targets: whoami | venv | synth | diff | deploy | destroy"
	@echo "         bootstrap | bootstrap-deployment"
	@echo "Set ACCOUNT=logarchive|sandbox|development|production|homelab (default: logarchive)"
	@echo "Optional: DEPLOY_REGIONS=us-east-1[,us-east-2] narrows logarchive regions"
	@echo "destroy also requires CONFIRM=<account>"

venv:
	python3 -m venv .venv && . .venv/bin/activate && \
	  pip install -r requirements.txt

# Confirm WHICH account/identity we're pointed at before doing anything real.
whoami:
	@echo ">> AWS profile: $(AWS_PROFILE) | watchtower account: $(ACCOUNT)"
	aws sts get-caller-identity --profile $(AWS_PROFILE)

# Pure local synth — no AWS mutation, safe to run anytime.
synth:
	$(CDK) synth

# MUST run before deploy — shows the changeset.
diff: whoami
	$(CDK) diff

# Deploy gates on identity + a diff first (no blind applies).
deploy: whoami diff
	@echo ">> Deploying cloud-watchtower ($(ACCOUNT)) via $(AWS_PROFILE)."
	$(CDK) deploy --all --profile $(AWS_PROFILE) --require-approval any-change

# One-time per account/region. Bootstraps BOTH regions under watchtwr26.
# The deployment account is the trusted cross-account deployer.
bootstrap: whoami
	@for R in $(REGIONS); do \
	  echo ">> Bootstrapping $(ACCOUNT) ($$R) qualifier=$(QUALIFIER) trust=$(DEPLOY_ACCT)"; \
	  cdk bootstrap "aws://$$(aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text)/$$R" \
	    --profile $(AWS_PROFILE) \
	    --qualifier $(QUALIFIER) \
	    --toolkit-stack-name $(TOOLKIT) \
	    --trust $(DEPLOY_ACCT) \
	    --cloudformation-execution-policies arn:aws:iam::aws:policy/AdministratorAccess; \
	done

# Bootstrap the DEPLOYMENT account itself (no --trust; it is the deployer).
# Run with ACCOUNT=deployment (needs an admin-deployment profile).
bootstrap-deployment: whoami
	@for R in $(REGIONS); do \
	  echo ">> Bootstrapping deployment ($$R) qualifier=$(QUALIFIER) (no trust)"; \
	  cdk bootstrap "aws://$(DEPLOY_ACCT)/$$R" \
	    --profile admin-deployment \
	    --qualifier $(QUALIFIER) \
	    --toolkit-stack-name $(TOOLKIT); \
	done

# Destructive: requires restating the account to proceed.
destroy: whoami
	@if [ "$(CONFIRM)" != "$(ACCOUNT)" ]; then \
	  echo "REFUSING: destroy requires scope restatement."; \
	  echo "Re-run: make destroy ACCOUNT=$(ACCOUNT) CONFIRM=$(ACCOUNT)"; \
	  exit 1; \
	fi
	@echo ">> Destroying ALL stacks for account '$(ACCOUNT)' (confirmed)."
	$(CDK) destroy --all --profile $(AWS_PROFILE) --force
