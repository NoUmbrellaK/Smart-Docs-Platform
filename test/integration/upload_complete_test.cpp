#include "app/application.h"
#include "auth/auth_service.h"
#include "core/app_error.h"
#include "core/crypto.h"
#include "file/file_store.h"
#include "project/project_service.h"
#include "upload/upload_service.h"
#include "../mysql_test_support.h"
#include "../test_support.h"

#include <cerrno>
#include <cstring>
#include <dirent.h>
#include <mutex>
#include <nlohmann/json.hpp>
#include <string>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>
#include <vector>

namespace {

const char* kPassword = "correct horse battery";
const char* kRequest = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

void RemoveTree(const std::string& path) {
    struct stat entry{};
    if (lstat(path.c_str(), &entry) != 0) return;
    if (!S_ISDIR(entry.st_mode) || S_ISLNK(entry.st_mode)) {
        unlink(path.c_str());
        return;
    }
    DIR* directory = opendir(path.c_str());
    CHECK(directory != nullptr);
    while (dirent* child = readdir(directory)) {
        if (std::strcmp(child->d_name, ".") == 0 ||
            std::strcmp(child->d_name, "..") == 0) {
            continue;
        }
        RemoveTree(path + "/" + child->d_name);
    }
    CHECK(closedir(directory) == 0);
    CHECK(rmdir(path.c_str()) == 0);
}

class TemporaryStorage {
public:
    TemporaryStorage() {
        char pattern[] = "/tmp/smart-docs-upload-complete.XXXXXX";
        char* created = mkdtemp(pattern);
        CHECK(created != nullptr);
        path_ = created;
        CHECK(mkdir((path_ + "/objects").c_str(), 0700) == 0);
        CHECK(mkdir((path_ + "/staging").c_str(), 0700) == 0);
    }
    ~TemporaryStorage() { RemoveTree(path_); }
    const std::string& path() const { return path_; }

private:
    std::string path_;
};

CreateUploadCommand NewFile(const Project& project, const std::string& name,
                            const std::string& bytes) {
    CreateUploadCommand command{};
    command.mode = UploadMode::CreateFile;
    command.directory_id = project.root_directory_id;
    command.name = name;
    command.size = bytes.size();
    command.sha256 = Sha256Hex(bytes.data(), bytes.size());
    command.media_type = "text/plain";
    return command;
}

void PutParts(UploadService& uploads, const SessionContext& session,
              const Project& project,
              const UploadTask& task, const std::string& bytes) {
    for (uint32_t number = 0; number < task.part_count; ++number) {
        const size_t offset = static_cast<size_t>(number * task.chunk_size);
        const size_t size = static_cast<size_t>(ExpectedUploadPartSize(
            bytes.size(), task.chunk_size, number));
        const std::string part = bytes.substr(offset, size);
        std::unique_ptr<PartWriter> writer = uploads.BeginPart(
            session, project.id, task.id, number, size,
            Sha256Hex(part.data(), part.size()));
        writer->Write(part.data(), part.size());
        uploads.ConfirmPart(session, project.id, task.id, writer->Finish(),
                            kRequest);
    }
}

RequestHead Head(const std::string& method, const std::string& path,
                 const std::string& token, uint64_t length = 0) {
    RequestHead head{};
    head.method = method;
    head.path = path;
    head.version = "1.1";
    head.content_length = length;
    head.headers.emplace("host", "smartdocs.test");
    head.headers.emplace("origin", "http://smartdocs.test");
    head.headers.emplace("cookie", "smartdocs_session=" + token);
    return head;
}

nlohmann::json ResponseJson(const HttpResponse& response) {
    const std::string& serialized = response.head_and_body();
    const size_t body = serialized.find("\r\n\r\n");
    CHECK(body != std::string::npos);
    return nlohmann::json::parse(serialized.substr(body + 4));
}

HttpResponse SendJson(Application& application, const std::string& path,
                      const std::string& token, const nlohmann::json& body) {
    const std::string serialized = body.dump();
    RequestHead head = Head("POST", path, token, serialized.size());
    head.headers.emplace("content-type", "application/json");
    std::unique_ptr<RequestBodyHandler> handler = application.Prepare(head);
    handler->OnData(serialized.data(), serialized.size());
    return handler->Finish();
}

HttpResponse SendPart(Application& application, const std::string& path,
                      const std::string& token, const std::string& bytes) {
    RequestHead head = Head("PUT", path, token, bytes.size());
    head.headers.emplace("content-type", "application/octet-stream");
    head.headers.emplace("x-chunk-sha256",
                         Sha256Hex(bytes.data(), bytes.size()));
    std::unique_ptr<RequestBodyHandler> handler = application.Prepare(head);
    handler->OnData(bytes.data(), bytes.size());
    return handler->Finish();
}

}  // namespace

