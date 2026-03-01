install:
	pip install -r requirements.txt

run-api:
	uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000

run-ui:
	streamlit run src/ui/app.py

test:
	pytest tests/ -v

lint:
	ruff check src/ && black --check src/ && mypy src/

eval:
	python -m src.evaluation.ragas_eval