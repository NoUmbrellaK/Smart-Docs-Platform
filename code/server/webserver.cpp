/*
 * @Author       : mark
 * @Date         : 2020-06-17
 * @copyleft Apache 2.0
 * Modified for Smart Docs Platform, 2026.
 */
#include "webserver.h"

#include "../app/application.h"
#include "../core/app_error.h"
#include "../log/log.h"

#include <arpa/inet.h>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdexcept>
#include <sys/epoll.h>
#include <sys/socket.h>
#include <unistd.h>
#include <utility>

WebServer::WebServer(const AppConfig& config,
                     std::shared_ptr<Application> application)
    : listen_address_(config.listen_address),
      port_(config.port),
      timeout_ms_(config.connection_timeout_ms),
      closed_(false),
      listen_fd_(-1),
      listen_events_(0),
      connection_events_(0),
      application_(std::move(application)),
      timer_(new HeapTimer()),
      thread_pool_(new ThreadPool(static_cast<size_t>(config.thread_count))),
      epoller_(new Epoller()) {
    if (!application_) {
        throw std::invalid_argument("WebServer requires an application");
    }
    InitEventMode();
    if (!InitSocket()) {
        throw AppError(500, "listen_failed", "failed to initialize the HTTP listener");
    }
}

WebServer::~WebServer() {
    closed_ = true;
    if (listen_fd_ >= 0) {
        epoller_->DelFd(listen_fd_);
        close(listen_fd_);
        listen_fd_ = -1;
    }
    thread_pool_->Stop();

    std::unordered_map<int, std::shared_ptr<HttpConn>> clients;
    {
        std::lock_guard<std::mutex> lock(clients_mutex_);
        clients.swap(clients_);
    }
    for (const auto& entry : clients) {
        entry.second->Close();
    }
}

void WebServer::InitEventMode() {
    listen_events_ = EPOLLIN | EPOLLET;
    connection_events_ = EPOLLONESHOT | EPOLLRDHUP | EPOLLET;
    HttpConn::isET = true;
}

int WebServer::SetFdNonblock(int fd) {
    if (fd < 0) {
        errno = EBADF;
        return -1;
    }
    const int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0) {
        return -1;
    }
    return fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

bool WebServer::InitSocket() {
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(port_);
    if (inet_pton(AF_INET, listen_address_.c_str(), &address.sin_addr) != 1) {
        throw AppError(500, "config_invalid", "SMARTDOCS_LISTEN_ADDRESS must be an IPv4 address");
    }

    listen_fd_ = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (listen_fd_ < 0) {
        return false;
    }
    const int enabled = 1;
    if (setsockopt(listen_fd_, SOL_SOCKET, SO_REUSEADDR, &enabled,
                   sizeof(enabled)) != 0 ||
        SetFdNonblock(listen_fd_) != 0 ||
        bind(listen_fd_, reinterpret_cast<sockaddr*>(&address),
             sizeof(address)) != 0 ||
        listen(listen_fd_, 128) != 0 ||
        !epoller_->AddFd(listen_fd_, listen_events_)) {
        close(listen_fd_);
        listen_fd_ = -1;
        return false;
    }
    return true;
}

void WebServer::Start() {
    while (!closed_) {
        const int wait_ms = timeout_ms_ > 0 ? timer_->GetNextTick() : -1;
        const int event_count = epoller_->Wait(wait_ms);
        if (event_count < 0) {
            if (errno == EINTR) {
                continue;
            }
            throw AppError(500, "event_loop_failed", "HTTP event loop failed");
        }

        for (int i = 0; i < event_count; ++i) {
            const int fd = epoller_->GetEventFd(static_cast<size_t>(i));
            const uint32_t events = epoller_->GetEvents(static_cast<size_t>(i));
            if (fd == listen_fd_) {
                AcceptClients();
                continue;
            }
            std::shared_ptr<HttpConn> client = FindClient(fd);
            if (!client) {
                continue;
            }
            if ((events & (EPOLLRDHUP | EPOLLHUP | EPOLLERR)) != 0) {
                CloseConnection(client);
            } else if ((events & EPOLLIN) != 0) {
                DispatchRead(client);
            } else if ((events & EPOLLOUT) != 0) {
                DispatchWrite(client);
            } else {
                CloseConnection(client);
            }
        }
    }
}

