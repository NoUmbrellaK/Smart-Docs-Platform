#include "auth/auth_service.h"
#include "app/application.h"
#include "core/app_error.h"
#include "http/httpconn.h"
#include "project/project_service.h"
#include "server/webserver.h"
#include "../mysql_test_support.h"
#include "../test_support.h"

#include <algorithm>
#include <arpa/inet.h>
#include <cerrno>
#include <cstdlib>
#include <memory>
#include <nlohmann/json.hpp>
#include <string>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

namespace {

const char* kPassword = "correct horse battery";
const char* kRequestA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const char* kRequestB = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

bool HasProject(const std::vector<ProjectMembership>& memberships,
                const std::string& project_id, Role role) {
    return std::any_of(memberships.begin(), memberships.end(),
                       [&](const ProjectMembership& membership) {
        return membership.project.id == project_id && membership.role == role;
    });
}

class TemporaryStorage {
public:
    TemporaryStorage() {
        char pattern[] = "/tmp/smart-docs-auth-http.XXXXXX";
        char* created = mkdtemp(pattern);
        CHECK(created != nullptr);
        path_ = created;
        CHECK(mkdir((path_ + "/objects").c_str(), 0700) == 0);
        CHECK(mkdir((path_ + "/staging").c_str(), 0700) == 0);
    }

    ~TemporaryStorage() {
        rmdir((path_ + "/objects").c_str());
        rmdir((path_ + "/staging").c_str());
        rmdir(path_.c_str());
    }

    const std::string& path() const { return path_; }

private:
    std::string path_;
};

std::string Exchange(const std::shared_ptr<Application>& application,
                     const std::string& request) {
    int sockets[2];
    CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
    CHECK(WebServer::SetFdNonblock(sockets[0]) == 0);
    sockaddr_in address{};
    address.sin_family = AF_INET;
    HttpConn connection(application);
    connection.init(sockets[0], address);
    CHECK(::write(sockets[1], request.data(), request.size()) ==
          static_cast<ssize_t>(request.size()));
    int saved_errno = 0;
    CHECK(connection.read(&saved_errno) > 0);
    CHECK(connection.process());
    while (connection.ToWriteBytes() != 0) {
        CHECK(connection.write(&saved_errno) >= 0);
    }
    char response[16384];
    const ssize_t size = ::read(sockets[1], response, sizeof(response));
    CHECK(size > 0);
    connection.Close();
    close(sockets[1]);
    return std::string(response, static_cast<size_t>(size));
}

std::string JsonRequest(const std::string& method, const std::string& path,
                        const std::string& body,
                        const std::string& cookie = std::string(),
                        const std::string& origin = "https://smartdocs.test") {
    std::string request = method + " " + path +
        " HTTP/1.1\r\nHost: smartdocs.test\r\nOrigin: " + origin +
        "\r\nContent-Type: application/json\r\nContent-Length: " +
        std::to_string(body.size()) + "\r\nConnection: close\r\n";
    if (!cookie.empty()) {
        request += "Cookie: " + cookie + "\r\n";
    }
    return request + "\r\n" + body;
}

std::string GetRequest(const std::string& path,
                       const std::string& cookie = std::string()) {
    std::string request = "GET " + path +
        " HTTP/1.1\r\nHost: smartdocs.test\r\nConnection: close\r\n";
    if (!cookie.empty()) {
        request += "Cookie: " + cookie + "\r\n";
    }
    return request + "\r\n";
}

nlohmann::json ResponseJson(const std::string& response) {
    const size_t body = response.find("\r\n\r\n");
    CHECK(body != std::string::npos);
    return nlohmann::json::parse(response.substr(body + 4));
}

std::string SessionCookie(const std::string& response) {
    const std::string prefix = "Set-Cookie: smartdocs_session=";
    const size_t start = response.find(prefix);
    CHECK(start != std::string::npos);
    const size_t value_start = start + prefix.size();
    const size_t end = response.find(';', value_start);
    CHECK(end != std::string::npos);
    return "smartdocs_session=" +
           response.substr(value_start, end - value_start);
}

}  // namespace

