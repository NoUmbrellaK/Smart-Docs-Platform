# Smart Docs Platform M1 File Business Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the M1 C++ file business: authenticated project-scoped file management, resumable chunk upload, immutable versions, protected streaming downloads, single-range PDF preview, and reproducible M1 evidence.

**Architecture:** Retain the imported `epoll` event loop, timers, thread pool, and buffer, but replace the static-only HTTP boundary with an incremental parser, router, bounded body handlers, and streaming file responses. MySQL 8.0 is the authority for identities, permissions, upload state, versions, and audit records; the Linux filesystem stores only server-named immutable objects and upload staging files.

**Tech Stack:** C++14, GNU Make, Linux `epoll`/`sendfile`/`openat`, MySQL 8.0 C API, OpenSSL 3 `libcrypto`, nlohmann/json 3.11.3, HTML/CSS/native ES Modules, Python 3 standard library for black-box tests

**Spec:** `docs/superpowers/specs/2026-09-13-m1-file-business-design.md`

## Global Constraints

- Treat `团队文档管理与智能整理平台_需求文档.md` as the product and acceptance source of truth.
- Keep the build at C++14 and preserve the imported Apache-2.0 attribution on inherited source files.
- MySQL 8.0 stores authoritative metadata; never infer a successful operation only from files on disk.
- IDs are lowercase 32-character hexadecimal strings generated from 128 random bits on the server.
- User names and file names are metadata only; filesystem paths contain server-generated IDs only.
- API identity comes from the validated session cookie, never a `user_id` supplied by the model or browser body.
- Every project resource query is constrained by the authenticated user's current project membership.
- A new file version never inherits the previous version's remote-AI approval.
- Upload chunks use `application/octet-stream`, default to 8 MiB, and never cause a complete file to be buffered in memory.
- File content is immutable after publication; updates create a new `file_versions` row and move the current-version pointer.
- Only one byte range is supported. Multi-range input is rejected explicitly and never returns fabricated partial content.
- Uploaded content is outside `resources/` and is opened only after authorization.
- Do not claim M1 or a requirement test has passed until the named command has run and its evidence has been saved.
- Do not add M2 parsing/indexing, MCP, Agent, archive-plan, or handover-report behavior in this plan.

## Planned File Map

| Path | Responsibility |
| --- | --- |
| `Makefile` | Server, admin CLI, unit/integration test, and test-server targets |
| `third_party/nlohmann/json.hpp` | Pinned JSON parser/serializer |
| `third_party/nlohmann/LICENSE.MIT` | Dependency license |
| `code/app/config.{h,cpp}` | Environment parsing and startup validation |
| `code/app/application.{h,cpp}` | Dependency ownership, route registration, readiness |
| `code/core/app_error.h` | Stable HTTP/business error representation |
| `code/core/id.{h,cpp}` | Random entity and request IDs |
| `code/core/crypto.{h,cpp}` | SHA-256, PBKDF2, secure token generation |
| `code/http/httprequest.{h,cpp}` | Incremental request-head and body framing parser |
| `code/http/httpresponse.{h,cpp}` | Buffered and file-region response state |
| `code/http/router.{h,cpp}` | Method/path matching and body-handler preparation |
| `code/http/httpconn.{h,cpp}` | Per-connection parse/body/response lifecycle |
| `code/server/webserver.{h,cpp}` | Existing event loop wired to `Application` |
| `code/db/mysql.{h,cpp}` | Safe pool, borrowed connection, transaction, prepared statements |
| `code/db/schema.{h,cpp}` | Schema-version/readiness checks |
| `db/migrations/001_m1_core.sql` | M1 authority tables and constraints |
| `scripts/migrate.sh` | Explicit versioned schema application |
| `scripts/test-with-mysql.sh` | Isolated MySQL integration-test runner |
| `code/auth/*` | Session repository/service/routes and password verification |
| `code/project/*` | Project, membership, role, and directory operations |
| `code/file/*` | File metadata, versions, immutable store, range parsing, routes |
| `code/upload/*` | Upload repository, streaming part sink, completion, reconciliation |
| `code/admin/main.cpp` | Password-stdin local user bootstrap command |
| `resources/app.html` | M1 shell page |
| `resources/css/app.css` | M1 layout and states |
| `resources/js/api.js` | Same-origin JSON/fetch client |
| `resources/js/sha256.js` | Pinned incremental browser SHA-256 module |
| `resources/js/app.js` | Login, project, directory, file, version, and settings UI |
| `resources/js/uploads.js` | Resumable upload controller |
| `test/unit/*_test.cpp` | Deterministic C++ unit tests |
| `test/integration/*_test.cpp` | MySQL/filesystem integration tests |
| `test/e2e/m1_http_test.py` | Real-socket M1 scenario runner |
| `test/e2e/m1_interrupt_test.py` | Twenty deterministic interruption rounds |
| `docs/evidence/m1/README.md` | Commands, environment, result files, and known gaps |

## Fixed HTTP Contracts

Successful JSON uses an envelope such as `{"data": null, "request_id": "00000000000000000000000000000001"}`. Errors use the spec section 9 envelope. Timestamps are UTC RFC 3339 with microseconds and `Z`. All entity IDs and SHA-256 values are lowercase hex. Unknown JSON fields and wrong JSON types return `400 invalid_request`; required strings are trimmed, limited by UTF-8 byte length, and cannot contain NUL or `/` where they represent a display name.

Core request bodies are fixed as follows:

```json
{"username":"admin","password":"at-least-12-characters"}
{"name":"Project A"}
{"role":"editor"}
{"parent_id":"32-hex-directory-id","name":"Design"}
{"name":"renamed.pdf","directory_id":"32-hex-directory-id"}
{"policy":"remote_ai_allowed","approved_version_ids":["32-hex-version-id"]}
```

Create-file upload body:

```json
{
  "mode":"create_file",
  "directory_id":"32-hex-directory-id",
  "name":"guide.pdf",
  "size":10485760,
  "sha256":"64-lowercase-hex",
  "media_type":"application/pdf"
}
```

Create-version adds `file_id` and `observed_current_version_id`, and omits `directory_id`/`name` because those remain file metadata. Upload responses contain `task_id`, `state`, `chunk_size`, `part_count`, `confirmed_parts`, `received_bytes`, `file_id`, `version_id`, `processing_job_id`, and `failure`; the last four are `null` until applicable. File lists accept `directory_id`, `name`, `deleted=true|false`, `page` (default 1), and `page_size` (default 50, maximum 100). Upload lists accept `state`, `page`, and `page_size`. List responses use `{"items":[],"page":1,"page_size":50,"total":0}` inside `data`.

Login returns user ID/name, expiry, and accessible project summaries while setting the cookie. `GET /me` returns the same identity/project shape without a password or token. Mutation success codes are `201` for creation, `200` for updates/completion/replay, and `204` for logout/cancel/delete where no body is needed; a `204` response carries `X-Request-ID` because it has no JSON body.

## Spec Coverage Map

| Approved requirement | Implemented and proved by |
| --- | --- |
| F-01 project/file management | Tasks 5, 6, 9, 10; black-box coverage in Task 11 |
| F-02 chunk upload/resume/idempotency | Tasks 7 and 8; browser resume in Task 10; interruption proof in Task 11 |
| F-03 protected download/Range/PDF | HTTP transport in Task 3; content behavior in Task 9; UI and black-box proof in Tasks 10-11 |
| F-04 immutable versions/stable citations | Schema/storage in Tasks 4 and 6; publication and version conflicts in Tasks 8-9; black-box proof in Task 11 |
| Identity, project roles, no ID substitution | Task 5 and T-01 scenarios in Task 11 |
| Remote-AI file/version flags | Schema in Task 4, policy transaction in Task 9, page state in Task 10 |
| Truthful task/restart states | Task 8 reconciliation and twenty process-interruption rounds in Task 11 |
| Configuration and secret boundaries | Task 2, readiness/migration in Task 4, deployment audit in Task 12 |
| M1 pages and real business states | Task 10 with real-socket verification in Task 11 |
| Reproducible evidence and honest README | Tasks 11-12 |

