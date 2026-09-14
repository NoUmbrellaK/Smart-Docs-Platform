#pragma once

#include "db/mysql.h"
#include "file_service.h"

#include <string>
#include <vector>

struct FileVersionRecord {
    FileVersionSummary summary;
    std::string content_id;
    std::string availability_state;
};

class FileRepository {
public:
    bool DirectoryExists(MySqlConnection& connection,
                         const std::string& project_id,
                         const std::string& directory_id) const;
    Page<FileSummary> List(MySqlConnection& connection,
                           const std::string& project_id,
                           const FileListQuery& query) const;
    bool FindFile(MySqlConnection& connection,
                  const std::string& project_id,
                  const std::string& file_id, bool include_deleted,
                  FileSummary* file) const;
    bool FindFileForUpdate(MySqlConnection& connection,
                           const std::string& project_id,
                           const std::string& file_id, bool include_deleted,
                           FileSummary* file) const;
    void UpdateFile(MySqlConnection& connection, const std::string& file_id,
                    const std::string& name,
                    const std::string& directory_id) const;
    void SoftDelete(MySqlConnection& connection, const std::string& file_id,
                    const std::string& actor_id) const;
    void Restore(MySqlConnection& connection,
                 const std::string& file_id) const;
    void SetRemoteAiPolicy(MySqlConnection& connection,
                           const std::string& file_id,
                           const std::string& policy) const;
    std::vector<std::string> ListVersionIdsForUpdate(
        MySqlConnection& connection, const std::string& file_id) const;
    void SetVersionApprovals(
        MySqlConnection& connection, const std::string& file_id,
        const std::vector<std::string>& approved_version_ids,
        const std::string& actor_id) const;
    void InsertAudit(MySqlConnection& connection, const std::string& actor_id,
                     const std::string& project_id, const std::string& action,
                     const std::string& file_id,
                     const std::string& request_id) const;
    std::vector<FileVersionSummary> ListVersions(
        MySqlConnection& connection, const std::string& file_id) const;
    bool FindVersion(MySqlConnection& connection, const std::string& file_id,
                     const std::string& version_id,
                     FileVersionRecord* version) const;
};
