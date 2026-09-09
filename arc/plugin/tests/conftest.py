"""Tests never deliver externally, even if a routing regression selects SMTP."""
import pytest


@pytest.fixture(autouse=True)
def no_external_smtp(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("External SMTP is forbidden in the recruitment test suite")
    monkeypatch.setattr("smtplib.SMTP.connect", blocked)
    monkeypatch.setattr("smtplib.SMTP_SSL.connect", blocked)
