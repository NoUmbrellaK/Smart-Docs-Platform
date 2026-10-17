"""Bounded real child IO/cleanup, with synthetic token IDs only."""
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from smartdocs import resident_embeddings as m

CHUNKS = [{'chunk_id': 'a'*64, 'text': 'abc'}]


class TransientTokenizerTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(getattr(m, '_tokenize_snapshot', None)),
                        'bounded transient tokenizer transport missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.marker = str(Path(self.temp.name)/'child.json')
        self.fds = []
        for _ in range(2):
            fd = os.memfd_create('test-tokenizer', os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC)
            self.addCleanup(os.close, fd)
            os.write(fd, b'abc')
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW |
                        fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            self.fds.append(fd)

    def run_child(self, mode, timeout=2):
        command = [sys.executable, '-B', str(Path(__file__).with_name('transient_tokenizer_fixture.py')),
                   mode, self.marker, *map(str, self.fds)]
        with patch.object(m, '_tokenizer_command', return_value=command), \
                patch.dict(os.environ, {'SMARTDOCS_TEST_SECRET': 'must-not-inherit'}):
            return m._tokenize_snapshot(CHUNKS, tuple(self.fds), timeout=timeout)

    def assert_reaped(self):
        state = json.loads(Path(self.marker).read_text())
        self.assertEqual(state['group'], os.getpgrp(), 'child escaped supervisor process group')
        with self.assertRaises(ProcessLookupError):
            os.kill(state['pid'], 0)

    def test_sealed_descriptors_only_environment_and_reaping_before_result(self):
        before = len(os.listdir('/proc/self/fd'))
        result = self.run_child('valid')
        self.assertEqual(result['chunks'][0]['windows'][0]['ids'], [1, 2])
        self.assert_reaped()
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    def test_native_stop_times_out_and_is_reaped(self):
        begin = time.monotonic()
        with self.assertRaises(m.base.EmbeddingError) as caught:
            self.run_child('stop', timeout=0.3)
        self.assertEqual(caught.exception.code, 'embedding_timeout')
        self.assertLess(time.monotonic()-begin, 2)
        self.assert_reaped()

    def test_bad_oversized_crash_and_safe_errors_never_return_partial_tokens(self):
        for mode, code in [('bad', 'embedding_invalid_result'), ('oversize', 'embedding_result_too_large'),
                           ('crash', 'embedding_worker_failed'), ('invalid', 'embedding_invalid_input')]:
            with self.subTest(mode=mode):
                with self.assertRaises(m.base.EmbeddingError) as caught:
                    self.run_child(mode)
                self.assertEqual(caught.exception.code, code)
                self.assert_reaped()

    def test_invalid_input_does_not_spawn(self):
        with patch.object(m, '_tokenizer_command', side_effect=AssertionError('invalid input spawned child')):
            with self.assertRaises(m.base.EmbeddingError):
                m._tokenize_snapshot([], tuple(self.fds))
        self.assertFalse(Path(self.marker).exists())

    def test_real_child_rejects_non_model_sealed_bytes(self):
        with self.assertRaises(m.base.EmbeddingError) as caught:
            m._tokenize_snapshot(CHUNKS, tuple(self.fds))
        self.assertEqual(caught.exception.code, 'embedding_model_unavailable')

    def test_constructor_retains_only_sealed_fds_and_close_is_idempotent(self):
        from types import SimpleNamespace
        hashes = dict.fromkeys(m.base.MODEL_HASHES, 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
        for name in hashes:
            (Path(self.temp.name)/name).write_bytes(b'abc')
        before = len(os.listdir('/proc/self/fd'))
        # Only native model construction is replaced; all SHA/seals/FD ownership are real.
        with patch.dict(m.base.MODEL_HASHES, hashes, clear=True), \
                patch.object(m.base, '_verify_dependencies'), \
                patch.object(m.base, '_new_session', return_value=SimpleNamespace(disable_fallback=lambda: None)):
            engine = m.ResidentEncoder(self.temp.name)
        self.addCleanup(engine.close)
        self.assertEqual(len(os.listdir('/proc/self/fd')), before+2)
        fds = engine._tokenizer_fds
        for fd in fds:
            self.assertTrue(fcntl.fcntl(fd, fcntl.F_GET_SEALS) & fcntl.F_SEAL_WRITE)
        engine.close(); engine.close()
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)
        with self.assertRaises(m.base.EmbeddingError):
            engine.encode(CHUNKS)


if __name__ == '__main__': unittest.main()
