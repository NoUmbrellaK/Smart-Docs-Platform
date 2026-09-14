/*
 * @Author       : mark
 * @Date         : 2020-06-26
 * @copyleft Apache 2.0
 * Modified for Smart Docs Platform, 2026.
 */
#include "httprequest.h"

#include <algorithm>
#include <cctype>
#include <limits>

namespace {

const size_t kMaxRequestLineBytes = 8192;
const size_t kMaxHeaderBytes = 32768;

std::string Lower(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return value;
}

std::string Trim(const std::string& value) {
    size_t first = 0;
    while (first < value.size() &&
           (value[first] == ' ' || value[first] == '\t')) {
        ++first;
    }
    size_t last = value.size();
    while (last > first &&
           (value[last - 1] == ' ' || value[last - 1] == '\t')) {
        --last;
    }
    return value.substr(first, last - first);
}

bool IsTokenCharacter(unsigned char ch) {
    if (std::isalnum(ch)) {
        return true;
    }
    const std::string punctuation("!#$%&'*+-.^_`|~");
    return punctuation.find(static_cast<char>(ch)) != std::string::npos;
}

bool IsUppercaseMethod(const std::string& method) {
    if (method.empty()) {
        return false;
    }
    for (unsigned char ch : method) {
        if (!IsTokenCharacter(ch) || (std::isalpha(ch) && !std::isupper(ch))) {
            return false;
        }
    }
    return true;
}

bool ParseUnsignedDecimal(const std::string& value, uint64_t* parsed) {
    if (value.empty()) {
        return false;
    }
    uint64_t result = 0;
    for (unsigned char ch : value) {
        if (!std::isdigit(ch)) {
            return false;
        }
        const uint64_t digit = static_cast<uint64_t>(ch - '0');
        if (result > (std::numeric_limits<uint64_t>::max() - digit) / 10) {
            return false;
        }
        result = result * 10 + digit;
    }
    *parsed = result;
    return true;
}

bool MethodRequiresLength(const std::string& method) {
    return method == "POST" || method == "PUT" || method == "PATCH";
}

bool MethodForbidsBody(const std::string& method) {
    return method == "GET" || method == "DELETE" || method == "HEAD";
}

bool ContainsCommaToken(const std::string& value, const std::string& expected) {
    size_t start = 0;
    while (start <= value.size()) {
        const size_t comma = value.find(',', start);
        const size_t end = comma == std::string::npos ? value.size() : comma;
        if (Lower(Trim(value.substr(start, end - start))) == expected) {
            return true;
        }
        if (comma == std::string::npos) {
            break;
        }
        start = comma + 1;
    }
    return false;
}

}  // namespace

HttpRequest::HttpRequest() {
    Reset();
}

void HttpRequest::Reset() {
    state_ = State::RequestLine;
    head_ = RequestHead();
    body_remaining_ = 0;
    header_bytes_ = 0;
    saw_content_length_ = false;
    saw_transfer_encoding_ = false;
    error_code_.clear();
    error_message_.clear();
}

HttpParseResult HttpRequest::Result(HttpParseStatus status) const {
    return HttpParseResult{status, std::string(), std::string()};
}

HttpParseResult HttpRequest::Fail(const std::string& code,
                                  const std::string& message) {
    state_ = State::Error;
    error_code_ = code;
    error_message_ = message;
    return HttpParseResult{HttpParseStatus::Error, code, message};
}

HttpParseResult HttpRequest::ParseRequestLine(const std::string& line) {
    const size_t first_space = line.find(' ');
    if (first_space == std::string::npos) {
        return Fail("invalid_request_line", "request line must contain method, target, and version");
    }
    const size_t second_space = line.find(' ', first_space + 1);
    if (second_space == std::string::npos ||
        line.find(' ', second_space + 1) != std::string::npos) {
        return Fail("invalid_request_line", "request line must contain exactly three fields");
    }

    head_.method = line.substr(0, first_space);
    std::string target = line.substr(first_space + 1,
                                     second_space - first_space - 1);
    const std::string protocol = line.substr(second_space + 1);
    if (!IsUppercaseMethod(head_.method) || target.empty() || target[0] != '/') {
        return Fail("invalid_request_line", "invalid method or request target");
    }
    if (protocol != "HTTP/1.1") {
        return Fail("http_version_not_supported", "only HTTP/1.1 is supported");
    }
    if (target.find('#') != std::string::npos) {
        return Fail("invalid_request_target", "fragments are not allowed in request targets");
    }

    const size_t query = target.find('?');
    head_.path = target.substr(0, query);
    if (query != std::string::npos) {
        head_.query = target.substr(query + 1);
    }
    head_.version = "1.1";
    state_ = State::Headers;
    return Result(HttpParseStatus::NeedMore);
}

HttpParseResult HttpRequest::ParseHeader(const std::string& line) {
    const size_t colon = line.find(':');
    if (colon == std::string::npos || colon == 0) {
        return Fail("invalid_header", "header must contain a non-empty field name");
    }

    const std::string raw_name = line.substr(0, colon);
    for (unsigned char ch : raw_name) {
        if (!IsTokenCharacter(ch)) {
            return Fail("invalid_header", "header field name is invalid");
        }
    }
    const std::string name = Lower(raw_name);
    const std::string value = Trim(line.substr(colon + 1));
    if (head_.headers.count(name) != 0) {
        if (name == "content-length") {
            return Fail("ambiguous_request", "duplicate Content-Length is not allowed");
        }
        return Fail("duplicate_header", "duplicate header fields are not supported");
    }
    head_.headers.emplace(name, value);

    if (name == "content-length") {
        saw_content_length_ = true;
        if (!ParseUnsignedDecimal(value, &head_.content_length)) {
            return Fail("invalid_content_length", "Content-Length must be an unsigned decimal integer");
        }
    } else if (name == "transfer-encoding") {
        saw_transfer_encoding_ = true;
    }
    return Result(HttpParseStatus::NeedMore);
}

