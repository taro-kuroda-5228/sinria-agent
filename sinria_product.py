"""Immutable product identity for every Sinria-facing renderer.

This module is deliberately dependency-free so CLI, gateway, installer, web,
and packaging code can import the same identity without creating import cycles.
Runtime compatibility belongs in dedicated adapters; it must never alter these
public product values.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProductIdentity:
    """Canonical, immutable public identity of this distribution."""

    name: str
    full_name: str
    cli_command: str
    package_name: str
    home_dir_name: str
    home_env: str
    cli_name_env: str
    service_label: str
    repository_url: str
    documentation_url: str


SINRIA_PRODUCT = ProductIdentity(
    name="Sinria",
    full_name="Sinria Agent",
    cli_command="sinria",
    package_name="sinria-agent",
    home_dir_name=".sinria",
    home_env="SINRIA_HOME",
    cli_name_env="SINRIA_CLI_NAME",
    service_label="ai.sinria.gateway",
    repository_url="https://github.com/taro-kuroda-5228/sinria",
    documentation_url="https://sinria-agent.nousresearch.com/docs",
)

# Short alias for renderers where the distribution is unambiguous.
PRODUCT = SINRIA_PRODUCT


def cli_command_name() -> str:
    """Return the immutable Sinria command name for rendered output."""

    return PRODUCT.cli_command


def product_name() -> str:
    """Return the full public product name."""

    return PRODUCT.full_name


def product_short_name() -> str:
    """Return the short public product name."""

    return PRODUCT.name


def display_home() -> str:
    """Return the user-facing default runtime-home path."""

    return f"~/{PRODUCT.home_dir_name}"


__all__ = [
    "PRODUCT",
    "SINRIA_PRODUCT",
    "ProductIdentity",
    "cli_command_name",
    "display_home",
    "product_name",
    "product_short_name",
]
