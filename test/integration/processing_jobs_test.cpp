#include "app/application.h"
#include "auth/auth_service.h"
#include "core/app_error.h"
#include "core/id.h"
#include "core/crypto.h"
#include "core/fault_injector.h"
#include "file/file_service.h"
#include "file/file_store.h"
#include "project/project_service.h"
#include "processing/processing_service.h"
#include "upload/upload_repository.h"
#include "../mysql_test_support.h"
#include "../test_support.h"

#include <nlohmann/json.hpp>
#include <exception>
#include <algorithm>
#include <string>
#include <thread>
#include <sys/stat.h>
#include <unistd.h>

namespace {

struct JobFixture {
    std::string id, file_id, version_id;
};

class Fixture {
public:
    Fixture() : auth(TestDatabase(), 600, 1000), projects(TestDatabase()) {
        char pattern[] = "/tmp/smartdocs-jobs-test.XXXXXX";
        char* path = mkdtemp(pattern);
        CHECK(path != nullptr);
        storage = path;
        CHECK(mkdir((storage + "/objects").c_str(), 0700) == 0);
        CHECK(mkdir((storage + "/staging").c_str(), 0700) == 0);
        auth.CreateUser("admin", "test-only-password");
        admin = auth.Login("admin", "test-only-password");
        project = projects.CreateProject(admin.user.id, "Jobs");
    }
    ~Fixture() {
        rmdir((storage + "/objects").c_str());
        rmdir((storage + "/staging").c_str());
        rmdir(storage.c_str());
    }
    JobFixture AddJob(const std::string& existing_file = "") {
        const JobFixture result{GenerateId(), existing_file.empty() ? GenerateId() : existing_file, GenerateId()};
        MySqlConnection connection = TestDatabase().Acquire();
        MySqlTransaction transaction(connection);
        if (existing_file.empty()) {
            connection.Execute("INSERT INTO files(id,project_id,directory_id,name,created_by) VALUES (?,?,?,?,?)",
                {SqlValue(result.file_id), SqlValue(project.id), SqlValue(project.root_directory_id),
                 SqlValue(result.file_id + ".txt"), SqlValue(admin.user.id)});
        } else {
            connection.Query("SELECT id FROM files WHERE id=? FOR UPDATE", {SqlValue(existing_file)});
        }
        const uint64_t number = UploadRepository().NextVersionNumber(connection, result.file_id);
        connection.Execute("INSERT INTO file_versions(id,file_id,version_number,content_id,size_bytes,sha256,media_type,created_by) VALUES (?,?,?,?,?,?,?,?)",
            {SqlValue(result.version_id), SqlValue(result.file_id), SqlValue(number), SqlValue(GenerateId()),
             SqlValue(uint64_t(3)), SqlValue(std::string(64, 'a')), SqlValue("text/plain"), SqlValue(admin.user.id)});
        connection.Execute("UPDATE files SET current_version_id=? WHERE id=?",
                           {SqlValue(result.version_id), SqlValue(result.file_id)});
        UploadRepository().InsertProcessingJob(connection, result.id, result.version_id);
        transaction.Commit();
        return result;
    }
    RequestHead Head(const std::string& method, const std::string& job_id,
                     const std::string& suffix = "", const std::string& token = "") {
        RequestHead head{};
        head.method = method;
        head.path = "/api/v1/projects/" + project.id + "/processing-jobs/" + job_id + suffix;
        head.version = "1.1";
        head.headers = {{"host", "smartdocs.test"}, {"origin", "http://smartdocs.test"},
                        {"cookie", "smartdocs_session=" + (token.empty() ? admin.raw_token : token)}};
        return head;
    }
    AuthService auth;
    ProjectService projects;
    SessionTokens admin;
    Project project;
    std::string storage;
};

nlohmann::json Body(HttpResponse response) {
    CHECK(response.status() == 200);
    const std::string text = response.head_and_body();
    return nlohmann::json::parse(text.substr(text.find("\r\n\r\n") + 4)).at("data");
}

nlohmann::json TextResult(const JobFixture& job, const std::string& text = "部署文档") {
    using Json = nlohmann::json;
    const uint64_t count = std::count_if(text.begin(), text.end(), [](unsigned char c) { return (c & 0xc0) != 0x80; });
    Json identity = Json::array({"utf8-lines-v1", job.file_id, job.version_id,
                                std::string(64, 'a'), 0, count});
    const auto bytes = identity.dump();
    Json chunk = {{"chunk_id", Sha256Hex(bytes.data(), bytes.size())},
        {"file_id", job.file_id}, {"version_id", job.version_id},
        {"source_sha256", std::string(64, 'a')}, {"parser_revision", "utf8-lines-v1"},
        {"text", text}, {"char_start", 0}, {"char_end", count},
        {"line_start", 1}, {"line_end", 1}};
    return {{"file_id", job.file_id}, {"version_id", job.version_id},
        {"source_sha256", std::string(64, 'a')}, {"parser_revision", "utf8-lines-v1"},
        {"page_count", 0}, {"pages_without_text", Json::array()}, {"chunks", Json::array({chunk})}};
}

nlohmann::json Search(Application& app, Fixture& f, const nlohmann::json& body,
                      const std::string& token = "") {
    auto head = f.Head("POST", "", "", token);
    head.path = "/api/v1/projects/" + f.project.id + "/search";
    const auto bytes = body.dump(); head.content_length = bytes.size();
    head.headers["content-type"] = "application/json";
    auto handler = app.Prepare(head); handler->OnData(bytes.data(), bytes.size());
    return Body(handler->Finish());
}

void PublishText(Fixture& f, const std::string& text = "部署文档") {
    ProcessingService service(TestDatabase(), f.projects); ProcessingClaim claim;
    CHECK(service.ClaimNext(&claim));
    service.Publish(claim.job.id, claim.attempt_token,
        TextResult({claim.job.id, claim.job.file_id, claim.job.version_id}, text));
}

}  // namespace

TEST_CASE(processing_http_status_cancel_retry_are_persistent_and_idempotent) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f;
    Application app(TestAppConfig(f.storage));
    const JobFixture job = f.AddJob();
    auto get = [&]() { return Body(app.Prepare(f.Head("GET", job.id))->Finish()); };
    CHECK(get().at("state") == "pending");
    CHECK(get().at("version_id") == job.version_id);
    CHECK(get().count("attempt_token") == 0);
    for (int i = 0; i < 2; ++i)
        CHECK(Body(app.Prepare(f.Head("POST", job.id, "/cancel"))->Finish()).at("state") == "cancelled");
    for (int i = 0; i < 2; ++i)
        CHECK(Body(app.Prepare(f.Head("POST", job.id, "/retry"))->Finish()).at("state") == "pending");
    CHECK(get().at("attempt_count") == 0);
    MySqlConnection db = TestDatabase().Acquire();
    CHECK(db.ScalarInt("SELECT COUNT(*) FROM processing_jobs") == 1);
    CHECK(db.ScalarInt("SELECT COUNT(*) FROM audit_records WHERE object_type='processing_job'") == 2);
}

