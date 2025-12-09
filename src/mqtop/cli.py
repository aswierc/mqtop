"""
CLI entrypoint for MQTop.

This module wires together:
- the main `mqtop` command,
- the `top` subcommand (live queue view),
- the `k8s forward` group for manual port-forward control,
- the `providers` group for inspecting configured providers.
"""

from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from .config import ProviderConfig, load_providers
from .errors import MQTopError
from .k8s import forward_status, start_forward, stop_forward
from .messages import peek_messages, print_peeked_messages
from .tui import MQTopApp

app = typer.Typer(help="MQTop – RabbitMQ top + Kubernetes port-forward helper.")

k8s_app = typer.Typer(help="Kubernetes-related commands.")
forward_app = typer.Typer(help="Manage port-forwarding to RabbitMQ.")
providers_app = typer.Typer(help="Manage RabbitMQ providers from config.")
msg_app = typer.Typer(help="Queue/message tools.")


def _load_providers_or_exit() -> dict[str, ProviderConfig]:
    """Load providers config or exit with a friendly message if missing."""
    try:
        return load_providers()
    except FileNotFoundError:
        typer.echo(
            "Config file ~/.mqtop/config.toml not found.\n"
            "Copy config.example.toml from the repository to ~/.mqtop/config.toml "
            "and adjust it to your environment."
        )
        raise typer.Exit(code=1)


def _run_textual_top(
    providers: dict[str, ProviderConfig],
    provider_name: str,
    refresh: float,
) -> None:
    """Shared helper to run the Textual-based top for a provider.

    The Textual app itself handles port-forward and health-check,
    and supports switching providers via keyboard shortcuts.
    """
    app_ui = MQTopApp(providers=providers, initial_provider=provider_name, refresh=refresh)
    try:
        app_ui.run()
    except MQTopError as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit(code=1)


@app.command()
def top(
    refresh: float = typer.Option(
        1.0,
        "--refresh",
        "-r",
        help="Refresh interval in seconds.",
    ),
    pattern: Optional[str] = typer.Option(
        None,
        "--pattern",
        "-p",
        help='Optional queue name pattern, e.g. "payments.*".',
    ),
    provider: str = typer.Option(
        "dev-k8s",
        "--provider",
        "-P",
        help="Provider name from TOML config (section [providers.<name>]).",
    ),
) -> None:
    """Top mode – live view of RabbitMQ queues in a Textual TUI."""
    providers = _load_providers_or_exit()
    if provider not in providers:
        available = ", ".join(sorted(providers.keys())) or "(none)"
        typer.echo(
            f"Provider '{provider}' not found in config. "
            f"Available providers: {available}"
        )
        raise typer.Exit(code=1)

    # For now we ignore pattern and reuse the Textual-based top.
    _run_textual_top(providers, provider_name=provider, refresh=refresh)


@forward_app.command("start")
def k8s_forward_start(
    provider: str = typer.Argument(
        "dev-k8s",
        help="Name of provider of type 'k8s' from TOML config.",
    ),
) -> None:
    """Manually starts port-forward for the given provider."""
    providers = _load_providers_or_exit()
    selected = providers.get(provider)
    if selected is None:
        available = ", ".join(sorted(providers.keys())) or "(none)"
        typer.echo(
            f"Provider '{provider}' not found in config. "
            f"Available providers: {available}"
        )
        raise typer.Exit(code=1)

    fs = start_forward(selected)
    if fs is None:
        typer.echo("Provider is not of type 'k8s' – nothing to forward.")
        raise typer.Exit(code=0)

    typer.echo(
        "Port-forward started (or was already running):\n"
        f"  provider={fs.provider_name}\n"
        f"  pid={fs.pid}\n"
        f"  command={' '.join(fs.command)}"
    )


@forward_app.command("stop")
def k8s_forward_stop(
    provider: str = typer.Argument(
        "dev-k8s",
        help="Name of provider of type 'k8s' from TOML config.",
    ),
) -> None:
    """Stops port-forward for the given provider."""
    providers = _load_providers_or_exit()
    selected = providers.get(provider)
    if selected is None:
        available = ", ".join(sorted(providers.keys())) or "(none)"
        typer.echo(
            f"Provider '{provider}' not found in config. "
            f"Available providers: {available}"
        )
        raise typer.Exit(code=1)

    stopped = stop_forward(selected)
    if stopped:
        typer.echo(f"Stopped port-forward for provider '{provider}'.")
    else:
        typer.echo(
            f"No active port-forward found for provider '{provider}'."
        )


