#!/usr/bin/env python3
"""Real UI/HTTP acceptance using only Python stdlib and W3C WebDriver.

Run only on a provisioned acceptance host. The harness self-tests never call main.
Raw protocol responses, passwords, cookies and browser profiles are not evidence.
"""

import argparse
import base64
import http.client
import json
import os
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib

from m1_http_test import (HttpClient, ServerProcess, complete, create_upload,
                          data, digest, login, put_part, upload_file)


def webdriver_value(status, body):
    """Never propagate remote messages: they may contain credentials or paths."""
    try:
        payload = json.loads(body)
        if type(payload) is not dict or 'value' not in payload:
            raise ValueError()
        value = payload['value']
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError('WebDriver malformed response') from None
    if status != 200 or (isinstance(value, dict) and 'error' in value):
        code = value.get('error') if isinstance(value, dict) else None
        # Only fixed protocol errors are safe to surface.
        safe = code if code in {'no such element', 'stale element reference',
                                'invalid session id', 'session not created',
                                'unknown command'} else 'command failed'
        raise RuntimeError(f'WebDriver {safe}')
    return value


def part_puts(entries, task_url):
    """Consume Chrome performance events in memory; retain part numbers only."""
    if type(entries) is not list:
        raise ValueError('invalid network log')
    parts = []
    for entry in entries:
        event = json.loads(entry['message'])['message']
        if event['method'] != 'Network.requestWillBeSent':
            continue
        request = event['params']['request']
        if request['method'] == 'PUT' and request['url'].startswith(task_url + '/parts/'):
            suffix = request['url'][len(task_url + '/parts/'):]
            if not re.fullmatch(r'0|[1-9][0-9]*', suffix):
                raise ValueError('invalid part PUT URL')
            parts.append(int(suffix))
    return parts


def require_puts(actual, expected):
    if (type(actual) is not list or any(type(n) is not int for n in actual) or
            sorted(actual) != sorted(expected)):
        raise AssertionError('browser part PUT set is incorrect or duplicated')


def require_task_read(entries, task_url):
    for entry in entries:
        event = json.loads(entry['message'])['message']
        if event['method'] == 'Network.requestWillBeSent':
            request = event['params']['request']
            if request['method'] == 'GET' and request['url'] == task_url:
                return
    raise AssertionError('browser network capture is missing the task GET')