---

### Task 1: Reproducible Build, Test Harness, and Pinned JSON

**Files:**
- Modify: `Makefile`
- Delete: `build/Makefile`
- Delete: `test/test.cpp`
- Modify: `.gitignore`
- Create: `test/test_support.h`
- Create: `test/test_main.cpp`
- Create: `test/unit/baseline_test.cpp`
- Create: `third_party/nlohmann/json.hpp`
- Create: `third_party/nlohmann/LICENSE.MIT`

**Interfaces:**
- Consumes: existing `code/**/*.cpp` and MySQL/OpenSSL system libraries.
- Produces: `make server`, `make admin`, `make test`, `make test-server`, and the `TEST_CASE(name)`/`CHECK(expr)` test API.

- [x] **Step 1: Record the current test-runner failure**

Run:

```bash
make test && test -x bin/smartdocs_tests
```

Expected: the existing Make invocation is a no-op because `test/` is a directory, then the executable check fails with exit status `1`.

- [x] **Step 2: Vendor nlohmann/json 3.11.3 and its license**

Fetch only the official tagged single header and license:

```bash
mkdir -p third_party/nlohmann
curl -fsSLo third_party/nlohmann/json.hpp https://raw.githubusercontent.com/nlohmann/json/v3.11.3/single_include/nlohmann/json.hpp
curl -fsSLo third_party/nlohmann/LICENSE.MIT https://raw.githubusercontent.com/nlohmann/json/v3.11.3/LICENSE.MIT
rg -n 'NLOHMANN_JSON_VERSION_MAJOR 3|NLOHMANN_JSON_VERSION_MINOR 11|NLOHMANN_JSON_VERSION_PATCH 3' third_party/nlohmann/json.hpp
```

Expected: all three version macros match. Do not continue with an HTML error page or an untagged branch response.

- [x] **Step 3: Add the minimal test registry**

`test/test_support.h` exposes:

```cpp
#pragma once
#include <functional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

using TestFunction = std::function<void()>;
std::vector<std::pair<std::string, TestFunction>>& TestRegistry();

struct TestRegistration {
    TestRegistration(const char* name, TestFunction test);
};

#define TEST_CASE(name) \
    static void name(); \
    static TestRegistration name##_registration(#name, name); \
    static void name()

#define CHECK(expression) \
    do { if (!(expression)) throw std::runtime_error(#expression); } while (false)
```

`test/test_main.cpp` runs every registered test, prints one `PASS` or `FAIL` line, and exits nonzero if any test fails. It accepts `--filter=<substring>` and falls back to the `TEST_FILTER` environment variable. `baseline_test.cpp` parses `{"ok":true}` with nlohmann/json and checks the Boolean value.

- [x] **Step 4: Replace the Make rules**

Use explicit targets and place intermediate files under `build/obj/`:

```make
CXX ?= g++
CPPFLAGS := -I. -Icode -Ithird_party
CXXFLAGS := -std=c++14 -O2 -g -Wall -Wextra -Wpedantic
LDLIBS := -pthread -lmysqlclient -lcrypto

APP_SOURCES := $(filter-out code/main.cpp code/admin/main.cpp,$(shell find code -name '*.cpp' -print))
TEST_SOURCES := test/test_main.cpp $(shell find test/unit test/integration -name '*_test.cpp' -print 2>/dev/null)

.PHONY: all server admin test clean
all: server
server: bin/server
admin: bin/smartdocs-admin
test: bin/smartdocs_tests
	./bin/smartdocs_tests
```

Add object rules with dependency files (`-MMD -MP`), a `bin/server` link using `code/main.cpp`, and an admin target only after `code/admin/main.cpp` exists. Until Task 5, `make admin` must print `admin target not available before Task 5` and exit nonzero. `make test-server` adds `-DSMARTDOCS_ENABLE_FAULT_INJECTION=1` and links `bin/smartdocs_test_server`.

- [x] **Step 5: Verify the new harness**

Run:

```bash
make clean
make server
make test
```

Expected: `bin/server` builds; the test runner prints `PASS baseline_json_version` and exits `0`.

- [x] **Step 6: Commit**

```bash
git add .gitignore Makefile build/Makefile test/test.cpp test/test_support.h test/test_main.cpp test/unit/baseline_test.cpp third_party/nlohmann
git commit -m "build: add reproducible M1 test harness"
```

### Task 2: Validated Configuration, IDs, and Cryptographic Utilities

**Files:**
- Create: `code/app/config.h`
- Create: `code/app/config.cpp`
- Create: `code/core/app_error.h`
- Create: `code/core/id.h`
- Create: `code/core/id.cpp`
- Create: `code/core/crypto.h`
- Create: `code/core/crypto.cpp`
- Create: `config/smart-docs.env.example`
- Create: `test/unit/config_test.cpp`
- Create: `test/unit/core_crypto_test.cpp`
- Modify: `test/test_support.h`
- Modify: `Makefile`

**Interfaces:**
- Produces: `AppConfig AppConfig::LoadFromEnvironment()`, `std::string GenerateId()`, `std::string GenerateTokenHex(size_t bytes)`, `std::array<unsigned char, 32> Sha256(...)`, `PasswordHash DerivePassword(...)`, and `bool VerifyPassword(...)`.
- `AppError` has `int http_status`, `std::string code`, `std::string message`, and `bool retryable`.

- [x] **Step 1: Write failing configuration and crypto tests**

Cover these exact cases:

```cpp
TEST_CASE(config_rejects_missing_mysql_password) {
    ScopedEnvironment env = ValidEnvironment();
    env.Unset("SMARTDOCS_MYSQL_PASSWORD");
    CHECK_THROWS_CODE(AppConfig::LoadFromEnvironment(), "config_missing");
}

TEST_CASE(ids_are_lower_hex_and_unique) {
    const std::string first = GenerateId();
    const std::string second = GenerateId();
    CHECK(first.size() == 32);
    CHECK(first.find_first_not_of("0123456789abcdef") == std::string::npos);
    CHECK(first != second);
}

TEST_CASE(password_verification_rejects_wrong_password) {
    PasswordHash stored = DerivePassword("correct horse", 1000);
    CHECK(VerifyPassword("correct horse", stored));
    CHECK(!VerifyPassword("wrong battery", stored));
}
```

The test helper adds `CHECK_THROWS_CODE(expression, expected_code)` and an RAII `ScopedEnvironment` that restores each modified variable.

- [x] **Step 2: Run the focused tests**

Run:

```bash
make test TEST_FILTER=config_
make test TEST_FILTER=ids_
make test TEST_FILTER=password_
```

Expected: compilation fails because the new headers do not exist.

- [x] **Step 3: Implement exact configuration fields**

`AppConfig` contains:

```cpp
struct AppConfig {
    std::string listen_address;
    uint16_t port;
    int thread_count;
    int connection_timeout_ms;
    std::string mysql_host;
    uint16_t mysql_port;
    std::string mysql_database;
    std::string mysql_user;
    std::string mysql_password;
    std::string mysql_socket;
    int mysql_pool_size;
    std::string storage_root;
    uint64_t max_file_bytes;
    uint64_t chunk_bytes;
    uint64_t max_json_bytes;
    int session_seconds;
    int password_iterations;
    bool secure_cookie;
    int log_level;
    static AppConfig LoadFromEnvironment();
};
```

