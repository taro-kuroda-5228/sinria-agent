#!/usr/bin/env python3
"""Compatibility wrapper for the packaged legacy-correction migrator."""

from agent.correction_loop.migrate_legacy import main, migrate

__all__ = ["main", "migrate"]


if __name__ == "__main__":
    raise SystemExit(main())
