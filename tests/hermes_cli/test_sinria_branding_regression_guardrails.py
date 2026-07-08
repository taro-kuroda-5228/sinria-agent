from pathlib import Path

import hermes_cli.auth_commands as auth_commands
import hermes_cli.backup as backup
import hermes_cli.banner as banner
import hermes_cli.model_catalog as model_catalog
import hermes_cli.oneshot as oneshot
import hermes_cli.relaunch as relaunch


def test_selected_helper_modules_avoid_known_hardcoded_hermes_first_prose_regressions():
    checks = {
        Path(auth_commands.__file__): [
            "<hermes-root>/shared/nous_auth.json",
            "`hermes --profile <name>",
        ],
        Path(backup.__file__): [
            "Sinria/Sinria CLI",
            "`hermes backup` / `sinria backup`",
            "`hermes import` / `sinria import`",
            "hermes backup --quick",
            "relative to hermes root",
            "inside hermes root",
            "a hermes home would have",
            "a hermes dir name",
        ],
        Path(banner.__file__): [
            "HermesCLI state dependency",
            "nix-built hermes",
        ],
        Path(model_catalog.__file__): [
            "The Sinria docs site hosts",
            "``hermes model --refresh``",
        ],
        Path(oneshot.__file__): [
            "configured for \"cli\" in `hermes tools`.",
            "Model / provider selection mirrors `hermes chat`:",
            "hermes -z:",
        ],
        Path(relaunch.__file__): [
            "Find the hermes entry point.",
            "fresh hermes invocation.",
            "Hermes relaunch failed:",
            "re-run hermes.",
            "Common causes: ``hermes`` not on PATH yet",
        ],
    }

    for path, forbidden in checks.items():
        source = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in source, f"unexpected legacy wording in {path.name}: {needle}"