HttpParseResult HttpRequest::FinishHeaders(size_t unread_bytes) {
    const auto host = head_.headers.find("host");
    if (host == head_.headers.end() || host->second.empty()) {
        return Fail("host_required", "HTTP/1.1 requests require a Host header");
    }
    if (saw_content_length_ && saw_transfer_encoding_) {
        return Fail("ambiguous_request", "Content-Length cannot be combined with Transfer-Encoding");
    }
    if (saw_transfer_encoding_) {
        return Fail("transfer_encoding_not_supported", "Transfer-Encoding is not supported");
    }
    if (MethodRequiresLength(head_.method) && !saw_content_length_) {
        return Fail("length_required", "this method requires Content-Length");
    }
    if (MethodForbidsBody(head_.method) && head_.content_length != 0) {
        return Fail("body_not_allowed", "this method does not accept a request body");
    }

    const auto connection = head_.headers.find("connection");
    head_.keep_alive = connection == head_.headers.end() ||
                       !ContainsCommaToken(connection->second, "close");
    body_remaining_ = head_.content_length;
    if (body_remaining_ == 0) {
        if (unread_bytes != 0) {
            return Fail("pipelining_not_supported", "pipelined requests are not supported");
        }
        state_ = State::Complete;
        return Result(HttpParseStatus::Complete);
    }
    state_ = State::Body;
    return Result(HttpParseStatus::HeadersComplete);
}

HttpParseResult HttpRequest::ParseHead(Buffer& buffer) {
    if (state_ == State::Error) {
        return HttpParseResult{HttpParseStatus::Error, error_code_, error_message_};
    }
    if (state_ == State::Complete) {
        return Result(HttpParseStatus::Complete);
    }
    if (state_ == State::Body) {
        return Result(HttpParseStatus::HeadersComplete);
    }

    const char delimiter[] = "\r\n";
    while (state_ == State::RequestLine || state_ == State::Headers) {
        const char* const begin = buffer.Peek();
        const char* const end = buffer.BeginWriteConst();
        const char* const line_end = std::search(begin, end, delimiter, delimiter + 2);
        if (line_end == end) {
            if (state_ == State::RequestLine && buffer.ReadableBytes() > kMaxRequestLineBytes) {
                return Fail("request_line_too_large", "request line exceeds 8192 bytes");
            }
            if (state_ == State::Headers &&
                header_bytes_ + buffer.ReadableBytes() > kMaxHeaderBytes) {
                return Fail("headers_too_large", "request headers exceed 32768 bytes");
            }
            return Result(HttpParseStatus::NeedMore);
        }

        const size_t line_size = static_cast<size_t>(line_end - begin);
        if (state_ == State::RequestLine && line_size > kMaxRequestLineBytes) {
            return Fail("request_line_too_large", "request line exceeds 8192 bytes");
        }
        if (state_ == State::Headers && header_bytes_ + line_size + 2 > kMaxHeaderBytes) {
            return Fail("headers_too_large", "request headers exceed 32768 bytes");
        }

        const std::string line(begin, line_end);
        buffer.RetrieveUntil(line_end + 2);
        if (state_ == State::RequestLine) {
            const HttpParseResult result = ParseRequestLine(line);
            if (result.status == HttpParseStatus::Error) {
                return result;
            }
            continue;
        }

        header_bytes_ += line_size + 2;
        if (line.empty()) {
            return FinishHeaders(buffer.ReadableBytes());
        }
        const HttpParseResult result = ParseHeader(line);
        if (result.status == HttpParseStatus::Error) {
            return result;
        }
    }
    return Result(HttpParseStatus::NeedMore);
}

HttpParseResult HttpRequest::ConsumeBody(Buffer& buffer,
                                         const BodyConsumer& consumer) {
    if (state_ == State::Error) {
        return HttpParseResult{HttpParseStatus::Error, error_code_, error_message_};
    }
    if (state_ == State::Complete) {
        if (buffer.ReadableBytes() != 0) {
            return Fail("pipelining_not_supported", "pipelined requests are not supported");
        }
        return Result(HttpParseStatus::Complete);
    }
    if (state_ != State::Body) {
        return Fail("invalid_parser_state", "request headers are not complete");
    }

    const uint64_t available = static_cast<uint64_t>(buffer.ReadableBytes());
    const size_t consume = static_cast<size_t>(std::min(body_remaining_, available));
    if (consume != 0) {
        consumer(buffer.Peek(), consume);
        buffer.Retrieve(consume);
        body_remaining_ -= consume;
    }
    if (body_remaining_ != 0) {
        return Result(HttpParseStatus::NeedMore);
    }
    if (buffer.ReadableBytes() != 0) {
        return Fail("pipelining_not_supported", "pipelined requests are not supported");
    }
    state_ = State::Complete;
    return Result(HttpParseStatus::Complete);
}

const RequestHead& HttpRequest::head() const {
    return head_;
}

uint64_t HttpRequest::body_remaining() const {
    return body_remaining_;
}

bool HttpRequest::BodyComplete() const {
    return state_ == State::Complete;
}
