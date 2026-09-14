#include "app/application.h"
#include "auth/auth_service.h"
#include "core/app_error.h"
#include "file/file_service.h"
#include "file/file_store.h"
#include "project/project_service.h"
#include "../mysql_test_support.h"
#include "../test_support.h"

#include <cstring>
#include <dirent.h>
#include <nlohmann/json.hpp>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

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
        if (std::strcmp(child->d_name, ".") != 0 &&
            std::strcmp(child->d_name, "..") != 0) {
            RemoveTree(path + "/" + child->d_name);
        }
    }
    CHECK(closedir(directory) == 0);
    CHECK(rmdir(path.c_str()) == 0);
}

class TemporaryStorage {
public:
    TemporaryStorage() {
        char pattern[] = "/tmp/smart-docs-file-mutation.XXXXXX";
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

void InsertFile(const std::string& file_id, const std::string& project_id,
                const std::string& directory_id, const std::string& name,
                const std::string& creator_id,
                const std::string& version_id) {
    MySqlConnection connection = TestDatabase().Acquire();
    MySqlTransaction transaction(connection);
    connection.Execute(
        "INSERT INTO files(id, project_id, directory_id, name, created_by) "
        "VALUES (?, ?, ?, ?, ?)",
        {SqlValue(file_id), SqlValue(project_id), SqlValue(directory_id),
         SqlValue(name), SqlValue(creator_id)});
    connection.Execute(
        "INSERT INTO file_versions(id, file_id, version_number, content_id, "
        "size_bytes, sha256, media_type, created_by) VALUES (?, ?, 1, ?, 1, "
        "?, 'text/plain', ?)",
        {SqlValue(version_id), SqlValue(file_id), SqlValue(version_id),
         SqlValue(std::string(64, '0')), SqlValue(creator_id)});
    connection.Execute("UPDATE files SET current_version_id=? WHERE id=?",
                       {SqlValue(version_id), SqlValue(file_id)});
    transaction.Commit();
}

void InsertVersion(const std::string& file_id, const std::string& version_id,
                   uint64_t number, const std::string& creator_id) {
    MySqlConnection connection = TestDatabase().Acquire();
    connection.Execute(
        "INSERT INTO file_versions(id, file_id, version_number, content_id, "
        "size_bytes, sha256, media_type, created_by) VALUES (?, ?, ?, ?, 1, "
        "?, 'text/plain', ?)",
        {SqlValue(version_id), SqlValue(file_id), SqlValue(number),
         SqlValue(version_id), SqlValue(std::string(64, '1')),
         SqlValue(creator_id)});
}

nlohmann::json ResponseJson(const HttpResponse& response) {
    const std::string& wire = response.head_and_body();
    const size_t body = wire.find("\r\n\r\n");
    CHECK(body != std::string::npos);
    return nlohmann::json::parse(wire.substr(body + 4));
}

std::string ResponseHeader(const HttpResponse& response,
                           const std::string& name) {
    const std::string prefix = "\r\n" + name + ": ";
    const std::string& wire = response.head_and_body();
    const size_t start = wire.find(prefix);
    CHECK(start != std::string::npos);
    const size_t value_start = start + prefix.size();
    const size_t end = wire.find("\r\n", value_start);
    CHECK(end != std::string::npos);
    return wire.substr(value_start, end - value_start);
}

RequestHead StateHead(const std::string& method, const std::string& path,
                      const std::string& token, uint64_t length = 0) {
    RequestHead head{};
    head.method = method;
    head.path = path;
    head.version = "1.1";
    head.content_length = length;
    head.headers.emplace("host", "smartdocs.test");
    head.headers.emplace("origin", "http://smartdocs.test");
    head.headers.emplace("cookie", "smartdocs_session=" + token);
    if (method == "PATCH" || method == "PUT") {
        head.headers.emplace("content-type", "application/json");
    }
    return head;
}

HttpResponse Exchange(Application& application, RequestHead head,
                      const std::string& body = std::string()) {
    std::unique_ptr<RequestBodyHandler> handler = application.Prepare(head);
    handler->OnData(body.data(), body.size());
    return handler->Finish();
}

}  // namespace

TEST_CASE(file_mutation_enforces_roles_project_scope_conflicts_and_stable_ids) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    FileStore store(storage.path());
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    FileService files(TestDatabase(), projects, store);

    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const UserIdentity editor = auth.CreateUser("editor", kPassword, kRequest);
    const UserIdentity reader = auth.CreateUser("reader", kPassword, kRequest);
    const UserIdentity outsider = auth.CreateUser("outsider", kPassword, kRequest);
    const SessionContext admin_session = auth.Login("admin", kPassword, kRequest).session;
    const SessionContext editor_session = auth.Login("editor", kPassword, kRequest).session;
    const SessionContext reader_session = auth.Login("reader", kPassword, kRequest).session;
    const SessionContext outsider_session = auth.Login("outsider", kPassword, kRequest).session;
    const Project alpha = projects.CreateProject(admin.id, "Alpha", kRequest);
    const Project beta = projects.CreateProject(outsider.id, "Beta", kRequest);
    projects.SetMemberRole(admin_session, alpha.id, editor.id, Role::Editor,
                           kRequest);
    projects.SetMemberRole(admin_session, alpha.id, reader.id, Role::Reader,
                           kRequest);
    const Directory destination = projects.CreateDirectory(
        admin_session, alpha.id, alpha.root_directory_id, "Destination",
        kRequest);

