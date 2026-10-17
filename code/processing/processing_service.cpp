#include "processing_service.h"
#include "document_result.h"

#include "core/app_error.h"
#include "core/id.h"
#include "core/fault_injector.h"
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
    db.Execute("UPDATE file_versions SET processing_state=?,parse_state=? WHERE id=?",
               {SqlValue(state), SqlValue(state), SqlValue(record.job.version_id)});
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
    if (full) db.Execute("UPDATE file_versions SET processing_state='failed',parse_state='failed' WHERE id=?", {SqlValue(version_id)});
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
    db.Execute("UPDATE file_versions SET processing_state='processing',parse_state='processing' WHERE id=?", {SqlValue(record.job.version_id)});
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

void ProcessingService::Publish(const std::string& id, const std::string& token,
                                const nlohmann::json& result) {
    RequireId(id); RequireId(token);
    const auto document = ValidateDocumentResult(result);
    auto db = pool_.Acquire(); MySqlTransaction transaction(db);
    const auto record = LockJob(db, id);
    if (record.token != token || (record.job.state != "running" && record.job.state != "completed"))
        throw AppError(409, "attempt_conflict", "processing attempt is no longer active");
    if (record.deleted || record.current_version != record.job.version_id || record.availability != "available" ||
        document.file_id != record.job.file_id || document.version_id != record.job.version_id)
        throw AppError(409, "version_conflict", "document version is no longer publishable");
    const auto version = db.Query("SELECT v.sha256,v.size_bytes,v.remote_ai_approved AND f.remote_ai_policy='remote_ai_allowed' "
        "FROM file_versions v JOIN files f ON f.id=v.file_id WHERE v.id=? FOR UPDATE", {SqlValue(record.job.version_id)})[0];
    if (version.String(0) != document.source_sha256)
        throw AppError(409, "source_hash_mismatch", "document source hash does not match");
    if (version.UInt64(1) > 8 * 1024 * 1024)
        throw AppError(413, "input_too_large", "source exceeds parsing limit");
    const auto existing = db.Query("SELECT result_sha256 FROM document_results WHERE version_id=? FOR UPDATE", {SqlValue(document.version_id)});
    if (!existing.empty()) {
        if (record.job.state != "completed" || existing[0].String(0) != document.fingerprint)
            throw AppError(409, "result_conflict", "published result is immutable");
        transaction.Commit(); return;
    }
    if (record.job.state != "running") throw AppError(409, "result_conflict", "completed result is unavailable");
    db.Execute("INSERT INTO document_results(version_id,source_sha256,parser_revision,result_sha256,page_count,pages_without_text,chunk_count,char_count) "
        "VALUES (?,?,?,?,?,?,?,?)", {SqlValue(document.version_id), SqlValue(document.source_sha256),
        SqlValue(document.parser_revision), SqlValue(document.fingerprint), SqlValue(result.at("page_count").get<uint64_t>()),
        SqlValue(result.at("pages_without_text").dump()), SqlValue(uint64_t(result.at("chunks").size())), SqlValue(document.char_count)});
    uint64_t ordinal = 0;
    for (const auto& chunk : result.at("chunks")) {
        db.Execute("INSERT INTO document_chunks(id,version_id,ordinal,page,char_start,char_end,line_start,line_end,body) VALUES (?,?,?,?,?,?,?,?,?)",
            {SqlValue(chunk.at("chunk_id").get<std::string>()), SqlValue(document.version_id), SqlValue(ordinal++),
             chunk.contains("page") ? SqlValue(chunk.at("page").get<uint64_t>()) : SqlValue::Null(),
             SqlValue(chunk.at("char_start").get<uint64_t>()), SqlValue(chunk.at("char_end").get<uint64_t>()),
             SqlValue(chunk.at("line_start").get<uint64_t>()), SqlValue(chunk.at("line_end").get<uint64_t>()),
             SqlValue(chunk.at("text").get<std::string>())});
    }
    SetState(db, record, "completed");
    db.Execute("UPDATE file_versions SET keyword_state='completed',vector_state=? WHERE id=?",
               {SqlValue(version.Bool(2) ? "pending" : "not_applicable"), SqlValue(document.version_id)});
    FaultInjector::Hit(FaultPoint::BeforeDatabaseCommit);
    transaction.Commit();
}

nlohmann::json ProcessingService::ReadDocument(const SessionContext& session, const std::string& project,
    const std::string& file, const std::string& version, const std::string& chunk_id) {
    using Json = nlohmann::json;
    RequireId(project); RequireId(file); RequireId(version);
    if (!chunk_id.empty() && (chunk_id.size() != 64 || chunk_id.find_first_not_of("0123456789abcdef") != std::string::npos))
        throw AppError(400, "invalid_request", "chunk ID is invalid");
    auto db = pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db, session.user_id, project, Role::Reader);
    const auto files = db.Query("SELECT current_version_id FROM files WHERE id=? AND project_id=? AND deleted_at IS NULL FOR SHARE",
                               {SqlValue(file), SqlValue(project)});
    if (files.empty()) throw AppError(404, "resource_not_found", "resource was not found");
    const auto versions = db.Query("SELECT parse_state,keyword_state,vector_state,sha256,availability_state FROM file_versions WHERE id=? AND file_id=? FOR SHARE",
                                  {SqlValue(version), SqlValue(file)});
    if (versions.empty()) throw AppError(404, "resource_not_found", "resource was not found");
    const auto& v = versions[0];
    if (v.String(4) != "available") throw AppError(409, "content_unavailable", "file content is unavailable");
    Json data = {{"file_id", file}, {"version_id", version}, {"is_current", files[0].String(0) == version},
        {"parse_state", v.String(0)}, {"keyword_state", v.String(1)}, {"vector_state", v.String(2)},
        {"source_sha256", v.String(3)}, {"parser_revision", ""}, {"page_count", 0},
        {"pages_without_text", Json::array()}, {"chunk_count", 0}, {"char_count", 0}};
    const auto results = db.Query("SELECT parser_revision,page_count,pages_without_text,chunk_count,char_count FROM document_results WHERE version_id=? FOR SHARE",
                                  {SqlValue(version)});
    if (!results.empty()) {
        const auto& r = results[0];
        data["parser_revision"] = r.String(0); data["page_count"] = r.UInt64(1);
        data["pages_without_text"] = Json::parse(r.String(2));
        data["chunk_count"] = r.UInt64(3); data["char_count"] = r.UInt64(4);
    }
    if (!chunk_id.empty()) {
        const auto chunks = db.Query("SELECT page,char_start,char_end,line_start,line_end,body FROM document_chunks WHERE id=? AND version_id=? FOR SHARE",
                                    {SqlValue(chunk_id), SqlValue(version)});
        if (results.empty() || chunks.empty() || v.String(0) != "completed")
            throw AppError(404, "resource_not_found", "resource was not found");
        const auto& c = chunks[0];
        data["chunk"] = {{"chunk_id", chunk_id}, {"file_id", file}, {"version_id", version},
            {"source_sha256", data["source_sha256"]}, {"parser_revision", data["parser_revision"]},
            {"char_start", c.UInt64(1)}, {"char_end", c.UInt64(2)}, {"line_start", c.UInt64(3)},
            {"line_end", c.UInt64(4)}, {"text", c.String(5)}};
        if (!c.IsNull(0)) data["chunk"]["page"] = c.UInt64(0);
    }
    transaction.Commit(); return data;
}
