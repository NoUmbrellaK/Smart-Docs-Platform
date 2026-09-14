/*
 * @Author       : mark
 * @Date         : 2020-06-15
 * @copyleft Apache 2.0
 * Modified for Smart Docs Platform, 2026.
 */
#include "httpconn.h"

#include "../app/application.h"
#include "../core/app_error.h"

#include <algorithm>
#include <cerrno>
#include <climits>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <sys/sendfile.h>
#include <sys/uio.h>
#include <unistd.h>
#include <utility>

std::atomic<int> HttpConn::userCount{0};
bool HttpConn::isET = false;

namespace {

int ParseErrorStatus(const std::string& code) {
    if (code == "request_line_too_large") {
        return 414;
    }
    if (code == "headers_too_large") {
        return 431;
    }
    if (code == "length_required") {
        return 411;
    }
    return 400;
}

}  // namespace

HttpConn::HttpConn(std::shared_ptr<const Application> application)
    : application_(std::move(application)),
      fd_(-1),
      address_{},
      closed_(true),
      keep_alive_(false),
      response_offset_(0) {
    if (!application_) {
        throw std::invalid_argument("HttpConn requires an application");
    }
}

HttpConn::~HttpConn() {
    Close();
}

void HttpConn::init(int socket_fd, const sockaddr_in& address) {
    if (socket_fd < 0) {
        throw std::invalid_argument("HttpConn requires a valid socket");
    }
    std::lock_guard<std::mutex> lock(mutex_);
    if (!closed_) {
        throw std::logic_error("HttpConn cannot be initialized twice");
    }
    fd_ = socket_fd;
    address_ = address;
    closed_ = false;
    keep_alive_ = false;
    response_offset_ = 0;
    read_buffer_.RetrieveAll();
    request_.Reset();
    body_handler_.reset();
    response_.reset();
    ++userCount;
}

ssize_t HttpConn::read(int* saved_errno) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (saved_errno == nullptr || closed_) {
        if (saved_errno != nullptr) {
            *saved_errno = EBADF;
        }
        return -1;
    }
    *saved_errno = 0;
    ssize_t total = 0;
    do {
        int read_errno = 0;
        const ssize_t size = read_buffer_.ReadFd(fd_, &read_errno);
        if (size > 0) {
            total += size;
            if (!isET) {
                break;
            }
            continue;
        }
        if (size == 0) {
            return total == 0 ? 0 : total;
        }
        *saved_errno = read_errno;
        if (read_errno == EINTR) {
            continue;
        }
        if ((read_errno == EAGAIN || read_errno == EWOULDBLOCK) && total > 0) {
            return total;
        }
        return total > 0 ? total : -1;
    } while (isET);
    return total;
}

void HttpConn::SetResponse(HttpResponse response, bool keep_alive) {
    response_.reset(new HttpResponse(std::move(response)));
    response_offset_ = 0;
    keep_alive_ = keep_alive;
    body_handler_.reset();
}

void HttpConn::SetParseError(const HttpParseResult& result) {
    const AppError error(ParseErrorStatus(result.code), result.code,
                         result.message, false);
    SetResponse(application_->ErrorResponse(error), false);
}

bool HttpConn::process() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (closed_) {
        return false;
    }
    if (response_) {
        return true;
    }
    if (read_buffer_.ReadableBytes() == 0 && request_.body_remaining() == 0) {
        return false;
    }

    try {
        HttpParseResult result = request_.ParseHead(read_buffer_);
        if (result.status == HttpParseStatus::Error) {
            SetParseError(result);
            return true;
        }
        if (result.status == HttpParseStatus::NeedMore) {
            return false;
        }

        if (!body_handler_) {
            body_handler_ = application_->Prepare(request_.head());
        }
        if (result.status == HttpParseStatus::HeadersComplete) {
            result = request_.ConsumeBody(
                read_buffer_, [this](const char* data, size_t size) {
                    body_handler_->OnData(data, size);
                });
            if (result.status == HttpParseStatus::Error) {
                SetParseError(result);
                return true;
            }
            if (result.status == HttpParseStatus::NeedMore) {
                return false;
            }
        }

        SetResponse(body_handler_->Finish(), request_.head().keep_alive);
        return true;
    } catch (const AppError& error) {
        SetResponse(application_->ErrorResponse(error), false);
        return true;
    } catch (const std::exception&) {
        SetResponse(application_->InternalErrorResponse(), false);
        return true;
    }
}

