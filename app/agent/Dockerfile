# Tessera AgentCore runtime image — the single generic agent runtime (FastAPI + Strands +
# ag-ui-strands + Bedrock) that every ecosystem's agents run inside, configured per request.
#
# Satisfies the Amazon Bedrock AgentCore Runtime container contract: linux/arm64, serves
# POST /invocations + GET /ping on 0.0.0.0:8080. Built by a standard Docker build:
#   docker buildx build --platform linux/arm64 -t <ecr>:<tag> . && docker push ...
# (No agentcore CLI required — this is a normal, reviewable, pipeline-built image.)
#
# This file is vendored into every exported ecosystem package under runtime/Dockerfile and
# built by runtime/build-and-push-runtime.sh at deploy time.
FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

# System deps for building a couple of wheels (cryptography, etc.); slim keeps the image small.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install runtime dependencies straight from pyproject's [project.dependencies].
# python3.12 stdlib `tomllib` parses it, so there is no second source of truth to drift.
COPY pyproject.toml ./
RUN python -c "import tomllib,subprocess,sys; \
deps=tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']; \
subprocess.check_call([sys.executable,'-m','pip','install','--no-cache-dir',*deps])"

# Application source.
COPY src/ ./src/

# Run as a non-root user (CKV_DOCKER_3). Create an unprivileged user and hand it /app so
# the runtime process has no root privileges inside the container.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# AgentCore Runtime contract.
EXPOSE 8080

# Container HEALTHCHECK (CKV_DOCKER_2) — probes the AgentCore GET /ping contract using the
# stdlib (no extra packages needed in the slim image).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/ping', timeout=4).status==200 else 1)" || exit 1

CMD ["python", "src/main.py"]
