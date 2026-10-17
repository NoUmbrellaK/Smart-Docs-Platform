#include "processing_routes.h"
#include "processing_service.h"
#include "auth/auth_routes.h"
#include "core/app_error.h"
#include "core/id.h"
#include "http/jsonbody.h"
#include "http/router.h"

namespace {
HttpResponse Response(const ProcessingJob& job, const std::string& request, bool keep_alive) {
    return HttpResponse::Json(200, {{"request_id", request}, {"data", {
        {"id", job.id}, {"file_id", job.file_id}, {"version_id", job.version_id},
        {"project_id", job.project_id}, {"state", job.state},
        {"attempt_count", job.attempt_count}, {"error_code", job.error_code}}}}, keep_alive);
}
void RequireEmpty(const RequestHead& head) {
    if (head.content_length != 0)
        throw AppError(400, "body_not_allowed", "this route does not accept a request body");
}
}  // namespace

void RegisterProcessingRoutes(Router& router, std::shared_ptr<AuthService> auth,
                               std::shared_ptr<ProcessingService> jobs, bool secure_cookie) {
    const std::string path = "/api/v1/projects/{project_id}/processing-jobs/{job_id}";
    router.Add("GET", path, [auth, jobs](const RequestHead& head, const RouteParams& params) {
        RequireEmpty(head);
        const auto session = auth->Authenticate(RequireSessionCookie(head));
        return ReadyResponse(Response(jobs->Get(session, params.at("project_id"), params.at("job_id")),
                                      GenerateId(), head.keep_alive));
    });
    for (const std::string action : {"cancel", "retry"}) {
        router.Add("POST", path + "/" + action,
            [auth, jobs, secure_cookie, action](const RequestHead& head, const RouteParams& params) {
                RequireSameOrigin(head, secure_cookie); RequireEmpty(head);
                const auto session = auth->Authenticate(RequireSessionCookie(head));
                const auto request = GenerateId();
                const auto result = action == "cancel"
                    ? jobs->Cancel(session, params.at("project_id"), params.at("job_id"), request)
                    : jobs->Retry(session, params.at("project_id"), params.at("job_id"), request);
                return ReadyResponse(Response(result, request, head.keep_alive));
            });
    }
    const std::string version = "/api/v1/projects/{project_id}/files/{file_id}/versions/{version_id}";
    for (const std::string suffix : {"/document", "/chunks/{chunk_id}"}) {
        router.Add("GET", version + suffix, [auth, jobs](const RequestHead& head, const RouteParams& params) {
            RequireEmpty(head);
            const auto session = auth->Authenticate(RequireSessionCookie(head));
            const auto chunk = params.find("chunk_id");
            const auto data = jobs->ReadDocument(session, params.at("project_id"), params.at("file_id"),
                params.at("version_id"), chunk == params.end() ? "" : chunk->second);
            return ReadyResponse(HttpResponse::Json(200, {{"request_id", GenerateId()}, {"data", data}}, head.keep_alive));
        });
    }
}
