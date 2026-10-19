#include "agent_tasks.h"
#include "tool_sources.h"
#include "core/app_error.h"
#include "core/id.h"
#include "db/mysql.h"
#include "project/project_service.h"
#include "http/jsonbody.h"
#include <algorithm>
#include <map>
#include <set>

namespace {
using Json=nlohmann::json;
void Id(const std::string& id) {
    if(id.size()!=32 || id.find_first_not_of("0123456789abcdef")!=std::string::npos)
        throw AppError(400,"invalid_request","entity ID is invalid");
}
[[noreturn]] void Missing() { throw AppError(404,"resource_not_found","resource was not found"); }
void LockQueue(MySqlConnection& db) {
    if(db.Query("SELECT id FROM agent_queue_guard WHERE id=1 FOR UPDATE").size()!=1)
        throw AppError(503,"agent_unavailable","agent task store is unavailable");
}
MySqlRow Task(MySqlConnection& db,const SessionContext& session,const std::string& project,const std::string& task) {
    Id(project); Id(task);
    const auto rows=db.Query("SELECT id,state,created_at,updated_at,confirmed_at FROM agent_tasks "
        "WHERE id=? AND project_id=? AND owner_user_id=? FOR UPDATE",
        {SqlValue(task),SqlValue(project),SqlValue(session.user_id)});
    if(rows.empty()) Missing();
    return rows[0];
}
bool Allowed(MySqlConnection& db,const std::string& project,const std::string& file,const std::string& version,bool exclusive=false) {
    const auto files=db.Query(std::string("SELECT current_version_id,remote_ai_policy,deleted_at FROM files WHERE id=? AND project_id=? ")+(exclusive ? "FOR UPDATE" : "FOR SHARE"),
        {SqlValue(file),SqlValue(project)});
    if(files.empty() || files[0].IsNull(0) || files[0].String(0)!=version || files[0].String(1)!="remote_ai_allowed" || !files[0].IsNull(2)) return false;
    const auto versions=db.Query("SELECT remote_ai_approved,availability_state FROM file_versions WHERE id=? AND file_id=? FOR SHARE",
        {SqlValue(version),SqlValue(file)});
    return !versions.empty() && versions[0].Bool(0) && versions[0].String(1)=="available";
}
Json Checklist(MySqlConnection& db,const std::string& project,uint64_t revision) {
    const auto rows=db.Query("SELECT revision,items_json,remote_ai_approved FROM handover_checklists WHERE project_id=? FOR SHARE",
        {SqlValue(project)});
    if(rows.empty() || rows[0].UInt64(0)!=revision || !rows[0].Bool(2))
        throw AppError(409,"task_scope_changed","task checklist is unavailable");
    auto items=Json::parse(rows[0].String(1));
    if(items.empty()) throw AppError(409,"task_scope_changed","task checklist is unavailable");
    return {{"revision",revision},{"items",items}};
}
Json TaskChecklist(MySqlConnection& db,const std::string& task) {
    const auto rows=db.Query("SELECT handover_context FROM agent_tasks WHERE id=? FOR SHARE",{SqlValue(task)});
    if(rows.empty()) Missing();
    return rows[0].IsNull(0) ? Json(nullptr) : Json::parse(rows[0].String(0));
}
void Scope(MySqlConnection& db,const std::string& project,const std::string& task,
    const std::string& file="",const std::string& version="",bool exclusive=false) {
    const auto checklist=TaskChecklist(db,task);
    if(!checklist.is_null() && Checklist(db,project,checklist.at("revision").get<uint64_t>())!=checklist)
        throw AppError(409,"task_scope_changed","task checklist is unavailable");
    const auto scope=db.Query("SELECT file_id,version_id FROM agent_task_versions WHERE task_id=? ORDER BY file_id FOR SHARE",{SqlValue(task)});
    bool contains=file.empty();
    if(scope.empty() || scope.size()>100) throw AppError(409,"task_scope_changed","task scope is unavailable");
    for(const auto& item:scope) {
        if(!Allowed(db,project,item.String(0),item.String(1),exclusive))
            throw AppError(409,"task_scope_changed","task scope is unavailable");
        if(item.String(0)==file && item.String(1)==version) contains=true;
    }
    if(!contains) Missing();
}
void FailRun(MySqlConnection& db,const std::string& task,const std::string& code) {
    db.Execute("UPDATE agent_tasks SET state='failed' WHERE id=?",{SqlValue(task)});
    db.Execute("UPDATE agent_runs SET result_json=NULL,finish_code=? WHERE task_id=?",{SqlValue(code),SqlValue(task)});
}
void LiveScope(MySqlConnection& db,MySqlTransaction& transaction,const std::string& project,const std::string& task) {
    try { Scope(db,project,task); }
    catch(const AppError& error) {
        if(error.code=="task_scope_changed") { FailRun(db,task,"scope_changed"); transaction.Commit(); }
        throw;
    }
}
MySqlRow Run(MySqlConnection& db,const std::string& task) {
    const auto rows=db.Query("SELECT model_calls,result_json,finish_code,"
        "started_at+INTERVAL 180 SECOND<=UTC_TIMESTAMP(6) FROM agent_runs WHERE task_id=? FOR UPDATE",{SqlValue(task)});
    if(rows.empty()) Missing();
    return rows[0];
}
void Deadline(MySqlConnection& db,MySqlTransaction& transaction,const std::string& task,const MySqlRow& run) {
    if(run.Bool(3)) {
        FailRun(db,task,"budget_exhausted"); transaction.Commit();
        throw AppError(409,"budget_exhausted","task budget is exhausted");
    }
}
void ValidateResult(MySqlConnection& db,const std::string& task,const Json& result) {
    RequireExactObject(result,{"answer","citations"});
    if(!result["answer"].is_string() || result["answer"].get_ref<const std::string&>().size()>16000 ||
        result["answer"].get_ref<const std::string&>().find_first_not_of(" \t\r\n")==std::string::npos ||
        !result["citations"].is_array() || result["citations"].size()>100)
        throw AppError(400,"invalid_request","result is invalid");
    std::set<std::string> seen;
    for(const auto& ref:result["citations"]) {
        RequireExactObject(ref,{"file_id","version_id","chunk_id"});
        if(!ref["file_id"].is_string() || !ref["version_id"].is_string() || !ref["chunk_id"].is_string())
            throw AppError(400,"invalid_source","source is invalid");
        const auto file=ref["file_id"].get<std::string>(), version=ref["version_id"].get<std::string>(), chunk=ref["chunk_id"].get<std::string>();
        Id(file); Id(version);
        if(chunk.size()!=64 || chunk.find_first_not_of("0123456789abcdef")!=std::string::npos || !seen.insert(chunk).second ||
            db.Query("SELECT c.id FROM agent_task_versions a JOIN document_chunks c ON c.version_id=a.version_id "
                "WHERE a.task_id=? AND a.file_id=? AND a.version_id=? AND c.id=? FOR SHARE",
                {SqlValue(task),SqlValue(file),SqlValue(version),SqlValue(chunk)}).empty())
            throw AppError(400,"invalid_source","source is invalid");
    }
}
Json Status(MySqlConnection& db,const MySqlRow& row,const std::string& project) {
    Json scope=Json::array();
    for(const auto& v:db.Query("SELECT file_id,version_id FROM agent_task_versions WHERE task_id=? ORDER BY file_id FOR SHARE",{SqlValue(row.String(0))}))
        scope.push_back({{"file_id",v.String(0)},{"version_id",v.String(1)}});
    Json result={{"id",row.String(0)},{"project_id",project},{"state",row.String(1)},
        {"created_at",row.String(2)},{"updated_at",row.String(3)},{"confirmed_at",row.String(4)},{"versions",scope}};
    const auto checklist=TaskChecklist(db,row.String(0));
    if(!checklist.is_null()) result["handover_checklist_revision"]=checklist["revision"];
    return result;
}
}

