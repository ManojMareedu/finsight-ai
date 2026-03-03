FROM python:3.11-slim

# Security: run as non-root
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Install system dependencies needed by chromadb and sentence-transformers
RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first — Docker layer cache means this only
# re-runs when requirements.txt actually changes
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Bake the embedding model into the image at build time
# Without this, the 80MB model downloads on every cold start
# adding 2-3 minutes to first request on HuggingFace
RUN python -c "\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); \
print('Embedding model cached')"

# Copy application code after dependencies
# This layer only rebuilds when source code changes
COPY . .

# Set PYTHONPATH so src.* imports resolve correctly
ENV PYTHONPATH=/app

# ChromaDB persistent storage — HuggingFace Spaces provides /data
# as a persistent volume (enable in Space settings)
ENV CHROMA_PERSIST_DIR=/data/chroma

RUN mkdir -p /data/chroma && chown -R appuser:appuser /app /data

USER appuser

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

CMD ["bash", "start.sh"]