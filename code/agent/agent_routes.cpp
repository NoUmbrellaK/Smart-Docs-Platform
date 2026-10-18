#include "agent_tasks.h"
#include "auth/auth_routes.h"
#include "core/app_error.h"
#include "core/id.h"
#include "http/jsonbody.h"
#include "processing/processing_service.h"
#include <algorithm>
#include <set>

namespace {
void NoQuery(const RequestHead& head) {
    if(!head.query.empty()) throw AppError(400,"invalid_request","query parameters are not supported");
}
HttpResponse Result(const nlohmann::json& data,bool keep_alive,int status=200) {
    return HttpResponse::Json(status,{{"request_id",GenerateId()},{"data",data}},keep_alive);
}
}
void RegisterAgentRoutes(Router& router,std::shared_ptr<AuthService> auth,std::shared_ptr<AgentTaskService> tasks,
    std::shared_ptr<ProcessingService> processing,bool secure_cookie,uint64_t max_json_bytes) {
    const std::string path="/api/v1/projects/{project_id}/agent-tasks";
    router.Add("POST",path,[=](const RequestHead& head,const RouteParams& params) {
        NoQuery(head); RequireSameOrigin(head,secure_cookie);
        const auto session=auth->Authenticate(RequireSessionCookie(head));
        return PrepareJsonBody(head,std::min<uint64_t>(32768,max_json_bytes),[=](const nlohmann::json& body) {
            return Result(tasks->Create(session,params.at("project_id"),body),head.keep_alive,201);
        });
    });
    for(const bool cancel:{false,true}) {
        router.Add(cancel ? "POST" : "GET",path+"/{task_id}"+(cancel ? "/cancel" : ""),
            [=](const RequestHead& head,const RouteParams& params) {
                NoQuery(head); if(cancel) RequireSameOrigin(head,secure_cookie);
                if(head.content_length) throw AppError(400,"body_not_allowed","body is not supported");
                const auto session=auth->Authenticate(RequireSessionCookie(head));
                const auto result=cancel ? tasks->Cancel(session,params.at("project_id"),params.at("task_id"))
                    : tasks->Get(session,params.at("project_id"),params.at("task_id"));
                return ReadyResponse(Result(result,head.keep_alive));
            });
    }
    for(const bool search:{true,false}) {
        router.Add("POST",path+"/{task_id}/tools/"+(search ? "search" : "read"),
            [=](const RequestHead& head,const RouteParams& params) {
                NoQuery(head); RequireSameOrigin(head,secure_cookie);
                const auto session=auth->Authenticate(RequireSessionCookie(head));
                return PrepareJsonBody(head,std::min<uint64_t>(search ? 131072 : 4096,max_json_bytes),[=](const nlohmann::json& body) {
                    if(search) return Result(processing->Search(session,params.at("project_id"),body,true,params.at("task_id")),head.keep_alive);
                    const std::set<std::string> keys{"file_id","version_id","chunk_id","range"};
                    if(!body.is_object() || !body.contains("file_id") || !body.contains("version_id") ||
                        !body["file_id"].is_string() || !body["version_id"].is_string() ||
                        (body.contains("chunk_id") && (!body["chunk_id"].is_string() || body.contains("range") || body["chunk_id"]=="")) ||
                        (body.contains("range") && !body["range"].is_object()))
                        throw AppError(400,"invalid_request","read selectors are invalid");
                    for(auto it=body.begin();it!=body.end();++it) if(!keys.count(it.key()))
                        throw AppError(400,"invalid_request","read parameters are invalid");
                    return Result(processing->ReadDocument(session,params.at("project_id"),body["file_id"],body["version_id"],
                        body.value("chunk_id",std::string()),true,body.value("range",nlohmann::json(nullptr)),params.at("task_id")),head.keep_alive);
                });
            });
    }
}