TEST_CASE(upload_complete_validates_parts_and_whole_file_before_publication) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    FileStore store(storage.path());
    UploadService uploads(TestDatabase(), projects, store, 1024, 4);
    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const SessionContext session =
        auth.Login("admin", kPassword, kRequest).session;
    const Project project = projects.CreateProject(admin.id, "Alpha", kRequest);

    UploadTask missing = uploads.Create(
        session, project.id, NewFile(project, "missing.txt", "abcdefgh"),
        kRequest);
    std::unique_ptr<PartWriter> second = uploads.BeginPart(
        session, project.id, missing.id, 1, 4, Sha256Hex("efgh", 4));
    second->Write("efgh", 4);
    uploads.ConfirmPart(session, project.id, missing.id, second->Finish(),
                        kRequest);
    CHECK_THROWS_CODE(uploads.Complete(session, project.id, missing.id, kRequest),
                      "upload_parts_incomplete");

    const UploadTask wrong_order = uploads.Create(
        session, project.id, NewFile(project, "order.txt", "abcdefgh"),
        kRequest);
    const UploadTask wrong_size = uploads.Create(
        session, project.id, NewFile(project, "size.txt", "abcdefgh"),
        kRequest);
    {
        MySqlConnection connection = TestDatabase().Acquire();
        connection.Execute(
            "INSERT INTO upload_parts(upload_task_id, part_number, size_bytes, "
            "sha256, staging_name) VALUES (?, 0, 4, ?, '0'), "
            "(?, 2, 4, ?, '2')",
            {SqlValue(wrong_order.id), SqlValue(Sha256Hex("abcd", 4)),
             SqlValue(wrong_order.id), SqlValue(Sha256Hex("efgh", 4))});
        connection.Execute(
            "INSERT INTO upload_parts(upload_task_id, part_number, size_bytes, "
            "sha256, staging_name) VALUES (?, 0, 3, ?, '0'), "
            "(?, 1, 5, ?, '1')",
            {SqlValue(wrong_size.id), SqlValue(Sha256Hex("abc", 3)),
             SqlValue(wrong_size.id), SqlValue(Sha256Hex("defgh", 5))});
    }
    CHECK_THROWS_CODE(
        uploads.Complete(session, project.id, wrong_order.id, kRequest),
        "upload_parts_invalid");
    CHECK_THROWS_CODE(
        uploads.Complete(session, project.id, wrong_size.id, kRequest),
        "upload_parts_invalid");

    CreateUploadCommand mismatch = NewFile(project, "mismatch.txt", "wxyz");
    UploadTask wrong_hash = uploads.Create(session, project.id, mismatch, kRequest);
    std::unique_ptr<PartWriter> writer = uploads.BeginPart(
        session, project.id, wrong_hash.id, 0, 4, Sha256Hex("abcd", 4));
    writer->Write("abcd", 4);
    uploads.ConfirmPart(session, project.id, wrong_hash.id, writer->Finish(),
                        kRequest);
    CHECK_THROWS_CODE(
        uploads.Complete(session, project.id, wrong_hash.id, kRequest),
        "whole_file_mismatch");
    CHECK(!store.ObjectExists(wrong_hash.id));
    CHECK(uploads.GetOwn(session, project.id, wrong_hash.id).task.state ==
          "failed");
}

