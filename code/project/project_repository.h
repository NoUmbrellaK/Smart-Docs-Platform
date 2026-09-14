#pragma once

#include "db/mysql.h"
#include "project_service.h"

#include <string>
#include <vector>

class ProjectRepository {
public:
    bool UserExists(MySqlConnection& connection,
                    const std::string& user_id) const;
    void InsertProject(MySqlConnection& connection, const Project& project,
                       const std::string& creator_id) const;
    void SetProjectRoot(MySqlConnection& connection,
                        const std::string& project_id,
                        const std::string& root_id) const;
    void UpsertMember(MySqlConnection& connection,
                      const std::string& project_id,
                      const std::string& user_id, Role role) const;
    bool FindRole(MySqlConnection& connection, const std::string& user_id,
                  const std::string& project_id, Role* role) const;
    std::vector<ProjectMembership> ListForUser(
        MySqlConnection& connection, const std::string& user_id) const;
    std::vector<ProjectMember> ListMembers(
        MySqlConnection& connection, const std::string& project_id) const;
    void InsertDirectory(MySqlConnection& connection,
                         const Directory& directory,
                         const std::string& creator_id) const;
    bool FindDirectory(MySqlConnection& connection,
                       const std::string& project_id,
                       const std::string& directory_id,
                       Directory* directory) const;
    std::vector<Directory> ListDirectories(
        MySqlConnection& connection, const std::string& project_id) const;
    void RenameDirectory(MySqlConnection& connection,
                         const std::string& project_id,
                         const std::string& directory_id,
                         const std::string& name) const;
    void InsertAudit(MySqlConnection& connection, const std::string& actor_id,
                     const std::string& project_id, const std::string& action,
                     const std::string& object_type,
                     const std::string& object_id,
                     const std::string& request_id) const;
};