nlohmann::json AgentTaskService::Create(const SessionContext& session,const std::string& project,const nlohmann::json& body) {
    Id(project);
    const bool handover=body.is_object() && body.contains("handover_checklist_revision");
    if(handover) {
        RequireExactObject(body,{"question","versions","confirm_remote_ai","handover_checklist_revision"});
        if(!body["handover_checklist_revision"].is_number_unsigned() || body["handover_checklist_revision"].get<uint64_t>()==0 ||
            body["handover_checklist_revision"].get<uint64_t>()>4294967295ULL)
            throw AppError(400,"invalid_request","checklist revision is invalid");
    } else RequireExactObject(body,{"question","versions","confirm_remote_ai"});
    if(!body["question"].is_string() || !body["confirm_remote_ai"].is_boolean() || !body["confirm_remote_ai"].get<bool>() ||
        !body["versions"].is_array() || body["versions"].empty() || body["versions"].size()>100)
        throw AppError(400,"invalid_request","task confirmation and scope are required");
    const auto question=body["question"].get<std::string>();
    if(question.size()>4000 || question.find_first_not_of(" \t\r\n")==std::string::npos ||
        std::any_of(question.begin(),question.end(),[](unsigned char c){return (c<32 && c!=9 && c!=10 && c!=13) || c==127;}))
        throw AppError(400,"invalid_request","question is invalid");
    std::map<std::string,std::string> versions;
    for(const auto& item:body["versions"]) {
        RequireExactObject(item,{"file_id","version_id"});
        if(!item["file_id"].is_string() || !item["version_id"].is_string())
            throw AppError(400,"invalid_request","scope IDs must be strings");
        const auto file=item["file_id"].get<std::string>(), version=item["version_id"].get<std::string>();
        Id(file); Id(version);
        if(!versions.emplace(file,version).second) throw AppError(400,"invalid_request","duplicate scope file");
    }
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db,session.user_id,project,Role::Reader); LockQueue(db);
    const auto checklist=handover ? Checklist(db,project,body["handover_checklist_revision"].get<uint64_t>()) : Json(nullptr);
    if(db.Query("SELECT id FROM agent_tasks WHERE state IN ('pending','running') LIMIT 100 FOR UPDATE").size()>=100)
        throw AppError(429,"agent_busy","agent task queue is full",true);
    for(const auto& item:versions) if(!Allowed(db,project,item.first,item.second)) Missing();
    const auto id=GenerateId();
    db.Execute("INSERT INTO agent_tasks(id,owner_user_id,project_id,question,handover_context) VALUES (?,?,?,?,?)",
        {SqlValue(id),SqlValue(session.user_id),SqlValue(project),SqlValue(question),
         checklist.is_null() ? SqlValue::Null() : SqlValue(checklist.dump())});
    for(const auto& item:versions)
        db.Execute("INSERT INTO agent_task_versions(task_id,project_id,file_id,version_id) VALUES (?,?,?,?)",
            {SqlValue(id),SqlValue(project),SqlValue(item.first),SqlValue(item.second)});
    auto result=Status(db,Task(db,session,project,id),project); transaction.Commit(); return result;
}