TEST_CASE(upload_complete_is_idempotent_and_supports_zero_byte_objects) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    FileStore store(storage.path());
    UploadService uploads(TestDatabase(), projects, store, 1024, 4);
    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const SessionContext session =
        auth.Login("admin", kPassword, kRequest).session;
    const Project project = projects.CreateProject(admin.id, "Alpha", kRequest);

    const UploadTask task = uploads.Create(
        session, project.id, NewFile(project, "empty.txt", ""), kRequest);
    const CompleteUploadResult first =
        uploads.Complete(session, project.id, task.id, kRequest);
    const CompleteUploadResult retry =
        uploads.Complete(session, project.id, task.id, kRequest);
    CHECK(!first.reused);
    CHECK(retry.reused);
    CHECK(first.file_id == retry.file_id);
    CHECK(first.version_id == retry.version_id);
    CHECK(first.processing_job_id == retry.processing_job_id);
    CHECK(store.ObjectExists(task.id));

    MySqlConnection connection = TestDatabase().Acquire();
    CHECK(connection.ScalarInt("SELECT COUNT(*) FROM files") == 1);
    CHECK(connection.ScalarInt("SELECT COUNT(*) FROM file_versions WHERE "
                               "size_bytes=0 AND remote_ai_approved=FALSE AND "
                               "remote_ai_approved_at IS NULL AND "
                               "remote_ai_approved_by IS NULL") == 1);
    CHECK(connection.ScalarInt("SELECT COUNT(*) FROM processing_jobs WHERE "
                               "state='pending' AND attempt_count=0") == 1);
}

TEST_CASE(upload_complete_concurrent_calls_return_one_committed_graph) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    FileStore store(storage.path());
    UploadService uploads(TestDatabase(), projects, store, 1024, 4);
    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const SessionContext session =
        auth.Login("admin", kPassword, kRequest).session;
    const Project project = projects.CreateProject(admin.id, "Alpha", kRequest);
    const UploadTask task = uploads.Create(
        session, project.id, NewFile(project, "race.txt", "race"), kRequest);
    PutParts(uploads, session, project, task, "race");

    std::mutex mutex;
    std::vector<CompleteUploadResult> results;
    std::vector<std::string> errors;
    auto complete = [&]() {
        try {
            CompleteUploadResult result =
                uploads.Complete(session, project.id, task.id, kRequest);
            std::lock_guard<std::mutex> lock(mutex);
            results.push_back(result);
        } catch (const AppError& error) {
            std::lock_guard<std::mutex> lock(mutex);
            errors.push_back(error.code);
        }
    };
    std::thread first(complete);
    std::thread second(complete);
    first.join();
    second.join();
    CHECK(errors.empty());
    CHECK(results.size() == 2);
    CHECK(results[0].file_id == results[1].file_id);
    CHECK(results[0].version_id == results[1].version_id);
    CHECK(results[0].processing_job_id == results[1].processing_job_id);
    CHECK(results[0].reused != results[1].reused);
}

