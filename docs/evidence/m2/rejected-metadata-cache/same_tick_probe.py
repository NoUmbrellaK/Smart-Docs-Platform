"""Small filesystem experiment, not a model verification implementation."""
from hashlib import sha256
import json
from pathlib import Path
import tempfile


def stamp(path):
    s = path.stat()
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


with tempfile.TemporaryDirectory(prefix="smartdocs-stat-probe-") as directory:
    path = Path(directory) / "fixture"
    for attempt in range(1, 101):
        path.write_bytes(b"abc")
        original_hash = sha256(path.read_bytes()).hexdigest()
        before = stamp(path)
        path.write_bytes(b"bad")
        after = stamp(path)
        changed_hash = sha256(path.read_bytes()).hexdigest()
        if before == after and original_hash != changed_hash:
            print(json.dumps({"metadata_collision_observed": True, "attempt": attempt,
                "before": before, "after": after, "original_sha256": original_hash,
                "changed_sha256": changed_hash}))
            break
    else:
        print(json.dumps({"metadata_collision_observed": False, "attempts": 100,
                          "note": "absence here does not prove metadata is an integrity check"}))
