#!/bin/bash
set -e

echo "=== FinSight AI Starting ==="
echo "CHROMA_PERSIST_DIR: ${CHROMA_PERSIST_DIR:-./data/chroma}"

# Start FastAPI in background
echo "Starting FastAPI on port 8000..."
uvicorn src.api.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 1 \
  --log-level info &

API_PID=$!

# Wait for API to be healthy — poll instead of fixed sleep
# sleep 5 is unreliable: too short on slow starts, wastes time on fast starts
echo "Waiting for API to be ready..."
MAX_WAIT=120
WAITED=0
until curl -sf http://localhost:8000/health > /dev/null 2>&1; do
    if [ $WAITED -ge $MAX_WAIT ]; then
        echo "ERROR: API did not start within ${MAX_WAIT}s"
        exit 1
    fi
    sleep 2
    WAITED=$((WAITED + 2))
    echo "  Waited ${WAITED}s..."
done

echo "API is ready after ${WAITED}s"

# Start Streamlit — this process stays in foreground
# so the container stays alive as long as Streamlit runs
echo "Starting Streamlit on port 7860..."
exec streamlit run src/ui/app.py \
    --server.port=7860 \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false