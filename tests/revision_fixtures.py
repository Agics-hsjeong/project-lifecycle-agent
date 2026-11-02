"""Test clients supply a current revision; strict-contract tests use the raw API."""
from tools import lifecycle


def apply_current(folder, payload, actor="agent"):
    history = lifecycle.events(folder)
    payload = dict(payload)
    payload.setdefault("expected_revision", history[-1]["revision"] if history else None)
    return lifecycle.apply_patch(folder, payload, actor)