ssize_t HttpConn::write(int* saved_errno) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (saved_errno == nullptr || closed_ || !response_) {
        if (saved_errno != nullptr) {
            *saved_errno = EBADF;
        }
        return -1;
    }
    *saved_errno = 0;
    ssize_t total = 0;

    do {
        ssize_t written = 0;
        const std::string& buffered = response_->head_and_body();
        if (response_offset_ < buffered.size()) {
            struct iovec output{};
            output.iov_base = const_cast<char*>(buffered.data() + response_offset_);
            output.iov_len = buffered.size() - response_offset_;
            written = writev(fd_, &output, 1);
            if (written > 0) {
                response_offset_ += static_cast<size_t>(written);
            }
        } else if (response_->file_region().length != 0) {
            FileRegion& region = response_->file_region();
            off_t offset = static_cast<off_t>(region.offset);
            const uint64_t max_write = static_cast<uint64_t>(
                std::numeric_limits<ssize_t>::max());
            const size_t count = static_cast<size_t>(
                std::min(region.length, max_write));
            written = sendfile(fd_, region.fd, &offset, count);
            if (written > 0) {
                region.offset = static_cast<uint64_t>(offset);
                region.length -= static_cast<uint64_t>(written);
            }
        } else {
            break;
        }

        if (written > 0) {
            total += written;
            if (!isET) {
                break;
            }
            continue;
        }
        if (written == 0) {
            if (ToWriteBytesUnlocked() != 0) {
                *saved_errno = EIO;
                return total > 0 ? total : -1;
            }
            break;
        }
        *saved_errno = errno;
        if (errno == EINTR) {
            continue;
        }
        return total > 0 ? total : -1;
    } while (isET);

    return total;
}

bool HttpConn::BeginNextRequest() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (closed_ || !response_ || ToWriteBytesUnlocked() != 0 || !keep_alive_) {
        return false;
    }
    response_.reset();
    response_offset_ = 0;
    keep_alive_ = false;
    request_.Reset();
    body_handler_.reset();
    return true;
}

void HttpConn::Close() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (closed_) {
        return;
    }
    closed_ = true;
    response_.reset();
    body_handler_.reset();
    if (fd_ >= 0) {
        close(fd_);
        fd_ = -1;
    }
    --userCount;
}

int HttpConn::GetFd() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return fd_;
}

int HttpConn::GetPort() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return ntohs(address_.sin_port);
}

const char* HttpConn::GetIP() const {
    thread_local char address[INET_ADDRSTRLEN];
    std::lock_guard<std::mutex> lock(mutex_);
    if (inet_ntop(AF_INET, &address_.sin_addr, address, sizeof(address)) == nullptr) {
        std::strncpy(address, "0.0.0.0", sizeof(address));
        address[sizeof(address) - 1] = '\0';
    }
    return address;
}

sockaddr_in HttpConn::GetAddr() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return address_;
}

uint64_t HttpConn::ToWriteBytesUnlocked() const {
    if (!response_) {
        return 0;
    }
    const uint64_t buffered = response_offset_ < response_->head_and_body().size()
        ? static_cast<uint64_t>(response_->head_and_body().size() - response_offset_)
        : 0;
    return buffered + response_->file_region().length;
}

uint64_t HttpConn::ToWriteBytes() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return ToWriteBytesUnlocked();
}

bool HttpConn::IsKeepAlive() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return keep_alive_;
}

bool HttpConn::IsClosed() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return closed_;
}
