"""Kubernetes integration (port-forwarding etc.).

This module focuses on:
- building `kubectl port-forward` commands from ProviderConfig,
- a small API that can be used from the CLI layer.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from .config import ProviderConfig


FORWARD_STATE_PATH: Path = Path("~/.mqtop/forward_state.json").expanduser()
LOG_PATH: Path = Path("~/.mqtop/kubectl_forward.log").expanduser()


@dataclass
class ForwardState:
    """Information about a running (or planned) port-forward."""

    provider_name: str
    pid: int
    command: List[str]


def build_port_forward_command(provider: ProviderConfig) -> List[str]:
    """Build a `kubectl port-forward` command for a K8s provider.

    Design idea:
    - keep command construction in one place,
    - make it easy to test (no kubectl subprocess here),
    - CLI can log or execute this command.
    """
    if provider.type != "k8s":
        raise ValueError(
            f"Provider {provider.name!r} is not of type 'k8s' (type={provider.type!r})."
        )

    if not provider.namespace or not provider.service:
        raise ValueError(
            "K8s provider requires 'namespace' and 'service' in configuration."
        )

    if not provider.remote_amqp_port or not provider.local_amqp_port:
        raise ValueError(
            "K8s provider requires 'remote_amqp_port' and 'local_amqp_port'."
        )

    resource = f"{provider.service}"

    ports = [
        f"{provider.local_amqp_port}:{provider.remote_amqp_port}",
    ]
    if provider.local_ui_port:
        # Przykład: 15672:15672
        ports.append(f"{provider.local_ui_port}:{provider.local_ui_port}")

    cmd: List[str] = ["kubectl"]

    if provider.context:
        cmd.extend(["--context", provider.context])

    cmd.extend(
        [
            "port-forward",
            resource,
            *ports,
            "-n",
            provider.namespace,
        ]
    )

    return cmd


def _load_forward_state() -> Dict[str, ForwardState]:
    """Load stored port-forward state from a JSON file.

    Typical Python CLI pattern: keep small helper state in a simple
    file (JSON here) instead of reaching for a full database.
    """
    if not FORWARD_STATE_PATH.exists():
        return {}

    raw = json.loads(FORWARD_STATE_PATH.read_text(encoding="utf-8"))
    result: Dict[str, ForwardState] = {}
    for provider_name, entry in raw.items():
        result[provider_name] = ForwardState(
            provider_name=provider_name,
            pid=entry["pid"],
            command=list(entry["command"]),
        )
    return result


def _save_forward_state(state: Dict[str, ForwardState]) -> None:
    serializable = {
        name: {"pid": fs.pid, "command": fs.command} for name, fs in state.items()
    }
    FORWARD_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FORWARD_STATE_PATH.write_text(json.dumps(serializable, indent=2), encoding="utf-8")


def _is_pid_running(pid: int) -> bool:
    """Check whether a process with given PID is still running.

    The `os.kill(pid, 0)` trick is common on Unix (Mac/Linux) –
    it does not kill the process, only asks the kernel if the PID exists.
    """
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def start_forward(provider: ProviderConfig) -> ForwardState | None:
    """Start `kubectl port-forward` for a K8s provider if needed.

    - If provider is not `k8s`, returns None (nothing to do).
    - If forward is already running (PID alive), returns existing state.
    - If not, starts a new process and stores its state in a file.
    """
    if provider.type != "k8s":
        return None

    state = _load_forward_state()
    existing = state.get(provider.name)

    if existing and _is_pid_running(existing.pid):
        return existing

    cmd = build_port_forward_command(provider)

    # Start process in the background and redirect stdout/stderr to a log file
    # so the user terminal is not flooded with `Forwarding from ...` messages.
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=log_file,
        )

    new_state = ForwardState(provider_name=provider.name, pid=proc.pid, command=cmd)
    state[provider.name] = new_state
    _save_forward_state(state)

    return new_state


def stop_forward(provider: ProviderConfig) -> bool:
    """Stop port-forward for a given provider, if it is running.

    Returns True if we actually stopped something."""
    state = _load_forward_state()
    fs = state.get(provider.name)
    if not fs:
        return False

    if not _is_pid_running(fs.pid):
        # Process no longer exists – just clean up stored state.
        state.pop(provider.name, None)
        _save_forward_state(state)
        return False

    try:
        os.kill(fs.pid, signal.SIGTERM)
    except OSError:
        # If we cannot kill it, treat it as already dead.
        pass

    state.pop(provider.name, None)
    _save_forward_state(state)
    return True


def forward_status(provider: ProviderConfig) -> ForwardState | None:
    """Return port-forward state for a provider, if it is running."""
    state = _load_forward_state()
    fs = state.get(provider.name)
    if not fs:
        return None

    if not _is_pid_running(fs.pid):
        # PID does not exist anymore – cleanup and indicate no state.
        state.pop(provider.name, None)
        _save_forward_state(state)
        return None

    return fs


def ensure_forward_for_provider(provider: ProviderConfig) -> ForwardState | None:
    """Convenience wrapper used from CLI / `top` mode.

    For `k8s` providers ensures forward is running,
    for `direct` simply returns None.
    """
    return start_forward(provider)
