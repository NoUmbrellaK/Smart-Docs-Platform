#include "project_routes.h"

#include "auth/auth_routes.h"
#include "auth/auth_service.h"
#include "core/app_error.h"
#include "core/id.h"
#include "http/jsonbody.h"
#include "project_service.h"

#include <cctype>
#include <nlohmann/json.hpp>
#include <utility>

namespace {

nlohmann::json ProjectJson(const Project& project) {
    return {{"id", project.id}, {"name", project.name},
            {"root_directory_id", project.root_directory_id}};
}

nlohmann::json DirectoryJson(const Directory& directory) {
    return {{"id", directory.id},
            {"project_id", directory.project_id},
            {"parent_id", directory.parent_id.empty()
                              ? nlohmann::json(nullptr)
                              : nlohmann::json(directory.parent_id)},
            {"name", directory.name}};
}

HttpResponse DataResponse(int status, nlohmann::json data,
                          const std::string& request_id, bool keep_alive) {
    return HttpResponse::Json(status,
                              {{"data", std::move(data)},
                               {"request_id", request_id}},
                              keep_alive);
}

SessionContext Authenticate(AuthService& auth, const RequestHead& head) {
    return auth.Authenticate(RequireSessionCookie(head));
}

std::string Parameter(const RouteParams& params, const char* name) {
    const auto found = params.find(name);
    if (found == params.end()) {
        throw AppError(500, "route_parameter_missing",
                       "route parameter is unavailable");
    }
    return found->second;
}

std::string EntityId(const std::string& value) {
    if (value.size() != 32) {
        throw AppError(400, "invalid_request", "entity ID is invalid");
    }
    for (unsigned char ch : value) {
        if (!std::isdigit(ch) && (ch < 'a' || ch > 'f')) {
            throw AppError(400, "invalid_request", "entity ID is invalid");
        }
    }
    return value;
}

std::string EntityIdParameter(const RouteParams& params, const char* name) {
    return EntityId(Parameter(params, name));
}

Role RequestedRole(const nlohmann::json& value) {
    if (!value.is_string()) {
        throw AppError(400, "invalid_request", "role must be a string");
    }
    const std::string role = value.get<std::string>();
    if (role != "reader" && role != "editor" && role != "admin") {
        throw AppError(400, "invalid_request", "role is invalid");
    }
    return ParseRole(role);
}

}  // namespace

