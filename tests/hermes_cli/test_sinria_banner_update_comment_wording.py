from pathlib import Path

import hermes_cli.banner as banner


def test_banner_source_avoids_hardcoded_hermes_update_check_comments():
    source = Path(banner.__file__).read_text(encoding="utf-8")
    assert "nix-built hermes" not in source
    assert "nix-built CLI binary" in source
    assert "a nix-built CLI binary with no local git history to count against" in source
