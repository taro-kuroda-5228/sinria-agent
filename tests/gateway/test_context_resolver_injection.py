
from gateway.session import SessionContext, SessionSource, Platform, build_session_context_prompt


def test_gateway_session_prompt_includes_context_resolver_guidance_for_discord_sinria_requests():
    context = SessionContext(
        source=SessionSource(
            platform=Platform.DISCORD,
            user_id="user-1",
            user_name="Taro Kuroda",
            chat_id="channel-1",
            chat_name="sinria-sidework-2",
            chat_type="channel",
        ),
        connected_platforms=[Platform.DISCORD],
        home_channels={},
    )

    prompt = build_session_context_prompt(context, current_user_message="Sinriaのコンテキストシェアが弱い")

    assert "Context Share Resolver" in prompt
    assert "prior corrections" in prompt
    assert "Sinria-native" in prompt
    assert "metadata-only" in prompt


def test_gateway_session_prompt_includes_project_source_lock_for_project_actions():
    context = SessionContext(
        source=SessionSource(
            platform=Platform.DISCORD,
            user_id="user-1",
            user_name="Taro Kuroda",
            chat_id="channel-1",
            chat_name="sinria-sidework-2",
            chat_type="channel",
        ),
        connected_platforms=[Platform.DISCORD],
        home_channels={},
    )

    prompt = build_session_context_prompt(context, current_user_message="MedSpotのUIをmockupに合わせて実装して")

    assert "Project Source-Lock Gate" in prompt
    assert "/Users/tarokuroda/projects/medspot" in prompt
    assert "medspot-mvp-spec-v0.md" in prompt
    assert "Save to local files only (" in prompt
    assert "/cron/output/)" in prompt
    assert "display_hermes_home" not in prompt
    assert "~/.hermes" not in prompt


def test_gateway_session_prompt_uses_current_reply_body_for_medevidence_source_lock():
    context = SessionContext(
        source=SessionSource(
            platform=Platform.DISCORD,
            user_id="user-1",
            user_name="Taro Kuroda",
            chat_id="channel-1",
            chat_name="sinria",
            chat_type="channel",
        ),
        connected_platforms=[Platform.DISCORD],
        home_channels={},
    )

    current_user_message = (
        '[Replying to: "Cloud Run proxy は終了済み"]\n\n'
        "[Taro Kuroda] メドエビデンスレポジトリのconflictを解消してmainにマージして"
    )
    prompt = build_session_context_prompt(context, current_user_message=current_user_message)

    assert "MedEvidence GCP implementation lane: /Users/tarokuroda/medevidence-gcp." in prompt
    assert "MedEvidence Vercel/LTS baseline: /Users/tarokuroda/med_evi-2" in prompt
    assert "Do not substitute MedSpot, Company OS, Sales Agent OS, or Sinria core" in prompt
    assert "Cloud Run proxy は終了済み" not in prompt
