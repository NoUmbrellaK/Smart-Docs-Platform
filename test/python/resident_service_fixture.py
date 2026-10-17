"""Subprocess fault fixture: real supervisor/IPC, synthetic encoder only."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import socket
import struct
from unittest.mock import patch

from smartdocs import resident_service as service
from smartdocs import resident_embeddings
from smartdocs.embeddings import EMBEDDING_REVISION


class FixtureEncoder:
    def __init__(self, directory):
        self.directory = Path(directory)
        (self.directory/"pid").write_text(str(os.getpid()))
        time.sleep(float((self.directory/"delay").read_text()))
        if (self.directory/"fail").exists(): raise RuntimeError("private model path")

    def encode(self, chunks):
        (self.directory/"encoded").touch()
        return {"embedding_revision": EMBEDDING_REVISION, "chunks": [dict(
            chunk_id=c["chunk_id"], text_sha256=hashlib.sha256(c["text"].encode()).hexdigest(),
            windows=[dict(char_start=0, char_end=len(c["text"]), vector=[1.0]+[0.0]*383)]) for c in chunks]}


if sys.argv[1] == "child":
    if (Path(sys.argv[3])/"wire_fault").exists():
        directory = Path(sys.argv[3])
        (directory/"pid").write_text(str(os.getpid()))
        with socket.socket(fileno=int(sys.argv[2])) as channel:
            channel.sendall(struct.pack("!I", len(service.READY))+service.READY)
            channel.recv(65536)
            payload = (struct.pack("!I", 2097153) if (directory/"wire_fault").read_text() == "oversized"
                       else struct.pack("!I", 2)+b"{}")
            channel.sendall(payload)
            time.sleep(10)
        raise SystemExit(0)
    with patch.object(resident_embeddings, "ResidentEncoder", FixtureEncoder):
        service.child_main(int(sys.argv[2]), sys.argv[3])
else:
    directory, startup, request = sys.argv[2:]
    def command(executable, fd, model):
        return [executable, "-B", __file__, "child", str(fd), model]
    with patch.object(service, "_child_command", command):
        raise SystemExit(service.serve(directory+"/encoder.sock", directory, sys.executable,
                                       directory+"/model.lock", float(startup), float(request)))
