#pragma once

#include "http/httpresponse.h"
#include "http/router.h"

#include <memory>
#include <string>

class AppError;
class AuthService;
class FileService;
class FileStore;
class MySqlPool;
class ProjectService;
struct AppConfig;

class Application {
public:
    explicit Application(std::string static_root = "resources");
    Application(const AppConfig& config,
                std::string static_root = "resources");
    Application(const Application&) = delete;
    Application& operator=(const Application&) = delete;

    std::unique_ptr<RequestBodyHandler> Prepare(const RequestHead& head) const;
    HttpResponse ErrorResponse(const AppError& error) const;
    HttpResponse InternalErrorResponse() const;
    bool Ready() const;

private:
    Router router_;
    std::shared_ptr<MySqlPool> database_;
    std::string storage_root_;
    std::shared_ptr<AuthService> auth_service_;
    std::shared_ptr<ProjectService> project_service_;
    std::shared_ptr<FileStore> file_store_;
    std::shared_ptr<FileService> file_service_;
};
