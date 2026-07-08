from pathlib import Path

import hermes_cli.plugins_cmd as plugins_cmd
import hermes_cli.profiles as profiles


def test_plugins_cmd_source_avoids_user_env_wording():
    source = Path(plugins_cmd.__file__).read_text(encoding="utf-8")
    assert "Values are saved to the user's ``.env``." not in source
    assert "Values are saved to the runtime ``.env``." in source


def test_profiles_source_avoids_hidden_hermes_dir_literal_in_export_comment():
    source = Path(profiles.__file__).read_text(encoding="utf-8")
    assert 'directory name is ".hermes", not "default".' not in source
    assert 'directory name is the hidden runtime-home directory, not "default".' in source
