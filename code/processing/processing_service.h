#pragma once

#include "auth/auth_service.h"
#include <cstdint>
#include <string>
#include <nlohmann/json.hpp>

class MySqlConnection;
class MySqlPool;
class ProjectService;

struct ProcessingJob {
    std::string id, file_id, version_id, project_id, state;
    uint64_t attempt_count = 0;
    std::string error_code;
};

struct ProcessingClaim {
    ProcessingJob job;
    std::string attempt_token;
};

// Within the caller's transaction, after locking/creating the file row.
void EnqueueProcessingJob(MySqlConnection& connection, const std::string& job_id,
                          const std::string& version_id);

class ProcessingService {
public:
    ProcessingService(MySqlPool& pool, ProjectService& projects);
    ProcessingJob Get(const SessionContext&, const std::string& project_id,
                      const std::string& job_id);
    ProcessingJob Cancel(const SessionContext&, const std::string& project_id,
                         const std::string& job_id, const std::string& request_id = "");
    ProcessingJob Retry(const SessionContext&, const std::string& project_id,
                        const std::string& job_id, const std::string& request_id = "");

    // Trusted server internals only; no public worker routes in this increment.
    bool ClaimNext(ProcessingClaim* claim);
    void Fail(const std::string& job_id, const std::string& attempt_token,
              const std::string& error_code);
    uint64_t RecoverInterrupted();
    void Publish(const std::string& job_id, const std::string& attempt_token,
                 const nlohmann::json& result);
    nlohmann::json ReadDocument(const SessionContext&, const std::string& project_id,
        const std::string& file_id, const std::string& version_id,
        const std::string& chunk_id = "");

private:
    MySqlPool& pool_;
    ProjectService& projects_;
};
