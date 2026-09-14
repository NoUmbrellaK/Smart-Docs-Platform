#include "file_repository.h"

#include "core/app_error.h"
#include "core/id.h"

#include <utility>

namespace {

FileSummary FileFromRow(const MySqlRow& row) {
    const std::string policy = row.String(6);
    if (policy != "internal_only" && policy != "remote_ai_allowed") {
        throw AppError(500, "database_result_invalid",
                       "stored file policy is invalid");
    }
    return FileSummary{row.String(0), row.String(1), row.String(2),
                       row.String(3), row.String(4), !row.IsNull(5), policy};
}

FileVersionSummary VersionFromRow(const MySqlRow& row) {
    return FileVersionSummary{row.String(0), row.String(1), row.UInt64(2),
                              row.UInt64(3), row.String(4), row.String(5),
                              row.String(6), row.Bool(7)};
}

std::string FileWhere(const FileListQuery& query) {
    std::string where = " WHERE project_id=? AND deleted_at IS ";
    where += query.deleted ? "NOT NULL" : "NULL";
    if (!query.directory_id.empty()) where += " AND directory_id=?";
    if (!query.name.empty()) where += " AND INSTR(name, ?) > 0";
    return where;
}

std::vector<SqlValue> FileValues(const std::string& project_id,
                                 const FileListQuery& query) {
    std::vector<SqlValue> values;
    values.emplace_back(project_id);
    if (!query.directory_id.empty()) values.emplace_back(query.directory_id);
    if (!query.name.empty()) values.emplace_back(query.name);
    return values;
}

}  // namespace

bool FileRepository::DirectoryExists(MySqlConnection& connection,
                                     const std::string& project_id,
                                     const std::string& directory_id) const {
    return connection.ScalarInt(
               "SELECT COUNT(*) FROM directories WHERE project_id=? AND id=?",
               {SqlValue(project_id), SqlValue(directory_id)}) == 1;
}

Page<FileSummary> FileRepository::List(MySqlConnection& connection,
                                       const std::string& project_id,
                                       const FileListQuery& query) const {
    const std::string where = FileWhere(query);
    const std::vector<SqlValue> values = FileValues(project_id, query);
    const uint64_t total = static_cast<uint64_t>(connection.ScalarInt(
        "SELECT COUNT(*) FROM files" + where, values));

    std::vector<SqlValue> page_values = values;
    page_values.emplace_back(query.page_size);
    page_values.emplace_back((query.page - 1) * query.page_size);
    const std::vector<MySqlRow> rows = connection.Query(
        "SELECT id, project_id, directory_id, name, current_version_id, "
        "deleted_at, remote_ai_policy FROM files" + where +
            " ORDER BY updated_at DESC, id LIMIT ? OFFSET ?",
        page_values);
    std::vector<FileSummary> items;
    items.reserve(rows.size());
    for (const MySqlRow& row : rows) items.push_back(FileFromRow(row));
    return Page<FileSummary>{std::move(items), query.page, query.page_size,
                             total};
}

bool FileRepository::FindFile(MySqlConnection& connection,
                              const std::string& project_id,
                              const std::string& file_id,
                              bool include_deleted,
                              FileSummary* file) const {
    std::string sql =
        "SELECT id, project_id, directory_id, name, current_version_id, "
        "deleted_at, remote_ai_policy FROM files WHERE project_id=? AND id=?";
    if (!include_deleted) sql += " AND deleted_at IS NULL";
    sql += " LIMIT 1";
    const std::vector<MySqlRow> rows =
        connection.Query(sql, {SqlValue(project_id), SqlValue(file_id)});
    if (rows.empty()) return false;
    *file = FileFromRow(rows[0]);
    return true;
}

bool FileRepository::FindFileForUpdate(MySqlConnection& connection,
                                       const std::string& project_id,
                                       const std::string& file_id,
                                       bool include_deleted,
                                       FileSummary* file) const {
    std::string sql =
        "SELECT id, project_id, directory_id, name, current_version_id, "
        "deleted_at, remote_ai_policy FROM files WHERE project_id=? AND id=?";
    if (!include_deleted) sql += " AND deleted_at IS NULL";
    sql += " LIMIT 1 FOR UPDATE";
    const std::vector<MySqlRow> rows =
        connection.Query(sql, {SqlValue(project_id), SqlValue(file_id)});
    if (rows.empty()) return false;
    *file = FileFromRow(rows[0]);
    return true;
}

