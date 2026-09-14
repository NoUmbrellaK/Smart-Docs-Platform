#include "project_repository.h"

#include "core/id.h"

bool ProjectRepository::UserExists(MySqlConnection& connection,
                                   const std::string& user_id) const {
    return connection.ScalarInt(
               "SELECT COUNT(*) FROM users WHERE id=? AND status='active'",
               {SqlValue(user_id)}) == 1;
}

void ProjectRepository::InsertProject(MySqlConnection& connection,
                                      const Project& project,
                                      const std::string& creator_id) const {
    connection.Execute(
        "INSERT INTO projects(id, name, created_by) VALUES (?, ?, ?)",
        {SqlValue(project.id), SqlValue(project.name), SqlValue(creator_id)});
}

void ProjectRepository::SetProjectRoot(MySqlConnection& connection,
                                       const std::string& project_id,
                                       const std::string& root_id) const {
    connection.Execute(
        "UPDATE projects SET root_directory_id=? WHERE id=?",
        {SqlValue(root_id), SqlValue(project_id)});
}

void ProjectRepository::UpsertMember(MySqlConnection& connection,
                                     const std::string& project_id,
                                     const std::string& user_id,
                                     Role role) const {
    connection.Execute(
        "INSERT INTO project_members(project_id, user_id, role) "
        "VALUES (?, ?, ?) ON DUPLICATE KEY UPDATE role=VALUES(role)",
        {SqlValue(project_id), SqlValue(user_id), SqlValue(RoleName(role))});
}

bool ProjectRepository::FindRole(MySqlConnection& connection,
                                 const std::string& user_id,
                                 const std::string& project_id,
                                 Role* role) const {
    const std::vector<MySqlRow> rows = connection.Query(
        "SELECT role FROM project_members WHERE user_id=? AND project_id=? "
        "LIMIT 1 FOR SHARE",
        {SqlValue(user_id), SqlValue(project_id)});
    if (rows.empty()) {
        return false;
    }
    *role = ParseRole(rows[0].String(0));
    return true;
}

std::vector<ProjectMembership> ProjectRepository::ListForUser(
    MySqlConnection& connection, const std::string& user_id) const {
    const std::vector<MySqlRow> rows = connection.Query(
        "SELECT p.id, p.name, p.root_directory_id, pm.role "
        "FROM project_members pm JOIN projects p ON p.id=pm.project_id "
        "WHERE pm.user_id=? ORDER BY p.created_at, p.id",
        {SqlValue(user_id)});
    std::vector<ProjectMembership> result;
    result.reserve(rows.size());
    for (const MySqlRow& row : rows) {
        result.push_back(ProjectMembership{
            Project{row.String(0), row.String(1), row.String(2)},
            ParseRole(row.String(3))});
    }
    return result;
}

std::vector<ProjectMember> ProjectRepository::ListMembers(
    MySqlConnection& connection, const std::string& project_id) const {
    const std::vector<MySqlRow> rows = connection.Query(
        "SELECT u.id, u.login_name, pm.role FROM project_members pm "
        "JOIN users u ON u.id=pm.user_id WHERE pm.project_id=? "
        "ORDER BY u.login_name, u.id",
        {SqlValue(project_id)});
    std::vector<ProjectMember> result;
    result.reserve(rows.size());
    for (const MySqlRow& row : rows) {
        result.push_back(ProjectMember{row.String(0), row.String(1),
                                       ParseRole(row.String(2))});
    }
    return result;
}

void ProjectRepository::InsertDirectory(MySqlConnection& connection,
                                        const Directory& directory,
                                        const std::string& creator_id) const {
    connection.Execute(
        "INSERT INTO directories(id, project_id, parent_id, name, created_by) "
        "VALUES (?, ?, ?, ?, ?)",
        {SqlValue(directory.id), SqlValue(directory.project_id),
         directory.parent_id.empty() ? SqlValue::Null()
                                     : SqlValue(directory.parent_id),
         SqlValue(directory.name), SqlValue(creator_id)});
}

bool ProjectRepository::FindDirectory(MySqlConnection& connection,
                                      const std::string& project_id,
                                      const std::string& directory_id,
                                      Directory* directory) const {
    const std::vector<MySqlRow> rows = connection.Query(
        "SELECT id, project_id, parent_id, name FROM directories "
        "WHERE project_id=? AND id=? LIMIT 1",
        {SqlValue(project_id), SqlValue(directory_id)});
    if (rows.empty()) {
        return false;
    }
    *directory = Directory{rows[0].String(0), rows[0].String(1),
                           rows[0].IsNull(2) ? std::string()
                                             : rows[0].String(2),
                           rows[0].String(3)};
    return true;
}

std::vector<Directory> ProjectRepository::ListDirectories(
    MySqlConnection& connection, const std::string& project_id) const {
    const std::vector<MySqlRow> rows = connection.Query(
        "SELECT id, project_id, parent_id, name FROM directories "
        "WHERE project_id=? ORDER BY created_at, id",
        {SqlValue(project_id)});
    std::vector<Directory> result;
    result.reserve(rows.size());
    for (const MySqlRow& row : rows) {
        result.push_back(Directory{
            row.String(0), row.String(1),
            row.IsNull(2) ? std::string() : row.String(2), row.String(3)});
    }
    return result;
}

void ProjectRepository::RenameDirectory(MySqlConnection& connection,
                                        const std::string& project_id,
                                        const std::string& directory_id,
                                        const std::string& name) const {
    connection.Execute(
        "UPDATE directories SET name=? WHERE project_id=? AND id=?",
        {SqlValue(name), SqlValue(project_id), SqlValue(directory_id)});
}

void ProjectRepository::InsertAudit(MySqlConnection& connection,
                                    const std::string& actor_id,
                                    const std::string& project_id,
                                    const std::string& action,
                                    const std::string& object_type,
                                    const std::string& object_id,
                                    const std::string& request_id) const {
    connection.Execute(
        "INSERT INTO audit_records(id, actor_user_id, project_id, action, "
        "object_type, object_id, request_id, result) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'success')",
        {SqlValue(GenerateId()), SqlValue(actor_id), SqlValue(project_id),
         SqlValue(action), SqlValue(object_type), SqlValue(object_id),
         SqlValue(request_id)});
}
