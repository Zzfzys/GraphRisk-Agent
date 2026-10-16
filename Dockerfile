FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim@sha256:4f5d923c9dcea037f57bda425dd209f3ec643da2f0b74227f68d09dab0b3bb36
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /usr/local/bin/
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=0 UV_CACHE_DIR=/app/.uv-cache UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --group demo --no-dev --no-install-project

COPY src ./src
COPY api ./api
COPY configs ./configs
COPY data/policies/stage05 ./data/policies/stage05
COPY streamlit_app.py ./
RUN uv sync --locked --group demo --no-dev --no-editable

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
