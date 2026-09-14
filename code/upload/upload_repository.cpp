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
    const std::string& file_id, std::string* version_id, bool lock) const {
    std::string sql =
        "SELECT current_version_id FROM files WHERE project_id=? AND id=? "
        "AND deleted_at IS NULL LIMIT 1";
    if (lock) sql += " FOR UPDATE";
    const std::vector<MySqlRow> rows = connection.Query(
        sql,
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

void UploadRepository::SetState(MySqlConnection& connection,
                                const std::string& task_id,
                                const std::string& state) const {
    connection.Execute(
        "UPDATE upload_tasks SET state=?, failure_code=NULL, "
        "failure_message=NULL WHERE id=? AND state<>'completed'",
        {SqlValue(state), SqlValue(task_id)});
}

void UploadRepository::SetFailed(MySqlConnection& connection,
                                 const std::string& task_id,
                                 const std::string& code,
                                 const std::string& message) const {
    connection.Execute(
        "UPDATE upload_tasks SET state='failed', failure_code=?, "
        "failure_message=? WHERE id=? AND state<>'completed'",
        {SqlValue(code), SqlValue(message), SqlValue(task_id)});
}

void UploadRepository::InsertFile(MySqlConnection& connection,
                                  const std::string& file_id,
                                  const UploadTaskRecord& task,
                                  const std::string& actor_id) const {
    connection.Execute(
        "INSERT INTO files(id, project_id, directory_id, name, created_by) "
        "VALUES (?, ?, ?, ?, ?)",
        {SqlValue(file_id), SqlValue(task.task.project_id),
         SqlValue(task.directory_id), SqlValue(task.expected_name),
         SqlValue(actor_id)});
}

uint64_t UploadRepository::NextVersionNumber(
    MySqlConnection& connection, const std::string& file_id) const {
    return static_cast<uint64_t>(connection.ScalarInt(
               "SELECT COALESCE(MAX(version_number), 0) FROM file_versions "
               "WHERE file_id=?",
               {SqlValue(file_id)})) + 1;
}

void UploadRepository::InsertVersion(MySqlConnection& connection,
                                     const std::string& version_id,
                                     const std::string& file_id,
                                     uint64_t version_number,
                                     const std::string& content_id,
                                     const UploadTaskRecord& task,
                                     const std::string& actor_id) const {
    connection.Execute(
        "INSERT INTO file_versions(id, file_id, version_number, content_id, "
        "size_bytes, sha256, media_type, created_by, processing_state, "
        "remote_ai_approved, remote_ai_approved_at, remote_ai_approved_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', FALSE, NULL, NULL)",
        {SqlValue(version_id), SqlValue(file_id), SqlValue(version_number),
         SqlValue(content_id), SqlValue(task.expected_size),
         SqlValue(task.expected_sha256), SqlValue(task.media_type),
         SqlValue(actor_id)});
}

void UploadRepository::SetCurrentVersion(MySqlConnection& connection,
                                         const std::string& file_id,
                                         const std::string& version_id) const {
    connection.Execute("UPDATE files SET current_version_id=? WHERE id=?",
                       {SqlValue(version_id), SqlValue(file_id)});
}

void UploadRepository::InsertProcessingJob(MySqlConnection& connection,
                                           const std::string& job_id,
                                           const std::string& version_id) const {
    connection.Execute(
        "INSERT INTO processing_jobs(id, file_version_id, task_type, state, "
        "attempt_count) VALUES (?, ?, 'parse_and_index', 'pending', 0)",
        {SqlValue(job_id), SqlValue(version_id)});
}

void UploadRepository::SetCompleted(MySqlConnection& connection,
                                    const std::string& task_id,
                                    const std::string& file_id,
                                    const std::string& version_id,
                                    const std::string& job_id) const {
    connection.Execute(
        "UPDATE upload_tasks SET state='completed', result_file_id=?, "
        "result_version_id=?, processing_job_id=?, failure_code=NULL, "
        "failure_message=NULL, completed_at=UTC_TIMESTAMP(6) WHERE id=?",
        {SqlValue(file_id), SqlValue(version_id), SqlValue(job_id),
         SqlValue(task_id)});
}

bool UploadRepository::CompletedGraphValid(
    MySqlConnection& connection, const UploadTaskRecord& task) const {
    if (task.task.file_id.empty() || task.task.version_id.empty() ||
        task.task.processing_job_id.empty()) {
        return false;
    }
    return connection.ScalarInt(
               "SELECT COUNT(*) FROM files f JOIN file_versions v "
               "ON v.file_id=f.id JOIN processing_jobs j "
               "ON j.file_version_id=v.id WHERE f.id=? AND f.project_id=? "
               "AND v.id=? AND j.id=?",
               {SqlValue(task.task.file_id), SqlValue(task.task.project_id),
                SqlValue(task.task.version_id),
                SqlValue(task.task.processing_job_id)}) == 1;
}

std::vector<UploadTaskRecord> UploadRepository::ListInFlight(
    MySqlConnection& connection) const {
    const std::vector<MySqlRow> rows = connection.Query(
        std::string("SELECT ") + kTaskColumns +
        " FROM upload_tasks t WHERE t.state IN "
        "('uploading', 'assembling', 'publishing') ORDER BY t.id");
    std::vector<UploadTaskRecord> tasks;
    tasks.reserve(rows.size());
    for (const MySqlRow& row : rows) tasks.push_back(RecordFromRow(row));
    return tasks;
}

std::vector<PublishedVersionRecord> UploadRepository::ListAvailableVersions(
    MySqlConnection& connection) const {
    const std::vector<MySqlRow> rows = connection.Query(
        "SELECT v.id, f.project_id, v.content_id FROM file_versions v "
        "JOIN files f ON f.id=v.file_id WHERE "
        "v.availability_state='available'");
    std::vector<PublishedVersionRecord> versions;
    versions.reserve(rows.size());
    for (const MySqlRow& row : rows) {
        versions.push_back(
            PublishedVersionRecord{row.String(0), row.String(1), row.String(2)});
    }
    return versions;
}

bool UploadRepository::ContentReferenced(MySqlConnection& connection,
                                         const std::string& content_id) const {
    return connection.ScalarInt(
               "SELECT COUNT(*) FROM file_versions WHERE content_id=?",
               {SqlValue(content_id)}) != 0;
}

void UploadRepository::MarkVersionUnavailable(
    MySqlConnection& connection,
    const PublishedVersionRecord& version) const {
    const uint64_t changed = connection.Execute(
        "UPDATE file_versions SET availability_state='unavailable' "
        "WHERE id=? AND availability_state='available'",
        {SqlValue(version.version_id)});
    if (changed == 0) return;
    connection.Execute(
        "INSERT INTO audit_records(id, actor_user_id, project_id, action, "
        "object_type, object_id, request_id, result, detail_code) VALUES "
        "(?, NULL, ?, 'file.version.missing_object', 'file_version', ?, ?, "
        "'error', 'content_missing')",
        {SqlValue(GenerateId()), SqlValue(version.project_id),
         SqlValue(version.version_id), SqlValue(GenerateId())});
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