TEST_CASE(auth_project_sessions_reject_invalid_expired_and_revoked_tokens) {
    RequireMySqlTests();
    ResetTestDatabase();
    AuthService auth(TestDatabase(), 60, 1000);

    const UserIdentity user = auth.CreateUser("admin", kPassword, kRequestA);
    CHECK(user.username == "admin");
    CHECK_THROWS_CODE(auth.CreateUser("ADMIN", kPassword, kRequestB),
                      "username_conflict");
    CHECK_THROWS_CODE(auth.Login("admin", "wrong password", kRequestB),
                      "invalid_credentials");
    CHECK_THROWS_CODE(auth.Authenticate(""), "authentication_required");
    CHECK_THROWS_CODE(auth.Authenticate("not-a-session-token"),
                      "authentication_required");

    SessionTokens expired = auth.Login("admin", kPassword, kRequestA);
    CHECK(expired.raw_token.size() == 64);
    CHECK(expired.user.id == user.id);
    {
        MySqlConnection connection = TestDatabase().Acquire();
        connection.Execute(
            "UPDATE auth_sessions SET expires_at="
            "DATE_SUB(UTC_TIMESTAMP(6), INTERVAL 1 SECOND) WHERE id=?",
            {SqlValue(expired.session.session_id)});
    }
    CHECK_THROWS_CODE(auth.Authenticate(expired.raw_token),
                      "authentication_required");

    SessionTokens active = auth.Login("admin", kPassword, kRequestA);
    SessionContext authenticated = auth.Authenticate(active.raw_token);
    CHECK(authenticated.user_id == user.id);
    CHECK(authenticated.session_id == active.session.session_id);
    auth.Logout(active.raw_token, kRequestB);
    CHECK_THROWS_CODE(auth.Authenticate(active.raw_token),
                      "authentication_required");
}

TEST_CASE(auth_project_membership_is_project_scoped_and_directories_are_checked) {
    RequireMySqlTests();
    ResetTestDatabase();
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());

    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequestA);
    const UserIdentity editor = auth.CreateUser("editor", kPassword, kRequestA);
    const UserIdentity reader = auth.CreateUser("reader", kPassword, kRequestA);
    const SessionContext admin_session =
        auth.Login("admin", kPassword, kRequestA).session;
    const SessionContext editor_session =
        auth.Login("editor", kPassword, kRequestA).session;
    const SessionContext reader_session =
        auth.Login("reader", kPassword, kRequestA).session;

    const Project alpha =
        projects.CreateProject(admin.id, "Alpha", kRequestA);
    const Project beta =
        projects.CreateProject(reader.id, "Beta", kRequestA);
    projects.SetMemberRole(admin_session, alpha.id, editor.id, Role::Editor,
                           kRequestA);
    projects.SetMemberRole(admin_session, alpha.id, reader.id, Role::Reader,
                           kRequestA);

    CHECK(projects.RequireRole(editor.id, alpha.id, Role::Reader) ==
          Role::Editor);
    CHECK_THROWS_CODE(
        projects.RequireRole(editor.id, beta.id, Role::Reader),
        "resource_not_found");
    CHECK_THROWS_CODE(
        projects.SetMemberRole(reader_session, alpha.id, reader.id,
                               Role::Admin, kRequestB),
        "forbidden");
    {
        MySqlConnection connection = TestDatabase().Acquire();
        CHECK(connection.ScalarInt(
                  "SELECT COUNT(*) FROM audit_records WHERE request_id=? "
                  "AND action='project.member.set'",
                  {SqlValue(kRequestB)}) == 0);
    }

    const std::vector<ProjectMembership> editor_projects =
        projects.ListForUser(editor.id);
    CHECK(editor_projects.size() == 1);
    CHECK(HasProject(editor_projects, alpha.id, Role::Editor));

    const std::vector<ProjectMember> members =
        projects.ListMembers(admin_session, alpha.id);
    CHECK(members.size() == 3);

    const Directory design = projects.CreateDirectory(
        admin_session, alpha.id, alpha.root_directory_id, "Design", kRequestA);
    CHECK(design.parent_id == alpha.root_directory_id);
    const Directory renamed = projects.RenameDirectory(
        admin_session, alpha.id, design.id, "Architecture", kRequestB);
    CHECK(renamed.name == "Architecture");
    CHECK(projects.ListDirectories(reader_session, alpha.id).size() == 2);
    CHECK_THROWS_CODE(
        projects.CreateDirectory(admin_session, alpha.id,
                                 beta.root_directory_id, "Wrong project",
                                 kRequestB),
        "resource_not_found");
    CHECK_THROWS_CODE(
        projects.CreateDirectory(editor_session, alpha.id,
                                 alpha.root_directory_id, "Editor directory",
                                 kRequestB),
        "forbidden");

    MySqlConnection connection = TestDatabase().Acquire();
    CHECK(connection.ScalarInt(
              "SELECT COUNT(*) FROM audit_records WHERE request_id=?",
              {SqlValue(kRequestA)}) >= 1);
    CHECK(connection.ScalarInt(
              "SELECT COUNT(*) FROM audit_records WHERE request_id=? "
              "AND action='directory.rename'",
              {SqlValue(kRequestB)}) == 1);
}

