"""Root prepares tiny fixtures; nobody verifies them. No model loading or network."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))
from smartdocs import embeddings as m

# SHA256 of literal b'abc', independently fixed; no real model is altered.
HASHES = {"model_optimized.onnx": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"}


def main():
    assert os.geteuid() == 0, "only fixture preparation needs root"
    assert callable(getattr(m, "prepare_model", None)), "missing protected model receipt preparation"
    cases = []
    with tempfile.TemporaryDirectory(prefix="smartdocs-receipt-test-") as tmp:
        base = Path(tmp)
        base.chmod(0o755)
        def fixture(name):
            directory = base / name
            directory.mkdir(mode=0o755)
            model = directory / "model_optimized.onnx"
            model.write_bytes(b"abc")
            model.chmod(0o644)
            with patch.object(m, "MODEL_HASHES", HASHES):
                m.prepare_model(str(directory))
            return directory, model, directory / ".smartdocs-verified.json"

        def check(name, directory, expected, cached=False):
            script = """
import sys
from unittest.mock import patch
from smartdocs import embeddings as m
m.MODEL_HASHES = %r
try:
    with patch.object(m, '_hash_model', side_effect=AssertionError('unexpected model reread')) if %r else patch.dict({}, {}):
        m._verify_model(%r)
    code = 'ok'
except m.EmbeddingError as error:
    code = error.code
assert code == %r, (code, %r)
print('PASS ' + %r)
""" % (HASHES, cached, str(directory), expected, expected, name)
            result = subprocess.run(["runuser", "-u", "nobody", "--", sys.executable, "-B", "-c", script],
                env={"PATH": "/usr/sbin:/usr/bin:/bin", "PYTHONPATH": str(ROOT / "python")},
                capture_output=True, text=True, timeout=10)
            assert result.returncode == 0, name + ": " + result.stdout + result.stderr
            cases.append(name)

        directory, model, receipt = fixture("valid")
        check("protected_receipt_avoids_full_read", directory, "ok", cached=True)
        # Missing receipts keep the original complete-hash path.
        receipt.unlink()
        check("missing_receipt_full_hash", directory, "ok")
        model.write_bytes(b"bad")
        check("missing_receipt_bad_hash", directory, "embedding_model_unavailable")

        mutations = {
            "changed_same_size": lambda d, f, r: f.write_bytes(b"bad"),
            "replaced_inode": lambda d, f, r: (f.unlink(), f.write_bytes(b"abc")),
            "writable_model": lambda d, f, r: f.chmod(0o666),
            "untrusted_model_owner": lambda d, f, r: os.chown(f, 65534, 65534),
            "writable_parent": lambda d, f, r: d.chmod(0o777),
            "writable_receipt": lambda d, f, r: r.chmod(0o666),
            "untrusted_receipt_owner": lambda d, f, r: os.chown(r, 65534, 65534),
            "receipt_symlink": lambda d, f, r: (r.rename(d / "other"), r.symlink_to(d / "other")),
            "broken_receipt_symlink": lambda d, f, r: (r.unlink(), r.symlink_to(d / "absent")),
            "model_symlink": lambda d, f, r: (f.rename(d / "other"), f.symlink_to(d / "other")),
            "malformed_receipt": lambda d, f, r: r.write_text("{}"),
        }
        for name, mutate in mutations.items():
            directory, model, receipt = fixture(name)
            previous = model.stat()
            mutate(directory, model, receipt)
            if name == "changed_same_size":
                current = model.stat()
                print("same_size_timestamps", previous.st_mtime_ns, current.st_mtime_ns,
                      previous.st_ctime_ns, current.st_ctime_ns, flush=True)
            check(name, directory, "embedding_model_unavailable")
        directory, model, receipt = fixture("restored_mtime")
        saved = model.stat()
        model.write_bytes(b"bad")
        os.utime(model, ns=(saved.st_atime_ns, saved.st_mtime_ns))
        check("ctime_catches_restored_mtime", directory, "embedding_model_unavailable")
    print(json.dumps({"passed": True, "cases": cases, "count": len(cases)}))


if __name__ == "__main__":
    main()
