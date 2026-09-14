#pragma once

#include <stdexcept>
#include <string>
#include <utility>

class AppError : public std::runtime_error {
public:
    AppError(int status, std::string error_code, std::string error_message,
             bool can_retry = false)
        : std::runtime_error(error_message),
          http_status(status),
          code(std::move(error_code)),
          message(std::move(error_message)),
          retryable(can_retry) {}

    int http_status;
    std::string code;
    std::string message;
    bool retryable;
};
