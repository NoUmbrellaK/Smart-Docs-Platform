/*
 * @Author       : mark
 * @Date         : 2020-06-15
 * @copyleft Apache 2.0
 * Modified for Smart Docs Platform, 2026.
 */
#pragma once

#include "httprequest.h"
#include "httpresponse.h"
#include "router.h"
#include "../buffer/buffer.h"

#include <arpa/inet.h>
#include <atomic>
#include <cstdint>
#include <memory>
#include <mutex>
#include <sys/types.h>

class Application;

class HttpConn {
public:
    explicit HttpConn(std::shared_ptr<const Application> application);
    ~HttpConn();

    HttpConn(const HttpConn&) = delete;
    HttpConn& operator=(const HttpConn&) = delete;

    void init(int socket_fd, const sockaddr_in& address);
    ssize_t read(int* saved_errno);
    ssize_t write(int* saved_errno);
    bool process();
    bool BeginNextRequest();
    void Close();

    int GetFd() const;
    int GetPort() const;
    const char* GetIP() const;
    sockaddr_in GetAddr() const;
    uint64_t ToWriteBytes() const;
    bool IsKeepAlive() const;
    bool IsClosed() const;

    static bool isET;
    static std::atomic<int> userCount;

private:
    uint64_t ToWriteBytesUnlocked() const;
    void SetResponse(HttpResponse response, bool keep_alive);
    void SetParseError(const HttpParseResult& result);

    std::shared_ptr<const Application> application_;
    mutable std::mutex mutex_;
    int fd_;
    sockaddr_in address_;
    bool closed_;
    bool keep_alive_;
    size_t response_offset_;

    Buffer read_buffer_;
    HttpRequest request_;
    std::unique_ptr<RequestBodyHandler> body_handler_;
    std::unique_ptr<HttpResponse> response_;
};
