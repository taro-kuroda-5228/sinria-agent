"""Live egress guard: rows that crossed into the company_os schema must be
metadata-only — no email addresses, no long free text, raw flags pinned false.

Skips cleanly when the live DB env is not configured (CI-safe).

Note on conftest interaction:
  COMPANY_OS_DATABASE_URL does NOT match any _CREDENTIAL_SUFFIXES or
  _CREDENTIAL_NAMES in tests/conftest.py (those patterns cover *_TOKEN,
  *_API_KEY, *_SECRET, *_PASSWORD, etc.). Therefore this env var is NOT
  blanked by the autouse _hermetic_environment fixture, and setting it in
  your shell before running the tests is sufficient to trigger live execution:

      export COMPANY_OS_DATABASE_URL="postgresql://..."
      bash scripts/run_tests.sh tests/test_live_egress_guard.py

  Direct-Postgres password for the Supabase project was not saved at
  provisioning time (see memory: company-os-new-db-direct-pg-password-not-persisted).
  Reset the password in the Supabase dashboard before performing a live run.
  Live execution is intended to be performed by Taro in Task 9 only.

Design notes:
  * READ-ONLY queries only — no INSERT/UPDATE/DELETE anywhere in this file.
  * Connection failure when env IS set (unreachable DB) surfaces as pytest.fail,
    not skip. A live-but-unreachable DB must not silently green the safety gate.
  * workspace_members.email is the only column in the company_os schema that
    legitimately holds an email address. It is a separate table from the two
    tables tested here (agent_os_projections, audit_logs), and neither of those
    tables has any email-address column by design.
"""
import json
import os
import re

import pytest

DATABASE_URL = os.environ.get("COMPANY_OS_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="COMPANY_OS_DATABASE_URL not configured (live check only)"
)

EMAIL_RX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Projected rows contain short enumerated values (health, status, display_name,
# etc.) and string arrays (next_actions, risks) with brief action phrases.
# 500 chars per token is generous; a raw email body or raw context would
# easily exceed this.
MAX_TEXT_LEN = 500


def _fetch_rows(query: str) -> list[dict]:
    """Execute a read-only SELECT and return rows as dicts.

    Raises pytest.fail (not skip) on OperationalError so that a reachable-but-
    credential-incorrect URL does not silently pass — the safety gate must fail
    loudly when the environment is set but the connection cannot be established.
    """
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        pytest.fail(
            f"psycopg is required for live egress guard tests but is not installed: {exc}. "
            "Install it with: pip install psycopg[binary]"
        )

    try:
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                return cur.fetchall()
    except Exception as exc:
        pytest.fail(
            f"Live DB connection failed: {type(exc).__name__}: {exc}. "
            "Check COMPANY_OS_DATABASE_URL and that the direct-Postgres password has been reset. "
            "(URL itself is not printed for security.)"
        )


def _assert_metadata_only(value: object, where: str) -> None:
    """Assert that `value` contains no email addresses and no oversized text tokens.

    `json.dumps` is used to flatten nested structures (dicts, lists, arrays)
    into a single string before scanning. Each '"'-delimited fragment is
    checked separately for length so that a concatenation of many short values
    does not hide a single oversized token.
    """
    text = json.dumps(value, ensure_ascii=False, default=str)
    assert not EMAIL_RX.search(text), (
        f"Email address leaked into {where}. "
        "company_os.agent_os_projections and company_os.audit_logs must be "
        "metadata-only; no email address column exists in those tables by design."
    )
    for fragment in text.split('"'):
        assert len(fragment) <= MAX_TEXT_LEN, (
            f"Oversized free-text fragment ({len(fragment)} chars) in {where}. "
            f"Fragment starts with: {fragment[:120]!r}"
        )


def test_sales_projection_rows_are_metadata_only():
    """agent_os_projections rows for the sales agent must be metadata-only.

    Checks:
    - No email address in any column.
    - No oversized free-text token (would indicate raw context bleed).
    - raw_source_body_stored is not True (schema CHECK guarantees this,
      but we verify at the row level as belt-and-suspenders).

    Table: company_os.agent_os_projections
    Relevant columns confirmed in company_os_schema.sql:
      projection_id, workspace_id, agent_os_id, display_name,
      source_state_version, fetched_at, projection_freshness, health,
      progress_percent, review_required_count, blocked_count,
      next_actions (text[]), risks (text[]),
      raw_source_body_stored (boolean, pinned false by CHECK),
      credential_stored_in_cloud (boolean, pinned false by CHECK),
      external_action_performed (boolean, pinned false by CHECK).
    """
    rows = _fetch_rows(
        "select * from company_os.agent_os_projections "
        "where agent_os_id = 'sales' order by fetched_at desc limit 5"
    )
    # Production was activated on 2026-06-11 (daemon KPI push live), so an
    # empty result means the guard is scanning the wrong place (e.g. a renamed
    # agent_os_id) — fail loudly instead of passing vacuously.
    assert rows, "no sales projection rows — guard would pass vacuously; check agent_os_id/table"
    for row in rows:
        _assert_metadata_only(row, "company_os.agent_os_projections")
        # Verify that the CHECK-constrained safety booleans are actually false
        # in real data. The schema enforces this at write time; we verify here
        # so a schema migration that accidentally removed the CHECK would be
        # caught at the next live run.
        assert row.get("raw_source_body_stored") is not True, (
            "raw_source_body_stored must be pinned false in agent_os_projections"
        )
        assert row.get("credential_stored_in_cloud") is not True, (
            "credential_stored_in_cloud must be pinned false in agent_os_projections"
        )


def test_recent_audit_summaries_are_metadata_only():
    """Recent audit_logs rows must be metadata-only.

    Checks:
    - sanitized_summary and the full row contain no email addresses.
    - No oversized free-text token.
    - raw_payload_stored is not True.

    Table: company_os.audit_logs
    Relevant columns confirmed in company_os_schema.sql:
      audit_id, workspace_id, at (NOT created_at — actual column is 'at'),
      actor_member_id, actor_instance_id, event (enum),
      sanitized_summary, raw_payload_stored (boolean, pinned false by CHECK),
      external_action_performed (boolean, pinned false by CHECK).

    Note: 'at' is the timestamp column name, not 'created_at'.
    """
    rows = _fetch_rows(
        "select * from company_os.audit_logs "
        "order by at desc limit 50"
    )
    for row in rows:
        _assert_metadata_only(row, "company_os.audit_logs")
        assert row.get("raw_payload_stored") is not True, (
            "raw_payload_stored must be pinned false in audit_logs"
        )
