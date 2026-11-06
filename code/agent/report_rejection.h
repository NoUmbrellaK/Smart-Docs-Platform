#pragma once
#include "core/app_error.h"

// Internal validation category only; never carries a submitted value or enters the model response.
class ReportRejection : public AppError {
public:
    ReportRejection(const AppError& error, const std::string& category)
        : AppError(error), reason(category) {}
    const std::string reason;
};
