#include "http/httpresponse.h"
#include "../test_support.h"

#include <cerrno>
#include <fcntl.h>
#include <nlohmann/json.hpp>
#include <string>
#include <type_traits>
#include <unistd.h>
#include <utility>
#include <vector>

TEST_CASE(http_response_json_sets_required_headers_and_exact_length) {
    const nlohmann::json body = {{"status", "live"}};
    HttpResponse response = HttpResponse::Json(200, body, true);
    const std::string serialized = response.head_and_body();
    const std::string payload = body.dump();

    CHECK(response.status() == 200);
    CHECK(serialized.find("HTTP/1.1 200 OK\r\n") == 0);
    CHECK(serialized.find("Content-Type: application/json; charset=utf-8\r\n") != std::string::npos);
    CHECK(serialized.find("Content-Length: " + std::to_string(payload.size()) + "\r\n") != std::string::npos);
    CHECK(serialized.find("X-Content-Type-Options: nosniff\r\n") != std::string::npos);
    CHECK(serialized.find("Connection: keep-alive\r\n") != std::string::npos);
    CHECK(serialized.substr(serialized.size() - payload.size()) == payload);
}

TEST_CASE(http_response_file_serializes_headers_without_buffering_file) {
    int descriptors[2];
    CHECK(pipe(descriptors) == 0);
    {
        FileRegion region{descriptors[0], 10, 25};
        HttpResponse response = HttpResponse::File(
            206, region,
            {{"Content-Type", "application/pdf"},
             {"Content-Range", "bytes 10-34/100"}}, false);

        CHECK(response.status() == 206);
        CHECK(response.file_region().fd == descriptors[0]);
        CHECK(response.file_region().offset == 10);
        CHECK(response.file_region().length == 25);
        CHECK(response.head_and_body().find("Content-Length: 25\r\n") != std::string::npos);
        CHECK(response.head_and_body().find("Content-Range: bytes 10-34/100\r\n") != std::string::npos);
        CHECK(response.head_and_body().find("Connection: close\r\n") != std::string::npos);
        CHECK(response.head_and_body().rfind("\r\n\r\n") == response.head_and_body().size() - 4);
    }
    errno = 0;
    CHECK(fcntl(descriptors[0], F_GETFD) == -1);
    CHECK(errno == EBADF);
    close(descriptors[1]);
}

TEST_CASE(http_response_move_assignment_closes_replaced_descriptor_once) {
    static_assert(!std::is_copy_constructible<HttpResponse>::value,
                  "HttpResponse must not duplicate file descriptor ownership");
    int first[2];
    int second[2];
    CHECK(pipe(first) == 0);
    CHECK(pipe(second) == 0);
    {
        HttpResponse source = HttpResponse::File(200, FileRegion{first[0], 0, 1}, {});
        HttpResponse destination = HttpResponse::File(200, FileRegion{second[0], 0, 1}, {});
        destination = std::move(source);

        errno = 0;
        CHECK(fcntl(second[0], F_GETFD) == -1);
        CHECK(errno == EBADF);
        CHECK(fcntl(first[0], F_GETFD) != -1);
        CHECK(source.file_region().fd == -1);
    }
    errno = 0;
    CHECK(fcntl(first[0], F_GETFD) == -1);
    CHECK(errno == EBADF);
    close(first[1]);
    close(second[1]);
}
