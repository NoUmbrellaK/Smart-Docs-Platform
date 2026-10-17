#include "processing_service.h"

#include "core/app_error.h"
#include "core/id.h"
#include "db/mysql.h"
#include "project/project_service.h"
#include <limits>
#include <unordered_set>

namespace {

void RequireId(const std::string& id) {
    if (id.size() != 32 || id.find_first_not_of("0123456789abcdef") != std::string::npos)
        throw AppError(400, "invalid_request", "entity ID is invalid");
}

void LockQueue(MySqlConnection& db) {
    if (db.Query("SELECT id FROM processing_queue_guard WHERE id=1 FOR UPDATE").size() != 1)
        throw AppError(503, "queue_unavailable", "processing queue is unavailable", true);
}

bool QueueFull(MySqlConnection& db) {
    // Current locking read, not the transaction's possibly older snapshot.
    return db.Query("SELECT id FROM processing_jobs WHERE state='pending' "
                    "ORDER BY created_at,id LIMIT 100 FOR UPDATE").size() == 100;
}

struct Record {
    ProcessingJob job;
    std::string token, current_version, availability;
    bool deleted;
};

Record Read(MySqlConnection& db, const std::string& id, bool lock = false) {
    const auto rows = db.Query(
        "SELECT j.id,v.file_id,v.id,f.project_id,j.state,j.attempt_count,"
        "COALESCE(j.last_error_code,''),COALESCE(j.attempt_token,''),"
        "COALESCE(f.current_version_id,''),v.availability_state,f.deleted_at IS NOT NULL "
        "FROM processing_jobs j JOIN file_versions v ON v.id=j.file_version_id "
        "JOIN files f ON f.id=v.file_id WHERE j.id=?" + std::string(lock ? " FOR UPDATE" : ""),
        {SqlValue(id)});
    if (rows.empty()) throw AppError(404, "resource_not_found", "resource was not found");
    const auto& r = rows[0];
    return {{r.String(0), r.String(1), r.String(2), r.String(3), r.String(4), r.UInt64(5), r.String(6)},
            r.String(7), r.String(8), r.String(9), r.Bool(10)};
}

Record LockJob(MySqlConnection& db, const std::string& id) {
    const Record locator = Read(db, id);
    db.Query("SELECT id FROM files WHERE id=? FOR UPDATE", {SqlValue(locator.job.file_id)});
    LockQueue(db);
    return Read(db, id, true);
}

void RequireVisible(const Record& record, const std::string& project_id) {
    if (record.deleted || record.job.project_id != project_id)
        throw AppError(404, "resource_not_found", "resource was not found");
}

void SetState(MySqlConnection& db, const Record& record, const std::string& state,
              const std::string& error = "") {
    db.Execute("UPDATE processing_jobs SET state=?,last_error_code=?,last_error_message=NULL,"
               "completed_at=CASE WHEN ?='pending' THEN NULL ELSE UTC_TIMESTAMP(6) END WHERE id=?",
               {SqlValue(state), error.empty() ? SqlValue::Null() : SqlValue(error),
                SqlValue(state), SqlValue(record.job.id)});
    db.Execute("UPDATE file_versions SET processing_state=? WHERE id=?",
               {SqlValue(state), SqlValue(record.job.version_id)});
}

void Audit(MySqlConnection& db, const SessionContext& session, const Record& record,
           const std::string& action, const std::string& request_id) {
    const std::string request = request_id.empty() ? GenerateId() : request_id;
    RequireId(request);
    db.Execute("INSERT INTO audit_records(id,actor_user_id,project_id,action,object_type,object_id,request_id,result) "
               "VALUES (?,?,?,?,'processing_job',?,?,'success')",
               {SqlValue(GenerateId()), SqlValue(session.user_id), SqlValue(record.job.project_id),
                SqlValue(action), SqlValue(record.job.id), SqlValue(request)});
}

}  // namespace

void EnqueueProcessingJob(MySqlConnection& db, const std::string& job_id,
                          const std::string& version_id) {
    LockQueue(db);
    const bool full = QueueFull(db);
    db.Execute("INSERT INTO processing_jobs(id,file_version_id,task_type,state,attempt_count,last_error_code,completed_at) "
               "VALUES (?,?,'parse_and_index',?,0,?,CASE WHEN ? THEN UTC_TIMESTAMP(6) ELSE NULL END)",
               {SqlValue(job_id), SqlValue(version_id), SqlValue(full ? "failed" : "pending"),
                full ? SqlValue("queue_full") : SqlValue::Null(), SqlValue(full)});
    if (full) db.Execute("UPDATE file_versions SET processing_state='failed' WHERE id=?", {SqlValue(version_id)});
}

ProcessingService::ProcessingService(MySqlPool& pool, ProjectService& projects)
    : pool_(pool), projects_(projects) {}

ProcessingJob ProcessingService::Get(const SessionContext& session, const std::string& project,
                                     const std::string& id) {
    RequireId(project); RequireId(id);
    auto db = pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db, session.user_id, project, Role::Reader);
    auto record = Read(db, id);
    RequireVisible(record, project);
    transaction.Commit(); return record.job;
}

