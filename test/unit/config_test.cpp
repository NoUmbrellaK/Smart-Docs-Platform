#include "app/config.h"
#include "core/app_error.h"
#include "../test_support.h"

namespace {

void SetValidEnvironment(ScopedEnvironment& environment) {
    environment.Set("SMARTDOCS_MYSQL_HOST", "127.0.0.1");
    environment.Set("SMARTDOCS_MYSQL_DATABASE", "smart_docs_test");
    environment.Set("SMARTDOCS_MYSQL_USER", "smart_docs");
    environment.Set("SMARTDOCS_MYSQL_PASSWORD", "development-only");
    environment.Set("SMARTDOCS_STORAGE_ROOT", "/tmp/smart-docs-config-test");
    // Defaults must not depend on the acceptance runner's resource settings.
    // ScopedEnvironment restores these overrides for subsequent DB/HTTP tests.
    for (const char* name : {
             "SMARTDOCS_LISTEN_ADDRESS", "SMARTDOCS_PORT",
             "SMARTDOCS_THREAD_COUNT", "SMARTDOCS_CONNECTION_TIMEOUT_MS",
             "SMARTDOCS_MYSQL_PORT", "SMARTDOCS_MYSQL_SOCKET",
             "SMARTDOCS_MYSQL_POOL_SIZE", "SMARTDOCS_MAX_FILE_BYTES",
             "SMARTDOCS_CHUNK_BYTES", "SMARTDOCS_MAX_JSON_BYTES",
             "SMARTDOCS_SESSION_SECONDS", "SMARTDOCS_PASSWORD_ITERATIONS",
             "SMARTDOCS_SECURE_COOKIE", "SMARTDOCS_LOG_LEVEL"}) {
        environment.Unset(name);
    }
}

}  // namespace

TEST_CASE(config_rejects_missing_mysql_password) {
    ScopedEnvironment environment;
    SetValidEnvironment(environment);
    environment.Unset("SMARTDOCS_MYSQL_PASSWORD");
    CHECK_THROWS_CODE(AppConfig::LoadFromEnvironment(), "config_missing");
}

TEST_CASE(config_uses_documented_defaults) {
    ScopedEnvironment environment;
    SetValidEnvironment(environment);
    const AppConfig config = AppConfig::LoadFromEnvironment();
    CHECK(config.port == 1316);
    CHECK(config.thread_count == 6);
    CHECK(config.connection_timeout_ms == 60000);
    CHECK(config.mysql_port == 3306);
    CHECK(config.mysql_pool_size == 12);
    CHECK(config.max_file_bytes == 1024ULL * 1024ULL * 1024ULL);
    CHECK(config.chunk_bytes == 8ULL * 1024ULL * 1024ULL);
    CHECK(config.max_json_bytes == 1024ULL * 1024ULL);
    CHECK(config.session_seconds == 43200);
    CHECK(config.password_iterations == 210000);
    CHECK(!config.secure_cookie);
}

TEST_CASE(config_accepts_private_mysql_socket) {
    ScopedEnvironment environment;
    SetValidEnvironment(environment);
    environment.Set("SMARTDOCS_MYSQL_SOCKET", "/tmp/smart-docs-mysql.sock");
    const AppConfig config = AppConfig::LoadFromEnvironment();
    CHECK(config.mysql_socket == "/tmp/smart-docs-mysql.sock");
}

TEST_CASE(config_rejects_invalid_numeric_values) {
    ScopedEnvironment environment;
    SetValidEnvironment(environment);
    environment.Set("SMARTDOCS_PORT", "70000");
    CHECK_THROWS_CODE(AppConfig::LoadFromEnvironment(), "config_invalid");
}

TEST_CASE(config_rejects_relative_storage_root) {
    ScopedEnvironment environment;
    SetValidEnvironment(environment);
    environment.Set("SMARTDOCS_STORAGE_ROOT", "relative/path");
    CHECK_THROWS_CODE(AppConfig::LoadFromEnvironment(), "config_invalid");
}
