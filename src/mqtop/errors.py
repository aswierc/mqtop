"""Shared exception types for MQTop.

Having a small hierarchy of custom exceptions makes it easier to:
- keep HTTP / OS-level errors close to the point where they happen,
- convert them into user-friendly messages in the CLI layer.
"""

from __future__ import annotations


class MQTopError(Exception):
    """Base exception for user-facing MQTop errors."""

    pass

