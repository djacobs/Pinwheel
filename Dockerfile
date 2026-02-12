# Pinwheel Fates — Multi-stage Docker build

FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

# Copy project metadata and source
COPY pyproject.toml ./
COPY src/ src/

# Install everything (deps + project) into a venv
RUN uv venv /app/.venv && \
    . /app/.venv/bin/activate && \
    uv pip install --no-cache .

# Runtime stage
FROM python:3.12-slim

WORKDIR /app

# Copy venv from builder
COPY --from=builder /app/.venv /app/.venv

# Copy app assets (templates, static, scripts)
COPY templates/ templates/
COPY static/ static/
COPY scripts/ scripts/

# Ensure the venv is on PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["uvicorn", "pinwheel.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
