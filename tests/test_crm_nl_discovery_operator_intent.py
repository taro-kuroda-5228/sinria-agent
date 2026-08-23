import importlib.util
import json
import re
from pathlib import Path

import pytest

plan_from_natural_language = pytest.importorskip(
    "scripts.sales_agent.discover_planner",
    reason="private sales workflow is not part of the sinria-agent distribution",
).plan_from_natural_language


def _load_daemon_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "sinria-sales-bridge-daemon-v2.py"
    spec = importlib.util.spec_from_file_location("sales_bridge_daemon_v2_for_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        from sinria_agentos_handlers import set_sales_outreach_runner

        # Unit tests import the daemon module only to exercise pure helper
        # functions. Import-time registry wiring must not leak into other tests and
        # turn the no-runner safety handler into a live DB-backed runner.
        set_sales_outreach_runner(None)
    except Exception:
        pass
    return mod


def test_visit_nursing_nl_query_generates_operator_queries():
    plan = plan_from_natural_language(nl_query="訪問看護事業を運営している事業者5社")

    assert plan.segment == "訪問看護/在宅医療"
    assert plan.queries[:3] == [
        "訪問看護ステーション 株式会社 公式",
        "訪問看護 運営会社 株式会社 公式",
        "訪問看護事業 会社 公式",
    ]
    assert not any("協会" in q or "財団" in q or "一覧" in q for q in plan.queries)


def test_yc_foreign_enterprise_request_does_not_search_yc_articles():
    plan = plan_from_natural_language(
        nl_query="Y combinatorにapplyするため、Sinriaが外部に売れるか試したい。外資企業と契約を取りたい。10件ピックアップして、営業文章も相手に合わせて作成して。"
    )

    assert plan.queries
    joined = "\n".join(plan.queries).lower()
    assert plan.segment in {"Healthcare AI", "医療DX", "ヘルスケアSaaS"}
    assert any("外資系" in q or "multinational" in q or "foreign" in q for q in plan.queries)
    assert any("digital health" in joined or "healthcare ai" in joined or "medical device" in joined for q in plan.queries)
    assert any("open innovation" in joined or "contact" in joined or "procurement" in joined for q in plan.queries)
    assert not any("y combinator" in q.lower() or "apply" in q.lower() for q in plan.queries)
    assert not any("一覧" in q or "まとめ" in q or "ランキング" in q for q in plan.queries)


def test_us_sinria_fit_request_generates_healthcare_ai_buyer_queries():
    plan = plan_from_natural_language(nl_query="アメリカ企業でSinriaに興味がありそうな会社を3つ")

    assert plan.segment == "Healthcare AI"
    joined = "\n".join(plan.queries).lower()
    assert "healthcare ai" in joined or "clinical documentation ai" in joined
    assert not any("top " in q.lower() or "directory" in q.lower() or "list" in q.lower() for q in plan.queries)


def test_us_healthcare_startup_request_generates_official_buyer_queries_not_ranking_pages():
    plan = plan_from_natural_language(nl_query="アメリカのヘルスケアスタートアップを3つ")

    assert plan.segment in {"Healthcare AI", "ヘルスケアSaaS", "Healthcare General"}
    joined = "\n".join(plan.queries).lower()
    assert "healthcare" in joined or "digital health" in joined or "medical" in joined
    assert any("official" in q.lower() or "company" in q.lower() or "demo" in q.lower() for q in plan.queries)
    assert not any("top " in q.lower() or "directory" in q.lower() or "list" in q.lower() or "ranking" in q.lower() for q in plan.queries)


def test_us_healthcare_ai_official_product_pages_are_sales_targets():
    daemon = _load_daemon_module()

    allowed = [
        (
            "Generative AI for Clinical Conversations | Abridge",
            "Abridge transforms clinical documentation with AI for clinicians and healthcare organizations.",
            "https://www.abridge.com/",
        ),
        (
            "HealthTalk A.I. in Action | Request a Personalized Demo",
            "Healthcare AI patient engagement platform for care teams and hospitals.",
            "https://www.healthtalkai.com/",
        ),
        (
            "Datavant | The Data Collaboration Platform Trusted for Healthcare",
            "Healthcare data platform used by life sciences, providers, payers, and clinical teams.",
            "https://www.datavant.com/",
        ),
    ]
    for title, snippet, url in allowed:
        assert daemon._is_company_url(url)
        assert daemon._looks_like_company_title(title)
        assert daemon._looks_like_business_operator(title, snippet, url)


def test_yc_foreign_enterprise_scoring_prefers_global_healthtech_buyers_over_ir_or_articles():
    daemon = _load_daemon_module()

    results = [
        {
            "title": "Pfizer Japan IRニュース",
            "snippet": "投資家向け決算説明と株主情報。",
            "url": "https://www.pfizer.co.jp/ir/news",
        },
        {
            "title": "外資系ヘルスケア企業ランキングまとめ | Example Media",
            "snippet": "外資系企業の年収ランキングと口コミまとめ。",
            "url": "https://media.example.jp/foreign-healthcare-ranking",
        },
        {
            "title": "GE HealthCare Japan - 会社概要・お問い合わせ",
            "snippet": "グローバル医療技術企業。日本法人で医療AI、病院DX、画像診断、ヘルスケアテクノロジーを提供。open innovation と問い合わせ窓口あり。",
            "url": "https://www.gehealthcare.co.jp/company/company",
        },
        {
            "title": "Medtronic Japan - Digital Health Partnerships",
            "snippet": "multinational medical device company with healthcare innovation, clinical workflow, hospital technology and Japan contact.",
            "url": "https://www.medtronic.com/jp-ja/about/contact.html",
        },
    ]

    ranked = daemon._rank_discovery_results_for_request(
        results,
        nl_query="Y combinatorにapplyするためSinriaが外部に売れるか試す。外資企業と契約したい。",
        query="foreign healthcare company Japan contact",
    )

    assert [r["title"] for r in ranked[:2]] == [
        "Medtronic Japan - Digital Health Partnerships",
        "GE HealthCare Japan - 会社概要・お問い合わせ",
    ]
    assert ranked[-1]["title"] == "外資系ヘルスケア企業ランキングまとめ | Example Media"
    assert ranked[0]["sinria_yc_fit_score"] > ranked[2]["sinria_yc_fit_score"]


def test_operator_filter_excludes_associations_and_public_lists():
    daemon = _load_daemon_module()

    blocked = [
        ("一般社団法人全国訪問看護事業協会", "", "https://www.zenhokan.or.jp/"),
        ("東京都訪問看護ステーション協会", "都内で活躍している訪問看護師を支援", "https://tokyohoukan-st.jp/"),
        ("介護サービス情報の公表について／千葉県", "指定事業者リスト", "https://www.pref.chiba.lg.jp/"),
        ("訪問看護の会社・企業一覧（全国）｜Baseconnect", "", "https://baseconnect.in/companies/example"),
    ]
    for title, snippet, url in blocked:
        assert not (
            daemon._is_company_url(url)
            and daemon._looks_like_company_title(title)
            and daemon._looks_like_business_operator(title, snippet, url)
        )

    allowed = [
        ("みんなのかかりつけ訪問看護ステーション 株式会社デザインケア", "訪問看護サービス", "https://kakaritsuke.co.jp/"),
        ("Sophiamedi - 訪問看護のソフィアメディ", "訪問看護サービス", "https://www.sophiamedi.co.jp/"),
        ("セントケア・ホールディング株式会社 - 質の高い介護サービス ...", "", "https://www.saint-care.com/"),
        (
            "GE HealthCare (Japan)",
            "GEヘルスケアの中核拠点。グローバル企業の強みと国内に有する開発、製造から販売、サービスまでの一貫した機能を活かす。",
            "https://www.gehealthcare.co.jp/company/company",
        ),
    ]
    for title, snippet, url in allowed:
        assert daemon._is_company_url(url)
        assert daemon._looks_like_company_title(title)
        assert daemon._looks_like_business_operator(title, snippet, url)


def test_company_name_extraction_skips_generic_page_titles():
    daemon = _load_daemon_module()

    assert daemon._shorten_company_name("会社概要 | ルミナスの和訪問看護ステーション（公式）") == "ルミナスの和訪問看護ステーション"
    assert daemon._shorten_company_name("事業所案内 | 訪問看護ステーションはな") == "訪問看護ステーションはな"


def test_discover_filter_excludes_sales_advice_article_pages():
    daemon = _load_daemon_module()

    title = "クリニックへの営業の特徴とは？営業方法やコツをご紹介"
    snippet = "クリニックへの営業を成功させるには、営業方法に拘る必要があります。この記事では営業方法について詳しく解説します。"
    url = "https://example-sales-media.jp/clinic-sales-tips"

    assert daemon._is_company_url(url)
    assert not daemon._looks_like_company_title(title)
    assert not daemon._looks_like_business_operator(title, snippet, url)
    assert not daemon._is_quality_sales_target_row({
        "company_name": title,
        "website": url,
        "company_metadata": {"snippet": snippet},
        "lead_metadata": {"discover_query": "地方 クリニック 公式 FDE"},
    })


def test_draft_quality_gate_blocks_healthcare_offer_for_non_healthcare_businesses():
    daemon = _load_daemon_module()

    polluted_rows = [
        {
            "company_name": "Second Line: 公式サイト",
            "website": "https://2ndl.co.jp/",
            "suggested_offer": "Healthcare Workflow Sprint",
            "company_metadata": {
                "snippet": "いつもセカンドラインの商品ならびに主催イベントをご愛顧いただきまして、誠にありがとうございます。開催会場を変更します。"
            },
            "lead_metadata": {"discover_query": "株式会社2nd 公式", "lead_board_eligible": True},
        },
        {
            "company_name": "second inc. 株式会社セカンド【公式】",
            "website": "https://second-inc.net/",
            "suggested_offer": "Healthcare Workflow Sprint",
            "company_metadata": {"snippet": "本社・倉庫が移転しました。ホームページがオープンしました。株式会社secondに法人化しました。"},
            "lead_metadata": {"discover_query": "株式会社2nd 公式", "lead_board_eligible": True},
        },
        {
            "company_name": "CLUB 2nd （クラブセカンド）",
            "website": "https://club-2nd.com/",
            "suggested_offer": "Healthcare Workflow Sprint",
            "company_metadata": {"snippet": "2nd公式LINEでは誌面では語りきれないモノの背景や数量限定の別注アイテム情報を編集部からお届けします。"},
            "lead_metadata": {"discover_query": "株式会社2nd 公式", "lead_board_eligible": True},
        },
        {
            "company_name": "Sharp",
            "website": "https://corporate.jp.sharp/",
            "industry": "healthcare",
            "suggested_offer": "Healthcare Workflow Sprint",
            "company_metadata": {"snippet": "シャープ株式会社の公式サイトです。会社情報、投資家情報、サステナビリティ、採用情報、ニュースリリースなどを掲載しています。"},
            "lead_metadata": {"discover_query": "株式会社 公式", "lead_board_eligible": True},
        },
    ]

    for row in polluted_rows:
        assert not daemon._is_quality_sales_target_row(row), row["company_name"]


def test_draft_quality_gate_allows_healthcare_relevant_sales_targets():
    daemon = _load_daemon_module()

    rows = [
        {
            "company_name": "GE HealthCare (Japan)",
            "website": "https://www.gehealthcare.co.jp/company/company",
            "suggested_offer": "Healthcare Workflow Sprint",
            "company_metadata": {
                "snippet": "グローバル医療技術企業として画像診断、病院DX、臨床ワークフロー改善、ヘルスケアテクノロジーを提供。"
            },
        },
        {
            "company_name": "みんなのかかりつけ訪問看護ステーション 株式会社デザインケア",
            "website": "https://kakaritsuke.co.jp/",
            "suggested_offer": "Healthcare Workflow Sprint",
            "company_metadata": {"snippet": "訪問看護サービスを提供する会社。看護記録や在宅医療の業務フロー改善余地がある。"},
        },
    ]

    for row in rows:
        assert daemon._is_quality_sales_target_row(row), row["company_name"]


def test_discover_filter_excludes_hospital_directory_and_recommendation_pages():
    daemon = _load_daemon_module()

    blocked = [
        (
            "東京都 心臓血管外科の病院・医院 60件【病院検索iタウン】",
            "東京都の心臓血管外科を標榜する病院・医院一覧です。",
            "https://medical.itp.ne.jp/byoin/tokyo/heart-surgery/",
        ),
        (
            "【2026年】大阪市の循環器内科 おすすめしたい8医院",
            "大阪市でおすすめの循環器内科を紹介します。医院選びの参考にしてください。",
            "https://medicaldoc.jp/clinic/osaka-cardiology-recommend/",
        ),
    ]

    for title, snippet, url in blocked:
        assert not (
            daemon._is_company_url(url)
            and daemon._looks_like_company_title(title)
            and daemon._looks_like_business_operator(title, snippet, url)
        )
        assert not daemon._is_quality_sales_target_row({
            "company_name": title,
            "website": url,
            "company_metadata": {"snippet": snippet},
            "lead_metadata": {"discover_query": "心臓血管外科 病院 東京"},
        })


def test_discover_filter_excludes_corporate_directory_review_and_generic_pages():
    daemon = _load_daemon_module()

    blocked = [
        (
            "株式会社メルセンヌ (東京都豊島区/未上場)の評判・口コミ",
            "株式会社メルセンヌ（本社東京都豊島区・未上場）の会社概要・住所・電話番号・業種・資本金を掲載。法人番号、インボイス登録番号、役員、グループ会社、決算公告まで無料で確認できる全国法人検索。",
            "https://houjin-search.example.jp/company/3010001192436",
        ),
        (
            "株式会社メルセンヌ (東京都豊島区)の企業情報",
            "法人番号、住所、電話番号、インボイス、決算公告などの企業データベースページ。",
            "https://corporate-db.example.jp/companies/3010001192436",
        ),
        ("Top 84 Digital Health Companies in Japan (2026) | ensun", "Company search result directory/list, not the buyer's official site.", "https://ensun.io/search/digital-health/japan"),
        ("Top 100 Healthcare Ai Companies in Japan (2026) | ensun", "Directory of healthcare AI companies.", "https://ensun.io/search/healthcare-ai/japan"),
        ("Top 100 medical and healthcare startups in USA 2026", "Ranking page of startups, not the buyer's official site.", "https://startup.example.com/top/medical-healthcare-startups-usa"),
        ("Top 50 Healthcare Startups in United States", "A startup list page rather than an operator page.", "https://directory.example.com/healthcare-startups-us"),
        ("Top Patient Engagement start-ups | VentureRadar", "Directory/list page for startups.", "https://www.ventureradar.com/startup/Patient%20Engagement"),
        ("35 Top Patient Engagement Companies in United States - F6S", "Company list/directory page.", "https://www.f6s.com/companies/patient-engagement/united-states/co"),
        ("Contact us", "お問い合わせフォームのみの汎用ページ", "https://example-healthcare.com/contact"),
        ("Corporate Profile", "会社概要ページの汎用タイトル", "https://example-healthcare.com/company/profile"),
    ]

    for title, snippet, url in blocked:
        assert not daemon._looks_like_company_title(title)
        assert not daemon._is_quality_sales_target_row({
            "company_name": title,
            "website": url,
            "company_metadata": {"snippet": snippet},
            "lead_metadata": {"discover_query": "外資系 医療機器 公式 会社概要"},
        })


def test_ai_draft_template_instruction_is_passed_to_local_generator_and_metadata(monkeypatch):
    daemon = _load_daemon_module()
    inserted: dict = {}

    lead_row = {
        "lead_id": "00000000-0000-0000-0000-000000000001",
        "lead_name": "GE HealthCare",
        "score": 80,
        "suggested_offer": "Y Combinator応募に向けた外資ヘルスケア企業との契約相談",
        "notes": "グローバル医療機器企業。日本法人あり。",
        "lead_segment": "外資系ヘルスケア",
        "lead_metadata": {
            "summary": "医療機器・ヘルスケアテクノロジーを提供するグローバル企業。",
            "requested_via": "crm_ui_user_instruction",
            "official_site_verified": True,
            "contact_route_summary": "contact_form",
        },
        "company_id": "10000000-0000-0000-0000-000000000001",
        "company_name": "GE HealthCare Japan",
        "website": "https://www.gehealthcare.co.jp/company/company",
        "industry": "healthcare",
        "company_segment": "外資系ヘルスケア",
        "company_metadata": {"snippet": "GEヘルスケアはグローバル医療技術企業。会社概要・お問い合わせ。"},
        "contact_name": "ご担当者",
        "contact_email": None,
        "contact_form_url": "https://www.gehealthcare.co.jp/contact",
    }

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=None):
            if "insert into outreach_drafts" in sql:
                inserted["params"] = params
                self._row = {"id": "20000000-0000-0000-0000-000000000001"}
            elif "select l.id as lead_id" in sql:
                self._row = lead_row
            else:
                self._row = None

        def fetchone(self):
            return self._row

    class Conn:
        def cursor(self):
            return Cursor()

    captured_kwargs: dict = {}

    def fake_generate(**kwargs):
        captured_kwargs.update(kwargs)
        return (
            "GE HealthCare Japan様との医療AI協業について",
            "LOCAL_LLM_BODY: GE HealthCare Japan様の医療技術事業と、Medical Horizonの医療現場AI実装を接続する具体相談です。",
            "local_sinria_bridge:test-model",
        )

    monkeypatch.setattr(daemon, "_generate_sales_draft_via_sinria_bridge", fake_generate)

    result = daemon._handle_ai_draft(
        conn=Conn(),
        task={"id": "task-1"},
        params={
            "leadId": lead_row["lead_id"],
            "instruction": "Y combinatorにapplyするため、外資企業と契約を取りたい。営業文章も相手に合わせて作成して。",
            "offerLabel": "YC応募に向けた外資企業との契約相談",
            "templateInstruction": "冒頭でMedical Horizonが何をしている会社かを1文で説明し、相手企業の医療技術事業に刺さる提案軸を必ず書く。",
            "requestedVia": "crm_ui_user_instruction",
        },
    )

    assert result["outcome"] == "completed"
    body = inserted["params"][3]
    metadata = json.loads(inserted["params"][6])
    assert captured_kwargs["suggested_offer"] == "YC応募に向けた外資企業との契約相談"
    assert "Y combinatorにapplyするため" in captured_kwargs["extra_context"]
    assert "テンプレ指示: 冒頭でMedical Horizonが何をしている会社か" in captured_kwargs["extra_context"]
    assert metadata["editable_template_instruction"].startswith("冒頭でMedical Horizon")
    assert "LOCAL_LLM_BODY" in body
    assert "貴院/貴社の取り組みを拝見し" not in body


