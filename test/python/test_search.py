import importlib.util
import unittest

from smartdocs.embeddings import EmbeddingError, EMBEDDING_REVISION


class SearchTests(unittest.TestCase):
    def search(self, *args):
        spec = importlib.util.find_spec("smartdocs.search")
        self.assertIsNotNone(spec, "query coordinator not implemented")
        return importlib.import_module("smartdocs.search").search_documents(*args)

    def test_encodes_query_only_after_backend_authorization(self):
        calls = []
        def backend(request):
            calls.append(request)
            return {"retrieval": "hybrid" if "query_embedding" in request else "keyword_only"}
        def encoder(chunks):
            self.assertEqual(len(calls), 1)
            self.assertEqual(chunks[0]["text"], "部署")
            return {"embedding_revision": EMBEDDING_REVISION, "chunks": [{
                "chunk_id": chunks[0]["chunk_id"], "text_sha256": chunks[0]["chunk_id"],
                "windows": [{"char_start": 0, "char_end": 2, "vector": [1.0]+[0.0]*383}]}]}
        request = {"query": "部署", "page_size": 2}
        self.assertEqual(self.search(backend, encoder, request), {"retrieval": "hybrid"})
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["mode"], "hybrid")
        self.assertEqual(request, {"query": "部署", "page_size": 2})

    def test_encoding_failure_rechecks_backend_instead_of_returning_stale_preflight(self):
        calls = []
        def backend(request):
            calls.append(request)
            return {"items": ["old"] if len(calls) == 1 else [], "retrieval": "keyword_only"}
        def encoder(chunks): raise EmbeddingError("embedding_timeout")
        self.assertEqual(self.search(backend, encoder, {"query": "部署"})["items"], [])
        self.assertEqual(len(calls), 2)
        self.assertNotIn("query_embedding", calls[-1])

    def test_denied_preflight_never_encodes(self):
        def backend(request): raise PermissionError("denied")
        def encoder(chunks): self.fail("unauthorized expensive encoding")
        with self.assertRaises(PermissionError): self.search(backend, encoder, {"query": "部署"})

    def test_permission_revoked_during_encoding_is_not_hidden_by_preflight(self):
        calls = []
        def backend(request):
            calls.append(request)
            if len(calls) == 2: raise PermissionError("revoked")
            return {"items": ["old"]}
        def encoder(chunks):
            return {"embedding_revision": EMBEDDING_REVISION, "chunks": [{
                "chunk_id": chunks[0]["chunk_id"], "text_sha256": chunks[0]["chunk_id"],
                "windows": [{"char_start": 0, "char_end": 2, "vector": [1.0]+[0.0]*383}]}]}
        with self.assertRaises(PermissionError): self.search(backend, encoder, {"query": "部署"})
        self.assertEqual(len(calls), 2)

    def test_model_arguments_cannot_supply_mode_vector_or_identity(self):
        def backend(request): self.fail("invalid request reached backend")
        def encoder(chunks): self.fail("invalid request reached encoder")
        for extra in ({"query_embedding": {}}, {"mode": "keyword"}, {"user_id": "fake"}):
            with self.assertRaises(ValueError): self.search(backend, encoder, {"query": "部署", **extra})


if __name__ == "__main__": unittest.main()