def png_details(content, page=None):
    """Check actual screenshot bytes, not just a caller's claimed hash/size."""
    if not 1024 <= len(content) <= 16 * 1024 * 1024 or content[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('invalid or trivial PNG')
    offset, kinds, compressed = 8, [], bytearray()
    width = height = channels = 0
    while offset < len(content):
        if len(content) - offset < 12:
            raise ValueError('truncated PNG')
        length = struct.unpack('!I', content[offset:offset + 4])[0]
        kind = content[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(content):
            raise ValueError('truncated PNG chunk')
        body = content[offset + 8:end - 4]
        if zlib.crc32(kind + body) != struct.unpack('!I', content[end - 4:end])[0]:
            raise ValueError('PNG checksum mismatch')
        if kind not in {b'IHDR', b'IDAT', b'IEND', b'sRGB', b'gAMA', b'pHYs', b'cHRM'}:
            raise ValueError('unexpected PNG metadata')
        if kind == b'IHDR':
            if kinds or len(body) != 13:
                raise ValueError('invalid PNG header')
            width, height, depth, color, compression, filtering, interlace = struct.unpack('!IIBBBBB', body)
            if (not 200 <= width <= 4096 or not 200 <= height <= 4096 or
                    depth != 8 or color not in (2, 6) or
                    (compression, filtering, interlace) != (0, 0, 0)):
                raise ValueError('unsupported or trivial PNG dimensions')
            channels = 3 if color == 2 else 4
        elif kind == b'IDAT':
            compressed.extend(body)
        elif kind == b'IEND':
            if body or end != len(content):
                raise ValueError('invalid PNG end')
        kinds.append(kind)
        offset = end
    if not kinds or kinds[0] != b'IHDR' or kinds[-1] != b'IEND' or not compressed:
        raise ValueError('incomplete PNG')
    expected = height * (1 + width * channels)
    decoder = zlib.decompressobj()
    raw = decoder.decompress(compressed, expected + 1)
    if len(raw) != expected or not decoder.eof or decoder.unused_data:
        raise ValueError('invalid PNG image data')
    if any(raw[row * (1 + width * channels)] > 4 for row in range(height)):
        raise ValueError('invalid PNG row filter')
    details = {'sha256': digest(content), 'size_bytes': len(content)}
    if page is not None:
        # The center third is inside the document viewport at our fixed CI
        # window size, away from the top toolbar and left thumbnail sidebar.
        # Keep its RGB fingerprint independent of PNG encoding and viewer UI.
        left_edge, right_edge = width // 3, width * 2 // 3
        top_edge, bottom_edge = height // 3, height * 2 // 3
        region_pixels = (right_edge - left_edge) * (bottom_edge - top_edge)
        document = bytearray(struct.pack('!II', right_edge - left_edge, bottom_edge - top_edge))
        stride, matched, opposite = width * channels, 0, 0
        previous = bytearray(stride)
        for row_number in range(height):
            start = row_number * (stride + 1)
            filtering = raw[start]
            row = bytearray(raw[start + 1:start + 1 + stride])
            for index in range(stride):
                left = row[index - channels] if index >= channels else 0
                above = previous[index]
                upper_left = previous[index - channels] if index >= channels else 0
                if filtering == 1:
                    predictor = left
                elif filtering == 2:
                    predictor = above
                elif filtering == 3:
                    predictor = (left + above) // 2
                elif filtering == 4:
                    estimate = left + above - upper_left
                    distances = (abs(estimate - left), abs(estimate - above), abs(estimate - upper_left))
                    predictor = (left, above, upper_left)[distances.index(min(distances))]
                else:
                    predictor = 0
                row[index] = (row[index] + predictor) & 255
            if top_edge <= row_number < bottom_edge:
                for index in range(left_edge * channels, right_edge * channels, channels):
                    pixel = row[index:index + 3]
                    document.extend(pixel)
                    red, green, blue = pixel
                    opaque = channels == 3 or row[index + 3] >= 240
                    is_red = opaque and red >= 180 and green <= 80 and blue <= 80
                    is_blue = opaque and blue >= 180 and red <= 80 and green <= 100
                    matched += is_red if page == 1 else is_blue
                    opposite += is_blue if page == 1 else is_red
            previous = row
        if matched < max(1000, region_pixels // 5) or opposite > region_pixels // 100:
            raise ValueError('PDF page content is missing, mixed or belongs to another page')
        details['document_sha256'] = digest(document)
    return details


def require_pdf_render(content, page):
    return png_details(content, page)


def dependencies():
    found = {}
    for kind, variable, names in (
            ('browser', 'SMARTDOCS_BROWSER_BIN', ('chromium', 'chromium-browser', 'google-chrome')),
            ('webdriver', 'SMARTDOCS_WEBDRIVER_BIN', ('chromedriver',))):
        explicit = os.environ.get(variable)
        binary = shutil.which(explicit) if explicit else next(
            (path for name in names if (path := shutil.which(name))), None)
        if not binary:
            raise RuntimeError(f'missing browser dependency: {variable}')
        result = subprocess.run([binary, '--version'], capture_output=True,
                                text=True, timeout=10, check=False)
        product = r'(?:Chromium|Google Chrome(?: for Testing)?|Chrome)' if kind == 'browser' else 'ChromeDriver'
        match = re.search(product + r'\s+(\d+\.\d+\.\d+\.\d+)\b', result.stdout)
        if result.returncode or not match:
            raise RuntimeError(f'unsupported browser dependency version: {variable}')
        basename = pathlib.Path(binary).name
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', basename):
            raise RuntimeError(f'invalid browser executable name: {variable}')
        found[kind] = (str(pathlib.Path(binary).resolve()),
                       {'executable': basename, 'version': match[1]})
    if found['browser'][1]['version'].split('.')[0] != found['webdriver'][1]['version'].split('.')[0]:
        raise RuntimeError('browser and WebDriver major versions differ')
    sandbox = 'enabled'
    if os.geteuid() == 0:
        if os.environ.get('SMARTDOCS_BROWSER_ALLOW_ROOT_NO_SANDBOX') != '1':
            raise RuntimeError('prefer non-root acceptance; root requires explicit SMARTDOCS_BROWSER_ALLOW_ROOT_NO_SANDBOX=1 on a disposable host')
        sandbox = 'disabled_explicit_root_disposable_host'
    return found, sandbox


class Browser:
    def __init__(self, binaries, sandbox, profile):
        self.binaries, self.sandbox, self.profile = binaries, sandbox, profile
        self.port = ServerProcess._free_port()
        self.process = None
        self.session = None

    def request(self, method, path, body=None):
        # Cold browser loading can exceed 30s under a host's disk I/O cap.
        timeout = 120 if method == 'POST' and path == '/session' else 30
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=timeout)
        try:
            connection.request(method, path, None if body is None else json.dumps(body),
                               {'Content-Type': 'application/json'})
            response = connection.getresponse()
            return webdriver_value(response.status, response.read())
        finally:
            connection.close()

    def command(self, method, path, body=None):
        return self.request(method, '/session/' + self.session + path, body)

    def start(self):
        self.process = subprocess.Popen(
            [self.binaries['webdriver'][0], f'--port={self.port}', '--allowed-ips=127.0.0.1'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 15
        while True:
            if self.process.poll() is not None:
                raise RuntimeError('WebDriver exited during startup')
            try:
                if self.request('GET', '/status').get('ready') is True:
                    break
            except (OSError, http.client.HTTPException):
                pass
            if time.monotonic() > deadline:
                raise RuntimeError('WebDriver startup timed out')
            time.sleep(0.1)
        flags = ['--headless=new', '--window-size=1280,1000',
                 '--no-first-run', '--no-default-browser-check',
                 '--user-data-dir=' + self.profile]
        if self.sandbox != 'enabled':
            flags.append('--no-sandbox')
        value = self.request('POST', '/session', {'capabilities': {'alwaysMatch': {
            'browserName': 'chrome',
            'goog:chromeOptions': {'binary': self.binaries['browser'][0], 'args': flags,
                                   'prefs': {'plugins.always_open_pdf_externally': False}},
            'goog:loggingPrefs': {'performance': 'ALL'},
        }}})
        self.session = value['sessionId']
        capabilities = value['capabilities']
        if capabilities['browserVersion'] != self.binaries['browser'][1]['version']:
            raise RuntimeError('WebDriver launched an unexpected browser version')
        if capabilities['chrome']['chromedriverVersion'].split(' ')[0] != self.binaries['webdriver'][1]['version']:
            raise RuntimeError('WebDriver product version mismatch')
        self.command('POST', '/timeouts', {'implicit': 0, 'pageLoad': 30000, 'script': 10000})

    def stop(self):
        if self.session:
            try:
                self.command('DELETE', '')
            except (RuntimeError, OSError, http.client.HTTPException):
                pass
            self.session = None
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process = None

    def find(self, selector, using='css selector'):
        return self.command('POST', '/element', {'using': using, 'value': selector})[
            'element-6066-11e4-a52e-4f735466cecf']

    def click(self, selector, using='css selector'):
        self.command('POST', '/element/' + self.find(selector, using) + '/click', {})

    def fill(self, selector, value, using='css selector', file=False):
        element = '/element/' + self.find(selector, using)
        if not file:
            self.command('POST', element + '/clear', {})
        self.command('POST', element + '/value', {'text': str(value)})

    def inspect(self, script, *args):
        return self.command('POST', '/execute/sync', {'script': script, 'args': list(args)})

    def wait(self, script, *args):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.inspect(script, *args):
                return
            time.sleep(0.2)
        raise AssertionError('browser visible state timed out')

    def logs(self):
        return self.command('POST', '/se/log', {'type': 'performance'})

    def screenshot(self):
        bounds = self.inspect('const r = document.querySelector("#pdf-preview").getBoundingClientRect(); '
                              'return {top:r.top, bottom:r.bottom, viewport:innerHeight};')
        if bounds['top'] < 0 or bounds['bottom'] > bounds['viewport']:
            # Scroll the outer page, not the PDF's internal document viewport.
            self.command('POST', '/actions', {'actions': [{
                'type': 'wheel', 'id': 'preview-scroll', 'actions': [{
                    'type': 'scroll', 'x': 0, 'y': 0, 'deltaX': 0,
                    'deltaY': round(bounds['top']) - 16, 'duration': 250}]}]})
        element = self.find('#pdf-preview')
        return base64.b64decode(self.command('GET', '/element/' + element + '/screenshot'), validate=True)

    def stable_screenshot(self, expected_page):
        # Give the native PDF compositor time to draw, then require 3 equal samples.
        time.sleep(2)
        deadline, previous, matches = time.monotonic() + 25, None, 0
        while time.monotonic() < deadline:
            content = self.screenshot()
            try:
                require_pdf_render(content, expected_page)
            except ValueError:
                previous, matches = None, 0
            else:
                matches = matches + 1 if content == previous else 1
                if matches >= 3:
                    return content
                previous = content
            time.sleep(0.5)
        raise AssertionError(f'PDF page {expected_page} did not render and stabilize')


class Checks:
    def __init__(self):
        self.count = 0

    def check(self, condition, message):
        self.count += 1
        if not condition:
            raise AssertionError(message)


def run_scenarios(browser, client, context, password, scratch, pdf_path, artifacts):
    project, directory = context['project_id'], context['root_directory_id']
    origin = f'http://127.0.0.1:{client.port}'
    base = f'/api/v1/projects/{project}'
    check = Checks()
    chunk = int(os.environ['SMARTDOCS_CHUNK_BYTES'])
    if not 4 <= chunk <= 1024 * 1024:
        raise ValueError('browser fixture chunk size outside bounded acceptance range')
    content = bytes((n * 37 + 11) % 256 for n in range(chunk * 3 - 3))
    original, mismatch = scratch / 'browser-resume.bin', scratch / 'mismatch.bin'
    original.write_bytes(content)
    mismatch.write_bytes(bytes(value ^ 0xff for value in content))
    task = create_upload(client, project, directory, original.name, content, 'application/octet-stream')
    task_id, count = task['task_id'], task['part_count']
    task_path = base + '/uploads/' + task_id
    data(put_part(client, project, task_id, 0, content[:task['chunk_size']]))

    def state():
        return data(client.request('GET', task_path))

    def parts(detail):
        return sorted(part['part_number'] for part in detail['confirmed_parts'])

    before = state()
    check.check(count >= 3 and task['chunk_size'] == chunk and parts(before) == [0],
                'fixture must confirm exactly the first of at least three parts')
    confirmed = sum(part['size'] for part in before['confirmed_parts'])
    check.check(confirmed == chunk, 'server confirmed byte count differs')
    browser.command('POST', '/url', {'url': origin + '/app.html'})
    browser.wait('return !document.querySelector("#login-form").hidden')
    browser.fill('#username', context['username'])
    browser.fill('#password', password)
    browser.click('#login-form button[type=submit]')

    def ready():
        browser.wait('return !document.querySelector("#signed-in").hidden && '
                     'Array.from(document.querySelector("#project-select").options).some(o => o.value === arguments[0])', project)
        browser.click(f'#project-select option[value="{project}"]')
        browser.wait('return !document.querySelector("#workbench").hidden && '
                     'document.querySelector("#files-status").textContent.includes("files loaded")')
        browser.wait('return Array.from(document.querySelectorAll("#upload-list article")).some(a => '
                     'a.querySelector("input")?.getAttribute("aria-label") === arguments[0] && '
                     'a.textContent.includes("State: awaiting reselect") && a.textContent.includes(arguments[1]))',
                     'Reselect file for upload ' + task_id, f'Confirmed: {chunk} of {len(content)} bytes')

    ready()
    check.check(True, 'initial awaiting-reselect UI and confirmed bytes')
    browser.command('POST', '/refresh', {})
    ready()
    check.check(parts(state()) == [0], 'browser reload changed confirmed service state')
    card = '//article[h3[text()="browser-resume.bin"]]'
    input_selector = f'input[aria-label="Reselect file for upload {task_id}"]'
    browser.logs()
    browser.fill(input_selector, mismatch, file=True)
    browser.click(card + '//button[text()="Reselect and resume"]', 'xpath')
    browser.wait('return document.querySelector("#upload-status").textContent.includes('
                 '"The selected file SHA-256 does not match this upload.")')
    mismatch_events = browser.logs()
    require_task_read(mismatch_events, origin + task_path)
    mismatch_puts = part_puts(mismatch_events, origin + task_path)
    require_puts(mismatch_puts, [])
    check.check(bool(mismatch_events), 'browser network events unavailable')
    after_mismatch = state()
    check.check(parts(after_mismatch) == [0] and
                sum(p['size'] for p in after_mismatch['confirmed_parts']) == confirmed,
                'mismatch altered confirmed parts')
    check.check(not mismatch_puts, 'mismatched file issued part PUT')
    browser.logs()
    browser.fill(input_selector, original, file=True)
    browser.click(card + '//button[text()="Reselect and resume"]', 'xpath')
    browser.wait('return Array.from(document.querySelectorAll("#upload-list article")).some(a => '
                 'a.querySelector("h3").textContent === "browser-resume.bin" && '
                 'a.textContent.includes("State: completed"))')
    observed = part_puts(browser.logs(), origin + task_path)
    require_puts(observed, list(range(1, count)))
    check.check(True, 'browser uploaded exactly missing parts')
    after = state()
    check.check(after['state'] == 'completed' and parts(after) == list(range(count)),
                'resume did not complete all service parts')
    result = data(complete(client, project, task_id))
    replay = data(complete(client, project, task_id))
    ids = {name: result[name] for name in ('file_id', 'version_id', 'processing_job_id')}
    check.check(all(after[name] == ids[name] == replay[name] for name in ids),
                'completion IDs were not stable')
    card_text = browser.command('GET', '/element/' + browser.find(card, 'xpath') + '/text')
    check.check(all(value in card_text for value in ids.values()), 'UI result IDs differ from service')
    downloaded = client.request('GET', base + '/files/' + ids['file_id'] + '/versions/' + ids['version_id'] + '/content')
    check.check(downloaded.status == 200 and digest(downloaded.body) == digest(content),
                'browser resumed download hash mismatch')
    resume = {
        'status': 'pass', 'assertions': check.count, 'part_count': count,
        'chunk_size_bytes': chunk, 'file_size_bytes': len(content),
        'confirmed_parts_before': parts(before), 'confirmed_bytes_before': confirmed,
        'confirmed_parts_after_mismatch': parts(after_mismatch),
        'mismatch_put_parts': mismatch_puts, 'resume_put_parts': observed,
        'confirmed_parts_after': parts(after), 'source_sha256': digest(content),
        'download_sha256': digest(downloaded.body), 'reloads': 1, **ids,
    }
    check = Checks()
    pdf = pdf_path.read_bytes()
    check.check(b'/Count 2' in pdf and len(re.findall(rb'/Type /Page\b', pdf)) == 2,
                'PDF fixture must declare two pages')
    _, pdf_result = upload_file(client, project, directory, 'browser-preview.pdf', pdf, 'application/pdf')
    path = base + '/files/' + pdf_result['file_id'] + '/versions/' + pdf_result['version_id'] + '/content'
    protected = client.request('GET', path)
    check.check(protected.status == 200 and protected.body == pdf and
                protected.headers.get('content-type') == 'application/pdf',
                'PDF bytes or media type differ')
    check.check(HttpClient(client.port).request('GET', path).status == 401,
                'PDF is not protected')
    browser.fill('#name-filter', 'browser-preview.pdf')
    browser.click('#file-search button[type=submit]')
    browser.wait('return Array.from(document.querySelectorAll("#file-rows tr")).some('
                 'r => r.firstElementChild.textContent === "browser-preview.pdf")')
    browser.click('//tr[td[text()="browser-preview.pdf"]]//button[text()="Open details"]', 'xpath')
    browser.wait('return !document.querySelector("#preview-form").hidden && '
                 'document.querySelector("#detail-status").textContent.includes("versions loaded")')
    browser.click(f'#preview-version option[value="{pdf_result["version_id"]}"]')
    pages = []
    for page in (1, 2):
        browser.fill('#preview-page', page)
        browser.click('#preview-form button[type=submit]')
        expected = path + f'#page={page}'
        browser.wait('return document.querySelector("#pdf-preview").getAttribute("src") === arguments[0]', expected)
        src = browser.inspect('return document.querySelector("#pdf-preview").getAttribute("src")')
        check.check(src == expected, 'wrong PDF page fragment or version')
        screenshot = browser.stable_screenshot(page)
        info = require_pdf_render(screenshot, page)
        check.check(info['size_bytes'] >= 1024, 'PDF screenshot is trivial')
        filename = f'pdf-page-{page}.png'
        (artifacts / filename).write_bytes(screenshot)
        pages.append({'page': page, 'src': src, 'screenshot': filename, **info})
    check.check(pages[0]['sha256'] != pages[1]['sha256'] and
                pages[0]['document_sha256'] != pages[1]['document_sha256'],
                'PDF document viewport did not change pages')
    return {'refresh_resume_reselect': resume,
            'protected_pdf_page_navigation': {'status': 'pass', 'assertions': check.count,
                                              'fixture_sha256': digest(pdf), 'pages': pages}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-dependencies', action='store_true')
    for argument in ('server-bin', 'log-dir', 'context-file', 'pdf', 'output'):
        parser.add_argument('--' + argument)
    args = parser.parse_args()
    binaries, sandbox = dependencies()
    if args.check_dependencies:
        print('M1 browser dependencies: ' + json.dumps(
            {**{key: value[1] for key, value in binaries.items()}, 'headless': 'new', 'sandbox': sandbox}))
        return 0
    if not all((args.server_bin, args.log_dir, args.context_file, args.pdf, args.output)):
        parser.error('browser driver requires server-bin, log-dir, context-file, pdf and output')
    password = os.environ.get('SMARTDOCS_E2E_PASSWORD')
    if not password:
        raise RuntimeError('SMARTDOCS_E2E_PASSWORD is required')
    context = json.loads(pathlib.Path(args.context_file).read_text())
    output = pathlib.Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    artifacts = output.parent / 'browser-artifacts'
    artifacts.mkdir(exist_ok=True)
    log_dir = pathlib.Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    server = ServerProcess(args.server_bin, log_dir / 'server')
    with tempfile.TemporaryDirectory(prefix='browser-', dir=log_dir) as temporary:
        browser = Browser(binaries, sandbox, str(pathlib.Path(temporary) / 'profile'))
        try:
            server.start()
            client = HttpClient(server.port)
            login(client, context['username'], password)
            browser.start()
            scenarios = run_scenarios(browser, client, context, password,
                                      pathlib.Path(temporary), pathlib.Path(args.pdf), artifacts)
        finally:
            browser.stop()
            server.stop()
    evidence = {
        'schema_version': 2, 'status': 'pass',
        **{key: value[1] for key, value in binaries.items()},
        'headless': 'new', 'sandbox': sandbox, 'scenarios': scenarios,
        'assertions': sum(value['assertions'] for value in scenarios.values()),
    }
    from m1_interrupt_test import validate_browser_evidence
    validate_browser_evidence(evidence, artifacts)
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + '\n')
    print(f'M1 browser: 2/2 scenarios; {evidence["assertions"]} assertions')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        # Helpers can carry response bodies, credentials, or temporary paths in
        # exception text. Only the exception class leaves this process.
        print(f'm1_browser_failed: {type(error).__name__}; no browser evidence accepted', file=sys.stderr)
        sys.exit(1)
