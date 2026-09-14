#include "upload_repository.h"

#include "core/app_error.h"
#include "core/id.h"

#include <utility>

namespace {

const char* kTaskColumns =
    "t.id, t.project_id, t.owner_user_id, t.state, t.chunk_size_bytes, "
    "t.total_parts, COALESCE((SELECT SUM(p.size_bytes) FROM upload_parts p "
    "WHERE p.upload_task_id=t.id), 0), COALESCE(t.result_file_id, ''), "
    "COALESCE(t.result_version_id, ''), COALESCE(t.processing_job_id, ''), "
    "COALESCE(t.failure_code, ''), t.mode, "
    "COALESCE(t.target_directory_id, ''), COALESCE(t.expected_name, ''), "
    "t.expected_size_bytes, t.expected_sha256, t.media_type, "
    "COALESCE(t.target_file_id, ''), "
    "COALESCE(t.observed_current_version_id, '')";

UploadMode Mode(const std::string& value) {
    if (value == "create_file") return UploadMode::CreateFile;
    if (value == "create_version") return UploadMode::CreateVersion;
    throw AppError(500, "database_result_invalid",
                   "stored upload mode is invalid");
}

UploadTaskRecord RecordFromRow(const MySqlRow& row) {
    return UploadTaskRecord{
        UploadTask{row.String(0), row.String(1), row.String(2), row.String(3),
                   row.UInt64(4), row.UInt64(5), row.UInt64(6), row.String(7),
                   row.String(8), row.String(9), row.String(10)},
        Mode(row.String(11)), row.String(12), row.String(13), row.UInt64(14),
        row.String(15), row.String(16), row.String(17), row.String(18)};
}

UploadTask TaskFromRow(const MySqlRow& row) {
    return RecordFromRow(row).task;
}

std::string ModeName(UploadMode mode) {
    return mode == UploadMode::CreateFile ? "create_file" : "create_version";
}

}  // namespace

bool UploadRepository::DirectoryExists(MySqlConnection& connection,
                                       const std::string& project_id,
                                       const std::string& directory_id) const {
    return connection.ScalarInt(
               "SELECT COUNT(*) FROM directories WHERE project_id=? AND id=?",
               {SqlValue(project_id), SqlValue(directory_id)}) == 1;
}

bool UploadRepository::ActiveNameExists(MySqlConnection& connection,
                                        const std::string& project_id,
                                        const std::string& directory_id,
                                        const std::string& name) const {
    return connection.ScalarInt(
               "SELECT COUNT(*) FROM files WHERE project_id=? AND "
               "directory_id=? AND name=? AND deleted_at IS NULL",
               {SqlValue(project_id), SqlValue(directory_id), SqlValue(name)}) !=
           0;
}

bool UploadRepository::FindActiveFileCurrentVersion(
    MySqlConnection& connection, const std::string& project_id,
    const std::string& file_id, std::string* version_id) const {
    const std::vector<MySqlRow> rows = connection.Query(
        "SELECT current_version_id FROM files WHERE project_id=? AND id=? "
        "AND deleted_at IS NULL LIMIT 1",
        {SqlValue(project_id), SqlValue(file_id)});
    if (rows.empty()) return false;
    *version_id = rows[0].IsNull(0) ? std::string() : rows[0].String(0);
    return true;
}

void UploadRepository::InsertTask(MySqlConnection& connection,
                                  const UploadTaskRecord& record) const {
    connection.Execute(
        "INSERT INTO upload_tasks(id, owner_user_id, project_id, "
        "target_directory_id, target_file_id, observed_current_version_id, "
        "mode, expected_name, expected_size_bytes, expected_sha256, media_type, "
        "chunk_size_bytes, total_parts, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, "
        "?, ?, ?, ?, ?, 'uploading')",
        {SqlValue(record.task.id), SqlValue(record.task.owner_id),
         SqlValue(record.task.project_id),
         record.directory_id.empty() ? SqlValue::Null()
                                     : SqlValue(record.directory_id),
         record.target_file_id.empty() ? SqlValue::Null()
                                       : SqlValue(record.target_file_id),
         record.observed_current_version_id.empty()
             ? SqlValue::Null()
             : SqlValue(record.observed_current_version_id),
         SqlValue(ModeName(record.mode)),
         record.expected_name.empty() ? SqlValue::Null()
                                      : SqlValue(record.expected_name),
         SqlValue(record.expected_size), SqlValue(record.expected_sha256),
         SqlValue(record.media_type), SqlValue(record.task.chunk_size),
         SqlValue(record.task.part_count)});
}