ProcessingJob ProcessingService::Cancel(const SessionContext& session, const std::string& project,
                                        const std::string& id, const std::string& request) {
    RequireId(project); RequireId(id);
    auto db = pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db, session.user_id, project, Role::Editor);
    RequireVisible(Read(db, id), project);
    auto record = LockJob(db, id);
    RequireVisible(record, project);
    if (record.job.state != "cancelled") {
        if (record.job.state != "pending" && record.job.state != "running")
            throw AppError(409, "task_conflict", "task cannot be cancelled in this state");
        SetState(db, record, "cancelled");
        Audit(db, session, record, "processing.cancel", request);
    }
    auto result = Read(db, id, true).job;
    transaction.Commit(); return result;
}

ProcessingJob ProcessingService::Retry(const SessionContext& session, const std::string& project,
                                       const std::string& id, const std::string& request) {
    RequireId(project); RequireId(id);
    auto db = pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db, session.user_id, project, Role::Editor);
    RequireVisible(Read(db, id), project);
    auto record = LockJob(db, id);
    RequireVisible(record, project);
    if (record.current_version != record.job.version_id)
        throw AppError(409, "version_conflict", "file current version has changed");
    if (record.availability != "available")
        throw AppError(409, "content_unavailable", "file content is unavailable");
    if (record.job.state != "pending") {
        if (record.job.state != "failed" && record.job.state != "interrupted" && record.job.state != "cancelled")
            throw AppError(409, "task_conflict", "task cannot be retried in this state");
        if (QueueFull(db)) throw AppError(503, "queue_full", "processing queue is full", true);
        SetState(db, record, "pending");
        db.Execute("UPDATE processing_jobs SET attempt_token=NULL,started_at=NULL WHERE id=?", {SqlValue(id)});
        Audit(db, session, record, "processing.retry", request);
    }
    auto result = Read(db, id, true).job;
    transaction.Commit(); return result;
}

bool ProcessingService::ClaimNext(ProcessingClaim* claim) {
    if (!claim) throw AppError(400, "invalid_request", "claim result is required");
    *claim = ProcessingClaim{};
    auto db = pool_.Acquire();
    const auto candidates = db.Query("SELECT id FROM processing_jobs WHERE state='pending' ORDER BY created_at,id LIMIT 1");
    if (candidates.empty()) return false;
    MySqlTransaction transaction(db);
    auto record = LockJob(db, candidates[0].String(0));
    if (record.job.state != "pending" ||
        !db.Query("SELECT id FROM processing_jobs WHERE state='running' LIMIT 1 FOR UPDATE").empty()) {
        transaction.Commit(); return false;
    }
    if (record.deleted || record.current_version != record.job.version_id || record.availability != "available") {
        SetState(db, record, "cancelled", record.current_version != record.job.version_id ? "version_conflict" : "content_unavailable");
        transaction.Commit(); return false;
    }
    if (record.job.attempt_count == std::numeric_limits<uint32_t>::max())
        throw AppError(409, "attempt_limit", "processing attempt limit reached");
    const std::string token = GenerateId();
    db.Execute("UPDATE processing_jobs SET state='running',attempt_count=attempt_count+1,attempt_token=?,"
               "last_error_code=NULL,last_error_message=NULL,started_at=UTC_TIMESTAMP(6),completed_at=NULL WHERE id=?",
               {SqlValue(token), SqlValue(record.job.id)});
    db.Execute("UPDATE file_versions SET processing_state='processing' WHERE id=?", {SqlValue(record.job.version_id)});
    claim->job = Read(db, record.job.id, true).job;
    claim->attempt_token = token;
    transaction.Commit(); return true;
}

void ProcessingService::Fail(const std::string& id, const std::string& token, const std::string& error) {
    RequireId(id); RequireId(token);
    static const std::unordered_set<std::string> allowed = {
        "invalid_input", "unsupported_format", "input_too_large", "invalid_encoding", "binary_text", "empty_text",
        "text_too_large", "too_many_chunks", "invalid_pdf", "encrypted_pdf", "too_many_pages", "page_stream_too_large",
        "no_extractable_text", "pdf_timeout", "pdf_resource_limit", "pdf_dependency_unavailable", "pdf_worker_failed",
        "content_unavailable", "source_hash_mismatch", "worker_failed"};
    if (!allowed.count(error)) throw AppError(400, "invalid_request", "processing error code is invalid");
    auto db = pool_.Acquire(); MySqlTransaction transaction(db);
    auto record = LockJob(db, id);
    if (record.token != token || (record.job.state != "running" &&
        !(record.job.state == "failed" && record.job.error_code == error)))
        throw AppError(409, "attempt_conflict", "processing attempt is no longer active");
    if (record.job.state == "running") SetState(db, record, "failed", error);
    transaction.Commit();
}

uint64_t ProcessingService::RecoverInterrupted() {
    auto db = pool_.Acquire();
    const auto running = db.Query("SELECT id FROM processing_jobs WHERE state='running' ORDER BY id LIMIT 100");
    uint64_t count = 0;
    for (const auto& row : running) {
        MySqlTransaction transaction(db);
        auto record = LockJob(db, row.String(0));
        if (record.job.state == "running") {
            SetState(db, record, "interrupted", "service_restarted"); ++count;
        }
        transaction.Commit();
    }
    return count;
}
