import errno
import importlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class ResidentSnapshotTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("smartdocs.resident_embeddings"),
                             "verified resident encoder is missing")
        return importlib.import_module("smartdocs.resident_embeddings")

    def test_verified_snapshot_is_immutable_and_independent_of_source(self):
        m = self.module()
        hashes = {"fixture": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"}
        with tempfile.TemporaryDirectory() as directory, patch.dict(m.base.MODEL_HASHES, hashes, clear=True):
            path = Path(directory) / "fixture"
            path.write_bytes(b"abc")
            with m.verified_snapshot(directory, "fixture") as snapshot:
                fd = snapshot.fileno()
                path.write_bytes(b"bad")
                self.assertEqual(os.pread(fd, 4, 0), b"abc")
                for mutate in (lambda: os.pwrite(fd, b"bad", 0), lambda: os.ftruncate(fd, 0)):
                    with self.assertRaises(OSError) as caught:
                        mutate()
                    self.assertEqual(caught.exception.errno, errno.EPERM)
            with self.assertRaises(OSError) as caught:
                os.fstat(fd)
            self.assertEqual(caught.exception.errno, errno.EBADF)
            with self.assertRaises(m.base.EmbeddingError) as caught:
                with m.verified_snapshot(directory, "fixture"):
                    self.fail("modified content was accepted")
            self.assertEqual(caught.exception.code, "embedding_model_unavailable")

    def test_snapshot_rejects_symlinks_nonregular_and_oversized_files(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as directory, patch.dict(m.base.MODEL_HASHES, {"fixture": "0"*64}, clear=True):
            path = Path(directory) / "fixture"
            for kind in ("symlink", "fifo", "directory", "oversized"):
                with self.subTest(kind=kind):
                    if kind == "symlink": path.symlink_to("missing")
                    elif kind == "fifo": os.mkfifo(path)
                    elif kind == "directory": path.mkdir()
                    else:
                        with path.open("wb") as out: out.truncate(256*1024*1024+1)
                    with self.assertRaises(m.base.EmbeddingError) as caught:
                        with m.verified_snapshot(directory, "fixture"):
                            self.fail("invalid artifact accepted")
                    self.assertEqual(caught.exception.code, "embedding_model_unavailable")
                    path.rmdir() if kind == "directory" else path.unlink()

    def test_failed_snapshot_does_not_leak_descriptors(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as directory, patch.dict(m.base.MODEL_HASHES, {"fixture": "0"*64}, clear=True):
            (Path(directory)/"fixture").write_bytes(b"bad")
            before = len(os.listdir("/proc/self/fd"))
            for _ in range(10):
                with self.assertRaises(m.base.EmbeddingError):
                    with m.verified_snapshot(directory, "fixture"):
                        self.fail("bad hash accepted")
            self.assertEqual(len(os.listdir("/proc/self/fd")), before)


if __name__ == "__main__":
    unittest.main()
