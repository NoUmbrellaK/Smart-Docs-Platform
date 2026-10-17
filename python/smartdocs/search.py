"""Query coordinator for a trusted, session-bound C++ search callable (not an MCP server)."""
import hashlib
import json

from .embeddings import EmbeddingError, validate_result


def search_documents(backend_search, encoder, arguments):
    """The caller binds project/session; neither may come from model arguments.

    encoder is the existing private Unix request_encoding callable. No credentials,
    network destinations or document text are passed to the query encoder.
    """
    if (not isinstance(arguments, dict) or "query" not in arguments
            or set(arguments)-{"query", "directory_id", "media_type", "page", "page_size"}):
        raise ValueError("invalid_request")
    request = {**arguments, "mode": "hybrid"}
    # Backend validation + authorization must precede expensive model execution.
    backend_search(request)
    query = arguments["query"]
    identity = hashlib.sha256(query.encode("utf-8")).hexdigest()
    chunks = [{"chunk_id": identity, "text": query}]
    try:
        result = encoder(chunks)
        validate_result(result, chunks)
        if (len(result["chunks"][0]["windows"]) > 8
                or len(json.dumps(result, allow_nan=False).encode()) > 120*1024):
            raise EmbeddingError("embedding_result_too_large")
        request["query_embedding"] = result
    except EmbeddingError:
        pass  # Backend reports query_embedding_unavailable; do not return stale preflight data.
    return backend_search(request)
