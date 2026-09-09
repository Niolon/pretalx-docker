"""Tests never deliver externally, even if a routing regression selects SMTP."""
import pytest


@pytest.fixture(autouse=True)
def no_external_smtp(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("External SMTP is forbidden in the recruitment test suite")
    monkeypatch.setattr("smtplib.SMTP.connect", blocked)
    monkeypatch.setattr("smtplib.SMTP_SSL.connect", blocked)


@pytest.fixture(scope="session")
def django_db_modify_db_settings():
    # Upstream skips migrations outside GitHub. These tests must exercise the
    # same schema/constraints locally and in CI, including RunPython indexes.
    from django.conf import settings
    settings.MIGRATION_MODULES = {}
