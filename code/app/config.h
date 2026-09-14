#pragma once

#include <cstdint>
#include <string>

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
