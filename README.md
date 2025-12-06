# MQTop

MQTop is a lightweight Python CLI tool for developers and SREs working with RabbitMQ, especially in Kubernetes environments. It provides a `top`-like live view of queues and can automatically manage `kubectl port-forward` for RabbitMQ running inside a cluster.

## Installation (development)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

This will install the `mqtop` command in your virtualenv.

## Configuration

MQTop reads its configuration from:

- `~/.mqtop/config.toml`

Start by copying the example config from the repo:

```bash
mkdir -p ~/.mqtop
cp config.example.toml ~/.mqtop/config.toml
```

Adjust provider definitions to match your environment. Example `dev-k8s` provider:

```toml
[providers.dev-k8s]
type = "k8s"
context = "dev"
namespace = "messaging"
service = "rabbitmq"
remote_amqp_port = 5672
local_amqp_port = 5673
local_ui_port = 15672
username = "guest"
password = "guest"
```

## Usage

- Default `top` view (auto-uses provider `dev-k8s`):

```bash
mqtop
```

The `top` table shows, among others:
- current queue depth (`ready`, `unacked`),
- basic rates (`pub/s`, `del/s`),
- per-session totals (`pubΔ`, `delΔ`) since MQTop was started.

- Explicit provider and refresh interval:

```bash
mqtop --provider dev-k8s --refresh 1.0
```

- List configured providers:

```bash
mqtop providers list
```

- Manually manage Kubernetes port-forward:

```bash
mqtop k8s forward start dev-k8s
mqtop k8s forward status dev-k8s
mqtop k8s forward stop dev-k8s
```

- Peek messages from a queue (non-destructive):

```bash
mqtop msg peek my_queue_name -P dev-k8s -n 5
```

## Notes for learning Python

The codebase is intentionally structured to be educational:

- `src/mqtop/config.py` – typed configuration models and TOML handling.
- `src/mqtop/k8s.py` – small adapter around `kubectl port-forward`.
- `src/mqtop/monitor.py` – RabbitMQ Management API integration + Rich TUI.
- `src/mqtop/cli.py` – Typer-based CLI wiring everything together.

Comments and structure should help you see how a typical modern Python CLI project is organised.
