#pragma once

#include "auth/auth_service.h"
#include "http/router.h"

#include <cstdint>
#include <memory>
#include <string>

class PartWriter;
class UploadService;
typedef struct evp_md_ctx_st EVP_MD_CTX;

class ChunkBodyHandler : public RequestBodyHandler {
public:
    ChunkBodyHandler(UploadService& uploads, SessionContext session,
                     std::string project_id, std::string task_id,
                     uint32_t part_number, uint64_t expected_size,
                     std::string expected_sha256,
                     std::unique_ptr<PartWriter> writer,
                     std::string request_id, bool keep_alive);
    ~ChunkBodyHandler() override;

    void OnData(const char* data, size_t size) override;
    HttpResponse Finish() override;

private:
    UploadService& uploads_;
    SessionContext session_;
    std::string project_id_;
    std::string task_id_;
    uint32_t part_number_;
    uint64_t expected_size_;
    std::string expected_sha256_;
    std::unique_ptr<PartWriter> writer_;
    std::string request_id_;
    bool keep_alive_;
    uint64_t received_;
    EVP_MD_CTX* digest_;
    bool finished_;
};
