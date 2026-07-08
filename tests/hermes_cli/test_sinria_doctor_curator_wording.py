from pathlib import Path

import hermes_cli.curator as curator
import hermes_cli.doctor as doctor


def test_doctor_source_avoids_hardcoded_hermes_oauth_comment():
    source = Path(doctor.__file__).read_text(encoding="utf-8")
    assert "Native OAuth uses Hermes' own device-code flow" not in source
    assert "Native OAuth uses the CLI's own device-code flow" in source


def test_curator_source_avoids_hardcoded_hermes_subcommand_docstring():
    source = Path(curator.__file__).read_text(encoding="utf-8")
    assert 'CLI subcommand: `hermes curator <subcommand>`.' not in source
    assert 'CLI subcommand: `curator <subcommand>`.' in source