@forward_app.command("status")
def k8s_forward_status(
    provider: str = typer.Argument(
        "dev-k8s",
        help="Name of provider of type 'k8s' from TOML config.",
    ),
) -> None:
    """Shows port-forward status for the given provider."""
    providers = _load_providers_or_exit()
    selected = providers.get(provider)
    if selected is None:
        available = ", ".join(sorted(providers.keys())) or "(none)"
        typer.echo(
            f"Provider '{provider}' not found in config. "
            f"Available providers: {available}"
        )
        raise typer.Exit(code=1)

    fs = forward_status(selected)
    if fs is None:
        typer.echo(f"No active port-forward for provider '{provider}'.")
    else:
        typer.echo(
            "Port-forward is running:\n"
            f"  provider={fs.provider_name}\n"
            f"  pid={fs.pid}\n"
            f"  command={' '.join(fs.command)}"
        )


k8s_app.add_typer(forward_app, name="forward")
app.add_typer(k8s_app, name="k8s")


@providers_app.command("list")
def providers_list() -> None:
    """Prints providers defined in the TOML config."""
    console = Console()
    providers = _load_providers_or_exit()

    table = Table(
        title="MQTop – providers",
        box=box.SIMPLE,
        header_style="bold white",
        border_style="orange3",
    )
    table.add_column("name", style="bold cyan")
    table.add_column("type", style="magenta")
    table.add_column("host/context", style="dim")
    table.add_column("details", style="dim")

    if not providers:
        table.add_row("(none)", "-", "-", "-")
    else:
        for p in providers.values():
            if p.type == "k8s":
                host_ctx = p.context or "(no-context)"
                details = f"{p.namespace}/{p.service} amqp {p.local_amqp_port}->{p.remote_amqp_port}"
            else:
                host_ctx = p.host or "localhost"
                details = f"mgmt:{p.management_port or 15672}"

            table.add_row(p.name, p.type, host_ctx, details)

    console.print(table)


app.add_typer(providers_app, name="providers")
app.add_typer(k8s_app, name="k8s")


@msg_app.command("peek")
def msg_peek(
    queue: str = typer.Argument(..., help="Queue name to peek into."),
    count: int = typer.Option(
        10,
        "--count",
        "-n",
        help="Maximum number of messages to peek.",
    ),
    provider: str = typer.Option(
        "dev-k8s",
        "--provider",
        "-P",
        help="Provider name from TOML config.",
    ),
) -> None:
    """Peek into a queue without consuming messages."""
    providers = _load_providers_or_exit()
    selected = providers.get(provider)
    if selected is None:
        available = ", ".join(sorted(providers.keys())) or "(none)"
        typer.echo(
            f"Provider '{provider}' not found in config. "
            f"Available providers: {available}"
        )
        raise typer.Exit(code=1)

    try:
        msgs = peek_messages(selected, queue=queue, count=count)
    except MQTopError as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit(code=1)
    print_peeked_messages(msgs)


app.add_typer(msg_app, name="msg")


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    provider: str = typer.Option(
        "dev-k8s",
        "--provider",
        "-P",
        help="Provider name from TOML config (section [providers.<name>]).",
    ),
    refresh: float = typer.Option(
        1.0,
        "--refresh",
        "-r",
        help="Refresh interval in seconds.",
    ),
) -> None:
    """When called without subcommand, run Textual `top` by default."""
    if ctx.invoked_subcommand is not None:
        return

    providers = _load_providers_or_exit()
    if provider not in providers:
        available = ", ".join(sorted(providers.keys())) or "(none)"
        typer.echo(
            f"Provider '{provider}' not found in config. "
            f"Available providers: {available}"
        )
        raise typer.Exit(code=1)

    _run_textual_top(providers, provider_name=provider, refresh=refresh)


def main() -> None:
    app()