nlohmann::json AgentTaskService::List(const SessionContext& session,const std::string& project,const std::string& before) {
    Id(project); if(!before.empty()) Id(before);
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db,session.user_id,project,Role::Reader);
    std::vector<SqlValue> values{SqlValue(project),SqlValue(session.user_id)};
    std::string cursor_filter;
    if(!before.empty()) {
        const auto cursor=db.Query("SELECT created_at FROM agent_tasks WHERE id=? AND project_id=? AND owner_user_id=?",
            {SqlValue(before),SqlValue(project),SqlValue(session.user_id)});
        if(cursor.empty()) Missing();
        cursor_filter=" AND (created_at<? OR (created_at=? AND id<?))";
        values.emplace_back(cursor[0].String(0)); values.emplace_back(cursor[0].String(0)); values.emplace_back(before);
    }
    // ponytail: existing indexes plus a query deadline; add an owner/project/time index if history outgrows this P0 path.
    const auto rows=db.Query("SELECT /*+ MAX_EXECUTION_TIME(1000) */ id,state,created_at,updated_at,confirmed_at "
        "FROM agent_tasks WHERE project_id=? AND owner_user_id=?"+cursor_filter+" ORDER BY created_at DESC,id DESC LIMIT 21",values);
    Json items=Json::array();
    for(size_t i=0;i<std::min<size_t>(20,rows.size());++i) items.push_back(Status(db,rows[i],project));
    const bool more=rows.size()>20;
    Json result={{"items",items},{"page_size",20},{"has_more",more},{"next_before",more ? Json(rows[19].String(0)) : Json(nullptr)}};
    transaction.Commit(); return result;
}