void RegisterProjectRoutes(Router& router,
                           const std::shared_ptr<AuthService>& auth,
                           const std::shared_ptr<ProjectService>& projects,
                           uint64_t maximum_json_bytes, bool secure_cookie) {
    router.Add("POST", "/api/v1/projects",
        [auth, projects, maximum_json_bytes, secure_cookie](
            const RequestHead& head, const RouteParams&) {
        RequireSameOrigin(head, secure_cookie);
        const SessionContext session = Authenticate(*auth, head);
        return PrepareJsonBody(
            head, maximum_json_bytes,
            [projects, session, keep_alive = head.keep_alive](
                const nlohmann::json& body) {
            RequireExactObject(body, {"name"});
            if (!body["name"].is_string()) {
                throw AppError(400, "invalid_request", "name must be a string");
            }
            const std::string request_id = GenerateId();
            const Project project = projects->CreateProject(
                session.user_id, body["name"].get<std::string>(), request_id);
            return DataResponse(201, ProjectJson(project), request_id,
                                keep_alive);
        });
    });

    router.Add("GET", "/api/v1/projects/{project_id}/members",
        [auth, projects](const RequestHead& head, const RouteParams& params) {
        const SessionContext session = Authenticate(*auth, head);
        nlohmann::json items = nlohmann::json::array();
        for (const ProjectMember& member : projects->ListMembers(
                 session, EntityIdParameter(params, "project_id"))) {
            items.push_back({{"user_id", member.user_id},
                             {"username", member.username},
                             {"role", RoleName(member.role)}});
        }
        const std::string request_id = GenerateId();
        return ReadyResponse(DataResponse(200, {{"items", std::move(items)}},
                                          request_id, head.keep_alive));
    });

    router.Add("PUT", "/api/v1/projects/{project_id}/members/{user_id}",
        [auth, projects, maximum_json_bytes, secure_cookie](
            const RequestHead& head, const RouteParams& params) {
        RequireSameOrigin(head, secure_cookie);
        const SessionContext session = Authenticate(*auth, head);
        const std::string project_id =
            EntityIdParameter(params, "project_id");
        const std::string user_id = EntityIdParameter(params, "user_id");
        return PrepareJsonBody(
            head, maximum_json_bytes,
            [projects, session, project_id, user_id,
             keep_alive = head.keep_alive](const nlohmann::json& body) {
            RequireExactObject(body, {"role"});
            const Role role = RequestedRole(body["role"]);
            const std::string request_id = GenerateId();
            projects->SetMemberRole(session, project_id, user_id, role,
                                    request_id);
            return DataResponse(200, {{"user_id", user_id},
                                      {"role", RoleName(role)}},
                                request_id, keep_alive);
        });
    });

    router.Add("GET", "/api/v1/projects/{project_id}/directories",
        [auth, projects](const RequestHead& head, const RouteParams& params) {
        const SessionContext session = Authenticate(*auth, head);
        nlohmann::json items = nlohmann::json::array();
        for (const Directory& directory : projects->ListDirectories(
                 session, EntityIdParameter(params, "project_id"))) {
            items.push_back(DirectoryJson(directory));
        }
        const std::string request_id = GenerateId();
        return ReadyResponse(DataResponse(200, {{"items", std::move(items)}},
                                          request_id, head.keep_alive));
    });

    router.Add("POST", "/api/v1/projects/{project_id}/directories",
        [auth, projects, maximum_json_bytes, secure_cookie](
            const RequestHead& head, const RouteParams& params) {
        RequireSameOrigin(head, secure_cookie);
        const SessionContext session = Authenticate(*auth, head);
        const std::string project_id =
            EntityIdParameter(params, "project_id");
        return PrepareJsonBody(
            head, maximum_json_bytes,
            [projects, session, project_id, keep_alive = head.keep_alive](
                const nlohmann::json& body) {
            RequireExactObject(body, {"parent_id", "name"});
            if (!body["parent_id"].is_string() || !body["name"].is_string()) {
                throw AppError(400, "invalid_request",
                               "parent_id and name must be strings");
            }
            const std::string request_id = GenerateId();
            const Directory directory = projects->CreateDirectory(
                session, project_id,
                EntityId(body["parent_id"].get<std::string>()),
                body["name"].get<std::string>(), request_id);
            return DataResponse(201, DirectoryJson(directory), request_id,
                                keep_alive);
        });
    });

    router.Add("PATCH", "/api/v1/projects/{project_id}/directories/{directory_id}",
        [auth, projects, maximum_json_bytes, secure_cookie](
            const RequestHead& head, const RouteParams& params) {
        RequireSameOrigin(head, secure_cookie);
        const SessionContext session = Authenticate(*auth, head);
        const std::string project_id =
            EntityIdParameter(params, "project_id");
        const std::string directory_id =
            EntityIdParameter(params, "directory_id");
        return PrepareJsonBody(
            head, maximum_json_bytes,
            [projects, session, project_id, directory_id,
             keep_alive = head.keep_alive](const nlohmann::json& body) {
            RequireExactObject(body, {"name"});
            if (!body["name"].is_string()) {
                throw AppError(400, "invalid_request", "name must be a string");
            }
            const std::string request_id = GenerateId();
            const Directory directory = projects->RenameDirectory(
                session, project_id, directory_id,
                body["name"].get<std::string>(), request_id);
            return DataResponse(200, DirectoryJson(directory), request_id,
                                keep_alive);
        });
    });
}
