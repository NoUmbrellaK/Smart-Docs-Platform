"""Real PDF extraction and bounded subprocess behavior on synthetic documents."""

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject
from smartdocs.text_parser import ParseError

spec = importlib.util.find_spec("smartdocs.pdf_parser")
if spec is not None:
    from smartdocs import pdf_parser
    from smartdocs.pdf_parser import PdfLimits, parse_pdf

SAMPLE = (Path(__file__).resolve().parents[1] / "fixtures/m1/sample.pdf").read_bytes()


def fixture(*, blank=False, blank_first=False, password=None, same_page=False):
    writer = PdfWriter()
    if blank or blank_first:
        writer.add_blank_page(width=300, height=300)
    if not blank:
        reader = PdfReader(io.BytesIO(SAMPLE))
        writer.add_page(reader.pages[0])
        writer.add_page(reader.pages[0 if same_page else 1])
    if password is not None:
        writer.encrypt(password, algorithm="RC4-128")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def form_fixture(*, missing=False, cyclic=False):
    writer = PdfWriter()
    page = writer.add_page(PdfReader(io.BytesIO(SAMPLE)).pages[0])
    content = DecodedStreamObject()
    content.set_data(page.get_contents().get_data() + b"\n/Outer Do\n")
    page[NameObject("/Contents")] = writer._add_object(content)
    if not missing:
        inner = DecodedStreamObject()
        inner.set_data(b"BT /F1 12 Tf 20 20 Td (FORM TEXT) Tj ET\n" + b" " * 1000
                       + (b"/Self Do\n" if cyclic else b""))
        inner.update({NameObject("/Type"): NameObject("/XObject"),
                      NameObject("/Subtype"): NameObject("/Form"),
                      NameObject("/BBox"): ArrayObject([NumberObject(x) for x in (0, 0, 300, 300)]),
                      NameObject("/Resources"): DictionaryObject({
                          NameObject("/Font"): page["/Resources"]["/Font"]})})
        inner = inner.flate_encode()
        inner_ref = writer._add_object(inner)
        if cyclic:
            inner["/Resources"][NameObject("/XObject")] = DictionaryObject({NameObject("/Self"): inner_ref})
        outer = DecodedStreamObject()
        outer.set_data(b"/Inner Do\n")
        outer.update({NameObject("/Type"): NameObject("/XObject"),
                      NameObject("/Subtype"): NameObject("/Form"),
                      NameObject("/BBox"): inner["/BBox"],
                      NameObject("/Resources"): DictionaryObject({
                          NameObject("/XObject"): DictionaryObject({NameObject("/Inner"): inner_ref})})})
        page["/Resources"][NameObject("/XObject")] = DictionaryObject({
            NameObject("/Outer"): writer._add_object(outer)})
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class PdfParserTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(spec, "M2 PDF parser is not implemented")

    def parse(self, data=SAMPLE, **kwargs):
        return parse_pdf(data, file_id="f1", version_id="v1", **kwargs)

    def assert_error(self, code, data=SAMPLE, **kwargs):
        with self.assertRaises(ParseError) as caught:
            self.parse(data, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def test_real_two_page_pdf_uses_physical_pages_and_page_local_offsets(self):
        result = self.parse()
        self.assertEqual(result.page_count, 2)
        self.assertEqual(result.pages_without_text, ())
        self.assertEqual([(c.page, c.text, c.char_start, c.char_end, c.line_start, c.line_end)
                          for c in result.chunks], [(1, "PAGE ONE", 0, 8, 1, 1),
                                                   (2, "PAGE TWO", 0, 8, 1, 1)])

    def test_chinese_pdf_preserves_text_physical_pages_and_unicode_offsets(self):
        data = (Path(__file__).resolve().parents[1] / "fixtures/m2/chinese.pdf").read_bytes()
        result = self.parse(data)
        self.assertEqual(result.page_count, 2)
        self.assertEqual(result.pages_without_text, ())
        self.assertEqual([(c.page, c.text, c.char_start, c.char_end, c.line_start, c.line_end)
                          for c in result.chunks], [(1, "部署说明\n数据库备份", 0, 10, 1, 2),
                                                   (2, "恢复步骤\n验证数据完整性", 0, 12, 1, 2)])

    def test_image_only_pdf_is_not_misreported_as_parsed_text(self):
        data = (Path(__file__).resolve().parents[1] / "fixtures/m2/scanned.pdf").read_bytes()
        # Check this is a painted image, not the existing blank-page fixture.
        page = PdfReader(io.BytesIO(data), strict=True).pages[0]
        self.assertIn(b" Do", page.get_contents().get_data())
        images = [obj.get_object() for obj in page["/Resources"]["/XObject"].values()]
        self.assertTrue(any(obj.get("/Subtype") == "/Image" for obj in images))
        self.assert_error("no_extractable_text", data)

    def test_stable_citations_bind_pdf_file_version_content_and_page(self):
        first = self.parse()
        self.assertEqual(first, self.parse())
        for changes in ({"file_id": "f2"}, {"version_id": "v2"}):
            args = dict(file_id="f1", version_id="v1")
            args.update(changes)
            other = parse_pdf(SAMPLE, **args)
            self.assertNotEqual(first.chunks[0].chunk_id, other.chunks[0].chunk_id)
        modified = self.parse(SAMPLE + b"\n")
        self.assertNotEqual(first.chunks[0].source_sha256, modified.chunks[0].source_sha256)
        self.assertNotEqual(first.chunks[0].chunk_id, modified.chunks[0].chunk_id)
        repeated_page = self.parse(fixture(same_page=True)).chunks
        self.assertEqual(repeated_page[0].text, repeated_page[1].text)
        self.assertNotEqual(repeated_page[0].chunk_id, repeated_page[1].chunk_id)
        self.assertEqual(first.chunks[0].source_sha256, first.chunks[1].source_sha256)

    def test_empty_pages_preserve_physical_page_numbers_and_coverage(self):
        result = self.parse(fixture(blank_first=True))
        self.assertEqual(result.page_count, 3)
        self.assertEqual(result.pages_without_text, (1,))
        self.assertEqual([c.page for c in result.chunks], [2, 3])
        self.assert_error("no_extractable_text", fixture(blank=True))

    def test_encrypted_unreadable_pdf_fails_but_empty_password_is_readable(self):
        self.assert_error("encrypted_pdf", fixture(password="test-only-password"))
        self.assertEqual(self.parse(fixture(password="")).page_count, 2)

    def test_malformed_pdf_never_returns_success_or_content_in_error(self):
        for data in (b"", b"private text", b"%PDF-1.4\nprivate broken content", SAMPLE[:350]):
            with self.subTest(length=len(data)):
                self.assert_error("invalid_pdf", data)

    def test_file_page_text_chunk_and_stream_limits_are_enforced(self):
        self.assertEqual(self.parse(limits=PdfLimits(max_bytes=len(SAMPLE))).page_count, 2)
        self.assert_error("input_too_large", limits=PdfLimits(max_bytes=len(SAMPLE) - 1))
        self.assertEqual(self.parse(limits=PdfLimits(max_pages=2)).page_count, 2)
        self.assert_error("too_many_pages", limits=PdfLimits(max_pages=1))
        self.assertEqual(len(self.parse(limits=PdfLimits(max_chars=16)).chunks), 2)
        self.assert_error("text_too_large", limits=PdfLimits(max_chars=15))
        result = self.parse(limits=PdfLimits(chunk_chars=4, max_chunks=4))
        self.assertEqual([(c.page, c.text) for c in result.chunks],
                         [(1, "PAGE"), (1, " ONE"), (2, "PAGE"), (2, " TWO")])
        self.assert_error("too_many_chunks", limits=PdfLimits(chunk_chars=4, max_chunks=3))
        self.assertEqual(self.parse(limits=PdfLimits(max_stream_bytes=111)).page_count, 2)
        self.assert_error("page_stream_too_large", limits=PdfLimits(max_stream_bytes=110))

    def test_resource_and_processing_limits_cannot_be_disabled_or_increased(self):
        for field, values in {
            "max_bytes": [0, 8388609], "max_chars": [0, 2000001],
            "max_chunks": [0, 2001], "max_pages": [0, 301, True, 1.5],
            "max_stream_bytes": [0, 16777217],
            "timeout_seconds": [0, -1, 16, float("inf"), float("nan"), True, "1"],
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ParseError) as caught:
                        PdfLimits(**{field: value})
                    self.assertEqual(caught.exception.code, "invalid_input")
        self.assert_error("invalid_input", "not bytes")
        self.assert_error("invalid_input", limits=None)
        with self.assertRaises(ParseError):
            parse_pdf(SAMPLE, file_id="bad\nname", version_id="v1")

    def test_real_timeout_kills_and_reaps_child(self):
        real_popen = subprocess.Popen
        children = []

        def slow_child(_command, **kwargs):
            child = real_popen([sys.executable, "-I", "-c", "import time; time.sleep(30)"], **kwargs)
            children.append(child)
            return child

        with mock.patch.object(pdf_parser.subprocess, "Popen", side_effect=slow_child):
            self.assert_error("pdf_timeout", limits=PdfLimits(timeout_seconds=0.1))
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].returncode)
        with self.assertRaises(ChildProcessError):
            os.waitpid(children[0].pid, os.WNOHANG)

    def test_child_receives_no_secret_environment_and_ignores_python_injection(self):
        real_popen = subprocess.Popen
        observations = []

        def observe(command, **kwargs):
            observations.append((command, kwargs.get("env"), kwargs.get("close_fds")))
            return real_popen(command, **kwargs)

        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-not-a-real-key",
                                         "PYTHONPATH": "/invalid-injection"}):
            with mock.patch.object(pdf_parser.subprocess, "Popen", side_effect=observe):
                self.assertEqual(self.parse().page_count, 2)
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0][1], {})
        self.assertTrue(observations[0][2])
        self.assertIn("-I", observations[0][0])

    def test_real_child_crash_and_bad_response_are_safe_failures(self):
        real_popen = subprocess.Popen
        for program in ("raise SystemExit(7)", "print('private invalid output')",
                        "print('{\"pages\": [1]}')", "print('{\"error\": \"private text\"}')",
                        "print('[' * 2000 + '0' + ']' * 2000)"):
            with self.subTest(program=program):
                def child(_command, **kwargs):
                    return real_popen([sys.executable, "-I", "-c", program], **kwargs)
                with mock.patch.object(pdf_parser.subprocess, "Popen", side_effect=child):
                    self.assert_error("pdf_worker_failed")

    def test_invalid_extracted_unicode_has_a_safe_error_not_raw_text(self):
        with mock.patch.object(pdf_parser, "_extract_pages", return_value=["private\ud800text"]):
            self.assert_error("invalid_pdf")

    def test_missing_pdf_dependency_is_explicit_without_skipping_test(self):
        real_popen = subprocess.Popen

        def no_site_packages(command, **kwargs):
            return real_popen(command[:1] + ["-S"] + command[1:], **kwargs)

        with mock.patch.object(pdf_parser.subprocess, "Popen", side_effect=no_site_packages):
            self.assert_error("pdf_dependency_unavailable")

    def test_library_warning_about_missing_or_cyclic_form_cannot_publish_partial_text(self):
        self.assert_error("invalid_pdf", form_fixture(missing=True))
        self.assert_error("invalid_pdf", form_fixture(cyclic=True))

    def test_nested_compressed_form_streams_are_included_in_page_limit(self):
        data = form_fixture()
        result = self.parse(data, limits=PdfLimits(max_stream_bytes=2000))
        self.assertIn("FORM TEXT", "".join(c.text for c in result.chunks))
        self.assert_error("page_stream_too_large", data, limits=PdfLimits(max_stream_bytes=200))

    def test_kernel_memory_limit_blocks_allocation_in_real_child(self):
        worker = Path(pdf_parser.__file__).with_name("pdf_worker.py")
        program = (
            "import runpy,resource,sys,json; "
            "m=runpy.run_path(sys.argv[1]); m['_apply_resource_limits'](); "
            "print(json.dumps([resource.getrlimit(resource.RLIMIT_AS), "
            "resource.getrlimit(resource.RLIMIT_CPU), resource.getrlimit(resource.RLIMIT_FSIZE)]), flush=True); "
            "bytearray(300*1024*1024)"
        )
        result = subprocess.run([sys.executable, "-I", "-B", "-c", program, str(worker)],
                                capture_output=True, env={}, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"MemoryError", result.stderr)
        limits = json.loads(result.stdout)
        self.assertEqual(limits, [[268435456, 268435456], [10, 10], [16777216, 16777216]])


if __name__ == "__main__":
    unittest.main()
