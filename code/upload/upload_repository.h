#pragma once

#include "db/mysql.h"
#include "upload_service.h"

#include <string>
#include <vector>

struct UploadTaskRecord {
    UploadTask task;
    UploadMode mode;
    std::string directory_id;
    std::string expected_name;
    uint64_t expected_size;
    std::string expected_sha256;
    std::string media_type;
    std::string target_file_id;
    std::string observed_current_version_id;
};

struct PublishedVersionRecord {
    std::string version_id;
    std::string project_id;
    std::string content_id;
};

class UploadRepository {
public:
    bool DirectoryExists(MySqlConnection& connection,
                         const std::string& project_id,
                         const std::string& directory_id) const;
    bool ActiveNameExists(MySqlConnection& connection,
                          const std::string& project_id,
                          const std::string& directory_id,
                          const std::string& name) const;
    bool FindActiveFileCurrentVersion(MySqlConnection& connection,
                                      const std::string& project_id,
                                      const std::string& file_id,
                                      std::string* version_id,
                                      bool lock = false) const;
    void InsertTask(MySqlConnection& connection,
                    const UploadTaskRecord& record) const;
    Page<UploadTask> ListOwn(MySqlConnection& connection,
                             const std::string& project_id,
                             const std::string& owner_id,
                             const UploadListQuery& query) const;
    bool FindOwn(MySqlConnection& connection, const std::string& project_id,
                 const std::string& owner_id, const std::string& task_id,
                 bool lock, UploadTaskRecord* record) const;
    bool FindById(MySqlConnection& connection, const std::string& task_id,
                  bool lock, UploadTaskRecord* record) const;
    std::vector<PartInfo> ListParts(MySqlConnection& connection,
                                    const std::string& task_id) const;
    bool FindPart(MySqlConnection& connection, const std::string& task_id,
                  uint32_t part_number, PartInfo* part) const;
    void InsertPart(MySqlConnection& connection, const std::string& task_id,
                    const PartInfo& part) const;
    void Cancel(MySqlConnection& connection, const std::string& task_id) const;
    void SetState(MySqlConnection& connection, const std::string& task_id,
                  const std::string& state) const;
    void SetFailed(MySqlConnection& connection, const std::string& task_id,
                   const std::string& code,
                   const std::string& message) const;
    void InsertFile(MySqlConnection& connection, const std::string& file_id,
                    const UploadTaskRecord& task,
                    const std::string& actor_id) const;
    uint64_t NextVersionNumber(MySqlConnection& connection,
                               const std::string& file_id) const;
    void InsertVersion(MySqlConnection& connection,
                       const std::string& version_id,
                       const std::string& file_id, uint64_t version_number,
                       const std::string& content_id,
                       const UploadTaskRecord& task,
                       const std::string& actor_id) const;
    void SetCurrentVersion(MySqlConnection& connection,
                           const std::string& file_id,
                           const std::string& version_id) const;
    void InsertProcessingJob(MySqlConnection& connection,
                             const std::string& job_id,
                             const std::string& version_id) const;
    void SetCompleted(MySqlConnection& connection, const std::string& task_id,
                      const std::string& file_id,
                      const std::string& version_id,
                      const std::string& job_id) const;
    bool CompletedGraphValid(MySqlConnection& connection,
                             const UploadTaskRecord& task) const;
    std::vector<UploadTaskRecord> ListInFlight(
        MySqlConnection& connection) const;
    std::vector<UploadTaskRecord> ListCompleted(
        MySqlConnection& connection) const;
    std::vector<PublishedVersionRecord> ListAvailableVersions(
        MySqlConnection& connection) const;
    bool ContentReferenced(MySqlConnection& connection,
                           const std::string& content_id) const;
    void MarkVersionUnavailable(MySqlConnection& connection,
                                const PublishedVersionRecord& version) const;
    void InsertAudit(MySqlConnection& connection, const std::string& actor_id,
                     const std::string& project_id,
                     const std::string& action,
                     const std::string& object_id,
                     const std::string& request_id) const;
};
