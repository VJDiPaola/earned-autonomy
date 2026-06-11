# Cloud Run image: Python 3.12 + Node LTS (Node needed for the Phoenix MCP
# server, which the reflection agent launches via `npx @arizeai/phoenix-mcp`).
FROM python:3.12-slim

# Node 20 LTS from NodeSource (Debian's default is older)
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

# Pre-warm the npx cache so the MCP server's first boot isn't a cold download.
RUN npx -y @arizeai/phoenix-mcp@latest --help >/dev/null 2>&1 || true

ENV PYTHONUNBUFFERED=1
# Cloud Run injects PORT; seed the demo DB on boot, then serve.
CMD ["/bin/sh", "-c", "uv run python -m earned_autonomy.seed.data && uv run uvicorn earned_autonomy.web.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
