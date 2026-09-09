# Plugin tests

Run in a disposable Python 3.13+ environment against the upstream source revision
recorded by the repository's `pretalx` submodule (currently pretalx 2026.2.1).
Install upstream with its `dev` dependencies and the ARC plugin into that
environment. Do not use production database or media configuration.

From the repository root, after initialising the submodule:

```sh
python -m pip install -e './pretalx[dev]' -e ./arc/plugin
PRETALX_CONFIG_FILE="$PWD/pretalx/src/tests/ci_sqlite.cfg" \
PRETALX_DB_TYPE=sqlite3 \
PRETALX_DATA_DIR="$(mktemp -d)" \
PYTHONPATH="$PWD/pretalx/src" \
python -m pytest -c arc/plugin/pyproject.toml arc/plugin/tests
```

The suite uses an isolated test database and temporary document directories.
Download tests cover owners, unrelated applicants, event/team boundaries,
reviewer assignments and tracks, hidden questions, anonymous reviews, revoked
access, public-question flags, disabled workflows, temporary uploads, archives,
file deletion/replacement and transaction rollback. Re-run them whenever the
upstream base image changes: the plugin adapts upstream storage and cleanup hooks.

Also perform the proxy checks in `deployment/PRODUCTION.md` at deployment time;
Django tests cannot detect a hosting panel that publishes the private directory.

Privacy regressions also cover notice/intake gates, withdrawal and retention,
account isolation, mail ownership, stale retries, file-cleanup failures and strict
readiness. The two concurrent delivery/erasure tests require PostgreSQL and skip
on SQLite. CI runs on disposable PostgreSQL; external SMTP connections are
blocked for every test.

Real migrations run locally as well as in CI, so the single-application mail
indexes are exercised. Approval tests cover administrator-only actions, missing
metadata and immediate intake closure after policy edits. Bulk-mail regressions
cover two applicants and one person with two applications, withdrawal and manual
copies. Upload tests check all three required PDFs, renamed non-PDFs, incorrect
MIME types, extensions and the 10 MiB limit at both form and storage boundaries.