TEST_CASE(processing_http_reuses_project_roles_and_same_origin) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f;
    Application app(TestAppConfig(f.storage));
    const auto job = f.AddJob();
    const auto reader = f.auth.CreateUser("reader", "test-only-password");
    f.projects.SetMemberRole(f.admin.session, f.project.id, reader.id, Role::Reader);
    const auto reader_token = f.auth.Login("reader", "test-only-password").raw_token;
    f.auth.CreateUser("outsider", "test-only-password");
    const auto outsider = f.auth.Login("outsider", "test-only-password").raw_token;
    CHECK(Body(app.Prepare(f.Head("GET", job.id, "", reader_token))->Finish()).at("id") == job.id);
    CHECK_THROWS_CODE(app.Prepare(f.Head("POST", job.id, "/cancel", reader_token)), "forbidden");
    CHECK_THROWS_CODE(app.Prepare(f.Head("GET", job.id, "", outsider)), "resource_not_found");
    auto head = f.Head("POST", job.id, "/cancel");
    head.headers["origin"] = "http://evil.test";
    CHECK_THROWS_CODE(app.Prepare(head), "origin_mismatch");
}

TEST_CASE(processing_http_cannot_retry_superseded_or_deleted_versions) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f;
    Application app(TestAppConfig(f.storage));
    auto old = f.AddJob();
    const auto current = f.AddJob(old.file_id);
    app.Prepare(f.Head("POST", old.id, "/cancel"))->Finish();
    CHECK_THROWS_CODE(app.Prepare(f.Head("POST", old.id, "/retry")), "version_conflict");
    { auto db = TestDatabase().Acquire(); db.Execute("UPDATE files SET deleted_at=UTC_TIMESTAMP(6) WHERE id=?", {SqlValue(old.file_id)}); }
    CHECK_THROWS_CODE(app.Prepare(f.Head("GET", current.id)), "resource_not_found");
}

TEST_CASE(processing_queue_capacity_preserves_file_and_reports_busy_failure) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f;
    for (int i = 0; i < 100; ++i) f.AddJob();
    const auto extra = f.AddJob();
    MySqlConnection db = TestDatabase().Acquire();
    CHECK(db.ScalarInt("SELECT COUNT(*) FROM processing_jobs WHERE state='pending'") == 100);
    const auto rows = db.Query("SELECT state,last_error_code FROM processing_jobs WHERE id=?", {SqlValue(extra.id)});
    CHECK(rows[0].String(0) == "failed");
    CHECK(rows[0].String(1) == "queue_full");
    CHECK(db.ScalarInt("SELECT COUNT(*) FROM files WHERE current_version_id=?", {SqlValue(extra.version_id)}) == 1);
    CHECK(db.Query("SELECT processing_state FROM file_versions WHERE id=?", {SqlValue(extra.version_id)})[0].String(0) == "failed");
}

TEST_CASE(processing_attempts_are_fenced_across_restart_retry_and_cancel) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f;
    const auto job = f.AddJob();
    ProcessingService jobs(TestDatabase(), f.projects);
    ProcessingClaim old_claim, current;
    CHECK(jobs.ClaimNext(&old_claim));
    CHECK(old_claim.job.id == job.id && old_claim.job.attempt_count == 1);
    CHECK_THROWS_CODE(jobs.Retry(f.admin.session, f.project.id, job.id), "task_conflict");
    ProcessingService restarted(TestDatabase(), f.projects);
    CHECK(restarted.RecoverInterrupted() == 1);
    CHECK(restarted.RecoverInterrupted() == 0);
    CHECK(restarted.Get(f.admin.session, f.project.id, job.id).state == "interrupted");
    CHECK_THROWS_CODE(jobs.Fail(job.id, old_claim.attempt_token, "invalid_pdf"), "attempt_conflict");
    restarted.Retry(f.admin.session, f.project.id, job.id);
    CHECK(restarted.ClaimNext(&current));
    CHECK(current.job.attempt_count == 2 && current.attempt_token != old_claim.attempt_token);
    CHECK_THROWS_CODE(jobs.Fail(job.id, old_claim.attempt_token, "invalid_pdf"), "attempt_conflict");
    restarted.Fail(job.id, current.attempt_token, "invalid_pdf");
    restarted.Fail(job.id, current.attempt_token, "invalid_pdf");
    CHECK(restarted.Get(f.admin.session, f.project.id, job.id).error_code == "invalid_pdf");
    CHECK_THROWS_CODE(restarted.Fail(job.id, current.attempt_token, "private document text"), "invalid_request");
    restarted.Retry(f.admin.session, f.project.id, job.id);
    CHECK(restarted.ClaimNext(&current));
    restarted.Cancel(f.admin.session, f.project.id, job.id);
    CHECK_THROWS_CODE(restarted.Fail(job.id, current.attempt_token, "invalid_pdf"), "attempt_conflict");
    auto db = TestDatabase().Acquire();
    CHECK(db.Query("SELECT processing_state FROM file_versions WHERE id=?", {SqlValue(job.version_id)})[0].String(0) == "cancelled");
}

TEST_CASE(processing_concurrent_claims_allow_only_one_running_task) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f;
    f.AddJob(); f.AddJob();
    ProcessingService jobs(TestDatabase(), f.projects);
    ProcessingClaim claims[2]; bool accepted[2] = {false, false};
    std::exception_ptr errors[2];
    auto claim = [&](int i) { try { accepted[i] = jobs.ClaimNext(&claims[i]); } catch (...) { errors[i] = std::current_exception(); } };
    std::thread first(claim, 0), second(claim, 1); first.join(); second.join();
    for (const auto& error : errors) if (error) std::rethrow_exception(error);
    CHECK(int(accepted[0]) + int(accepted[1]) == 1);
    ProcessingClaim third;
    CHECK(!jobs.ClaimNext(&third));
    CHECK(third.attempt_token.empty());
    auto db = TestDatabase().Acquire();
    CHECK(db.ScalarInt("SELECT COUNT(*) FROM processing_jobs WHERE state='running'") == 1);
}

TEST_CASE(processing_concurrent_enqueue_cannot_overfill_queue) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f;
    for (int i = 0; i < 99; ++i) f.AddJob();
    std::exception_ptr errors[2];
    auto enqueue = [&](int i) { try { f.AddJob(); } catch (...) { errors[i] = std::current_exception(); } };
    std::thread first(enqueue, 0), second(enqueue, 1); first.join(); second.join();
    for (const auto& error : errors) if (error) std::rethrow_exception(error);
    auto db = TestDatabase().Acquire();
    CHECK(db.ScalarInt("SELECT COUNT(*) FROM processing_jobs WHERE state='pending'") == 100);
    CHECK(db.ScalarInt("SELECT COUNT(*) FROM processing_jobs WHERE state='failed' AND last_error_code='queue_full'") == 1);
    CHECK(db.ScalarInt("SELECT COUNT(*) FROM files") == 101);
}

TEST_CASE(processing_stale_deleted_unavailable_jobs_are_not_claimed) {
    RequireMySqlTests();
    for (const std::string change : {"superseded", "deleted", "unavailable"}) {
        ResetTestDatabase(); Fixture f; const auto job = f.AddJob();
        if (change == "superseded") f.AddJob(job.file_id);
        else {
            auto db = TestDatabase().Acquire();
            if (change == "deleted") db.Execute("UPDATE files SET deleted_at=UTC_TIMESTAMP(6) WHERE id=?", {SqlValue(job.file_id)});
            else db.Execute("UPDATE file_versions SET availability_state='unavailable' WHERE id=?", {SqlValue(job.version_id)});
        }
        ProcessingService jobs(TestDatabase(), f.projects); ProcessingClaim claim;
        CHECK(!jobs.ClaimNext(&claim));
        auto db = TestDatabase().Acquire();
        CHECK(db.Query("SELECT state FROM processing_jobs WHERE id=?", {SqlValue(job.id)})[0].String(0) == "cancelled");
    }
}

