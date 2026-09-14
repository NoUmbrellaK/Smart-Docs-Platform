#include "app/application.h"
#include "auth/auth_service.h"
#include "core/app_error.h"
#include "core/crypto.h"
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
        char pattern[] = "/tmp/smart-docs-upload-part.XXXXXX";
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

RequestHead Head(const std::string& method, const std::string& path,
                 const std::string& raw_token, uint64_t length = 0) {
    RequestHead head{};
    head.method = method;
    head.path = path;
    head.version = "1.1";
    head.content_length = length;
    head.headers.emplace("host", "smartdocs.test");
    head.headers.emplace("origin", "http://smartdocs.test");
    head.headers.emplace("cookie", "smartdocs_session=" + raw_token);
    return head;
}

nlohmann::json ResponseJson(const HttpResponse& response) {
    const std::string& serialized = response.head_and_body();
    const size_t body = serialized.find("\r\n\r\n");
    CHECK(body != std::string::npos);
    return nlohmann::json::parse(serialized.substr(body + 4));
}

HttpResponse SendJson(Application& application, const std::string& method,
                      const std::string& path, const std::string& token,
                      const nlohmann::json& body) {
    const std::string serialized = body.dump();
    RequestHead head = Head(method, path, token, serialized.size());
    head.headers.emplace("content-type", "application/json");
    std::unique_ptr<RequestBodyHandler> handler = application.Prepare(head);
    handler->OnData(serialized.data(), serialized.size());
    return handler->Finish();
}

HttpResponse SendPart(Application& application, const std::string& path,
                      const std::string& token, const std::string& bytes,
                      const std::string& digest) {
    RequestHead head = Head("PUT", path, token, bytes.size());
    head.headers.emplace("content-type", "application/octet-stream");
    head.headers.emplace("x-chunk-sha256", digest);
    std::unique_ptr<RequestBodyHandler> handler = application.Prepare(head);
    const size_t split = bytes.size() / 2;
    handler->OnData(bytes.data(), split);
    handler->OnData(bytes.data() + split, bytes.size() - split);
    return handler->Finish();
}

CreateUploadCommand NewFile(const Project& project, const std::string& name,
                            const std::string& bytes) {
    CreateUploadCommand command{};
    command.mode = UploadMode::CreateFile;
    command.directory_id = project.root_directory_id;
    command.name = name;
    command.size = bytes.size();
    command.sha256 = Sha256Hex(bytes.data(), bytes.size());
    command.media_type = "application/octet-stream";
    return command;
}

}  // namespace

TEST_CASE(upload_part_tasks_enforce_boundaries_ownership_and_cancellation) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    FileStore store(storage.path());
    UploadService uploads(TestDatabase(), projects, store, 8, 4);

    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const UserIdentity editor = auth.CreateUser("editor", kPassword, kRequest);
    const UserIdentity reader = auth.CreateUser("reader", kPassword, kRequest);
    const SessionContext admin_session =
        auth.Login("admin", kPassword, kRequest).session;
    const SessionContext editor_session =
        auth.Login("editor", kPassword, kRequest).session;
    const SessionContext reader_session =
        auth.Login("reader", kPassword, kRequest).session;
    const Project project = projects.CreateProject(admin.id, "Alpha", kRequest);
    projects.SetMemberRole(admin_session, project.id, editor.id, Role::Editor,
                           kRequest);
    projects.SetMemberRole(admin_session, project.id, reader.id, Role::Reader,
                           kRequest);

    UploadTask empty = uploads.Create(editor_session, project.id,
                                      NewFile(project, "empty.txt", ""), kRequest);
    CHECK(empty.owner_id == editor.id);
    CHECK(empty.state == "uploading");
    CHECK(empty.chunk_size == 4);
    CHECK(empty.part_count == 0);
    CHECK(empty.received_bytes == 0);
    CHECK(uploads.GetOwn(editor_session, project.id, empty.id)
              .confirmed_parts.empty());

    UploadListQuery query;
    query.state = "uploading";
    const Page<UploadTask> page =
        uploads.ListOwn(editor_session, project.id, query);
    CHECK(page.total == 1);
    CHECK(page.items[0].id == empty.id);
    CHECK(uploads.ListOwn(admin_session, project.id, query).total == 0);
    CHECK_THROWS_CODE(uploads.GetOwn(admin_session, project.id, empty.id),
                      "resource_not_found");
    CHECK_THROWS_CODE(uploads.Create(reader_session, project.id,
                                     NewFile(project, "denied.bin", "a"),
                                     kRequest),
                      "forbidden");
    CHECK_THROWS_CODE(uploads.Create(editor_session, project.id,
                                     NewFile(project, "large.bin", "123456789"),
                                     kRequest),
                      "file_too_large");

    uploads.Cancel(editor_session, project.id, empty.id, kRequest);
    uploads.Cancel(editor_session, project.id, empty.id, kRequest);
    CHECK(uploads.GetOwn(editor_session, project.id, empty.id).task.state ==
          "cancelled");
    CHECK_THROWS_CODE(uploads.Cancel(admin_session, project.id, empty.id,
                                    kRequest),
                      "resource_not_found");
    const Project other =
        projects.CreateProject(admin.id, "Other", kRequest);
    CHECK_THROWS_CODE(uploads.GetOwn(admin_session, other.id, empty.id),
                      "resource_not_found");
    MySqlConnection connection = TestDatabase().Acquire();
    CHECK(connection.ScalarInt(
              "SELECT COUNT(*) FROM audit_records WHERE action='upload.cancel' "
              "AND object_id=?",
              {SqlValue(empty.id)}) == 1);
}

