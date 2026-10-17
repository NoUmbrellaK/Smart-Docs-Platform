import importlib
import importlib.util
import json
import math
import os
import sys
import unittest


class EmbeddingTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("smartdocs.embeddings"),
                             "bounded embedding encoder is missing")
        return importlib.import_module("smartdocs.embeddings")

    def test_windows_cover_unicode_and_tail_without_truncation(self):
        m = self.module()
        text = ("中文🙂e\u0301\n " * 200)[:1200]
        windows = m.window_ranges(text, lambda value: len(value) + 2)
        self.assertGreater(len(windows), 1)
        self.assertEqual("".join(text[a:b] for a, b in windows), text)
        self.assertEqual(windows[0][0], 0)
        self.assertEqual(windows[-1][1], 1200)
        self.assertTrue(all(b-a+2 <= 128 for a, b in windows))
        self.assertTrue(all(left[1] == right[0] for left, right in zip(windows, windows[1:])))

    def test_nonmonotonic_counts_do_not_create_over_budget_windows(self):
        m = self.module()
        def count(text):
            return 2 if len(text) == 4 else len(text) * 40 + 2
        windows = m.window_ranges("abcdefghij", count)
        self.assertTrue(all(count("abcdefghij"[a:b]) <= 128 for a, b in windows))
        self.assertEqual("".join("abcdefghij"[a:b] for a,b in windows), "abcdefghij")

    def test_window_budget_and_unencodable_character_fail(self):
        m = self.module()
        for count in (lambda _: 129, lambda text: 2+len(text)*100):
            with self.assertRaises(m.EmbeddingError):
                m.window_ranges("x" * 1200, count)

    def test_input_boundary_rejects_oversize_duplicate_ids_and_invalid_utf8(self):
        m = self.module()
        item = {"chunk_id": "a" * 64, "text": "valid"}
        bad = [[], [item] * 9, [item, item], [{**item, "text": "x" * 1201}],
               [{**item, "text": " "}], [{**item, "text": "\ud800"}],
               [{**item, "text": "a\x00b"}], [{**item, "text": 1}],
               [{**item, "chunk_id": "X" * 64}], [{**item, "extra": True}]]
        for value in bad:
            with self.subTest(value=repr(value)[:40]), self.assertRaises(m.EmbeddingError):
                m.validate_chunks(value)
        m.validate_chunks([item, {"chunk_id": "b" * 64, "text": "中文\n🙂"}])

    def test_output_validator_rejects_holes_wrong_digest_and_bad_vectors(self):
        m = self.module()
        import hashlib
        chunks = [{"chunk_id": "a" * 64, "text": "abc"}]
        valid = {"embedding_revision": m.EMBEDDING_REVISION, "chunks": [{
            "chunk_id": "a" * 64, "text_sha256": hashlib.sha256(b"abc").hexdigest(),
            "windows": [{"char_start": 0, "char_end": 3, "vector": [1.0] + [0.0] * 383}]}]}
        m.validate_result(valid, chunks)
        changes = [lambda d: d["chunks"][0].update(text_sha256="0"*64),
                   lambda d: d["chunks"][0]["windows"][0].update(char_start=1),
                   lambda d: d["chunks"][0]["windows"][0].update(char_end=2),
                   lambda d: d["chunks"][0]["windows"][0].update(vector=[0.0]*384),
                   lambda d: d["chunks"][0]["windows"][0].update(vector=[math.nan]*384),
                   lambda d: d["chunks"][0]["windows"][0].update(vector=[True]*384),
                   lambda d: d.update(embedding_revision="wrong"),
                   lambda d: d["chunks"].append(d["chunks"][0])]
        for change in changes:
            invalid = json.loads(json.dumps(valid))
            change(invalid)
            with self.assertRaises(m.EmbeddingError):
                m.validate_result(invalid, chunks)

    def test_real_subprocess_timeout_output_limit_and_errors_are_safe(self):
        m = self.module()
        scripts = [
            ("import time; time.sleep(10)", 0.05, "embedding_timeout"),
            ("print('x' * (2*1024*1024+1))", 2, "embedding_result_too_large"),
            ("raise RuntimeError('private document secret')", 2, "embedding_worker_failed"),
            ("print('not json')", 2, "embedding_invalid_result"),
        ]
        for script, timeout, expected in scripts:
            with self.subTest(expected=expected), self.assertRaises(m.EmbeddingError) as caught:
                m._run_child([sys.executable, "-I", "-B", "-c", script], b"{}", timeout)
            self.assertEqual(caught.exception.code, expected)
            self.assertNotIn("secret", str(caught.exception))

    def test_child_environment_does_not_inherit_credentials_or_proxy(self):
        m = self.module()
        from unittest.mock import patch
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret", "HTTPS_PROXY": "test-proxy"}):
            result = m._run_child([sys.executable, "-I", "-B", "-c",
                "import json,os; print(json.dumps({'secret': 'DEEPSEEK_API_KEY' in os.environ,"
                "'proxy': 'HTTPS_PROXY' in os.environ, 'offline': os.environ.get('HF_HUB_OFFLINE')}))"], b"{}", 2)
        self.assertEqual(result, {"secret": False, "proxy": False, "offline": "1"})

    def test_encoder_disables_native_arena_to_limit_retained_memory(self):
        m = self.module()
        from types import SimpleNamespace
        from unittest.mock import patch

        class ConfigurationObserved(Exception):
            pass

        def constructor(*args, **kwargs):
            self.assertIs(kwargs.get("enable_cpu_mem_arena", True), False)
            raise ConfigurationObserved()

        # Stop at the native loading boundary; the capped real-model probe separately
        # checks the resulting ORT option, memory and unchanged complete vectors.
        with patch.object(m, "_verify_model"), \
                patch.object(m.importlib.metadata, "version", side_effect=m.VERSIONS.__getitem__), \
                patch.dict(sys.modules, {"fastembed": SimpleNamespace(TextEmbedding=constructor)}):
            with self.assertRaises(ConfigurationObserved):
                m._encode([{"chunk_id": "a"*64, "text": "Artificial memory policy check."}], "/unused-model")

    def test_process_group_cleanup_also_terminates_descendants(self):
        m = self.module()
        from pathlib import Path
        import signal
        import tempfile
        import time
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "descendant.pid"
            for tail in ("time.sleep(10)", "print('{}')", "sys.exit(1)"):
                script = ("import subprocess,sys,time,pathlib; "
                          "p=subprocess.Popen([sys.executable,'-I','-B','-c','import time;time.sleep(10)']); "
                          "pathlib.Path(" + repr(str(pid_file)) + ").write_text(str(p.pid)); " + tail)
                try:
                    try:
                        m._run_child([sys.executable, "-I", "-B", "-c", script], b"{}", 0.5)
                    except m.EmbeddingError:
                        pass  # Existing test separately verifies exact error codes.
                    self.assertTrue(pid_file.exists(), "descendant was not started")
                    pid = int(pid_file.read_text())
                    state = Path("/proc") / str(pid) / "stat"
                    def terminated():
                        try:
                            return state.read_text().split()[2] == "Z"
                        except (FileNotFoundError, ProcessLookupError):
                            return True
                    deadline = time.monotonic() + 1
                    while not terminated() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(terminated())
                finally:
                    if pid_file.exists():
                        try:
                            os.kill(int(pid_file.read_text()), signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        pid_file.unlink()


if __name__ == "__main__":
    unittest.main()
