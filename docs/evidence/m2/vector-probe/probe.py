"""Disposable, synthetic-only CPU embedding feasibility probe; not a service."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import resource
import socket
import sys
import time

assert os.geteuid() != 0, "Run this probe in the non-root capped unit"
assert os.environ.get("HF_HUB_OFFLINE") == "1", "Offline execution required"
network_interfaces = sorted(name for _, name in socket.if_nameindex())
assert network_interfaces == ["lo"], "Use PrivateNetwork=yes, not just offline library flags"
resource.setrlimit(resource.RLIMIT_CPU, (60, 65))
root = Path(sys.argv[1])
model_path = root / "model"
expected = {
    "model_optimized.onnx": "634d0f66c29dc934c8fa72b8a4fe91dd4d420a22f1d82a241058d4316e659a99",
    "tokenizer.json": "fa685fc160bbdbab64058d4fc91b60e62d207e8dc60b9af5c002c5ab946ded00",
    "config.json": "c8ec081fdad2df991bf5abbf18418fec7a5cdaa421f60ffb060a30040b8c376f",
    "tokenizer_config.json": "0666eebf692422757e1dddf3c9fb1ded73ba3dc726c5828671fc89e45bf3609f",
    "special_tokens_map.json": "8c785abebea9ae3257b61681b4e6fd8365ceafde980c21970d001e834cf10835",
}
for name, expected_hash in expected.items():
    digest = hashlib.sha256()
    with (model_path / name).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    assert digest.hexdigest() == expected_hash, name

started = time.perf_counter()
from fastembed import TextEmbedding
import numpy as np
import_seconds = time.perf_counter() - started

started = time.perf_counter()
model = TextEmbedding("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    threads=1, providers=["CPUExecutionProvider"], local_files_only=True,
    specific_model_path=str(model_path), cache_dir=str(root / "offline-cache"))
load_seconds = time.perf_counter() - started
assert model.model.model.get_providers() == ["CPUExecutionProvider"]

documents = [
    "部署应用前，请配置数据库连接地址、账号密码和服务监听端口。",
    "交接时应提供接口说明、设计文档、测试报告以及尚未完成的事项。",
    "开票抬头和报销金额需要财务审核。",
    "The backup procedure takes a database snapshot and copies immutable document files.",
    "To rotate credentials, replace the secret and restart the application.",
    "The team picnic is scheduled for Friday afternoon.",
]
queries = ["上线需要设置哪些参数？", "离职移交需要准备什么资料？",
           "How can I recover stored information after disk failure?",
           "How do I change an application password safely?"]
expected_top = [0, 1, 3, 4]
started = time.perf_counter()
vectors = np.stack(list(model.embed(documents + queries, batch_size=1, parallel=None)))
first_ten_seconds = time.perf_counter() - started
assert vectors.shape == (10, 384)
assert np.isfinite(vectors).all()
raw_norms = np.linalg.norm(vectors, axis=1)
assert (raw_norms > 0).all()
vectors = vectors / raw_norms[:, None]
assert np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5)
assert not np.allclose(vectors[0], vectors[2])
scores = vectors[6:] @ vectors[:6].T
rankings = np.argsort(-scores, axis=1)

started = time.perf_counter()
repeat = np.stack(list(model.embed(documents + queries, batch_size=1, parallel=None)))
repeat_ten_seconds = time.perf_counter() - started
repeat = repeat / np.linalg.norm(repeat, axis=1)[:, None]
assert np.allclose(vectors, repeat, atol=1e-6)
inference_peak_rss_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

# Intentionally observe truncation rather than accepting it as full coverage.
long_text = ("项目部署参数数据库连接地址配置说明。" * 100)[:1200]
tokenizer = model.model.tokenizer
truncation = tokenizer.truncation
tokenizer.no_truncation()
try:
    full_tokens = len(tokenizer.encode(long_text).ids)
finally:
    tokenizer.enable_truncation(**truncation)
limited_tokens = len(model.model.tokenizer.encode(long_text).ids)
assert full_tokens > limited_tokens
started = time.perf_counter()
long_vector = next(model.embed([long_text], batch_size=1, parallel=None))
long_seconds = time.perf_counter() - started
assert long_vector.shape == (384,) and np.isfinite(long_vector).all()

result = {
    "purpose": "synthetic-only disposable feasibility; not M2 acceptance",
    "model": "Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q",
    "model_revision": "faf4aa4225822f3bc6376869cb1164e8e3feedd0",
    "model_sha256": expected,
    "versions": {name: importlib.metadata.version(name)
                 for name in ("fastembed", "onnxruntime", "numpy", "tokenizers", "huggingface-hub")},
    "python": sys.version.split()[0], "uid": os.geteuid(),
    "network_interfaces": network_interfaces,
    "providers": model.model.model.get_providers(), "threads": 1, "batch_size": 1,
    "dimensions": 384, "finite_unit_vectors_after_explicit_normalization": True,
    "raw_norm_range": [float(raw_norms.min()), float(raw_norms.max())],
    "repeat_atol_1e_6": True,
    "documents": documents, "queries": queries, "expected_top_indices": expected_top,
    "actual_top_indices": rankings[:, 0].tolist(), "cosine_scores": scores.tolist(),
    "fixture_matches": int(np.sum(rankings[:, 0] == expected_top)), "fixture_total": 4,
    "long_input": {"characters": len(long_text), "untruncated_tokens": full_tokens,
                   "encoded_tokens": limited_tokens, "silently_truncated": True,
                   "may_publish_complete_chunk_index": False},
    "seconds": {"import": import_seconds, "load": load_seconds,
                "first_ten": first_ten_seconds, "repeat_ten": repeat_ten_seconds,
                "truncated_long": long_seconds},
    "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    "inference_peak_rss_kib_before_long_input": inference_peak_rss_kib,
    "total_process_cpu_seconds": resource.getrusage(resource.RUSAGE_SELF).ru_utime +
                                 resource.getrusage(resource.RUSAGE_SELF).ru_stime,
}
print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