    const std::string file_id = "10101010101010101010101010101010";
    const std::string version_id = "11111111111111111111111111111111";
    InsertFile(file_id, alpha.id, alpha.root_directory_id, "guide.txt",
               admin.id, version_id);

    CHECK_THROWS_CODE(
        files.Update(reader_session, alpha.id, file_id,
                     UpdateFileCommand{"reader.txt", destination.id}),
        "forbidden");
    CHECK_THROWS_CODE(
        files.Update(outsider_session, alpha.id, file_id,
                     UpdateFileCommand{"outside.txt", destination.id}),
        "resource_not_found");
    CHECK_THROWS_CODE(
        files.Update(admin_session, alpha.id, file_id,
                     UpdateFileCommand{"cross-project.txt", beta.root_directory_id}),
        "resource_not_found");

    const std::string update_request = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    const FileSummary moved = files.Update(
        editor_session, alpha.id, file_id,
        UpdateFileCommand{"moved.txt", destination.id}, update_request);
    CHECK(moved.id == file_id);
    CHECK(moved.current_version_id == version_id);
    CHECK(moved.directory_id == destination.id);
    CHECK(moved.name == "moved.txt");

    const std::string other_file = "12121212121212121212121212121212";
    InsertFile(other_file, alpha.id, destination.id, "occupied.txt", admin.id,
               "13131313131313131313131313131313");
    CHECK_THROWS_CODE(
        files.Update(editor_session, alpha.id, file_id,
                     UpdateFileCommand{"occupied.txt", destination.id}),
        "name_conflict");

    CHECK_THROWS_CODE(files.SoftDelete(reader_session, alpha.id, file_id),
                      "forbidden");
    const std::string delete_request = "cccccccccccccccccccccccccccccccc";
    files.SoftDelete(editor_session, alpha.id, file_id, delete_request);
    CHECK_THROWS_CODE(files.Restore(reader_session, alpha.id, file_id),
                      "forbidden");
    InsertFile("14141414141414141414141414141414", alpha.id, destination.id,
               "moved.txt", admin.id,
               "15151515151515151515151515151515");
    CHECK_THROWS_CODE(files.Restore(editor_session, alpha.id, file_id),
                      "name_conflict");
    {
        MySqlConnection connection = TestDatabase().Acquire();
        connection.Execute("UPDATE files SET name='released.txt' WHERE id=?",
                           {SqlValue("14141414141414141414141414141414")});
    }
    const std::string restore_request = "dddddddddddddddddddddddddddddddd";
    const FileSummary restored =
        files.Restore(editor_session, alpha.id, file_id, restore_request);
    CHECK(!restored.deleted);
    CHECK(restored.id == file_id);
    CHECK(restored.current_version_id == version_id);

    {
        MySqlConnection revoked = TestDatabase().Acquire();
        revoked.Execute(
            "DELETE FROM project_members WHERE project_id=? AND user_id=?",
            {SqlValue(alpha.id), SqlValue(editor.id)});
    }
    CHECK_THROWS_CODE(
        files.Update(editor_session, alpha.id, file_id,
                     UpdateFileCommand{"revoked.txt", destination.id}),
        "resource_not_found");

    MySqlConnection connection = TestDatabase().Acquire();
    CHECK(connection.ScalarInt(
              "SELECT COUNT(*) FROM audit_records WHERE action='file.update' "
              "AND object_id=? AND request_id=?",
              {SqlValue(file_id), SqlValue(update_request)}) == 1);
    CHECK(connection.ScalarInt(
              "SELECT COUNT(*) FROM audit_records WHERE action='file.delete' "
              "AND object_id=? AND request_id=?",
              {SqlValue(file_id), SqlValue(delete_request)}) == 1);
    CHECK(connection.ScalarInt(
              "SELECT COUNT(*) FROM audit_records WHERE action='file.restore' "
              "AND object_id=? AND request_id=?",
              {SqlValue(file_id), SqlValue(restore_request)}) == 1);
}

