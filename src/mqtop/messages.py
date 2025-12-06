"""Queue/message tools built on top of RabbitMQ Management API.

Right now we implement a simple, non-destructive message peek for a queue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import quote

import requests
from rich.console import Console
from rich.table import Table

from .config import ProviderConfig
from .monitor import _management_base_url, _fetch_queues


@dataclass
class PeekedMessage:
    payload: str
    routing_key: str
    exchange: str
    redelivered: bool


def peek_messages(
    provider: ProviderConfig,
    queue: str,
    count: int = 10,
    vhost: Optional[str] = None,
) -> List[PeekedMessage]:
    """Peek into a queue using the Management API `/api/queues/vhost/name/get`.

    We use `ackmode=ack_requeue_true` which:
    - acks messages,
    - but requeues them immediately – effectively giving us a non-destructive peek.
    """
    base = _management_base_url(provider)

    # If vhost is not explicitly provided, try to discover it from `/api/queues`
    # by matching queue name. This helps when queues live in non-default vhosts
    # and the user did not specify vhost in config.
    effective_vhost = vhost or provider.vhost
    if effective_vhost is None:
        try:
            queues = _fetch_queues(provider, pattern=None)
            for q in queues:
                if q.name == queue:
                    effective_vhost = q.vhost or "/"
                    break
        except requests.RequestException:
            effective_vhost = "/"

    if effective_vhost is None:
        effective_vhost = "/"

    url = f"{base}/api/queues/{quote(effective_vhost, safe='')}/{quote(queue, safe='')}/get"

    auth = None
    if provider.username and provider.password:
        auth = (provider.username, provider.password)

    body = {
      "count": count,
      "ackmode": "ack_requeue_true",
      "encoding": "auto",
      "truncate": 5000,
    }

    resp = requests.post(url, json=body, auth=auth, timeout=5)
    resp.raise_for_status()
    data = resp.json()

    messages: List[PeekedMessage] = []
    for item in data:
        payload = item.get("payload", "")
        routing_key = item.get("routing_key", "")
        exchange = item.get("exchange", "")
        redelivered = bool(item.get("redelivered", False))
        messages.append(
            PeekedMessage(
                payload=payload,
                routing_key=routing_key,
                exchange=exchange,
                redelivered=redelivered,
            )
        )

    return messages


def print_peeked_messages(messages: List[PeekedMessage]) -> None:
    """Render peeked messages in a simple Rich table."""
    console = Console()

    if not messages:
        console.print("No messages to show.")
        return

    table = Table(
        title="MQTop – message peek",
        header_style="bold white",
        box=None,
    )
    table.add_column("#", style="dim", width=4)
    table.add_column("routing_key", style="cyan", overflow="ellipsis", no_wrap=True)
    table.add_column("exchange", style="magenta", overflow="ellipsis", no_wrap=True)
    table.add_column("redelivered", style="yellow", width=10)
    table.add_column("payload", style="white", overflow="fold")

    for idx, msg in enumerate(messages, start=1):
        table.add_row(
            str(idx),
            msg.routing_key or "",
            msg.exchange or "",
            "yes" if msg.redelivered else "no",
            msg.payload,
        )

    console.print(table)
