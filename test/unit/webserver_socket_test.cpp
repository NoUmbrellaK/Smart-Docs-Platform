#include "app/application.h"
#include "http/httpconn.h"
#include "server/webserver.h"
#include "server/epoller.h"
#include "../test_support.h"

#include <arpa/inet.h>
#include <cerrno>
#include <cstdlib>
#include <fcntl.h>
#include <memory>
#include <string>
#include <sys/socket.h>
#include <sys/epoll.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

namespace {

struct SocketTestScope {
    int peer;
    bool original_et;
    ~SocketTestScope() {
        close(peer);
        HttpConn::isET = original_et;
    }
};

std::string Exchange(const std::shared_ptr<Application>& application,
                     const std::string& request) {
    int sockets[2];
    CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
    CHECK(WebServer::SetFdNonblock(sockets[0]) == 0);

    sockaddr_in address{};
    address.sin_family = AF_INET;
    HttpConn connection(application);
    connection.init(sockets[0], address);
    size_t sent = 0;
    while (sent < request.size()) {
        const ssize_t size = ::write(sockets[1], request.data() + sent,
                                     request.size() - sent);
        if (size <= 0) {
            throw std::runtime_error("socket test send failed: errno=" +
                                     std::to_string(errno));
        }
        sent += static_cast<size_t>(size);
    }

    int saved_errno = 0;
    CHECK(connection.read(&saved_errno) > 0);
    CHECK(connection.process());
    while (connection.ToWriteBytes() != 0) {
        CHECK(connection.write(&saved_errno) >= 0);
    }

    char response[4096];
    const ssize_t size = ::read(sockets[1], response, sizeof(response));
    CHECK(size > 0);
    connection.Close();
    close(sockets[1]);
    return std::string(response, static_cast<size_t>(size));
}

class TemporaryStaticRoot {
public:
    TemporaryStaticRoot() {
        char pattern[] = "/tmp/smart-docs-static-test.XXXXXX";
        char* created = mkdtemp(pattern);
        CHECK(created != nullptr);
        path_ = created;
        CHECK(mkdir((path_ + "/css").c_str(), S_IRUSR | S_IWUSR | S_IXUSR) == 0);
        CHECK(mkdir((path_ + "/js").c_str(), S_IRUSR | S_IWUSR | S_IXUSR) == 0);
        Write("index.html", "index-shell");
        Write("app.html", "workbench-shell");
        Write("css/app.css", "workbench-style");
        Write("js/api.js", "api-module");
        Write("js/app.js", "app-module");
        Write("js/uploads.js", "uploads-module");
        Write("js/sha256.js", "sha256-module");
        Write("js/search.js", "search-module");
        Write("js/processing.js", "processing-module");
        Write("js/project-settings.js", "project-settings-module");
    }

    ~TemporaryStaticRoot() {
        for (const char* relative : {"index.html", "app.html", "css/app.css",
                                     "js/api.js", "js/app.js", "js/uploads.js",
                                     "js/sha256.js", "js/search.js", "js/processing.js",
                                     "js/project-settings.js"}) {
            unlink((path_ + "/" + relative).c_str());
        }
        rmdir((path_ + "/css").c_str());
        rmdir((path_ + "/js").c_str());
        rmdir(path_.c_str());
    }

    const std::string& path() const { return path_; }

private:
    void Write(const std::string& relative, const std::string& content) {
        const std::string file_path = path_ + "/" + relative;
        const int fd = open(file_path.c_str(), O_WRONLY | O_CREAT | O_EXCL,
                            S_IRUSR | S_IWUSR);
        CHECK(fd >= 0);
        CHECK(::write(fd, content.data(), content.size()) ==
              static_cast<ssize_t>(content.size()));
        close(fd);
    }
    std::string path_;
};

}  // namespace

TEST_CASE(webserver_sets_status_flags_nonblocking) {
    int sockets[2];
    CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
    const int before = fcntl(sockets[0], F_GETFL, 0);
    CHECK(before >= 0);
    CHECK((before & O_NONBLOCK) == 0);

    CHECK(WebServer::SetFdNonblock(sockets[0]) == 0);
    const int after = fcntl(sockets[0], F_GETFL, 0);
    CHECK(after >= 0);
    CHECK((after & O_NONBLOCK) != 0);

    close(sockets[0]);
    close(sockets[1]);
}

TEST_CASE(http_connection_serves_live_health_over_socket) {
    const std::string response = Exchange(
        std::make_shared<Application>(),
        "GET /api/v1/health/live HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n");
    CHECK(response.find("HTTP/1.1 200 OK\r\n") == 0);
    CHECK(response.find("\"status\":\"live\"") != std::string::npos);
    CHECK(response.find("\"request_id\":\"") != std::string::npos);
}

