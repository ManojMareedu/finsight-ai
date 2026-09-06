install:
	pip install -r requirements.txt

run-api:
	uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000

run-ui:
	streamlit run src/ui/app.py

test:
	pytest tests/ -v

lint:
	ruff check src tests && black --check src tests && mypy src

eval:
	python -m src.evaluation.ragas_eval

benchmark:
	python -m src.evaluation.benchmark

product-eval:
	python -m src.evaluation.report_eval

eval-dataset:
	python -m src.evaluation.dataset check

.PHONY: install run-api run-ui test lint eval benchmark product-eval eval-dataset