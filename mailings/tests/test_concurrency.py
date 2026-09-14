from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections

from mailings.delivery import deliver_import_run
from mailings.models import ImportEntry, MailingRecord
from mailings.services import import_xlsx
from mailings.tests.test_import_command import make_workbook

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Concurrent writers need PostgreSQL"),
]


def test_competing_imports_keep_unique_ids_and_correct_counts(tmp_path):
    users = [get_user_model().objects.create_user(username=f"race-{i}") for i in range(2)]
    paths = [tmp_path / f"race-{i}.xlsx" for i in range(2)]
    for i, path in enumerate(paths):
        keys = ["alpha", "beta"] if i == 0 else ["beta", "alpha"]
        make_workbook(path, [(key, users[i].pk, "race@example.com", "Same ID", "First writer wins") for key in keys])
    barrier = Barrier(2)

    def submit(path):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            _, stats = import_xlsx(path)
            return stats.created, stats.skipped
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, paths, timeout=20))
    assert sorted(results) == [(0, 2), (2, 0)]
    assert MailingRecord.objects.count() == 2
    assert ImportEntry.objects.count() == 4


def test_two_workers_cannot_send_same_record(tmp_path):
    user = get_user_model().objects.create_user(username="worker")
    path = tmp_path / "workers.xlsx"
    make_workbook(path, [("once", user.pk, "race@example.com", "Once", "Body")])
    run, _ = import_xlsx(path)
    second_run, _ = import_xlsx(path)
    started, release = Event(), Event()

    def fake_send(*args):
        assert not connection.in_atomic_block, "Do not hold a transaction over external I/O"
        started.set()
        assert release.wait(timeout=10)

    def worker(owner):
        close_old_connections()
        try:
            return deliver_import_run(owner)
        finally:
            connections.close_all()

    with (
        patch("mailings.delivery.send_email", side_effect=fake_send) as send,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        first = pool.submit(worker, run)
        try:
            assert started.wait(timeout=10)
            second = pool.submit(worker, second_run)
            assert second.result(timeout=10).attempted == 0
        finally:
            release.set()
        assert first.result(timeout=10).sent == 1
        assert send.call_count == 1
    record = MailingRecord.objects.get()
    assert record.status == "sent"
    assert record.attempts == 1


def test_parallel_workers_can_process_different_records(tmp_path):
    user = get_user_model().objects.create_user(username="parallel")
    path = tmp_path / "parallel.xlsx"
    make_workbook(path, [(f"id-{i}", user.pk, "p@example.com", f"Subject {i}", "Body") for i in range(2)])
    run, _ = import_xlsx(path)
    barrier = Barrier(2)

    def send_in_parallel(*args):
        assert not connection.in_atomic_block
        barrier.wait(timeout=10)

    def worker():
        close_old_connections()
        try:
            return deliver_import_run(run)
        finally:
            connections.close_all()

    with patch("mailings.delivery.send_email", side_effect=send_in_parallel), ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(worker) for _ in range(2)]
        assert sum(job.result(timeout=20).sent for job in jobs) == 2
    assert MailingRecord.objects.filter(status="sent").count() == 2
