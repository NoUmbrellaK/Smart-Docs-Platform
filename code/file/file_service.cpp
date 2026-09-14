#include "file_service.h"

#include "file_repository.h"
#include "file_store.h"
#include "core/app_error.h"
#include "core/id.h"
#include "project/project_service.h"

#include <cctype>
#include <limits>
#include <unordered_set>
#include <unistd.h>
#include <utility>

namespace {

bool IsLowerHexId(const std::string& value) {
    return value.size() == 32 &&
           value.find_first_not_of("0123456789abcdef") == std::string::npos;
}

void RequireId(const std::string& value) {
    if (!IsLowerHexId(value)) {
        throw AppError(400, "invalid_request", "entity ID is invalid");
    }
}

[[noreturn]] void ResourceNotFound() {
    throw AppError(404, "resource_not_found", "resource was not found");
}

void ValidateListQuery(const FileListQuery& query) {
    if (query.page == 0 || query.page_size == 0 || query.page_size > 100 ||
        query.page - 1 >
            std::numeric_limits<uint64_t>::max() / query.page_size ||
        query.name.size() > 255 ||
        query.name.find('\0') != std::string::npos) {
        throw AppError(400, "invalid_request", "file list query is invalid");
    }
    if (!query.directory_id.empty()) RequireId(query.directory_id);
}

std::string CheckedRequestId(const std::string& request_id) {
    if (request_id.empty()) return GenerateId();
    RequireId(request_id);
    return request_id;
}

std::vector<std::string> DistinctVersionIds(
    const std::vector<std::string>& ids) {
    std::vector<std::string> result;
    std::unordered_set<std::string> seen;
    result.reserve(ids.size());
    for (const std::string& id : ids) {
        RequireId(id);
        if (!seen.insert(id).second) {
            throw AppError(400, "invalid_request",
                           "approved version IDs must be distinct");
        }
        result.push_back(id);
    }
    return result;
}

}  // namespace

AuthorizedVersion::AuthorizedVersion(FileSummary file_value,
                                     FileVersionSummary version_value,
                                     int descriptor)
    : file(std::move(file_value)),
      version(std::move(version_value)),
      fd(descriptor) {
    if (fd < 0) {
        throw AppError(500, "content_unavailable",
                       "file content is unavailable", true);
    }
}

AuthorizedVersion::~AuthorizedVersion() {
    if (fd >= 0) close(fd);
}

AuthorizedVersion::AuthorizedVersion(AuthorizedVersion&& other) noexcept
    : file(std::move(other.file)),
      version(std::move(other.version)),
      fd(other.fd) {
    other.fd = -1;
}

AuthorizedVersion& AuthorizedVersion::operator=(
    AuthorizedVersion&& other) noexcept {
    if (this != &other) {
        if (fd >= 0) close(fd);
        file = std::move(other.file);
        version = std::move(other.version);
        fd = other.fd;
        other.fd = -1;
    }
    return *this;
}

int AuthorizedVersion::ReleaseFd() {
    const int result = fd;
    fd = -1;
    return result;
}

std::string ValidateFileName(const std::string& value) {
    size_t first = 0;
    while (first < value.size() &&
           std::isspace(static_cast<unsigned char>(value[first]))) {
        ++first;
    }
    size_t last = value.size();
    while (last > first &&
           std::isspace(static_cast<unsigned char>(value[last - 1]))) {
        --last;
    }
    const std::string name = value.substr(first, last - first);
    if (name.empty() || name.size() > 255 ||
        name.find('/') != std::string::npos ||
        name.find('\0') != std::string::npos) {
        throw AppError(400, "invalid_request", "file name is invalid");
    }
    return name;
}

FileService::FileService(MySqlPool& pool, ProjectService& projects,
                         FileStore& store)
    : pool_(pool),
      projects_(projects),
      store_(store),
      repository_(new FileRepository()) {}

FileService::~FileService() = default;