Page<UploadTask> UploadRepository::ListOwn(
    MySqlConnection& connection, const std::string& project_id,
    const std::string& owner_id, const UploadListQuery& query) const {
    std::string where = " WHERE t.project_id=? AND t.owner_user_id=?";
    std::vector<SqlValue> values = {SqlValue(project_id), SqlValue(owner_id)};
    if (!query.state.empty()) {
        where += " AND t.state=?";
        values.emplace_back(query.state);
    }
    const uint64_t total = static_cast<uint64_t>(connection.ScalarInt(
        "SELECT COUNT(*) FROM upload_tasks t" + where, values));
    std::vector<SqlValue> page_values = values;
    page_values.emplace_back(query.page_size);
    page_values.emplace_back((query.page - 1) * query.page_size);
    const std::vector<MySqlRow> rows = connection.Query(
        std::string("SELECT ") + kTaskColumns + " FROM upload_tasks t" + where +
            " ORDER BY t.created_at DESC, t.id LIMIT ? OFFSET ?",
        page_values);
    std::vector<UploadTask> items;
    items.reserve(rows.size());
    for (const MySqlRow& row : rows) items.push_back(TaskFromRow(row));
    return Page<UploadTask>{std::move(items), query.page, query.page_size,
                            total};
}

bool UploadRepository::FindOwn(MySqlConnection& connection,
                               const std::string& project_id,
                               const std::string& owner_id,
                               const std::string& task_id, bool lock,
                               UploadTaskRecord* record) const {
    std::string sql = std::string("SELECT ") + kTaskColumns +
        " FROM upload_tasks t WHERE t.project_id=? AND t.owner_user_id=? "
        "AND t.id=? LIMIT 1";
    if (lock) sql += " FOR UPDATE";
    const std::vector<MySqlRow> rows = connection.Query(
        sql, {SqlValue(project_id), SqlValue(owner_id), SqlValue(task_id)});
    if (rows.empty()) return false;
    *record = RecordFromRow(rows[0]);
    return true;
}

std::vector<PartInfo> UploadRepository::ListParts(
    MySqlConnection& connection, const std::string& task_id) const {
    const std::vector<MySqlRow> rows = connection.Query(
        "SELECT part_number, size_bytes, sha256 FROM upload_parts "
        "WHERE upload_task_id=? ORDER BY part_number",
        {SqlValue(task_id)});
    std::vector<PartInfo> parts;
    parts.reserve(rows.size());
    for (const MySqlRow& row : rows) {
        parts.push_back(PartInfo{static_cast<uint32_t>(row.UInt64(0)),
                                 row.UInt64(1), row.String(2)});
    }
    return parts;
}

bool UploadRepository::FindPart(MySqlConnection& connection,
                                const std::string& task_id,
                                uint32_t part_number, PartInfo* part) const {
    const std::vector<MySqlRow> rows = connection.Query(
        "SELECT part_number, size_bytes, sha256 FROM upload_parts WHERE "
        "upload_task_id=? AND part_number=? LIMIT 1",
        {SqlValue(task_id), SqlValue(static_cast<uint64_t>(part_number))});
    if (rows.empty()) return false;
    *part = PartInfo{static_cast<uint32_t>(rows[0].UInt64(0)),
                     rows[0].UInt64(1), rows[0].String(2)};
    return true;
}

void UploadRepository::InsertPart(MySqlConnection& connection,
                                  const std::string& task_id,
                                  const PartInfo& part) const {
    connection.Execute(
        "INSERT INTO upload_parts(upload_task_id, part_number, size_bytes, "
        "sha256, staging_name) VALUES (?, ?, ?, ?, ?)",
        {SqlValue(task_id), SqlValue(static_cast<uint64_t>(part.part_number)),
         SqlValue(part.size), SqlValue(part.sha256),
         SqlValue(std::to_string(part.part_number))});
}

void UploadRepository::Cancel(MySqlConnection& connection,
                              const std::string& task_id) const {
    connection.Execute(
        "UPDATE upload_tasks SET state='cancelled' WHERE id=?",
        {SqlValue(task_id)});
}

void UploadRepository::InsertAudit(MySqlConnection& connection,
                                   const std::string& actor_id,
                                   const std::string& project_id,
                                   const std::string& action,
                                   const std::string& object_id,
                                   const std::string& request_id) const {
    connection.Execute(
        "INSERT INTO audit_records(id, actor_user_id, project_id, action, "
        "object_type, object_id, request_id, result) VALUES (?, ?, ?, ?, "
        "'upload_task', ?, ?, 'success')",
        {SqlValue(GenerateId()), SqlValue(actor_id), SqlValue(project_id),
         SqlValue(action), SqlValue(object_id), SqlValue(request_id)});
}
