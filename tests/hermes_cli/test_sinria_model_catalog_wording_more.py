from pathlib import Path

import hermes_cli.model_catalog as model_catalog


def test_model_catalog_source_avoids_hardcoded_product_docs_site_prose():
    source = Path(model_catalog.__file__).read_text(encoding="utf-8")
    assert "The Sinria docs site hosts" not in source
    assert "The project docs site hosts" in source
