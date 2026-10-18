#include "agent_tasks.h"
#include "auth/auth_routes.h"
#include "core/app_error.h"
#include "core/id.h"
#include "http/jsonbody.h"
#include "processing/processing_service.h"
#include <algorithm>
#include <chrono>
#include <set>

namespace {
void NoQuery(const RequestHead& head) {
    if(!head.query.empty()) throw AppError(400,"invalid_request","query parameters are not supported");
}
HttpResponse Result(const nlohmann::json& data,bool keep_alive,int status=200) {
    return HttpResponse::Json(status,{{"request_id",GenerateId()},{"data",data}},keep_alive);
}
std::string ToolError(int status) {
    if(status==400 || status==413) return "invalid_request";
    if(status==401 || status==403 || status==404) return "access_denied";
    if(status==409) return "conflict";
    if(status==429) return "busy";
    if(status==408 || status==504) return "timeout";
    return "dependency_failed";
}
}
void RegisterAgentRoutes(Router& router,std::shared_ptr<AuthService> auth,std::shared_ptr<AgentTaskService> tasks,
    std::shared_ptr<ProcessingService> processing,bool secure_cookie,uint64_t max_json_bytes) {
    const std::string path="/api/v1/projects/{project_id}/agent-tasks";
    router.Add("GET",path+"/{task_id}/tool-calls",[=](const RequestHead& head,const RouteParams& params) {
        NoQuery(head);
        if(head.content_length) throw AppError(400,"body_not_allowed","body is not supported");
        const auto session=auth->Authenticate(RequireSessionCookie(head));
        return ReadyResponse(Result(tasks->ToolCalls(session,params.at("project_id"),params.at("task_id")),head.keep_alive));
    });
    router.Add("GET",path,[=](const RequestHead& head,const RouteParams& params) {
        if(head.content_length) throw AppError(400,"body_not_allowed","body is not supported");
        std::string before;
        if(!head.query.empty()) {
            if(head.query.size()!=39 || head.query.compare(0,7,"before=")!=0 ||
                head.query.find_first_not_of("0123456789abcdef",7)!=std::string::npos)
                throw AppError(400,"invalid_request","task cursor is invalid");
            before=head.query.substr(7);
        }
        const auto session=auth->Authenticate(RequireSessionCookie(head));
        return ReadyResponse(Result(tasks->List(session,params.at("project_id"),before),head.keep_alive));
    });
    for(const std::string operation:{"start","check","result","finish"}) {
        router.Add(operation=="result" ? "GET" : "POST",path+"/{task_id}/run/"+operation,
            [=](const RequestHead& head,const RouteParams& params) {
                NoQuery(head); if(operation!="result") RequireSameOrigin(head,secure_cookie);
                const auto session=auth->Authenticate(RequireSessionCookie(head));
                const auto project=params.at("project_id"), task=params.at("task_id");
                if(operation=="finish") return PrepareJsonBody(head,std::min<uint64_t>(65536,max_json_bytes),[=](const nlohmann::json& body) {
                    return Result(tasks->FinishRun(session,project,task,body),head.keep_alive);
                });
                if(head.content_length) throw AppError(400,"body_not_allowed","body is not supported");
                return ReadyResponse(Result(operation=="start" ? tasks->StartRun(session,project,task) :
                    operation=="check" ? tasks->CheckRun(session,project,task) : tasks->GetResult(session,project,task),head.keep_alive));
            });
    }
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
                    const auto started=std::chrono::steady_clock::now();
                    const auto request=tasks->BeginToolCall(session,params.at("project_id"),params.at("task_id"),search,body);
                    const auto respond=[&](const std::string& status,int http,nlohmann::json payload) {
                        try {
                            tasks->FinishToolCall(request,status,static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
                                std::chrono::steady_clock::now()-started).count()));
                        } catch(...) {
                            return HttpResponse::Json(503,{{"request_id",request},{"error",{{"code","agent_unavailable"},
                                {"message","tool outcome could not be recorded"},{"retryable",false}}}},false);
                        }
                        payload["request_id"]=request;
                        return HttpResponse::Json(http,payload,http<400 && head.keep_alive);
                    };
                    nlohmann::json result;
                    try {
                        if(search) result=processing->Search(session,params.at("project_id"),body,true,params.at("task_id"));
                        else {
                            const std::set<std::string> keys{"file_id","version_id","chunk_id","range"};
                            if(!body.is_object() || !body.contains("file_id") || !body.contains("version_id") ||
                                !body["file_id"].is_string() || !body["version_id"].is_string() ||
                                (body.contains("chunk_id") && (!body["chunk_id"].is_string() || body.contains("range") || body["chunk_id"]=="")) ||
                                (body.contains("range") && !body["range"].is_object()))
                                throw AppError(400,"invalid_request","read selectors are invalid");
                            for(auto it=body.begin();it!=body.end();++it) if(!keys.count(it.key()))
                                throw AppError(400,"invalid_request","read parameters are invalid");
                            result=processing->ReadDocument(session,params.at("project_id"),body["file_id"],body["version_id"],
                                body.value("chunk_id",std::string()),true,body.value("range",nlohmann::json(nullptr)),params.at("task_id"));
                        }
                    } catch(const AppError& error) {
                        return respond(ToolError(error.http_status),error.http_status,
                            {{"error",{{"code",error.code},{"message",error.message},{"retryable",error.retryable}}}});
                    } catch(...) {
                        return respond("dependency_failed",500,{{"error",{{"code","internal_error"},
                            {"message","tool request failed"},{"retryable",false}}}});
                    }
                    std::string status="success";
                    if(!search && !body.contains("chunk_id") && result.contains("parse_state")) {
                        if(result["parse_state"]=="pending" || result["parse_state"]=="processing") status="processing";
                        else if(result["parse_state"]!="completed") status="unavailable";
                    }
                    return respond(status,200,{{"data",result}});
                });
            });
    }
}