TEST_CASE(processing_old_failure_does_not_change_new_version_state) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f;
    const auto old = f.AddJob(); ProcessingService jobs(TestDatabase(), f.projects);
    ProcessingClaim claim; CHECK(jobs.ClaimNext(&claim));
    const auto current = f.AddJob(old.file_id);
    jobs.Fail(old.id, claim.attempt_token, "invalid_pdf");
    auto db = TestDatabase().Acquire();
    CHECK(db.Query("SELECT processing_state FROM file_versions WHERE id=?", {SqlValue(current.version_id)})[0].String(0) == "pending");
    CHECK(jobs.Get(f.admin.session, f.project.id, current.id).state == "pending");
}

TEST_CASE(processing_retry_obeys_capacity_and_completed_tasks_remain_terminal) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f;
    const auto retry = f.AddJob(); ProcessingService jobs(TestDatabase(), f.projects);
    jobs.Cancel(f.admin.session, f.project.id, retry.id);
    for (int i = 0; i < 100; ++i) f.AddJob();
    CHECK_THROWS_CODE(jobs.Retry(f.admin.session, f.project.id, retry.id), "queue_full");
    { auto db = TestDatabase().Acquire(); db.Execute("UPDATE processing_jobs SET state='completed' WHERE id=?", {SqlValue(retry.id)}); }
    CHECK_THROWS_CODE(jobs.Retry(f.admin.session, f.project.id, retry.id), "task_conflict");
    CHECK_THROWS_CODE(jobs.Cancel(f.admin.session, f.project.id, retry.id), "task_conflict");
}

TEST_CASE(processing_document_status_exposes_unparsed_version_without_fake_readiness) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f;
    Application app(TestAppConfig(f.storage));
    const auto job = f.AddJob();
    auto head = f.Head("GET", job.id);
    head.path = "/api/v1/projects/" + f.project.id + "/files/" + job.file_id +
                "/versions/" + job.version_id + "/document";
    const auto data = Body(app.Prepare(head)->Finish());
    CHECK(data.at("parse_state") == "pending");
    CHECK(data.at("keyword_state") == "pending");
    CHECK(data.at("vector_state") == "not_applicable");
    CHECK(data.at("chunk_count") == 0);
}

TEST_CASE(processing_document_missing_chunk_is_not_a_source) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f;
    Application app(TestAppConfig(f.storage));
    const auto job = f.AddJob();
    auto head = f.Head("GET", job.id);
    head.path = "/api/v1/projects/" + f.project.id + "/files/" + job.file_id +
                "/versions/" + job.version_id + "/chunks/" + std::string(64, 'a');
    CHECK_THROWS_CODE(app.Prepare(head), "resource_not_found");
}

TEST_CASE(processing_publication_is_atomic_immutable_and_keyword_indexed) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f; auto job = f.AddJob();
    ProcessingService jobs(TestDatabase(), f.projects); ProcessingClaim claim;
    CHECK(jobs.ClaimNext(&claim)); auto result = TextResult(job);
    jobs.Publish(job.id, claim.attempt_token, result);
    jobs.Publish(job.id, claim.attempt_token, result);
    CHECK(jobs.Get(f.admin.session, f.project.id, job.id).state == "completed");
    auto status = jobs.ReadDocument(f.admin.session, f.project.id, job.file_id, job.version_id);
    CHECK(status.at("parse_state") == "completed");
    CHECK(status.at("keyword_state") == "completed");
    CHECK(status.at("vector_state") == "not_applicable");
    CHECK(status.at("chunk_count") == 1 && status.at("char_count") == 4);
    auto db = TestDatabase().Acquire();
    CHECK(db.ScalarInt("SELECT COUNT(*) FROM document_chunks") == 1);
    CHECK(db.ScalarInt("SELECT COUNT(*) FROM document_chunks WHERE MATCH(body) AGAINST(? IN BOOLEAN MODE)",
                      {SqlValue("部署")}) == 1);
    result["chunks"][0]["text"] = "其他文档";
    CHECK_THROWS_CODE(jobs.Publish(job.id, claim.attempt_token, result), "result_conflict");
    CHECK(db.Query("SELECT body FROM document_chunks")[0].String(0) == "部署文档");
}

TEST_CASE(processing_publication_rejects_stale_cancelled_and_restarted_attempts) {
    RequireMySqlTests();
    for (const std::string change : {"superseded", "cancelled", "restarted", "deleted", "unavailable"}) {
        ResetTestDatabase(); Fixture f; const auto job = f.AddJob();
        ProcessingService jobs(TestDatabase(), f.projects); ProcessingClaim claim;
        CHECK(jobs.ClaimNext(&claim));
        if (change == "superseded") f.AddJob(job.file_id);
        else if (change == "cancelled") jobs.Cancel(f.admin.session, f.project.id, job.id);
        else if (change == "restarted") jobs.RecoverInterrupted();
        else {
            auto db = TestDatabase().Acquire();
            if (change == "deleted") db.Execute("UPDATE files SET deleted_at=UTC_TIMESTAMP(6) WHERE id=?", {SqlValue(job.file_id)});
            else db.Execute("UPDATE file_versions SET availability_state='unavailable' WHERE id=?", {SqlValue(job.version_id)});
        }
        CHECK_THROWS_CODE(jobs.Publish(job.id, claim.attempt_token, TextResult(job)),
            change == "cancelled" || change == "restarted" ? "attempt_conflict" : "version_conflict");
        auto db = TestDatabase().Acquire();
        CHECK(db.ScalarInt("SELECT COUNT(*) FROM document_results") == 0);
        CHECK(db.ScalarInt("SELECT COUNT(*) FROM document_chunks") == 0);
    }
}

TEST_CASE(processing_publication_checks_authoritative_source_hash_and_size) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f; const auto job = f.AddJob();
    ProcessingService jobs(TestDatabase(), f.projects); ProcessingClaim claim;
    CHECK(jobs.ClaimNext(&claim));
    { auto db = TestDatabase().Acquire(); db.Execute("UPDATE file_versions SET sha256=? WHERE id=?",
        {SqlValue(std::string(64, 'b')), SqlValue(job.version_id)}); }
    CHECK_THROWS_CODE(jobs.Publish(job.id, claim.attempt_token, TextResult(job)), "source_hash_mismatch");
    { auto db = TestDatabase().Acquire(); db.Execute("UPDATE file_versions SET sha256=?,size_bytes=8388609 WHERE id=?",
        {SqlValue(std::string(64, 'a')), SqlValue(job.version_id)}); }
    CHECK_THROWS_CODE(jobs.Publish(job.id, claim.attempt_token, TextResult(job)), "input_too_large");
    CHECK(jobs.Get(f.admin.session, f.project.id, job.id).state == "running");
}

TEST_CASE(processing_publication_accepts_new_retry_but_never_the_old_token) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f; const auto job = f.AddJob();
    ProcessingService jobs(TestDatabase(), f.projects); ProcessingClaim old, current;
    CHECK(jobs.ClaimNext(&old)); jobs.RecoverInterrupted();
    jobs.Retry(f.admin.session, f.project.id, job.id); CHECK(jobs.ClaimNext(&current));
    CHECK_THROWS_CODE(jobs.Publish(job.id, old.attempt_token, TextResult(job)), "attempt_conflict");
    jobs.Publish(job.id, current.attempt_token, TextResult(job));
    CHECK(jobs.Get(f.admin.session, f.project.id, job.id).state == "completed");
}

