# Tessera — helper targets for the NON-Terraform deploy steps.
#
# Terraform is run DIRECTLY (terraform init / plan / apply from the infra/ directory),
# NOT through make — so its output and errors are easy to read and troubleshoot. This
# Makefile only wraps the surrounding steps (state bucket, Lambda packaging, agent,
# frontend, seeding, catalog ingestion, admin) and does NOT hide terraform behind an abstraction.
#
# Typical local flow (see docs/deployment/local-deployment.md):
#
#   make bootstrap                         # one-time: create the TF state bucket
#   make package                           # build Lambda zips + shared layer
#   source scripts/load-env.sh             # export AWS_PROFILE/REGION + $TF_VARS/$TF_BACKEND
#   cd infra
#   terraform init  $TF_BACKEND
#   terraform plan  $TF_VARS
#   terraform apply $TF_VARS
#   cd ..
#   make deploy-agent                      # deploy AgentCore runtime; writes the ARN to
#                                          #   environments/<env>/agent.auto.tfvars
#   #   then re-run terraform apply: `source scripts/load-env.sh` picks up that file via
#   #   $TF_VARS automatically, so from infra/: terraform apply $TF_VARS
#   make deploy-frontend
#   make seed                              # publish content + compile ecosystems (fast)
#   make create-admin EMAIL=you@example.com
#   make ingest                            # OPTIONAL, long-running: build + synthesize + promote the catalog
#
# Config comes from .env (AWS_PROFILE / AWS_REGION / ENVIRONMENT), loaded below.

# Load .env (if present) so AWS_PROFILE / AWS_REGION / ENVIRONMENT are picked up.
ifneq (,$(wildcard .env))
include .env
export
endif

ENV        ?= $(if $(ENVIRONMENT),$(ENVIRONMENT),dev)
AWS_REGION ?= us-east-1
PROJECT    ?= tessera

export ENVIRONMENT := $(ENV)
export AWS_REGION
export AWS_PROFILE

.PHONY: help bootstrap package deploy-agent deploy-frontend seed ingest create-admin set-websearch-key verify-websearch verify-layer tf-flags test test-unit test-integration test-venv

help:
	@grep -hE '^[a-zA-Z_-]+:.*?#' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*# "} {printf "  %-16s %s\n", $$1, $$2}' | sort

bootstrap: # one-time: create the Terraform state bucket + write backend.hcl (optional BUCKET=name)
	bash scripts/bootstrap.sh $(if $(BUCKET),--bucket $(BUCKET),)

package: # build Lambda packages + shared layer (verbose by default; VERBOSE=0 to quiet, ONLY="a b" for specific ones)
	VERBOSE=$(if $(VERBOSE),$(VERBOSE),1) ONLY="$(ONLY)" bash scripts/package-lambdas.sh

# Python tests. Creates a throwaway venv from app/tests/requirements.txt so the pinned
# pytest/moto/boto3 are used (system versions differ). Runs from app/ — the tests'
# conftest maps `tessera_platform` -> app/platform and keeps app/ off sys.path so the
# stdlib `platform` module isn't shadowed.
#
#   make test              # unit + integration (the CI gate)
#   make test-unit         # unit only
#   make test-integration  # integration only
#   make test ARGS="tests/unit/test_decision_engine.py -v"   # forward pytest flags
#
# Unit tests are offline (moto-mocked AWS). Integration tests exercise the platform
# library wired together (orchestrator + ecosystem YAML + memory ACLs), still offline.
TEST_VENV ?= .test-venv
test-venv:
	python3 -m venv $(TEST_VENV)
	$(TEST_VENV)/bin/pip install -q -r app/tests/requirements.txt

# NOTE: run pytest from the REPO ROOT (not `cd app`). Running from app/ puts app/ on
# sys.path[0] (cwd), so `import platform` resolves to app/platform/ and shadows the
# stdlib — breaking pytest's own reporter at session start. From the repo root the
# shadow can't happen; the conftest maps `tessera_platform` and tests use __file__-
# relative paths, so cwd doesn't matter otherwise.
test: test-venv # run unit + integration tests (CI gate)
	$(TEST_VENV)/bin/python -m pytest -c app/pytest.ini --rootdir app app/tests/unit app/tests/integration $(if $(ARGS),$(ARGS),-q)

test-unit: test-venv # run unit tests only
	$(TEST_VENV)/bin/python -m pytest -c app/pytest.ini --rootdir app app/tests/unit $(if $(ARGS),$(ARGS),-q)

test-integration: test-venv # run integration tests only
	$(TEST_VENV)/bin/python -m pytest -c app/pytest.ini --rootdir app app/tests/integration $(if $(ARGS),$(ARGS),-q)

tf-flags: # print the terraform flags for the current ENV (run terraform yourself from infra/)
	@echo "Run terraform directly from the infra/ directory, e.g.:"
	@echo "  cd infra"
	@echo "  terraform init  -backend-config=environments/$(ENV)/backend.hcl"
	@echo "  terraform plan  -var-file=environments/$(ENV)/terraform.tfvars"
	@echo "  terraform apply -var-file=environments/$(ENV)/terraform.tfvars"
	@echo "Or: source scripts/load-env.sh $(ENV)   then use \$$TF_BACKEND / \$$TF_VARS"

deploy-agent: # deploy the AgentCore runtime; saves the ARN to agent.auto.tfvars + .deploy-logs/agent-runtime-arn.env
	bash scripts/deploy-agent.sh

deploy-frontend: # build + publish the frontend (reads terraform outputs)
	bash scripts/deploy-frontend.sh

seed: # publish content + compile ecosystems (fast; does NOT ingest — run 'make ingest' next)
	bash scripts/seed.sh

ingest: # (optional, LONG-RUNNING) populate the catalog from domain sources; DOMAINS="a b" to scope
	bash scripts/ingest.sh $(if $(DOMAINS),--domains "$(DOMAINS)",)

create-admin: # bootstrap the first admin user (pass EMAIL=you@example.com)
	bash scripts/create-admin.sh $(EMAIL)

set-websearch-key: # store/rotate the web-search API key in Secrets Manager (prompts, hidden). ENV=dev
	PROJECT=$(PROJECT) ENV=$(ENV) bash scripts/set-websearch-key.sh

verify-websearch: # assert the web-search credential provider exists (fails loudly if missing). ENV=dev
	PROJECT=$(PROJECT) ENV=$(ENV) bash scripts/verify-websearch.sh

verify-layer: # assert the shared layer bundles the seed catalog content quorum falls back on
	bash scripts/verify-layer-content.sh