def test_discover_candidate_must_be_legal_operator_not_article_source():
    daemon = _load_daemon_module()

    article_source = (
        "東京心臓血管外科病院の導入事例 | HealthTech Journal",
        "医療法人や病院の導入事例を紹介する第三者記事です。営業候補の参考情報としては使えるが、宛先法人ではありません。",
        "https://healthtech-journal.example.jp/cardiac-surgery-hospitals-case-study",
    )
    official_operator = (
        "医療法人社団ハートケア会 東京心臓血管外科病院 公式サイト",
        "病院概要、外来、医師紹介、お問い合わせ。運営法人: 医療法人社団ハートケア会。",
        "https://heartcare-hospital.or.jp/",
    )

    title, snippet, url = article_source
    assert not daemon._looks_like_business_operator(title, snippet, url)
    assert not daemon._is_quality_sales_target_row({
        "company_name": title,
        "website": url,
        "company_metadata": {"snippet": snippet, "source_role": "research_source_only"},
        "lead_metadata": {"discover_query": "東京 心臓血管外科 病院 医療法人 公式"},
    })

    title, snippet, url = official_operator
    assert daemon._looks_like_business_operator(title, snippet, url)
    assert daemon._is_quality_sales_target_row({
        "company_name": title,
        "website": url,
        "company_metadata": {"snippet": snippet, "candidate_entity_type": "legal_operator"},
        "lead_metadata": {"discover_query": "東京 心臓血管外科 病院 運営法人 公式"},
    })


