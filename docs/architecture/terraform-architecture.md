# Terraform architecture

The infrastructure lives under [`infra/`](../../infra) as a root module that composes
nine focused child modules. Everything is config-driven: the AWS account is resolved at
runtime from the caller identity, the region comes from a variable, and no account ids,
emails, or model ids are hardcoded.

## Providers

| Provider | Version | Used for |
|---|---|---|
| `hashicorp/aws` | `~> 5.70` | All classic services (Lambda, API Gateway, DynamoDB, S3, CloudFront, Cognito, KMS, SQS, SNS, VPC, EventBridge, Step Functions, IAM, WAFv2, Bedrock Guardrail, Verified Permissions, Secrets Manager). |
| `hashicorp/awscc` | `~> 1.20` | Amazon Bedrock AgentCore Memory, Gateway, and Workload Identity — modelled through the AWS Cloud Control provider, which tracks new services sooner than the classic provider. |
| `hashicorp/archive` | `~> 2.4` | Packaging Lambda source into deployment zips. |
| `hashicorp/null` | `~> 3.2` | The small number of imperative steps. |

Terraform `>= 1.11` is required for S3-native state locking (see
[state backend](../deployment/state-backend.md)).

## Modules

| Module | Owns |
|---|---|
| `foundation` | VPC (public/private subnets, NAT), KMS keys, the shared Lambda DLQ, and the data S3 buckets (access-logs, ecosystems, runs, memory-payloads). |
| `data` | DynamoDB tables (WebSocket connections, attention inbox, catalog refresh state) and the EventBridge buses. |
| `identity` | Cognito (user pool, app clients, hosted-UI domain, resource server, M2M client) and Amazon Verified Permissions (Cedar policy store, schema, identity source, static policies). |
| `api` | The REST API Gateway (all routes, built from an OpenAPI document) and the WebSocket API, with the Cognito authorizer, stages, and access logs. |
| `compute` | The control-plane Lambda functions, the shared layer, their IAM, SNS topics, and API/S3/EventBridge wiring. |
| `orchestration` | The ingest / catalog / quorum runtime Lambdas, the catalog state machines, the catalog HTTP API, schedules, and the re-grounding rule. |
| `agentcore` | AgentCore Memory, Gateway, and Workload Identity (AWSCC), the Bedrock Guardrail, and the supporting IAM roles. The runtime itself is deployed by CLI (see below). |
| `frontend` | The site S3 bucket, the CloudFront distribution (Origin Access Control), and the CLOUDFRONT-scoped WAFv2 web ACL. |
| `auth-federated` | Optional OIDC SSO side resources — the PreSignUp / PreTokenGeneration trigger Lambdas (+ IAM, Cognito invoke permission) and an optional baseline `Users` group, all gated on a single variable. The OIDC identity provider itself and the app-client OAuth wiring live in `identity` (colocated with the pool + client). |

## Dependency graph

```
foundation ─┐
data ───────┼─▶ api ────────┐
            │                ├─▶ compute ─▶ orchestration
            ├─▶ agentcore ───┘
            └─▶ frontend ─▶ identity ─▶ auth-federated
```

- `api` references Lambda functions only by their deterministic names
  (`${project}-${environment}-<fn>`), so it does not depend on `compute`. The dependency
  flows `api → compute`: the compute module consumes the API ids to create the Lambda
  invoke permissions and to set the WebSocket callback URL. This keeps the graph
  acyclic.
- `agentcore` depends only on `foundation` (the memory-payloads bucket) and `identity`
  (Cognito for the Gateway's JWT authorizer). Its Memory/guardrail outputs flow into
  `compute` and `orchestration`.
- `frontend` depends only on `foundation`; its URL flows into `compute` (CORS origin),
  `orchestration` (catalog API CORS), and `identity` (the OAuth callback/logout origin
  used when federated login is enabled).
- `identity` depends on `frontend` for that callback origin. When `federated_login` is
  enabled it creates the OIDC IdP, switches the app client to the hosted-UI code flow,
  and wires the pool's PreSignUp / PreTokenGeneration triggers to `auth-federated`'s
  Lambdas by their deterministic ARNs — so `auth-federated` depends on `identity` (pool
  id) without a cycle.

## AgentCore Runtime

The container-based agent Runtime is the one piece not modelled as a declarative
resource — it is deployed with the `agentcore` CLI (which builds the source into a
container and creates the runtime + execution role). Its ARN is fed back into the
platform as the `agent_runtime_arn` variable. See
[AgentCore runtime](../deployment/agentcore-runtime.md).

## Naming

Every resource is named `${project}-${environment}-*` (e.g. `tessera-dev-agent-invoker`),
so environments are cleanly namespaced within an account.
