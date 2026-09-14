#pragma once

#include "auth/auth_service.h"

#include <memory>
#include <string>
#include <vector>

class MySqlConnection;
class MySqlPool;
class ProjectRepository;
class FileService;

enum class Role { Reader, Editor, Admin };

enum class Action {
    ReadProject,
    UploadFile,
    MutateFile,
    ManageMembers,
    ManageDirectories,
    ManageRemoteAiPolicy,
};

bool Authorize(Role role, Action action);

std::string RoleName(Role role);
Role ParseRole(const std::string& value);

struct Project {
    std::string id;
    std::string name;
    std::string root_directory_id;
};

struct ProjectMembership {
    Project project;
    Role role;
};

struct ProjectMember {
    std::string user_id;
    std::string username;
    Role role;
};

struct Directory {
    std::string id;
    std::string project_id;
    std::string parent_id;
    std::string name;
};

class ProjectService {
public:
    explicit ProjectService(MySqlPool& pool);
    ~ProjectService();

    Project CreateProject(const std::string& user_id, const std::string& name,
                          const std::string& request_id = std::string());
    std::vector<ProjectMembership> ListForUser(const std::string& user_id);
    Role RequireRole(const std::string& user_id, const std::string& project_id,
                     Role minimum);
    std::vector<ProjectMember> ListMembers(const SessionContext& session,
                                           const std::string& project_id);
    void SetMemberRole(const SessionContext& session,
                       const std::string& project_id,
                       const std::string& member_user_id, Role role,
                       const std::string& request_id = std::string());
    std::vector<Directory> ListDirectories(const SessionContext& session,
                                           const std::string& project_id);
    Directory CreateDirectory(const SessionContext& session,
                              const std::string& project_id,
                              const std::string& parent_id,
                              const std::string& name,
                              const std::string& request_id = std::string());
    Directory RenameDirectory(const SessionContext& session,
                              const std::string& project_id,
                              const std::string& directory_id,
                              const std::string& name,
                              const std::string& request_id = std::string());

private:
    Role RequireRole(MySqlConnection& connection, const std::string& user_id,
                     const std::string& project_id, Role minimum);

    MySqlPool& pool_;
    std::unique_ptr<ProjectRepository> repository_;

    friend class FileService;
};
