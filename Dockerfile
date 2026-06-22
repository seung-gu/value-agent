# uv official image -- installs Python on its own (from .python-version, 3.14).
FROM ghcr.io/astral-sh/uv:bookworm-slim

WORKDIR /app

# 1) Install deps first -> this layer is cached even when the source changes.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev

# 2) Copy backend source (FE/app, .env, etc. are excluded via .dockerignore).
COPY . ./

# Railway injects $PORT. Binding to 0.0.0.0 is required for external access.
CMD uv run uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000}
