#include "app/application.h"
#include "auth/auth_service.h"
#include "core/app_error.h"
#include "file/file_service.h"
#include "file/file_store.h"
#include "project/project_service.h"
#include "../mysql_test_support.h"
#include "../test_support.h"

#include <cerrno>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <nlohmann/json.hpp>
#include <string>
#include <sys/stat.h>
#include <type_traits>
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
        char pattern[] = "/tmp/smart-docs-file-metadata.XXXXXX";
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

void Publish(FileStore& store, const std::string& task_id,
             const std::string& content_id, const std::string& content) {
    PartWriter writer = store.CreatePartWriter(task_id, 0);
    writer.Write(content.data(), content.size());
    const PartInfo part = writer.Finish();
    store.PublishObject(store.Assemble(task_id, {part}), content_id);
}

void InsertFileVersion(const std::string& file_id,
                       const std::string& project_id,
                       const std::string& directory_id,
                       const std::string& name,
                       const std::string& version_id,
                       const std::string& content_id,
                       const std::string& creator_id,
                       const std::string& sha256,
                       const std::string& media_type = "text/plain") {
    MySqlConnection connection = TestDatabase().Acquire();
    MySqlTransaction transaction(connection);
    connection.Execute(
        "INSERT INTO files(id, project_id, directory_id, name, created_by) "
        "VALUES (?, ?, ?, ?, ?)",
        {SqlValue(file_id), SqlValue(project_id), SqlValue(directory_id),
         SqlValue(name), SqlValue(creator_id)});
    connection.Execute(
        "INSERT INTO file_versions(id, file_id, version_number, content_id, "
        "size_bytes, sha256, media_type, created_by) "
        "VALUES (?, ?, 1, ?, 11, ?, ?, ?)",
        {SqlValue(version_id), SqlValue(file_id), SqlValue(content_id),
         SqlValue(sha256), SqlValue(media_type), SqlValue(creator_id)});
    connection.Execute(
        "UPDATE files SET current_version_id=? WHERE id=?",
        {SqlValue(version_id), SqlValue(file_id)});
    transaction.Commit();
}

std::string ReadAll(int fd) {
    std::string result;
    char buffer[8];
    while (true) {
        const ssize_t count = read(fd, buffer, sizeof(buffer));
        if (count < 0 && errno == EINTR) continue;
        CHECK(count >= 0);
        if (count == 0) return result;
        result.append(buffer, static_cast<size_t>(count));
    }
}

nlohmann::json ResponseJson(const HttpResponse& response) {
    const std::string& serialized = response.head_and_body();
    const size_t body = serialized.find("\r\n\r\n");
    CHECK(body != std::string::npos);
    return nlohmann::json::parse(serialized.substr(body + 4));
}

RequestHead GetHead(const std::string& path, const std::string& raw_token,
                    const std::string& query = std::string()) {
    RequestHead head{};
    head.method = "GET";
    head.path = path;
    head.query = query;
    head.version = "1.1";
    head.headers.emplace("host", "smartdocs.test");
    head.headers.emplace("cookie", "smartdocs_session=" + raw_token);
    return head;
}

}  // namespace

static_assert(!std::is_copy_constructible<AuthorizedVersion>::value,
              "an authorized version must not duplicate descriptor ownership");
static_assert(std::is_move_constructible<AuthorizedVersion>::value,
              "an authorized version must transfer descriptor ownership");