def test_discover_metadata_adds_sanitized_provenance_keys_without_raw_body():
    """Task 1.2: lock that the discover handler stamps the sanitized provenance
    keys at the insert/update sites and constrains requested_via to the accepted
    set — by reading the source (matching the no-fallback-template test style)."""
    daemon_path = Path(__file__).resolve().parents[1] / "scripts" / "sinria-sales-bridge-daemon-v2.py"
    source = daemon_path.read_text()

    discover_section = source.split("def _handle_discover", 1)[1].split("def _handle_enrich", 1)[0]

    # requested_via is constrained to the two accepted values.
    assert "crm_ui_nl_search" in discover_section
    assert "crm_ui_user_instruction" in discover_section
    assert "_sales_agent_os_requested_via(requested_via_raw)" in discover_section
    assert "params.get(\"requested_via\")" in discover_section
    assert '"outcome": "blocked_invalid_requested_via"' in discover_section

    # Companies INSERT carries a sanitized source_reference (usage="discovery").
    assert '"source_reference": _source_reference_summary(' in discover_section
    # Leads INSERT carries requested_via + evidence_summary.
    assert '"requested_via": requested_via,' in discover_section
    assert '"evidence_summary":' in discover_section
    assert "pending contact verification" in discover_section
    # Source-only pages are retained as research_source_only references + returned.
    assert '"source_references": source_references,' in discover_section

    # Enrich UPDATE branches carry official_site_verified + contact_route_summary.
    enrich_section = source.split("def _handle_enrich", 1)[1].split("# Email patterns that should never", 1)[0]
    assert "'official_site_verified', true" in enrich_section
    assert "'official_site_verified', false" in enrich_section
    assert "'contact_route_summary'" in enrich_section

    # CRITICAL: the metadata PAYLOADS persisted to the DB must never carry raw
    # page body / html / full article text. We scope the check to the actual
    # json.dumps({...}) INSERT payloads and the jsonb_build_object(...) UPDATE
    # payloads (the internal getattr(fetched, "html", ...) entity-extraction read
    # is a legitimate pre-existing in-memory use and is never stored).
    insert_payloads = re.findall(r"json\.dumps\(\{(.*?)\}\)", discover_section, re.DOTALL)
    update_payloads = re.findall(r"jsonb_build_object\((.*?)\)\s*\n", enrich_section, re.DOTALL)
    assert insert_payloads, "expected to find json.dumps INSERT metadata payloads"
    persisted = "\n".join(insert_payloads + update_payloads)
    for forbidden in ("body_text", "raw_body", "page_body", "body_html", "full_text", '"html"', "'html'", "page_html"):
        assert forbidden not in persisted, f"raw body key '{forbidden}' must not be persisted in discover/enrich metadata"


