.PHONY: install lint typecheck test run

install:
	python -m pip install -e ".[dev]"

lint:
	ruff check .

typecheck:
	mypy app

test:
	pytest

run:
	uvicorn app.api.main:app --reload