TEST_CASE(upload_complete_rechecks_name_version_and_transaction_state) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    FileStore store(storage.path());
    UploadService uploads(TestDatabase(), projects, store, 1024, 4);
    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const SessionContext session =
        auth.Login("admin", kPassword, kRequest).session;
    const Project project = projects.CreateProject(admin.id, "Alpha", kRequest);

    const UploadTask conflict = uploads.Create(
        session, project.id, NewFile(project, "taken.txt", "data"), kRequest);
    PutParts(uploads, session, project, conflict, "data");
    {
        MySqlConnection connection = TestDatabase().Acquire();
        connection.Execute(
            "INSERT INTO files(id, project_id, directory_id, name, created_by) "
            "VALUES ('11111111111111111111111111111111', ?, ?, 'taken.txt', ?)",
            {SqlValue(project.id), SqlValue(project.root_directory_id),
             SqlValue(admin.id)});
    }
    CHECK_THROWS_CODE(
        uploads.Complete(session, project.id, conflict.id, kRequest),
        "name_conflict");

    const UploadTask base = uploads.Create(
        session, project.id, NewFile(project, "versions.txt", "one!"), kRequest);
    PutParts(uploads, session, project, base, "one!");
    const CompleteUploadResult base_result =
        uploads.Complete(session, project.id, base.id, kRequest);
    CreateUploadCommand next{};
    next.mode = UploadMode::CreateVersion;
    next.file_id = base_result.file_id;
    next.observed_current_version_id = base_result.version_id;
    next.size = 4;
    next.sha256 = Sha256Hex("late", 4);
    next.media_type = "text/plain";
    const UploadTask stale = uploads.Create(session, project.id, next, kRequest);
    PutParts(uploads, session, project, stale, "late");
    {
        MySqlConnection connection = TestDatabase().Acquire();
        connection.Execute(
            "INSERT INTO file_versions(id, file_id, version_number, content_id, "
            "size_bytes, sha256, media_type, created_by) VALUES "
            "('22222222222222222222222222222222', ?, 2, "
            "'33333333333333333333333333333333', 1, ?, 'text/plain', ?)",
            {SqlValue(base_result.file_id), SqlValue(Sha256Hex("x", 1)),
             SqlValue(admin.id)});
        connection.Execute(
            "UPDATE files SET current_version_id="
            "'22222222222222222222222222222222' WHERE id=?",
            {SqlValue(base_result.file_id)});
    }
    CHECK_THROWS_CODE(uploads.Complete(session, project.id, stale.id, kRequest),
                      "version_conflict");

    const UploadTask rollback = uploads.Create(
        session, project.id, NewFile(project, "rollback.txt", "undo"), kRequest);
    PutParts(uploads, session, project, rollback, "undo");
    CHECK_THROWS_CODE(
        uploads.Complete(session, project.id, rollback.id, "invalid"),
        "invalid_request");
    MySqlConnection connection = TestDatabase().Acquire();
    CHECK(connection.ScalarInt(
              "SELECT COUNT(*) FROM files WHERE name='rollback.txt'") == 0);
    CHECK(connection.ScalarInt(
              "SELECT COUNT(*) FROM processing_jobs j JOIN file_versions v "
              "ON v.id=j.file_version_id JOIN files f ON f.id=v.file_id "
              "WHERE f.name='rollback.txt'") == 0);
    CHECK(connection.ScalarInt(
              "SELECT COUNT(*) FROM upload_tasks WHERE id=? AND "
              "state='publishing' AND result_file_id IS NULL",
              {SqlValue(rollback.id)}) == 1);
    CHECK(store.ObjectExists(rollback.id));
}

TEST_CASE(upload_complete_http_response_replays_stable_result_ids) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    AuthService bootstrap(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    const UserIdentity admin =
        bootstrap.CreateUser("admin", kPassword, kRequest);
    const SessionTokens tokens = bootstrap.Login("admin", kPassword, kRequest);
    const Project project = projects.CreateProject(admin.id, "Alpha", kRequest);
    AppConfig config = TestAppConfig(storage.path(), 4);
    config.chunk_bytes = 4;
    Application application(config);
    const std::string uploads_path = "/api/v1/projects/" + project.id + "/uploads";
    const nlohmann::json body = {
        {"mode", "create_file"},
        {"directory_id", project.root_directory_id},
        {"name", "http.txt"},
        {"size", 4},
        {"sha256", Sha256Hex("http", 4)},
        {"media_type", "text/plain"}};
    const std::string task_id =
        ResponseJson(SendJson(application, uploads_path, tokens.raw_token, body))
            ["data"]["task_id"].get<std::string>();
    CHECK(SendPart(application, uploads_path + "/" + task_id + "/parts/0",
                   tokens.raw_token, "http").status() == 200);
    const std::string complete_path =
        uploads_path + "/" + task_id + "/complete";
    const HttpResponse first = application.Prepare(
        Head("POST", complete_path, tokens.raw_token))->Finish();
    const HttpResponse retry = application.Prepare(
        Head("POST", complete_path, tokens.raw_token))->Finish();
    CHECK(first.status() == 200);
    CHECK(retry.status() == 200);
    const nlohmann::json first_data = ResponseJson(first)["data"];
    const nlohmann::json retry_data = ResponseJson(retry)["data"];
    CHECK(!first_data["reused"].get<bool>());
    CHECK(retry_data["reused"].get<bool>());
    CHECK(first_data["file_id"] == retry_data["file_id"]);
    CHECK(first_data["version_id"] == retry_data["version_id"]);
    CHECK(first_data["processing_job_id"] == retry_data["processing_job_id"]);
}