def test_source_only_page_is_retained_as_source_reference_not_as_lead():
    """Task 1.2: a source-only ranking/article page must NOT become a lead, but it
    SHOULD be retainable as a sanitized `source_reference` summary so the UI can
    explain WHY follow-up leads exist — without storing any raw page body."""
    daemon = _load_daemon_module()

    title = "東京都の心臓血管外科おすすめ病院ランキング | HealthTech Journal"
    url = "https://healthtech-journal.example.jp/ranking/cardiac"
    snippet = "掲載医療機関: 医療法人社団ハートケア会 東京心臓血管外科病院 ほか。第三者ランキング記事。"

    # (a) It is NOT a quality sales target (stays out of leads).
    assert not daemon._is_quality_sales_target_row({
        "company_name": title,
        "website": url,
        "company_metadata": {"snippet": snippet, "source_role": "research_source_only"},
        "lead_metadata": {"discover_query": "東京 心臓血管外科 病院 公式"},
    })

    # (b) It CAN be retained as a sanitized source_reference summary.
    ref = daemon._source_reference_summary(
        title=title,
        url=url,
        usage="discovery_source",
        source_only=True,
    )
    assert isinstance(ref, dict)
    # Sanitized, bounded fields only.
    assert ref["type"] == "duckduckgo_snippet"
    assert ref["url"] == url
    assert ref["title"] and len(ref["title"]) <= 120
    assert ref["usage"] == "discovery_source"
    # Marked as source-only so it is never mistaken for a lead/company record.
    assert ref["source_role"] == "research_source_only"
    # CRITICAL: no raw page body / html / full article text / snippet body stored.
    forbidden_keys = {"body", "body_text", "html", "raw_body", "body_html", "snippet", "content", "text"}
    assert forbidden_keys.isdisjoint(ref.keys())
    # And no value leaks the raw snippet body.
    assert snippet not in "\n".join(str(v) for v in ref.values())


