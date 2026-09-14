#include "config.h"

#include "core/app_error.h"

#include <cerrno>
#include <cstdlib>
#include <limits>
#include <string>

namespace {

std::string Required(const char* name) {
    const char* value = std::getenv(name);
    if (value == nullptr || *value == '\0') {
        throw AppError(500, "config_missing",
                       std::string("Missing required environment variable: ") + name);
    }
    return value;
}

std::string Optional(const char* name, const char* fallback) {
    const char* value = std::getenv(name);
    return value == nullptr || *value == '\0' ? std::string(fallback) : value;
}

uint64_t Unsigned(const char* name, uint64_t fallback, uint64_t minimum,
                  uint64_t maximum) {
    const char* raw = std::getenv(name);
    if (raw == nullptr || *raw == '\0') {
        return fallback;
    }
    if (*raw == '-') {
        throw AppError(500, "config_invalid", std::string("Invalid value for ") + name);
    }
    errno = 0;
    char* end = nullptr;
    const unsigned long long parsed = std::strtoull(raw, &end, 10);
    if (errno != 0 || end == raw || *end != '\0' || parsed < minimum ||
        parsed > maximum) {
        throw AppError(500, "config_invalid", std::string("Invalid value for ") + name);
    }
    return static_cast<uint64_t>(parsed);
}

int Integer(const char* name, int fallback, int minimum, int maximum) {
    return static_cast<int>(Unsigned(name, static_cast<uint64_t>(fallback),
                                     static_cast<uint64_t>(minimum),
                                     static_cast<uint64_t>(maximum)));
}

bool Boolean(const char* name, bool fallback) {
    const char* raw = std::getenv(name);
    if (raw == nullptr || *raw == '\0') {
        return fallback;
    }
    const std::string value(raw);
    if (value == "1" || value == "true") {
        return true;
    }
    if (value == "0" || value == "false") {
        return false;
    }
    throw AppError(500, "config_invalid", std::string("Invalid Boolean for ") + name);
}

void RequireAbsolutePath(const std::string& value, const char* name) {
    if (value.empty() || value.front() != '/' || value == "/") {
        throw AppError(500, "config_invalid", std::string(name) + " must be an absolute, non-root path");
    }
}

}  // namespace

AppConfig AppConfig::LoadFromEnvironment() {
    AppConfig config;
    config.listen_address = Optional("SMARTDOCS_LISTEN_ADDRESS", "0.0.0.0");
    config.port = static_cast<uint16_t>(Unsigned("SMARTDOCS_PORT", 1316, 1, 65535));
    config.thread_count = Integer("SMARTDOCS_THREAD_COUNT", 6, 1, 256);
    config.connection_timeout_ms =
        Integer("SMARTDOCS_CONNECTION_TIMEOUT_MS", 60000, 1, 3600000);

    config.mysql_host = Required("SMARTDOCS_MYSQL_HOST");
    config.mysql_port =
        static_cast<uint16_t>(Unsigned("SMARTDOCS_MYSQL_PORT", 3306, 1, 65535));
    config.mysql_database = Required("SMARTDOCS_MYSQL_DATABASE");
    config.mysql_user = Required("SMARTDOCS_MYSQL_USER");
    config.mysql_password = Required("SMARTDOCS_MYSQL_PASSWORD");
    config.mysql_socket = Optional("SMARTDOCS_MYSQL_SOCKET", "");
    if (!config.mysql_socket.empty()) {
        RequireAbsolutePath(config.mysql_socket, "SMARTDOCS_MYSQL_SOCKET");
    }
    config.mysql_pool_size = Integer("SMARTDOCS_MYSQL_POOL_SIZE", 12, 1, 256);

    config.storage_root = Required("SMARTDOCS_STORAGE_ROOT");
    RequireAbsolutePath(config.storage_root, "SMARTDOCS_STORAGE_ROOT");
    config.max_file_bytes =
        Unsigned("SMARTDOCS_MAX_FILE_BYTES", 1024ULL * 1024ULL * 1024ULL, 1,
                 1024ULL * 1024ULL * 1024ULL * 1024ULL);
    config.chunk_bytes =
        Unsigned("SMARTDOCS_CHUNK_BYTES", 8ULL * 1024ULL * 1024ULL, 1,
                 config.max_file_bytes);
    config.max_json_bytes =
        Unsigned("SMARTDOCS_MAX_JSON_BYTES", 1024ULL * 1024ULL, 1,
                 64ULL * 1024ULL * 1024ULL);
    config.session_seconds = Integer("SMARTDOCS_SESSION_SECONDS", 43200, 60, 31536000);
    config.password_iterations =
        Integer("SMARTDOCS_PASSWORD_ITERATIONS", 210000, 1000, 10000000);
    config.secure_cookie = Boolean("SMARTDOCS_SECURE_COOKIE", false);
    config.log_level = Integer("SMARTDOCS_LOG_LEVEL", 1, 0, 3);
    return config;
}