TEST_CASE(file_metadata_listing_is_scoped_paginated_and_deletion_explicit) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    FileStore store(storage.path());
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    FileService files(TestDatabase(), projects, store);

    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const UserIdentity outsider =
        auth.CreateUser("outsider", kPassword, kRequest);
    const SessionContext admin_session =
        auth.Login("admin", kPassword, kRequest).session;
    const SessionContext outsider_session =
        auth.Login("outsider", kPassword, kRequest).session;
    const Project alpha = projects.CreateProject(admin.id, "Alpha", kRequest);
    const Project beta =
        projects.CreateProject(outsider.id, "Beta", kRequest);

    const std::string file_id = "10101010101010101010101010101010";
    const std::string version_id = "20202020202020202020202020202020";
    const std::string task_id = "30303030303030303030303030303030";
    const std::string content_id = "aabb4040404040404040404040404040";
    const std::string digest =
        "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9";
    Publish(store, task_id, content_id, "hello world");
    InsertFileVersion(file_id, alpha.id, alpha.root_directory_id,
                      "guide.txt", version_id, content_id, admin.id, digest);

    FileListQuery query;
    query.directory_id = alpha.root_directory_id;
    query.name = "GUIDE";
    query.page = 1;
    query.page_size = 1;
    const Page<FileSummary> page = files.List(admin_session, alpha.id, query);
    CHECK(page.page == 1);
    CHECK(page.page_size == 1);
    CHECK(page.total == 1);
    CHECK(page.items.size() == 1);
    CHECK(page.items[0].id == file_id);
    CHECK(page.items[0].current_version_id == version_id);
    CHECK(!page.items[0].deleted);
    CHECK(page.items[0].remote_ai_policy == "internal_only");

    CHECK_THROWS_CODE(files.List(outsider_session, alpha.id, query),
                      "resource_not_found");
    query.directory_id = beta.root_directory_id;
    CHECK_THROWS_CODE(files.List(admin_session, alpha.id, query),
                      "resource_not_found");

    query.directory_id.clear();
    query.name.clear();
    {
        MySqlConnection connection = TestDatabase().Acquire();
        connection.Execute(
            "UPDATE files SET deleted_at=UTC_TIMESTAMP(6), deleted_by=? "
            "WHERE id=?",
            {SqlValue(admin.id), SqlValue(file_id)});
    }
    CHECK(files.List(admin_session, alpha.id, query).total == 0);
    query.deleted = true;
    const Page<FileSummary> deleted =
        files.List(admin_session, alpha.id, query);
    CHECK(deleted.total == 1);
    CHECK(deleted.items[0].deleted);
    CHECK_THROWS_CODE(files.ListVersions(admin_session, alpha.id, file_id),
                      "resource_not_found");
    CHECK_THROWS_CODE(
        files.OpenVersion(admin_session, alpha.id, file_id, version_id),
        "resource_not_found");
}

TEST_CASE(file_metadata_versions_open_only_the_exact_authorized_object) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    FileStore store(storage.path());
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    FileService files(TestDatabase(), projects, store);

    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const UserIdentity outsider =
        auth.CreateUser("outsider", kPassword, kRequest);
    const SessionContext admin_session =
        auth.Login("admin", kPassword, kRequest).session;
    const SessionContext outsider_session =
        auth.Login("outsider", kPassword, kRequest).session;
    const Project alpha = projects.CreateProject(admin.id, "Alpha", kRequest);
    projects.CreateProject(outsider.id, "Beta", kRequest);

    const std::string file_id = "51515151515151515151515151515151";
    const std::string version_id = "52525252525252525252525252525252";
    const std::string content_id = "aabb5353535353535353535353535353";
    const std::string digest =
        "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9";
    Publish(store, "54545454545454545454545454545454", content_id,
            "hello world");
    InsertFileVersion(file_id, alpha.id, alpha.root_directory_id,
                      "guide.txt", version_id, content_id, admin.id, digest);

    const std::vector<FileVersionSummary> versions =
        files.ListVersions(admin_session, alpha.id, file_id);
    CHECK(versions.size() == 1);
    CHECK(versions[0].id == version_id);
    CHECK(versions[0].file_id == file_id);
    CHECK(versions[0].version_number == 1);
    CHECK(versions[0].size == 11);
    CHECK(versions[0].sha256 == digest);
    CHECK(versions[0].processing_state == "pending");
    CHECK(!versions[0].remote_ai_approved);

    int owned_fd = -1;
    {
        AuthorizedVersion opened =
            files.OpenVersion(admin_session, alpha.id, file_id, version_id);
        CHECK(opened.file.id == file_id);
        CHECK(opened.version.id == version_id);
        owned_fd = opened.fd;
        CHECK(ReadAll(opened.fd) == "hello world");
    }
    errno = 0;
    CHECK(fcntl(owned_fd, F_GETFD) == -1);
    CHECK(errno == EBADF);

    AuthorizedVersion released =
        files.OpenVersion(admin_session, alpha.id, file_id, version_id);
    const int released_fd = released.ReleaseFd();
    CHECK(released.fd == -1);
    CHECK(fcntl(released_fd, F_GETFD) != -1);
    CHECK(close(released_fd) == 0);

    CHECK_THROWS_CODE(
        files.OpenVersion(outsider_session, alpha.id, file_id, version_id),
        "resource_not_found");
    CHECK_THROWS_CODE(
        files.OpenVersion(admin_session, alpha.id, file_id,
                          "99999999999999999999999999999999"),
        "resource_not_found");

    const std::string missing_file = "61616161616161616161616161616161";
    const std::string missing_version = "62626262626262626262626262626262";
    InsertFileVersion(
        missing_file, alpha.id, alpha.root_directory_id, "missing.txt",
        missing_version, "aabb6363636363636363636363636363", admin.id,
        "0000000000000000000000000000000000000000000000000000000000000000");
    CHECK_THROWS_CODE(
        files.OpenVersion(admin_session, alpha.id, missing_file,
                          missing_version),
        "content_unavailable");
}