Page<FileSummary> FileService::List(const SessionContext& session,
                                    const std::string& project_id,
                                    const FileListQuery& query) {
    RequireId(project_id);
    ValidateListQuery(query);
    MySqlConnection connection = pool_.Acquire();
    MySqlTransaction transaction(connection);
    projects_.RequireRole(connection, session.user_id, project_id,
                          Role::Reader);
    if (!query.directory_id.empty() &&
        !repository_->DirectoryExists(connection, project_id,
                                      query.directory_id)) {
        ResourceNotFound();
    }
    Page<FileSummary> result = repository_->List(connection, project_id, query);
    transaction.Commit();
    return result;
}

std::vector<FileVersionSummary> FileService::ListVersions(
    const SessionContext& session, const std::string& project_id,
    const std::string& file_id) {
    RequireId(project_id);
    RequireId(file_id);
    MySqlConnection connection = pool_.Acquire();
    MySqlTransaction transaction(connection);
    projects_.RequireRole(connection, session.user_id, project_id,
                          Role::Reader);
    FileSummary file;
    if (!repository_->FindFile(connection, project_id, file_id, false, &file)) {
        ResourceNotFound();
    }
    std::vector<FileVersionSummary> versions =
        repository_->ListVersions(connection, file_id);
    transaction.Commit();
    return versions;
}

FileSummary FileService::Update(const SessionContext& session,
                                const std::string& project_id,
                                const std::string& file_id,
                                const UpdateFileCommand& raw_command,
                                const std::string& request_id) {
    RequireId(project_id);
    RequireId(file_id);
    RequireId(raw_command.directory_id);
    const std::string name = ValidateFileName(raw_command.name);
    MySqlConnection connection = pool_.Acquire();
    try {
        MySqlTransaction transaction(connection);
        projects_.RequireRole(connection, session.user_id, project_id,
                              Role::Editor);
        FileSummary file;
        if (!repository_->FindFileForUpdate(connection, project_id, file_id,
                                            false, &file) ||
            !repository_->DirectoryExists(connection, project_id,
                                          raw_command.directory_id)) {
            ResourceNotFound();
        }
        repository_->UpdateFile(connection, file_id, name,
                                raw_command.directory_id);
        repository_->InsertAudit(connection, session.user_id, project_id,
                                 "file.update", file_id,
                                 CheckedRequestId(request_id));
        transaction.Commit();
        file.name = name;
        file.directory_id = raw_command.directory_id;
        return file;
    } catch (const AppError& error) {
        if (error.code == "constraint_conflict") {
            throw AppError(409, "name_conflict",
                           "an active file with this name already exists");
        }
        throw;
    }
}

void FileService::SoftDelete(const SessionContext& session,
                             const std::string& project_id,
                             const std::string& file_id,
                             const std::string& request_id) {
    RequireId(project_id);
    RequireId(file_id);
    MySqlConnection connection = pool_.Acquire();
    MySqlTransaction transaction(connection);
    projects_.RequireRole(connection, session.user_id, project_id,
                          Role::Editor);
    FileSummary file;
    if (!repository_->FindFileForUpdate(connection, project_id, file_id,
                                        false, &file)) {
        ResourceNotFound();
    }
    repository_->SoftDelete(connection, file_id, session.user_id);
    repository_->InsertAudit(connection, session.user_id, project_id,
                             "file.delete", file_id,
                             CheckedRequestId(request_id));
    transaction.Commit();
}

FileSummary FileService::Restore(const SessionContext& session,
                                 const std::string& project_id,
                                 const std::string& file_id,
                                 const std::string& request_id) {
    RequireId(project_id);
    RequireId(file_id);
    MySqlConnection connection = pool_.Acquire();
    try {
        MySqlTransaction transaction(connection);
        projects_.RequireRole(connection, session.user_id, project_id,
                              Role::Editor);
        FileSummary file;
        if (!repository_->FindFileForUpdate(connection, project_id, file_id,
                                            true, &file) ||
            !file.deleted ||
            !repository_->DirectoryExists(connection, project_id,
                                          file.directory_id)) {
            ResourceNotFound();
        }
        repository_->Restore(connection, file_id);
        repository_->InsertAudit(connection, session.user_id, project_id,
                                 "file.restore", file_id,
                                 CheckedRequestId(request_id));
        transaction.Commit();
        file.deleted = false;
        return file;
    } catch (const AppError& error) {
        if (error.code == "constraint_conflict") {
            throw AppError(409, "name_conflict",
                           "an active file with this name already exists");
        }
        throw;
    }
}