TEST_CASE(file_mutation_remote_ai_approval_is_exact_version_specific_and_admin_only) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    FileStore store(storage.path());
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    FileService files(TestDatabase(), projects, store);

    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const UserIdentity editor = auth.CreateUser("editor", kPassword, kRequest);
    const UserIdentity reader = auth.CreateUser("reader", kPassword, kRequest);
    const UserIdentity outsider = auth.CreateUser("outsider", kPassword, kRequest);
    const SessionContext admin_session = auth.Login("admin", kPassword, kRequest).session;
    const SessionContext editor_session = auth.Login("editor", kPassword, kRequest).session;
    const SessionContext reader_session = auth.Login("reader", kPassword, kRequest).session;
    const Project alpha = projects.CreateProject(admin.id, "Alpha", kRequest);
    const Project beta = projects.CreateProject(outsider.id, "Beta", kRequest);
    projects.SetMemberRole(admin_session, alpha.id, editor.id, Role::Editor,
                           kRequest);
    projects.SetMemberRole(admin_session, alpha.id, reader.id, Role::Reader,
                           kRequest);

    const std::string file_id = "20202020202020202020202020202020";
    const std::string first = "21212121212121212121212121212121";
    const std::string second = "22222222222222222222222222222222";
    const std::string third = "23232323232323232323232323232323";
    InsertFile(file_id, alpha.id, alpha.root_directory_id, "policy.txt",
               admin.id, first);
    InsertVersion(file_id, second, 2, admin.id);
    InsertFile("24242424242424242424242424242424", beta.id,
               beta.root_directory_id, "foreign.txt", outsider.id,
               "25252525252525252525252525252525");

    const RemoteAiPolicyCommand duplicate{"remote_ai_allowed",
                                           {first, first, second}};
    const RemoteAiPolicyCommand allow{"remote_ai_allowed", {first, second}};
    CHECK_THROWS_CODE(
        files.SetRemoteAiPolicy(reader_session, alpha.id, file_id, allow),
        "forbidden");
    CHECK_THROWS_CODE(
        files.SetRemoteAiPolicy(editor_session, alpha.id, file_id, allow),
        "forbidden");
    CHECK_THROWS_CODE(
        files.SetRemoteAiPolicy(admin_session, alpha.id, file_id, duplicate),
        "invalid_request");
    const std::string policy_request = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee";
    const RemoteAiState state = files.SetRemoteAiPolicy(
        admin_session, alpha.id, file_id, allow, policy_request);
    CHECK(state.policy == "remote_ai_allowed");
    CHECK(state.approved_version_ids.size() == 2);
    CHECK(state.approved_version_ids[0] == first);
    CHECK(state.approved_version_ids[1] == second);

    InsertVersion(file_id, third, 3, admin.id);
    const RemoteAiState exact = files.SetRemoteAiPolicy(
        admin_session, alpha.id, file_id,
        RemoteAiPolicyCommand{"remote_ai_allowed", {second}});
    CHECK(exact.approved_version_ids.size() == 1);
    CHECK(exact.approved_version_ids[0] == second);
    CHECK_THROWS_CODE(
        files.SetRemoteAiPolicy(
            admin_session, alpha.id, file_id,
            RemoteAiPolicyCommand{"remote_ai_allowed",
                                  {"25252525252525252525252525252525"}}),
        "resource_not_found");
    {
        MySqlConnection connection = TestDatabase().Acquire();
        CHECK(connection.ScalarInt(
                  "SELECT COUNT(*) FROM file_versions WHERE file_id=? AND "
                  "remote_ai_approved=TRUE AND id=?",
                  {SqlValue(file_id), SqlValue(second)}) == 1);
    }
    CHECK_THROWS_CODE(
        files.SetRemoteAiPolicy(
            admin_session, alpha.id, file_id,
            RemoteAiPolicyCommand{"internal_only", {first}}),
        "invalid_request");
    const RemoteAiState internal = files.SetRemoteAiPolicy(
        admin_session, alpha.id, file_id,
        RemoteAiPolicyCommand{"internal_only", {}});
    CHECK(internal.policy == "internal_only");
    CHECK(internal.approved_version_ids.empty());

    MySqlConnection connection = TestDatabase().Acquire();
    CHECK(connection.ScalarInt(
              "SELECT COUNT(*) FROM file_versions WHERE file_id=? AND "
              "(remote_ai_approved=TRUE OR remote_ai_approved_at IS NOT NULL "
              "OR remote_ai_approved_by IS NOT NULL)",
              {SqlValue(file_id)}) == 0);
    CHECK(connection.ScalarInt(
              "SELECT COUNT(*) FROM audit_records WHERE "
              "action='file.remote_ai_policy' AND object_id=? AND request_id=?",
              {SqlValue(file_id), SqlValue(policy_request)}) == 1);
}

