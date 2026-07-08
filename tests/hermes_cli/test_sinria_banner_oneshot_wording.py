from pathlib import Path

import hermes_cli.banner as banner
import hermes_cli.oneshot as oneshot


def test_banner_source_avoids_hardcoded_hermescli_state_wording():
    source = Path(banner.__file__).read_text(encoding="utf-8")
    assert "Pure display functions with no HermesCLI state dependency." not in source
    assert "Pure display functions with no main-CLI state dependency." in source


def test_oneshot_source_avoids_hardcoded_hermescli_init_agent_wording():
    source = Path(oneshot.__file__).read_text(encoding="utf-8")
    assert "Oneshot bypasses ``HermesCLI._init_agent()``" not in source
    assert "Oneshot bypasses the main CLI's ``_init_agent()`` path" in source