RemoteAiState FileService::SetRemoteAiPolicy(
    const SessionContext& session, const std::string& project_id,
    const std::string& file_id, const RemoteAiPolicyCommand& command,
    const std::string& request_id) {
    RequireId(project_id);
    RequireId(file_id);
    if (command.policy != "internal_only" &&
        command.policy != "remote_ai_allowed") {
        throw AppError(400, "invalid_request", "remote AI policy is invalid");
    }
    if (command.policy == "internal_only" &&
        !command.approved_version_ids.empty()) {
        throw AppError(400, "invalid_request",
                       "internal-only policy cannot approve versions");
    }
    const std::vector<std::string> approved =
        DistinctVersionIds(command.approved_version_ids);
    MySqlConnection connection = pool_.Acquire();
    MySqlTransaction transaction(connection);
    projects_.RequireRole(connection, session.user_id, project_id,
                          Role::Admin);
    FileSummary file;
    if (!repository_->FindFileForUpdate(connection, project_id, file_id,
                                        false, &file)) {
        ResourceNotFound();
    }
    const std::vector<std::string> versions =
        repository_->ListVersionIdsForUpdate(connection, file_id);
    const std::unordered_set<std::string> belonging(versions.begin(),
                                                     versions.end());
    for (const std::string& id : approved) {
        if (belonging.count(id) == 0) ResourceNotFound();
    }
    repository_->SetRemoteAiPolicy(connection, file_id, command.policy);
    repository_->SetVersionApprovals(connection, file_id, approved,
                                     session.user_id);
    repository_->InsertAudit(connection, session.user_id, project_id,
                             "file.remote_ai_policy", file_id,
                             CheckedRequestId(request_id));
    transaction.Commit();
    return RemoteAiState{command.policy, approved};
}

AuthorizedVersion FileService::OpenCurrentVersion(
    const SessionContext& session, const std::string& project_id,
    const std::string& file_id) {
    RequireId(project_id);
    RequireId(file_id);
    MySqlConnection connection = pool_.Acquire();
    MySqlTransaction transaction(connection);
    projects_.RequireRole(connection, session.user_id, project_id,
                          Role::Reader);
    FileSummary file;
    if (!repository_->FindFile(connection, project_id, file_id, false, &file)) {
        ResourceNotFound();
    }
    FileVersionRecord version;
    if (file.current_version_id.empty() ||
        !repository_->FindVersion(connection, file_id,
                                  file.current_version_id, &version)) {
        throw AppError(500, "database_result_invalid",
                       "file current version is invalid");
    }
    if (version.availability_state != "available") {
        throw AppError(503, "content_unavailable",
                       "file content is unavailable", true);
    }
    AuthorizedVersion result(std::move(file), std::move(version.summary),
                             store_.OpenObject(version.content_id));
    transaction.Commit();
    return result;
}

AuthorizedVersion FileService::OpenVersion(const SessionContext& session,
                                           const std::string& project_id,
                                           const std::string& file_id,
                                           const std::string& version_id) {
    RequireId(project_id);
    RequireId(file_id);
    RequireId(version_id);
    MySqlConnection connection = pool_.Acquire();
    MySqlTransaction transaction(connection);
    projects_.RequireRole(connection, session.user_id, project_id,
                          Role::Reader);
    FileSummary file;
    if (!repository_->FindFile(connection, project_id, file_id, false, &file)) {
        ResourceNotFound();
    }
    FileVersionRecord version;
    if (!repository_->FindVersion(connection, file_id, version_id, &version)) {
        ResourceNotFound();
    }
    if (version.availability_state != "available") {
        throw AppError(503, "content_unavailable",
                       "file content is unavailable", true);
    }
    AuthorizedVersion result(std::move(file), std::move(version.summary),
                             store_.OpenObject(version.content_id));
    transaction.Commit();
    return result;
}
