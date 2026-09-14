#pragma once

#include "httpresponse.h"
#include "router.h"

#include <cstdint>
#include <functional>
#include <initializer_list>
#include <memory>
#include <nlohmann/json.hpp>

using JsonBodyCallback = std::function<HttpResponse(const nlohmann::json&)>;

std::unique_ptr<RequestBodyHandler> PrepareJsonBody(
    const RequestHead& head, uint64_t maximum_bytes,
    JsonBodyCallback callback);
std::unique_ptr<RequestBodyHandler> ReadyResponse(HttpResponse response);
void RequireExactObject(const nlohmann::json& body,
                        std::initializer_list<const char*> fields);
