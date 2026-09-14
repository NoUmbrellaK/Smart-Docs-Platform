#include "application.h"

#include "config.h"
#include "auth/auth_routes.h"
#include "auth/auth_service.h"
#include "core/app_error.h"
#include "core/id.h"
#include "db/mysql.h"
#include "db/schema.h"
#include "project/project_routes.h"
#include "project/project_service.h"

#include <cerrno>
#include <fcntl.h>
#include <memory>
#include <nlohmann/json.hpp>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>
#include <utility>

namespace {

class ImmediateHandler : public RequestBodyHandler {
public:
    explicit ImmediateHandler(HttpResponse response)
        : response_(std::move(response)) {}

    void OnData(const char*, size_t size) override {
        if (size != 0) {
            throw AppError(400, "body_not_allowed", "this route does not accept a request body");
        }
    }

    HttpResponse Finish() override {
        return std::move(response_);
    }

private:
    HttpResponse response_;
};

std::unique_ptr<RequestBodyHandler> JsonHandler(int status,
                                                const nlohmann::json& body,
                                                bool keep_alive) {
    return std::unique_ptr<RequestBodyHandler>(
        new ImmediateHandler(HttpResponse::Json(status, body, keep_alive)));
}

std::unique_ptr<RequestBodyHandler> StaticFileHandler(
    const std::string& exact_path, const std::string& content_type,
    bool keep_alive) {
    const int fd = open(exact_path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) {
        throw AppError(404, "route_not_found", "static application shell was not found");
    }
    struct stat file_stat{};
    if (fstat(fd, &file_stat) != 0 || !S_ISREG(file_stat.st_mode) ||
        file_stat.st_size < 0) {
        close(fd);
        throw AppError(404, "route_not_found", "static application shell was not found");
    }
    return std::unique_ptr<RequestBodyHandler>(new ImmediateHandler(
        HttpResponse::File(200,
                           FileRegion{fd, 0, static_cast<uint64_t>(file_stat.st_size)},
                           {{"Content-Type", content_type}}, keep_alive)));
}

nlohmann::json ErrorBody(const std::string& request_id, const std::string& code,
                         const std::string& message, bool retryable) {
    return {{"request_id", request_id},
            {"error", {{"code", code},
                       {"message", message},
                       {"retryable", retryable}}}};
}

bool SecureDirectory(int fd) {
    struct stat directory_stat{};
    return fstat(fd, &directory_stat) == 0 &&
           S_ISDIR(directory_stat.st_mode) &&
           (directory_stat.st_mode & (S_IWGRP | S_IWOTH)) == 0;
}

bool StorageReady(const std::string& root) {
    const int root_fd = open(root.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC |
                                               O_NOFOLLOW);
    if (root_fd < 0) {
        return false;
    }
    bool ready = SecureDirectory(root_fd);
    const char* required[] = {"objects", "staging"};
    for (const char* name : required) {
        const int fd = openat(root_fd, name,
                              O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        if (fd < 0) {
            ready = false;
            continue;
        }
        ready = SecureDirectory(fd) && ready;
        close(fd);
    }
    close(root_fd);
    return ready;
}

}  // namespace

Application::Application(std::string static_root) {
    router_.Add("GET", "/api/v1/health/live",
                [](const RequestHead& head, const RouteParams&) {
        return JsonHandler(200,
                           {{"status", "live"}, {"request_id", GenerateId()}},
                           head.keep_alive);
    });
    router_.Add("GET", "/api/v1/health/ready",
                [this](const RequestHead& head, const RouteParams&) {
        const bool ready = Ready();
        return JsonHandler(ready ? 200 : 503,
                           {{"status", ready ? "ready" : "not_ready"},
                            {"request_id", GenerateId()}},
                           head.keep_alive);
    });

    const std::string index_path = static_root + "/index.html";
    router_.Add("GET", "/", [index_path](const RequestHead& head,
                                           const RouteParams&) {
        return StaticFileHandler(index_path, "text/html; charset=utf-8",
                                 head.keep_alive);
    });
    const std::string favicon_path = static_root + "/images/favicon.ico";
    router_.Add("GET", "/favicon.ico",
                [favicon_path](const RequestHead& head, const RouteParams&) {
        return StaticFileHandler(favicon_path, "image/x-icon", head.keep_alive);
    });
}

Application::Application(const AppConfig& config, std::string static_root)
    : Application(std::move(static_root)) {
    std::shared_ptr<MySqlPool> database(new MySqlPool());
    database->Initialize(MySqlConfig::FromAppConfig(config));
    {
        MySqlConnection connection = database->Acquire();
        connection.Ping();
        Schema::RequireVersion(connection, 1);
    }
    if (!StorageReady(config.storage_root)) {
        throw AppError(503, "storage_unavailable",
                       "controlled storage is not safely available", true);
    }
    storage_root_ = config.storage_root;
    database_ = std::move(database);
    auth_service_.reset(new AuthService(*database_, config.session_seconds,
                                        config.password_iterations));
    project_service_.reset(new ProjectService(*database_));
    RegisterAuthRoutes(router_, auth_service_, project_service_,
                       config.max_json_bytes, config.secure_cookie,
                       config.session_seconds);
    RegisterProjectRoutes(router_, auth_service_, project_service_,
                          config.max_json_bytes, config.secure_cookie);
}

std::unique_ptr<RequestBodyHandler> Application::Prepare(
    const RequestHead& head) const {
    return router_.Prepare(head);
}

HttpResponse Application::ErrorResponse(const AppError& error) const {
    return HttpResponse::Json(error.http_status,
                              ErrorBody(GenerateId(), error.code, error.message,
                                        error.retryable),
                              false);
}

HttpResponse Application::InternalErrorResponse() const {
    return HttpResponse::Json(
        500, ErrorBody(GenerateId(), "internal_error", "internal server error", false),
        false);
}

bool Application::Ready() const {
    if (!database_) {
        return false;
    }
    try {
        MySqlConnection connection = database_->Acquire();
        connection.Ping();
        Schema::RequireVersion(connection, 1);
        return StorageReady(storage_root_);
    } catch (...) {
        return false;
    }
}