Defaults are port `1316`, 6 threads, 60,000 ms timeout, MySQL port `3306`, pool size `12`, 1 GiB files, 8 MiB chunks, 1 MiB JSON, 43,200-second sessions, 210,000 PBKDF2 iterations, non-secure development cookie, and log level `1`. Host, database, user, password, and absolute storage root have no defaults and are mandatory. `SMARTDOCS_MYSQL_SOCKET` is optional; when present, pass it as `unix_socket` to `mysql_real_connect` and use it instead of TCP in scripts and tests.

- [x] **Step 4: Implement core utilities with OS/OpenSSL primitives**

Use `getrandom(2)` and retry `EINTR`; throw `AppError(500, "random_failed", ...)` on a short/failing read. Use `EVP_Digest*`, `PKCS5_PBKDF2_HMAC`, and `CRYPTO_memcmp`; never log input secrets or generated tokens.

```cpp
struct PasswordHash {
    std::vector<unsigned char> salt;
    std::vector<unsigned char> digest;
    int iterations;
};
```

The salt is 16 bytes, derived digest is 32 bytes, and session tokens are 32 random bytes represented as 64 lowercase hex characters.

- [x] **Step 5: Add the no-secret example configuration**

`config/smart-docs.env.example` lists every variable, uses `change-me` for passwords, `/var/lib/smart-docs` for storage, and comments that real secrets belong in a protected environment file outside Git.

- [x] **Step 6: Verify and commit**

```bash
make test
git diff --check
git add Makefile code/app code/core config test/test_support.h test/unit/config_test.cpp test/unit/core_crypto_test.cpp
git commit -m "feat: add validated configuration and crypto primitives"
```

Expected: all tests pass; the example contains no real credential.

### Task 3: Incremental HTTP Framing, Router, and Streaming Response State

**Files:**
- Modify: `code/http/httprequest.h`
- Modify: `code/http/httprequest.cpp`
- Modify: `code/http/httpresponse.h`
- Modify: `code/http/httpresponse.cpp`
- Create: `code/http/router.h`
- Create: `code/http/router.cpp`
- Modify: `code/http/httpconn.h`
- Modify: `code/http/httpconn.cpp`
- Modify: `code/server/webserver.h`
- Modify: `code/server/webserver.cpp`
- Create: `code/app/application.h`
- Create: `code/app/application.cpp`
- Modify: `code/main.cpp`
- Create: `test/unit/http_request_test.cpp`
- Create: `test/unit/router_test.cpp`
- Create: `test/unit/http_response_test.cpp`
- Create: `test/unit/webserver_socket_test.cpp`

**Interfaces:**
- Produces: `RequestHead`, `HttpRequestParser::Consume(Buffer&)`, `RequestBodyHandler::OnData/Finish`, `Router::Prepare(const RequestHead&)`, and `HttpResponse::Json/File`.
- `WebServer` owns a shared `Application`; every `HttpConn` receives the same immutable route table and per-request dependencies.

- [x] **Step 1: Write failing request-framing tests**

Tests must cover:

```cpp
TEST_CASE(request_body_can_arrive_across_reads);
TEST_CASE(binary_body_preserves_crlf_and_zero_bytes);
TEST_CASE(duplicate_content_length_is_rejected);
TEST_CASE(content_length_with_transfer_encoding_is_rejected);
TEST_CASE(chunked_transfer_encoding_is_rejected);
TEST_CASE(request_line_over_8192_bytes_is_rejected);
TEST_CASE(headers_over_32768_bytes_are_rejected);
TEST_CASE(pipelined_second_request_is_rejected);
```

`request_body_can_arrive_across_reads` first feeds headers plus two body bytes and expects `NeedBody`, then feeds the remaining three bytes and expects `Complete` with exactly five bytes delivered.

- [x] **Step 2: Define the HTTP interfaces**

```cpp
struct RequestHead {
    std::string method;
    std::string path;
    std::string query;
    std::string version;
    std::unordered_map<std::string, std::string> headers;
    uint64_t content_length;
};

class RequestBodyHandler {
public:
    virtual ~RequestBodyHandler() = default;
    virtual void OnData(const char* data, size_t size) = 0;
    virtual HttpResponse Finish() = 0;
};

using RouteParams = std::unordered_map<std::string, std::string>;

class Router {
public:
    using Factory = std::function<std::unique_ptr<RequestBodyHandler>(const RequestHead&, const RouteParams&)>;
    void Add(std::string method, std::string pattern, Factory factory);
    std::unique_ptr<RequestBodyHandler> Prepare(const RequestHead& head) const;
};
```

Route patterns contain literal segments and `{name}` parameters only. Percent-decode path segments once, reject invalid escapes, encoded `/`, NUL, `.` and `..`, and keep query values separate from the path.

- [x] **Step 3: Replace request parsing and response representation**

Parser states are `RequestLine`, `Headers`, `Body`, `Complete`, and `Error`; parser state persists until a response completes. Normalize header names to lowercase, trim optional whitespace, require one valid decimal `Content-Length`, and allow bodyless GET/DELETE requests without that header.

```cpp
struct FileRegion {
    int fd = -1;
    uint64_t offset = 0;
    uint64_t length = 0;
};

class HttpResponse {
public:
    static HttpResponse Json(int status, const nlohmann::json& body);
    static HttpResponse File(int status, FileRegion region,
                             std::vector<std::pair<std::string, std::string>> headers);
    int status() const;
    const std::string& head_and_body() const;
    FileRegion& file_region();
};
```

JSON responses include `Content-Type: application/json; charset=utf-8`, `Content-Length`, `X-Content-Type-Options: nosniff`, and `Connection`. File descriptors close exactly once in `HttpResponse` destruction/move assignment.

- [x] **Step 4: Wire the connection state machine and health routes**

`HttpConn::process()` parses a head, calls `Router::Prepare` before consuming a body, streams available bytes to the returned handler, and builds the response only after exactly `Content-Length` bytes. `HttpConn::write()` sends headers/body with `writev`, then file bytes with nonblocking `sendfile`; `EAGAIN` keeps `EPOLLOUT` armed.

Replace `unordered_map<int, HttpConn>` with `unordered_map<int, shared_ptr<HttpConn>>`. Worker jobs capture a `shared_ptr`; timers capture a `weak_ptr`; `EPOLLONESHOT` plus a per-connection mutex ensures at most one read/process/write state transition at a time. Closing is idempotent and the event loop removes the fd before the last owner releases it. Fix nonblocking setup to use `F_GETFL`/`F_SETFL` rather than `F_GETFD`, and add a socket test that confirms `O_NONBLOCK` is set.

Register:

```text
GET /api/v1/health/live  -> 200 {"status":"live","request_id":"..."}
GET /api/v1/health/ready -> 503 {"status":"not_ready","request_id":"..."}
```

Readiness becomes real in Task 4. The static app shell is served only from an explicit allowlist and cannot resolve arbitrary paths.

- [x] **Step 5: Run tests and a socket smoke check**

```bash
make test
SMARTDOCS_MYSQL_HOST=127.0.0.1 SMARTDOCS_MYSQL_DATABASE=unused \
SMARTDOCS_MYSQL_USER=unused SMARTDOCS_MYSQL_PASSWORD=unused \
SMARTDOCS_STORAGE_ROOT=/tmp/smart-docs-smoke ./bin/server &
server_pid=$!
curl --fail --silent http://127.0.0.1:1316/api/v1/health/live
kill "$server_pid"
wait "$server_pid" || true
```

