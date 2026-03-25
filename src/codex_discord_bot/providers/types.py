from __future__ import annotations

from enum import Enum


class ProviderKind(str, Enum):
    codex = "codex"
    claude = "claude"


def provider_value(provider: ProviderKind | str) -> str:
    if isinstance(provider, ProviderKind):
        return provider.value
    return provider


def provider_display_name(provider: ProviderKind | str) -> str:
    if provider_value(provider) == ProviderKind.claude.value:
        return "Claude"
    return "Codex"


def provider_root_command(provider: ProviderKind | str) -> str:
    if provider_value(provider) == ProviderKind.claude.value:
        return "claude"
    return "codex"
