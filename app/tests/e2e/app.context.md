# Tessera App Context (for browser verification)

## What this app does
Tessera is a composable multi-agent platform. Users design ecosystems of AI agents via a visual Builder, run them with real-time streaming output, and get quality-assured reports.

## URL
- Production: (Amazon CloudFront distribution URL — see CFN output `FrontendUrl`)

## Auth
- Amazon Cognito login gate (email/password)
- Test credentials: use the test Cognito user pool configured for this environment

## Core workflows

### 1. View Ecosystems
- Route: main page, "Ecosystems" tab
- Lists all available ecosystems (loaded from Amazon S3)
- Each card shows ecosystem name, description, agent count

### 2. Run an Ecosystem
- To enter Run View, choose an ecosystem
- Enter a mission in the input box
- To start a run, choose "Run" → WebSocket connects, AWS Step Functions runs
- DAG map shows agent progress (idle → running → complete)
- Agent cards appear with streaming output
- RUN_FINISHED event ends the run

### 3. Builder
- Route: "Builder" tab
- Visual ecosystem editor: add agents, set dependencies, configure tools
- YAML preview panel
- "Save & Deploy" wizard: saves to S3 + compiles ASL + creates Step Functions state machine

### 4. Registry
- Route: "Registry" tab
- Lists all agents across ecosystems

## What to verify after a feature change
1. App loads without errors (no white screen)
2. Ecosystems list populates
3. Choosing an ecosystem shows its agents
4. Run button triggers the run (DAG map animates)
5. Agent output appears in cards (not empty, not raw SSE chunks)
6. Builder can add/remove agents
7. No console errors

## NOT in scope for verification
- Cognito signup flow
- CloudFront caching behavior
- Direct API calls (REST endpoints)
