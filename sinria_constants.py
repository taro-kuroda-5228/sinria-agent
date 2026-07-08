"""Sinria-native constants module.

The fork still keeps ``hermes_constants`` as the compatibility implementation
module so upstream changes can be copied with minimal conflicts. New Sinria code
should import from this module when touching product-facing runtime/home/path
helpers; the names below delegate to the existing compatibility layer.
"""

from hermes_constants import (  # noqa: F401
    AI_GATEWAY_BASE_URL,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODELS_URL,
    VALID_REASONING_EFFORTS,
    apply_ipv4_preference,
    display_hermes_home,
    display_sinria_home,
    get_config_path,
    get_default_hermes_root,
    get_default_sinria_root,
    get_env_path,
    get_hermes_dir,
    get_hermes_home,
    get_optional_skills_dir,
    get_sinria_dir,
    get_sinria_home,
    get_skills_dir,
    get_subprocess_home,
    is_container,
    is_termux,
    is_wsl,
    parse_reasoning_effort,
)

# Product-native aliases. These keep call sites readable without forcing a risky
# all-at-once rename of the internal ``hermes_*`` compatibility package.
get_home = get_sinria_home
display_home = display_sinria_home
get_default_root = get_default_sinria_root
get_dir = get_sinria_dir