void FileRepository::UpdateFile(MySqlConnection& connection,
                                const std::string& file_id,
                                const std::string& name,
                                const std::string& directory_id) const {
    connection.Execute("UPDATE files SET name=?, directory_id=? WHERE id=?",
                       {SqlValue(name), SqlValue(directory_id),
                        SqlValue(file_id)});
}

void FileRepository::SoftDelete(MySqlConnection& connection,
                                const std::string& file_id,
                                const std::string& actor_id) const {
    connection.Execute(
        "UPDATE files SET deleted_at=UTC_TIMESTAMP(6), deleted_by=? "
        "WHERE id=?",
        {SqlValue(actor_id), SqlValue(file_id)});
}

void FileRepository::Restore(MySqlConnection& connection,
                             const std::string& file_id) const {
    connection.Execute(
        "UPDATE files SET deleted_at=NULL, deleted_by=NULL WHERE id=?",
        {SqlValue(file_id)});
}

void FileRepository::SetRemoteAiPolicy(MySqlConnection& connection,
                                       const std::string& file_id,
                                       const std::string& policy) const {
    connection.Execute("UPDATE files SET remote_ai_policy=? WHERE id=?",
                       {SqlValue(policy), SqlValue(file_id)});
}

std::vector<std::string> FileRepository::ListVersionIdsForUpdate(
    MySqlConnection& connection, const std::string& file_id) const {
    const std::vector<MySqlRow> rows = connection.Query(
        "SELECT id FROM file_versions WHERE file_id=? "
        "ORDER BY version_number FOR UPDATE",
        {SqlValue(file_id)});
    std::vector<std::string> ids;
    ids.reserve(rows.size());
    for (const MySqlRow& row : rows) ids.push_back(row.String(0));
    return ids;
}

void FileRepository::SetVersionApprovals(
    MySqlConnection& connection, const std::string& file_id,
    const std::vector<std::string>& approved_version_ids,
    const std::string& actor_id) const {
    connection.Execute(
        "UPDATE file_versions SET remote_ai_approved=FALSE, "
        "remote_ai_approved_at=NULL, remote_ai_approved_by=NULL "
        "WHERE file_id=?",
        {SqlValue(file_id)});
    for (const std::string& version_id : approved_version_ids) {
        connection.Execute(
            "UPDATE file_versions SET remote_ai_approved=TRUE, "
            "remote_ai_approved_at=UTC_TIMESTAMP(6), remote_ai_approved_by=? "
            "WHERE file_id=? AND id=?",
            {SqlValue(actor_id), SqlValue(file_id), SqlValue(version_id)});
    }
}

void FileRepository::InsertAudit(MySqlConnection& connection,
                                 const std::string& actor_id,
                                 const std::string& project_id,
                                 const std::string& action,
                                 const std::string& file_id,
                                 const std::string& request_id) const {
    connection.Execute(
        "INSERT INTO audit_records(id, actor_user_id, project_id, action, "
        "object_type, object_id, request_id, result) "
        "VALUES (?, ?, ?, ?, 'file', ?, ?, 'success')",
        {SqlValue(GenerateId()), SqlValue(actor_id), SqlValue(project_id),
         SqlValue(action), SqlValue(file_id), SqlValue(request_id)});
}

std::vector<FileVersionSummary> FileRepository::ListVersions(
    MySqlConnection& connection, const std::string& file_id) const {
    const std::vector<MySqlRow> rows = connection.Query(
        "SELECT id, file_id, version_number, size_bytes, sha256, media_type, "
        "processing_state, remote_ai_approved FROM file_versions "
        "WHERE file_id=? ORDER BY version_number DESC",
        {SqlValue(file_id)});
    std::vector<FileVersionSummary> versions;
    versions.reserve(rows.size());
    for (const MySqlRow& row : rows) versions.push_back(VersionFromRow(row));
    return versions;
}

bool FileRepository::FindVersion(MySqlConnection& connection,
                                 const std::string& file_id,
                                 const std::string& version_id,
                                 FileVersionRecord* version) const {
    const std::vector<MySqlRow> rows = connection.Query(
        "SELECT id, file_id, version_number, size_bytes, sha256, media_type, "
        "processing_state, remote_ai_approved, content_id, "
        "availability_state FROM file_versions WHERE file_id=? AND id=? "
        "LIMIT 1",
        {SqlValue(file_id), SqlValue(version_id)});
    if (rows.empty()) return false;
    version->summary = VersionFromRow(rows[0]);
    version->content_id = rows[0].String(8);
    version->availability_state = rows[0].String(9);
    return true;
}
