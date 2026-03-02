#!/bin/bash

echo "Starting FastAPI..."

uvicorn src.api.main:app --host 0.0.0.0 --port 8000 &

# wait for API to boot
sleep 5

echo "Starting Streamlit..."

streamlit run src/ui/app.py \
  --server.port=7860 \
  --server.address=0.0.0.0