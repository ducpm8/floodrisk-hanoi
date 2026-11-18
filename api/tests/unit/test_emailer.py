"""Unit tests for the admin alert emailer — SMTP is always mocked."""

import logging

import pytest

from api.config import settings
from api.utils import emailer


def test_no_host_logs_only(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(settings, "email_smtp_host", "")
    monkeypatch.setattr(settings, "admin_alert_email", "ops@example.com")

    with caplog.at_level(logging.ERROR):
        sent = emailer.send_admin_alert("subject", "body")

    assert sent is False
    assert "ADMIN ALERT (email not configured)" in caplog.text
    assert "body" in caplog.text


def test_sends_email_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "email_smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "email_smtp_user", "alerts@example.com")
    monkeypatch.setattr(settings, "email_smtp_pass", "secret")
    monkeypatch.setattr(settings, "admin_alert_email", "ops@example.com")

    captured: list[object] = []
    monkeypatch.setattr(emailer, "_send_smtp", captured.append)

    sent = emailer.send_admin_alert("session expired", "group 999")

    assert sent is True
    assert len(captured) == 1
    message = captured[0]
    assert message["To"] == "ops@example.com"
    assert message["Subject"] == "[FloodRisk] session expired"
    assert "group 999" in message.get_content()


def test_smtp_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "email_smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "admin_alert_email", "ops@example.com")

    def boom(_message: object) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(emailer, "_send_smtp", boom)

    assert emailer.send_admin_alert("subject", "body") is False
