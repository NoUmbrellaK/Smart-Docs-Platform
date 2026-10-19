#include "agent_tasks.h"
#include "core/app_error.h"
#include "core/crypto.h"
#include "core/id.h"
#include "db/mysql.h"
#include "http/jsonbody.h"
#include "project/project_service.h"
#include "project/project_repository.h"
#include <algorithm>
#include <set>

namespace {
using Json = nlohmann::json;
[[noreturn]] void Invalid() { throw AppError(400,"invalid_request","archive plan is invalid"); }
[[noreturn]] void Conflict() { throw AppError(409,"archive_plan_changed","archive plan must be reviewed"); }
void Id(const Json& id) {
    if(!id.is_string()) Invalid();
    const auto& text=id.get_ref<const std::string&>();
    if(text.size()!=32 || text.find_first_not_of("0123456789abcdef")!=std::string::npos) Invalid();
}
void Validate(const Json& body) {
    RequireExactObject(body,{"expected_revision","policy_revision","items"});
    for(const char* key:{"expected_revision","policy_revision"})
        if(!body[key].is_number_unsigned() || body[key].get<uint64_t>()>=4294967295ULL) Invalid();
    if(!body["items"].is_array() || body["items"].empty() || body["items"].size()>100 || body.dump().size()>65536) Invalid();
    std::set<std::string> seen;
    for(const auto& item:body["items"]) {
        RequireExactObject(item,{"file_id","version_id","source_directory_id","target_directory_id","reason"});
        for(const char* key:{"file_id","version_id","source_directory_id"}) Id(item[key]);
        if(!item["target_directory_id"].is_null()) Id(item["target_directory_id"]);
        if(!seen.insert(item["file_id"].get<std::string>()).second || !item["reason"].is_string()) Invalid();
        const auto& reason=item["reason"].get_ref<const std::string&>();
        if(reason.size()>1000 || reason.find_first_not_of(" \t\r\n")==std::string::npos ||
            std::any_of(reason.begin(),reason.end(),[](unsigned char c){return (c<32 && c!=9 && c!=10 && c!=13) || c==127;})) Invalid();
    }
}
Json PreviewItems(MySqlConnection& db,const std::string& project,const std::string& task,const Json& body) {
    const auto policy=ProjectService::ApprovedArchivePolicy(db,project,body["policy_revision"].get<uint64_t>());
    Json items=Json::array();
    for(const auto& item:body["items"]) {
        const auto file=item["file_id"].get<std::string>(), version=item["version_id"].get<std::string>();
        if(db.Query("SELECT file_id FROM agent_task_versions WHERE task_id=? AND file_id=? AND version_id=? FOR SHARE",
            {SqlValue(task),SqlValue(file),SqlValue(version)}).empty())
            throw AppError(404,"resource_not_found","resource was not found");
        const auto rows=db.Query("SELECT name,directory_id FROM files WHERE project_id=? AND id=? FOR SHARE",
            {SqlValue(project),SqlValue(file)});
        if(rows.empty() || rows[0].String(1)!=item["source_directory_id"].get<std::string>()) Conflict();
        Json preview=item;
        preview["name"]=rows[0].String(0); preview["target_name"]=nullptr;
        preview["state"]=item["target_directory_id"].is_null() ? "needs_review" : "proposed";
        if(!item["target_directory_id"].is_null()) {
            const auto target=item["target_directory_id"].get<std::string>();
            bool allowed=false;
            for(const auto& directory:policy["targets"]) if(directory["directory_id"]==target) {
                preview["target_name"]=directory["name"]; allowed=true; break;
            }
            if(!allowed || target==rows[0].String(1)) Conflict();
            if(!db.Query("SELECT id FROM files WHERE project_id=? AND directory_id=? AND active_name=? LIMIT 1 FOR SHARE",
                {SqlValue(project),SqlValue(target),SqlValue(rows[0].String(0))}).empty())
                throw AppError(409,"name_conflict","archive target has a name conflict");
        }
        items.push_back(std::move(preview));
    }
    // Match the files.active_name unique key's collation, not C++ byte equality.
    if(!db.Query("SELECT target FROM JSON_TABLE(?, '$[*]' COLUMNS ("
        "target CHAR(32) PATH '$.target_directory_id', "
        "name VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci PATH '$.name')) AS proposed "
        "WHERE target IS NOT NULL GROUP BY target,name HAVING COUNT(*)>1 LIMIT 1",{SqlValue(items.dump())}).empty())
        throw AppError(409,"name_conflict","archive target has a name conflict");
    return items;
}
std::string Digest(const Json& preview) {
    const auto canonical=preview.dump(); return Sha256Hex(canonical.data(),canonical.size());
}
Json View(MySqlConnection& db,const Json& base,const std::string& state) {
    Json view=base;
    view["content_sha256"]=Digest(base); view["decision"]=nullptr;
    view["operation_id"]=nullptr; view["task_state"]=state;
    const auto rows=db.Query("SELECT decision,operation_id,result_json FROM archive_decisions WHERE task_id=? AND revision=? FOR UPDATE",
        {SqlValue(base["task_id"].get<std::string>()),SqlValue(base["revision"].get<uint64_t>())});
    if(!rows.empty()) {
        view["decision"]=rows[0].String(0);
        view["operation_id"]=rows[0].IsNull(1) ? Json(nullptr) : Json(rows[0].String(1));
        view["state"]=!rows[0].IsNull(2) ? "executed" : rows[0].String(0)=="confirm" ? "confirmed" : "rejected";
        view["executable"]=view["state"]=="confirmed" && state=="waiting_execution";
    }
    return view;
}
void Identity(const Json& body,bool decision) {
    if(decision) RequireExactObject(body,{"plan_id","revision","content_sha256","decision"});
    else RequireExactObject(body,{"plan_id","revision","operation_id"});
    Id(body["plan_id"]);
    if(!body["revision"].is_number_unsigned() || body["revision"]==0 || body["revision"].get<uint64_t>()>4294967295ULL) Invalid();
    if(!decision) { Id(body["operation_id"]); return; }
    if(!body["content_sha256"].is_string()) Invalid();
    const auto hash=body["content_sha256"].get<std::string>();
    if(hash.size()!=64 || hash.find_first_not_of("0123456789abcdef")!=std::string::npos ||
        (body["decision"]!="confirm" && body["decision"]!="reject")) Invalid();
}
MySqlRow Plan(MySqlConnection& db,const std::string& task) {
    const auto rows=db.Query("SELECT request_json,preview_json FROM archive_plans WHERE task_id=? FOR UPDATE",{SqlValue(task)});
    if(rows.empty()) throw AppError(404,"resource_not_found","resource was not found");
    return rows[0];
}
void Match(const Json& body,const Json& preview) {
    if(body["plan_id"]!=preview["id"] || body["revision"]!=preview["revision"]) Conflict();
}
}