nlohmann::json AgentTaskService::Get(const SessionContext& session,const std::string& project,const std::string& task) {
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db,session.user_id,project,Role::Reader);
    auto result=Status(db,Task(db,session,project,task),project); transaction.Commit(); return result;
}
nlohmann::json AgentTaskService::Cancel(const SessionContext& session,const std::string& project,const std::string& task) {
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db,session.user_id,project,Role::Reader); Task(db,session,project,task);
    db.Execute("UPDATE agent_tasks SET state='cancelled' WHERE id=? AND state IN ('pending','running','interrupted','waiting_confirmation','waiting_execution')",{SqlValue(task)});
    auto result=Status(db,Task(db,session,project,task),project); transaction.Commit(); return result;
}
void AgentTaskService::RecoverInterrupted() {
    auto db=pool_.Acquire(); MySqlTransaction transaction(db); LockQueue(db);
    db.Execute("UPDATE agent_tasks SET state='interrupted' WHERE state='running'"); transaction.Commit();
}

nlohmann::json AgentTaskService::StartRun(const SessionContext& session,const std::string& project,const std::string& task) {
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db,session.user_id,project,Role::Reader); LockQueue(db);
    if(Task(db,session,project,task).String(1)!="pending") throw AppError(409,"task_inactive","task cannot be claimed");
    db.Execute("INSERT INTO agent_runs(task_id) VALUES (?)",{SqlValue(task)});
    LiveScope(db,transaction,project,task);
    RequireAgentTaskScope(db,session,project,task);
    const auto question=db.Query("SELECT question FROM agent_tasks WHERE id=?",{SqlValue(task)})[0].String(0);
    Json result={{"state","running"},{"question",question}};
    const auto checklist=TaskChecklist(db,task);
    if(!checklist.is_null()) {
        result["handover_checklist"]=checklist;
        const auto operation=GenerateId();
        db.Execute("UPDATE agent_runs SET report_operation_id=? WHERE task_id=?",{SqlValue(operation),SqlValue(task)});
        result["report_operation_id"]=operation;
    }
    if(result.dump().size()>64000) {
        FailRun(db,task,"budget_exhausted"); transaction.Commit();
        throw AppError(409,"budget_exhausted","task context exceeds the response budget");
    }
    transaction.Commit(); return result;
}
nlohmann::json AgentTaskService::CheckRun(const SessionContext& session,const std::string& project,const std::string& task,const Json& body) {
    const bool handover=body.is_object() && body.contains("handover_checklist_revision");
    if(handover) RequireExactObject(body,{"tool_request_ids","handover_checklist_revision"});
    else RequireExactObject(body,{"tool_request_ids"});
    const auto invalid=[] { throw AppError(400,"invalid_source_receipt","model source receipts are invalid"); };
    if(!body["tool_request_ids"].is_array() || body["tool_request_ids"].size()>64) invalid();
    std::set<std::string> receipts;
    for(const auto& id:body["tool_request_ids"]) {
        if(!id.is_string()) invalid();
        const auto value=id.get<std::string>(); Id(value);
        if(!receipts.insert(value).second) invalid();
    }
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db,session.user_id,project,Role::Reader); LockQueue(db);
    if(Task(db,session,project,task).String(1)!="running") throw AppError(409,"task_inactive","task is not running");
    const auto checklist=TaskChecklist(db,task);
    if(handover==checklist.is_null() || (handover && (!body["handover_checklist_revision"].is_number_unsigned() ||
        body["handover_checklist_revision"]!=checklist["revision"])))
        throw AppError(400,"invalid_request","checklist acknowledgment is invalid");
    const auto run=Run(db,task); Deadline(db,transaction,task,run); LiveScope(db,transaction,project,task);
    if(run.UInt64(0)>=8) {
        FailRun(db,task,"budget_exhausted"); transaction.Commit();
        throw AppError(409,"budget_exhausted","task budget is exhausted");
    }
    size_t matched=0;
    for(const auto& row:db.Query("SELECT request_id,status,source_json IS NOT NULL,model_call FROM agent_tool_calls "
        "WHERE task_id=? ORDER BY sequence FOR UPDATE",{SqlValue(task)})) {
        const bool selected=receipts.count(row.String(0));
        if(!row.IsNull(3) && !selected) invalid();
        if(selected) {
            if(!row.Bool(2) || (row.String(1)!="success" && row.String(1)!="processing" && row.String(1)!="unavailable")) invalid();
            ++matched;
        }
    }
    if(matched!=receipts.size()) invalid();
    for(const auto& request:receipts)
        db.Execute("UPDATE agent_tool_calls SET model_call=COALESCE(model_call,?) WHERE task_id=? AND request_id=?",
            {SqlValue(run.UInt64(0)+1),SqlValue(task),SqlValue(request)});
    db.Execute("UPDATE agent_runs SET model_calls=model_calls+1 WHERE task_id=?",{SqlValue(task)});
    transaction.Commit(); return {{"model_calls",run.UInt64(0)+1}};
}
nlohmann::json AgentTaskService::FinishRun(const SessionContext& session,const std::string& project,const std::string& task,const Json& body) {
    RequireExactObject(body,{"status","result","error_code"});
    const std::set<std::string> codes{"timeout","budget_exhausted","model_invalid","invalid_source","dependency_failed",
        "scope_changed","repeated_tool_error","access_denied","cancelled"};
    if(!body["status"].is_string()) throw AppError(400,"invalid_request","status is invalid");
    const auto status=body["status"].get<std::string>();
    if((status=="completed" && (!body["error_code"].is_null() || !body["result"].is_object())) ||
        (status=="failed" && (!body["result"].is_null() || !body["error_code"].is_string() || !codes.count(body["error_code"].get<std::string>()))) ||
        (status!="completed" && status!="failed")) throw AppError(400,"invalid_request","outcome is invalid");
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db,session.user_id,project,Role::Reader); LockQueue(db);
    const auto row=Task(db,session,project,task), run=Run(db,task);
    if(row.String(1)=="completed" || row.String(1)=="failed") {
        const Json result=run.IsNull(1) ? Json(nullptr) : Json::parse(run.String(1));
        const Json error=run.IsNull(2) ? Json(nullptr) : Json(run.String(2));
        if(row.String(1)!=status) throw AppError(409,"task_inactive","task is not running");
        if(result!=body["result"] || error!=body["error_code"]) throw AppError(409,"task_conflict","outcome differs");
        if(status=="completed") LiveScope(db,transaction,project,task);
        transaction.Commit(); return {{"state",status}};
    }
    if(row.String(1)!="running") throw AppError(409,"task_inactive","task is not running");
    if(status=="completed") {
        Deadline(db,transaction,task,run); LiveScope(db,transaction,project,task);
        if(run.UInt64(0)==0) throw AppError(409,"task_conflict","no model request was admitted");
        ValidateResult(db,task,body["result"]);
    }
    db.Execute("UPDATE agent_runs SET result_json=?,finish_code=? WHERE task_id=?",{
        body["result"].is_null() ? SqlValue::Null() : SqlValue(body["result"].dump()),
        body["error_code"].is_null() ? SqlValue::Null() : SqlValue(body["error_code"].get<std::string>()),SqlValue(task)});
    db.Execute("UPDATE agent_tasks SET state=? WHERE id=?",{SqlValue(status),SqlValue(task)});
    transaction.Commit(); return {{"state",status}};
}
nlohmann::json AgentTaskService::GetResult(const SessionContext& session,const std::string& project,const std::string& task) {
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db,session.user_id,project,Role::Reader); LockQueue(db);
    const auto row=Task(db,session,project,task);
    if(row.String(1)=="completed") {
        const auto report=ReadHandoverReport(db,project,task);
        if(!report.is_null()) {
            const auto run=Run(db,task);
            Json result={{"state","completed"},{"kind","handover_report"},{"result",nullptr},{"error_code",nullptr},
                {"model_calls",run.UInt64(0)},{"handover_report",HandoverReportReceipt(report)}};
            transaction.Commit(); return result;
        }
        const auto receipt=CommittedArchiveReceipt(db,task);
        if(!receipt.is_null()) {
            const auto runs=db.Query("SELECT model_calls FROM agent_runs WHERE task_id=? FOR SHARE",{SqlValue(task)});
            Json result={{"state","completed"},{"kind","archive_operation"},{"result",nullptr},{"error_code",nullptr},
                {"model_calls",runs.empty() ? 0 : runs[0].UInt64(0)},{"archive_operation",receipt}};
            transaction.Commit(); return result;
        }
    }
    const auto run=Run(db,task);
    if(row.String(1)=="completed") LiveScope(db,transaction,project,task);
    Json result={{"state",row.String(1)},{"model_calls",run.UInt64(0)},
        {"result",row.String(1)=="completed" && !run.IsNull(1) ? Json::parse(run.String(1)) : Json(nullptr)},
        {"error_code",run.IsNull(2) ? Json(nullptr) : Json(run.String(2))}};
    transaction.Commit(); return result;
}

