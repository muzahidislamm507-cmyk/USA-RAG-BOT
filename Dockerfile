# ============================================================
# Dockerfile — RAG Chatbot (FastAPI + FAISS + Gemini)
# Multi-stage, production-ready build
# ============================================================

# ---------- Stage 1: Builder ----------
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies required by some pip packages
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---------- Stage 2: Runtime ----------
FROM python:3.12-slim AS runtime

LABEL maintainer="muzahid"
LABEL description="RAG Demo Chatbot — FastAPI + FAISS + Gemini"

# Create a non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application source code
COPY api.py .
COPY rag_core.py .
COPY chunker.py .
COPY orders_api.py .
COPY patch_api.py .
COPY eval.py .

# Copy static assets and knowledge-base docs
COPY static/ ./static/
COPY docs/ ./docs/

# Copy pre-built index & data files (if they exist at build time)
# These can also be volume-mounted at runtime
COPY chunks.pkl* ./
COPY vector_store.index* ./
COPY unanswered.json* ./

# Start script: fixes folder ownership of the Render disk, then runs the app as appuser
COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

# Ensure the app directory is owned by the non-root user
RUN chown -R appuser:appuser /app

# NOTE: no 'USER appuser' here. entrypoint.sh starts as root, fixes the ownership of
# the Render disk (/app/data), then switches to appuser before the app starts.

# Expose the application port
EXPOSE 8000

# Health check — hit the /health endpoint every 30s
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Default port (can be overridden via $PORT env var)
ENV PORT=8000

ENTRYPOINT ["/entrypoint.sh"]

# Start the application
CMD uvicorn api:app --host 0.0.0.0 --port ${PORT}
