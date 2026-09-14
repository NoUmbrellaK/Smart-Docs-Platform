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
        char pattern[] = "/tmp/smart-docs-file-download.XXXXXX";
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
    if (content.empty()) {
        store.PublishObject(store.Assemble(task_id, {}), content_id);
        return;
    }
    PartWriter writer = store.CreatePartWriter(task_id, 0);
    writer.Write(content.data(), content.size());
    const PartInfo part = writer.Finish();
    store.PublishObject(store.Assemble(task_id, {part}), content_id);
}

void InsertFileAndVersions(
    const std::string& file_id, const std::string& project_id,
    const std::string& directory_id, const std::string& name,
    const std::string& creator_id, const std::string& first_version,
    const std::string& first_content, uint64_t first_size,
    const std::string& first_media, const std::string& second_version,
    const std::string& second_content, uint64_t second_size,
    const std::string& second_media, bool current_is_second = false) {
    MySqlConnection connection = TestDatabase().Acquire();
    MySqlTransaction transaction(connection);
    connection.Execute(
        "INSERT INTO files(id, project_id, directory_id, name, created_by) "
        "VALUES (?, ?, ?, ?, ?)",
        {SqlValue(file_id), SqlValue(project_id), SqlValue(directory_id),
         SqlValue(name), SqlValue(creator_id)});
    connection.Execute(
        "INSERT INTO file_versions(id, file_id, version_number, content_id, "
        "size_bytes, sha256, media_type, created_by) VALUES (?, ?, 1, ?, ?, "
        "?, ?, ?)",
        {SqlValue(first_version), SqlValue(file_id), SqlValue(first_content),
         SqlValue(first_size), SqlValue(std::string(64, '0')),
         SqlValue(first_media), SqlValue(creator_id)});
    if (!second_version.empty()) {
        connection.Execute(
            "INSERT INTO file_versions(id, file_id, version_number, content_id, "
            "size_bytes, sha256, media_type, created_by) VALUES (?, ?, 2, ?, "
            "?, ?, ?, ?)",
            {SqlValue(second_version), SqlValue(file_id), SqlValue(second_content),
             SqlValue(second_size), SqlValue(std::string(64, '1')),
             SqlValue(second_media), SqlValue(creator_id)});
    }
    connection.Execute("UPDATE files SET current_version_id=? WHERE id=?",
                       {SqlValue(current_is_second ? second_version
                                                   : first_version),
                        SqlValue(file_id)});
    transaction.Commit();
}

std::string ReadAll(int fd) {
    std::string result;
    char buffer[16];
    while (true) {
        const ssize_t count = read(fd, buffer, sizeof(buffer));
        if (count < 0 && errno == EINTR) continue;
        CHECK(count >= 0);
        if (count == 0) return result;
        result.append(buffer, static_cast<size_t>(count));
    }
}

RequestHead GetHead(const std::string& path, const std::string& token,
                    const std::string& range = std::string()) {
    RequestHead head{};
    head.method = "GET";
    head.path = path;
    head.version = "1.1";
    head.headers.emplace("host", "smartdocs.test");
    head.headers.emplace("cookie", "smartdocs_session=" + token);
    if (!range.empty()) head.headers.emplace("range", range);
    return head;
}

std::string Header(const HttpResponse& response, const std::string& name) {
    const std::string prefix = name + ": ";
    const std::string& wire = response.head_and_body();
    const size_t start = wire.find("\r\n" + prefix);
    CHECK(start != std::string::npos);
    const size_t value_start = start + 2 + prefix.size();
    const size_t end = wire.find("\r\n", value_start);
    CHECK(end != std::string::npos);
    return wire.substr(value_start, end - value_start);
}

nlohmann::json ResponseJson(const HttpResponse& response) {
    const std::string& wire = response.head_and_body();
    const size_t body = wire.find("\r\n\r\n");
    CHECK(body != std::string::npos);
    return nlohmann::json::parse(wire.substr(body + 4));
}

}  // namespace

TEST_CASE(file_download_current_binds_one_immutable_version_and_history_is_exact) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    FileStore store(storage.path());
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    FileService files(TestDatabase(), projects, store);
    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const SessionContext session = auth.Login("admin", kPassword, kRequest).session;
    const Project project = projects.CreateProject(admin.id, "Alpha", kRequest);

    const std::string file_id = "40404040404040404040404040404040";
    const std::string v1 = "41414141414141414141414141414141";
    const std::string v2 = "42424242424242424242424242424242";
    const std::string c1 = "aabb4343434343434343434343434343";
    const std::string c2 = "aabb4444444444444444444444444444";
    Publish(store, "45454545454545454545454545454545", c1, "old bytes");
    Publish(store, "46464646464646464646464646464646", c2, "new bytes");
    InsertFileAndVersions(file_id, project.id, project.root_directory_id,
                          "history.txt", admin.id, v1, c1, 9, "text/plain",
                          v2, c2, 9, "text/plain");

    AuthorizedVersion opened =
        files.OpenCurrentVersion(session, project.id, file_id);
    CHECK(opened.version.id == v1);
    {
        MySqlConnection connection = TestDatabase().Acquire();
        connection.Execute("UPDATE files SET current_version_id=? WHERE id=?",
                           {SqlValue(v2), SqlValue(file_id)});
    }
    CHECK(ReadAll(opened.fd) == "old bytes");

    AuthorizedVersion historical =
        files.OpenVersion(session, project.id, file_id, v1);
    CHECK(historical.version.id == v1);
    CHECK(ReadAll(historical.fd) == "old bytes");
    AuthorizedVersion current =
        files.OpenCurrentVersion(session, project.id, file_id);
    CHECK(current.version.id == v2);
    CHECK(ReadAll(current.fd) == "new bytes");
}

