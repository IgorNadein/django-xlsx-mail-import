"""Reproducible import-only benchmark; uses a temporary SQLite database and synthetic XLSX.

Run: uv run python scripts/benchmark_import.py --rows 10000 100000
Each measured import runs in a fresh process. Requires Linux/macOS (resource module).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def worker(mode: str, source: Path, batch_size: int) -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from django.db import connection

    from mailings.services import import_xlsx

    if mode == "setup":
        call_command("migrate", verbosity=0)
        get_user_model().objects.create_user(pk=1, username="benchmark")
        return
    queries = 0

    def count_sql(execute: Callable[..., Any], *args: Any) -> Any:
        nonlocal queries
        queries += 1
        return execute(*args)

    started = time.perf_counter()
    with connection.execute_wrapper(count_sql):
        _, stats = import_xlsx(source, batch_size=batch_size)
    elapsed = time.perf_counter() - started
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mib = peak / (1024 * 1024 if sys.platform == "darwin" else 1024)
    print(
        json.dumps(
            {"seconds": round(elapsed, 3), "peak_rss_mib": round(peak_mib, 1), "queries": queries, **asdict(stats)}
        )
    )


def benchmark(row_counts: list[int], batch_size: int) -> None:
    from openpyxl import Workbook

    print(json.dumps({"python": platform.python_version(), "platform": platform.platform(), "batch_size": batch_size}))
    for rows in row_counts:
        with tempfile.TemporaryDirectory(prefix="mail-import-benchmark-") as directory:
            source = Path(directory) / "benchmark.xlsx"
            workbook = Workbook(write_only=True)
            sheet = workbook.create_sheet()
            sheet.append(("external_id", "user_id", "email", "subject", "message"))
            for number in range(rows):
                sheet.append((f"bench-{number}", 1, "benchmark@example.com", f"Message {number}", "Synthetic content"))
            workbook.save(source)
            # Force an isolated SQLite database even when the caller uses PostgreSQL.
            env = {key: value for key, value in os.environ.items() if not key.startswith("POSTGRES_")}
            env["SQLITE_PATH"] = str(Path(directory) / "benchmark.sqlite3")

            def run(mode: str, source: Path = source, env: dict[str, str] = env) -> str:
                result = subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--worker",
                        mode,
                        "--source",
                        str(source),
                        "--batch-size",
                        str(batch_size),
                    ],
                    env=env,
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                return result.stdout

            run("setup")
            first = json.loads(run("import"))
            repeat = json.loads(run("import"))
            assert first["processed"] == first["created"] == rows
            assert repeat["processed"] == repeat["skipped"] == rows
            assert first["errors"] == repeat["errors"] == repeat["created"] == 0
            print(
                json.dumps({"rows": rows, "xlsx_bytes": source.stat().st_size, "first": first, "repeat": repeat}),
                flush=True,
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", nargs="+", type=int, default=[10000, 100000])
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--worker", choices=["setup", "import"], help=argparse.SUPPRESS)
    parser.add_argument("--source", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 1000 or any(count < 1 for count in args.rows):
        parser.error("rows must be positive; batch size must be between 1 and 1000")
    if args.worker:
        worker(args.worker, args.source, args.batch_size)
    else:
        benchmark(args.rows, args.batch_size)