TEST_CASE(upload_part_creation_rejects_name_and_version_conflicts) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    FileStore store(storage.path());
    UploadService uploads(TestDatabase(), projects, store, 8, 4);
    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const SessionContext session =
        auth.Login("admin", kPassword, kRequest).session;
    const Project project = projects.CreateProject(admin.id, "Alpha", kRequest);

    const std::string file_id = "11111111111111111111111111111111";
    const std::string version_id = "22222222222222222222222222222222";
    {
        MySqlConnection connection = TestDatabase().Acquire();
        connection.Execute(
            "INSERT INTO files(id, project_id, directory_id, name, created_by) "
            "VALUES (?, ?, ?, 'guide.txt', ?)",
            {SqlValue(file_id), SqlValue(project.id),
             SqlValue(project.root_directory_id), SqlValue(admin.id)});
        connection.Execute(
            "INSERT INTO file_versions(id, file_id, version_number, content_id, "
            "size_bytes, sha256, media_type, created_by) VALUES (?, ?, 1, ?, "
            "1, ?, 'text/plain', ?)",
            {SqlValue(version_id), SqlValue(file_id),
             SqlValue("33333333333333333333333333333333"),
             SqlValue(Sha256Hex("x", 1)), SqlValue(admin.id)});
        connection.Execute("UPDATE files SET current_version_id=? WHERE id=?",
                           {SqlValue(version_id), SqlValue(file_id)});
    }

    CHECK_THROWS_CODE(uploads.Create(session, project.id,
                                     NewFile(project, "guide.txt", "y"),
                                     kRequest),
                      "name_conflict");
    CreateUploadCommand version{};
    version.mode = UploadMode::CreateVersion;
    version.file_id = file_id;
    version.observed_current_version_id =
        "44444444444444444444444444444444";
    version.size = 1;
    version.sha256 = Sha256Hex("y", 1);
    version.media_type = "text/plain";
    CHECK_THROWS_CODE(uploads.Create(session, project.id, version, kRequest),
                      "version_conflict");
    version.observed_current_version_id = version_id;
    CHECK(uploads.Create(session, project.id, version, kRequest).part_count ==
          1);
}

