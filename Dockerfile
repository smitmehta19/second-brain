FROM python:3.12-slim

WORKDIR /app

# Install system deps for yt-dlp, lxml, and git (for mind map push)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg git cron curl && \
    rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

# Copy source
COPY src/ src/
COPY scripts/ scripts/
COPY docs/ docs/

# Create data directory for SQLite (mounted as a volume in production —
# it holds the DB, logs, and JSON state; docs/notes.json is only a cache
# regenerated from the DB, so it needs no volume)
RUN mkdir -p data vault

# Expose API port for dashboard
EXPOSE 8080

# Liveness probe — /healthz checks DB reachability (no auth required)
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
    CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1

# Setup daily mind map + notes.json regeneration cron (midnight UTC)
RUN echo "0 0 * * * cd /app && python scripts/generate_mindmap.py >> /var/log/mindmap.log 2>&1\n0 */6 * * * cd /app && python -m src.search.export_json >> /var/log/export.log 2>&1" | crontab -

# Start cron, prime the notes.json cache from the DB, then run bot + API server
CMD cron && (python -m src.search.export_json || true) && python -m src.main