TEST_CASE(processing_publication_fault_rolls_back_chunks_index_and_status) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f; const auto job = f.AddJob();
    ProcessingService jobs(TestDatabase(), f.projects); ProcessingClaim claim;
    CHECK(jobs.ClaimNext(&claim));
    FaultInjector::SetObserverForTesting([](FaultPoint point) {
        if (point == FaultPoint::BeforeDatabaseCommit) throw AppError(500, "injected", "test");
    });
    try { CHECK_THROWS_CODE(jobs.Publish(job.id, claim.attempt_token, TextResult(job)), "injected"); }
    catch (...) { FaultInjector::ClearObserverForTesting(); throw; }
    FaultInjector::ClearObserverForTesting();
    auto db = TestDatabase().Acquire();
    CHECK(db.ScalarInt("SELECT COUNT(*) FROM document_chunks") == 0);
    CHECK(db.ScalarInt("SELECT COUNT(*) FROM document_results") == 0);
    CHECK(db.ScalarInt("SELECT COUNT(*) FROM document_chunks WHERE MATCH(body) AGAINST(? IN BOOLEAN MODE)",
        {SqlValue("部署")}) == 0);
    CHECK(jobs.Get(f.admin.session, f.project.id, job.id).state == "running");
    jobs.Publish(job.id, claim.attempt_token, TextResult(job));
}

TEST_CASE(processing_document_history_never_redirects_and_rechecks_access) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f;
    Application app(TestAppConfig(f.storage));
    const auto old = f.AddJob(); ProcessingService jobs(TestDatabase(), f.projects); ProcessingClaim claim;
    CHECK(jobs.ClaimNext(&claim)); const auto result = TextResult(old);
    jobs.Publish(old.id, claim.attempt_token, result);
    const auto current = f.AddJob(old.file_id);
    const auto chunk_id = result["chunks"][0]["chunk_id"].get<std::string>();
    auto head = f.Head("GET", old.id);
    head.path = "/api/v1/projects/" + f.project.id + "/files/" + old.file_id +
                "/versions/" + old.version_id + "/chunks/" + chunk_id;
    const auto source = Body(app.Prepare(head)->Finish());
    CHECK(source.at("chunk").at("text") == "部署文档");
    CHECK(source.at("version_id") == old.version_id && source.at("is_current") == false);
    CHECK_THROWS_CODE(jobs.ReadDocument(f.admin.session, f.project.id, old.file_id, current.version_id, chunk_id), "resource_not_found");
    const auto other = f.projects.CreateProject(f.admin.user.id, "Other");
    CHECK_THROWS_CODE(jobs.ReadDocument(f.admin.session, other.id, old.file_id, old.version_id, chunk_id), "resource_not_found");
    const auto unrelated = f.AddJob();
    CHECK_THROWS_CODE(jobs.ReadDocument(f.admin.session, f.project.id, unrelated.file_id, old.version_id, chunk_id), "resource_not_found");
    auto reader_user = f.auth.CreateUser("source_reader", "test-only-password");
    f.projects.SetMemberRole(f.admin.session, f.project.id, reader_user.id, Role::Reader);
    auto reader = f.auth.Login("source_reader", "test-only-password");
    CHECK(jobs.ReadDocument(reader.session, f.project.id, old.file_id, old.version_id, chunk_id).at("chunk").at("text") == "部署文档");
    { auto db = TestDatabase().Acquire(); db.Execute("DELETE FROM project_members WHERE project_id=? AND user_id=?",
        {SqlValue(f.project.id), SqlValue(reader_user.id)}); }
    CHECK_THROWS_CODE(jobs.ReadDocument(reader.session, f.project.id, old.file_id, old.version_id, chunk_id), "resource_not_found");
    { auto db = TestDatabase().Acquire(); db.Execute("UPDATE file_versions SET availability_state='unavailable' WHERE id=?", {SqlValue(old.version_id)}); }
    CHECK_THROWS_CODE(app.Prepare(head), "content_unavailable");
    { auto db = TestDatabase().Acquire(); db.Execute("UPDATE files SET deleted_at=UTC_TIMESTAMP(6) WHERE id=?", {SqlValue(old.file_id)}); }
    CHECK_THROWS_CODE(app.Prepare(head), "resource_not_found");
}

TEST_CASE(processing_document_vector_applicability_follows_exact_version_approval) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f; const auto old = f.AddJob();
    FileStore store(f.storage); FileService files(TestDatabase(), f.projects, store);
    ProcessingService jobs(TestDatabase(), f.projects); ProcessingClaim claim;
    CHECK(jobs.ClaimNext(&claim)); jobs.Publish(old.id, claim.attempt_token, TextResult(old));
    files.SetRemoteAiPolicy(f.admin.session, f.project.id, old.file_id, {"remote_ai_allowed", {old.version_id}});
    CHECK(jobs.ReadDocument(f.admin.session, f.project.id, old.file_id, old.version_id).at("vector_state") == "pending");
    const auto current = f.AddJob(old.file_id);
    CHECK(jobs.ReadDocument(f.admin.session, f.project.id, old.file_id, current.version_id).at("vector_state") == "not_applicable");
    files.SetRemoteAiPolicy(f.admin.session, f.project.id, old.file_id, {"internal_only", {}});
    CHECK(jobs.ReadDocument(f.admin.session, f.project.id, old.file_id, old.version_id).at("vector_state") == "not_applicable");
}

namespace {
RequestHead WorkerHead(const std::string& method, const std::string& path, const std::string& attempt = "") {
    RequestHead head{}; head.method = method; head.path = "/internal/v1/processing" + path;
    head.version = "1.1";
    head.headers = {{"host", "127.0.0.1"}, {"authorization", "Bearer " + std::string(64, 'a')}};
    if (!attempt.empty()) head.headers["x-smartdocs-attempt"] = attempt;
    return head;
}

nlohmann::json VectorCall(Application& app, const std::string& method, const std::string& path,
                         const std::string& token = "", const nlohmann::json& body = nullptr) {
    auto head = WorkerHead(method, "/vector" + path, token);
    std::string bytes;
    if (!body.is_null()) { bytes = body.dump(); head.content_length = bytes.size(); head.headers["content-type"] = "application/json"; }
    auto handler = app.Prepare(head);
    if (!bytes.empty()) handler->OnData(bytes.data(), bytes.size());
    return Body(handler->Finish());
}

nlohmann::json VectorResult(const nlohmann::json& batch) {
    using Json = nlohmann::json;
    Json chunks = Json::array();
    for (const auto& chunk : batch.at("chunks")) {
        const auto text = chunk.at("text").get<std::string>();
        const uint64_t length = std::count_if(text.begin(), text.end(), [](unsigned char c) { return (c & 0xc0) != 0x80; });
        Json vector = std::vector<double>(384, 0); vector[0] = 1.0;
        chunks.push_back({{"chunk_id",chunk.at("chunk_id")}, {"text_sha256",Sha256Hex(text.data(),text.size())},
            {"windows",Json::array({{{"char_start",0},{"char_end",length},{"vector",vector}}})}});
    }
    return {{"batch_start",batch.at("batch_start")}, {"result", {
        {"embedding_revision","minilm-634d0f66-fastembed0.9.0-ort1.23.2-halves128-l2-v1"}, {"chunks",chunks}}}};
}

void PublishChunks(Fixture& f, const JobFixture& job, unsigned count) {
    ProcessingService jobs(TestDatabase(), f.projects); ProcessingClaim claim;
    CHECK(jobs.ClaimNext(&claim)); CHECK(claim.job.id == job.id);
    auto result = TextResult(job); auto prototype = result["chunks"][0]; result["chunks"] = nlohmann::json::array();
    for (unsigned i = 0; i < count; ++i) {
        auto chunk = prototype; chunk["char_start"] = i*4; chunk["char_end"] = (i+1)*4;
        const auto identity = nlohmann::json::array({"utf8-lines-v1",job.file_id,job.version_id,std::string(64,'a'),i*4,(i+1)*4}).dump();
        chunk["chunk_id"] = Sha256Hex(identity.data(),identity.size()); result["chunks"].push_back(chunk);
    }
    jobs.Publish(job.id,claim.attempt_token,result);
}
}