TEST_CASE(http_connection_reports_not_ready_before_database_bootstrap) {
    const std::string response = Exchange(
        std::make_shared<Application>(),
        "GET /api/v1/health/ready HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n");
    CHECK(response.find("HTTP/1.1 503 Service Unavailable\r\n") == 0);
    CHECK(response.find("\"status\":\"not_ready\"") != std::string::npos);
}

TEST_CASE(http_connection_bounds_et_reads_and_rearms_queued_bytes) {
    HttpConn connection(std::make_shared<Application>());
    int sockets[2];
    CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
    SocketTestScope scope{sockets[1], HttpConn::isET};
    sockaddr_in address{};
    connection.init(sockets[0], address);
    CHECK(WebServer::SetFdNonblock(sockets[0]) == 0);
    HttpConn::isET = true;
    Epoller events;
    const uint32_t flags = EPOLLIN | EPOLLET | EPOLLONESHOT;
    CHECK(events.AddFd(sockets[0], flags));
    const std::string queued(128 * 1024, 'x');
    CHECK(send(sockets[1], queued.data(), queued.size(), MSG_NOSIGNAL) ==
          static_cast<ssize_t>(queued.size()));
    CHECK(events.Wait(1000) == 1);
    int error = 0;
    const ssize_t first = connection.read(&error);
    CHECK(events.ModFd(sockets[0], flags));
    const int next_event = events.Wait(1000);
    const ssize_t second = next_event == 1 ? connection.read(&error) : -1;
    connection.Close();
    CHECK(first > 0 && first <= 64 * 1024);
    CHECK(next_event == 1);
    CHECK(second > 0 && second <= 64 * 1024);
    CHECK(first + second == static_cast<ssize_t>(queued.size()));
}

TEST_CASE(http_connection_rejects_pipeline_still_queued_in_socket) {
    HttpConn connection(std::make_shared<Application>());
    int sockets[2];
    CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
    SocketTestScope scope{sockets[1], HttpConn::isET};
    sockaddr_in address{};
    connection.init(sockets[0], address);
    CHECK(WebServer::SetFdNonblock(sockets[0]) == 0);
    const std::string request = "GET /api/v1/health/live HTTP/1.1\r\nHost: localhost\r\n\r\n";
    CHECK(write(sockets[1], request.data(), request.size()) ==
          static_cast<ssize_t>(request.size()));
    int error = 0;
    CHECK(connection.read(&error) == static_cast<ssize_t>(request.size()));
    // First request ends exactly at the current read boundary. The next
    // request is already queued before processing/response publication.
    CHECK(write(sockets[1], request.data(), request.size()) ==
          static_cast<ssize_t>(request.size()));
    CHECK(connection.process());
    while (connection.ToWriteBytes() != 0) CHECK(connection.write(&error) >= 0);
    char bytes[4096];
    const ssize_t size = read(sockets[1], bytes, sizeof(bytes));
    const std::string response(bytes, size > 0 ? static_cast<size_t>(size) : 0);
    connection.Close();
    CHECK(response.find("HTTP/1.1 400 Bad Request\r\n") == 0);
    CHECK(response.find("pipelining_not_supported") != std::string::npos);
}

TEST_CASE(http_connection_streams_only_the_allowlisted_static_shell) {
    TemporaryStaticRoot static_root;
    const std::shared_ptr<Application> application =
        std::make_shared<Application>(static_root.path());
    struct AssetCase {
        const char* route;
        const char* content_type;
        const char* body;
    };
    const std::vector<AssetCase> assets = {
        {"/", "text/html; charset=utf-8", "index-shell"},
        {"/app.html", "text/html; charset=utf-8", "workbench-shell"},
        {"/css/app.css", "text/css; charset=utf-8", "workbench-style"},
        {"/js/api.js", "text/javascript; charset=utf-8", "api-module"},
        {"/js/app.js", "text/javascript; charset=utf-8", "app-module"},
        {"/js/uploads.js", "text/javascript; charset=utf-8", "uploads-module"},
        {"/js/sha256.js", "text/javascript; charset=utf-8", "sha256-module"},
        {"/js/search.js", "text/javascript; charset=utf-8", "search-module"},
        {"/js/processing.js", "text/javascript; charset=utf-8", "processing-module"},
        {"/js/project-settings.js", "text/javascript; charset=utf-8", "project-settings-module"},
    };
    for (const AssetCase& asset : assets) {
        const std::string response = Exchange(
            application, std::string("GET ") + asset.route +
                " HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n");
        CHECK(response.find("HTTP/1.1 200 OK\r\n") == 0);
        CHECK(response.find(std::string("Content-Type: ") + asset.content_type +
                            "\r\n") != std::string::npos);
        CHECK(response.find(asset.body) != std::string::npos);
    }

    const std::string unknown = Exchange(
        application,
        "GET /js/unknown.js HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n");
    CHECK(unknown.find("HTTP/1.1 404 Not Found\r\n") == 0);
    CHECK(unknown.find("\"code\":\"route_not_found\"") != std::string::npos);
}