Expected: all unit tests pass and curl returns a JSON `live` response. Use a `mktemp -d` path in the actual run and remove only that exact path in the shell trap.

- [x] **Step 6: Commit**

```bash
git add code/http code/server code/app/application.* code/main.cpp test/unit/http_request_test.cpp test/unit/router_test.cpp test/unit/http_response_test.cpp test/unit/webserver_socket_test.cpp
git commit -m "feat: add incremental HTTP API transport"
```

### Task 4: MySQL Schema, Safe Connections, and Explicit Migrations

**Files:**
- Create: `db/migrations/001_m1_core.sql`
- Create: `code/db/mysql.h`
- Create: `code/db/mysql.cpp`
- Create: `code/db/schema.h`
- Create: `code/db/schema.cpp`
- Delete: `code/pool/sqlconnRAII.h`
- Modify: `code/pool/sqlconnpool.h`
- Modify: `code/pool/sqlconnpool.cpp`
- Create: `scripts/migrate.sh`
- Create: `scripts/test-with-mysql.sh`
- Create: `test/integration/mysql_schema_test.cpp`
- Create: `test/integration/mysql_transaction_test.cpp`
- Create: `test/mysql_test_support.h`
- Create: `test/mysql_test_support.cpp`
- Modify: `code/app/application.cpp`
- Modify: `Makefile`

**Interfaces:**
- Produces: `MySqlPool::Acquire()`, move-only `MySqlConnection`, `MySqlTransaction`, `MySqlStatement`, and `Schema::RequireVersion(connection, 1)`.
- Later repositories use prepared statements only; no caller receives a raw pooled connection without RAII ownership.

`SqlValue` is a tagged value with constructors for `std::string`, `uint64_t`, `int64_t`, Boolean, UTC timestamp text, and SQL NULL. `MySqlRow` exposes checked `String`, `UInt64`, `Int64`, `Bool`, and `IsNull` accessors by zero-based result column. `MySqlConnection::Execute(sql, values)` returns affected rows; `Query` returns `std::vector<MySqlRow>`; `ScalarInt` requires exactly one numeric cell.

- [x] **Step 1: Write failing pool, migration, and transaction tests**

Cover connection failure without inserting null handles, one-and-only-one connection return, rollback on destructor, commit persistence, schema version `1`, duplicate active file-name rejection, duplicate upload-part rejection, and one-root-directory-per-project rejection.

```cpp
TEST_CASE(transaction_rolls_back_without_commit) {
    MySqlConnection connection = TestDatabase().Acquire();
    {
        MySqlTransaction transaction(connection);
        connection.Execute("INSERT INTO schema_test(value) VALUES (?)", {SqlValue("rolled-back")});
    }
    CHECK(connection.ScalarInt("SELECT COUNT(*) FROM schema_test WHERE value=?", {SqlValue("rolled-back")}) == 0);
}
```

- [x] **Step 2: Create the complete M1 schema**

`001_m1_core.sql` creates `schema_migrations`, `users`, `auth_sessions`, `projects`, `project_members`, `directories`, `files`, `file_versions`, `upload_tasks`, `upload_parts`, `processing_jobs`, and `audit_records`. Use `CHAR(32) CHARACTER SET ascii COLLATE ascii_bin` IDs, `DATETIME(6)` UTC timestamps, `BIGINT UNSIGNED` byte counts, InnoDB, and foreign keys with `ON DELETE RESTRICT` except sessions (`ON DELETE CASCADE`).

Required generated constraints:

```sql
ALTER TABLE directories
  ADD COLUMN parent_scope CHAR(32) CHARACTER SET ascii COLLATE ascii_bin
    GENERATED ALWAYS AS (COALESCE(parent_id, '00000000000000000000000000000000')) STORED,
  ADD COLUMN root_marker TINYINT
    GENERATED ALWAYS AS (CASE WHEN parent_id IS NULL THEN 1 ELSE NULL END) STORED,
  ADD UNIQUE KEY uq_directory_sibling (project_id, parent_scope, name),
  ADD UNIQUE KEY uq_project_root (project_id, root_marker),
  ADD UNIQUE KEY uq_directory_project_id (project_id, id);

ALTER TABLE files
  ADD COLUMN active_name VARCHAR(255)
    GENERATED ALWAYS AS (CASE WHEN deleted_at IS NULL THEN name ELSE NULL END) STORED,
  ADD UNIQUE KEY uq_active_file_name (project_id, directory_id, active_name),
  ADD UNIQUE KEY uq_file_project_id (project_id, id);

ALTER TABLE file_versions
  ADD UNIQUE KEY uq_file_version_number (file_id, version_number),
  ADD UNIQUE KEY uq_content_id (content_id),
  ADD UNIQUE KEY uq_version_file_id (file_id, id);

ALTER TABLE upload_parts
  ADD PRIMARY KEY (upload_task_id, part_number);
```

Add checks for valid roles, policies, states, nonnegative file sizes, positive configured chunk/part sizes, 64-character lowercase SHA-256 strings, part-number bounds, and positive version numbers. Insert migration version `1` only after all DDL succeeds.

- [x] **Step 3: Replace the unsafe pool and add database RAII**

`MySqlPool::Initialize` attempts every configured connection before publishing the pool; one failed connection closes all opened handles and throws `database_unavailable`. `Acquire` waits on the semaphore before inspecting the queue. `MySqlConnection` is move-only and returns the handle exactly once.

`MySqlStatement` wraps `mysql_stmt_prepare`, typed input binds, `mysql_stmt_execute`, result metadata, and fetch. Map duplicate-key error `1062` to `AppError(409, "constraint_conflict", ...)`, deadlock/lock timeout to retryable `503`, and all other database errors to a sanitized `503 database_unavailable`.

- [x] **Step 4: Add explicit migration and isolated-test scripts**

`scripts/migrate.sh` requires host, database, user, and password plus either a TCP port or `SMARTDOCS_MYSQL_SOCKET`, refuses an empty database name, applies numbered SQL files in lexical order, and confirms:

```sql
SELECT MAX(version) FROM schema_migrations;
```

Because MySQL DDL auto-commits, migration 001 requires an empty application schema when version 1 is absent. If an M1 table exists without the version row, the script exits with `partial_schema` and instructs the operator to restore or recreate that application database; it never guesses which partial statements are safe to replay.

`scripts/test-with-mysql.sh` creates a `mktemp -d` data directory, initializes MySQL with `mysqld --no-defaults --initialize-insecure`, starts it on a private Unix socket with networking disabled, creates `smart_docs_test`, applies migrations, runs the command passed after `--`, and stops the exact PID in a trap. When running as root, pass `--user=root`; otherwise pass the current user. Never reuse the system data directory. `test/mysql_test_support.{h,cpp}` reads those exported connection variables, provides `TestDatabase()`, and truncates M1 tables in foreign-key-safe order between cases.

- [x] **Step 5: Make readiness authoritative**

`Application::Ready()` returns true only when a pool connection succeeds, schema version equals `1`, the storage root can be opened as a directory without following a symlink, and required `objects/` and `staging/` directories exist with no group/other write permission. `/health/ready` returns 200 only in that state.

- [x] **Step 6: Verify and commit**

```bash
make test
scripts/test-with-mysql.sh -- ./bin/smartdocs_tests --filter=mysql_
git diff --check
git add Makefile code/app/application.cpp code/db code/pool db scripts test/mysql_test_support.* test/integration
git commit -m "feat: add transactional M1 metadata store"
```

Expected: unit tests and isolated MySQL tests pass; no system database is modified.

### Task 5: Sessions, Project Membership, Roles, and Directories