TEST_CASE(processing_vectors_require_approval_and_publish_all_batches_idempotently) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f;
    auto config = TestAppConfig(f.storage); config.worker_token = std::string(64,'a'); Application app(config);
    const auto job = f.AddJob();
    FileStore store(f.storage); FileService files(TestDatabase(),f.projects,store); ProcessingService jobs(TestDatabase(),f.projects);
    PublishChunks(f,job,9);
    CHECK(VectorCall(app,"POST","/claim").is_null());
    files.SetRemoteAiPolicy(f.admin.session,f.project.id,job.file_id,{"remote_ai_allowed",{job.version_id}});
    const auto claim = VectorCall(app,"POST","/claim");
    const auto id = claim.at("id").get<std::string>(), token = claim.at("attempt_token").get<std::string>();
    CHECK(id != job.id); CHECK(claim.at("version_id") == job.version_id);
    CHECK(VectorCall(app,"POST","/claim").is_null());
    ProcessingClaim parse; CHECK(!jobs.ClaimNext(&parse));
    const auto first = VectorCall(app,"GET","/"+id+"/chunks",token);
    CHECK(first.at("batch_start") == 0 && first.at("chunks").size() == 8);
    const auto first_result = VectorResult(first);
    CHECK(VectorCall(app,"POST","/"+id+"/result",token,first_result).at("state") == "running");
    CHECK(VectorCall(app,"POST","/"+id+"/result",token,first_result).at("state") == "running");
    auto db = TestDatabase().Acquire();
    CHECK(db.ScalarInt("SELECT COUNT(*) FROM document_embeddings") == 8);
    const auto state = jobs.ReadDocument(f.admin.session,f.project.id,job.file_id,job.version_id);
    CHECK(state.at("parse_state") == "completed" && state.at("keyword_state") == "completed" && state.at("vector_state") == "processing");
    const auto last = VectorCall(app,"GET","/"+id+"/chunks",token);
    CHECK(last.at("batch_start") == 8 && last.at("chunks").size() == 1);
    CHECK(VectorCall(app,"POST","/"+id+"/result",token,VectorResult(last)).at("state") == "completed");
    CHECK(VectorCall(app,"POST","/"+id+"/result",token,first_result).at("state") == "completed");
    CHECK(db.ScalarInt("SELECT COUNT(*) FROM document_embeddings") == 9);
    CHECK(jobs.ReadDocument(f.admin.session,f.project.id,job.file_id,job.version_id).at("vector_state") == "completed");
    auto conflict = first_result; conflict["result"]["chunks"][0]["windows"][0]["vector"][0] = -1.0;
    CHECK_THROWS_CODE(VectorCall(app,"POST","/"+id+"/result",token,conflict),"result_conflict");
}

TEST_CASE(processing_vectors_revoke_reapprove_and_retry_fence_old_attempts) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f;
    auto config = TestAppConfig(f.storage); config.worker_token = std::string(64,'a'); Application app(config);
    const auto job = f.AddJob();
    FileStore store(f.storage); FileService files(TestDatabase(),f.projects,store); ProcessingService jobs(TestDatabase(),f.projects);
    files.SetRemoteAiPolicy(f.admin.session,f.project.id,job.file_id,{"remote_ai_allowed",{job.version_id}});
    CHECK(VectorCall(app,"POST","/claim").is_null());
    PublishChunks(f,job,9);
    const auto claim = VectorCall(app,"POST","/claim");
    const auto id = claim.at("id").get<std::string>(), old = claim.at("attempt_token").get<std::string>();
    auto result = VectorResult(VectorCall(app,"GET","/"+id+"/chunks",old));
    VectorCall(app,"POST","/"+id+"/result",old,result);
    files.SetRemoteAiPolicy(f.admin.session,f.project.id,job.file_id,{"internal_only",{}});
    CHECK_THROWS_CODE(VectorCall(app,"GET","/"+id+"/chunks",old),"attempt_conflict");
    CHECK_THROWS_CODE(VectorCall(app,"POST","/"+id+"/result",old,result),"attempt_conflict");
    CHECK_THROWS_CODE(jobs.Retry(f.admin.session,f.project.id,id),"vector_not_allowed");
    files.SetRemoteAiPolicy(f.admin.session,f.project.id,job.file_id,{"remote_ai_allowed",{job.version_id}});
    const auto current = VectorCall(app,"POST","/claim"); CHECK(current.at("id") == id);
    const auto token = current.at("attempt_token").get<std::string>(); CHECK(token != old);
    CHECK(VectorCall(app,"GET","/"+id+"/chunks",token).at("batch_start") == 0);
    CHECK_THROWS_CODE(VectorCall(app,"POST","/"+id+"/result",old,result),"attempt_conflict");
    CHECK(jobs.RecoverInterrupted() == 0); // Parser recovery must not interrupt encoder.
    CHECK(VectorCall(app,"POST","/recover").at("interrupted_count") == 1);
    CHECK(jobs.Get(f.admin.session,f.project.id,id).state == "interrupted");
    jobs.Retry(f.admin.session,f.project.id,id);
    const auto retried = VectorCall(app,"POST","/claim"); CHECK(retried.at("attempt_token") != token);
    CHECK_THROWS_CODE(VectorCall(app,"GET","/"+id+"/chunks",token),"attempt_conflict");
    CHECK(jobs.ReadDocument(f.admin.session,f.project.id,job.file_id,job.version_id).at("parse_state") == "completed");
}

TEST_CASE(processing_vectors_reject_malformed_or_out_of_order_results_and_rollback_faults) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f;
    auto config = TestAppConfig(f.storage); config.worker_token = std::string(64,'a'); Application app(config);
    const auto job = f.AddJob();
    FileStore store(f.storage); FileService files(TestDatabase(),f.projects,store);
    PublishChunks(f,job,1); files.SetRemoteAiPolicy(f.admin.session,f.project.id,job.file_id,{"remote_ai_allowed",{job.version_id}});
    const auto claim = VectorCall(app,"POST","/claim");
    const auto id = claim.at("id").get<std::string>(), token = claim.at("attempt_token").get<std::string>();
    auto result = VectorResult(VectorCall(app,"GET","/"+id+"/chunks",token));
    auto invalid = result; invalid["batch_start"] = 1;
    CHECK_THROWS_CODE(VectorCall(app,"POST","/"+id+"/result",token,invalid),"batch_conflict");
    for (int mutation=0; mutation<8; ++mutation) {
        invalid = result; auto& c = invalid["result"]["chunks"][0]; auto& w = c["windows"][0];
        if (mutation==0) c["text_sha256"] = std::string(64,'0');
        if (mutation==1) c["chunk_id"] = std::string(64,'0');
        if (mutation==2) w["char_start"] = 1;
        if (mutation==3) w["char_end"] = 3;
        if (mutation==4) w["vector"][0] = 0;
        if (mutation==5) w["vector"][0] = true;
        if (mutation==6) invalid["result"]["embedding_revision"] = "wrong";
        if (mutation==7) w["vector"] = nlohmann::json::array({1.0});
        CHECK_THROWS_CODE(VectorCall(app,"POST","/"+id+"/result",token,invalid),"invalid_result");
    }
    FaultInjector::SetObserverForTesting([](FaultPoint point) {
        if (point == FaultPoint::BeforeDatabaseCommit) throw AppError(500,"injected","test");
    });
    try { CHECK_THROWS_CODE(VectorCall(app,"POST","/"+id+"/result",token,result),"injected"); }
    catch (...) { FaultInjector::ClearObserverForTesting(); throw; }
    FaultInjector::ClearObserverForTesting();
    auto db = TestDatabase().Acquire();
    CHECK(db.ScalarInt("SELECT COUNT(*) FROM document_embeddings") == 0);
    CHECK(VectorCall(app,"GET","/"+id+"/chunks",token).at("batch_start") == 0);
    CHECK(VectorCall(app,"POST","/"+id+"/result",token,result).at("state") == "completed");
}

