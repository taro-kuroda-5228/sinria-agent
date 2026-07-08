"""Sinria product metadata must not expose Hermes branding.

Sinria is a standalone Medical Horizon product, not a Hermes-branded build.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _load_llms_generator():
    """Import website/scripts/generate-llms-txt.py as a module."""
    script = ROOT / "website" / "scripts" / "generate-llms-txt.py"
    spec = importlib.util.spec_from_file_location("generate_llms_txt", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_python_package_metadata_is_sinria_branded():
    project = _pyproject()["project"]

    assert project["name"] == "sinria-agent"
    assert "Sinria" in project["description"]
    assert "Hermes" not in project["description"]
    assert project["authors"] == [{"name": "Medical Horizon"}]



def test_python_console_scripts_are_sinria_branded():
    scripts = _pyproject()["project"]["scripts"]

    assert scripts == {
        "sinria": "sinria_cli.main:main",
        "sinria-agent": "run_agent:main",
        "sinria-acp": "acp_adapter.entry:main",
    }



def test_self_referencing_extras_use_sinria_agent_package_name():
    optional = _pyproject()["project"]["optional-dependencies"]
    offenders = [
        dep
        for deps in optional.values()
        for dep in deps
        if "hermes-agent[" in dep
    ]
    assert offenders == []



def test_node_package_metadata_is_sinria_branded():
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert package["name"] == "sinria-agent"
    assert "Sinria" in package["description"]
    assert "Hermes" not in json.dumps(package, ensure_ascii=False)
    assert "NousResearch" not in json.dumps(package, ensure_ascii=False)



def test_acp_registry_manifest_is_sinria_branded():
    manifest = json.loads((ROOT / "acp_registry" / "agent.json").read_text(encoding="utf-8"))

    assert manifest["id"] == "sinria-agent"
    assert manifest["name"] == "Sinria"
    assert "Sinria" in manifest["description"]
    assert manifest["repository"] == "https://github.com/taro-kuroda-5228/sinria-agent"
    assert manifest["website"] == "https://taro-kuroda-5228.github.io/sinria-agent/docs"
    assert manifest["authors"] == ["Medical Horizon"]
    assert manifest["distribution"]["uvx"]["args"] == ["sinria-acp"]
    serialized = json.dumps(manifest, ensure_ascii=False)
    assert "Hermes" not in serialized
    assert "hermes" not in serialized
    assert "NousResearch" not in serialized



def test_root_launcher_is_sinria_branded():
    launcher = ROOT / "sinria"
    assert launcher.exists()
    text = launcher.read_text(encoding="utf-8")
    assert "Sinria" in text
    assert "Hermes" not in text
    assert "HERMES_CLI_NAME" in text
    assert "sinria" in text


def test_homebrew_formula_and_install_cmd_are_sinria_branded():
    formula = (ROOT / "packaging" / "homebrew" / "sinria-agent.rb").read_text(encoding="utf-8")
    install_cmd = (ROOT / "scripts" / "install.cmd").read_text(encoding="utf-8")

    assert "taro-kuroda-5228/sinria-agent/releases/download" in formula
    assert "sinria_agent-0.6.0.tar.gz" in formula
    assert "%w[sinria sinria-agent sinria-acp]" in formula
    assert 'shell_output("#{bin}/sinria version")' in formula
    assert 'shell_output("#{bin}/sinria update 2>&1")' in formula
    assert "Hermes Agent Installer" not in install_cmd
    assert "NousResearch/hermes-agent" not in install_cmd
    assert "taro-kuroda-5228/sinria-agent" in install_cmd



def test_website_shell_metadata_is_sinria_branded():
    config = (ROOT / "website" / "docusaurus.config.ts").read_text(encoding="utf-8")
    skills_page = (ROOT / "website" / "src" / "pages" / "skills" / "index.tsx").read_text(encoding="utf-8")

    assert "title: 'Sinria Agent'" in config
    assert "Medical-Horizon" in config
    assert "github.com/taro-kuroda-5228/sinria-agent" in config
    assert "Sinria Agent" in skills_page
    assert "sinria skills install" in skills_page

    install_doc = (ROOT / "website" / "docs" / "getting-started" / "installation.md").read_text(encoding="utf-8")
    teams_doc = (ROOT / "website" / "docs" / "guides" / "operate-teams-meeting-pipeline.md").read_text(encoding="utf-8")

    assert "/usr/local/bin/sinria" in install_doc
    assert "~/.sinria/" in install_doc
    assert "/usr/local/lib/sinria-agent/" in install_doc
    assert "`sinria doctor`" in install_doc
    assert "raw.githubusercontent.com/taro-kuroda-5228/sinria-agent/main/scripts/install.ps1" in install_doc
    assert "%LOCALAPPDATA%\\sinria\\sinria-agent" in install_doc
    assert "pip install sinria-agent" in install_doc
    assert "Hermes Agent" not in install_doc
    assert "NousResearch/hermes-agent" not in install_doc
    assert "`hermes" not in install_doc
    assert "/usr/local/bin/hermes" not in install_doc
    assert "~/.hermes/" not in install_doc
    assert "ExecStart=/usr/local/bin/sinria teams-pipeline maintain-subscriptions" in teams_doc
    assert "/var/log/sinria/teams-pipeline-maintain.log" in teams_doc
    assert "~/.sinria/.env" in teams_doc
    assert "/usr/local/bin/hermes teams-pipeline maintain-subscriptions" not in teams_doc

    index_doc = (ROOT / "website" / "docs" / "index.md").read_text(encoding="utf-8")
    quickstart = (ROOT / "website" / "docs" / "getting-started" / "quickstart.md").read_text(encoding="utf-8")
    updating = (ROOT / "website" / "docs" / "getting-started" / "updating.md").read_text(encoding="utf-8")
    termux = (ROOT / "website" / "docs" / "getting-started" / "termux.md").read_text(encoding="utf-8")
    nix_setup = (ROOT / "website" / "docs" / "getting-started" / "nix-setup.md").read_text(encoding="utf-8")
    windows_native = (ROOT / "website" / "docs" / "user-guide" / "windows-native.md").read_text(encoding="utf-8")
    python_library = (ROOT / "website" / "docs" / "guides" / "python-library.md").read_text(encoding="utf-8")
    # website/static/llms-full.txt is a generated, .gitignore'd build artifact
    # (emitted by website/scripts/generate-llms-txt.py during `npm run build`
    # via prebuild.mjs). It is intentionally not committed, so generate it
    # in-memory here instead of depending on a prior build step.
    llms_full = _load_llms_generator().emit_llms_full()
    tui_doc = (ROOT / "website" / "docs" / "user-guide" / "tui.md").read_text(encoding="utf-8")
    windows_wsl = (ROOT / "website" / "docs" / "user-guide" / "windows-wsl-quickstart.md").read_text(encoding="utf-8")
    context_files = (ROOT / "website" / "docs" / "user-guide" / "features" / "context-files.md").read_text(encoding="utf-8")
    provider_routing = (ROOT / "website" / "docs" / "user-guide" / "features" / "provider-routing.md").read_text(encoding="utf-8")
    tools_doc = (ROOT / "website" / "docs" / "user-guide" / "features" / "tools.md").read_text(encoding="utf-8")
    api_server = (ROOT / "website" / "docs" / "user-guide" / "features" / "api-server.md").read_text(encoding="utf-8")
    subscription_proxy = (ROOT / "website" / "docs" / "user-guide" / "features" / "subscription-proxy.md").read_text(encoding="utf-8")
    credential_pools = (ROOT / "website" / "docs" / "user-guide" / "features" / "credential-pools.md").read_text(encoding="utf-8")
    personality = (ROOT / "website" / "docs" / "user-guide" / "features" / "personality.md").read_text(encoding="utf-8")
    automate = (ROOT / "website" / "docs" / "guides" / "automate-with-cron.md").read_text(encoding="utf-8")
    templates = (ROOT / "website" / "docs" / "guides" / "automation-templates.md").read_text(encoding="utf-8")
    contributor_audit = (ROOT / "scripts" / "contributor_audit.py").read_text(encoding="utf-8")

    assert "Sinria Agent Documentation" in index_doc
    assert "taro-kuroda-5228/sinria-agent" in index_doc
    assert "raw.githubusercontent.com/taro-kuroda-5228/sinria-agent/main/scripts/install.sh" in index_doc
    assert "raw.githubusercontent.com/taro-kuroda-5228/sinria-agent/main/scripts/install.ps1" in index_doc
    assert "sinria model" in quickstart
    assert "~/.sinria/.env" in quickstart
    assert "sinria config set" in quickstart
    assert "Medical Horizon Portal" in quickstart
    assert "Sinria Agent requires a model" in quickstart
    assert "https://github.com/taro-kuroda-5228/sinria-agent/releases" in updating
    assert "sinria gateway restart" in updating
    assert "git clone --recurse-submodules https://github.com/taro-kuroda-5228/sinria-agent.git" in termux
    assert "taro-kuroda-5228/sinria-agent" in automate
    assert "gh issue list --repo taro-kuroda-5228/sinria-agent" in automate
    assert "sinria cron create" in templates
    assert "sinria webhook subscribe" in templates
    assert "taro-kuroda-5228/sinria-agent" in templates
    assert '"--repo", "taro-kuroda-5228/sinria-agent"' in contributor_audit
    assert "github:taro-kuroda-5228/sinria-agent" in nix_setup
    assert "sinria setup" in nix_setup
    assert "sinria gateway install" in nix_setup
    assert "~/.sinria/" in nix_setup
    assert "raw.githubusercontent.com/taro-kuroda-5228/sinria-agent/main/scripts/install.ps1" in windows_native
    assert "github.com/taro-kuroda-5228/sinria-agent/issues" in windows_native
    assert "%LOCALAPPDATA%\\sinria" in windows_native
    assert "Using Sinria as a Python Library" in python_library
    assert "git+https://github.com/taro-kuroda-5228/sinria-agent.git" in python_library
    assert "sinria-agent @ git+https://github.com/taro-kuroda-5228/sinria-agent.git" in python_library
    assert "Launch the modern terminal UI for Sinria" in tui_doc
    assert "sinria --tui" in tui_doc
    assert "~/.sinria/.env" in tui_doc
    assert "Run Sinria Agent on Windows via WSL2" in windows_wsl
    assert "raw.githubusercontent.com/taro-kuroda-5228/sinria-agent/main/scripts/install.sh" in windows_wsl
    assert "sinria\n```" in windows_wsl
    assert ".sinria.md" in context_files
    assert "~/.sinria/SOUL.md" in context_files
    assert "~/.sinria/config.yaml" in provider_routing
    assert "Overview of Sinria Agent's tools" in tools_doc
    tips_py = (ROOT / "hermes_cli" / "tips.py").read_text(encoding="utf-8")
    assert "sinria tools" in tools_doc
    assert "~/.sinria/.env" in tools_doc
    assert "pip install 'sinria-agent[vercel]'" in tools_doc
    assert "Expose sinria-agent as an OpenAI-compatible API" in api_server
    assert "~/.sinria/.env" in api_server
    assert "sinria gateway" in api_server
    assert '"model": "sinria-agent"' in api_server
    assert "Medical Horizon subscription" in subscription_proxy
    assert "sinria proxy start" in subscription_proxy
    assert "~/.sinria/auth.json" in subscription_proxy
    assert "Sinria-4-70B" in subscription_proxy
    assert "Sinria automatically rotates" in credential_pools
    assert "sinria auth add openrouter" in credential_pools
    assert "sinria auth" in credential_pools
    assert "Customize Sinria Agent's personality" in personality
    assert "~/.sinria/SOUL.md" in personality
    assert "change Sinria's default personality" in personality
    assert "Context files (.sinria.md, .hermes.md, AGENTS.md)" in tips_py
    assert "project context from .sinria.md/.hermes.md" in tips_py
    assert ".sinria.md" in llms_full
    assert '"model": "sinria-agent"' in llms_full
    assert "pip install sinria-agent" in llms_full
    assert "raw.githubusercontent.com/taro-kuroda-5228/sinria-agent/main/scripts/install.ps1" in llms_full
    assert "%LOCALAPPDATA%\\sinria\\sinria-agent" in llms_full
    assert "https://github.com/taro-kuroda-5228/sinria-agent.git" in llms_full
    assert "github:taro-kuroda-5228/sinria-agent" in llms_full
    assert "./result/bin/sinria setup" in llms_full
    assert "nix profile install github:taro-kuroda-5228/sinria-agent\nsinria setup\nsinria chat" in llms_full
    assert "inputs.sinria-agent.url = \"github:taro-kuroda-5228/sinria-agent\";" in llms_full
    assert "sinria             # Start chatting!" in llms_full
    assert "sinria model          # Choose your LLM provider and model" in llms_full
    assert "sinria gateway setup  # Set up messaging platforms" in llms_full
    assert "~/.local/bin/sinria" in llms_full
    assert "/usr/local/bin/sinria" in llms_full
    assert "sinria doctor" in llms_full
    assert "sinria version" in llms_full
    assert "/path/to/sinria-agent" in llms_full
    assert "# Sinria Agent — Full Documentation" in llms_full
    assert "raw.githubusercontent.com/taro-kuroda-5228/sinria-agent/main/scripts/install.sh" in llms_full
    assert "raw.githubusercontent.com/taro-kuroda-5228/sinria-agent/main/scripts/install.ps1" in llms_full
    assert "%LOCALAPPDATA%\\sinria\\sinria-agent" in llms_full

    assert "NousResearch/hermes-agent" not in index_doc
    assert "NousResearch/hermes-agent" not in quickstart
    assert "NousResearch/hermes-agent" not in updating
    assert "NousResearch/hermes-agent" not in termux
    assert "NousResearch/hermes-agent" not in nix_setup
    assert "NousResearch/hermes-agent" not in windows_native
    assert "NousResearch/hermes-agent" not in python_library
    assert "NousResearch/hermes-agent" not in windows_wsl
    assert "NousResearch/hermes-agent" not in api_server

    assert "Hermes Agent" not in config
    assert "NousResearch" not in config
    assert "hermes skills install" not in skills_page
    assert (ROOT / "website" / "static" / "img" / "sinria-agent-banner.png").exists()


def test_chinese_readme_is_sinria_branded():
    readme = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

    assert "# Sinria" in readme
    assert "taro-kuroda-5228/sinria-agent" in readme
    assert "sinria model" in readme
    assert "~/.sinria/skills/openclaw-imports/" in readme

    assert "Hermes Agent" not in readme
    assert "hermes-agent" not in readme
    assert "NousResearch" not in readme
    assert "`hermes" not in readme


def test_llms_txt_generator_uses_sinria_public_identity():
    module = _load_llms_generator()

    index = module.emit_llms_index()
    full = module.emit_llms_full()

    assert index.startswith("# Sinria Agent\n")
    assert "Medical Horizon's independent AI agent platform" in index
    assert "https://taro-kuroda-5228.github.io/sinria-agent/docs" in index
    assert "https://github.com/taro-kuroda-5228/sinria-agent" in index
    assert "## Using Sinria" in index
    assert full.startswith("# Sinria Agent — Full Documentation\n")
    assert "Canonical site: https://taro-kuroda-5228.github.io/sinria-agent/docs" in full

    generated_header = "\n".join(index.splitlines()[:8])
    assert "Hermes Agent" not in generated_header
    assert "NousResearch" not in generated_header
    assert "hermes-agent.nousresearch.com" not in index


def test_reference_docs_use_sinria_public_commands_and_identity():
    mcp_reference = (ROOT / "website" / "docs" / "reference" / "mcp-config-reference.md").read_text(encoding="utf-8")
    toolsets_reference = (ROOT / "website" / "docs" / "reference" / "toolsets-reference.md").read_text(encoding="utf-8")
    tools_reference = (ROOT / "website" / "docs" / "reference" / "tools-reference.md").read_text(encoding="utf-8")
    faq_reference = (ROOT / "website" / "docs" / "reference" / "faq.md").read_text(encoding="utf-8")

    assert "description: \"Reference for Sinria MCP" in mcp_reference
    assert "Use MCP with Sinria" in mcp_reference
    assert "Sinria uses the MCP SDK" in mcp_reference
    assert "~/.sinria/mcp-tokens/<server>.json" in mcp_reference
    assert "description: \"Reference for Sinria core" in toolsets_reference
    assert "sinria chat --toolsets web,file,terminal" in toolsets_reference
    assert "sinria tools" in toolsets_reference
    assert "description: \"Authoritative reference for Sinria built-in tools" in tools_reference
    assert "This page documents Sinria's built-in tools" in tools_reference
    assert "`sinria tools` → Video Generation" in tools_reference
    assert "run `sinria spotify setup` once" in tools_reference
    assert "description: \"Frequently asked questions and solutions to common issues with Sinria Agent" in faq_reference
    assert "What LLM providers work with Sinria?" in faq_reference
    assert "curl -fsSL https://raw.githubusercontent.com/taro-kuroda-5228/sinria-agent/main/scripts/install.sh | bash" in faq_reference
    assert "Set your provider with `sinria model`" in faq_reference

    banner = (ROOT / "hermes_cli" / "banner.py").read_text(encoding="utf-8")
    main_py = (ROOT / "hermes_cli" / "main.py").read_text(encoding="utf-8")
    uninstall = (ROOT / "hermes_cli" / "uninstall.py").read_text(encoding="utf-8")

    assert "github.com/taro-kuroda-5228/sinria-agent.git" in banner
    assert "github.com/taro-kuroda-5228/sinria-agent/releases/tag" in banner
    assert "github.com/taro-kuroda-5228/sinria-agent/archive/refs/heads/" in main_py
    assert "git remote add upstream https://github.com/taro-kuroda-5228/sinria-agent.git" in main_py
    assert "raw.githubusercontent.com/taro-kuroda-5228/sinria-agent/main/scripts/install.sh" in main_py
    assert "raw.githubusercontent.com/taro-kuroda-5228/sinria-agent/main/scripts/install.ps1" in uninstall
    assert "raw.githubusercontent.com/taro-kuroda-5228/sinria-agent/main/scripts/install.sh" in uninstall

    assert "Reference for Hermes" not in mcp_reference
    assert "Use MCP with Hermes" not in mcp_reference
    assert "Hermes uses the MCP SDK" not in mcp_reference
    assert "Reference for Hermes" not in toolsets_reference
    assert "hermes chat --toolsets" not in toolsets_reference
    assert "`hermes tools`" not in toolsets_reference
    public_tools_preamble = tools_reference.split("## `browser` toolset", 1)[0]
    assert "Hermes" not in public_tools_preamble
    assert "`hermes tools` → Video Generation" not in tools_reference
    faq_intro = faq_reference.split("### Can I use it offline / with local models?", 1)[0]
    assert "Hermes Agent works" not in faq_intro
    assert "NousResearch/hermes-agent" not in faq_intro
    assert "`hermes model`" not in faq_intro


def test_model_catalog_public_surfaces_are_sinria_branded():
    reference = (ROOT / "website" / "docs" / "reference" / "model-catalog.md").read_text(encoding="utf-8")
    manifest = json.loads((ROOT / "website" / "static" / "api" / "model-catalog.json").read_text(encoding="utf-8"))

    assert "Sinria fetches curated model lists" in reference
    assert "sinria model" in reference
    assert "~/.sinria/cache/model_catalog.json" in reference
    assert manifest["metadata"]["source"] == "sinria-agent repo"
    assert manifest["metadata"]["docs"] == "https://taro-kuroda-5228.github.io/sinria-agent/docs/reference/model-catalog"
    assert "https://taro-kuroda-5228.github.io/sinria-agent/docs/api/model-catalog.json" in reference

    from hermes_cli import model_catalog

    assert model_catalog.DEFAULT_CATALOG_URL == "https://taro-kuroda-5228.github.io/sinria-agent/docs/api/model-catalog.json"

    public_intro = reference.split("## Updating the manifest", 1)[0]
    assert "Hermes" not in public_intro
    assert "hermes model" not in public_intro
    assert "hermes-agent.nousresearch.com" not in reference
    assert "sinria-agent.nousresearch.com" not in reference
    assert "hermes-agent" not in json.dumps(manifest.get("metadata", {}), ensure_ascii=False)


def test_skill_catalog_public_intros_are_sinria_branded():
    bundled = (ROOT / "website" / "docs" / "reference" / "skills-catalog.md").read_text(encoding="utf-8")
    optional = (ROOT / "website" / "docs" / "reference" / "optional-skills-catalog.md").read_text(encoding="utf-8")
    generator = (ROOT / "website" / "scripts" / "generate-skill-docs.py").read_text(encoding="utf-8")

    assert "Catalog of bundled skills that ship with Sinria Agent" in bundled
    assert "Sinria ships with a large built-in skill library" in bundled
    assert "`~/.sinria/skills/`" in bundled
    assert "Sinria also syncs bundled skills on `sinria update`" in bundled
    assert "Official optional skills shipped with Sinria Agent" in optional
    assert "sinria skills install official/<category>/<skill>" in optional
    assert "sinria skills uninstall <skill-name>" in optional

    intro_surfaces = "\n".join([
        bundled.split("## autonomous-ai-agents", 1)[0],
        optional.split("## autonomous-ai-agents", 1)[0],
        generator,
    ])
    assert "Catalog of bundled skills that ship with Hermes Agent" not in intro_surfaces
    assert "Hermes ships with a large built-in skill library" not in intro_surfaces
    assert "Hermes also syncs bundled skills" not in intro_surfaces
    assert "hermes skills install official/<category>/<skill>" not in intro_surfaces
    assert "hermes skills uninstall <skill-name>" not in intro_surfaces


def test_gateway_status_locale_headers_are_sinria_branded():
    locale_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "locales").glob("*.yaml")
    )

    assert "Sinria Gateway" in locale_text
    assert "Hermes Gateway" not in locale_text


def test_runtime_user_facing_copy_is_sinria_native():
    runtime_surfaces = "\n".join(
        (ROOT / rel).read_text(encoding="utf-8")
        for rel in [
            "cron/__init__.py",
            "cron/scheduler.py",
            "cron/jobs.py",
            "gateway/run.py",
            "gateway/platforms/discord.py",
            "gateway/platforms/base.py",
            "gateway/runtime_footer.py",
            "gateway/pairing.py",
            "gateway/sticker_cache.py",
            "gateway/channel_directory.py",
            "gateway/__init__.py",
            "hermes_logging.py",
            "cli.py",
            "mcp_serve.py",
        ]
    )

    assert "Sinria update finished" in runtime_surfaces
    assert "Gateway online — Sinria is back and ready" in runtime_surfaces
    assert "sinria gateway restart" in runtime_surfaces
    assert "~/.sinria" in runtime_surfaces

    forbidden_user_facing = [
        "Hermes update finished",
        "Hermes update failed",
        "Gateway online — Hermes is back and ready",
        "new Hermes chat",
        "normal Hermes chat",
        "Stop the running Hermes agent",
        "Re-scan ~/.hermes/skills/",
        "hermes gateway restart",
        "hermes gateway stop",
        "hermes gateway run --replace",
        "Hermes Agent CLI",
        "Welcome to Hermes Agent",
    ]
    for forbidden in forbidden_user_facing:
        assert forbidden not in runtime_surfaces


def test_sales_agent_os_readmes_use_consistent_name_layer():
    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    app_readme_path = ROOT / "apps" / "chatops-crm" / "README.md"
    if not app_readme_path.exists():
        # The public distribution snapshot excludes private/operational app workspaces.
        assert "ChatOps CRM reference app" not in root_readme
        assert "### ChatOps CRM" not in root_readme
        return
    app_readme = app_readme_path.read_text(encoding="utf-8")

    assert "Sinria Sales Agent OS / CRM Workspace" in root_readme
    assert "Standalone Sinria Sales Agent OS / CRM Workspace" in app_readme
    assert "ChatOps CRM reference app" not in root_readme
    assert "### ChatOps CRM" not in root_readme


def test_high_frequency_optional_skill_setup_paths_are_sinria_native():
    optional_paths = [
        ROOT / "optional-skills" / "productivity" / "siyuan" / "SKILL.md",
        ROOT / "optional-skills" / "productivity" / "telephony" / "SKILL.md",
        ROOT / "optional-skills" / "productivity" / "canvas" / "SKILL.md",
        ROOT / "optional-skills" / "email" / "agentmail" / "SKILL.md",
        ROOT / "optional-skills" / "autonomous-ai-agents" / "honcho" / "SKILL.md",
        ROOT / "optional-skills" / "productivity" / "memento-flashcards" / "SKILL.md",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in optional_paths)

    assert "~/.sinria" in combined
    assert "sinria skills install" in combined
    assert "sinria honcho setup" in combined
    assert "sinria --toolsets mcp" in combined

    forbidden = [
        "add to `~/.hermes",
        "Add to `~/.hermes",
        "Store it in `~/.hermes",
        "Save to `~/.hermes",
        "python3 ~/.hermes/skills",
        "hermes skills install",
        "hermes honcho",
        "hermes --toolsets",
        "Hermes Agent",
    ]
    for token in forbidden:
        assert token not in combined
