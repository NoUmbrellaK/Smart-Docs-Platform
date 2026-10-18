#pragma once

#include "auth/auth_service.h"
#include <cstdint>
#include <string>
#include <nlohmann/json.hpp>

class MySqlConnection;
class MySqlPool;
class ProjectService;
class FileStore;
class AuthorizedVersion;

struct ProcessingJob {
    std::string id, file_id, version_id, project_id, state;
    uint64_t attempt_count = 0;
    std::string error_code;
    std::string task_type = "parse_and_index";
};

struct ProcessingClaim {
    ProcessingJob job;
    std::string attempt_token;
    std::string source_sha256, kind;
    uint64_t source_size = 0;
};

// Within the caller's transaction, after locking/creating the file row.
void EnqueueProcessingJob(MySqlConnection& connection, const std::string& job_id,
                          const std::string& version_id);
// After policy/approval changes, within the caller's file-locked transaction.
void RefreshVectorJobs(MySqlConnection& connection, const std::string& file_id);

class ProcessingService {
public:
    ProcessingService(MySqlPool& pool, ProjectService& projects);
    ProcessingJob Get(const SessionContext&, const std::string& project_id,
                      const std::string& job_id);
    ProcessingJob Cancel(const SessionContext&, const std::string& project_id,
                         const std::string& job_id, const std::string& request_id = "");
    ProcessingJob Retry(const SessionContext&, const std::string& project_id,
                        const std::string& job_id, const std::string& request_id = "");

    // Trusted local worker operations, not end-user or Agent authorization.
    bool ClaimNext(ProcessingClaim* claim, const std::string& task_type = "parse_and_index");
    void Fail(const std::string& job_id, const std::string& attempt_token,
              const std::string& error_code);
    uint64_t RecoverInterrupted(const std::string& task_type = "parse_and_index");
    nlohmann::json ReadVectorBatch(const std::string& job_id, const std::string& attempt_token);
    std::string PublishVectorBatch(const std::string& job_id, const std::string& attempt_token,
        uint64_t batch_start, const nlohmann::json& result);
    AuthorizedVersion OpenInput(const std::string& job_id, const std::string& attempt_token,
                                FileStore& store);
    void Publish(const std::string& job_id, const std::string& attempt_token,
                 const nlohmann::json& result);
    nlohmann::json ReadDocument(const SessionContext&, const std::string& project_id,
        const std::string& file_id, const std::string& version_id,
        const std::string& chunk_id = "", bool remote_ai = false,
        const nlohmann::json& range = nullptr);
    nlohmann::json Search(const SessionContext&, const std::string& project_id,
                          const nlohmann::json& request, bool remote_ai = false);

private:
    MySqlPool& pool_;
    ProjectService& projects_;
};
