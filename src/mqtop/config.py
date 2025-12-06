"""Configuration handling for MQTop (TOML, paths, data models).

For now we assume the user manually creates `~/.mqtop/config.toml`
based on `config.example.toml` in the repository.

The goal of this module is to:
- keep the config path definition in a single place,
- provide a small typed API we can extend later (e.g. validation).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Literal

import toml


CONFIG_PATH: Path = Path("~/.mqtop/config.toml").expanduser()


ProviderType = Literal["direct", "k8s"]


@dataclass
class ProviderConfig:
    """Minimal shared configuration for a RabbitMQ provider.

    This acts as a small contract between the CLI and the rest of the code:
    dataclasses give us a simple, typed object instead of raw dictionaries.
    """

    name: str
    type: ProviderType

    # Common fields / potentially useful later (auth, vhost, etc.).
    host: str | None = None
    amqp_port: int | None = None
    management_port: int | None = None
    # In practice RabbitMQ often uses guest/guest by default.
    # vhost is left as None – then we do not filter by it until the user
    # explicitly sets it in config.
    username: str | None = "guest"
    password: str | None = "guest"
    vhost: str | None = None

    # K8s-specific settings (may be empty if type != "k8s").
    context: str | None = None
    namespace: str | None = None
    service: str | None = None
    remote_amqp_port: int | None = None
    local_amqp_port: int | None = None
    local_ui_port: int | None = None


def load_providers() -> Dict[str, ProviderConfig]:
    """Load provider definitions from TOML.

    For you as a user this means:
    - you edit `~/.mqtop/config.toml` based on `config.example.toml`,
    - in code we only deal with provider names (e.g. `dev-k8s`).

    For now we assume the file exists and is valid – we can add
    more validation and better error messages later.
    """

    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Config file not found at {CONFIG_PATH}. "
            "Please copy config.example.toml to ~/.mqtop/config.toml."
        )

    data = toml.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    providers_section = data.get("providers", {})

    providers: Dict[str, ProviderConfig] = {}
    for name, cfg in providers_section.items():
        providers[name] = ProviderConfig(
            name=name,
            type=cfg.get("type", "direct"),
            host=cfg.get("host"),
            amqp_port=cfg.get("amqp_port"),
            management_port=cfg.get("management_port"),
            username=cfg.get("username", "guest"),
            password=cfg.get("password", "guest"),
            vhost=cfg.get("vhost"),
            context=cfg.get("context"),
            namespace=cfg.get("namespace"),
            service=cfg.get("service"),
            remote_amqp_port=cfg.get("remote_amqp_port"),
            local_amqp_port=cfg.get("local_amqp_port"),
            local_ui_port=cfg.get("local_ui_port"),
        )

    return providers