**Files:**
- Create: `code/auth/auth_repository.h`
- Create: `code/auth/auth_repository.cpp`
- Create: `code/auth/auth_service.h`
- Create: `code/auth/auth_service.cpp`
- Create: `code/auth/auth_routes.h`
- Create: `code/auth/auth_routes.cpp`
- Create: `code/project/project_repository.h`
- Create: `code/project/project_repository.cpp`
- Create: `code/project/project_service.h`
- Create: `code/project/project_service.cpp`
- Create: `code/project/project_routes.h`
- Create: `code/project/project_routes.cpp`
- Create: `code/admin/main.cpp`
- Modify: `code/app/application.cpp`
- Modify: `Makefile`
- Create: `test/unit/authorization_test.cpp`
- Create: `test/integration/auth_project_test.cpp`

**Interfaces:**
- Produces: `SessionContext AuthService::Authenticate(cookie)`, `Role ProjectService::RequireRole(user, project, minimum)`, project/member/directory services, and working identity/project HTTP routes.
- `SessionContext` contains only authenticated user/session IDs and expiry; route bodies cannot override it.

Domain values are declared in the owning headers:

```cpp
enum class Role { Reader, Editor, Admin };
struct SessionContext { std::string user_id; std::string session_id; std::string expires_at; };
struct Project { std::string id; std::string name; std::string root_directory_id; };
struct ProjectMembership { Project project; Role role; };
struct Directory { std::string id; std::string project_id; std::string parent_id; std::string name; };
```

Authorization uses an explicit action-to-role switch matching the approved matrix, not numeric enum comparison.

- [ ] **Step 1: Write the role matrix and session tests**

Use three users and two projects. Assert that readers can list but cannot mutate, editors can upload/file-mutate but cannot manage members or AI policy, admins can manage members, and membership in project A grants nothing in project B. Add expired, revoked, malformed, and missing session-cookie cases.

```cpp
TEST_CASE(account_identity_does_not_grant_other_project_access) {
    CHECK(Authorize(Role::Editor, Action::ReadFile));
    CHECK_THROWS_CODE(project_service.RequireRole(editor_id, other_project_id, Role::Reader),
                      "resource_not_found");
}
```

- [ ] **Step 2: Implement parameterized repositories and services**

Exact service methods:

```cpp
SessionTokens AuthService::Login(const std::string& username, const std::string& password);
void AuthService::Logout(const std::string& raw_token);
SessionContext AuthService::Authenticate(const std::string& raw_token);
Project ProjectService::CreateProject(const std::string& user_id, const std::string& name);
std::vector<ProjectMembership> ProjectService::ListForUser(const std::string& user_id);
void ProjectService::SetMemberRole(const SessionContext&, const std::string& project_id,
                                   const std::string& member_user_id, Role role);
Directory ProjectService::CreateDirectory(const SessionContext&, const std::string& project_id,
                                          const std::string& parent_id, const std::string& name);
Directory ProjectService::RenameDirectory(const SessionContext&, const std::string& project_id,
                                          const std::string& directory_id, const std::string& name);
```

Project creation uses one transaction: insert project, insert its `/` root, update `root_directory_id`, and add the creator as admin. Directory creation checks the parent belongs to the same project.

- [ ] **Step 3: Implement same-origin API routes**

Register all spec section 6.1 and directory routes. Login sets `smartdocs_session=<token>; HttpOnly; SameSite=Strict; Path=/; Max-Age=<seconds>` and adds `Secure` when configured. Mutating cookie-authenticated requests reject a present mismatched `Origin`, require `Host`, and never emit CORS credentials headers.

All responses include `request_id`. Missing/hidden project resources return `404 resource_not_found`; a known project with an insufficient role returns `403 forbidden`.

- [ ] **Step 4: Add the local user-bootstrap CLI**

Command:

```bash
printf '%s\n' 'development-password' | ./bin/smartdocs-admin create-user --username admin --password-stdin
```

The CLI refuses a TTY-less invocation without `--password-stdin`, enforces username length 3-64 and password length 12-1024, reads exactly one newline-terminated password, derives it through `AuthService`, and never echoes/logs it. A duplicate username exits `2` with `username_conflict`.

- [ ] **Step 5: Run the vertical-slice integration test**

```bash
make server admin test
scripts/test-with-mysql.sh -- ./bin/smartdocs_tests --filter=auth_project_
```

Expected: login, create project, add users, create/rename directories, role denial, and cross-project denial all pass through repositories and services.

- [ ] **Step 6: Commit**

```bash
git add Makefile code/admin code/app/application.cpp code/auth code/project test/unit/authorization_test.cpp test/integration/auth_project_test.cpp
git commit -m "feat: add project-scoped authentication and roles"
```

### Task 6: Immutable File Store, File Metadata, and Versions

**Files:**
- Create: `code/file/file_store.h`
- Create: `code/file/file_store.cpp`
- Create: `code/file/file_repository.h`
- Create: `code/file/file_repository.cpp`
- Create: `code/file/file_service.h`
- Create: `code/file/file_service.cpp`
- Create: `code/file/file_routes.h`
- Create: `code/file/file_routes.cpp`
- Modify: `code/app/application.cpp`
- Create: `test/unit/file_store_test.cpp`
- Create: `test/integration/file_metadata_test.cpp`

**Interfaces:**
- Produces: safe staging/object primitives, paginated file listing, version listing, and fixed-version lookup.
- Upload code depends on `FileStore::CreatePartWriter`, `FileStore::Assemble`, and `FileStore::PublishObject`.

```cpp
template <typename T> struct Page { std::vector<T> items; uint64_t page; uint64_t page_size; uint64_t total; };
struct FileSummary { std::string id; std::string project_id; std::string directory_id; std::string name; std::string current_version_id; bool deleted; std::string remote_ai_policy; };
struct FileVersionSummary { std::string id; std::string file_id; uint64_t version_number; uint64_t size; std::string sha256; std::string media_type; std::string processing_state; bool remote_ai_approved; };
struct AuthorizedVersion { FileSummary file; FileVersionSummary version; int fd; };
```

`AuthorizedVersion` is move-only and closes `fd` unless ownership moves into `HttpResponse::File`.

- [ ] **Step 1: Write failing path and metadata tests**

Tests reject symlink storage roots, user names such as `../../resources/index.html`, object IDs with non-hex characters, cross-project directory/file combinations, and duplicate active names. Tests also prove that soft-deleted names may be reused but restoring the old file then conflicts.

- [ ] **Step 2: Define immutable-store interfaces**

```cpp
class FileStore {
public:
    explicit FileStore(const std::string& root);
    PartWriter CreatePartWriter(const std::string& task_id, uint32_t part_number);
    StoredTemp Assemble(const std::string& task_id, const std::vector<PartInfo>& parts);
    PublishedObject PublishObject(StoredTemp assembled, const std::string& content_id);
    int OpenObject(const std::string& content_id) const;
    bool ObjectExists(const std::string& content_id) const;
    void RemoveTaskTemporaryFiles(const std::string& task_id);
};
```

The value types are fixed as:

```cpp
struct PartInfo {
    uint32_t part_number;
    uint64_t size;
    std::string sha256;
};
struct StoredTemp {
    std::string task_id;
    uint64_t size;
    std::string sha256;
};
struct PublishedObject {
    std::string content_id;
    uint64_t size;
    std::string sha256;
};
```

`PartWriter` is move-only and exposes `Write(const char*, size_t)` and `Finish() -> PartInfo`; unfinished writers remove only their unique temporary file.

Every directory traversal uses directory file descriptors and `openat`/`mkdirat` with `O_NOFOLLOW`. `PartWriter` streams bytes through `write(2)`, tracks byte count and OpenSSL SHA-256 state, `fsync`s before atomic rename, and removes its uniquely named temporary file on failure.

