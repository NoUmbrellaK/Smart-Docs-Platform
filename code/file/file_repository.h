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
    std::vector<FileVersionSummary> ListVersions(
        MySqlConnection& connection, const std::string& file_id) const;
    bool FindVersion(MySqlConnection& connection, const std::string& file_id,
                     const std::string& version_id,
                     FileVersionRecord* version) const;
};
