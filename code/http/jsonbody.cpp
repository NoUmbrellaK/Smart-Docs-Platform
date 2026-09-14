#include "jsonbody.h"

#include "core/app_error.h"

#include <algorithm>
#include <cctype>
#include <string>
#include <utility>

namespace {

std::string Lower(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(),
                   [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return value;
}

bool IsJsonContentType(const RequestHead& head) {
    const auto found = head.headers.find("content-type");
    if (found == head.headers.end()) {
        return false;
    }
    std::string value = Lower(found->second);
    value.erase(std::remove_if(value.begin(), value.end(),
                               [](unsigned char ch) {
        return ch == ' ' || ch == '\t';
    }), value.end());
    return value == "application/json" ||
           value == "application/json;charset=utf-8";
}

class JsonBodyHandler : public RequestBodyHandler {
public:
    JsonBodyHandler(uint64_t maximum_bytes, JsonBodyCallback callback)
        : maximum_bytes_(maximum_bytes), callback_(std::move(callback)) {}

    void OnData(const char* data, size_t size) override {
        if (size > maximum_bytes_ - body_.size()) {
            throw AppError(413, "body_too_large",
                           "JSON request body exceeds the configured limit");
        }
        body_.append(data, size);
    }

    HttpResponse Finish() override {
        try {
            return callback_(nlohmann::json::parse(body_));
        } catch (const nlohmann::json::exception&) {
            throw AppError(400, "invalid_request",
                           "request body must be valid JSON");
        }
    }

private:
    size_t maximum_bytes_;
    std::string body_;
    JsonBodyCallback callback_;
};

class ReadyResponseHandler : public RequestBodyHandler {
public:
    explicit ReadyResponseHandler(HttpResponse response)
        : response_(std::move(response)) {}

    void OnData(const char*, size_t size) override {
        if (size != 0) {
            throw AppError(400, "body_not_allowed",
                           "this route does not accept a request body");
        }
    }

    HttpResponse Finish() override { return std::move(response_); }

private:
    HttpResponse response_;
};

}  // namespace

std::unique_ptr<RequestBodyHandler> PrepareJsonBody(
    const RequestHead& head, uint64_t maximum_bytes,
    JsonBodyCallback callback) {
    if (!IsJsonContentType(head)) {
        throw AppError(400, "invalid_request",
                       "Content-Type must be application/json");
    }
    if (head.content_length > maximum_bytes) {
        throw AppError(413, "body_too_large",
                       "JSON request body exceeds the configured limit");
    }
    return std::unique_ptr<RequestBodyHandler>(
        new JsonBodyHandler(maximum_bytes, std::move(callback)));
}

std::unique_ptr<RequestBodyHandler> ReadyResponse(HttpResponse response) {
    return std::unique_ptr<RequestBodyHandler>(
        new ReadyResponseHandler(std::move(response)));
}

void RequireExactObject(const nlohmann::json& body,
                        std::initializer_list<const char*> fields) {
    if (!body.is_object() || body.size() != fields.size()) {
        throw AppError(400, "invalid_request",
                       "request body contains missing or unknown fields");
    }
    for (const char* field : fields) {
        if (!body.contains(field)) {
            throw AppError(400, "invalid_request",
                           "request body contains missing or unknown fields");
        }
    }
}