TEST_CASE(processing_vectors_routes_and_job_controls_reuse_authorization) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f;
    Application disabled(TestAppConfig(f.storage));
    CHECK_THROWS_CODE(VectorCall(disabled,"POST","/claim"),"route_not_found");
    auto config = TestAppConfig(f.storage); config.worker_token = std::string(64,'a'); Application app(config);
    const auto job = f.AddJob();
    for (const std::string key : {"authorization","cookie","origin"}) {
        auto head = WorkerHead("POST","/vector/claim");
        if (key == "authorization") head.headers.erase(key); else head.headers[key] = "untrusted";
        CHECK_THROWS_CODE(app.Prepare(head),"worker_unauthorized");
    }
    FileStore store(f.storage); FileService files(TestDatabase(),f.projects,store); ProcessingService jobs(TestDatabase(),f.projects);
    PublishChunks(f,job,1); files.SetRemoteAiPolicy(f.admin.session,f.project.id,job.file_id,{"remote_ai_allowed",{job.version_id}});
    const auto claim = VectorCall(app,"POST","/claim"); const auto id = claim.at("id").get<std::string>();
    const auto token = claim.at("attempt_token").get<std::string>();
    const auto reader_user = f.auth.CreateUser("vector-reader","test-only-password");
    f.projects.SetMemberRole(f.admin.session,f.project.id,reader_user.id,Role::Reader);
    const auto reader = f.auth.Login("vector-reader","test-only-password");
    CHECK(jobs.Get(reader.session,f.project.id,id).state == "running");
    CHECK_THROWS_CODE(jobs.Cancel(reader.session,f.project.id,id),"forbidden");
    { auto db = TestDatabase().Acquire(); db.Execute("DELETE FROM project_members WHERE project_id=? AND user_id=?",
        {SqlValue(f.project.id),SqlValue(reader_user.id)}); }
    CHECK_THROWS_CODE(jobs.Get(reader.session,f.project.id,id),"resource_not_found");
    jobs.Cancel(f.admin.session,f.project.id,id);
    CHECK_THROWS_CODE(VectorCall(app,"GET","/"+id+"/chunks",token),"attempt_conflict");
    jobs.Retry(f.admin.session,f.project.id,id);
    const auto again = VectorCall(app,"POST","/claim").at("attempt_token").get<std::string>();
    jobs.Fail(id,again,"embedding_timeout"); jobs.Fail(id,again,"embedding_timeout");
    CHECK(jobs.Get(f.admin.session,f.project.id,id).error_code == "embedding_timeout");
    const auto state = jobs.ReadDocument(f.admin.session,f.project.id,job.file_id,job.version_id);
    CHECK(state.at("parse_state") == "completed" && state.at("keyword_state") == "completed" && state.at("vector_state") == "failed");
}

TEST_CASE(processing_vectors_cannot_read_or_publish_superseded_deleted_or_unavailable_source) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f;
    auto config = TestAppConfig(f.storage); config.worker_token = std::string(64,'a'); Application app(config);
    FileStore store(f.storage); FileService files(TestDatabase(),f.projects,store); ProcessingService jobs(TestDatabase(),f.projects);
    for (int mutation=0; mutation<3; ++mutation) {
        const auto job = f.AddJob(); PublishChunks(f,job,1);
        files.SetRemoteAiPolicy(f.admin.session,f.project.id,job.file_id,{"remote_ai_allowed",{job.version_id}});
        const auto claim = VectorCall(app,"POST","/claim");
        const auto id = claim.at("id").get<std::string>(), token = claim.at("attempt_token").get<std::string>();
        auto result = VectorResult(VectorCall(app,"GET","/"+id+"/chunks",token));
        auto db = TestDatabase().Acquire();
        if (mutation==0) {
            const auto next = f.AddJob(job.file_id);
            CHECK(jobs.ReadDocument(f.admin.session,f.project.id,job.file_id,next.version_id).at("vector_state") == "not_applicable");
            jobs.Cancel(f.admin.session,f.project.id,next.id);
        }
        if (mutation==1) db.Execute("UPDATE files SET deleted_at=UTC_TIMESTAMP(6) WHERE id=?",{SqlValue(job.file_id)});
        if (mutation==2) db.Execute("UPDATE file_versions SET availability_state='unavailable' WHERE id=?",{SqlValue(job.version_id)});
        CHECK_THROWS_CODE(VectorCall(app,"GET","/"+id+"/chunks",token),"version_conflict");
        CHECK_THROWS_CODE(VectorCall(app,"POST","/"+id+"/result",token,result),"version_conflict");
        jobs.Fail(id,token,"version_conflict");
    }
}

TEST_CASE(processing_vectors_share_queue_capacity_and_bound_version_window_count) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f;
    auto config = TestAppConfig(f.storage); config.worker_token = std::string(64,'a'); Application app(config);
    const auto job = f.AddJob(); PublishChunks(f,job,1);
    std::vector<JobFixture> pending;
    for (unsigned i=0;i<100;++i) pending.push_back(f.AddJob());
    FileStore store(f.storage); FileService files(TestDatabase(),f.projects,store); ProcessingService jobs(TestDatabase(),f.projects);
    files.SetRemoteAiPolicy(f.admin.session,f.project.id,job.file_id,{"remote_ai_allowed",{job.version_id}});
    const auto state = jobs.ReadDocument(f.admin.session,f.project.id,job.file_id,job.version_id);
    const auto id=state.at("vector_job_id").get<std::string>();
    CHECK(state.at("vector_state")=="failed" && state.at("parse_state")=="completed");
    CHECK(jobs.Get(f.admin.session,f.project.id,id).error_code=="queue_full");
    CHECK_THROWS_CODE(jobs.Retry(f.admin.session,f.project.id,id),"queue_full");
    jobs.Cancel(f.admin.session,f.project.id,pending[0].id);
    jobs.Retry(f.admin.session,f.project.id,id);
    const auto claim=VectorCall(app,"POST","/claim");
    const auto token=claim.at("attempt_token").get<std::string>();
    const auto result=VectorResult(VectorCall(app,"GET","/"+id+"/chunks",token));
    auto db=TestDatabase().Acquire();
    // Simulate the persisted cumulative budget immediately before another batch.
    db.Execute("UPDATE processing_jobs SET vector_window_count=4096 WHERE id=?",{SqlValue(id)});
    CHECK_THROWS_CODE(VectorCall(app,"POST","/"+id+"/result",token,result),"embedding_window_limit");
    CHECK(db.ScalarInt("SELECT COUNT(*) FROM document_embeddings")==0);
    CHECK(jobs.Get(f.admin.session,f.project.id,id).state=="running");
    jobs.Fail(id,token,"embedding_window_limit");
}

