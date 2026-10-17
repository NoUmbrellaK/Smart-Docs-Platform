import importlib
import importlib.util
import json
import math
import os
import sys
import unittest
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import tempfile
from unittest.mock import patch


class EmbeddingTests(unittest.TestCase):
    def stage_fixtures(self):
        chunks = [{"chunk_id": "a"*64, "text": "abc"}]
        common = {"chunk_id": "a"*64, "text_sha256": sha256(b"abc").hexdigest()}
        prepared = {"embedding_revision": self.module().EMBEDDING_REVISION, "chunks": [{
            **common, "windows": [{"char_start": 0, "char_end": 3,
                                   "ids": [0, 123, 2], "mask": [1, 1, 1]}]}]}
        result = {"embedding_revision": self.module().EMBEDDING_REVISION, "chunks": [{
            **common, "windows": [{"char_start": 0, "char_end": 3,
                                   "vector": [1.0] + [0.0]*383}]}]}
        return chunks, prepared, result

    def test_stages_share_deadline_and_lock_until_validated_result(self):
        m = self.module()
        from smartdocs.worker import WorkerError, worker_lock
        chunks, prepared, result = self.stage_fixtures()
        clock = [100.0]
        stages = []
        with tempfile.TemporaryDirectory() as directory:
            lock = str(Path(directory)/"model.lock")
            def child(command, payload, timeout, **kwargs):
                with self.assertRaises(WorkerError):
                    with worker_lock(lock):
                        pass
                stages.append(command[-1])
                if len(stages) == 1:
                    self.assertEqual(json.loads(payload), chunks)
                    self.assertEqual(timeout, 60)
                    clock[0] += 41
                    return prepared
                self.assertEqual(json.loads(payload), {"chunks": chunks, "prepared": prepared})
                self.assertEqual(timeout, 19)
                return result
            with patch.object(m.os, "geteuid", return_value=65534), \
                    patch.object(m, "monotonic", side_effect=lambda: clock[0], create=True), \
                    patch.object(m, "_run_child", side_effect=child):
                try:
                    actual = m.encode_chunks(chunks, model_dir="/model", python_executable=sys.executable,
                                             lock_path=lock)
                except m.EmbeddingError as error:
                    self.fail("valid staged request rejected: " + error.code)
                self.assertEqual(actual, result)
            self.assertEqual(stages, ["tokenize", "infer"])
            with worker_lock(lock):
                pass

    def test_first_stage_timeout_never_starts_inference_and_releases_lock(self):
        m = self.module()
        from smartdocs.worker import worker_lock
        chunks, prepared, _ = self.stage_fixtures()
        clock = [0.0]
        calls = []
        def child(*args, **kwargs):
            calls.append(args)
            clock[0] = 61
            return prepared
        with tempfile.TemporaryDirectory() as directory:
            lock = str(Path(directory)/"model.lock")
            with patch.object(m.os, "geteuid", return_value=65534), \
                    patch.object(m, "monotonic", side_effect=lambda: clock[0], create=True), \
                    patch.object(m, "_run_child", side_effect=child):
                with self.assertRaises(m.EmbeddingError) as caught:
                    m.encode_chunks(chunks, model_dir="/model", python_executable=sys.executable, lock_path=lock)
                self.assertEqual(caught.exception.code, "embedding_timeout")
            self.assertEqual(len(calls), 1)
            with worker_lock(lock):
                pass

    def test_malformed_stage_output_is_rejected_before_inference(self):
        m = self.module()
        chunks, prepared, _ = self.stage_fixtures()
        changes = [lambda p: p.update(embedding_revision="wrong"),
                   lambda p: p["chunks"][0].update(chunk_id="b"*64),
                   lambda p: p["chunks"][0].update(text_sha256="0"*64),
                   lambda p: p["chunks"][0]["windows"][0].update(char_end=2),
                   lambda p: p["chunks"][0]["windows"][0].update(ids=[True]),
                   lambda p: p["chunks"][0]["windows"][0].update(ids=[250037]*3),
                   lambda p: p["chunks"][0]["windows"][0].update(ids=[1]*129, mask=[1]*129),
                   lambda p: p["chunks"][0]["windows"][0].update(mask=[1, 0, 1])]
        for change in changes:
            bad = deepcopy(prepared)
            change(bad)
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory, \
                    patch.object(m.os, "geteuid", return_value=65534), \
                    patch.object(m, "_run_child", return_value=bad) as child:
                with self.assertRaises(m.EmbeddingError) as caught:
                    m.encode_chunks(chunks, model_dir="/model", python_executable=sys.executable,
                                    lock_path=str(Path(directory)/"model.lock"))
                self.assertEqual(caught.exception.code, "embedding_invalid_result")
                self.assertEqual(child.call_count, 1)

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
            options = kwargs["sess_options"]
            self.assertIs(options.enable_cpu_mem_arena, False)
            self.assertEqual((options.intra_op_num_threads, options.inter_op_num_threads), (1, 1))
            self.assertEqual(kwargs["providers"], ["CPUExecutionProvider"])
            raise ConfigurationObserved()

        # Stop at the native loading boundary; the capped real-model probe separately
        # checks the resulting ORT option, memory and unchanged complete vectors.
        chunks, prepared, _ = self.stage_fixtures()
        with patch.object(m, "_verify_model"), patch.object(m, "_verify_dependencies"), \
                patch.dict(sys.modules, {
                    "numpy": SimpleNamespace(),
                    "fastembed.common.utils": SimpleNamespace(mean_pooling=None),
                    "onnxruntime": SimpleNamespace(SessionOptions=SimpleNamespace,
                        GraphOptimizationLevel=SimpleNamespace(ORT_ENABLE_ALL=99),
                        InferenceSession=constructor)}):
            with self.assertRaises(ConfigurationObserved):
                m._infer(chunks, prepared, "/unused-model")

    def test_real_stages_run_serially_and_reap_after_second_stage_timeout(self):
        m = self.module()
        from smartdocs.worker import worker_lock
        chunks, prepared, result = self.stage_fixtures()
        run_child = m._run_child
        for fail_stage in (None, "tokenize", "infer"):
            with self.subTest(fail_stage=fail_stage), tempfile.TemporaryDirectory() as directory:
                lock = str(Path(directory)/"model.lock")
                stages = []
                pids = []
                def run(command, payload, timeout, **kwargs):
                    stage = command[-1]
                    # No first-stage process may remain when inference starts.
                    self.assertTrue(all(not Path('/proc', str(pid)).exists() for pid in pids))
                    stages.append(stage)
                    pid_file = Path(directory)/(stage+'.pid')
                    body = prepared if stage == "tokenize" else result
                    script = ("import json,os,pathlib,sys,time; sys.stdin.buffer.read(); "
                        "pathlib.Path(" + repr(str(pid_file)) + ").write_text(str(os.getpid())); "
                        + ("time.sleep(10); " if stage == fail_stage else "")
                        + "print(" + repr(json.dumps(body)) + ")")
                    try:
                        return run_child([sys.executable, '-I', '-B', '-c', script], payload,
                                         min(timeout, 0.3) if stage == fail_stage else timeout, **kwargs)
                    finally:
                        if pid_file.exists():
                            pids.append(int(pid_file.read_text()))
                with patch.object(m.os, 'geteuid', return_value=65534), \
                        patch.object(m, '_run_child', side_effect=run):
                    if fail_stage:
                        with self.assertRaises(m.EmbeddingError) as caught:
                            m.encode_chunks(chunks, model_dir='/model', python_executable=sys.executable,
                                            lock_path=lock)
                        self.assertEqual(caught.exception.code, 'embedding_timeout')
                    else:
                        self.assertEqual(m.encode_chunks(chunks, model_dir='/model',
                            python_executable=sys.executable, lock_path=lock), result)
                self.assertEqual(stages, ['tokenize'] if fail_stage == 'tokenize' else ['tokenize', 'infer'])
                self.assertTrue(pids)
                self.assertTrue(all(not Path('/proc', str(pid)).exists() for pid in pids))
                with worker_lock(lock):
                    pass

    def test_second_stage_overrun_cannot_return_success(self):
        m = self.module()
        chunks, prepared, result = self.stage_fixtures()
        clock = [0.0]
        def run(command, payload, timeout, **kwargs):
            if command[-1] == 'tokenize':
                clock[0] = 40
                return prepared
            clock[0] = 61
            return result
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(m.os, 'geteuid', return_value=65534), \
                patch.object(m, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(m, '_run_child', side_effect=run):
            with self.assertRaises(m.EmbeddingError) as caught:
                m.encode_chunks(chunks, model_dir='/model', python_executable=sys.executable,
                                lock_path=str(Path(directory)/'model.lock'))
            self.assertEqual(caught.exception.code, 'embedding_timeout')

    def test_interrupt_during_either_stage_reaps_child_and_releases_lock(self):
        import signal
        m = self.module()
        from smartdocs.worker import worker_lock
        chunks, prepared, _ = self.stage_fixtures()
        run_child = m._run_child
        def interrupt(signum, frame):
            raise KeyboardInterrupt()
        previous = signal.signal(signal.SIGALRM, interrupt)
        try:
            for interrupted_stage in ('tokenize', 'infer'):
                with self.subTest(stage=interrupted_stage), tempfile.TemporaryDirectory() as directory:
                    pid_file = Path(directory)/'child.pid'
                    lock = str(Path(directory)/'model.lock')
                    def run(command, payload, timeout, **kwargs):
                        if command[-1] != interrupted_stage:
                            return prepared
                        script = ("import os,pathlib,time; pathlib.Path(" + repr(str(pid_file))
                                  + ").write_text(str(os.getpid())); time.sleep(10)")
                        signal.setitimer(signal.ITIMER_REAL, 0.3)
                        return run_child([sys.executable, '-I', '-B', '-c', script], payload, timeout, **kwargs)
                    with patch.object(m.os, 'geteuid', return_value=65534), \
                            patch.object(m, '_run_child', side_effect=run), self.assertRaises(KeyboardInterrupt):
                        m.encode_chunks(chunks, model_dir='/model', python_executable=sys.executable, lock_path=lock)
                    signal.setitimer(signal.ITIMER_REAL, 0)
                    self.assertTrue(pid_file.exists())
                    self.assertFalse(Path('/proc', pid_file.read_text()).exists())
                    with worker_lock(lock):
                        pass
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)

    def test_missing_stage_dependency_returns_safe_dependency_error(self):
        import io
        from types import SimpleNamespace
        m = self.module()
        chunks, prepared, _ = self.stage_fixtures()
        for mode in ('tokenize', 'infer'):
            payload = chunks if mode == 'tokenize' else {'chunks': chunks, 'prepared': prepared}
            output = io.BytesIO()
            with self.subTest(mode=mode), patch.object(m, '_set_limits'), \
                    patch.object(m, '_'+mode, side_effect=ImportError('private deployment detail')), \
                    patch.object(sys, 'argv', ['embeddings.py', '/model', mode]), \
                    patch.object(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode()))), \
                    patch.object(sys, 'stdout', SimpleNamespace(buffer=output)):
                m._main()
            self.assertEqual(json.loads(output.getvalue()), {'error': 'embedding_dependency_unavailable'})

    def test_tokenizer_stdout_is_bounded_before_json_parsing(self):
        m = self.module()
        chunks, _, _ = self.stage_fixtures()
        popen = m.subprocess.Popen
        def large_stage_output(command, **kwargs):
            return popen([sys.executable, '-I', '-B', '-c',
                "import sys; sys.stdin.buffer.read(); print(' '*(256*1024)+'{}')"], **kwargs)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(m.os, 'geteuid', return_value=65534), \
                patch.object(m.subprocess, 'Popen', side_effect=large_stage_output):
            with self.assertRaises(m.EmbeddingError) as caught:
                m.encode_chunks(chunks, model_dir='/model', python_executable=sys.executable,
                                lock_path=str(Path(directory)/'model.lock'))
            self.assertEqual(caught.exception.code, 'embedding_result_too_large')

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
