#include "file_service.h"

#include "file_repository.h"
#include "file_store.h"
#include "core/app_error.h"
#include "project/project_service.h"

#include <cctype>
#include <limits>
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