TEST_CASE(processing_worker_routes_are_disabled_by_default_and_require_service_auth) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f;
    Application disabled(TestAppConfig(f.storage));
    CHECK_THROWS_CODE(disabled.Prepare(WorkerHead("POST", "/claim")), "route_not_found");
    auto config = TestAppConfig(f.storage); config.worker_token = std::string(64, 'a');
    Application app(config);
    auto head = WorkerHead("POST", "/claim"); head.headers.erase("authorization");
    CHECK_THROWS_CODE(app.Prepare(head), "worker_unauthorized");
    head.headers["authorization"] = "Bearer " + std::string(64, 'b');
    CHECK_THROWS_CODE(app.Prepare(head), "worker_unauthorized");
    for (const std::string key : {"origin", "cookie"}) {
        head = WorkerHead("POST", "/claim"); head.headers[key] = "browser";
        CHECK_THROWS_CODE(app.Prepare(head), "worker_unauthorized");
    }
    CHECK(Body(app.Prepare(WorkerHead("POST", "/claim"))->Finish()).is_null());
}

TEST_CASE(processing_worker_claim_exposes_only_bounded_metadata_and_fences_source) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f;
    auto config = TestAppConfig(f.storage); config.worker_token = std::string(64, 'a');
    Application app(config); const auto job = f.AddJob();
    const auto claim = Body(app.Prepare(WorkerHead("POST", "/claim"))->Finish());
    CHECK(claim.at("id") == job.id && claim.at("version_id") == job.version_id);
    CHECK(claim.at("source_size") == 3 && claim.at("kind") == "txt");
    CHECK(claim.at("source_sha256") == std::string(64, 'a'));
    CHECK(claim.size() == 7);
    CHECK_THROWS_CODE(app.Prepare(WorkerHead("GET", "/" + job.id + "/content", std::string(32, 'b'))), "attempt_conflict");
    const auto token = claim.at("attempt_token").get<std::string>();
    ProcessingService jobs(TestDatabase(), f.projects);
    jobs.Cancel(f.admin.session, f.project.id, job.id);
    CHECK_THROWS_CODE(app.Prepare(WorkerHead("GET", "/" + job.id + "/content", token)), "attempt_conflict");
}

TEST_CASE(processing_worker_result_body_is_bounded_before_buffering) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f;
    auto config = TestAppConfig(f.storage); config.worker_token = std::string(64, 'a');
    Application app(config); auto head = WorkerHead("POST", "/" + std::string(32, 'b') + "/result", std::string(32, 'c'));
    head.headers["content-type"] = "application/json"; head.content_length = 16*1024*1024+1;
    CHECK_THROWS_CODE(app.Prepare(head), "body_too_large");
    head.headers["authorization"] = "Bearer wrong";
    CHECK_THROWS_CODE(app.Prepare(head), "worker_unauthorized");
}

TEST_CASE(processing_search_metadata_discovers_unparsed_and_literal_names_with_pagination) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f; Application app(TestAppConfig(f.storage));
    auto a = f.AddJob(), b = f.AddJob();
    { auto db = TestDatabase().Acquire(); db.Execute("UPDATE files SET name='100%_guide.txt' WHERE id=?", {SqlValue(a.file_id)}); }
    auto found = Search(app, f, {{"mode","metadata"},{"query","%_"}});
    CHECK(found.at("items").size() == 1);
    CHECK(found.at("items")[0].at("file_id") == a.file_id);
    CHECK(found.at("items")[0].at("parse_state") == "pending");
    CHECK(!found.at("items")[0].contains("chunk"));
    CHECK(found.at("coverage").at("keyword_ready_files") == 0);
    auto page1 = Search(app, f, {{"mode","metadata"},{"query",""},{"page_size",1}});
    auto page2 = Search(app, f, {{"mode","metadata"},{"query",""},{"page_size",1},{"page",2}});
    CHECK(page1.at("items").size() == 1 && page1.at("has_more") == true);
    CHECK(page1.at("next_page") == 2);
    CHECK(page2.at("items").size() == 1 && page2.at("has_more") == false);
    CHECK(page2.at("next_page").is_null());
    CHECK(page1.at("items")[0].at("file_id") != page2.at("items")[0].at("file_id"));
    CHECK(Search(app, f, {{"mode","metadata"},{"query","%' OR 1=1 --"}}).at("items").empty());
}

TEST_CASE(processing_search_keyword_returns_exact_sources_and_explicit_limits) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f; Application app(TestAppConfig(f.storage));
    auto a = f.AddJob(); PublishText(f, "部署文档 orbit"); f.AddJob();
    for (const std::string query : {"部署", "orbit"}) {
        const auto found = Search(app, f, {{"mode","keyword"},{"query",query}});
        CHECK(found.at("retrieval") == "keyword_only");
        CHECK(found.at("items").size() == 1);
        const auto& hit = found.at("items")[0];
        CHECK(hit.at("version_id") == a.version_id);
        CHECK(hit.at("chunk").at("text") == "部署文档 orbit");
        CHECK(hit.at("chunk").at("char_end") == 10);
        CHECK(hit.at("coverage").at("page_count") == 0);
        CHECK(found.at("coverage").at("current_files") == 2);
        CHECK(found.at("coverage").at("keyword_ready_files") == 1);
        CHECK(!found.at("warnings").empty());
        ProcessingService jobs(TestDatabase(), f.projects);
        CHECK(jobs.ReadDocument(f.admin.session, f.project.id, a.file_id, a.version_id,
            hit.at("chunk").at("chunk_id").get<std::string>()).at("chunk") == hit.at("chunk"));
    }
    // Isolated mysqld uses its default bigram parser and English stopword list.
    for (const std::string query : {"部", "+", "x", "or"})
        CHECK(Search(app, f, {{"mode","keyword"},{"query",query}}).at("items").empty());
}

TEST_CASE(processing_search_enforces_roles_input_limits_and_same_origin) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f; Application app(TestAppConfig(f.storage));
    f.AddJob();
    auto reader = f.auth.CreateUser("search_reader", "test-only-password");
    f.projects.SetMemberRole(f.admin.session, f.project.id, reader.id, Role::Reader);
    const auto token = f.auth.Login("search_reader", "test-only-password").raw_token;
    CHECK(Search(app, f, {{"mode","metadata"},{"query",""}}, token).at("items").size() == 1);
    { auto db = TestDatabase().Acquire(); db.Execute("DELETE FROM project_members WHERE user_id=?", {SqlValue(reader.id)}); }
    CHECK_THROWS_CODE(Search(app, f, {{"mode","metadata"},{"query",""}}, token), "resource_not_found");
    auto head = f.Head("POST", ""); head.path = "/api/v1/projects/" + f.project.id + "/search";
    head.headers["origin"] = "http://evil.test";
    CHECK_THROWS_CODE(app.Prepare(head), "origin_mismatch");
    head.headers["origin"] = "http://smartdocs.test";
    head.headers["content-type"] = "application/json"; head.content_length = 4097;
    CHECK_THROWS_CODE(app.Prepare(head), "body_too_large");
    for (const auto& invalid : std::vector<nlohmann::json>{
        {{"mode","hybrid"},{"query","x"}}, {{"mode","keyword"},{"query"," "}},
        {{"mode","keyword"},{"query",std::string(257,'a')}}, {{"mode","metadata"},{"query",""},{"page",0}},
        {{"mode","metadata"},{"query",""},{"page",true}}, {{"mode","metadata"},{"query",""},{"page_size",21}},
        {{"mode","metadata"},{"query",""},{"page",-1}}, {{"mode","metadata"},{"query",""},{"page",1001}},
        {{"mode","metadata"},{"query",""},{"page_size",0}}, {{"mode","metadata"},{"query",""},{"page",1.5}},
        {{"mode","metadata"},{"query",std::string("bad\0query",9)}}, {{"mode","metadata"},{"query",""},{"directory_id","../"}},
        {{"mode","metadata"},{"query",""},{"media_type","bad"}},
        {{"mode","metadata"},{"query",""},{"user_id",f.admin.user.id}}})
        CHECK_THROWS_CODE(Search(app, f, invalid), "invalid_request");
}