def test_source_reference_summary_for_company_discovery_is_sanitized():
    """A company-discovery source_reference uses the official URL + a bounded title
    and is tagged usage='discovery', source_only=False, with no raw body."""
    daemon = _load_daemon_module()

    long_title = "みんなのかかりつけ訪問看護ステーション 株式会社デザインケア " + ("あ" * 200)
    ref = daemon._source_reference_summary(
        title=long_title,
        url="https://kakaritsuke.co.jp/",
        usage="discovery",
        source_only=False,
    )
    assert ref["type"] == "duckduckgo_snippet"
    assert ref["url"] == "https://kakaritsuke.co.jp/"
    assert len(ref["title"]) <= 120  # truncated
    assert ref["usage"] == "discovery"
    # Not source-only ⇒ no research_source_only marker (it backs a real lead/company).
    assert ref.get("source_role") is None
    forbidden_keys = {"body", "body_text", "html", "raw_body", "body_html", "snippet", "content"}
    assert forbidden_keys.isdisjoint(ref.keys())


def test_source_only_article_can_spawn_official_candidate_followup_queries():
    daemon = _load_daemon_module()

    followups = daemon._candidate_followup_queries_from_source(
        title="東京都の心臓血管外科おすすめ病院",
        snippet="掲載医療機関: 医療法人社団ハートケア会 東京心臓血管外科病院、医療法人循環会 大阪循環器クリニック。",
        original_query="東京 心臓血管外科 病院 公式",
    )

    joined = "\n".join(followups)
    assert "医療法人社団ハートケア会" in joined
    assert "東京心臓血管外科病院" in joined
    assert "大阪循環器クリニック" in joined
    assert all(any(token in q for token in ("公式", "法人概要", "お問い合わせ")) for q in followups)
    assert not any("おすすめ" in q or "病院検索" in q for q in followups)


