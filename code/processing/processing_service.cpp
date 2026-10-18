#include "processing_service.h"
#include "document_result.h"
#include "vector_result.h"

#include "core/app_error.h"
#include "core/id.h"
#include "core/crypto.h"
#include "core/fault_injector.h"
#include "db/mysql.h"
#include "project/project_service.h"
#include "file/file_service.h"
#include "file/file_store.h"
#include <algorithm>
#include <cctype>
#include <limits>
#include <sys/stat.h>
#include <unordered_set>

namespace {

void RequireId(const std::string& id) {
    if (id.size() != 32 || id.find_first_not_of("0123456789abcdef") != std::string::npos)
        throw AppError(400, "invalid_request", "entity ID is invalid");
}

struct Source {
    std::string hash, kind, content_id;
    uint64_t size;
};
Source ReadSource(MySqlConnection& db, const std::string& version) {
    const auto row = db.Query("SELECT v.sha256,v.size_bytes,v.content_id,v.media_type,f.name "
        "FROM file_versions v JOIN files f ON f.id=v.file_id WHERE v.id=? FOR UPDATE", {SqlValue(version)})[0];
    std::string kind, mime = row.String(3), name = row.String(4);
    if (mime == "application/pdf") kind = "pdf";
    else if (mime == "text/plain") kind = "txt";
    else if (mime == "text/markdown") kind = "md";
    else if (mime == "application/octet-stream") {
        const auto dot = name.find_last_of('.');
        std::string extension = dot == std::string::npos ? "" : name.substr(dot);
        std::transform(extension.begin(), extension.end(), extension.begin(), [](unsigned char c) { return std::tolower(c); });
        if (extension == ".pdf") kind = "pdf";
        else if (extension == ".txt") kind = "txt";
        else if (extension == ".md" || extension == ".markdown") kind = "md";
    }
    return {row.String(0), kind, row.String(2), row.UInt64(1)};
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
    bool deleted, approved;
    std::string parse_state;
};

Record Read(MySqlConnection& db, const std::string& id, bool lock = false) {
    const auto rows = db.Query(
        "SELECT j.id,v.file_id,v.id,f.project_id,j.state,j.attempt_count,"
        "COALESCE(j.last_error_code,''),COALESCE(j.attempt_token,''),"
        "COALESCE(f.current_version_id,''),v.availability_state,f.deleted_at IS NOT NULL,j.task_type,"
        "v.remote_ai_approved AND f.remote_ai_policy='remote_ai_allowed',v.parse_state "
        "FROM processing_jobs j JOIN file_versions v ON v.id=j.file_version_id "
        "JOIN files f ON f.id=v.file_id WHERE j.id=?" + std::string(lock ? " FOR UPDATE" : ""),
        {SqlValue(id)});
    if (rows.empty()) throw AppError(404, "resource_not_found", "resource was not found");
    const auto& r = rows[0];
    return {{r.String(0), r.String(1), r.String(2), r.String(3), r.String(4), r.UInt64(5), r.String(6),r.String(11)},
            r.String(7), r.String(8), r.String(9), r.Bool(10),r.Bool(12),r.String(13)};
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
    if (record.job.task_type == "vectorize")
        db.Execute("UPDATE file_versions SET vector_state=? WHERE id=?",
                   {SqlValue(state == "running" ? "processing" : state),SqlValue(record.job.version_id)});
    else db.Execute("UPDATE file_versions SET processing_state=?,parse_state=? WHERE id=?",
                    {SqlValue(state), SqlValue(state), SqlValue(record.job.version_id)});
}

void RequireTaskType(const std::string& type) {
    if (type!="parse_and_index" && type!="vectorize")
        throw AppError(400,"invalid_request","processing task type is invalid");
}

void RequireVector(const Record& record, const std::string& token, bool replay = false) {
    if (record.job.task_type!="vectorize" || record.token!=token ||
        (record.job.state!="running" && !(replay && record.job.state=="completed")))
        throw AppError(409,"attempt_conflict","vector attempt is no longer active");
    if (record.deleted || record.current_version!=record.job.version_id || record.availability!="available")
        throw AppError(409,"version_conflict","vector source is no longer current and available");
    if (!record.approved || record.parse_state!="completed")
        throw AppError(409,"vector_not_allowed","vector processing is not allowed");
}

void EnqueueVector(MySqlConnection& db, const std::string& version) {
    LockQueue(db);
    const auto existing = db.Query("SELECT id FROM processing_jobs WHERE file_version_id=? AND task_type='vectorize' FOR UPDATE",{SqlValue(version)});
    if (!existing.empty()) return;
    const bool full = QueueFull(db);
    db.Execute("INSERT INTO processing_jobs(id,file_version_id,task_type,state,last_error_code,completed_at) "
        "VALUES (?,?,'vectorize',?,?,CASE WHEN ? THEN UTC_TIMESTAMP(6) ELSE NULL END)",
        {SqlValue(GenerateId()),SqlValue(version),SqlValue(full ? "failed" : "pending"),
         full ? SqlValue("queue_full") : SqlValue::Null(),SqlValue(full)});
    db.Execute("UPDATE file_versions SET vector_state=? WHERE id=?",{SqlValue(full ? "failed" : "pending"),SqlValue(version)});
}

nlohmann::json VectorChunks(MySqlConnection& db, const std::string& version, uint64_t start) {
    nlohmann::json chunks = nlohmann::json::array();
    for (const auto& row : db.Query("SELECT id,body FROM document_chunks WHERE version_id=? AND ordinal>=? ORDER BY ordinal LIMIT 8 FOR SHARE",
                                    {SqlValue(version),SqlValue(start)}))
        chunks.push_back({{"chunk_id",row.String(0)},{"text",row.String(1)}});
    return chunks;
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

void RefreshVectorJobs(MySqlConnection& db, const std::string& file) {
    LockQueue(db);
    // Every explicit policy revision invalidates old attempts, even revoke/reapprove.
    db.Execute("UPDATE processing_jobs j JOIN file_versions v ON v.id=j.file_version_id "
        "SET j.state='cancelled',j.attempt_token=NULL,j.last_error_code='approval_changed',j.completed_at=UTC_TIMESTAMP(6) "
        "WHERE v.file_id=? AND j.task_type='vectorize'",{SqlValue(file)});
    const auto ready = db.Query("SELECT v.id FROM file_versions v JOIN files f ON f.current_version_id=v.id "
        "JOIN document_results d ON d.version_id=v.id WHERE f.id=? AND f.deleted_at IS NULL "
        "AND v.availability_state='available' AND v.parse_state='completed' "
        "AND f.remote_ai_policy='remote_ai_allowed' AND v.remote_ai_approved FOR UPDATE",{SqlValue(file)});
    if (ready.empty()) return;
    const auto version = ready[0].String(0);
    const auto existing = db.Query("SELECT id FROM processing_jobs WHERE file_version_id=? AND task_type='vectorize' FOR UPDATE",{SqlValue(version)});
    if (existing.empty()) { EnqueueVector(db,version); return; }
    const bool full = QueueFull(db);
    db.Execute("UPDATE processing_jobs SET state=?,last_error_code=?,started_at=NULL,"
        "completed_at=CASE WHEN ? THEN UTC_TIMESTAMP(6) ELSE NULL END WHERE id=?",
        {SqlValue(full ? "failed" : "pending"),full ? SqlValue("queue_full") : SqlValue::Null(),SqlValue(full),SqlValue(existing[0].String(0))});
    db.Execute("UPDATE file_versions SET vector_state=? WHERE id=?",{SqlValue(full ? "failed" : "pending"),SqlValue(version)});
}

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
    if (record.job.task_type=="vectorize" && (!record.approved || record.parse_state!="completed"))
        throw AppError(409,"vector_not_allowed","vector processing is not allowed");
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

bool ProcessingService::ClaimNext(ProcessingClaim* claim, const std::string& task_type) {
    RequireTaskType(task_type);
    if (!claim) throw AppError(400, "invalid_request", "claim result is required");
    *claim = ProcessingClaim{};
    auto db = pool_.Acquire();
    const auto candidates = db.Query("SELECT id FROM processing_jobs WHERE state='pending' AND task_type=? ORDER BY created_at,id LIMIT 1",{SqlValue(task_type)});
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
    if (task_type=="vectorize" && (!record.approved || record.parse_state!="completed")) {
        SetState(db,record,"cancelled","vector_not_allowed"); transaction.Commit(); return false;
    }
    if (record.job.attempt_count == std::numeric_limits<uint32_t>::max())
        throw AppError(409, "attempt_limit", "processing attempt limit reached");
    const std::string token = GenerateId();
    db.Execute("UPDATE processing_jobs SET state='running',attempt_count=attempt_count+1,attempt_token=?,"
               "last_error_code=NULL,last_error_message=NULL,started_at=UTC_TIMESTAMP(6),completed_at=NULL WHERE id=?",
               {SqlValue(token), SqlValue(record.job.id)});
    if (task_type=="vectorize") {
        db.Execute("DELETE FROM document_embeddings WHERE job_id=?",{SqlValue(record.job.id)});
        db.Execute("DELETE FROM embedding_batches WHERE job_id=?",{SqlValue(record.job.id)});
        db.Execute("UPDATE processing_jobs SET vector_next_chunk=0,vector_window_count=0 WHERE id=?",{SqlValue(record.job.id)});
        db.Execute("UPDATE file_versions SET vector_state='processing' WHERE id=?",{SqlValue(record.job.version_id)});
    } else db.Execute("UPDATE file_versions SET processing_state='processing',parse_state='processing' WHERE id=?", {SqlValue(record.job.version_id)});
    claim->job = Read(db, record.job.id, true).job;
    claim->attempt_token = token;
    if (task_type=="parse_and_index") {
        const auto source = ReadSource(db, record.job.version_id);
        claim->source_sha256 = source.hash; claim->source_size = source.size; claim->kind = source.kind;
    }
    transaction.Commit(); return true;
}

void ProcessingService::Fail(const std::string& id, const std::string& token, const std::string& error) {
    RequireId(id); RequireId(token);
    static const std::unordered_set<std::string> allowed = {
        "invalid_input", "unsupported_format", "input_too_large", "invalid_encoding", "binary_text", "empty_text",
        "text_too_large", "too_many_chunks", "invalid_pdf", "encrypted_pdf", "too_many_pages", "page_stream_too_large",
        "no_extractable_text", "pdf_timeout", "pdf_resource_limit", "pdf_dependency_unavailable", "pdf_worker_failed",
        "content_unavailable", "source_hash_mismatch", "version_conflict", "worker_failed",
        "embedding_invalid_input", "embedding_window_limit", "embedding_model_unavailable", "embedding_dependency_unavailable",
        "embedding_resource_limit", "embedding_invalid_result", "embedding_worker_failed", "embedding_timeout",
        "embedding_result_too_large", "embedding_busy", "embedding_lock_unavailable", "embedding_must_be_unprivileged"};
    if (!allowed.count(error)) throw AppError(400, "invalid_request", "processing error code is invalid");
    auto db = pool_.Acquire(); MySqlTransaction transaction(db);
    auto record = LockJob(db, id);
    if (record.token != token || (record.job.state != "running" &&
        !(record.job.state == "failed" && record.job.error_code == error)))
        throw AppError(409, "attempt_conflict", "processing attempt is no longer active");
    if (record.job.state == "running") SetState(db, record, "failed", error);
    transaction.Commit();
}

uint64_t ProcessingService::RecoverInterrupted(const std::string& task_type) {
    RequireTaskType(task_type);
    auto db = pool_.Acquire();
    const auto running = db.Query("SELECT id FROM processing_jobs WHERE state='running' AND task_type=? ORDER BY id LIMIT 100",{SqlValue(task_type)});
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

AuthorizedVersion ProcessingService::OpenInput(const std::string& id, const std::string& token,
                                               FileStore& store) {
    RequireId(id); RequireId(token);
    auto db = pool_.Acquire(); MySqlTransaction transaction(db);
    const auto record = LockJob(db, id);
    if (record.job.task_type!="parse_and_index" || record.token != token || record.job.state != "running")
        throw AppError(409, "attempt_conflict", "processing attempt is no longer active");
    if (record.deleted || record.current_version != record.job.version_id || record.availability != "available")
        throw AppError(409, "version_conflict", "document version is no longer readable by this attempt");
    const auto source = ReadSource(db, record.job.version_id);
    if (source.size > 8 * 1024 * 1024) throw AppError(413, "input_too_large", "source exceeds parsing limit");
    if (source.kind.empty()) throw AppError(422, "unsupported_format", "source format is not supported");
    FileSummary file{}; file.id = record.job.file_id; file.project_id = record.job.project_id;
    FileVersionSummary version{}; version.id = record.job.version_id; version.file_id = file.id;
    version.size = source.size; version.sha256 = source.hash;
    AuthorizedVersion input(std::move(file), std::move(version), store.OpenObject(source.content_id));
    struct stat info{};
    if (fstat(input.fd, &info) != 0 || info.st_size < 0 || static_cast<uint64_t>(info.st_size) != source.size)
        throw AppError(503, "content_unavailable", "source size does not match the immutable version", true);
    transaction.Commit(); return input;
}

void ProcessingService::Publish(const std::string& id, const std::string& token,
                                const nlohmann::json& result) {
    RequireId(id); RequireId(token);
    const auto document = ValidateDocumentResult(result);
    auto db = pool_.Acquire(); MySqlTransaction transaction(db);
    const auto record = LockJob(db, id);
    if (record.job.task_type!="parse_and_index" || record.token != token || (record.job.state != "running" && record.job.state != "completed"))
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
    if (version.Bool(2)) EnqueueVector(db,document.version_id);
    FaultInjector::Hit(FaultPoint::BeforeDatabaseCommit);
    transaction.Commit();
}

nlohmann::json ProcessingService::ReadVectorBatch(const std::string& id, const std::string& token) {
    RequireId(id); RequireId(token);
    auto db = pool_.Acquire(); MySqlTransaction transaction(db);
    const auto record = LockJob(db,id); RequireVector(record,token);
    const auto progress = db.Query("SELECT j.vector_next_chunk,d.chunk_count FROM processing_jobs j "
        "JOIN document_results d ON d.version_id=j.file_version_id WHERE j.id=? FOR UPDATE",{SqlValue(id)});
    if (progress.empty()) throw AppError(409,"result_conflict","parsed result is unavailable");
    const auto start = progress[0].UInt64(0), count = progress[0].UInt64(1);
    auto chunks = VectorChunks(db,record.job.version_id,start);
    if (start>=count || chunks.size()!=std::min<uint64_t>(8,count-start))
        throw AppError(409,"result_conflict","parsed result is incomplete");
    nlohmann::json result = {{"batch_start",start},{"chunk_count",count},
        {"embedding_revision",kEmbeddingRevision},{"chunks",std::move(chunks)}};
    transaction.Commit(); return result;
}

std::string ProcessingService::PublishVectorBatch(const std::string& id, const std::string& token,
    uint64_t start, const nlohmann::json& result) {
    RequireId(id); RequireId(token);
    if (start>=2000) throw AppError(400,"invalid_request","vector batch offset is invalid");
    std::string canonical;
    try { canonical=result.dump(); }
    catch (const nlohmann::json::exception&) { throw AppError(400,"invalid_result","vector result is invalid"); }
    if (canonical.size()>2*1024*1024) throw AppError(413,"result_too_large","vector result is too large");
    const auto digest = Sha256Hex(canonical.data(),canonical.size());
    auto db = pool_.Acquire(); MySqlTransaction transaction(db);
    const auto record = LockJob(db,id); RequireVector(record,token,true);
    const auto existing = db.Query("SELECT result_sha256 FROM embedding_batches WHERE job_id=? AND batch_start=? FOR UPDATE",
                                  {SqlValue(id),SqlValue(start)});
    if (!existing.empty()) {
        if (existing[0].String(0)!=digest) throw AppError(409,"result_conflict","vector batch is immutable within this attempt");
        transaction.Commit(); return record.job.state;
    }
    const auto progress = db.Query("SELECT j.vector_next_chunk,j.vector_window_count,d.chunk_count FROM processing_jobs j "
        "JOIN document_results d ON d.version_id=j.file_version_id WHERE j.id=? FOR UPDATE",{SqlValue(id)});
    if (progress.empty() || record.job.state!="running" || start!=progress[0].UInt64(0) || start>=progress[0].UInt64(2))
        throw AppError(409,"batch_conflict","vector batch is not the next pending batch");
    const auto count = progress[0].UInt64(2);
    const auto chunks = VectorChunks(db,record.job.version_id,start);
    if (chunks.size()!=std::min<uint64_t>(8,count-start))
        throw AppError(409,"result_conflict","parsed result is incomplete");
    const auto windows = ValidateVectorResult(result,chunks);
    if (progress[0].UInt64(1)+windows>4096)
        throw AppError(413,"embedding_window_limit","document exceeds vector window limit");
    for (const auto& chunk : result.at("chunks")) {
        uint64_t ordinal=0;
        for (const auto& window : chunk.at("windows"))
            db.Execute("INSERT INTO document_embeddings(chunk_id,window_ordinal,job_id,embedding_revision,char_start,char_end,vector) "
                "VALUES (?,?,?,?,?,?,?)",{SqlValue(chunk.at("chunk_id").get<std::string>()),SqlValue(ordinal++),SqlValue(id),
                SqlValue(kEmbeddingRevision),SqlValue(window.at("char_start").get<uint64_t>()),
                SqlValue(window.at("char_end").get<uint64_t>()),SqlValue(window.at("vector").dump())});
    }
    db.Execute("INSERT INTO embedding_batches(job_id,batch_start,result_sha256) VALUES (?,?,?)",{SqlValue(id),SqlValue(start),SqlValue(digest)});
    db.Execute("UPDATE processing_jobs SET vector_next_chunk=?,vector_window_count=vector_window_count+? WHERE id=?",
               {SqlValue(start+chunks.size()),SqlValue(windows),SqlValue(id)});
    const std::string state = start+chunks.size()==count ? "completed" : "running";
    if (state=="completed") SetState(db,record,state);
    FaultInjector::Hit(FaultPoint::BeforeDatabaseCommit);
    transaction.Commit(); return state;
}

nlohmann::json ProcessingService::ReadDocument(const SessionContext& session, const std::string& project,
    const std::string& file, const std::string& version, const std::string& chunk_id, bool remote_ai) {
    using Json = nlohmann::json;
    RequireId(project); RequireId(file); RequireId(version);
    if (!chunk_id.empty() && (chunk_id.size() != 64 || chunk_id.find_first_not_of("0123456789abcdef") != std::string::npos))
        throw AppError(400, "invalid_request", "chunk ID is invalid");
    auto db = pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db, session.user_id, project, Role::Reader);
    const auto files = db.Query("SELECT current_version_id,remote_ai_policy FROM files WHERE id=? AND project_id=? AND deleted_at IS NULL FOR SHARE",
                               {SqlValue(file), SqlValue(project)});
    if (files.empty()) throw AppError(404, "resource_not_found", "resource was not found");
    const auto versions = db.Query("SELECT parse_state,keyword_state,vector_state,sha256,availability_state,remote_ai_approved FROM file_versions WHERE id=? AND file_id=? FOR SHARE",
                                  {SqlValue(version), SqlValue(file)});
    if (versions.empty()) throw AppError(404, "resource_not_found", "resource was not found");
    const auto& v = versions[0];
    if (remote_ai && (files[0].String(1) != "remote_ai_allowed" || v.UInt64(5) != 1))
        throw AppError(404, "resource_not_found", "resource was not found");
    if (v.String(4) != "available") throw AppError(409, "content_unavailable", "file content is unavailable");
    Json data = {{"file_id", file}, {"version_id", version}, {"is_current", files[0].String(0) == version},
        {"parse_state", v.String(0)}, {"keyword_state", v.String(1)}, {"vector_state", v.String(2)},
        {"source_sha256", v.String(3)}, {"parser_revision", ""}, {"page_count", 0},
        {"pages_without_text", Json::array()}, {"chunk_count", 0}, {"char_count", 0}};
    data["parse_job_id"] = nullptr;
    data["vector_job_id"] = nullptr;
    const auto jobs = db.Query("SELECT task_type,id FROM processing_jobs WHERE file_version_id=? FOR SHARE", {SqlValue(version)});
    for (const auto& job : jobs) {
        data[job.String(0) == "vectorize" ? "vector_job_id" : "parse_job_id"] = job.String(1);
    }
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
