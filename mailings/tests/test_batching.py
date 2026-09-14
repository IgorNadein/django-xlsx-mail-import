import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext

from mailings.services import import_xlsx
from mailings.tests.test_import_command import make_workbook

pytestmark = pytest.mark.django_db


def test_database_reads_scale_per_batch_not_per_row(tmp_path):
    user = get_user_model().objects.create_user(username="batch")
    read_counts = []
    for length in (10, 100):
        path = tmp_path / f"batch-{length}.xlsx"
        make_workbook(path, [(f"id-{length}-{i}", user.pk, "b@example.com", "Subject", "Body") for i in range(length)])
        with CaptureQueriesContext(connection) as queries:
            run, stats = import_xlsx(path, batch_size=100)
        read_counts.append(sum(q["sql"].startswith("SELECT") for q in queries))
        assert stats.created == length
        assert run.processed_count == stats.created + stats.skipped + stats.errors
    assert read_counts == [3, 3]


@pytest.mark.parametrize("batch_size", [0, -1, 1001])
def test_rejects_unbounded_batch_size(tmp_path, batch_size):
    from mailings.services import ImportFileError

    path = tmp_path / "batch.xlsx"
    make_workbook(path, [])
    with pytest.raises(ImportFileError, match="between 1 and 1000"):
        import_xlsx(path, batch_size=batch_size)
