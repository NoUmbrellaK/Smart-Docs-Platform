#include "mysql_test_support.h"

#include "test_support.h"

#include <cstdlib>
#include <memory>
#include <string>

namespace {

std::string Environment(const char* name, const char* fallback = "") {
    const char* value = std::getenv(name);
    return value == nullptr ? std::string(fallback) : std::string(value);
}

}  // namespace

bool MySqlTestsConfigured() {
    return Environment("SMARTDOCS_TEST_MYSQL") == "1";
}

void RequireMySqlTests() {
    if (!MySqlTestsConfigured()) {
        SKIP_TEST("set SMARTDOCS_TEST_MYSQL=1 via scripts/test-with-mysql.sh");
    }
}

MySqlConfig TestMySqlConfig(int pool_size) {
    MySqlConfig config;
    config.host = Environment("SMARTDOCS_MYSQL_HOST", "localhost");
    config.port = static_cast<uint16_t>(
        std::stoul(Environment("SMARTDOCS_MYSQL_PORT", "3306")));
    config.database = Environment("SMARTDOCS_MYSQL_DATABASE", "smart_docs_test");
    config.user = Environment("SMARTDOCS_MYSQL_USER", "smart_docs_test");
    config.password = Environment("SMARTDOCS_MYSQL_PASSWORD", "test-only-password");
    config.socket = Environment("SMARTDOCS_MYSQL_SOCKET");
    config.pool_size = pool_size;
    return config;
}

AppConfig TestAppConfig(const std::string& storage_root, int pool_size) {
    AppConfig config{};
    const MySqlConfig mysql = TestMySqlConfig(pool_size);
    config.listen_address = "127.0.0.1";
    config.port = 1316;
    config.thread_count = 1;
    config.connection_timeout_ms = 1000;
    config.mysql_host = mysql.host;
    config.mysql_port = mysql.port;
    config.mysql_database = mysql.database;
    config.mysql_user = mysql.user;
    config.mysql_password = mysql.password;
    config.mysql_socket = mysql.socket;
    config.mysql_pool_size = mysql.pool_size;
    config.storage_root = storage_root;
    config.max_file_bytes = 1024;
    config.chunk_bytes = 256;
    config.max_json_bytes = 1024;
    config.session_seconds = 60;
    config.password_iterations = 1000;
    config.secure_cookie = false;
    config.log_level = 3;
    return config;
}

MySqlPool& TestDatabase() {
    static std::unique_ptr<MySqlPool> database;
    if (!database) {
        database.reset(new MySqlPool());
        database->Initialize(TestMySqlConfig(4));
    }
    return *database;
}

void ResetTestDatabase() {
    MySqlConnection connection = TestDatabase().Acquire();
    connection.Execute("SET FOREIGN_KEY_CHECKS=0");
    try {
        const char* tables[] = {
            "audit_records", "upload_parts", "processing_jobs", "upload_tasks",
            "file_versions", "files", "directories", "project_members",
            "projects", "auth_sessions", "users"};
        for (const char* table : tables) {
            connection.Execute(std::string("TRUNCATE TABLE ") + table);
        }
    } catch (...) {
        connection.Execute("SET FOREIGN_KEY_CHECKS=1");
        throw;
    }
    connection.Execute("SET FOREIGN_KEY_CHECKS=1");
}
