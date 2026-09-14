# Changelog

## 0.2.0 — 2026-09-14

- Resume pending jobs after import-only runs, interrupted delivery or partial imports.
- Add `send_mailings`, explicit retry policies and token-protected atomic claims.
- Separate file progress from live delivery counts using import-to-job membership.
- Validate user ID bounds; checkpoint committed batches and preserve prior progress.
- Add database state constraints and a tested upgrade from the original schema.
- Cover real PostgreSQL import/claim races and delivery outside transactions in CI.
- Record a reproducible 10k / 100k-row benchmark with repeat-import assertions.
- Use locked runtime dependencies in Docker; document behavior in English and Russian.

## 0.1.0

Initial XLSX import, validation, deduplication and delayed logging implementation.
