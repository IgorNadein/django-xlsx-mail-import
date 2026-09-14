.PHONY: check test lint format migrate sample

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy .
	uv run pytest
	uv run python manage.py check
	uv run python manage.py makemigrations --check --dry-run

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run mypy .

format:
	uv run ruff check --fix .
	uv run ruff format .

migrate:
	uv run python manage.py migrate

sample:
	uv run python manage.py seed_demo_users
	uv run python manage.py generate_sample_xlsx samples/mailings.xlsx