nlohmann::json AgentTaskService::SaveArchivePlan(const SessionContext& session,const std::string& project,
    const std::string& task,const Json& body,const std::string& request_id) {
    Validate(body);
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db,session.user_id,project,Role::Editor);
    const auto state=RequireArchiveTaskScope(db,session,project,task,true);
    const auto rows=db.Query("SELECT id,revision,request_json,preview_json FROM archive_plans WHERE task_id=? FOR UPDATE",{SqlValue(task)});
    const auto items=PreviewItems(db,project,task,body);
    const uint64_t revision=rows.empty() ? 0 : rows[0].UInt64(1), expected=body["expected_revision"].get<uint64_t>();
    if(expected!=revision) {
        if(revision==expected+1 && !rows.empty() && Json::parse(rows[0].String(2))==body) {
            auto old=Json::parse(rows[0].String(3));
            if(old["items"]!=items) Conflict();
            auto view=View(db,old,state); transaction.Commit(); return view;
        }
        Conflict();
    }
    const auto id=rows.empty() ? GenerateId() : rows[0].String(0);
    Json preview={{"id",id},{"task_id",task},{"project_id",project},{"revision",revision+1},
        {"policy_revision",body["policy_revision"]},{"state","preview"},{"executable",false},
        {"has_unresolved",std::any_of(items.begin(),items.end(),[](const Json& item){return item["target_directory_id"].is_null();})},
        {"items",items}};
    db.Execute("INSERT INTO archive_plans(task_id,project_id,id,revision,request_json,preview_json) VALUES (?,?,?,?,?,?) "
        "ON DUPLICATE KEY UPDATE revision=VALUES(revision),request_json=VALUES(request_json),preview_json=VALUES(preview_json)",
        {SqlValue(task),SqlValue(project),SqlValue(id),SqlValue(revision+1),SqlValue(body.dump()),SqlValue(preview.dump())});
    ProjectRepository().InsertAudit(db,session.user_id,project,"archive.plan.save","archive_plan",id,request_id);
    db.Execute("UPDATE agent_tasks SET state='waiting_confirmation' WHERE id=?",{SqlValue(task)});
    auto view=View(db,preview,"waiting_confirmation"); transaction.Commit(); return view;
}

nlohmann::json AgentTaskService::GetArchivePlan(const SessionContext& session,const std::string& project,const std::string& task) {
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db,session.user_id,project,Role::Reader);
    const auto state=RequireArchiveTaskScope(db,session,project,task,false);
    const auto row=Plan(db,task);
    const auto preview=Json::parse(row.String(1));
    auto view=View(db,preview,state);
    if(view["state"]=="executed") ProjectService::ApprovedArchivePolicy(db,project,preview["policy_revision"].get<uint64_t>());
    else if(PreviewItems(db,project,task,Json::parse(row.String(0)))!=preview["items"]) Conflict();
    transaction.Commit(); return view;
}

