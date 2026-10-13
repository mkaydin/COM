# Council of Minds — backend image
#
# Multi-stage: wheels are built once with a compiler available, then copied
# into a slim runtime that never sees a toolchain. The runtime still needs
# libgomp1 (OpenMP, pulled in by sentence-transformers' torch) and the
# Postgres/asyncpg runtime bits.

# ---------------------------------------------------------------- builder
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /wheels
COPY backend/requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt

# ---------------------------------------------------------------- runtime
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/com

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root. The wiki and the OKF bundle are written to at runtime, so /app is
# owned by the app user rather than left read-only.
RUN useradd --create-home --home-dir /home/com --shell /usr/sbin/nologin com

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels requirements.txt

COPY --chown=com:com backend/ ./

# Two writable knowledge stores:
#   /sage_wiki            - agents/sage_wiki.py resolves it as
#                           Path(__file__).parent.parent.parent / "sage_wiki"
#                           i.e. /app/agents -> /app -> / -> /sage_wiki
#   /app/okf/bundles/oracle - already inside the package (okf/bundle.py's
#                           BUNDLE_ROOT), so the COPY above covers it.
# Both are mounted as volumes in compose so filed knowledge survives rebuilds.
RUN mkdir -p /sage_wiki \
    && chown -R com:com /sage_wiki /app

# The `okfbundle` named volume is created root-owned by Docker, which shadows
# this directory. A tiny init container (see docker-compose.yml) chowns it
# before the backend starts; this just makes the in-image path correct for
# runs that skip compose.
ENV PYTHONPATH=/app

# NOTE: the `opencode-cli` provider — the default on a host install, and the
# only route to Zen's free models — is NOT available in this image: it shells
# out to an `opencode` binary, and adding one plus a credential mount is more
# machinery than the alternative providers cost. Set LLM_PROVIDER=ollama
# (free, local) or opencode (needs a funded Zen account) when running here.

EXPOSE 8734

HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
    CMD curl -fsS http://localhost:8734/api/health || exit 1

# Non-root, and init so PID 1 reaps the sentence-transformers subprocesses.
USER com

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8734"]