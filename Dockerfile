# ── Dalios Trading System — Production Docker Image ──────────
# Lightweight Python 3.12 image (~150MB base)
# No torch, no transformers, no heavy ML dependencies
# Target: <300MB total image size

FROM python:3.12-slim AS base

# System deps for PostgreSQL client + build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directories
RUN mkdir -p data/storage logs

# Expose API port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import requests; r=requests.get('http://localhost:8000/api/health'); exit(0 if r.status_code==200 else 1)" || exit 1

# Run API server
CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
