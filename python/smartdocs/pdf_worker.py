"""Private subprocess entry point; accepts PDF bytes, never a document path."""

import io
import json
import logging
import resource
import sys


class _WarningFlag(logging.Handler):
    seen = False

    def emit(self, record):
        # Keep no message or exception: PDF warnings may contain source text.
        self.seen = True


def _apply_resource_limits():
    for kind, ceiling in ((resource.RLIMIT_AS, 256 * 1024 * 1024),
                          (resource.RLIMIT_CPU, 10),
                          (resource.RLIMIT_FSIZE, 16 * 1024 * 1024),
                          (resource.RLIMIT_CORE, 0)):
        inherited = resource.getrlimit(kind)
        limit = min([ceiling] + [value for value in inherited if value != resource.RLIM_INFINITY])
        resource.setrlimit(kind, (limit, limit))


def _content_streams_within_limit(page, limit):
    """Count page content and unique nested Form streams before extraction."""
    content = page.get_contents()
    total = len(content.get_data()) if content is not None else 0
    if total > limit:
        return False
    pending = [page.get("/Resources", {})]
    seen = set()
    while pending:
        resources = pending.pop()
        resources = resources.get_object() if hasattr(resources, "get_object") else resources
        objects = resources.get("/XObject", {})
        objects = objects.get_object() if hasattr(objects, "get_object") else objects
        for reference in objects.values():
            form = reference.get_object()
            if form.get("/Subtype") == "/Image" or id(form) in seen:
                continue
            seen.add(id(form))
            total += len(form.get_data())
            if total > limit:
                return False
            pending.append(form.get("/Resources", {}))
    # Cyclic forms cannot loop this traversal; extraction warnings reject them.
    return True


def _extract(data, limits):
    try:
        import pypdf
        from pypdf.errors import DependencyError
    except ImportError:
        return {"error": "pdf_dependency_unavailable"}
    if pypdf.__version__ != "6.21.0":
        return {"error": "pdf_dependency_unavailable"}
    warning_flag = _WarningFlag(level=logging.WARNING)
    logger = logging.getLogger("pypdf")
    logger.setLevel(logging.WARNING)
    logger.addHandler(warning_flag)
    try:
        reader = pypdf.PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    return {"error": "encrypted_pdf"}
            except DependencyError:
                return {"error": "encrypted_pdf"}
        if len(reader.pages) > limits["max_pages"]:
            return {"error": "too_many_pages"}
        pages = []
        total_chars = 0
        for page in reader.pages:
            if not _content_streams_within_limit(page, limits["max_stream_bytes"]):
                return {"error": "page_stream_too_large"}
            text = (page.extract_text() or "").removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
            if warning_flag.seen:
                return {"error": "invalid_pdf"}
            total_chars += len(text)
            if total_chars > limits["max_chars"]:
                return {"error": "text_too_large"}
            pages.append(text)
        return {"pages": pages} if pages else {"error": "invalid_pdf"}
    except MemoryError:
        return {"error": "pdf_resource_limit"}
    except Exception:
        # Third-party exceptions may contain raw document content or paths.
        return {"error": "invalid_pdf"}
    finally:
        logger.removeHandler(warning_flag)


def main():
    try:
        _apply_resource_limits()
        limits = json.loads(sys.argv[1])
        data = sys.stdin.buffer.read(limits["max_bytes"] + 1)
        result = ({"error": "input_too_large"} if len(data) > limits["max_bytes"]
                  else _extract(data, limits))
        encoded = json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        sys.stdout.buffer.write(encoded)
    except MemoryError:
        sys.stdout.buffer.write(b'{"error":"pdf_resource_limit"}')


if __name__ == "__main__":
    main()
