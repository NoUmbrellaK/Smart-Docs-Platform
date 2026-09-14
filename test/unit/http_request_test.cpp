#include "http/httprequest.h"
#include "../test_support.h"

#include "buffer/buffer.h"

#include <string>

namespace {

void Append(Buffer& buffer, const std::string& value) {
    buffer.Append(value.data(), value.size());
}

}  // namespace

TEST_CASE(request_body_can_arrive_across_reads) {
    HttpRequest request;
    Buffer buffer;
    Append(buffer, "POST /api/v1/test HTTP/1.1\r\nHost: localhost\r\nContent-Length: 5\r\n\r\nab");

    CHECK(request.ParseHead(buffer).status == HttpParseStatus::HeadersComplete);
    std::string body;
    CHECK(request.ConsumeBody(buffer, [&](const char* data, size_t size) {
        body.append(data, size);
    }).status == HttpParseStatus::NeedMore);
    CHECK(body == "ab");

    Append(buffer, "cde");
    CHECK(request.ConsumeBody(buffer, [&](const char* data, size_t size) {
        body.append(data, size);
    }).status == HttpParseStatus::Complete);
    CHECK(body == "abcde");
}

TEST_CASE(binary_body_preserves_crlf_and_zero_bytes) {
    HttpRequest request;
    Buffer buffer;
    Append(buffer, "PUT /chunk HTTP/1.1\r\nHost: localhost\r\nContent-Length: 5\r\n\r\n");
    const char bytes[] = {'a', '\r', '\n', '\0', 'z'};
    buffer.Append(bytes, sizeof(bytes));

    CHECK(request.ParseHead(buffer).status == HttpParseStatus::HeadersComplete);
    std::string body;
    CHECK(request.ConsumeBody(buffer, [&](const char* data, size_t size) {
        body.append(data, size);
    }).status == HttpParseStatus::Complete);
    CHECK(body.size() == sizeof(bytes));
    CHECK(body[3] == '\0');
}

TEST_CASE(duplicate_content_length_is_rejected) {
    HttpRequest request;
    Buffer buffer;
    Append(buffer, "POST /x HTTP/1.1\r\nHost: localhost\r\nContent-Length: 1\r\nContent-Length: 1\r\n\r\nx");
    const HttpParseResult result = request.ParseHead(buffer);
    CHECK(result.status == HttpParseStatus::Error);
    CHECK(result.code == "ambiguous_request");
}

TEST_CASE(content_length_with_transfer_encoding_is_rejected) {
    HttpRequest request;
    Buffer buffer;
    Append(buffer, "POST /x HTTP/1.1\r\nHost: localhost\r\nContent-Length: 1\r\nTransfer-Encoding: chunked\r\n\r\nx");
    const HttpParseResult result = request.ParseHead(buffer);
    CHECK(result.status == HttpParseStatus::Error);
    CHECK(result.code == "ambiguous_request");
}

TEST_CASE(chunked_transfer_encoding_is_rejected) {
    HttpRequest request;
    Buffer buffer;
    Append(buffer, "POST /x HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n");
    const HttpParseResult result = request.ParseHead(buffer);
    CHECK(result.status == HttpParseStatus::Error);
    CHECK(result.code == "transfer_encoding_not_supported");
}

TEST_CASE(request_line_over_8192_bytes_is_rejected) {
    HttpRequest request;
    Buffer buffer;
    Append(buffer, "GET /" + std::string(8192, 'a'));
    const HttpParseResult result = request.ParseHead(buffer);
    CHECK(result.status == HttpParseStatus::Error);
    CHECK(result.code == "request_line_too_large");
}

TEST_CASE(headers_over_32768_bytes_are_rejected) {
    HttpRequest request;
    Buffer buffer;
    Append(buffer, "GET / HTTP/1.1\r\nHost: localhost\r\nX-Large: " +
                       std::string(32768, 'a'));
    const HttpParseResult result = request.ParseHead(buffer);
    CHECK(result.status == HttpParseStatus::Error);
    CHECK(result.code == "headers_too_large");
}

TEST_CASE(pipelined_second_request_is_rejected) {
    HttpRequest request;
    Buffer buffer;
    Append(buffer, "GET /one HTTP/1.1\r\nHost: localhost\r\n\r\nGET /two HTTP/1.1\r\nHost: localhost\r\n\r\n");
    const HttpParseResult result = request.ParseHead(buffer);
    CHECK(result.status == HttpParseStatus::Error);
    CHECK(result.code == "pipelining_not_supported");
}

TEST_CASE(request_connection_close_token_is_honored) {
    HttpRequest request;
    Buffer buffer;
    Append(buffer, "GET / HTTP/1.1\r\nHost: localhost\r\n"
                   "Connection: keep-alive, Close\r\n\r\n");
    CHECK(request.ParseHead(buffer).status == HttpParseStatus::Complete);
    CHECK(!request.head().keep_alive);
}
