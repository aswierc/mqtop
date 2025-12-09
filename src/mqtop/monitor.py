"""RabbitMQ metrics collection and `top` mode.

This module implements a simple `top`-like view on queues using Rich:
- it calls the RabbitMQ Management API (`/api/queues`),
- shows all queues (no pattern filtering for now),
- renders a colored TUI table that refreshes every `refresh` seconds,
- tracks simple per-session history of published/delivered messages.

Later we can extend this with interactive controls (sorting, filters, etc.).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import requests
from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from .config import ProviderConfig
from .errors import MQTopError


@dataclass
class QueueInfo:
    name: str
    vhost: str
    messages_ready: int
    messages_unacked: int
    consumers: int
    publish_rate: float
    deliver_rate: float
    publish_total: int
    deliver_total: int


def _management_base_url(provider: ProviderConfig) -> str:
    """Build base URL to RabbitMQ Management API for a provider.

    - for `direct` we use host + management_port,
    - for `k8s` we assume UI is exposed on local_ui_port.
    """
    host: str
    port: int

    if provider.type == "k8s":
        host = "127.0.0.1"
        port = provider.local_ui_port or provider.management_port or 15672
    else:
        host = provider.host or "127.0.0.1"
        port = provider.management_port or 15672

    return f"http://{host}:{port}"


def _fetch_queues(provider: ProviderConfig, pattern: Optional[str]) -> List[QueueInfo]:
    base = _management_base_url(provider)
    url = f"{base}/api/queues"

    auth = None
    if provider.username and provider.password:
        auth = (provider.username, provider.password)

    resp = requests.get(url, auth=auth, timeout=5)
    resp.raise_for_status()
    data = resp.json()

    queues: List[QueueInfo] = []
    for item in data:
        name = item.get("name", "")
        vhost = item.get("vhost", "")

        msgs_ready = int(item.get("messages_ready") or 0)
        msgs_unacked = int(item.get("messages_unacknowledged") or 0)
        consumers = int(item.get("consumers") or 0)

        stats = item.get("message_stats") or {}

        # RabbitMQ can expose different detail fields – we take a simple variant.
        publish_rate = float(
            (stats.get("publish_details") or {}).get("rate")
            or stats.get("publish", 0)
            or 0.0
        )
        deliver_rate = float(
            (stats.get("deliver_get_details") or {}).get("rate")
            or stats.get("deliver_get", 0)
            or 0.0
        )

        publish_total = int(stats.get("publish") or 0)
        deliver_total = int(stats.get("deliver_get") or 0)

        queues.append(
            QueueInfo(
                name=name,
                vhost=vhost,
                messages_ready=msgs_ready,
                messages_unacked=msgs_unacked,
                consumers=consumers,
                publish_rate=publish_rate,
                deliver_rate=deliver_rate,
                publish_total=publish_total,
                deliver_total=deliver_total,
            )
        )

    # Sort by messages_ready descending – classic `top` behavior.
    queues.sort(key=lambda q: q.messages_ready, reverse=True)
    return queues


def check_management_health(provider: ProviderConfig) -> None:
    """Perform a lightweight health-check against the Management API.

    The goal is to fail fast with a clear message if:
    - port-forward is not active,
    - RabbitMQ Management is not reachable or returns an error.
    """
    base = _management_base_url(provider)
    url = f"{base}/api/overview"

    auth = None
    if provider.username and provider.password:
        auth = (provider.username, provider.password)

    try:
        resp = requests.get(url, auth=auth, timeout=3)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise MQTopError(
            f"Cannot connect to RabbitMQ Management API at {base}: {exc}"
        ) from exc


_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


def _build_spinner(step: int, text: Optional[str] = None) -> Panel:
    """Simple spinner panel so the user sees the screen is alive.

    `text` can be used by callers to display profile / status information
    inside the panel. If omitted, only the spinner is shown.
    """
    frame = _SPINNER_FRAMES[step % len(_SPINNER_FRAMES)]
    if text:
        content = f"[orange3]{frame}[/orange3] {text}"
    else:
        content = f"[orange3]{frame}[/orange3]"
    return Panel(
        content,
        title="[bold orange3]MQTop[/bold orange3]",
        border_style="orange3",
        padding=(0, 2),
    )


def _build_table(provider: ProviderConfig, queues: List[QueueInfo]) -> Table:
    """Build a Rich table with the queue list.

    Colors are loosely inspired by RabbitMQ orange and tuned for dark themes.
    """
    table = Table(
        title=f"MQTop – {provider.name} ({provider.type})",
        box=box.SIMPLE,
        border_style="orange3",
        show_edge=True,
        show_header=True,
        header_style="bold white",
    )

    table.add_column("queue", style="bold orange3", no_wrap=True, overflow="ellipsis")
    table.add_column("vhost", style="dim", no_wrap=True, overflow="ellipsis")
    table.add_column("ready", justify="right", style="green")
    table.add_column("unacked", justify="right", style="yellow")
    table.add_column("cons", justify="right", style="cyan")
    table.add_column("pub/s", justify="right", style="magenta")
    table.add_column("del/s", justify="right", style="magenta")
    table.add_column("pubΔ", justify="right", style="magenta")
    table.add_column("delΔ", justify="right", style="magenta")

    if not queues:
        table.add_row("(no queues)", "-", "-", "-", "-", "-", "-", "-", "-")
        return table

    for q in queues:
        ready_style = "green"
        if q.messages_ready > 1000:
            ready_style = "red"
        elif q.messages_ready > 100:
            ready_style = "yellow"

        table.add_row(
            q.name,
            q.vhost or "",
            f"[{ready_style}]{q.messages_ready}[/]",
            str(q.messages_unacked),
            str(q.consumers),
            f"{q.publish_rate:.2f}",
            f"{q.deliver_rate:.2f}",
            f"{q.publish_total:d}",
            f"{q.deliver_total:d}",
        )

    return table


def run_top(
    provider: ProviderConfig,
    refresh: float,
    pattern: Optional[str],
) -> None:
    """Simple `top` implementation in TUI using Rich.

    - every `refresh` seconds we fetch `/api/queues`,
    - update the table in a Live view,
    - exit on Ctrl+C.

    `pattern` is currently ignored – we show all queues.
    """
    console = Console()

    try:
        step = 0
        # Per-session baselines for message totals so we can show
        # "Δ since MQTop started".
        baselines: Dict[Tuple[str, str], Tuple[int, int]] = {}

        with Live(console=console, refresh_per_second=8) as live:
            while True:
                try:
                    queues = _fetch_queues(provider, pattern=None)
                except requests.RequestException as exc:
                    # Convert low-level HTTP/connection errors to a user-facing exception.
                    base = _management_base_url(provider)
                    raise MQTopError(
                        f"Failed to fetch queues from {base}: {exc}"
                    ) from exc
                for q in queues:
                    key = (q.vhost, q.name)
                    if key not in baselines:
                        baselines[key] = (q.publish_total, q.deliver_total)
                    base_pub, base_del = baselines[key]
                    q.publish_total = max(0, q.publish_total - base_pub)
                    q.deliver_total = max(0, q.deliver_total - base_del)

                table = _build_table(provider, queues)
                spinner = _build_spinner(step)
                live.update(Group(spinner, table))
                # Spin a bit faster visually by advancing more than one frame.
                step += 2
                time.sleep(refresh)
    except KeyboardInterrupt:
        console.print("\n[bold]Interrupted (Ctrl+C).[/bold]")
