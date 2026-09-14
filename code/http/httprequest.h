/*
 * @Author       : mark
 * @Date         : 2020-06-25
 * @copyleft Apache 2.0
 * Modified for Smart Docs Platform, 2026.
 */
#ifndef HTTP_REQUEST_H
#define HTTP_REQUEST_H

#include "../buffer/buffer.h"

#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>
#include <unordered_map>

enum class HttpParseStatus {
    NeedMore,
    HeadersComplete,
    Complete,
    Error,
};

struct HttpParseResult {
    HttpParseStatus status;
    std::string code;
    std::string message;
};

struct RequestHead {
    std::string method;
    std::string path;
    std::string query;
    std::string version;
    std::unordered_map<std::string, std::string> headers;
    uint64_t content_length = 0;
    bool keep_alive = false;
};

class HttpRequest {
public:
    using BodyConsumer = std::function<void(const char*, size_t)>;

    HttpRequest();

    void Reset();
    HttpParseResult ParseHead(Buffer& buffer);
    HttpParseResult ConsumeBody(Buffer& buffer, const BodyConsumer& consumer);

    const RequestHead& head() const;
    uint64_t body_remaining() const;
    bool BodyComplete() const;

private:
    enum class State {
        RequestLine,
        Headers,
        Body,
        Complete,
        Error,
    };

    HttpParseResult Result(HttpParseStatus status) const;
    HttpParseResult Fail(const std::string& code, const std::string& message);
    HttpParseResult ParseRequestLine(const std::string& line);
    HttpParseResult ParseHeader(const std::string& line);
    HttpParseResult FinishHeaders(size_t unread_bytes);

    State state_;
    RequestHead head_;
    uint64_t body_remaining_;
    size_t header_bytes_;
    bool saw_content_length_;
    bool saw_transfer_encoding_;
    std::string error_code_;
    std::string error_message_;
};

#endif  // HTTP_REQUEST_H
