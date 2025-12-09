"""Textual-based TUI for MQTop.

This module hosts the main interactive UI:
- it uses the same queue-fetching logic as the Rich-based `top`,
- shows a spinner + queue table,
- allows switching between providers (profiles) with a key binding.
"""

from __future__ import annotations

from typing import Dict, Tuple

import requests
from rich.console import Group
from textual import events
from textual.app import App, ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from .config import ProviderConfig
from .errors import MQTopError
from .k8s import ensure_forward_for_provider
from .monitor import (
    QueueInfo,
    _build_spinner,
    _build_table,
    _fetch_queues,
    _management_base_url,
    check_management_health,
)


class MQTopApp(App):
    """Textual application showing the MQTop queue table with profile switching."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #status {
        height: 1;
        padding: 0 1;
    }

    #body {
        height: 1fr;
    }
    """

    # `p` opens provider selector, `q` quits.
    BINDINGS = [
        ("p", "choose_provider", "Choose provider"),
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        providers: Dict[str, ProviderConfig],
        initial_provider: str,
        refresh: float = 1.0,
    ) -> None:
        super().__init__()
        self._providers = providers
        self._provider_name = initial_provider
        # Fallback to any provider if initial name is not present.
        self._provider = providers.get(
            initial_provider, next(iter(providers.values()))
        )
        self._refresh = refresh
        self._step = 0
        self._baselines: Dict[Tuple[str, str], Tuple[int, int]] = {}
        self._connection_status: str = "Initializing..."
        self._interval_started = False

    def compose(self) -> ComposeResult:
        yield Static(id="status")
        yield Static(id="body")
        yield Footer()

    def on_mount(self) -> None:
        # Activate initial provider (port-forward + health-check) and start loop.
        self._activate_provider(self._provider_name, initial=True)
        self._update_status()

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

    def _update_status(self) -> None:
        """Render current profile and connection status."""
        status_widget = self.query_one("#status", Static)
        provider = self._provider
        profile_info = f"profile: {self._provider_name} ({provider.type})"
        text = f"[bold orange3]MQTop[/bold orange3] – {profile_info} | {self._connection_status}"
        status_widget.update(text)

    def _activate_provider(self, name: str, initial: bool = False) -> None:
        """Switch or activate provider, including port-forward and health-check.

        On failure keeps the previous provider and only updates status text.
        """
        provider = self._providers.get(name)
        if provider is None:
            self._connection_status = f"[red]Unknown provider: {name}[/red]"
            self._update_status()
            return

        try:
            fs = ensure_forward_for_provider(provider)
            if fs is not None:
                status_suffix = (
                    f"K8s port-forward pid={fs.pid} "
                    f"({provider.namespace}/{provider.service})"
                )
            else:
                status_suffix = "direct connection"

            # Health-check will raise MQTopError on failure.
            check_management_health(provider)
        except MQTopError as exc:
            self._connection_status = f"[red]Profile {name}[/red] – {exc}"
            self._update_status()
            return

        # Successful activation – update current provider and UI state.
        self._provider_name = name
        self._provider = provider
        self._baselines.clear()
        self._step = 0
        self.title = f"MQTop – {provider.name} ({provider.type})"
        self._connection_status = (
            f"[green]Profile {name} ({provider.type})[/green] – {status_suffix}"
        )
        self._update_status()

        if initial and not self._interval_started:
            # Start periodic refresh loop only once.
            self.set_interval(self._refresh, self._refresh_view)
            self.call_later(self._refresh_view)
            self._interval_started = True

    def action_choose_provider(self) -> None:
        """Open a selector to choose provider and confirm with Enter."""

        def _on_chosen(name: str | None) -> None:
            if name:
                self._activate_provider(name, initial=False)

        self.push_screen(
            ProviderSelectScreen(self._providers, self._provider_name), _on_chosen
        )


class ProviderSelectScreen(ModalScreen[str | None]):
    """Modal screen with a simple list of providers to choose from."""

    CSS = """
    ProviderSelectScreen {
        align: center middle;
    }
    """

    def __init__(self, providers: Dict[str, ProviderConfig], current: str) -> None:
        super().__init__()
        self._providers = providers
        self._current = current

    def compose(self) -> ComposeResult:
        yield Static(
            "Select provider (Enter=confirm, Esc=cancel)", id="selector-title"
        )
        options = [Option(name) for name in sorted(self._providers.keys())]
        yield OptionList(*options, id="provider-list")

    def on_mount(self) -> None:
        option_list = self.query_one(OptionList)
        names = [opt.prompt for opt in option_list.options]
        if not names:
            return
        try:
            index = names.index(self._current)
        except ValueError:
            index = 0
        option_list.index = index
        option_list.focus()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        self.dismiss(str(event.option.prompt))

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.dismiss(None)