- [ ] **Step 3: Implement file repository and read services**

```cpp
Page<FileSummary> FileService::List(const SessionContext&, const std::string& project_id,
                                    const FileListQuery& query);
std::vector<FileVersionSummary> FileService::ListVersions(const SessionContext&,
                                    const std::string& project_id, const std::string& file_id);
AuthorizedVersion FileService::OpenVersion(const SessionContext&, const std::string& project_id,
                                    const std::string& file_id, const std::string& version_id);
```

Page size defaults to 50 and is limited to 100. Default listing excludes `deleted_at IS NOT NULL`; deleted listing is explicit. `OpenVersion` checks membership, project/file/version relationship, deletion, version availability, and object existence before returning an already-open file descriptor.

- [ ] **Step 4: Register metadata routes**

Implement file listing and version listing from spec section 6.2. Content routes remain registered as `501 not_implemented` until Task 9 so clients cannot fall through to static files.

- [ ] **Step 5: Verify and commit**

```bash
make test
scripts/test-with-mysql.sh -- ./bin/smartdocs_tests --filter=file_
git diff --check
git add code/app/application.cpp code/file test/unit/file_store_test.cpp test/integration/file_metadata_test.cpp
git commit -m "feat: add immutable file metadata foundation"
```

### Task 7: Upload Task Creation, Discovery, Cancellation, and Idempotent Parts

**Files:**
- Create: `code/upload/upload_repository.h`
- Create: `code/upload/upload_repository.cpp`
- Create: `code/upload/upload_service.h`
- Create: `code/upload/upload_service.cpp`
- Create: `code/upload/upload_routes.h`
- Create: `code/upload/upload_routes.cpp`
- Create: `code/upload/chunk_body_handler.h`
- Create: `code/upload/chunk_body_handler.cpp`
- Modify: `code/app/application.cpp`
- Create: `test/unit/upload_validation_test.cpp`
- Create: `test/integration/upload_part_test.cpp`

**Interfaces:**
- Produces: upload create/list/get/cancel endpoints and a `ChunkBodyHandler` selected immediately after request-head authorization.
- Task 8 consumes `UploadService::Complete` and repository row-lock operations.

```cpp
enum class UploadMode { CreateFile, CreateVersion };
struct CreateUploadCommand { UploadMode mode; std::string directory_id; std::string name; uint64_t size; std::string sha256; std::string media_type; std::string file_id; std::string observed_current_version_id; };
struct UploadListQuery { std::string state; uint64_t page; uint64_t page_size; };
struct UploadTask { std::string id; std::string project_id; std::string owner_id; std::string state; uint64_t chunk_size; uint64_t part_count; uint64_t received_bytes; std::string file_id; std::string version_id; std::string processing_job_id; std::string failure; };
struct UploadTaskDetail { UploadTask task; std::vector<PartInfo> confirmed_parts; };
struct PartResult { uint32_t part_number; uint64_t size; std::string sha256; bool reused; };
```

- [ ] **Step 1: Write failing upload-boundary tests**

Cover an accepted 0-byte file, rejected over-limit files, invalid SHA strings, invalid part numbers, wrong media type, content-length mismatch, wrong user/project, reader denial, new-file name conflict, new-version target mismatch, cancel ownership, same-part replay, and different-content conflict.

```cpp
TEST_CASE(same_chunk_replay_returns_existing_part) {
    PartResult first = upload_service.StorePart(context, project, task, 0, bytes, digest);
    PartResult replay = upload_service.StorePart(context, project, task, 0, bytes, digest);
    CHECK(first.part_number == replay.part_number);
    CHECK(first.sha256 == replay.sha256);
    CHECK(replay.reused);
}
```

- [ ] **Step 2: Implement task creation and queries**

```cpp
UploadTask UploadService::Create(const SessionContext&, const std::string& project_id,
                                 const CreateUploadCommand& command);
Page<UploadTask> UploadService::ListOwn(const SessionContext&, const std::string& project_id,
                                       const UploadListQuery& query);
UploadTaskDetail UploadService::GetOwn(const SessionContext&, const std::string& project_id,
                                      const std::string& task_id);
void UploadService::Cancel(const SessionContext&, const std::string& project_id,
                           const std::string& task_id);
```

`CreateUploadCommand` has mode, directory ID, display name, expected size/SHA-256/media type, optional target file ID, and optional observed current-version ID. The server returns task ID, configured chunk size, total part count, and confirmed parts. New-version creation captures the current version and new versions default to no remote-AI approval.

- [ ] **Step 3: Prepare and stream a chunk before buffering it**

`upload_routes.cpp` validates method/path/cookie/project/task/part number, `Content-Type`, `Content-Length`, and `X-Chunk-SHA256` from the request head. It then returns a `ChunkBodyHandler` holding an authorized `PartWriter`. `OnData` writes bounded pieces and aborts if more bytes arrive than declared. `Finish` checks declared length and digest before the repository inserts the part row.

The final part length is `file_size - part_number * chunk_size`; all other parts equal `chunk_size`. A zero-byte file has zero parts and completes through Task 8 without accepting a part upload.

- [ ] **Step 4: Implement part conflict semantics**

In a transaction, lock the task, require state `uploading`, and query `(task_id, part_number)`. An existing identical size/digest returns the existing record and deletes the new temporary file. An existing different digest returns `409 chunk_conflict` without replacing the confirmed part. Insert the database row only after the part file has been fsynced and atomically named.

- [ ] **Step 5: Verify all upload query/part routes**

```bash
make test
scripts/test-with-mysql.sh -- ./bin/smartdocs_tests --filter=upload_part_
```

Expected: all tests pass, including concurrent identical PUTs producing one row and one confirmed part file.

- [ ] **Step 6: Commit**

```bash
git add code/app/application.cpp code/upload test/unit/upload_validation_test.cpp test/integration/upload_part_test.cpp
git commit -m "feat: add resumable upload tasks and chunks"
```

### Task 8: Idempotent Upload Completion and Restart Reconciliation

**Files:**
- Modify: `code/upload/upload_repository.h`
- Modify: `code/upload/upload_repository.cpp`
- Modify: `code/upload/upload_service.h`
- Modify: `code/upload/upload_service.cpp`
- Modify: `code/upload/upload_routes.cpp`
- Create: `code/upload/upload_reconciler.h`
- Create: `code/upload/upload_reconciler.cpp`
- Create: `code/core/fault_injector.h`
- Create: `code/core/fault_injector.cpp`
- Modify: `code/app/application.cpp`
- Create: `test/integration/upload_complete_test.cpp`
- Create: `test/integration/upload_recovery_test.cpp`

**Interfaces:**
- Produces: `CompleteUploadResult UploadService::Complete(...)` and `UploadReconciler::RunAtStartup()`.
- Completion always returns the same file, version, and processing-job IDs for a completed task.

```cpp
struct CompleteUploadResult { std::string file_id; std::string version_id; std::string processing_job_id; bool reused; };
```

- [ ] **Step 1: Write failing completion and recovery tests**

Cover missing parts, wrong part order/size, whole-file hash mismatch, duplicate completion, concurrent completion, response-loss retry, current-version change, name conflict, transaction rollback, orphan published object, incomplete temporary file, completed database row, and missing published object.

```cpp
TEST_CASE(completion_is_idempotent) {
    CompleteUploadResult first = upload_service.Complete(context, project, task);
    CompleteUploadResult second = upload_service.Complete(context, project, task);
    CHECK(first.file_id == second.file_id);
    CHECK(first.version_id == second.version_id);
    CHECK(first.processing_job_id == second.processing_job_id);
}
```