def test_source_only_page_body_can_spawn_official_candidate_followup_queries(monkeypatch):
    daemon = _load_daemon_module()

    class Fetched:
        html = """
        <html><head><title>地域医療DX導入事例</title></head>
        <body>
          <nav>ランキング おすすめ 病院検索</nav>
          <article>
            <h2>導入先医療機関</h2>
            <p>医療法人社団蒼心会 蒼心循環器病院では遠隔説明を開始。</p>
            <p>医療法人メディカルブリッジ 横浜ハートクリニックも家族説明を改善。</p>
          </article>
        </body></html>
        """

    monkeypatch.setattr(daemon.sa_discovery, "fetch_html", lambda url, timeout=4.0: Fetched())

    followups = daemon._candidate_followup_queries_from_source(
        title="循環器病院のDX導入事例 | HealthTech Journal",
        snippet="第三者記事です。本文に掲載医療機関があります。",
        original_query="東京 心臓血管外科 病院 公式",
        source_url="https://healthtech-journal.example.jp/case/cardiac-dx",
        fetch_source_body=True,
    )

    joined = "\n".join(followups)
    assert "医療法人社団蒼心会" in joined
    assert "蒼心循環器病院" in joined
    assert "医療法人メディカルブリッジ" in joined
    assert "横浜ハートクリニック" in joined
    assert not any("HealthTech" in q or "ランキング" in q or "病院検索" in q for q in followups)


def test_us_healthcare_startup_ranking_source_spawns_official_company_followup_queries(monkeypatch):
    daemon = _load_daemon_module()

    class Fetched:
        html = """
        <html><head><title>Top 100 medical and healthcare startups in USA 2026</title></head>
        <body>
          <article>
            <h1>Top 100 medical and healthcare startups in USA 2026</h1>
            <ol>
              <li><a href="/company/blossom-health">Blossom Health</a> - mental health telehealth startup.</li>
              <li><a href="/company/insight-health">Insight Health</a> - clinical AI agent platform for patient communication and clinical documentation.</li>
              <li><a href="/company/abridge">Abridge</a> - AI clinical documentation.</li>
              <li><a href="/company/suki">Suki</a> - AI assistant for clinicians.</li>
            </ol>
          </article>
        </body></html>
        """

    monkeypatch.setattr(daemon.sa_discovery, "fetch_html", lambda url, timeout=4.0: Fetched())

    followups = daemon._candidate_followup_queries_from_source(
        title="Top 100 medical and healthcare startups in USA 2026",
        snippet="Ranking page of startups, not the buyer's official site.",
        original_query="アメリカのヘルスケアスタートアップを3つ",
        source_url="https://www.medicalstartups.org/country/USA/",
        fetch_source_body=True,
    )

    joined = "\n".join(followups)
    assert "Blossom Health official" in joined
    assert "Insight Health official" in joined
    assert "Abridge official" in joined
    assert "Suki official" in joined
    assert not any("Top 100" in q or "medicalstartups" in q or "Ranking" in q for q in followups)


def test_clinical_institution_queries_are_official_site_biased_not_directory_biased():
    plan = plan_from_natural_language(nl_query="東京都の心臓血管外科の病院候補を5件")

    assert plan.segment == "病院"
    assert plan.queries
    assert all(any(token in q for token in ("公式", "病院概要", "医療法人", "運営法人", "法人概要", "お問い合わせ")) for q in plan.queries)
    assert any(any(token in q for token in ("医療法人", "運営法人", "法人概要")) for q in plan.queries)
    assert not any("一覧" in q or "おすすめ" in q or "病院検索" in q for q in plan.queries)
    assert not any(q == "心臓血管外科 病院 東京" for q in plan.queries)


def test_dynamic_clinical_discovery_queries_include_official_site_intent():
    from scripts.sales_agent.discover_planner import build_dynamic_queries

    plan = build_dynamic_queries(db_pivots={"industries": [], "areas": [], "suggested_offers": [], "lead_count": 0}, recent_history=[], target_count=6)

    assert plan.queries
    assert all(any(token in q for token in ("公式", "病院概要", "医療法人", "お問い合わせ")) for q in plan.queries[:6])
    assert not any(q == "心臓血管外科 クリニック 東京" for q in plan.queries)


def test_llm_query_prompts_require_official_site_not_directory_results():
    from scripts.sales_agent import discover_planner

    prompt_text = discover_planner._NL_SYSTEM + discover_planner._LLM_SYSTEM
    assert "公式サイト" in prompt_text
    assert "一覧" in prompt_text
    assert "おすすめ" in prompt_text
    assert "病院検索" in prompt_text


def test_outreach_plan_reports_only_displayable_drafts(monkeypatch):
    daemon = _load_daemon_module()

    def fake_discover(conn, task, params):
        return {
            "kind": "discover",
            "outcome": "completed",
            "leads_created": 2,
            "created_leads": [
                {"lead_id": "lead-1", "company_id": "company-1"},
                {"lead_id": "lead-2", "company_id": "company-2"},
            ],
        }

    def fake_enrich(conn, task, params):
        return {"kind": "enrich", "outcome": "completed", "leads_touched": 2, "with_contact_form": 2}

    def fake_ai_draft(conn, task, params):
        lead_id = params["leadId"]
        return {"kind": "ai_draft", "outcome": "completed", "lead_id": lead_id, "draft_id": lead_id.replace("lead", "draft")}

    monkeypatch.setattr(daemon, "_handle_discover", fake_discover)
    monkeypatch.setattr(daemon, "_handle_enrich", fake_enrich)
    monkeypatch.setattr(daemon, "_handle_ai_draft", fake_ai_draft)
    monkeypatch.setattr(daemon, "_visible_outreach_draft_ids", lambda conn, draft_ids: ["draft-1"])

    result = daemon._handle_outreach_plan(
        conn=object(),
        task={"id": "task-1"},
        params={"instruction": "地方クリニック向けFDE営業を2件", "maxTotal": 2},
    )

    assert result["drafts_created"] == 1
    assert result["draft_ids"] == ["draft-1"]
    assert result["hidden_draft_ids"] == ["draft-2"]
    assert "候補2件" in result["answer_summary"]
    assert "表示可能な承認待ち1件" in result["answer_summary"]


