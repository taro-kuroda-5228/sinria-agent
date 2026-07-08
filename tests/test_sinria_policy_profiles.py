from hermes_cli.config import DEFAULT_CONFIG



def test_sinria_policy_profiles_scaffold_exists():
    policy = DEFAULT_CONFIG["sinria"]["policy"]

    assert policy["active_profile"] == "dogfood_frontier"
    profiles = policy["profiles"]
    assert set(profiles) >= {
        "dogfood_frontier",
        "enterprise_guarded_cloud",
        "sovereign_local_only",
    }



def test_sinria_policy_profiles_encode_expected_boundaries():
    profiles = DEFAULT_CONFIG["sinria"]["policy"]["profiles"]

    assert profiles["dogfood_frontier"]["external_send"] == "ask"
    assert profiles["dogfood_frontier"]["retain_raw_history_locally"] is True
    assert profiles["dogfood_frontier"]["retain_sanitized_training_log"] is True

    assert profiles["enterprise_guarded_cloud"]["confidential_external_send"] == "block_unless_approved"

    assert profiles["sovereign_local_only"]["external_send"] == "block"
    assert profiles["sovereign_local_only"]["confidential_external_send"] == "block"
    assert profiles["sovereign_local_only"]["retain_sanitized_training_log"] is False
