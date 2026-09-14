#include "project_service.h"

#include "project_repository.h"
#include "core/app_error.h"
#include "core/id.h"

#include <cctype>
#include <utility>

namespace {

bool IsLowerHexId(const std::string& value) {
    if (value.size() != 32) {
        return false;
    }
    for (unsigned char ch : value) {
        if (!std::isdigit(ch) && (ch < 'a' || ch > 'f')) {
            return false;
        }
    }
    return true;
}

std::string CheckedRequestId(const std::string& request_id) {
    if (request_id.empty()) {
        return GenerateId();
    }
    if (!IsLowerHexId(request_id)) {
        throw AppError(400, "invalid_request", "request ID is invalid");
    }
    return request_id;
}

std::string TrimmedName(const std::string& value) {
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
    const std::string result = value.substr(first, last - first);
    if (result.empty() || result.size() > 255 ||
        result.find('/') != std::string::npos ||
        result.find('\0') != std::string::npos) {
        throw AppError(400, "invalid_request", "name is invalid");
    }
    return result;
}

Action MinimumAction(Role minimum) {
    switch (minimum) {
    case Role::Reader: return Action::ReadProject;
    case Role::Editor: return Action::UploadFile;
    case Role::Admin: return Action::ManageMembers;
    }
    return Action::ManageMembers;
}

[[noreturn]] void ResourceNotFound() {
    throw AppError(404, "resource_not_found", "resource was not found");
}

}  // namespace

bool Authorize(Role role, Action action) {
    switch (action) {
    case Action::ReadProject:
        return true;
    case Action::UploadFile:
    case Action::MutateFile:
        return role == Role::Editor || role == Role::Admin;
    case Action::ManageMembers:
    case Action::ManageDirectories:
    case Action::ManageRemoteAiPolicy:
        return role == Role::Admin;
    }
    return false;
}

std::string RoleName(Role role) {
    switch (role) {
    case Role::Reader: return "reader";
    case Role::Editor: return "editor";
    case Role::Admin: return "admin";
    }
    throw AppError(500, "role_invalid", "stored project role is invalid");
}

Role ParseRole(const std::string& value) {
    if (value == "reader") return Role::Reader;
    if (value == "editor") return Role::Editor;
    if (value == "admin") return Role::Admin;
    throw AppError(500, "role_invalid", "stored project role is invalid");
}

ProjectService::ProjectService(MySqlPool& pool)
    : pool_(pool), repository_(new ProjectRepository()) {}

ProjectService::~ProjectService() = default;

Role ProjectService::RequireRole(MySqlConnection& connection,
                                 const std::string& user_id,
                                 const std::string& project_id,
                                 Role minimum) {
    Role actual;
    if (!repository_->FindRole(connection, user_id, project_id, &actual)) {
        ResourceNotFound();
    }
    if (!Authorize(actual, MinimumAction(minimum))) {
        throw AppError(403, "forbidden", "project role does not permit this action");
    }
    return actual;
}

Role ProjectService::RequireRole(const std::string& user_id,
                                 const std::string& project_id,
                                 Role minimum) {
    MySqlConnection connection = pool_.Acquire();
    return RequireRole(connection, user_id, project_id, minimum);
}

Project ProjectService::CreateProject(const std::string& user_id,
                                      const std::string& raw_name,
                                      const std::string& request_id) {
    const std::string name = TrimmedName(raw_name);
    if (!IsLowerHexId(user_id)) {
        ResourceNotFound();
    }
    Project project{GenerateId(), name, GenerateId()};
    MySqlConnection connection = pool_.Acquire();
    MySqlTransaction transaction(connection);
    if (!repository_->UserExists(connection, user_id)) {
        ResourceNotFound();
    }
    repository_->InsertProject(connection, project, user_id);
    repository_->InsertDirectory(
        connection, Directory{project.root_directory_id, project.id,
                              std::string(), "/"}, user_id);
    repository_->SetProjectRoot(connection, project.id,
                                project.root_directory_id);
    repository_->UpsertMember(connection, project.id, user_id, Role::Admin);
    repository_->InsertAudit(connection, user_id, project.id, "project.create",
                             "project", project.id,
                             CheckedRequestId(request_id));
    transaction.Commit();
    return project;
}

