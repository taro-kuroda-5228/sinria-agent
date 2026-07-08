import json
from unittest.mock import MagicMock, patch


def test_image_generation_blocks_confidential_prompt_before_external_provider():
    from tools import image_generation_tool

    dispatch = MagicMock(return_value=json.dumps({"success": True, "image": "https://example.com/out.png"}))

    with patch("tools.image_generation_tool._dispatch_to_plugin_provider", dispatch):
        result = json.loads(image_generation_tool._handle_image_generate({
            "prompt": "社外秘の患者ID12345を含む説明画像を作成",
            "aspect_ratio": "square",
        }))

    dispatch.assert_not_called()
    assert result["success"] is False
    assert "Sinria external egress guard" in result["error"]
    serialized = json.dumps(result, ensure_ascii=False)
    assert "患者ID12345" not in serialized
