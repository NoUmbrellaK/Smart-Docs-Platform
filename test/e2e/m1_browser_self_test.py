"""Safe browser boundary tests: no server, browser, build or database starts."""

import copy
import hashlib
import importlib
import json
import os
import pathlib
import struct
import tempfile
import unittest
from unittest import mock
import zlib


def png_fixture(seed, mixed=False, toolbar_pixel=0):
    def chunk(kind, body):
        return (struct.pack('!I', len(body)) + kind + body +
                struct.pack('!I', zlib.crc32(kind + body)))
    # Deterministic color marker plus RGB noise. Synthetic, NOT render proof.
    color = bytes((217, 31, 31) if seed == 1 else (26, 51, 217))
    rows = []
    for y in range(256):
        row = bytearray(hashlib.sha256(f'{seed}:{y}'.encode()).digest() * 24)
        if 32 <= y < 224:
            shade = bytes((26, 51, 217)) if mixed and y >= 128 else color
            row[32 * 3:224 * 3] = shade * 192
        if y == 0:
            row[:3] = bytes((toolbar_pixel, 0, 0))
        rows.append(b'\0' + row)
    return (b'\x89PNG\r\n\x1a\n' +
            chunk(b'IHDR', struct.pack('!IIBBBBB', 256, 256, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(b''.join(rows))) + chunk(b'IEND', b''))


def browser_fixture():
    images = {f'pdf-page-{page}.png': png_fixture(page) for page in (1, 2)}
    return {
        'schema_version': 2, 'status': 'pass', 'assertions': 20,
        'browser': {'executable': 'chromium', 'version': '131.0.1.2'},
        'webdriver': {'executable': 'chromedriver', 'version': '131.0.1.2'},
        'headless': 'new', 'sandbox': 'enabled',
        'scenarios': {
            'refresh_resume_reselect': {
                'status': 'pass', 'assertions': 12, 'part_count': 3,
                'chunk_size_bytes': 16, 'file_size_bytes': 45,
                'confirmed_parts_before': [0], 'confirmed_bytes_before': 16,
                'confirmed_parts_after_mismatch': [0], 'mismatch_put_parts': [],
                'resume_put_parts': [1, 2], 'confirmed_parts_after': [0, 1, 2],
                'source_sha256': 'a' * 64, 'download_sha256': 'a' * 64,
                'reloads': 1, 'file_id': '1' * 32, 'version_id': '2' * 32,
                'processing_job_id': '3' * 32,
            },
            'protected_pdf_page_navigation': {
                'status': 'pass', 'assertions': 8, 'fixture_sha256': 'b' * 64,
                'pages': [{
                    'page': page,
                    'src': '/api/v1/projects/' + '1' * 32 + '/files/' + '2' * 32 +
                           '/versions/' + '3' * 32 + f'/content#page={page}',
                    'screenshot': f'pdf-page-{page}.png',
                    'sha256': hashlib.sha256(images[f'pdf-page-{page}.png']).hexdigest(),
                    'size_bytes': len(images[f'pdf-page-{page}.png']),
                    'document_sha256': hashlib.sha256(struct.pack('!II', 85, 85) +
                        bytes((217, 31, 31) if page == 1 else (26, 51, 217)) * 85 * 85).hexdigest(),
                } for page in (1, 2)],
            },
        },
    }, images


class BrowserEvidenceTest(unittest.TestCase):
    def driver(self):
        self.assertTrue(pathlib.Path(__file__).with_name('m1_browser_test.py').exists(),
                        'real-browser evidence driver is missing')
        return importlib.import_module('m1_browser_test')

    def test_cold_browser_start_gets_extra_time_but_commands_do_not_retry(self):
        browser = self.driver()
        viewer = browser.Browser.__new__(browser.Browser)
        viewer.port = 1234
        with mock.patch.object(browser.http.client, 'HTTPConnection') as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 200
            response.read.return_value = b'{"value": {}}'
            viewer.request('POST', '/session', {})
            self.assertEqual(connection.call_args.kwargs['timeout'], 120)
            viewer.request('GET', '/session/fixture/title')
            self.assertEqual(connection.call_args.kwargs['timeout'], 30)
            connection.return_value.getresponse.side_effect = TimeoutError('slow')
            connection.reset_mock()
            with self.assertRaises(TimeoutError):
                viewer.request('POST', '/session/fixture/click', {})
            connection.assert_called_once_with('127.0.0.1', 1234, timeout=30)
            connection.return_value.request.assert_called_once()
            connection.return_value.close.assert_called_once()

    def test_session_requests_blank_startup_without_disabling_sandbox_or_pdf(self):
        browser = self.driver()
        with mock.patch.object(browser.ServerProcess, '_free_port', return_value=1234):
            viewer = browser.Browser({'browser': ('chrome', {'version': '131.0.1.2'}),
                'webdriver': ('chromedriver', {'version': '131.0.1.2'})}, 'enabled', '/tmp/profile')
        def request(method, path, body=None):
            if (method, path) == ('GET', '/status'):
                return {'ready': True}
            if (method, path) == ('POST', '/session'):
                options = body['capabilities']['alwaysMatch']['goog:chromeOptions']
                self.assertEqual(options['prefs'].get('session.restore_on_startup'), 4)
                self.assertEqual(options['prefs'].get('session.startup_urls'), ['about:blank'])
                self.assertFalse(options['prefs']['plugins.always_open_pdf_externally'])
                self.assertNotIn('--no-sandbox', options['args'])
                return {'sessionId': 'fixture', 'capabilities': {'browserVersion': '131.0.1.2',
                    'chrome': {'chromedriverVersion': '131.0.1.2 fixture'}}}
            self.assertEqual((method, path), ('POST', '/session/fixture/timeouts'))
        with mock.patch.object(browser.subprocess, 'Popen') as process, \
                mock.patch.object(viewer, 'request', side_effect=request):
            process.return_value.poll.return_value = None
            viewer.start()

    def test_iframe_screenshot_scrolls_clipped_frame_into_outer_viewport(self):
        import base64
        browser = self.driver()
        content = png_fixture(2)
        for top, bottom, scroll in ((594, 1042, 578), (-24, 424, -40), (100, 548, None)):
            with self.subTest(top=top, bottom=bottom):
                viewer = browser.Browser.__new__(browser.Browser)
                viewer.inspect = mock.Mock(return_value={
                    'top': top, 'bottom': bottom, 'viewport': 857})
                viewer.find = mock.Mock(return_value='preview-element')
                viewer.command = mock.Mock(side_effect=lambda method, path, *args:
                    base64.b64encode(content).decode() if method == 'GET' else None)
                self.assertEqual(viewer.screenshot(), content)
                expected = []
                if scroll is not None:
                    expected.append(mock.call('POST', '/actions', {'actions': [{
                        'type': 'wheel', 'id': 'preview-scroll', 'actions': [{
                            'type': 'scroll', 'x': 0, 'y': 0, 'deltaX': 0,
                            'deltaY': scroll, 'duration': 250}]}]}))
                expected.append(mock.call('GET', '/element/preview-element/screenshot'))
                self.assertEqual(viewer.command.call_args_list, expected)

    def test_aggregate_rejects_mixed_pages_with_only_toolbar_changes(self):
        import m1_interrupt_test as harness
        evidence, _ = browser_fixture()
        with tempfile.TemporaryDirectory() as root:
            artifacts = pathlib.Path(root)
            for page in evidence['scenarios']['protected_pdf_page_navigation']['pages']:
                # Both viewports contain the same red/blue document. Only one
                # toolbar pixel changes; both whole-image hashes are truthful.
                content = png_fixture(1, mixed=True, toolbar_pixel=page['page'])
                (artifacts / page['screenshot']).write_bytes(content)
                page.update(sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content))
                page['document_sha256'] = hashlib.sha256(
                    struct.pack('!II', 85, 85) + bytes((217, 31, 31)) * 85 * 43 +
                    bytes((26, 51, 217)) * 85 * 42).hexdigest()
            with self.assertRaisesRegex(AssertionError, 'browser evidence'):
                harness.validate_browser_evidence(evidence, artifacts)

    def test_document_fingerprint_ignores_toolbar_pixels(self):
        browser = self.driver()
        first = browser.require_pdf_render(png_fixture(1, toolbar_pixel=1), 1)
        second = browser.require_pdf_render(png_fixture(1, toolbar_pixel=2), 1)
        self.assertNotEqual(first['sha256'], second['sha256'])
        # Center third is 85 x 85 opaque RGB red, independent of PNG encoding.
        expected = hashlib.sha256(struct.pack('!II', 85, 85) + bytes((217, 31, 31)) * 85 * 85).hexdigest()
        self.assertEqual(first.get('document_sha256'), expected)
        self.assertEqual(second.get('document_sha256'), expected)

    def test_browser_schema_rejects_incomplete_or_fabricated_results(self):
        import m1_interrupt_test as harness
        validator = getattr(harness, 'validate_browser_evidence', None)
        self.assertIsNotNone(validator, 'strict browser evidence validator is missing')
        evidence, images = browser_fixture()
        with tempfile.TemporaryDirectory() as root:
            artifacts = pathlib.Path(root) / 'browser-artifacts'
            artifacts.mkdir()
            for name, content in images.items():
                (artifacts / name).write_bytes(content)
            self.assertEqual(validator(evidence, artifacts), evidence)
            changes = [
                ((), 'schema_version', True), ((), 'schema_version', 2.0),
                ((), 'schema_version', 1),
                ((), 'status', 'fail'), ((), 'password', 'sensitive'),
                ((), 'assertions', 20.0), ((), 'headless', '/tmp/browser'),
                (('browser',), 'version', 'cookie=secret'),
                (('webdriver',), 'executable', '/tmp/chromedriver'),
            ]
            resume = ('scenarios', 'refresh_resume_reselect')
            pdf = ('scenarios', 'protected_pdf_page_navigation')
            changes += [(resume, key, value) for key, value in (
                ('status', 'fail'), ('part_count', 3.0), ('reloads', True),
                ('confirmed_bytes_before', True), ('confirmed_parts_before', [False]),
                ('confirmed_parts_before', [1]), ('mismatch_put_parts', [1]),
                ('confirmed_parts_after_mismatch', [0, 1]),
                ('resume_put_parts', [0, 1, 2]), ('resume_put_parts', [1]),
                ('resume_put_parts', [1, 1, 2]), ('resume_put_parts', [1.0, 2]),
                ('confirmed_parts_after', [0, 1]), ('download_sha256', 'f' * 64),
                ('file_id', '../secret'), ('assertions', 0))]
            changes += [(pdf + ('pages', 0), key, value) for key, value in (
                ('page', True), ('src', evidence['scenarios'][pdf[1]]['pages'][1]['src']),
                ('screenshot', '../pdf-page-1.png'), ('size_bytes', 0),
                ('size_bytes', 100.0), ('sha256', 'x' * 64),
                ('document_sha256', 'f' * 64), ('document_sha256', 'x' * 64))]
            for path, key, value in changes:
                invalid = copy.deepcopy(evidence)
                node = invalid
                for part in path:
                    node = node[part]
                node[key] = value
                with self.subTest(path=path, key=key, value=value):
                    with self.assertRaisesRegex(AssertionError, 'browser evidence'):
                        validator(invalid, artifacts)
            for key in evidence:
                invalid = copy.deepcopy(evidence)
                del invalid[key]
                with self.assertRaisesRegex(AssertionError, 'browser evidence'):
                    validator(invalid, artifacts)
            second = artifacts / 'pdf-page-2.png'
            for content in (b'', b'\x89PNG\r\n\x1a\n' + b'x' * 2000,
                            images['pdf-page-1.png'], images['pdf-page-2.png'][:-8]):
                second.write_bytes(content)
                invalid = copy.deepcopy(evidence)
                page = invalid['scenarios'][pdf[1]]['pages'][1]
                page.update(sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content))
                with self.assertRaisesRegex(AssertionError, 'browser evidence'):
                    validator(invalid, artifacts)
            second.unlink()
            with self.assertRaisesRegex(AssertionError, 'browser evidence'):
                validator(evidence, artifacts)

    def test_webdriver_protocol_and_network_events_fail_closed(self):
        browser = self.driver()
        self.assertEqual(browser.webdriver_value(200, b'{"value":{"ready":true}}'),
                         {'ready': True})
        for status, body in ((500, b'{"value":{"error":"unknown error","message":"secret"}}'),
                             (200, b'{"value":{"error":"invalid session id"}}'),
                             (200, b'{}'), (200, b'not json')):
            with self.assertRaisesRegex(RuntimeError, 'WebDriver') as caught:
                browser.webdriver_value(status, body)
            self.assertNotIn('secret', str(caught.exception))
        prefix = 'http://127.0.0.1:1234/api/v1/projects/' + '1' * 32 + '/uploads/' + '2' * 32
        def event(number):
            return {'message': json.dumps({'message': {'method': 'Network.requestWillBeSent',
                'params': {'request': {'method': 'PUT', 'url': prefix + '/parts/' + str(number)}}}})}
        self.assertEqual(browser.part_puts([event(1), event(2)], prefix), [1, 2])
        browser.require_puts([1, 2], [1, 2])
        for actual in ([0, 1, 2], [1], [1, 1, 2]):
            with self.assertRaisesRegex(AssertionError, 'part PUT'):
                browser.require_puts(actual, [1, 2])
        for invalid in ([{'message': '{'}], [event('bad')]):
            with self.assertRaises((ValueError, AssertionError)):
                browser.part_puts(invalid, prefix)

    def test_browser_dependency_overrides_versions_and_explicit_root_exception(self):
        browser = self.driver()
        with tempfile.TemporaryDirectory() as root:
            binary, driver = pathlib.Path(root) / 'chromium', pathlib.Path(root) / 'chromedriver'
            binary.write_text('#!/bin/sh\nprintf "Chromium 131.0.1.2\\n"\n')
            driver.write_text('#!/bin/sh\nprintf "ChromeDriver 131.0.1.2 (fixture)\\n"\n')
            binary.chmod(0o755)
            driver.chmod(0o755)
            environment = {'SMARTDOCS_BROWSER_BIN': str(binary),
                           'SMARTDOCS_WEBDRIVER_BIN': str(driver)}
            with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(os, 'geteuid', return_value=1000):
                found, sandbox = browser.dependencies()
                self.assertEqual(sandbox, 'enabled')
                self.assertEqual(found['browser'][1], {'executable': 'chromium', 'version': '131.0.1.2'})
                self.assertNotIn(root, json.dumps({key: value[1] for key, value in found.items()}))
            with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(os, 'geteuid', return_value=0):
                with self.assertRaisesRegex(RuntimeError, 'explicit'):
                    browser.dependencies()
                os.environ['SMARTDOCS_BROWSER_ALLOW_ROOT_NO_SANDBOX'] = '1'
                self.assertEqual(browser.dependencies()[1], 'disabled_explicit_root_disposable_host')
                driver.write_text('#!/bin/sh\nprintf "ChromeDriver 130.0.1.2\\n"\n')
                with self.assertRaisesRegex(RuntimeError, 'major versions differ'):
                    browser.dependencies()

    def test_network_capture_requires_the_service_task_read_not_unrelated_noise(self):
        browser = self.driver()
        checker = getattr(browser, 'require_task_read', None)
        self.assertIsNotNone(checker, 'mismatch must prove task request was actually observed')
        url = 'http://127.0.0.1:1234/api/v1/projects/' + '1' * 32 + '/uploads/' + '2' * 32
        event = {'message': json.dumps({'message': {'method': 'Network.requestWillBeSent',
            'params': {'request': {'method': 'GET', 'url': url}}}})}
        checker([event], url)
        for invalid in ([], [{'message': json.dumps({'message': {'method': 'Page.loadEventFired'}})}]):
            with self.assertRaisesRegex(AssertionError, 'task GET'):
                checker(invalid, url)

    def test_png_requires_rendered_pdf_color_not_just_a_changed_toolbar(self):
        browser = self.driver()
        checker = getattr(browser, 'require_pdf_render', None)
        self.assertIsNotNone(checker, 'PDF screenshot must contain its page content marker')
        checker(png_fixture(1), 1)
        checker(png_fixture(2), 2)
        with self.assertRaisesRegex(ValueError, 'PDF page content'):
            checker(png_fixture(1), 2)

    def test_stable_screenshot_waits_for_requested_page_before_counting_frames(self):
        browser = self.driver()
        viewer = browser.Browser.__new__(browser.Browser)
        non_target, expected = png_fixture(1), png_fixture(2)
        viewer.screenshot = mock.Mock(side_effect=[non_target] * 3 + [expected] * 3)
        with mock.patch.object(browser.time, 'sleep'), \
                mock.patch.object(browser.time, 'monotonic', return_value=0):
            screenshot = viewer.stable_screenshot(2)
        self.assertEqual(screenshot, expected)
        self.assertEqual(viewer.screenshot.call_count, 6)

    def test_stable_screenshot_fails_closed_when_requested_page_never_renders(self):
        browser = self.driver()
        viewer = browser.Browser.__new__(browser.Browser)
        viewer.screenshot = mock.Mock(return_value=png_fixture(1))
        with mock.patch.object(browser.time, 'sleep'), \
                mock.patch.object(browser.time, 'monotonic', side_effect=[0, 0, 10, 20, 25]):
            with self.assertRaisesRegex(AssertionError, 'page 2 did not render and stabilize'):
                viewer.stable_screenshot(2)

    def test_pdf_has_two_distinct_valid_page_streams_and_xref(self):
        import re
        pdf = pathlib.Path(__file__).resolve().parents[1] / 'fixtures/m1/sample.pdf'
        content = pdf.read_bytes()
        self.assertIn(b'/Count 2', content, 'PDF fixture is still single-page')
        self.assertEqual(len(re.findall(rb'/Type /Page\b', content)), 2)
        streams = re.findall(rb'/Length (\d+) >>\nstream\n(.*?)endstream', content, re.S)
        self.assertEqual(len(streams), 2)
        for length, stream in streams:
            self.assertEqual(int(length), len(stream))
        self.assertNotEqual(streams[0][1], streams[1][1])
        self.assertIn(b'(PAGE ONE)', streams[0][1])
        self.assertIn(b'(PAGE TWO)', streams[1][1])
        start = int(re.search(rb'startxref\n(\d+)', content).group(1))
        self.assertTrue(content[start:].startswith(b'xref\n'))
        for index, offset in enumerate(re.findall(rb'(\d{10}) 00000 n', content), 1):
            self.assertTrue(content[int(offset):].startswith(f'{index} 0 obj'.encode()))


if __name__ == '__main__':
    unittest.main()
