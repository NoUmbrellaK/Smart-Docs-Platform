#pragma once

#include "db/mysql.h"
#include "upload_service.h"

#include <string>

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
                                      std::string* version_id) const;
    void InsertTask(MySqlConnection& connection,
                    const UploadTaskRecord& record) const;
    Page<UploadTask> ListOwn(MySqlConnection& connection,
                             const std::string& project_id,
                             const std::string& owner_id,
                             const UploadListQuery& query) const;
    bool FindOwn(MySqlConnection& connection, const std::string& project_id,
                 const std::string& owner_id, const std::string& task_id,
                 bool lock, UploadTaskRecord* record) const;
    std::vector<PartInfo> ListParts(MySqlConnection& connection,
                                    const std::string& task_id) const;
    bool FindPart(MySqlConnection& connection, const std::string& task_id,
                  uint32_t part_number, PartInfo* part) const;
    void InsertPart(MySqlConnection& connection, const std::string& task_id,
                    const PartInfo& part) const;
    void Cancel(MySqlConnection& connection, const std::string& task_id) const;
    void InsertAudit(MySqlConnection& connection, const std::string& actor_id,
                     const std::string& project_id,
                     const std::string& action,
                     const std::string& object_id,
                     const std::string& request_id) const;
};
