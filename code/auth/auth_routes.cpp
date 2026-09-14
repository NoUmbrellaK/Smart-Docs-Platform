#include "auth_routes.h"

#include "auth_service.h"
#include "core/app_error.h"
#include "core/id.h"
#include "http/jsonbody.h"
#include "project/project_service.h"

#include <algorithm>
#include <cctype>
#include <nlohmann/json.hpp>
#include <utility>

namespace {

std::string Lower(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(),
                   [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return value;
}

std::string Trim(const std::string& value) {
    size_t first = 0;
    while (first < value.size() &&
           (value[first] == ' ' || value[first] == '\t')) {
        ++first;
    }
    size_t last = value.size();
    while (last > first &&
           (value[last - 1] == ' ' || value[last - 1] == '\t')) {
        --last;
    }
    return value.substr(first, last - first);
}

nlohmann::json ProjectJson(const ProjectMembership& membership) {
    return {{"id", membership.project.id},
            {"name", membership.project.name},
            {"root_directory_id", membership.project.root_directory_id},
            {"role", RoleName(membership.role)}};
}

nlohmann::json IdentityData(AuthService& auth, ProjectService& projects,
                            const SessionContext& session) {
    const UserIdentity user = auth.GetUser(session.user_id);
    nlohmann::json project_items = nlohmann::json::array();
    for (const ProjectMembership& membership :
         projects.ListForUser(session.user_id)) {
        project_items.push_back(ProjectJson(membership));
    }
    return {{"user", {{"id", user.id}, {"username", user.username}}},
            {"expires_at", session.expires_at},
            {"projects", std::move(project_items)}};
}

HttpResponse DataResponse(int status, nlohmann::json data,
                          const std::string& request_id, bool keep_alive,
                          HttpResponse::Headers headers = {}) {
    return HttpResponse::Json(status,
                              {{"data", std::move(data)},
                               {"request_id", request_id}},
                              keep_alive, std::move(headers));
}

std::string SessionCookieValue(const std::string& token, bool secure,
                               int maximum_age) {
    std::string value = "smartdocs_session=" + token +
        "; HttpOnly; SameSite=Strict; Path=/; Max-Age=" +
        std::to_string(maximum_age);
    if (secure) {
        value += "; Secure";
    }
    return value;
}

}  // namespace

std::string RequireSessionCookie(const RequestHead& head) {
    const auto found = head.headers.find("cookie");
    if (found == head.headers.end()) {
        throw AppError(401, "authentication_required",
                       "a valid session is required");
    }
    std::string token;
    bool seen_session_cookie = false;
    size_t start = 0;
    while (start <= found->second.size()) {
        const size_t semicolon = found->second.find(';', start);
        const size_t end = semicolon == std::string::npos
                               ? found->second.size() : semicolon;
        const std::string pair = Trim(found->second.substr(start, end - start));
        const size_t equals = pair.find('=');
        if (equals != std::string::npos &&
            pair.substr(0, equals) == "smartdocs_session") {
            if (seen_session_cookie) {
                throw AppError(401, "authentication_required",
                               "a valid session is required");
            }
            seen_session_cookie = true;
            token = pair.substr(equals + 1);
        }
        if (semicolon == std::string::npos) {
            break;
        }
        start = semicolon + 1;
    }
    if (token.empty()) {
        throw AppError(401, "authentication_required",
                       "a valid session is required");
    }
    return token;
}

void RequireSameOrigin(const RequestHead& head, bool secure_cookie) {
    const auto host = head.headers.find("host");
    if (host == head.headers.end() || host->second.empty()) {
        throw AppError(400, "host_required", "Host header is required");
    }
    const auto origin = head.headers.find("origin");
    if (origin == head.headers.end()) {
        throw AppError(403, "origin_mismatch",
                       "request Origin does not match Host");
    }
    const std::string expected =
        std::string(secure_cookie ? "https://" : "http://") + host->second;
    if (Lower(origin->second) != Lower(expected)) {
        throw AppError(403, "origin_mismatch",
                       "request Origin does not match Host");
    }
}

void RegisterAuthRoutes(Router& router,
                        const std::shared_ptr<AuthService>& auth,
                        const std::shared_ptr<ProjectService>& projects,
                        uint64_t maximum_json_bytes, bool secure_cookie,
                        int session_seconds) {
    router.Add("POST", "/api/v1/auth/login",
        [auth, projects, maximum_json_bytes, secure_cookie, session_seconds](
            const RequestHead& head, const RouteParams&) {
        RequireSameOrigin(head, secure_cookie);
        return PrepareJsonBody(
            head, maximum_json_bytes,
            [auth, projects, keep_alive = head.keep_alive, secure_cookie,
             session_seconds](const nlohmann::json& body) {
            RequireExactObject(body, {"username", "password"});
            if (!body["username"].is_string() ||
                !body["password"].is_string()) {
                throw AppError(400, "invalid_request",
                               "username and password must be strings");
            }
            const std::string request_id = GenerateId();
            const SessionTokens tokens = auth->Login(
                body["username"].get<std::string>(),
                body["password"].get<std::string>(), request_id);
            return DataResponse(
                200, IdentityData(*auth, *projects, tokens.session), request_id,
                keep_alive,
                {{"Set-Cookie", SessionCookieValue(tokens.raw_token,
                                                     secure_cookie,
                                                     session_seconds)}});
        });
    });

    router.Add("POST", "/api/v1/auth/logout",
        [auth, maximum_json_bytes, secure_cookie](
            const RequestHead& head, const RouteParams&) {
        RequireSameOrigin(head, secure_cookie);
        const std::string token = RequireSessionCookie(head);
        return PrepareJsonBody(
            head, maximum_json_bytes,
            [auth, token, keep_alive = head.keep_alive,
             secure_cookie](const nlohmann::json& body) {
            RequireExactObject(body, {});
            const std::string request_id = GenerateId();
            auth->Logout(token, request_id);
            return HttpResponse::Empty(
                204, keep_alive,
                {{"X-Request-ID", request_id},
                 {"Set-Cookie", SessionCookieValue(std::string(),
                                                    secure_cookie, 0)}});
        });
    });

    router.Add("GET", "/api/v1/me",
        [auth, projects](const RequestHead& head, const RouteParams&) {
        const SessionContext session =
            auth->Authenticate(RequireSessionCookie(head));
        const std::string request_id = GenerateId();
        return ReadyResponse(DataResponse(
            200, IdentityData(*auth, *projects, session), request_id,
            head.keep_alive));
    });
}