std::string LockArchiveTask(MySqlConnection& db,const SessionContext& session,const std::string& project,const std::string& task) {
    LockQueue(db); return Task(db,session,project,task).String(1);
}
void RequireReportDependencies(MySqlConnection& db,const std::string& project,const Json& sources,const Json& checklist) {
    if(checklist.is_null() || Checklist(db,project,checklist.at("revision").get<uint64_t>())!=checklist)
        throw AppError(409,"task_scope_changed","report checklist is unavailable");
    for(const auto& item:sources.at("versions"))
        if(!Allowed(db,project,item.at("file_id"),item.at("version_id")))
            throw AppError(409,"task_scope_changed","report sources are unavailable");
}
std::string RequireArchiveTaskScope(MySqlConnection& db,const SessionContext& session,const std::string& project,
    const std::string& task,bool writing,bool exclusive) {
    const auto state=LockArchiveTask(db,session,project,task);
    if(writing && state!="pending" && state!="running" && state!="waiting_confirmation" && state!="waiting_execution")
        throw AppError(409,"task_inactive","task is not active");
    if(writing && state=="running" && !db.Query("SELECT task_id FROM agent_runs WHERE task_id=? "
        "AND started_at+INTERVAL 180 SECOND<=UTC_TIMESTAMP(6) FOR UPDATE",{SqlValue(task)}).empty())
        throw AppError(409,"budget_exhausted","task budget is exhausted");
    Scope(db,project,task,"","",exclusive);
    return state;
}