TEST_CASE(file_download_authorizes_and_rejects_deleted_files_before_object_open) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    FileStore store(storage.path());
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    FileService files(TestDatabase(), projects, store);
    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const UserIdentity outsider = auth.CreateUser("outsider", kPassword, kRequest);
    const SessionContext session = auth.Login("admin", kPassword, kRequest).session;
    const SessionContext outside = auth.Login("outsider", kPassword, kRequest).session;
    const Project project = projects.CreateProject(admin.id, "Alpha", kRequest);
    projects.CreateProject(outsider.id, "Beta", kRequest);
    const std::string file_id = "50505050505050505050505050505050";
    const std::string version = "51515151515151515151515151515151";
    InsertFileAndVersions(
        file_id, project.id, project.root_directory_id, "missing.txt", admin.id,
        version, "aabb5252525252525252525252525252", 7, "text/plain", "",
        "", 0, "");

    CHECK_THROWS_CODE(
        files.OpenCurrentVersion(outside, project.id, file_id),
        "resource_not_found");
    files.SoftDelete(session, project.id, file_id);
    CHECK_THROWS_CODE(
        files.OpenCurrentVersion(session, project.id, file_id),
        "resource_not_found");
    CHECK_THROWS_CODE(
        files.OpenVersion(session, project.id, file_id, version),
        "resource_not_found");
}

TEST_CASE(file_download_http_serves_ranges_safe_disposition_pdf_and_errors) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    FileStore store(storage.path());
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const SessionTokens tokens = auth.Login("admin", kPassword, kRequest);
    const Project project = projects.CreateProject(admin.id, "Alpha", kRequest);

    const std::string pdf_file = "60606060606060606060606060606060";
    const std::string pdf_version = "61616161616161616161616161616161";
    const std::string pdf_content = "aabb6262626262626262626262626262";
    Publish(store, "63636363636363636363636363636363", pdf_content,
            "0123456789");
    InsertFileAndVersions(pdf_file, project.id, project.root_directory_id,
                          "r\xc3\xa9sum\xc3\xa9 \"2026\".pdf", admin.id,
                          pdf_version, pdf_content, 10, "application/pdf", "",
                          "", 0, "");
    const std::string html_file = "64646464646464646464646464646464";
    const std::string html_version = "65656565656565656565656565656565";
    const std::string html_content = "aabb6666666666666666666666666666";
    Publish(store, "67676767676767676767676767676767", html_content,
            "<script>");
    InsertFileAndVersions(html_file, project.id, project.root_directory_id,
                          "danger.html", admin.id, html_version, html_content,
                          8, "text/html", "", "", 0, "");
    const std::string empty_file = "68686868686868686868686868686868";
    const std::string empty_version = "69696969696969696969696969696969";
    const std::string empty_content = "aabb7070707070707070707070707070";
    Publish(store, "71717171717171717171717171717171", empty_content, "");
    InsertFileAndVersions(empty_file, project.id, project.root_directory_id,
                          "empty.bin", admin.id, empty_version, empty_content,
                          0, "application/octet-stream", "", "", 0, "");

    Application application(TestAppConfig(storage.path(), 2));
    const std::string pdf_path = "/api/v1/projects/" + project.id + "/files/" +
                                 pdf_file + "/content";
    HttpResponse partial =
        application.Prepare(GetHead(pdf_path, tokens.raw_token, "bytes=2-5"))
            ->Finish();
    CHECK(partial.status() == 206);
    CHECK(partial.file_region().offset == 2);
    CHECK(partial.file_region().length == 4);
    CHECK(Header(partial, "Accept-Ranges") == "bytes");
    CHECK(Header(partial, "Content-Range") == "bytes 2-5/10");
    CHECK(Header(partial, "Content-Type") == "application/pdf");
    CHECK(Header(partial, "Content-Disposition") ==
          "inline; filename*=UTF-8''r%C3%A9sum%C3%A9%20%222026%22.pdf");
    CHECK(Header(partial, "X-Content-Type-Options") == "nosniff");

    HttpResponse full = application.Prepare(GetHead(
        "/api/v1/projects/" + project.id + "/files/" + html_file +
            "/versions/" + html_version + "/content",
        tokens.raw_token))->Finish();
    CHECK(full.status() == 200);
    CHECK(full.file_region().offset == 0);
    CHECK(full.file_region().length == 8);
    CHECK(Header(full, "Content-Disposition") ==
          "attachment; filename*=UTF-8''danger.html");
    CHECK(Header(full, "Content-Type") == "text/html");

    HttpResponse empty = application.Prepare(GetHead(
        "/api/v1/projects/" + project.id + "/files/" + empty_file +
            "/content",
        tokens.raw_token))->Finish();
    CHECK(empty.status() == 200);
    CHECK(empty.file_region().offset == 0);
    CHECK(empty.file_region().length == 0);
    CHECK(Header(empty, "Content-Length") == "0");

    HttpResponse invalid = application.Prepare(
        GetHead(pdf_path, tokens.raw_token, "bytes=99-100"))->Finish();
    CHECK(invalid.status() == 416);
    CHECK(Header(invalid, "Content-Range") == "bytes */10");
    CHECK(ResponseJson(invalid)["error"]["code"] == "range_not_satisfiable");
    CHECK(invalid.file_region().fd == -1);

    HttpResponse multiple = application.Prepare(
        GetHead(pdf_path, tokens.raw_token, "bytes=0-1,3-4"))->Finish();
    CHECK(multiple.status() == 501);
    CHECK(ResponseJson(multiple)["error"]["code"] == "range_not_supported");
    CHECK(multiple.file_region().fd == -1);
}