- [ ] **Step 2: Implement the exact completion state machine**

The ordered operations are:

```text
lock upload row -> return stored result if completed
validate owner/project/state/parts -> set assembling
stream parts to assembled.tmp and compute SHA-256
fail without publishing when size/hash differs
fsync assembled.tmp -> rename to objects/aa/bb/content-id -> fsync directories
begin transaction -> relock upload row -> recheck permission/target/current version/name
insert file when mode=create_file
insert next immutable file_version with remote approval NULL
update files.current_version_id
insert one pending processing_job
insert audit record
store result IDs and set upload completed
commit -> return stored IDs
```

Use unique constraints plus row locks, not a process-local mutex, as the final concurrency authority. A failed database transaction leaves an unreferenced object for reconciliation and never exposes it through file queries.

- [ ] **Step 3: Add test-only fault injection**

```cpp
enum class FaultPoint {
    AfterPartTempFsync,
    AfterAssembledFsync,
    AfterObjectRename,
    BeforeDatabaseCommit,
    AfterDatabaseCommit,
    BeforeHttpResponse
};
```

`FaultInjector::Hit` is a no-op in normal builds. In `smartdocs_test_server`, `SMARTDOCS_FAULT_POINT=<name>` causes `_exit(86)` at that exact point. Reject this environment variable in the normal server so production cannot enable intentional crashes.

- [ ] **Step 4: Implement startup reconciliation**

Run reconciliation after schema/storage checks and before readiness becomes true. Mark `uploading`, `assembling`, and unverifiable `publishing` tasks as `interrupted`; delete only server-named `*.tmp` files; retain confirmed parts. Repair a task to `completed` only when its result IDs form a valid committed file/version/job graph. Mark a version unavailable and emit an audit error when a published row's object is missing. Remove an orphan object only when no version references it and its age exceeds 24 hours.

- [ ] **Step 5: Verify and commit**

```bash
make test
scripts/test-with-mysql.sh -- ./bin/smartdocs_tests --filter=upload_complete_
scripts/test-with-mysql.sh -- ./bin/smartdocs_tests --filter=upload_recovery_
git diff --check
git add code/app/application.cpp code/core/fault_injector.* code/upload test/integration/upload_complete_test.cpp test/integration/upload_recovery_test.cpp
git commit -m "feat: publish uploads idempotently and recover state"
```

### Task 9: File Mutation, AI Policy, Protected Download, Range, and PDF Preview

**Files:**
- Create: `code/file/range.h`
- Create: `code/file/range.cpp`
- Modify: `code/file/file_repository.h`
- Modify: `code/file/file_repository.cpp`
- Modify: `code/file/file_service.h`
- Modify: `code/file/file_service.cpp`
- Modify: `code/file/file_routes.cpp`
- Modify: `code/http/httpresponse.cpp`
- Create: `test/unit/range_test.cpp`
- Create: `test/integration/file_mutation_test.cpp`
- Create: `test/integration/file_download_test.cpp`

**Interfaces:**
- Produces: rename/move/delete/restore/policy routes and authenticated current/historical version `FileRegion` responses.
- Range API: `ByteRange ParseSingleRange(const std::string& header, uint64_t file_size)`.

```cpp
struct UpdateFileCommand { std::string name; std::string directory_id; };
struct RemoteAiPolicyCommand { std::string policy; std::vector<std::string> approved_version_ids; };
struct RemoteAiState { std::string policy; std::vector<std::string> approved_version_ids; };
```

- [ ] **Step 1: Write failing Range and mutation tests**

Cover `bytes=0-9`, `bytes=10-`, `bytes=-10`, complete-range clamping, zero-length files, reversed/out-of-bounds ranges, overflow, invalid units, commas/multi-range, whitespace ambiguity, and new-version-during-download behavior. Mutation tests cover role matrix, same-directory rename conflicts, cross-project target directory, deleted download, restore conflict, stable IDs after move, and version-specific remote approval.

- [ ] **Step 2: Implement strict single-range parsing**

```cpp
struct ByteRange {
    uint64_t first;
    uint64_t last;
    uint64_t length() const { return last - first + 1; }
};
```

No header means full `200`. One satisfiable range means `206`. Syntactically invalid or unsatisfiable input means `416` with `Content-Range: bytes */<size>`. Any comma means `501 range_not_supported`. Parse digits with checked arithmetic; never rely on signed `atoi`.

- [ ] **Step 3: Implement transactional file mutations**

Exact service methods:

```cpp
FileSummary FileService::Update(const SessionContext&, const std::string& project_id,
                                const std::string& file_id, const UpdateFileCommand&);
void FileService::SoftDelete(const SessionContext&, const std::string& project_id,
                             const std::string& file_id);
FileSummary FileService::Restore(const SessionContext&, const std::string& project_id,
                                 const std::string& file_id);
RemoteAiState FileService::SetRemoteAiPolicy(const SessionContext&, const std::string& project_id,
                                 const std::string& file_id, const RemoteAiPolicyCommand&);
```

Use `SELECT ... FOR UPDATE`, recheck membership and destination directory, rely on the active-name unique index, record audit results, and leave file/version/content IDs unchanged. Setting a file to `internal_only` clears all version approval timestamps in the same transaction. Setting `remote_ai_allowed` approves only explicitly listed version IDs that belong to that file.

- [ ] **Step 4: Implement authenticated download responses**

Resolve the current version once, or require the exact historical version route. Check authorization and deletion before `FileStore::OpenObject`. Use the already-open descriptor for the response, so a concurrent new version cannot affect bytes. Return `Accept-Ranges: bytes`, correct lengths, escaped RFC 5987 filename, `nosniff`, and `Content-Disposition: inline` only for PDF; every other upload is `attachment`.

- [ ] **Step 5: Verify and commit**

```bash
make test
scripts/test-with-mysql.sh -- ./bin/smartdocs_tests --filter=range_
scripts/test-with-mysql.sh -- ./bin/smartdocs_tests --filter=file_mutation_
scripts/test-with-mysql.sh -- ./bin/smartdocs_tests --filter=file_download_
git diff --check
git add code/file code/http/httpresponse.cpp test/unit/range_test.cpp test/integration/file_mutation_test.cpp test/integration/file_download_test.cpp
git commit -m "feat: add protected versioned file operations"
```

### Task 10: M1 Same-Origin Web Interface and Resumable Upload Controller

**Files:**
- Create: `resources/app.html`
- Create: `resources/css/app.css`
- Create: `resources/js/api.js`
- Create: `resources/js/app.js`
- Create: `resources/js/uploads.js`
- Create: `resources/js/sha256.js`
- Modify: `resources/index.html`
- Create: `test/e2e/ui_contract_test.py`

**Interfaces:**
- Produces: login/project selection, file workbench, upload panel, version/detail/preview, and member settings pages backed only by M1 APIs.
- Uses current-user upload listing to restore task state after refresh; file bytes must be reselected by the user.

- [ ] **Step 1: Add a failing UI contract test**

`ui_contract_test.py` uses `html.parser` and file reads to require accessible labels, live status regions, keyboard-operable controls, the five M1 page sections, no hard-coded credential/API host, no `innerHTML` assignment with API data, and imports of `api.js`, `uploads.js`, and `sha256.js`.

Run:

```bash
python3 test/e2e/ui_contract_test.py
```

Expected: fail because `resources/app.html` does not exist.

- [ ] **Step 2: Vendor a pinned incremental SHA-256 browser module**