void RequireAgentTaskScope(MySqlConnection& db,const SessionContext& session,const std::string& project,
    const std::string& task,const std::string& file,const std::string& version) {
    // ponytail: one short task-read transaction at a time; replace the guard with worker admission if measured throughput requires it.
    LockQueue(db); const auto row=Task(db,session,project,task);
    if(row.String(1)!="pending" && row.String(1)!="running") throw AppError(409,"task_inactive","task is not active");
    Scope(db,project,task,file,version);
    if(row.String(1)=="pending") {
        if(db.Query("SELECT id FROM agent_tasks WHERE state='running' LIMIT 2 FOR UPDATE").size()>=2)
            throw AppError(429,"agent_busy","agent execution slots are full",true);
        db.Execute("UPDATE agent_tasks SET state='running' WHERE id=?",{SqlValue(task)});
    }
}

std::string AgentTaskService::BeginToolCall(const SessionContext& session,const std::string& project,
    const std::string& task,bool search,const Json& body) {
    Json parameters=Json::object();
    if(search) {
        if(body.is_object() && body.contains("mode") && body["mode"].is_string() &&
            (body["mode"]=="metadata" || body["mode"]=="keyword" || body["mode"]=="hybrid"))
            parameters["mode"]=body["mode"];
        if(body.is_object() && body.contains("query") && body["query"].is_string())
            parameters["query_bytes"]=body["query"].get_ref<const std::string&>().size();
    } else parameters["selector"]=body.is_object() && body.contains("chunk_id") ? "chunk" :
        body.is_object() && body.contains("range") ? "range" : "status";
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db,session.user_id,project,Role::Reader);
    const auto row=Task(db,session,project,task);
    if(row.String(1)!="pending" && row.String(1)!="running")
        throw AppError(409,"task_inactive","task is not active");
    // The task row serializes admission. A hybrid MCP call may issue multiple backend requests.
    const auto count=db.ScalarInt("SELECT COUNT(*) FROM agent_tool_calls WHERE task_id=?",{SqlValue(task)});
    if(count>=64) throw AppError(429,"agent_audit_full","task backend call limit reached");
    const auto request=GenerateId();
    db.Execute("INSERT INTO agent_tool_calls(task_id,sequence,request_id,tool,parameters) VALUES (?,?,?,?,?)",
        {SqlValue(task),SqlValue(count+1),SqlValue(request),
         SqlValue(search ? "search_documents" : "read_document_version"),SqlValue(parameters.dump())});
    transaction.Commit(); return request;
}

