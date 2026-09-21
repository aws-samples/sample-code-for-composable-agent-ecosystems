# Tessera Agent Runtime

## Introduction

This is the Tessera agent runtime, deployed to Amazon Bedrock AgentCore. It serves all agents via a single container configured per-request through ecosystem YAML definitions.


## Prerequisites

- Python 3.12+
- AWS CLI v2 configured with appropriate profile
- AgentCore CLI installed (`pip install bedrock-agentcore`)
- AWS account bootstrapped for AgentCore

# Layout

The project is organized as follows:

There are two directories generated, `src/` and `test/`. At the root, there is a `.gitignore` file, a `.bedrock_agentcore.yaml` file
which is used for other `agentcore` commands like `deploy`, `dev`, and `invoke`.

## src/

The main entrypoint to your app is defined in `src/main.py`. Using the AgentCore SDK `@app.entrypoint` decorator, this file defines a Starlette ASGI app with the chosen Agent framework SDK
running within.

`src/mcp_client/client.py` implements an MCP client using the library from your chosen Agent framework SDK.

`src/model/load.py` instantiates your chosen model provider.

## test/

Tests are not defined by default. Add your own pytest definitions here.

# Developing locally

If installation was successful, a virtual environment is already created with dependencies installed.

Run `source .venv/bin/activate` before developing.

`agentcore dev` will start a local server on 0.0.0.0:8080.

To verify the server started successfully, open http://localhost:8080/health in your browser or run `curl http://localhost:8080/health`. A 200 response confirms the agent is running.

In a new terminal, you can invoke that server with:

`agentcore invoke --dev "What can you do"`

# Deployment

If you want to customize your project, you can run `agentcore configure` before deploying to set the runtime name, region, and model preferences. Otherwise, the default project settings
will work out of the box.

After providing credentials, `agentcore deploy` will deploy your project into Amazon Bedrock AgentCore.

Use `agentcore invoke` to invoke your deployed agent.
## Next Steps

After deployment, the agent runtime is accessible via the AgentCore invoke API. Use the Tessera frontend to run ecosystems, or invoke directly with `agentcore invoke`.

## Cleanup

To remove the deployed runtime: `agentcore destroy --profile example-profile --region us-east-1`.
