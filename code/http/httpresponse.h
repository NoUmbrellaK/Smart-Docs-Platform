/*
 * @Author       : mark
 * @Date         : 2020-06-25
 * @copyleft Apache 2.0
 * Modified for Smart Docs Platform, 2026.
 */
#pragma once

#include <cstdint>
#include <nlohmann/json.hpp>
#include <string>
#include <utility>
#include <vector>

struct FileRegion {
    int fd = -1;
    uint64_t offset = 0;
    uint64_t length = 0;
};

class HttpResponse {
public:
    using Headers = std::vector<std::pair<std::string, std::string>>;

    HttpResponse();
    ~HttpResponse();
    HttpResponse(const HttpResponse&) = delete;
    HttpResponse& operator=(const HttpResponse&) = delete;
    HttpResponse(HttpResponse&& other) noexcept;
    HttpResponse& operator=(HttpResponse&& other) noexcept;

    static HttpResponse Json(int status, const nlohmann::json& body,
                             bool keep_alive = false,
                             Headers headers = Headers());
    static HttpResponse Empty(int status, bool keep_alive = false,
                              Headers headers = Headers());
    static HttpResponse File(int status, FileRegion region, Headers headers,
                             bool keep_alive = false);

    int status() const;
    const std::string& head_and_body() const;
    FileRegion& file_region();
    const FileRegion& file_region() const;

private:
    static HttpResponse Build(int status, uint64_t content_length,
                              bool keep_alive, Headers headers,
                              std::string body, FileRegion region);
    void CloseFileRegion();

    int status_;
    std::string head_and_body_;
    FileRegion file_region_;

};
