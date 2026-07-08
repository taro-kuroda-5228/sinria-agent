import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_web_crawl_blocks_confidential_url_before_provider_call():
    from tools import web_tools

    fake_provider = MagicMock()
    fake_provider.supports_crawl.return_value = True
    fake_provider.is_available.return_value = True
    fake_provider.name = "fake"
    fake_provider.crawl.return_value = {"results": []}

    with patch("tools.web_tools._get_backend", return_value="fake"), \
         patch("agent.web_search_registry.get_provider", return_value=fake_provider):
        result = json.loads(await web_tools.web_crawl_tool("https://example.com/?q=患者ID12345社外秘"))

    fake_provider.crawl.assert_not_called()
    assert "error" in result
    assert "Sinria external egress guard" in result["error"]
    assert "患者ID12345" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.asyncio
async def test_web_crawl_blocks_confidential_instructions_before_provider_call():
    from tools import web_tools

    fake_provider = MagicMock()
    fake_provider.supports_crawl.return_value = True
    fake_provider.is_available.return_value = True
    fake_provider.name = "fake"
    fake_provider.crawl.return_value = {"results": []}

    with patch("tools.web_tools._get_backend", return_value="fake"), \
         patch("agent.web_search_registry.get_provider", return_value=fake_provider):
        result = json.loads(await web_tools.web_crawl_tool(
            "https://example.com/",
            instructions="社外秘の契約書から token=secret-value を抽出",
        ))

    fake_provider.crawl.assert_not_called()
    assert "error" in result
    assert "Sinria external egress guard" in result["error"]
    assert "secret-value" not in json.dumps(result, ensure_ascii=False)