Vendor `js-sha256` version `0.11.1` from its official tagged distribution into `resources/js/sha256.js`, retain its MIT header/license notice, and expose only the incremental `sha256.create().update(arrayBuffer).hex()` API used by `uploads.js`. Record the upstream URL and version in a comment; do not use `crypto.subtle.digest` on a whole 1 GiB `ArrayBuffer`.

- [ ] **Step 3: Implement the API and upload modules**

`api.js` always uses relative `/api/v1/...` URLs, `credentials: "same-origin"`, JSON content type for metadata, and converts the unified error body into an `ApiError` containing HTTP status/code/retryability/request ID.

`uploads.js` performs:

```text
incrementally hash selected file -> create upload task
GET task to obtain confirmed part numbers
slice one configured part -> PUT raw Blob with X-Chunk-SHA256
update confirmed byte count only after server confirmation
pause by stopping new PUT requests; cancel via server API
on refresh list current user's active/interrupted tasks
on resume require reselect -> hash/size compare -> upload only missing parts
complete once -> display returned file/version/job IDs
```

Use at most four parallel PUTs, configurable in one constant. Do not manufacture percent progress for hashing, server assembly, or processing; display named phases.

- [ ] **Step 4: Implement the M1 page states**

Build semantic views for login/project role, directory/file table, upload tasks, version list, download/PDF link, remote-AI policy, and member roles. Distinguish idle, loading, awaiting reselect, uploading, assembling, interrupted, failed, cancelled, and completed. Render API strings with `textContent`, not HTML interpolation.

- [ ] **Step 5: Verify and commit**

```bash
python3 test/e2e/ui_contract_test.py
make test
git diff --check
git add resources test/e2e/ui_contract_test.py
git commit -m "feat: add M1 document workbench UI"
```

### Task 11: Real-Socket M1 Acceptance and Twenty Interruption Rounds

**Files:**
- Create: `test/e2e/m1_http_test.py`
- Create: `test/e2e/m1_interrupt_test.py`
- Create: `test/e2e/run_m1.sh`
- Create: `test/fixtures/m1/sample.pdf`
- Create: `test/fixtures/m1/plain.txt`
- Create: `docs/evidence/m1/README.md`
- Create: `docs/evidence/m1/.gitkeep`
- Modify: `.gitignore`

**Interfaces:**
- Produces: one command that starts isolated MySQL/storage/server, executes the M1 subset of T-01 through T-09, and writes machine-readable evidence tied to the current commit.

- [ ] **Step 1: Write the black-box test before wiring the runner**

`m1_http_test.py` uses `http.client` plus `http.cookiejar` and creates admin/editor/reader users and projects A/B through the CLI/API. It asserts:

```text
T-01: ID substitution cannot list/download/Range across projects or roles
T-02: identical chunk replay is reused; different bytes conflict
T-03: confirmed parts survive server restart and resume to the expected SHA-256
T-04: repeated complete returns identical file/version/job IDs
T-05: full/open/closed/suffix/invalid/multi Range and PDF #page URL behavior
T-06: old version URL remains old while current URL resolves the new version
T-07: an upload created against an old current version cannot replace a newer current version; stale parsing/index publication remains a named M2 check
T-08: move preserves IDs; delete/restore/permission change immediately affect list/download
T-09: processing status is visible and never claims parsing/indexing succeeded in M1
```

Expected initial run: fail because no isolated runner supplies service URLs and credentials.

- [ ] **Step 2: Implement the isolated runner**

`run_m1.sh` creates exact `mktemp -d` directories for MySQL, storage, logs, and evidence; starts MySQL on a private socket, migrates, creates users, starts `bin/smartdocs_test_server` on a free loopback port, and runs both Python scripts. A trap terminates only captured PIDs and removes only the created temporary directory.

Evidence files are written to `docs/evidence/m1/latest/` during a run and ignored by default. The operator copies a completed, reviewed run to a dated directory before committing it.

- [ ] **Step 3: Implement twenty deterministic interruption rounds**

Run these cases with fixed input/expected result and a fresh task where needed:

```text
4 rounds AfterPartTempFsync
4 rounds AfterAssembledFsync
4 rounds AfterObjectRename
3 rounds BeforeDatabaseCommit
3 rounds AfterDatabaseCommit
2 rounds BeforeHttpResponse
```

For each round, `m1_interrupt_test.py` records fault point, process exit, task state after restart, confirmed parts, result IDs, final content SHA-256, duplicate-version count, and pass/fail. Success requires: no half-published download, no duplicate file/version/job, truthful interrupted/completed state, and correct final hash after permitted retry.

- [ ] **Step 4: Run the full M1 evidence command**

```bash
make clean
make server admin test-server test
test/e2e/run_m1.sh
```

Expected: C++ tests pass, the M1 HTTP scenario passes, all 20 interruption rounds pass, and `docs/evidence/m1/latest/results.json` reports actual counts rather than fixed success text.

- [ ] **Step 5: Commit tests without claiming unreviewed results**

```bash
git add .gitignore test/e2e test/fixtures docs/evidence/m1/README.md docs/evidence/m1/.gitkeep
git commit -m "test: add reproducible M1 acceptance scenarios"
```

### Task 12: M1 Documentation, Evidence Review, and Stage Gate

**Files:**
- Modify: `README.md`
- Create: `docs/deployment/m1-single-host.md`
- Create: `docs/api/m1-http-api.md`
- Create: `docs/evidence/m1/verified/environment.txt`
- Create: `docs/evidence/m1/verified/results.json`
- Create: `docs/evidence/m1/verified/summary.md`

**Interfaces:**
- Produces: reproducible deployment/API instructions and an evidence-backed decision on whether M1 can close.
- M2 may start only if every M1 gate below is proved by current evidence.

- [ ] **Step 1: Write deployment and API documentation from the running system**

Document exact dependency versions, MySQL database/user creation, migration, protected environment file permissions, storage permissions, admin creation, build/start/stop, reverse-proxy TLS expectation, backup inputs, every M1 route, request/response examples, limits, and error codes. Do not copy passwords, session cookies, absolute temporary paths, or test database contents into Git.

- [ ] **Step 2: Capture an actual dated evidence run**

```bash
mkdir -p docs/evidence/m1/verified
cp docs/evidence/m1/latest/environment.txt docs/evidence/m1/verified/environment.txt
cp docs/evidence/m1/latest/results.json docs/evidence/m1/verified/results.json
cp docs/evidence/m1/latest/summary.md docs/evidence/m1/verified/summary.md
```

Before staging, inspect all three files for secrets and confirm `results.json` contains the current `git rev-parse HEAD`, compiler/MySQL/OpenSSL versions, individual test counts, all interruption rounds, and failure details.

- [ ] **Step 3: Run the M1 completion audit**

```bash
make clean
make server admin test-server test
test/e2e/run_m1.sh
git diff --check
git status --short
```

Manually map each F-01 through F-04 rule and the M1 portions of T-01 through T-09 to a named test/evidence record. Any missing, failed, flaky, indirect, or manually assumed item keeps M1 open; document it under `Known gaps` and do not start M2.

- [ ] **Step 4: Update README truthfully**

Replace “current WebServer baseline only” text only for capabilities proved by the dated evidence. Keep M2-M5 explicitly planned. Report measured environment and denominators next to any throughput or correctness number; omit metrics not actually measured.

- [ ] **Step 5: Commit the reviewed M1 handoff**

```bash
git add README.md docs/deployment docs/api docs/evidence/m1
git commit -m "docs: record verified M1 file service"
git status --short --branch
```

Expected: clean worktree and an evidence-backed M1 status. Push only after verifying `git remote -v`, current branch, and that the destination is `NoUmbrellaK/Smart-Docs-Platform`; never force-push.
