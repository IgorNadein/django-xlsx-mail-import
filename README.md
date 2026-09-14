# Django XLSX Mailing Import

[Русская версия](README.ru.md)

A production-style Django management command that imports mailing jobs from an XLSX workbook and logs their delivery after a required random delay. It is designed for large files: the workbook is read in streaming mode, validation and database lookups are batched, and imported records are delivered through a database iterator.

## What the solution covers

- validates the workbook structure and every non-empty data row;
- checks referenced Django users without an N+1 query pattern;
- enforces idempotency with a database-level unique constraint on `external_id`;
- handles duplicates both inside one file and across repeated imports;
- inserts valid records in configurable batches;
- continues after row-level errors and reports their XLSX row numbers;
- stores import counters and delivery states for audit and recovery;
- claims a pending record transactionally before delivery;
- logs delivery after a random delay of 5–20 seconds;
- supports SQLite locally and PostgreSQL through environment variables;
- includes an admin interface, Docker Compose configuration, type checks, linting, and tests.

## Data model

`ImportRun` stores the source filename, timestamps, status, and processing counters. `MailingRecord` stores the source `external_id`, recipient, message data, owning import, and delivery state.

The `external_id` column is unique in the database. This is the final protection against two concurrent commands inserting the same external record. `bulk_create(ignore_conflicts=True)` lets one command continue when another wins that race.

Delivery states are `pending`, `processing`, `sent`, and `failed`. A command sends only records created by its own import run, so importing an old file never resends earlier records.

## XLSX format

The first row must contain these headers. Header order does not matter, and additional columns are allowed.

| Column | Meaning |
| --- | --- |
| `external_id` | Unique identifier in the source system, up to 128 characters |
| `user_id` | ID of an existing Django user |
| `email` | Recipient email address |
| `subject` | Message subject, up to 255 characters |
| `message` | Message text |

Blank rows are ignored. A malformed row is counted as erroneous and does not stop the remaining import.

## Local setup with SQLite

Python 3.10 or newer is required. The examples use [uv](https://docs.astral.sh/uv/); regular `pip` commands work as well.

```bash
uv sync --group dev
uv run python manage.py migrate
uv run python manage.py seed_demo_users
uv run python manage.py generate_sample_xlsx samples/mailings.xlsx
uv run python manage.py import_mailings samples/mailings.xlsx
```

The default command really waits 5–20 seconds before logging each message, as required by the assignment. Use `--no-send` when only the import stage is needed:

```bash
uv run python manage.py import_mailings samples/mailings.xlsx --no-send --batch-size 500
```

Example result:

```text
Import run: 43ffde16-7f4f-4ac2-a250-44928c9e7f2f
Processed rows: 2
Created records: 2
Skipped records: 0
Erroneous rows: 0
Sent messages: 2
Delivery failures: 0
```

Run the development server and inspect records in Django admin if needed:

```bash
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

The health endpoint is `GET /health/`.

## PostgreSQL with Docker

```bash
docker compose up --build --wait
docker compose exec web python manage.py seed_demo_users
docker compose exec web python manage.py generate_sample_xlsx /tmp/mailings.xlsx
docker compose exec web python manage.py import_mailings /tmp/mailings.xlsx
```

The application is available at <http://localhost:8016>. Put your own workbooks in `samples/`; the container sees that directory as read-only `/data`. Stop the stack with `docker compose down`; add `-v` to remove the database volume.

## Quality checks

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
```

Tests replace the real sleep call, keeping the suite fast while asserting that the production function chooses and uses a delay in the required 5–20 second range.

## Processing and reliability notes

XLSX parsing uses openpyxl's read-only mode, so worksheet rows are not all loaded into memory. The application still needs memory proportional to `--batch-size`, not to the complete file. Each batch performs set-based queries for users and existing identifiers, followed by a bulk insert.

The sample delivery implementation is synchronous because the assignment explicitly requests delayed log output. In a real mail system, imports would enqueue transactionally persisted jobs for asynchronous workers, and retries would use an idempotency key accepted by the email provider.

The current claim flow provides at-most-one active worker for a pending database record under normal operation. A process crash after the external send succeeds but before the `sent` update can leave a record in `processing`; a production outbox and provider idempotency key are the standard way to close that gap.

## License

[MIT](LICENSE)