TEST_CASE(upload_part_http_streams_and_replays_identical_content) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    AuthService bootstrap(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    const UserIdentity admin =
        bootstrap.CreateUser("admin", kPassword, kRequest);
    const UserIdentity editor =
        bootstrap.CreateUser("editor", kPassword, kRequest);
    const SessionTokens tokens = bootstrap.Login("admin", kPassword, kRequest);
    const SessionTokens editor_tokens =
        bootstrap.Login("editor", kPassword, kRequest);
    const Project project = projects.CreateProject(admin.id, "Alpha", kRequest);
    projects.SetMemberRole(tokens.session, project.id, editor.id, Role::Editor,
                           kRequest);
    AppConfig config = TestAppConfig(storage.path(), 4);
    config.chunk_bytes = 4;
    Application application(config);

    const nlohmann::json create_body = {
        {"mode", "create_file"},
        {"directory_id", project.root_directory_id},
        {"name", "payload.bin"},
        {"size", 4},
        {"sha256", Sha256Hex("abcd", 4)},
        {"media_type", "application/octet-stream"}};
    HttpResponse created = SendJson(
        application, "POST", "/api/v1/projects/" + project.id + "/uploads",
        tokens.raw_token, create_body);
    CHECK(created.status() == 201);
    const std::string task_id =
        ResponseJson(created)["data"]["task_id"].get<std::string>();
    const std::string part_path = "/api/v1/projects/" + project.id +
        "/uploads/" + task_id + "/parts/0";
    const std::string digest = Sha256Hex("abcd", 4);

    HttpResponse first =
        SendPart(application, part_path, tokens.raw_token, "abcd", digest);
    CHECK(first.status() == 200);
    const nlohmann::json first_json = ResponseJson(first);
    CHECK(!first_json["data"]["reused"].get<bool>());
    {
        MySqlConnection connection = TestDatabase().Acquire();
        CHECK(connection.ScalarInt(
                  "SELECT COUNT(*) FROM audit_records WHERE "
                  "action='upload.part' AND request_id=? AND object_id=?",
                  {SqlValue(first_json["request_id"].get<std::string>()),
                   SqlValue(task_id)}) == 1);
    }
    HttpResponse replay =
        SendPart(application, part_path, tokens.raw_token, "abcd", digest);
    CHECK(replay.status() == 200);
    CHECK(ResponseJson(replay)["data"]["reused"].get<bool>());
    CHECK_THROWS_CODE(
        SendPart(application, part_path, tokens.raw_token, "wxyz",
                 Sha256Hex("wxyz", 4)),
        "chunk_conflict");

    RequestHead wrong_size = Head("PUT", part_path, tokens.raw_token, 3);
    wrong_size.headers.emplace("content-type", "application/octet-stream");
    wrong_size.headers.emplace("x-chunk-sha256", digest);
    CHECK_THROWS_CODE(application.Prepare(wrong_size), "invalid_request");

    RequestHead wrong_type = Head("PUT", part_path, tokens.raw_token, 4);
    wrong_type.headers.emplace("content-type", "text/plain");
    wrong_type.headers.emplace("x-chunk-sha256", digest);
    CHECK_THROWS_CODE(application.Prepare(wrong_type), "invalid_request");
    RequestHead missing_digest = Head("PUT", part_path, tokens.raw_token, 4);
    missing_digest.headers.emplace("content-type", "application/octet-stream");
    CHECK_THROWS_CODE(application.Prepare(missing_digest), "invalid_request");
    RequestHead missing_origin = Head("PUT", part_path, tokens.raw_token, 4);
    missing_origin.headers.erase("origin");
    missing_origin.headers.emplace("content-type", "application/octet-stream");
    missing_origin.headers.emplace("x-chunk-sha256", digest);
    CHECK_THROWS_CODE(application.Prepare(missing_origin), "origin_mismatch");
    const std::string invalid_part_path = "/api/v1/projects/" + project.id +
        "/uploads/" + task_id + "/parts/1";
    CHECK_THROWS_CODE(
        SendPart(application, invalid_part_path, tokens.raw_token, "abcd",
                 digest),
        "invalid_part_number");
    CHECK_THROWS_CODE(
        SendPart(application, part_path, editor_tokens.raw_token, "abcd",
                 digest),
        "resource_not_found");

    const nlohmann::json detail = ResponseJson(
        application.Prepare(Head("GET", "/api/v1/projects/" + project.id +
            "/uploads/" + task_id, tokens.raw_token))->Finish())["data"];
    CHECK(detail["task_id"] == task_id);
    CHECK(detail["received_bytes"] == 4);
    CHECK(detail["confirmed_parts"].size() == 1);
    CHECK(detail["confirmed_parts"][0]["part_number"] == 0);

    RequestHead list_head = Head(
        "GET", "/api/v1/projects/" + project.id + "/uploads",
        tokens.raw_token);
    list_head.query = "state=uploading&page=1&page_size=10";
    const nlohmann::json list =
        ResponseJson(application.Prepare(list_head)->Finish())["data"];
    CHECK(list["total"] == 1);
    CHECK(list["items"][0]["task_id"] == task_id);

    const nlohmann::json digest_body = {
        {"mode", "create_file"},
        {"directory_id", project.root_directory_id},
        {"name", "digest.bin"},
        {"size", 4},
        {"sha256", Sha256Hex("good", 4)},
        {"media_type", "application/octet-stream"}};
    const std::string digest_task = ResponseJson(SendJson(
        application, "POST", "/api/v1/projects/" + project.id + "/uploads",
        tokens.raw_token, digest_body))["data"]["task_id"].get<std::string>();
    const std::string digest_path = "/api/v1/projects/" + project.id +
        "/uploads/" + digest_task + "/parts/0";
    {
        RequestHead short_head = Head("PUT", digest_path, tokens.raw_token, 4);
        short_head.headers.emplace("content-type", "application/octet-stream");
        short_head.headers.emplace("x-chunk-sha256", Sha256Hex("good", 4));
        std::unique_ptr<RequestBodyHandler> short_body =
            application.Prepare(short_head);
        short_body->OnData("goo", 3);
        CHECK_THROWS_CODE(short_body->Finish(), "invalid_request");
    }
    {
        RequestHead long_head = Head("PUT", digest_path, tokens.raw_token, 4);
        long_head.headers.emplace("content-type", "application/octet-stream");
        long_head.headers.emplace("x-chunk-sha256", Sha256Hex("good", 4));
        std::unique_ptr<RequestBodyHandler> long_body =
            application.Prepare(long_head);
        CHECK_THROWS_CODE(long_body->OnData("good!", 5), "invalid_request");
    }
    CHECK_THROWS_CODE(
        SendPart(application, digest_path, tokens.raw_token, "bad!",
                 Sha256Hex("good", 4)),
        "chunk_digest_mismatch");
    {
        MySqlConnection connection = TestDatabase().Acquire();
        CHECK(connection.ScalarInt(
                  "SELECT COUNT(*) FROM upload_parts WHERE upload_task_id=?",
                  {SqlValue(digest_task)}) == 0);
    }
    struct stat missing{};
    errno = 0;
    CHECK(lstat((storage.path() + "/staging/" + digest_task + "/parts/0").c_str(),
                &missing) != 0);
    CHECK(errno == ENOENT);
    CHECK(application.ErrorResponse(
              AppError(422, "chunk_digest_mismatch", "digest mismatch"))
              .status() == 422);

    const std::string cancel_path = "/api/v1/projects/" + project.id +
        "/uploads/" + task_id + "/cancel";
    HttpResponse cancelled = application.Prepare(
        Head("POST", cancel_path, tokens.raw_token))->Finish();
    CHECK(cancelled.status() == 204);
    CHECK(cancelled.head_and_body().find("X-Request-ID: ") !=
          std::string::npos);
    CHECK_THROWS_CODE(
        SendPart(application, part_path, tokens.raw_token, "abcd", digest),
        "upload_state_conflict");

    projects.SetMemberRole(tokens.session, project.id, admin.id, Role::Reader,
                           kRequest);
    CHECK_THROWS_CODE(
        SendPart(application, digest_path, tokens.raw_token, "good",
                 Sha256Hex("good", 4)),
        "forbidden");
}

