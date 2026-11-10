# One image for gateway, worker and agent. The command is set per service.
FROM python:3.12-slim AS builder

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1

RUN pip install --no-cache-dir uv==0.13.7

WORKDIR /build
COPY pyproject.toml uv.lock .python-version ./
# Third-party packages get their own layer so source edits do not reinstall them.
RUN uv sync --locked --no-install-project
COPY src ./src
RUN uv sync --locked --no-editable


FROM python:3.12-slim AS runtime

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv

USER app