TEST_CASE(file_mutation_http_routes_enforce_origin_json_limit_and_audit_request_id) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const SessionTokens tokens = auth.Login("admin", kPassword, kRequest);
    const Project alpha = projects.CreateProject(admin.id, "Alpha", kRequest);
    const std::string file_id = "30303030303030303030303030303030";
    InsertFile(file_id, alpha.id, alpha.root_directory_id, "route.txt",
               admin.id, "31313131313131313131313131313131");

    AppConfig config = TestAppConfig(storage.path(), 2);
    Application application(config);
    const std::string path = "/api/v1/projects/" + alpha.id + "/files/" + file_id;
    const std::string update_body =
        nlohmann::json{{"name", "route-renamed.txt"},
                       {"directory_id", alpha.root_directory_id}}.dump();

    RequestHead missing_origin = StateHead("PATCH", path, tokens.raw_token,
                                           update_body.size());
    missing_origin.headers.erase("origin");
    CHECK_THROWS_CODE(application.Prepare(missing_origin), "origin_mismatch");
    RequestHead delete_without_origin =
        StateHead("DELETE", path, tokens.raw_token);
    delete_without_origin.headers.erase("origin");
    CHECK_THROWS_CODE(application.Prepare(delete_without_origin),
                      "origin_mismatch");
    RequestHead restore_without_origin =
        StateHead("POST", path + "/restore", tokens.raw_token);
    restore_without_origin.headers.erase("origin");
    CHECK_THROWS_CODE(application.Prepare(restore_without_origin),
                      "origin_mismatch");

    AppConfig tiny = config;
    tiny.max_json_bytes = 8;
    Application limited(tiny);
    CHECK_THROWS_CODE(
        limited.Prepare(StateHead("PATCH", path, tokens.raw_token,
                                  update_body.size())),
        "body_too_large");

    HttpResponse updated = Exchange(
        application,
        StateHead("PATCH", path, tokens.raw_token, update_body.size()),
        update_body);
    CHECK(updated.status() == 200);
    const std::string request_id =
        ResponseJson(updated)["request_id"].get<std::string>();
    MySqlConnection connection = TestDatabase().Acquire();
    CHECK(connection.ScalarInt(
              "SELECT COUNT(*) FROM audit_records WHERE request_id=? AND "
              "action='file.update' AND object_id=?",
              {SqlValue(request_id), SqlValue(file_id)}) == 1);

    const std::string policy_path = path + "/remote-ai-policy";
    const std::string policy_body =
        nlohmann::json{{"policy", "remote_ai_allowed"},
                       {"approved_version_ids",
                        nlohmann::json::array(
                            {"31313131313131313131313131313131"})}}.dump();
    RequestHead policy_without_origin = StateHead(
        "PUT", policy_path, tokens.raw_token, policy_body.size());
    policy_without_origin.headers.erase("origin");
    CHECK_THROWS_CODE(application.Prepare(policy_without_origin),
                      "origin_mismatch");
    CHECK_THROWS_CODE(
        limited.Prepare(StateHead("PUT", policy_path, tokens.raw_token,
                                  policy_body.size())),
        "body_too_large");
    HttpResponse policy = Exchange(
        application,
        StateHead("PUT", policy_path, tokens.raw_token, policy_body.size()),
        policy_body);
    CHECK(policy.status() == 200);
    const std::string policy_request =
        ResponseJson(policy)["request_id"].get<std::string>();

    HttpResponse deleted = Exchange(
        application, StateHead("DELETE", path, tokens.raw_token));
    CHECK(deleted.status() == 204);
    const std::string delete_request = ResponseHeader(deleted, "X-Request-ID");
    HttpResponse restored = Exchange(
        application,
        StateHead("POST", path + "/restore", tokens.raw_token));
    CHECK(restored.status() == 200);
    const std::string restore_request =
        ResponseJson(restored)["request_id"].get<std::string>();

    CHECK(connection.ScalarInt(
              "SELECT COUNT(*) FROM audit_records WHERE request_id=? AND "
              "action='file.remote_ai_policy' AND object_id=?",
              {SqlValue(policy_request), SqlValue(file_id)}) == 1);
    CHECK(connection.ScalarInt(
              "SELECT COUNT(*) FROM audit_records WHERE request_id=? AND "
              "action='file.delete' AND object_id=?",
              {SqlValue(delete_request), SqlValue(file_id)}) == 1);
    CHECK(connection.ScalarInt(
              "SELECT COUNT(*) FROM audit_records WHERE request_id=? AND "
              "action='file.restore' AND object_id=?",
              {SqlValue(restore_request), SqlValue(file_id)}) == 1);
}
