"""Offline checks for the benchmark-only serialized request observer."""
import asyncio
import hashlib
import json
import unittest
from unittest.mock import patch
import httpx

from smartdocs.deepseek import DeepSeekModel
from smartdocs.agent_runner import RunError
from benchmark_wire import observe_requests


class WireTests(unittest.TestCase):
    def test_real_adapter_records_the_same_bytes_received_by_transport(self):
        records, audit, received = [], [], []
        async def receive(request):
            received.append(request.content)
            reply = {'choices': [{'finish_reason': 'stop',
                                  'message': {'role': 'assistant', 'content': '{}'}}]}
            return httpx.Response(200, stream=httpx.ByteStream(json.dumps(reply).encode()))
        model = DeepSeekModel('synthetic-wire-key', audit.append,
                              transport=httpx.MockTransport(receive))
        with observe_requests(records.append, ['PRIVATE']):
            asyncio.run(model(messages=[{'role': 'user', 'content': 'PUBLIC'}],
                              tools=[], max_tokens=512, timeout=5))
        self.assertEqual(len(received), 1)
        self.assertEqual(records[0]['sha256'], hashlib.sha256(received[0]).hexdigest())
        self.assertEqual(records[0]['request_bytes'], audit[0]['request_bytes'])
        self.assertEqual(audit[-1]['reason'], 'success')

    def test_allowed_bytes_are_unchanged_and_record_contains_no_content_or_key(self):
        body = json.dumps({'messages': [{'role': 'user', 'content': 'PUBLIC-BODY'}]}).encode()
        records, received = [], []
        async def send(model, raw):
            received.append(raw)
            return {'result': 'original response'}
        model = DeepSeekModel('synthetic-wire-key', lambda e: None)
        with patch.object(DeepSeekModel, '_send', send), observe_requests(records.append, ['PRIVATE']):
            result = asyncio.run(model._send(body))
        self.assertEqual(result, {'result': 'original response'})
        self.assertEqual(received, [body])
        self.assertEqual(records, [{'sequence': 1, 'request_bytes': len(body),
            'sha256': hashlib.sha256(body).hexdigest(), 'restricted_matches': 0, 'forwarded': True}])
        self.assertNotIn('PUBLIC-BODY', json.dumps(records))
        self.assertNotIn('synthetic-wire-key', json.dumps(records))

    def test_restricted_nested_json_unicode_is_blocked_before_send(self):
        # Losing recursive JSON decoding would miss escaped Chinese filenames.
        for content in ('PRIVATE-MARKER', json.dumps({'name': '内部清单.txt'})):
            with self.subTest(content=content):
                records = []
                async def send(model, raw):
                    self.fail('restricted bytes must never reach the delegate')
                body = json.dumps({'messages': [{'role': 'tool', 'content': content}]}).encode()
                model = DeepSeekModel('synthetic-wire-key', lambda e: None)
                with patch.object(DeepSeekModel, '_send', send), observe_requests(
                        records.append, ['PRIVATE-MARKER', '内部清单.txt']):
                    with self.assertRaises(RunError):
                        asyncio.run(model._send(body))
                self.assertFalse(records[0]['forwarded'])
                self.assertEqual(records[0]['restricted_matches'], 1)

    def test_record_failure_prevents_send(self):
        async def send(model, raw):
            self.fail('unrecorded request must not be sent')
        def record(event):
            raise OSError('unavailable')
        model = DeepSeekModel('synthetic-wire-key', lambda e: None)
        with patch.object(DeepSeekModel, '_send', send), observe_requests(record, ['PRIVATE']):
            with self.assertRaises(OSError):
                asyncio.run(model._send(b'{"messages":[]}'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
