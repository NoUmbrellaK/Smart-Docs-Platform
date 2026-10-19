#include "agent_tasks.h"
#include "auth/auth_routes.h"
#include "core/app_error.h"
#include "core/id.h"
#include "http/jsonbody.h"
#include "processing/processing_service.h"
#include <algorithm>
#include <chrono>
#include <functional>
#include <set>

namespace {
void NoQuery(const RequestHead& head) {
    if(!head.query.empty()) throw AppError(400,"invalid_request","query parameters are not supported");
}
HttpResponse Result(const nlohmann::json& data,bool keep_alive,int status=200) {
    return HttpResponse::Json(status,{{"request_id",GenerateId()},{"data",data}},keep_alive,{{"Cache-Control","no-store"}});
}
std::string ToolError(int status) {
    if(status==400 || status==413) return "invalid_request";
    if(status==401 || status==403 || status==404) return "access_denied";
    if(status==409) return "conflict";
    if(status==429) return "busy";
    if(status==408 || status==504) return "timeout";
    return "dependency_failed";
}
HttpResponse AuditedTool(AgentTaskService& tasks,const SessionContext& session,const std::string& project,
    const std::string& task,const std::string& tool,const nlohmann::json& body,bool keep_alive,
    const std::function<nlohmann::json(const std::string&)>& execute) {
    const auto started=std::chrono::steady_clock::now();
    const auto request=tasks.BeginToolCall(session,project,task,tool,body);
    const bool reading=tool=="search_documents" || tool=="read_document_version";
    const auto unknown=[&] {
        return HttpResponse::Json(503,{{"request_id",request},{"error",{{"code","agent_unavailable"},
            {"message","tool outcome is unknown; check the persisted result before any new action"},
            {"retryable",false}}}},false,{{"Cache-Control","no-store"}});
    };
    const auto respond=[&](const std::string& status,int http,nlohmann::json payload) {
        try {
            // Only read-tool responses can become model source receipts; write outcomes never do.
            tasks.FinishToolCall(request,status,static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now()-started).count()),
                reading ? payload.value("data",nlohmann::json(nullptr)) : nlohmann::json(nullptr));
        } catch(...) {
            return unknown();
        }
        payload["request_id"]=request;
        return HttpResponse::Json(http,payload,http<400 && keep_alive,{{"Cache-Control","no-store"}});
    };
    nlohmann::json result;
    try { result=execute(request); }
    catch(const AppError& error) {
        // A connection error may follow a successful COMMIT. Do not certify rollback or invite replay.
        if(!reading && error.http_status>=500) return unknown();
        return respond(ToolError(error.http_status),error.http_status,
            {{"error",{{"code",error.code},{"message",error.message},{"retryable",error.retryable}}}});
    } catch(...) {
        if(!reading) return unknown();
        return respond("dependency_failed",500,{{"error",{{"code","internal_error"},
            {"message","tool request failed"},{"retryable",false}}}});
    }
    std::string status="success";
    if(tool=="read_document_version" && !body.contains("chunk_id") && result.contains("parse_state")) {
        if(result["parse_state"]=="pending" || result["parse_state"]=="processing") status="processing";
        else if(result["parse_state"]!="completed") status="unavailable";
    }
    if(tool=="create_archive_plan" && result.value("task_state",std::string())=="waiting_confirmation")
        status="waiting_confirmation";
    return respond(status,200,{{"data",result}});
}
}
void RegisterAgentRoutes(Router& router,std::shared_ptr<AuthService> auth,std::shared_ptr<AgentTaskService> tasks,
    std::shared_ptr<ProcessingService> processing,bool secure_cookie,uint64_t max_json_bytes) {
    const std::string path="/api/v1/projects/{project_id}/agent-tasks";
    for(const std::string operation:{"save","read","markdown"}) {
        router.Add(operation=="save" ? "PUT" : "GET",path+"/{task_id}/handover-report"+(operation=="markdown" ? "/markdown" : ""),
            [=](const RequestHead& head,const RouteParams& params) {
            NoQuery(head); if(operation=="save") RequireSameOrigin(head,secure_cookie);
            const auto session=auth->Authenticate(RequireSessionCookie(head));
            const auto project=params.at("project_id"),task=params.at("task_id");
            if(operation=="save") return PrepareJsonBody(head,std::min<uint64_t>(65536,max_json_bytes),[=](const nlohmann::json& body) {
                return AuditedTool(*tasks,session,project,task,"save_handover_report",body,head.keep_alive,
                    [&](const std::string& request) { return tasks->SaveHandoverReport(session,project,task,body,request); });
            });
            if(head.content_length) throw AppError(400,"body_not_allowed","body is not supported");
            const auto report=tasks->GetHandoverReport(session,project,task);
            if(operation=="markdown") return ReadyResponse(HttpResponse::Text(200,RenderHandoverMarkdown(report),
                {{"Content-Type","text/markdown; charset=utf-8"},{"Cache-Control","no-store"},
                 {"Content-Disposition","attachment; filename=\"handover-"+report["id"].get<std::string>()+".md\""}},head.keep_alive));
            return ReadyResponse(HttpResponse::Json(200,{{"request_id",GenerateId()},{"data",report}},head.keep_alive,{{"Cache-Control","no-store"}}));
        });
    }
    for(const std::string operation:{"decision","execute","result"}) {
        router.Add(operation=="result" ? "GET" : "POST",path+"/{task_id}/archive-plan/"+operation,
            [=](const RequestHead& head,const RouteParams& params) {
            NoQuery(head); if(operation!="result") RequireSameOrigin(head,secure_cookie);
            const auto session=auth->Authenticate(RequireSessionCookie(head));
            const auto project=params.at("project_id"),task=params.at("task_id");
            if(operation=="result") {
                if(head.content_length) throw AppError(400,"body_not_allowed","body is not supported");
                return ReadyResponse(Result(tasks->ArchiveResult(session,project,task),head.keep_alive));
            }
            return PrepareJsonBody(head,std::min<uint64_t>(4096,max_json_bytes),[=](const nlohmann::json& body) {
                if(operation=="execute") return AuditedTool(*tasks,session,project,task,"execute_archive_plan",body,head.keep_alive,
                    [&](const std::string& request) { return tasks->ExecuteArchivePlan(session,project,task,body,request); });
                const auto request=GenerateId();
                const auto result=tasks->DecideArchivePlan(session,project,task,body,request);
                return HttpResponse::Json(200,{{"request_id",request},{"data",result}},head.keep_alive);
            });
        });
    }
    for(const bool writing:{false,true}) {
        router.Add(writing ? "PUT" : "GET",path+"/{task_id}/archive-plan",
            [=](const RequestHead& head,const RouteParams& params) {
            NoQuery(head); if(writing) RequireSameOrigin(head,secure_cookie);
            const auto session=auth->Authenticate(RequireSessionCookie(head));
            const auto project=params.at("project_id"),task=params.at("task_id");
            if(writing) return PrepareJsonBody(head,std::min<uint64_t>(65536,max_json_bytes),[=](const nlohmann::json& body) {
                return AuditedTool(*tasks,session,project,task,"create_archive_plan",body,head.keep_alive,
                    [&](const std::string& request) { return tasks->SaveArchivePlan(session,project,task,body,request); });
            });
            if(head.content_length) throw AppError(400,"body_not_allowed","body is not supported");
            return ReadyResponse(Result(tasks->GetArchivePlan(session,project,task),head.keep_alive));
        });
    }
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
                if(operation=="check") return PrepareJsonBody(head,std::min<uint64_t>(4096,max_json_bytes),[=](const nlohmann::json& body) {
                    return Result(tasks->CheckRun(session,project,task,body),head.keep_alive);
                });
                if(head.content_length) throw AppError(400,"body_not_allowed","body is not supported");
                return ReadyResponse(Result(operation=="start" ? tasks->StartRun(session,project,task) :
                    tasks->GetResult(session,project,task),head.keep_alive));
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
                    return AuditedTool(*tasks,session,params.at("project_id"),params.at("task_id"),
                        search ? "search_documents" : "read_document_version",body,head.keep_alive,[&](const std::string&) {
                        if(search) return processing->Search(session,params.at("project_id"),body,true,params.at("task_id"));
                        else {
                            const std::set<std::string> keys{"file_id","version_id","chunk_id","range"};
                            if(!body.is_object() || !body.contains("file_id") || !body.contains("version_id") ||
                                !body["file_id"].is_string() || !body["version_id"].is_string() ||
                                (body.contains("chunk_id") && (!body["chunk_id"].is_string() || body.contains("range") || body["chunk_id"]=="")) ||
                                (body.contains("range") && !body["range"].is_object()))
                                throw AppError(400,"invalid_request","read selectors are invalid");
                            for(auto it=body.begin();it!=body.end();++it) if(!keys.count(it.key()))
                                throw AppError(400,"invalid_request","read parameters are invalid");
                            return processing->ReadDocument(session,params.at("project_id"),body["file_id"],body["version_id"],
                                body.value("chunk_id",std::string()),true,body.value("range",nlohmann::json(nullptr)),params.at("task_id"));
                        }
                    });
                });
            });
    }
}
