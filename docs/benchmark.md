# Import benchmark

[Русский](benchmark.ru.md) · [Back to README](../README.md)

Measured on 2026-09-14 with Python 3.12.13, Django 5.2.17, openpyxl 3.1.5,
Linux x86-64 and SQLite on the development machine. Batch size: 500.

```bash
uv sync --dev --locked
uv run python scripts/benchmark_import.py --rows 10000 100000
```

The script generates an XLSX with five required columns and synthetic recipients,
creates an isolated temporary database, seeds one user, and invokes the actual
import service in a fresh subprocess for each measurement. It then re-imports the
same file. File generation, database setup and Django startup are outside elapsed
time; peak RSS covers the entire import subprocess. No delivery is requested.

| Rows | XLSX bytes | First import, s | Repeat, s | Peak RSS first / repeat, MiB | SQL first / repeat |
|---:|---:|---:|---:|---:|---:|
| 10,000 | 233,668 | 0.906 | 0.615 | 54.0 / 53.3 | 282 / 122 |
| 100,000 | 2,285,981 | 9.619 | 6.214 | 61.8 / 60.7 | 2,802 / 1,202 |

Both workloads created exactly the requested number of jobs. On repeat, every row
was counted as skipped: zero new jobs and zero erroneous rows. The script asserts
these invariants. SQL counts include transaction statements and membership inserts,
not just SELECTs; an automated query-budget test also checks that lookup count does
not grow per row inside one batch.

These are individual local measurements, not throughput guarantees or a comparative
benchmark. The workbook uses inline strings and short message bodies. Large shared
string tables, formatting, long bodies, slower disks or different databases change
memory/time substantially. Read-only openpyxl still loads shared strings and styles.
The measurements support bounded row processing on this workload, not constant
memory for arbitrary workbooks. Delivery keeps the required 5–20 second wait per
message and is deliberately excluded. The script requires Linux/macOS for `resource`.
