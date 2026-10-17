#include "app/application.h"
#include "auth/auth_service.h"
#include "core/app_error.h"
#include "core/id.h"
#include "project/project_service.h"
#include "processing/processing_service.h"
#include "upload/upload_repository.h"
#include "../mysql_test_support.h"
#include "../test_support.h"

#include <nlohmann/json.hpp>
#include <exception>
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
