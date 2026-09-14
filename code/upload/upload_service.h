#pragma once

#include "auth/auth_service.h"
#include "file/file_service.h"
#include "file/file_store.h"

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

class MySqlPool;
class ProjectService;
class UploadRepository;

enum class UploadMode { CreateFile, CreateVersion };

struct CreateUploadCommand {
    UploadMode mode;
    std::string directory_id;
    std::string name;
    uint64_t size;
    std::string sha256;
    std::string media_type;
    std::string file_id;
    std::string observed_current_version_id;
};

struct UploadListQuery {
    std::string state;
    uint64_t page = 1;
    uint64_t page_size = 50;
};

struct UploadTask {
    std::string id;
    std::string project_id;
    std::string owner_id;
    std::string state;
    uint64_t chunk_size;
    uint64_t part_count;
    uint64_t received_bytes;
    std::string file_id;
    std::string version_id;
    std::string processing_job_id;
    std::string failure;
};

struct UploadTaskDetail {
    UploadTask task;
    std::vector<PartInfo> confirmed_parts;
};

struct PartResult {
    uint32_t part_number;
    uint64_t size;
    std::string sha256;
    bool reused;
};

uint64_t UploadPartCount(uint64_t file_size, uint64_t chunk_size);
uint64_t ExpectedUploadPartSize(uint64_t file_size, uint64_t chunk_size,
                                uint32_t part_number);
void ValidateUploadSha256(const std::string& value);
std::string ValidateUploadMediaType(const std::string& value);

class UploadService {
public:
    UploadService(MySqlPool& pool, ProjectService& projects, FileStore& store,
                  uint64_t maximum_file_bytes, uint64_t chunk_size);
    ~UploadService();

    UploadTask Create(const SessionContext& session,
                      const std::string& project_id,
                      const CreateUploadCommand& command,
                      const std::string& request_id = std::string());
    Page<UploadTask> ListOwn(const SessionContext& session,
                             const std::string& project_id,
                             const UploadListQuery& query);
    UploadTaskDetail GetOwn(const SessionContext& session,
                            const std::string& project_id,
                            const std::string& task_id);
    void Cancel(const SessionContext& session, const std::string& project_id,
                const std::string& task_id,
                const std::string& request_id = std::string());

    std::unique_ptr<PartWriter> BeginPart(
        const SessionContext& session, const std::string& project_id,
        const std::string& task_id, uint32_t part_number,
        uint64_t content_length, const std::string& sha256);
    PartResult ConfirmPart(const SessionContext& session,
                           const std::string& project_id,
                           const std::string& task_id,
                           const PartInfo& part,
                           const std::string& request_id);

private:
    MySqlPool& pool_;
    ProjectService& projects_;
    FileStore& store_;
    uint64_t maximum_file_bytes_;
    uint64_t chunk_size_;
    std::unique_ptr<UploadRepository> repository_;
};