void AgentTaskService::FinishToolCall(const std::string& request,const std::string& status,uint64_t duration,const Json& data) {
    const auto sources=data.is_null() ? SqlValue::Null() : SqlValue(ExtractToolSources(data).dump());
    auto db=pool_.Acquire();
    if(db.Execute("UPDATE agent_tool_calls SET status=?,duration_ms=?,source_json=? WHERE request_id=? AND status='unknown'",
        {SqlValue(status),SqlValue(duration),sources,SqlValue(request)})!=1)
        throw AppError(503,"agent_unavailable","tool outcome could not be recorded");
}

nlohmann::json AgentTaskService::ToolCalls(const SessionContext& session,const std::string& project,const std::string& task) {
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db,session.user_id,project,Role::Reader);
    Task(db,session,project,task); Scope(db,project,task);
    Json items=Json::array();
    for(const auto& row:db.Query("SELECT sequence,request_id,tool,parameters,status,started_at,duration_ms "
        "FROM agent_tool_calls WHERE task_id=? ORDER BY sequence LIMIT 64",{SqlValue(task)}))
        items.push_back({{"task_id",task},{"sequence",row.UInt64(0)},{"request_id",row.String(1)},
            {"tool",row.String(2)},{"parameters",Json::parse(row.String(3))},{"status",row.String(4)},
            {"started_at",row.String(5)},{"duration_ms",row.IsNull(6) ? Json(nullptr) : Json(row.UInt64(6))}});
    transaction.Commit(); return {{"kind","backend_requests"},{"capacity",64},{"items",items}};
}