TEST_CASE(file_metadata_constraints_and_name_validation_prevent_aliasing) {
    RequireMySqlTests();
    ResetTestDatabase();
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const Project alpha = projects.CreateProject(admin.id, "Alpha", kRequest);

    CHECK(ValidateFileName("  guide.txt  ") == "guide.txt");
    CHECK_THROWS_CODE(ValidateFileName("../../resources/index.html"),
                      "invalid_request");
    CHECK_THROWS_CODE(ValidateFileName(std::string("bad\0name", 8)),
                      "invalid_request");

    MySqlConnection connection = TestDatabase().Acquire();
    const std::string first = "71717171717171717171717171717171";
    const std::string second = "72727272727272727272727272727272";
    connection.Execute(
        "INSERT INTO files(id, project_id, directory_id, name, created_by, "
        "deleted_at, deleted_by) VALUES (?, ?, ?, 'same.txt', ?, "
        "UTC_TIMESTAMP(6), ?)",
        {SqlValue(first), SqlValue(alpha.id),
         SqlValue(alpha.root_directory_id), SqlValue(admin.id),
         SqlValue(admin.id)});
    connection.Execute(
        "INSERT INTO files(id, project_id, directory_id, name, created_by) "
        "VALUES (?, ?, ?, 'same.txt', ?)",
        {SqlValue(second), SqlValue(alpha.id),
         SqlValue(alpha.root_directory_id), SqlValue(admin.id)});
    CHECK_THROWS_CODE(
        connection.Execute("UPDATE files SET deleted_at=NULL, deleted_by=NULL "
                           "WHERE id=?", {SqlValue(first)}),
        "constraint_conflict");
    CHECK_THROWS_CODE(
        connection.Execute(
            "INSERT INTO files(id, project_id, directory_id, name, created_by) "
            "VALUES ('73737373737373737373737373737373', ?, ?, 'same.txt', ?)",
            {SqlValue(alpha.id), SqlValue(alpha.root_directory_id),
             SqlValue(admin.id)}),
        "constraint_conflict");
}

TEST_CASE(file_metadata_http_routes_expose_lists_and_reserve_content_paths) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    FileStore store(storage.path());
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const SessionTokens tokens = auth.Login("admin", kPassword, kRequest);
    const Project alpha = projects.CreateProject(admin.id, "Alpha", kRequest);

    const std::string file_id = "81818181818181818181818181818181";
    const std::string version_id = "82828282828282828282828282828282";
    const std::string content_id = "aabb8383838383838383838383838383";
    const std::string digest =
        "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9";
    Publish(store, "84848484848484848484848484848484", content_id,
            "hello world");
    InsertFileVersion(file_id, alpha.id, alpha.root_directory_id,
                      "guide.txt", version_id, content_id, admin.id, digest);

    Application application(TestAppConfig(storage.path(), 2));
    HttpResponse list = application.Prepare(GetHead(
        "/api/v1/projects/" + alpha.id + "/files", tokens.raw_token,
        "directory_id=" + alpha.root_directory_id +
            "&name=guide&page=1&page_size=1"))->Finish();
    CHECK(list.status() == 200);
    const nlohmann::json page = ResponseJson(list)["data"];
    CHECK(page["total"] == 1);
    CHECK(page["items"][0]["id"] == file_id);

    HttpResponse versions = application.Prepare(GetHead(
        "/api/v1/projects/" + alpha.id + "/files/" + file_id +
            "/versions",
        tokens.raw_token))->Finish();
    CHECK(versions.status() == 200);
    CHECK(ResponseJson(versions)["data"]["items"][0]["id"] == version_id);

    CHECK_THROWS_CODE(
        application.Prepare(GetHead(
            "/api/v1/projects/" + alpha.id + "/files", tokens.raw_token,
            "page_size=101")),
        "invalid_request");
    CHECK_THROWS_CODE(
        application.Prepare(GetHead(
            "/api/v1/projects/" + alpha.id + "/files/" + file_id +
                "/content",
            tokens.raw_token)),
        "not_implemented");
    CHECK_THROWS_CODE(
        application.Prepare(GetHead(
            "/api/v1/projects/" + alpha.id + "/files/" + file_id +
                "/versions/" + version_id + "/content",
            tokens.raw_token)),
        "not_implemented");
    CHECK(application.ErrorResponse(
              AppError(501, "not_implemented", "not implemented"))
              .status() == 501);
}
