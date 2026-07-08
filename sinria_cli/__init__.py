"""Sinria-native CLI package.

This package is the public entrypoint for Sinria. It delegates to the
legacy-compatible ``hermes_cli`` implementation while setting Sinria-native
runtime identity markers before any shared modules are imported.
"""

__all__ = ["main"]
