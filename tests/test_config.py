from pathlib import Path

import pytest

from mqtop import config


def test_load_providers_reads_valid_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """load_providers should parse providers from TOML into ProviderConfig objects."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        """
[providers.dev]
type = "direct"
host = "localhost"
amqp_port = 5672
management_port = 15672
username = "user"
password = "pass"
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "CONFIG_PATH", cfg_file)

    providers = config.load_providers()

    assert "dev" in providers
    dev = providers["dev"]
    assert dev.name == "dev"
    assert dev.type == "direct"
    assert dev.host == "localhost"
    assert dev.amqp_port == 5672
    assert dev.management_port == 15672
    assert dev.username == "user"
    assert dev.password == "pass"


def test_load_providers_missing_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If config file does not exist, load_providers should raise FileNotFoundError."""
    cfg_file = tmp_path / "nonexistent.toml"
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_file)

    with pytest.raises(FileNotFoundError):
        config.load_providers()

