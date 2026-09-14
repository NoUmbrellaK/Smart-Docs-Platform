#pragma once

#include "auth/auth_service.h"

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

class FileRepository;
class FileStore;
class MySqlPool;
class ProjectService;

template <typename T>
struct Page {
    std::vector<T> items;
    uint64_t page;
    uint64_t page_size;
    uint64_t total;
};

struct FileSummary {
    std::string id;
    std::string project_id;
    std::string directory_id;
    std::string name;
    std::string current_version_id;
    bool deleted;
    std::string remote_ai_policy;
};

struct FileVersionSummary {
    std::string id;
    std::string file_id;
    uint64_t version_number;
    uint64_t size;
    std::string sha256;
    std::string media_type;
    std::string processing_state;
    bool remote_ai_approved;
};

struct FileListQuery {
    std::string directory_id;
    std::string name;
    bool deleted = false;
    uint64_t page = 1;
    uint64_t page_size = 50;
};

struct UpdateFileCommand {
    std::string name;
    std::string directory_id;
};

struct RemoteAiPolicyCommand {
    std::string policy;
    std::vector<std::string> approved_version_ids;
};

struct RemoteAiState {
    std::string policy;
    std::vector<std::string> approved_version_ids;
};

class AuthorizedVersion {
public:
    AuthorizedVersion(FileSummary file, FileVersionSummary version, int fd);
    ~AuthorizedVersion();
    AuthorizedVersion(const AuthorizedVersion&) = delete;
    AuthorizedVersion& operator=(const AuthorizedVersion&) = delete;
    AuthorizedVersion(AuthorizedVersion&& other) noexcept;
    AuthorizedVersion& operator=(AuthorizedVersion&& other) noexcept;

    int ReleaseFd();

    FileSummary file;
    FileVersionSummary version;
    int fd;
};

std::string ValidateFileName(const std::string& value);

class FileService {
public:
    FileService(MySqlPool& pool, ProjectService& projects, FileStore& store);
    ~FileService();

    Page<FileSummary> List(const SessionContext& session,
                           const std::string& project_id,
                           const FileListQuery& query);
    std::vector<FileVersionSummary> ListVersions(
        const SessionContext& session, const std::string& project_id,
        const std::string& file_id);
    FileSummary Update(const SessionContext& session,
                       const std::string& project_id,
                       const std::string& file_id,
                       const UpdateFileCommand& command,
                       const std::string& request_id = std::string());
    void SoftDelete(const SessionContext& session,
                    const std::string& project_id,
                    const std::string& file_id,
                    const std::string& request_id = std::string());
    FileSummary Restore(const SessionContext& session,
                        const std::string& project_id,
                        const std::string& file_id,
                        const std::string& request_id = std::string());
    RemoteAiState SetRemoteAiPolicy(
        const SessionContext& session, const std::string& project_id,
        const std::string& file_id, const RemoteAiPolicyCommand& command,
        const std::string& request_id = std::string());
    AuthorizedVersion OpenCurrentVersion(const SessionContext& session,
                                         const std::string& project_id,
                                         const std::string& file_id);
    AuthorizedVersion OpenVersion(const SessionContext& session,
                                  const std::string& project_id,
                                  const std::string& file_id,
                                  const std::string& version_id);

private:
    MySqlPool& pool_;
    ProjectService& projects_;
    FileStore& store_;
    std::unique_ptr<FileRepository> repository_;
};
