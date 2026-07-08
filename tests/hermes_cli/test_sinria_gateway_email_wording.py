from pathlib import Path

import hermes_cli.gateway as gateway


def test_gateway_email_help_avoids_hermes_specific_address_text():
    source = Path(gateway.__file__).read_text(encoding="utf-8")
    assert "The email address Hermes will use (e.g., hermes@gmail.com)." not in source
    assert "The email address {_gateway_product_name()} will use (e.g., agent@example.com)." in source
