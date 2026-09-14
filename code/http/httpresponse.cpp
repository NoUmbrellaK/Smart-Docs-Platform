/*
 * @Author       : mark
 * @Date         : 2020-06-27
 * @copyleft Apache 2.0
 * Modified for Smart Docs Platform, 2026.
 */
#include "httpresponse.h"

#include <algorithm>
#include <cctype>
#include <stdexcept>
#include <unistd.h>
#include <unordered_set>

namespace {

std::string Lower(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return value;
}

bool IsHeaderNameCharacter(unsigned char ch) {
    if (std::isalnum(ch)) {
        return true;
    }
    const std::string punctuation("!#$%&'*+-.^_`|~");
    return punctuation.find(static_cast<char>(ch)) != std::string::npos;
}

void ValidateHeader(const std::string& name, const std::string& value) {
    if (name.empty()) {
        throw std::invalid_argument("response header name cannot be empty");
    }
    for (unsigned char ch : name) {
        if (!IsHeaderNameCharacter(ch)) {
            throw std::invalid_argument("response header name is invalid");
        }
    }
    if (value.find('\r') != std::string::npos ||
        value.find('\n') != std::string::npos) {
        throw std::invalid_argument("response header value cannot contain a line break");
    }
}

const char* ReasonPhrase(int status) {
    switch (status) {
    case 200: return "OK";
    case 201: return "Created";
    case 204: return "No Content";
    case 206: return "Partial Content";
    case 400: return "Bad Request";
    case 401: return "Unauthorized";
    case 403: return "Forbidden";
    case 404: return "Not Found";
    case 405: return "Method Not Allowed";
    case 411: return "Length Required";
    case 409: return "Conflict";
    case 413: return "Payload Too Large";
    case 414: return "URI Too Long";
    case 416: return "Range Not Satisfiable";
    case 422: return "Unprocessable Entity";
    case 431: return "Request Header Fields Too Large";
    case 500: return "Internal Server Error";
    case 501: return "Not Implemented";
    case 503: return "Service Unavailable";
    default: throw std::invalid_argument("unsupported HTTP response status");
    }
}

}  // namespace

HttpResponse::HttpResponse()
    : status_(500) {}

HttpResponse::~HttpResponse() {
    CloseFileRegion();
}

HttpResponse::HttpResponse(HttpResponse&& other) noexcept
    : status_(other.status_),
      head_and_body_(std::move(other.head_and_body_)),
      file_region_(other.file_region_) {
    other.file_region_ = FileRegion();
}

HttpResponse& HttpResponse::operator=(HttpResponse&& other) noexcept {
    if (this == &other) {
        return *this;
    }
    CloseFileRegion();
    status_ = other.status_;
    head_and_body_ = std::move(other.head_and_body_);
    file_region_ = other.file_region_;
    other.file_region_ = FileRegion();
    return *this;
}

HttpResponse HttpResponse::Build(int status, uint64_t content_length,
                                 bool keep_alive, Headers headers,
                                 std::string body, FileRegion region) {
    HttpResponse response;
    response.status_ = status;
    response.file_region_ = region;

    std::unordered_set<std::string> names;
    std::string serialized = "HTTP/1.1 " + std::to_string(status) + " " +
                             ReasonPhrase(status) + "\r\n";
    for (const auto& header : headers) {
        ValidateHeader(header.first, header.second);
        const std::string lower_name = Lower(header.first);
        if (lower_name == "content-length" || lower_name == "connection" ||
            lower_name == "x-content-type-options" ||
            !names.insert(lower_name).second) {
            throw std::invalid_argument("response header duplicates a managed header");
        }
        serialized += header.first + ": " + header.second + "\r\n";
    }
    serialized += "Content-Length: " + std::to_string(content_length) + "\r\n";
    serialized += "X-Content-Type-Options: nosniff\r\n";
    serialized += std::string("Connection: ") + (keep_alive ? "keep-alive" : "close") + "\r\n\r\n";
    serialized += body;
    response.head_and_body_ = std::move(serialized);
    return response;
}

HttpResponse HttpResponse::Json(int status, const nlohmann::json& body,
                                bool keep_alive, Headers headers) {
    for (const auto& header : headers) {
        if (Lower(header.first) == "content-type") {
            throw std::invalid_argument("JSON Content-Type is fixed");
        }
    }
    headers.emplace_back("Content-Type", "application/json; charset=utf-8");
    std::string serialized_body = body.dump();
    const uint64_t content_length = serialized_body.size();
    return Build(status, content_length, keep_alive, std::move(headers),
                 std::move(serialized_body), FileRegion());
}

HttpResponse HttpResponse::Empty(int status, bool keep_alive,
                                 Headers headers) {
    return Build(status, 0, keep_alive, std::move(headers), std::string(),
                 FileRegion());
}

HttpResponse HttpResponse::File(int status, FileRegion region, Headers headers,
                                bool keep_alive) {
    if (region.fd < 0) {
        throw std::invalid_argument("file response requires an owned descriptor");
    }
    bool has_content_type = false;
    for (const auto& header : headers) {
        if (Lower(header.first) == "content-type") {
            has_content_type = true;
        }
    }
    if (!has_content_type) {
        headers.emplace_back("Content-Type", "application/octet-stream");
    }
    return Build(status, region.length, keep_alive, std::move(headers),
                 std::string(), region);
}

int HttpResponse::status() const {
    return status_;
}

const std::string& HttpResponse::head_and_body() const {
    return head_and_body_;
}

FileRegion& HttpResponse::file_region() {
    return file_region_;
}

const FileRegion& HttpResponse::file_region() const {
    return file_region_;
}

void HttpResponse::CloseFileRegion() {
    if (file_region_.fd >= 0) {
        close(file_region_.fd);
        file_region_.fd = -1;
    }
}
