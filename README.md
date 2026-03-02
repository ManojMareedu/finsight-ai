---
title: FinSight AI
emoji: 📊
colorFrom: blue
colorTo: purple
sdk: docker
app_port: 7860
---


# FinSight AI — Multi-Agent Financial Due Diligence System
[![CI](https://github.com/<your-org>/finsight-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/<your-org>/finsight-ai/actions)
[![HuggingFace Space](https://img.shields.io/badge/demo-huggingface-blue)](https://huggingface.co/spaces/<username>/finsight-ai)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

FinSight AI — a production-grade multi-agent RAG system that ingests SEC filings, runs structured LLM risk analysis, and produces analyst-style reports (FastAPI + Streamlit + Docker + CI).

It combines:

- Retrieval-Augmented Generation (RAG)
- Agent orchestration with LangGraph
- Structured LLM reasoning
- FastAPI backend
- Streamlit UI
- Docker deployment
- CI validation pipeline

The goal is simple:

> Turn raw financial filings into clear, analyst-style risk insights.

---

## 🌐 Live Demo

🔗 **HuggingFace Space:**  
[ManojM25/finsight-ai](https://huggingface.co/spaces/ManojM25/finsight-ai)

Example output includes:

- Executive summary
- Key risk factors
- Overall risk score
- Investment recommendation

---

## 🧠 System Architecture

User
  ↓
Streamlit UI
  ↓
FastAPI API
  ↓
LangGraph Workflow
  ├── Filing Agent (RAG)
  ├── Risk Agent (LLM reasoning)
  └── Synthesis Agent
  ↓
Final Report


## ⚙️ Core Components

### 1️⃣ RAG Pipeline

- SEC EDGAR filing retrieval
- Text cleaning and chunking
- Embedding generation (sentence-transformers)
- ChromaDB vector storage
- MMR-based retrieval

**Key files**

- src/rag/ingestion.py
- src/rag/ingestion.py
- src/rag/embeddings.py


---

### 2️⃣ Multi-Agent Workflow (LangGraph)

The system uses a typed shared state passed across agents.

#### Filing Agent
- Retrieves relevant filing context from vector DB.

#### Risk Agent
- Performs structured financial risk analysis.
- Returns validated JSON via Pydantic schemas.

#### Synthesis Agent
- Produces final analyst-style report.

**Key files**
- src/agents/
- src/graph/workflow.py
- src/graph/state.py

### 3️⃣ Structured LLM Layer

- OpenRouter (OpenAI-compatible client)
- Model abstraction layer
- Defensive JSON parsing
- Retry logic for reliability

**Key file**
- src/utils/llm_client.py


### 4️⃣ API Layer

FastAPI exposes the system as a service.

Endpoints:

- `GET /health`
- `POST /analyze`

#### Example request:

```json
{
  "company_name": "Tesla"
}
```

#### Example response:
```json
{
  "company": "Tesla",
  "report": {
    "executive_summary": "...",
    "key_risks": [],
    "overall_risk_score": 6.5,
    "recommendation": "..."
  }
}
```

**Key files**
- src/api/main.py
- src/api/routes/

### 5️⃣ Streamlit UI

Simple interface for interactive analysis.
- Company input
- Live report display
- Risk score visualization

**Key file**
- src/ui/app.py

### 6️⃣ Observability
Langfuse tracing is integrated into the LLM layer.

If keys are not provided, tracing is automatically disabled.

**Key file**
- src/observability/tracer.py

### 7️⃣ Evaluation
Basic retrieval evaluation pipeline included.
- make eval

**Key file**
- src/evaluation/ragas_eval.py


## Engineering Highlights

- Multi-agent architecture using LangGraph
- Structured LLM outputs via Pydantic schemas
- RAG pipeline with SEC filings + ChromaDB
- FastAPI backend + Streamlit frontend
- Dockerized deployment on HuggingFace Spaces
- CI pipeline with linting, typing, and tests

## Project Structure
finsight-ai/
├── src/
│   ├── agents/
│   ├── api/
│   ├── evaluation/
│   ├── graph/
│   ├── models/
│   ├── observability/
│   ├── rag/
│   ├── ui/
│   └── utils/
├── tests/
├── docker/
├── configs/
├── data/
├── Dockerfile
├── start.sh
└── README.md

## 🚀 Local Setup
# Clone the repo (replace <repo-url> with your repository URL)
git clone <repo-url>
cd finsight-ai

# Create & activate virtual environment (macOS / Linux)
python -m venv .venv
source .venv/bin/activate

# (Windows PowerShell)
# python -m venv .venv
# .\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Create a .env file at project root with these values (edit keys as needed)
cat > .env <<EOF
OPENROUTER_API_KEY=your_openrouter_api_key_here
PRIMARY_MODEL=openrouter/free
# Optional (observability)
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=
EOF

# Optional: if you hit import path errors, make sure project root is on PYTHONPATH
export PYTHONPATH=$(pwd)

# (Optional) Rebuild vector store / ingest a company filing (runs SEC fetch, clean, chunk, embed, store)
python -c "from src.rag.ingestion import ingest_company_filing; print('Stored', ingest_company_filing('Tesla','TSLA','./data/chroma'),'chunks')"

# Start the backend (FastAPI) in one terminal
uvicorn src.api.main:app --reload

# In another terminal (with .venv activated) start the UI (Streamlit)
streamlit run src/ui/app.py

# Quick evaluation (retrieval inspection)
make eval
# or
python src/evaluation/ragas_eval.py

# Run the whole system inside Docker (build + run)
docker build -t finsight-ai .
docker run -p 7860:7860 finsight-ai

### 🔄 CI Pipeline
GitHub Actions automatically runs:

- Ruff lint
- Mypy type checks
- Pytest
- Docker build validation

**Workflow file:**
- .github/workflows/ci.yml

## ⚠️ Notes

- Free LLM models may be slower or rate-limited.
- SEC filings contain structured XBRL markup; cleaning is applied during ingestion.
- Langfuse tracing is optional and disabled if keys are missing.

## 📌 Future Improvements

- Potential extensions:
- Automated RAGAS scoring
- Multi-company comparison
- Streaming responses
- Model routing strategies
- Caching layer for faster inference

## 👤 Author

Manoj Mareedu


## 📄 License
- MIT