TEST_CASE(processing_search_filters_current_deleted_unavailable_and_restored_versions) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f; Application app(TestAppConfig(f.storage));
    auto old = f.AddJob(); PublishText(f);
    auto search = [&]() { return Search(app, f, {{"mode","keyword"},{"query","部署"}}); };
    CHECK(search().at("items").size() == 1);
    { auto db = TestDatabase().Acquire(); db.Execute("UPDATE file_versions SET availability_state='unavailable' WHERE id=?", {SqlValue(old.version_id)}); }
    CHECK(search().at("items").empty());
    CHECK(Search(app, f, {{"mode","metadata"},{"query",""}}).at("items")[0].at("availability_state") == "unavailable");
    { auto db = TestDatabase().Acquire(); db.Execute("UPDATE file_versions SET availability_state='available' WHERE id=?", {SqlValue(old.version_id)});
      db.Execute("UPDATE files SET deleted_at=UTC_TIMESTAMP(6) WHERE id=?", {SqlValue(old.file_id)}); }
    CHECK(search().at("items").empty());
    CHECK(Search(app, f, {{"mode","metadata"},{"query",""}}).at("items").empty());
    { auto db = TestDatabase().Acquire(); db.Execute("UPDATE files SET deleted_at=NULL WHERE id=?", {SqlValue(old.file_id)}); }
    CHECK(search().at("items").size() == 1);
    const auto current = f.AddJob(old.file_id);
    CHECK(search().at("items").empty());
    CHECK(Search(app, f, {{"mode","metadata"},{"query",""}}).at("items")[0].at("version_id") == current.version_id);
    PublishText(f, "部署新版");
    CHECK(search().at("items")[0].at("version_id") == current.version_id);
    CHECK(search().at("items")[0].at("chunk").at("text") == "部署新版");
}

TEST_CASE(processing_search_limits_project_directory_type_and_keyword_pages) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f; Application app(TestAppConfig(f.storage));
    const auto first = f.AddJob(); PublishText(f);
    const auto second = f.AddJob(); PublishText(f);
    auto page1 = Search(app, f, {{"mode","keyword"},{"query","部署"},{"page_size",1}});
    auto page2 = Search(app, f, {{"mode","keyword"},{"query","部署"},{"page_size",1},{"page",2}});
    CHECK(page1.at("has_more") == true && page2.at("has_more") == false);
    CHECK(page1.at("items")[0].at("file_id") != page2.at("items")[0].at("file_id"));
    const auto directory = f.projects.CreateDirectory(f.admin.session, f.project.id, f.project.root_directory_id, "Sub");
    { auto db = TestDatabase().Acquire(); db.Execute("UPDATE files SET directory_id=? WHERE id=?", {SqlValue(directory.id), SqlValue(first.file_id)}); }
    const auto filtered = Search(app, f, {{"mode","keyword"},{"query","部署"},{"directory_id",directory.id},{"media_type","TEXT/PLAIN"}});
    CHECK(filtered.at("items").size() == 1 && filtered.at("items")[0].at("file_id") == first.file_id);
    CHECK(filtered.at("coverage").at("current_files") == 1);
    CHECK(Search(app, f, {{"mode","keyword"},{"query","部署"},{"media_type","application/pdf"}}).at("items").empty());
    const auto project = f.project;
    f.project = f.projects.CreateProject(f.admin.user.id, "Secret other project");
    const auto foreign = f.AddJob(); PublishText(f);
    { auto db = TestDatabase().Acquire(); db.Execute("UPDATE files SET name='private-foreign-guide.txt' WHERE id=?", {SqlValue(foreign.file_id)}); }
    const auto foreign_dir = f.project.root_directory_id; f.project = project;
    CHECK(Search(app, f, {{"mode","keyword"},{"query","部署"}}).at("items").size() == 2);
    CHECK(Search(app, f, {{"mode","metadata"},{"query","private-foreign-guide"}}).at("items").empty());
    CHECK_THROWS_CODE(Search(app, f, {{"mode","metadata"},{"query",""},{"directory_id",foreign_dir}}), "resource_not_found");
}

TEST_CASE(processing_search_revalidates_candidates_after_concurrent_state_changes) {
    RequireMySqlTests(); ResetTestDatabase(); Fixture f; Application app(TestAppConfig(f.storage));
    const auto job = f.AddJob(); PublishText(f);
    const auto directory = f.projects.CreateDirectory(f.admin.session, f.project.id, f.project.root_directory_id, "Moved");
    for (const std::string change : {"deleted", "unavailable", "moved", "renamed", "superseded"}) {
        { auto db = TestDatabase().Acquire();
          db.Execute("UPDATE files SET deleted_at=NULL,directory_id=?,name='find-me.txt' WHERE id=?",
              {SqlValue(f.project.root_directory_id), SqlValue(job.file_id)});
          db.Execute("UPDATE file_versions SET availability_state='available' WHERE id=?", {SqlValue(job.version_id)}); }
        bool observed = false;
        FaultInjector::SetObserverForTesting([&](FaultPoint point) {
            if (point != FaultPoint::AfterSearchCandidates) return;
            observed = true;
            if (change == "superseded") { f.AddJob(job.file_id); return; }
            auto db = TestDatabase().Acquire();
            if (change == "deleted") db.Execute("UPDATE files SET deleted_at=UTC_TIMESTAMP(6) WHERE id=?", {SqlValue(job.file_id)});
            if (change == "unavailable") db.Execute("UPDATE file_versions SET availability_state='unavailable' WHERE id=?", {SqlValue(job.version_id)});
            if (change == "moved") db.Execute("UPDATE files SET directory_id=? WHERE id=?", {SqlValue(directory.id),SqlValue(job.file_id)});
            if (change == "renamed") db.Execute("UPDATE files SET name='no-match.txt' WHERE id=?", {SqlValue(job.file_id)});
        });
        nlohmann::json result;
        try { result = Search(app, f, {{"mode",change == "renamed" ? "metadata" : "keyword"},
            {"query",change == "renamed" ? "find-me" : "部署"},{"directory_id",f.project.root_directory_id}}); }
        catch (...) { FaultInjector::ClearObserverForTesting(); throw; }
        FaultInjector::ClearObserverForTesting();
        CHECK(observed); CHECK(result.at("items").empty());
        CHECK(result.at("truncated") == true);
        CHECK(std::find(result.at("warnings").begin(), result.at("warnings").end(), "scope_changed") != result.at("warnings").end());
    }
}