TEST_CASE(upload_part_concurrent_identical_puts_create_one_confirmation) {
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
    const nlohmann::json body = {
        {"mode", "create_file"}, {"directory_id", project.root_directory_id},
        {"name", "race.bin"}, {"size", 4},
        {"sha256", Sha256Hex("race", 4)},
        {"media_type", "application/octet-stream"}};
    const std::string task_id = ResponseJson(SendJson(
        application, "POST", "/api/v1/projects/" + project.id + "/uploads",
        tokens.raw_token, body))["data"]["task_id"].get<std::string>();
    const std::string path = "/api/v1/projects/" + project.id + "/uploads/" +
        task_id + "/parts/0";
    const std::string digest = Sha256Hex("race", 4);
    std::mutex mutex;
    std::vector<bool> reused;
    std::vector<std::string> errors;
    auto put = [&]() {
        try {
            HttpResponse response =
                SendPart(application, path, tokens.raw_token, "race", digest);
            const bool value =
                ResponseJson(response)["data"]["reused"].get<bool>();
            std::lock_guard<std::mutex> lock(mutex);
            reused.push_back(value);
        } catch (const AppError& error) {
            std::lock_guard<std::mutex> lock(mutex);
            errors.push_back(error.code);
        }
    };
    std::thread first(put);
    std::thread second(put);
    first.join();
    second.join();
    CHECK(errors.empty());
    CHECK(reused.size() == 2);
    CHECK(reused[0] != reused[1]);
    MySqlConnection connection = TestDatabase().Acquire();
    CHECK(connection.ScalarInt(
              "SELECT COUNT(*) FROM upload_parts WHERE upload_task_id=?",
              {SqlValue(task_id)}) == 1);
}
