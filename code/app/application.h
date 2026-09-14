#pragma once

#include "http/httpresponse.h"
#include "http/router.h"

#include <memory>
#include <string>

class AppError;

class Application {
public:
    explicit Application(std::string static_root = "resources");
    Application(const Application&) = delete;
    Application& operator=(const Application&) = delete;

    std::unique_ptr<RequestBodyHandler> Prepare(const RequestHead& head) const;
    HttpResponse ErrorResponse(const AppError& error) const;
    HttpResponse InternalErrorResponse() const;
    bool Ready() const;

private:
    Router router_;
};