def test_outreach_plan_skips_non_displayable_leads_before_drafting(monkeypatch):
    daemon = _load_daemon_module()
    calls: list[tuple[str, dict]] = []

    def fake_discover(conn, task, params):
        calls.append(("discover", params))
        return {
            "kind": "discover",
            "outcome": "completed",
            "leads_created": 3,
            "created_leads": [
                {"lead_id": "lead-ir", "company_id": "company-ir"},
                {"lead_id": "lead-career", "company_id": "company-career"},
                {"lead_id": "lead-ok", "company_id": "company-ok"},
            ],
        }

    def fake_enrich(conn, task, params):
        calls.append(("enrich", params))
        return {"kind": "enrich", "outcome": "completed", "leads_touched": 3, "with_contact_form": 3}

    def fake_displayable(conn, lead_ids):
        calls.append(("displayable", {"leadIds": lead_ids}))
        return ["lead-ok"]

    def fake_ai_draft(conn, task, params):
        calls.append(("ai_draft", params))
        return {"kind": "ai_draft", "outcome": "completed", "lead_id": params["leadId"], "draft_id": "draft-ok"}

    monkeypatch.setattr(daemon, "_handle_discover", fake_discover)
    monkeypatch.setattr(daemon, "_handle_enrich", fake_enrich)
    monkeypatch.setattr(daemon, "_displayable_outreach_lead_ids", fake_displayable)
    monkeypatch.setattr(daemon, "_handle_ai_draft", fake_ai_draft)
    monkeypatch.setattr(daemon, "_visible_outreach_draft_ids", lambda conn, draft_ids: draft_ids)

    result = daemon._handle_outreach_plan(
        conn=object(),
        task={"id": "task-1"},
        params={"instruction": "外資企業向け営業を3件", "maxTotal": 3},
    )

    assert [params["leadId"] for name, params in calls if name == "ai_draft"] == ["lead-ok"]
    assert result["drafts_created"] == 1
    assert result["draft_ids"] == ["draft-ok"]
    assert result["hidden_draft_ids"] == []
    assert [b["lead_id"] for b in result["blocked"]] == ["lead-ir", "lead-career"]
    assert all(b["outcome"] == "blocked_not_displayable_after_enrich" for b in result["blocked"])
    assert "非表示/ブロック: 2件" in result["answer_summary"]


def test_outreach_plan_enriches_discovered_leads_before_drafting(monkeypatch):
    daemon = _load_daemon_module()
    calls: list[tuple[str, dict]] = []

    def fake_discover(conn, task, params):
        calls.append(("discover", params))
        return {
            "kind": "discover",
            "outcome": "completed",
            "leads_created": 1,
            "created_leads": [{"lead_id": "lead-1", "company_id": "company-1"}],
        }

    def fake_enrich(conn, task, params):
        calls.append(("enrich", params))
        return {
            "kind": "enrich",
            "outcome": "completed",
            "leads_touched": 1,
            "with_contact_form": 1,
        }

    def fake_ai_draft(conn, task, params):
        calls.append(("ai_draft", params))
        enrich_seen = any(name == "enrich" for name, _ in calls)
        return {
            "kind": "ai_draft",
            "outcome": "completed" if enrich_seen else "blocked_no_contact_method",
            "draft_id": "draft-1" if enrich_seen else None,
        }

    monkeypatch.setattr(daemon, "_handle_discover", fake_discover)
    monkeypatch.setattr(daemon, "_handle_enrich", fake_enrich)
    monkeypatch.setattr(daemon, "_handle_ai_draft", fake_ai_draft)

    result = daemon._handle_outreach_plan(
        conn=object(),
        task={"id": "task-1"},
        params={"instruction": "地方クリニック向けFDE営業を1件", "maxTotal": 1},
    )

    assert [name for name, _ in calls] == ["discover", "enrich", "ai_draft"]
    assert calls[1][1]["leadIds"] == ["lead-1"]
    assert calls[1][1]["onlyWithoutEmail"] is True
    assert calls[1][1]["limit"] == 1
    assert result["drafts_created"] == 1
    assert result["draft_ids"] == ["draft-1"]


