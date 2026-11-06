"""Test-only observation of serialized DeepSeek bodies; never enabled by product code."""
from contextlib import contextmanager
import hashlib
import json
from unittest.mock import patch

from smartdocs.deepseek import DeepSeekModel
from smartdocs.agent_runner import RunError


def strings(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, str):
        yield value
        # Tool results/arguments are JSON strings inside the outer request JSON.
        try:
            nested = json.loads(value)
        except (ValueError, RecursionError):
            return
        if nested != value:
            yield from strings(nested)


@contextmanager
def observe_requests(record, forbidden):
    """Single-process trusted benchmark only; a match blocks rather than rewrites bytes.

    This finite fixture-string check is not a general secret detector or product ACL.
    forwarded means admitted to the original _send, not proven delivery to the provider.
    """
    original = DeepSeekModel._send
    sequence = 0

    async def observed(model, body):
        nonlocal sequence
        sequence += 1
        values = list(strings(json.loads(body)))
        matches = sum(any(needle in value for value in values) for needle in forbidden)
        record({'sequence': sequence, 'request_bytes': len(body),
                'sha256': hashlib.sha256(body).hexdigest(),
                'restricted_matches': matches, 'forwarded': matches == 0})
        if matches:
            raise RunError('scope_changed')
        return await original(model, body)

    with patch.object(DeepSeekModel, '_send', observed):
        yield
