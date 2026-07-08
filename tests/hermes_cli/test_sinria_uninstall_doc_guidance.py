import hermes_cli.uninstall as uninstall


def test_uninstall_docstrings_avoid_hermes_home_literal():
    assert "~/.hermes" not in uninstall.run_uninstall.__doc__
    assert "active runtime home" in uninstall.run_uninstall.__doc__
    assert "~/.hermes" not in uninstall._uninstall_profile.__doc__
    assert "<cli> -p <name> gateway stop|uninstall" in uninstall._uninstall_profile.__doc__