def test_outreach_plan_searches_buffer_until_requested_visible_drafts(monkeypatch):
    daemon = _load_daemon_module()
    calls: list[tuple[str, dict]] = []

    def fake_discover(conn, task, params):
        calls.append(("discover", params))
        return {
            "kind": "discover",
            "outcome": "completed",
            "leads_created": 4,
            "created_leads": [
                {"lead_id": "lead-1", "company_id": "company-1"},
                {"lead_id": "lead-2", "company_id": "company-2"},
                {"lead_id": "lead-3", "company_id": "company-3"},
                {"lead_id": "lead-4", "company_id": "company-4"},
            ],
        }

    def fake_enrich(conn, task, params):
        calls.append(("enrich", params))
        return {"kind": "enrich", "outcome": "completed", "leads_touched": len(params["leadIds"])}

    def fake_ai_draft(conn, task, params):
        calls.append(("ai_draft", params))
        lead_id = params["leadId"]
        if lead_id == "lead-1":
            return {"kind": "ai_draft", "outcome": "blocked_no_contact_method"}
        return {"kind": "ai_draft", "outcome": "completed", "lead_id": lead_id, "draft_id": lead_id.replace("lead", "draft")}

    monkeypatch.setattr(daemon, "_handle_discover", fake_discover)
    monkeypatch.setattr(daemon, "_handle_enrich", fake_enrich)
    monkeypatch.setattr(daemon, "_handle_ai_draft", fake_ai_draft)

    result = daemon._handle_outreach_plan(
        conn=object(),
        task={"id": "task-1"},
        params={"instruction": "外資企業向け営業を2件", "maxTotal": 2},
    )

    assert calls[0][1]["maxTotal"] >= 12
    assert [params["leadId"] for name, params in calls if name == "ai_draft"] == ["lead-1", "lead-2", "lead-3"]
    assert result["drafts_created"] == 2
    assert result["draft_ids"] == ["draft-2", "draft-3"]


def test_outreach_draft_generation_never_inserts_fallback_templates():
    daemon_path = Path(__file__).resolve().parents[1] / "scripts" / "sinria-sales-bridge-daemon-v2.py"
    source = daemon_path.read_text()

    generate_section = source.split("# -------------------- generate_drafts --------------------", 1)[1].split("def _build_draft_for_lead", 1)[0]
    ai_draft_section = source.split("def _handle_ai_draft", 1)[1].split("# --------------------", 1)[0]

    assert "llm_generation_required_no_fallback_draft" in generate_section
    assert "continue\n        channel =" in generate_section
    assert "local-evidence-bound-rule" in ai_draft_section
    assert "user_instruction and business_summary and evidence" in ai_draft_section
    assert "draft-only and human-confirmation-required" in ai_draft_section


def test_sales_bridge_drafting_uses_local_sinria_bridge_not_vendor_key_loader():
    daemon_path = Path(__file__).resolve().parents[1] / "scripts" / "sinria-sales-bridge-daemon-v2.py"
    source = daemon_path.read_text()
    ai_draft_section = source.split("def _handle_ai_draft", 1)[1].split("# --------------------", 1)[0]
    generate_section = source.split("def _handle_generate_drafts", 1)[1].split("def _generate_sales_draft_via_sinria_bridge", 1)[0]

    assert "_load_anthropic_key" not in source
    assert "ANTHROPIC_API_KEY" not in ai_draft_section
    assert "ANTHROPIC_TOKEN" not in ai_draft_section
    assert "sa_llm" not in source
    assert "_generate_sales_draft_via_sinria_bridge" in ai_draft_section
    assert "local_sinria_bridge" in source
    assert "_load_sinria_local_llm_config()" in generate_section
    assert "unexpected keyword argument" not in generate_section
    assert "_build_draft_for_lead(lead=lead)" in generate_section


def test_local_sinria_bridge_config_refuses_remote_by_default(monkeypatch):
    daemon = _load_daemon_module()
    monkeypatch.setattr(daemon, "_parse_env_file", lambda path: {})
    monkeypatch.setenv("SINRIA_LOCAL_LLM_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("SINRIA_LOCAL_LLM_MODEL", "demo-local")
    monkeypatch.delenv("SINRIA_LOCAL_LLM_ALLOW_REMOTE", raising=False)

    assert daemon._load_sinria_local_llm_config() is None

    monkeypatch.setenv("SINRIA_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    cfg = daemon._load_sinria_local_llm_config()
    assert cfg is not None
    assert cfg["base_url"] == "http://127.0.0.1:11434/v1"
    assert cfg["model"] == "demo-local"


def test_autonomous_discover_does_not_write_sales_db_without_user_candidate_search():
    daemon = _load_daemon_module()

    class Conn:
        def cursor(self):  # pragma: no cover - should not be reached
            raise AssertionError("non Sales Agent OS candidate search must not touch DB")

    result = daemon._handle_discover(Conn(), {"id": "chatops_crm_auto_discover_test"}, {})

    assert result["outcome"] == "blocked_requires_sales_agent_os_candidate_search"
    assert result["leads_created"] == 0
    assert result["companies_created"] == 0
    assert result["external_action_performed"] is False

    explicit_result = daemon._handle_discover(
        Conn(),
        {"id": "chatops_crm_explicit_query_test"},
        {"queries": ["東京 医療法人 公式"]},
    )
    assert explicit_result["outcome"] == "blocked_requires_sales_agent_os_candidate_search"
    assert explicit_result["plan"]["has_explicit_queries"] is True
    assert explicit_result["leads_created"] == 0


def test_generate_drafts_only_uses_user_sales_agent_os_candidate_search_rows(monkeypatch):
    daemon = _load_daemon_module()
    monkeypatch.setattr(daemon, "_load_sinria_local_llm_config", lambda: {"base_url": "http://127.0.0.1:11434/v1", "model": "demo"})
    captured = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=None):
            captured["sql"] = sql
            captured["params"] = params

        def fetchall(self):
            return []

    class Conn:
        def cursor(self):
            return Cursor()

    result = daemon._handle_generate_drafts(Conn(), {"id": "task"}, {"limit": 5})

    assert result["drafts_created"] == 0
    sql = captured["sql"]
    assert "l.source = 'nl_discover'" in sql
    assert "metadata->>'requested_via'" in sql
    assert "crm_ui_nl_search" in sql
    assert "crm_ui_user_instruction" in sql
    assert "not like '%%smoke%%'" in sql

