"""Textual-based TUI for MQTop.

For now this is a thin wrapper around the existing Rich-based table:
- it uses the same queue-fetching logic,
- renders the same spinner + table layout,
- but runs inside a Textual `App`, so we can extend it later
  (e.g. provider switching, message details, etc.).
"""

from __future__ import annotations

from typing import Dict, Tuple

import requests
from rich.console import Group
from textual.app import App, ComposeResult
from textual.widgets import Footer, Static

from .config import ProviderConfig
from .errors import MQTopError
from .monitor import (
    QueueInfo,
    _build_spinner,
    _build_table,
    _fetch_queues,
    _management_base_url,
)


class MQTopApp(App):
    """Minimal Textual application showing the MQTop queue table."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #body {
        height: 1fr;
    }
    """

    def __init__(self, provider: ProviderConfig, refresh: float = 1.0) -> None:
        super().__init__()
        self._provider = provider
        self._refresh = refresh
        self._step = 0
        self._baselines: Dict[Tuple[str, str], Tuple[int, int]] = {}

    def compose(self) -> ComposeResult:
        yield Static(id="body")
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"MQTop – {self._provider.name} ({self._provider.type})"
        # Initial render and periodic refresh.
        self.set_interval(self._refresh, self._refresh_view)
        self.call_later(self._refresh_view)

    def _refresh_view(self) -> None:
        """Fetch queues and update the Rich view inside Textual."""
        try:
            queues = _fetch_queues(self._provider, pattern=None)
        except requests.RequestException as exc:
            base = _management_base_url(self._provider)
            raise MQTopError(f"Failed to fetch queues from {base}: {exc}") from exc

        self._update_deltas(queues)

        table = _build_table(self._provider, queues)
        spinner = _build_spinner(self._step)
        self._step += 1

        body = self.query_one("#body", Static)
        body.update(Group(spinner, table))

    def _update_deltas(self, queues: list[QueueInfo]) -> None:
        """Update per-session publish/deliver deltas in-place."""
        for q in queues:
            key = (q.vhost, q.name)
            if key not in self._baselines:
                self._baselines[key] = (q.publish_total, q.deliver_total)
            base_pub, base_del = self._baselines[key]
            q.publish_total = max(0, q.publish_total - base_pub)
            q.deliver_total = max(0, q.deliver_total - base_del)