TEST_CASE(auth_project_http_routes_form_a_same_origin_vertical_slice) {
    RequireMySqlTests();
    ResetTestDatabase();
    AuthService bootstrap(TestDatabase(), 60, 1000);
    bootstrap.CreateUser("admin", kPassword, kRequestA);
    const UserIdentity editor =
        bootstrap.CreateUser("editor", kPassword, kRequestA);

    TemporaryStorage storage;
    AppConfig config = TestAppConfig(storage.path(), 2);
    config.secure_cookie = true;
    std::shared_ptr<Application> application(new Application(config));

    const std::string login_body =
        "{\"username\":\"admin\",\"password\":\"correct horse battery\"}";
    const std::string login = Exchange(
        application, JsonRequest("POST", "/api/v1/auth/login", login_body));
    CHECK(login.find("HTTP/1.1 200 OK\r\n") == 0);
    CHECK(login.find("HttpOnly; SameSite=Strict; Path=/; Max-Age=60") !=
          std::string::npos);
    CHECK(login.find("; Secure\r\n") != std::string::npos);
    CHECK(login.find("Access-Control-Allow-Credentials") == std::string::npos);
    const std::string cookie = SessionCookie(login);

    const std::string unknown = Exchange(
        application,
        JsonRequest("POST", "/api/v1/projects",
                    "{\"name\":\"Alpha\",\"unexpected\":true}", cookie));
    CHECK(unknown.find("HTTP/1.1 400 Bad Request\r\n") == 0);
    CHECK(unknown.find("\"code\":\"invalid_request\"") !=
          std::string::npos);

    const std::string project_response = Exchange(
        application,
        JsonRequest("POST", "/api/v1/projects", "{\"name\":\"Alpha\"}",
                    cookie));
    CHECK(project_response.find("HTTP/1.1 201 Created\r\n") == 0);
    const nlohmann::json project_data = ResponseJson(project_response)["data"];
    const std::string project_id = project_data["id"].get<std::string>();
    const std::string root_id =
        project_data["root_directory_id"].get<std::string>();

    const std::string me =
        Exchange(application, GetRequest("/api/v1/me", cookie));
    CHECK(me.find("HTTP/1.1 200 OK\r\n") == 0);
    CHECK(me.find("\"role\":\"admin\"") != std::string::npos);
    const std::string duplicate_cookie = Exchange(
        application,
        GetRequest("/api/v1/me", "smartdocs_session=; " + cookie));
    CHECK(duplicate_cookie.find("HTTP/1.1 401 Unauthorized\r\n") == 0);

    const std::string member = Exchange(
        application,
        JsonRequest("PUT", "/api/v1/projects/" + project_id + "/members/" +
                               editor.id,
                    "{\"role\":\"editor\"}", cookie));
    CHECK(member.find("HTTP/1.1 200 OK\r\n") == 0);
    const std::string invalid_role = Exchange(
        application,
        JsonRequest("PUT", "/api/v1/projects/" + project_id + "/members/" +
                               editor.id,
                    "{\"role\":\"owner\"}", cookie));
    CHECK(invalid_role.find("HTTP/1.1 400 Bad Request\r\n") == 0);
    CHECK(invalid_role.find("\"code\":\"invalid_request\"") !=
          std::string::npos);
    const std::string invalid_member_id = Exchange(
        application,
        JsonRequest("PUT", "/api/v1/projects/" + project_id +
                               "/members/not-an-id",
                    "{\"role\":\"reader\"}", cookie));
    CHECK(invalid_member_id.find("HTTP/1.1 400 Bad Request\r\n") == 0);
    const std::string members = Exchange(
        application,
        GetRequest("/api/v1/projects/" + project_id + "/members", cookie));
    CHECK(members.find("HTTP/1.1 200 OK\r\n") == 0);
    CHECK(members.find("\"username\":\"editor\"") != std::string::npos);

    const std::string beta_response = Exchange(
        application,
        JsonRequest("POST", "/api/v1/projects", "{\"name\":\"Beta\"}",
                    cookie));
    const std::string beta_id =
        ResponseJson(beta_response)["data"]["id"].get<std::string>();
    const std::string editor_login = Exchange(
        application,
        JsonRequest("POST", "/api/v1/auth/login",
                    "{\"username\":\"editor\",\"password\":"
                    "\"correct horse battery\"}"));
    const std::string editor_cookie = SessionCookie(editor_login);
    const std::string cross_project = Exchange(
        application,
        GetRequest("/api/v1/projects/" + beta_id + "/directories",
                   editor_cookie));
    CHECK(cross_project.find("HTTP/1.1 404 Not Found\r\n") == 0);
    CHECK(cross_project.find("\"code\":\"resource_not_found\"") !=
          std::string::npos);

    const std::string directory_response = Exchange(
        application,
        JsonRequest("POST", "/api/v1/projects/" + project_id +
                                "/directories",
                    "{\"parent_id\":\"" + root_id +
                        "\",\"name\":\"Design\"}",
                    cookie));
    CHECK(directory_response.find("HTTP/1.1 201 Created\r\n") == 0);
    const std::string invalid_parent = Exchange(
        application,
        JsonRequest("POST", "/api/v1/projects/" + project_id +
                                "/directories",
                    "{\"parent_id\":\"bad\",\"name\":\"Invalid\"}",
                    cookie));
    CHECK(invalid_parent.find("HTTP/1.1 400 Bad Request\r\n") == 0);
    CHECK(invalid_parent.find("\"code\":\"invalid_request\"") !=
          std::string::npos);
    const std::string directory_id =
        ResponseJson(directory_response)["data"]["id"].get<std::string>();
    const std::string renamed = Exchange(
        application,
        JsonRequest("PATCH", "/api/v1/projects/" + project_id +
                                 "/directories/" + directory_id,
                    "{\"name\":\"Architecture\"}", cookie));
    CHECK(renamed.find("HTTP/1.1 200 OK\r\n") == 0);
    const std::string directories = Exchange(
        application,
        GetRequest("/api/v1/projects/" + project_id + "/directories", cookie));
    CHECK(directories.find("\"name\":\"Architecture\"") !=
          std::string::npos);

    const std::string wrong_origin = Exchange(
        application,
        JsonRequest("POST", "/api/v1/projects", "{\"name\":\"Blocked\"}",
                    cookie, "https://evil.test"));
    CHECK(wrong_origin.find("HTTP/1.1 403 Forbidden\r\n") == 0);
    const std::string missing_cookie =
        Exchange(application, GetRequest("/api/v1/me"));
    CHECK(missing_cookie.find("HTTP/1.1 401 Unauthorized\r\n") == 0);

    const std::string logout = Exchange(
        application,
        JsonRequest("POST", "/api/v1/auth/logout", "{}", cookie));
    CHECK(logout.find("HTTP/1.1 204 No Content\r\n") == 0);
    CHECK(logout.find("X-Request-ID: ") != std::string::npos);
    CHECK(logout.find("Max-Age=0") != std::string::npos);
    const std::string logged_out =
        Exchange(application, GetRequest("/api/v1/me", cookie));
    CHECK(logged_out.find("HTTP/1.1 401 Unauthorized\r\n") == 0);
}