void WebServer::AcceptClients() {
    while (true) {
        sockaddr_in address{};
        socklen_t address_size = sizeof(address);
        const int fd = accept4(listen_fd_, reinterpret_cast<sockaddr*>(&address),
                               &address_size, SOCK_NONBLOCK | SOCK_CLOEXEC);
        if (fd < 0) {
            if (errno == EINTR) {
                continue;
            }
            return;
        }
        if (HttpConn::userCount.load() >= kMaxConnections) {
            SendBusyAndClose(fd);
            continue;
        }
        AddClient(fd, address);
    }
}

void WebServer::SendBusyAndClose(int fd) {
    const char response[] =
        "HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n"
        "Content-Length: 0\r\n\r\n";
    (void)send(fd, response, sizeof(response) - 1, MSG_NOSIGNAL);
    close(fd);
}

void WebServer::AddClient(int fd, const sockaddr_in& address) {
    std::shared_ptr<HttpConn> client(new HttpConn(application_));
    client->init(fd, address);
    {
        std::lock_guard<std::mutex> lock(clients_mutex_);
        clients_[fd] = client;
    }
    if (!epoller_->AddFd(fd, EPOLLIN | connection_events_)) {
        CloseConnection(client);
        return;
    }
    if (timeout_ms_ > 0) {
        std::weak_ptr<HttpConn> weak_client(client);
        timer_->add(fd, timeout_ms_, [this, weak_client]() {
            if (std::shared_ptr<HttpConn> locked = weak_client.lock()) {
                CloseConnection(locked);
            }
        });
    }
}

std::shared_ptr<HttpConn> WebServer::FindClient(int fd) {
    std::lock_guard<std::mutex> lock(clients_mutex_);
    const auto found = clients_.find(fd);
    return found == clients_.end() ? std::shared_ptr<HttpConn>() : found->second;
}

void WebServer::ExtendTimeout(const std::shared_ptr<HttpConn>& client) {
    if (timeout_ms_ > 0) {
        const int fd = client->GetFd();
        if (fd >= 0) {
            timer_->adjust(fd, timeout_ms_);
        }
    }
}

void WebServer::CloseConnection(const std::shared_ptr<HttpConn>& client) {
    const int fd = client->GetFd();
    if (fd < 0) {
        return;
    }
    epoller_->DelFd(fd);
    if (timeout_ms_ > 0) {
        timer_->remove(fd);
    }
    {
        std::lock_guard<std::mutex> lock(clients_mutex_);
        const auto found = clients_.find(fd);
        if (found != clients_.end() && found->second == client) {
            clients_.erase(found);
        }
    }
    client->Close();
}

void WebServer::DispatchRead(const std::shared_ptr<HttpConn>& client) {
    ExtendTimeout(client);
    thread_pool_->AddTask([this, client]() { OnRead(client); });
}

void WebServer::DispatchWrite(const std::shared_ptr<HttpConn>& client) {
    ExtendTimeout(client);
    thread_pool_->AddTask([this, client]() { OnWrite(client); });
}

void WebServer::OnRead(const std::shared_ptr<HttpConn>& client) {
    int saved_errno = 0;
    const ssize_t size = client->read(&saved_errno);
    if (size == 0 ||
        (size < 0 && saved_errno != EAGAIN && saved_errno != EWOULDBLOCK)) {
        CloseConnection(client);
        return;
    }
    RearmAfterProcess(client);
}

void WebServer::RearmAfterProcess(const std::shared_ptr<HttpConn>& client) {
    if (client->IsClosed()) {
        return;
    }
    const int fd = client->GetFd();
    if (client->process()) {
        epoller_->ModFd(fd, EPOLLOUT | connection_events_);
    } else {
        epoller_->ModFd(fd, EPOLLIN | connection_events_);
    }
}

void WebServer::OnWrite(const std::shared_ptr<HttpConn>& client) {
    int saved_errno = 0;
    const ssize_t size = client->write(&saved_errno);
    if (client->ToWriteBytes() != 0) {
        if (size < 0 && saved_errno != EAGAIN && saved_errno != EWOULDBLOCK) {
            CloseConnection(client);
            return;
        }
        const int fd = client->GetFd();
        if (fd >= 0) {
            epoller_->ModFd(fd, EPOLLOUT | connection_events_);
        }
        return;
    }

    if (client->IsKeepAlive() && client->BeginNextRequest()) {
        const int fd = client->GetFd();
        if (fd >= 0) {
            epoller_->ModFd(fd, EPOLLIN | connection_events_);
        }
        return;
    }
    CloseConnection(client);
}