std::vector<ProjectMembership> ProjectService::ListForUser(
    const std::string& user_id) {
    MySqlConnection connection = pool_.Acquire();
    return repository_->ListForUser(connection, user_id);
}

std::vector<ProjectMember> ProjectService::ListMembers(
    const SessionContext& session, const std::string& project_id) {
    MySqlConnection connection = pool_.Acquire();
    MySqlTransaction transaction(connection);
    RequireRole(connection, session.user_id, project_id, Role::Admin);
    std::vector<ProjectMember> members =
        repository_->ListMembers(connection, project_id);
    transaction.Commit();
    return members;
}

void ProjectService::SetMemberRole(const SessionContext& session,
                                   const std::string& project_id,
                                   const std::string& member_user_id,
                                   Role role,
                                   const std::string& request_id) {
    MySqlConnection connection = pool_.Acquire();
    MySqlTransaction transaction(connection);
    RequireRole(connection, session.user_id, project_id, Role::Admin);
    if (!repository_->UserExists(connection, member_user_id)) {
        ResourceNotFound();
    }
    repository_->UpsertMember(connection, project_id, member_user_id, role);
    repository_->InsertAudit(connection, session.user_id, project_id,
                             "project.member.set", "user", member_user_id,
                             CheckedRequestId(request_id));
    transaction.Commit();
}

std::vector<Directory> ProjectService::ListDirectories(
    const SessionContext& session, const std::string& project_id) {
    MySqlConnection connection = pool_.Acquire();
    MySqlTransaction transaction(connection);
    RequireRole(connection, session.user_id, project_id, Role::Reader);
    std::vector<Directory> directories =
        repository_->ListDirectories(connection, project_id);
    transaction.Commit();
    return directories;
}

Directory ProjectService::CreateDirectory(const SessionContext& session,
                                          const std::string& project_id,
                                          const std::string& parent_id,
                                          const std::string& raw_name,
                                          const std::string& request_id) {
    const std::string name = TrimmedName(raw_name);
    MySqlConnection connection = pool_.Acquire();
    try {
        MySqlTransaction transaction(connection);
        RequireRole(connection, session.user_id, project_id, Role::Admin);
        Directory parent;
        if (!repository_->FindDirectory(connection, project_id, parent_id,
                                        &parent)) {
            ResourceNotFound();
        }
        Directory directory{GenerateId(), project_id, parent.id, name};
        repository_->InsertDirectory(connection, directory, session.user_id);
        repository_->InsertAudit(connection, session.user_id, project_id,
                                 "directory.create", "directory", directory.id,
                                 CheckedRequestId(request_id));
        transaction.Commit();
        return directory;
    } catch (const AppError& error) {
        if (error.code == "constraint_conflict") {
            throw AppError(409, "name_conflict",
                           "a directory with this name already exists");
        }
        throw;
    }
}

Directory ProjectService::RenameDirectory(const SessionContext& session,
                                          const std::string& project_id,
                                          const std::string& directory_id,
                                          const std::string& raw_name,
                                          const std::string& request_id) {
    const std::string name = TrimmedName(raw_name);
    MySqlConnection connection = pool_.Acquire();
    try {
        MySqlTransaction transaction(connection);
        RequireRole(connection, session.user_id, project_id, Role::Admin);
        Directory directory;
        if (!repository_->FindDirectory(connection, project_id, directory_id,
                                        &directory)) {
            ResourceNotFound();
        }
        if (directory.parent_id.empty()) {
            throw AppError(409, "root_directory_immutable",
                           "the project root directory cannot be renamed");
        }
        repository_->RenameDirectory(connection, project_id, directory_id,
                                     name);
        repository_->InsertAudit(connection, session.user_id, project_id,
                                 "directory.rename", "directory", directory.id,
                                 CheckedRequestId(request_id));
        transaction.Commit();
        directory.name = name;
        return directory;
    } catch (const AppError& error) {
        if (error.code == "constraint_conflict") {
            throw AppError(409, "name_conflict",
                           "a directory with this name already exists");
        }
        throw;
    }
}
