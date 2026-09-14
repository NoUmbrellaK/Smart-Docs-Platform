/*
 * @Author       : mark
 * @Date         : 2020-06-17
 * @copyleft Apache 2.0
 * Modified for Smart Docs Platform, 2026.
 */
#pragma once

#include "../app/config.h"
#include "../http/httpconn.h"
#include "../pool/threadpool.h"
#include "../timer/heaptimer.h"
#include "epoller.h"

#include <atomic>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>

class Application;

class WebServer {
public:
    WebServer(const AppConfig& config,
              std::shared_ptr<Application> application);
    ~WebServer();

    WebServer(const WebServer&) = delete;
    WebServer& operator=(const WebServer&) = delete;

    void Start();
    static int SetFdNonblock(int fd);

private:
    static const int kMaxConnections = 65536;

    bool InitSocket();
    void InitEventMode();
    void AcceptClients();
    void AddClient(int fd, const sockaddr_in& address);
    void SendBusyAndClose(int fd);

    std::shared_ptr<HttpConn> FindClient(int fd);
    void ExtendTimeout(const std::shared_ptr<HttpConn>& client);
    void CloseConnection(const std::shared_ptr<HttpConn>& client);
    void DispatchRead(const std::shared_ptr<HttpConn>& client);
    void DispatchWrite(const std::shared_ptr<HttpConn>& client);
    void OnRead(const std::shared_ptr<HttpConn>& client);
    void OnWrite(const std::shared_ptr<HttpConn>& client);
    void RearmAfterProcess(const std::shared_ptr<HttpConn>& client);

    std::string listen_address_;
    uint16_t port_;
    int timeout_ms_;
    bool closed_;
    int listen_fd_;
    uint32_t listen_events_;
    uint32_t connection_events_;

    std::shared_ptr<Application> application_;
    std::unique_ptr<HeapTimer> timer_;
    std::unique_ptr<ThreadPool> thread_pool_;
    std::unique_ptr<Epoller> epoller_;
    std::mutex clients_mutex_;
    std::unordered_map<int, std::shared_ptr<HttpConn>> clients_;
};
