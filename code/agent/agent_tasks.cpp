#include "agent_tasks.h"
#include "core/app_error.h"
#include "core/id.h"
#include "db/mysql.h"
#include "project/project_service.h"
#include "http/jsonbody.h"
#include <algorithm>
#include <map>

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
bool Allowed(MySqlConnection& db,const std::string& project,const std::string& file,const std::string& version) {
    const auto files=db.Query("SELECT current_version_id,remote_ai_policy,deleted_at FROM files WHERE id=? AND project_id=? FOR SHARE",
        {SqlValue(file),SqlValue(project)});
    if(files.empty() || files[0].IsNull(0) || files[0].String(0)!=version || files[0].String(1)!="remote_ai_allowed" || !files[0].IsNull(2)) return false;
    const auto versions=db.Query("SELECT remote_ai_approved,availability_state FROM file_versions WHERE id=? AND file_id=? FOR SHARE",
        {SqlValue(version),SqlValue(file)});
    return !versions.empty() && versions[0].Bool(0) && versions[0].String(1)=="available";
}
Json Status(MySqlConnection& db,const MySqlRow& row,const std::string& project) {
    Json scope=Json::array();
    for(const auto& v:db.Query("SELECT file_id,version_id FROM agent_task_versions WHERE task_id=? ORDER BY file_id FOR SHARE",{SqlValue(row.String(0))}))
        scope.push_back({{"file_id",v.String(0)},{"version_id",v.String(1)}});
    return {{"id",row.String(0)},{"project_id",project},{"state",row.String(1)},
        {"created_at",row.String(2)},{"updated_at",row.String(3)},{"confirmed_at",row.String(4)},{"versions",scope}};
}
}

nlohmann::json AgentTaskService::Create(const SessionContext& session,const std::string& project,const nlohmann::json& body) {
    Id(project); RequireExactObject(body,{"question","versions","confirm_remote_ai"});
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
    if(db.Query("SELECT id FROM agent_tasks WHERE state IN ('pending','running') LIMIT 100 FOR UPDATE").size()>=100)
        throw AppError(429,"agent_busy","agent task queue is full",true);
    for(const auto& item:versions) if(!Allowed(db,project,item.first,item.second)) Missing();
    const auto id=GenerateId();
    db.Execute("INSERT INTO agent_tasks(id,owner_user_id,project_id,question) VALUES (?,?,?,?)",
        {SqlValue(id),SqlValue(session.user_id),SqlValue(project),SqlValue(question)});
    for(const auto& item:versions)
        db.Execute("INSERT INTO agent_task_versions(task_id,project_id,file_id,version_id) VALUES (?,?,?,?)",
            {SqlValue(id),SqlValue(project),SqlValue(item.first),SqlValue(item.second)});
    auto result=Status(db,Task(db,session,project,id),project); transaction.Commit(); return result;
}

nlohmann::json AgentTaskService::Get(const SessionContext& session,const std::string& project,const std::string& task) {
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db,session.user_id,project,Role::Reader);
    auto result=Status(db,Task(db,session,project,task),project); transaction.Commit(); return result;
}
nlohmann::json AgentTaskService::Cancel(const SessionContext& session,const std::string& project,const std::string& task) {
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db,session.user_id,project,Role::Reader); Task(db,session,project,task);
    db.Execute("UPDATE agent_tasks SET state='cancelled' WHERE id=? AND state IN ('pending','running','interrupted')",{SqlValue(task)});
    auto result=Status(db,Task(db,session,project,task),project); transaction.Commit(); return result;
}
void AgentTaskService::RecoverInterrupted() {
    auto db=pool_.Acquire(); MySqlTransaction transaction(db); LockQueue(db);
    db.Execute("UPDATE agent_tasks SET state='interrupted' WHERE state='running'"); transaction.Commit();
}

void RequireAgentTaskScope(MySqlConnection& db,const SessionContext& session,const std::string& project,
    const std::string& task,const std::string& file,const std::string& version) {
    // ponytail: one short task-read transaction at a time; replace the guard with worker admission if measured throughput requires it.
    LockQueue(db); const auto row=Task(db,session,project,task);
    if(row.String(1)!="pending" && row.String(1)!="running") throw AppError(409,"task_inactive","task is not active");
    const auto scope=db.Query("SELECT file_id,version_id FROM agent_task_versions WHERE task_id=? ORDER BY file_id FOR SHARE",{SqlValue(task)});
    bool contains=file.empty();
    if(scope.empty() || scope.size()>100) throw AppError(409,"task_scope_changed","task scope is unavailable");
    for(const auto& item:scope) {
        if(!Allowed(db,project,item.String(0),item.String(1)))
            throw AppError(409,"task_scope_changed","task scope is unavailable");
        if(item.String(0)==file && item.String(1)==version) contains=true;
    }
    if(!contains) Missing();
    if(row.String(1)=="pending") {
        if(db.Query("SELECT id FROM agent_tasks WHERE state='running' LIMIT 2 FOR UPDATE").size()>=2)
            throw AppError(429,"agent_busy","agent execution slots are full",true);
        db.Execute("UPDATE agent_tasks SET state='running' WHERE id=?",{SqlValue(task)});
    }
}
