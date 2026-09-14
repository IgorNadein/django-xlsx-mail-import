import logging
from unittest.mock import patch

from mailings.services import send_email


def test_send_email_waits_between_five_and_twenty_seconds(caplog) -> None:
    with (
        patch("mailings.services.randint", return_value=11) as mocked_randint,
        patch("mailings.services.sleep") as mocked_sleep,
        caplog.at_level(logging.INFO, logger="mailings.delivery"),
    ):
        send_email("alice@example.com", "Subject", "Message")

    mocked_randint.assert_called_once_with(5, 20)
    mocked_sleep.assert_called_once_with(11)
    assert "Send EMAIL" in caplog.text
    assert "alice@example.com" in caplog.text