nlohmann::json AgentTaskService::DecideArchivePlan(const SessionContext& session,const std::string& project,
    const std::string& task,const Json& body,const std::string& request_id) {
    Identity(body,true);
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db,session.user_id,project,Role::Editor);
    const auto state=RequireArchiveTaskScope(db,session,project,task,true);
    const auto row=Plan(db,task); const auto preview=Json::parse(row.String(1));
    Match(body,preview);
    if(body["content_sha256"]!=Digest(preview) || PreviewItems(db,project,task,Json::parse(row.String(0)))!=preview["items"]) Conflict();
    const bool confirm=body["decision"]=="confirm";
    if(confirm && preview["has_unresolved"].get<bool>()) Conflict();
    auto view=View(db,preview,state);
    if(!view["decision"].is_null()) {
        if(view["decision"]!=body["decision"]) Conflict();
        transaction.Commit(); return view;
    }
    db.Execute("INSERT INTO archive_decisions(task_id,revision,plan_id,content_sha256,actor_user_id,decision,operation_id) VALUES (?,?,?,?,?,?,?)",
        {SqlValue(task),SqlValue(body["revision"].get<uint64_t>()),SqlValue(body["plan_id"].get<std::string>()),
         SqlValue(Digest(preview)),SqlValue(session.user_id),SqlValue(body["decision"].get<std::string>()),confirm ? SqlValue(GenerateId()) : SqlValue::Null()});
    const std::string next=confirm ? "waiting_execution" : "waiting_confirmation";
    db.Execute("UPDATE agent_tasks SET state=? WHERE id=?",{SqlValue(next),SqlValue(task)});
    ProjectRepository().InsertAudit(db,session.user_id,project,confirm ? "archive.confirm" : "archive.reject","archive_plan",body["plan_id"],request_id);
    view=View(db,preview,next); transaction.Commit(); return view;
}

nlohmann::json AgentTaskService::ExecuteArchivePlan(const SessionContext& session,const std::string& project,
    const std::string& task,const Json& body,const std::string& request_id) {
    Identity(body,false);
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db,session.user_id,project,Role::Editor);
    const auto state=RequireArchiveTaskScope(db,session,project,task,false,true);
    const auto row=Plan(db,task); const auto preview=Json::parse(row.String(1)); Match(body,preview);
    const auto decisions=db.Query("SELECT decision,operation_id,content_sha256,actor_user_id,result_json FROM archive_decisions "
        "WHERE task_id=? AND revision=? FOR UPDATE",{SqlValue(task),SqlValue(body["revision"].get<uint64_t>())});
    if(decisions.empty()) Conflict();
    const auto& decision=decisions[0];
    if(decision.String(0)!="confirm" || decision.IsNull(1) || decision.String(1)!=body["operation_id"] ||
        decision.String(2)!=Digest(preview) || decision.String(3)!=session.user_id) Conflict();
    if(!decision.IsNull(4)) { auto result=Json::parse(decision.String(4)); transaction.Commit(); return result; }
    if(state!="waiting_execution" || preview["has_unresolved"].get<bool>() ||
        PreviewItems(db,project,task,Json::parse(row.String(0)))!=preview["items"]) Conflict();
    Json items=Json::array();
    for(const auto& item:preview["items"]) {
        db.Execute("UPDATE files SET directory_id=? WHERE id=? AND project_id=?",
            {SqlValue(item["target_directory_id"].get<std::string>()),SqlValue(item["file_id"].get<std::string>()),SqlValue(project)});
        items.push_back({{"file_id",item["file_id"]},{"version_id",item["version_id"]},{"source_directory_id",item["source_directory_id"]},
            {"target_directory_id",item["target_directory_id"]},{"state","moved"}});
    }
    ProjectRepository().InsertAudit(db,session.user_id,project,"archive.execute","archive_plan",body["plan_id"],request_id);
    const auto audit=db.Query("SELECT id FROM audit_records WHERE request_id=? AND action='archive.execute' AND object_id=?",
        {SqlValue(request_id),SqlValue(body["plan_id"].get<std::string>())});
    Json result={{"operation_id",body["operation_id"]},{"plan_id",body["plan_id"]},{"revision",body["revision"]},
        {"status","executed"},{"moved_count",items.size()},{"audit_id",audit.at(0).String(0)},{"items",items}};
    db.Execute("UPDATE archive_decisions SET result_json=? WHERE task_id=? AND revision=?",
        {SqlValue(result.dump()),SqlValue(task),SqlValue(body["revision"].get<uint64_t>())});
    db.Execute("UPDATE agent_tasks SET state='completed' WHERE id=?",{SqlValue(task)});
    transaction.Commit(); return result;
}

nlohmann::json CommittedArchiveReceipt(MySqlConnection& db,const std::string& task) {
    const auto rows=db.Query("SELECT result_json FROM archive_decisions WHERE task_id=? AND result_json IS NOT NULL FOR SHARE",{SqlValue(task)});
    if(rows.empty()) return nullptr;
    const auto result=Json::parse(rows[0].String(0)); Json receipt=Json::object();
    for(const char* key:{"operation_id","plan_id","revision","status","moved_count","audit_id"}) receipt[key]=result.at(key);
    return receipt;
}
nlohmann::json AgentTaskService::ArchiveResult(const SessionContext& session,const std::string& project,const std::string& task) {
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db,session.user_id,project,Role::Reader); LockArchiveTask(db,session,project,task);
    auto receipt=CommittedArchiveReceipt(db,task);
    if(receipt.is_null()) throw AppError(404,"resource_not_found","resource was not found");
    transaction.Commit(); return receipt;
}
