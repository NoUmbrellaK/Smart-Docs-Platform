#include "agent_tasks.h"
#include "core/app_error.h"
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
}

nlohmann::json AgentTaskService::SaveArchivePlan(const SessionContext& session,const std::string& project,
    const std::string& task,const Json& body,const std::string& request_id) {
    Validate(body);
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db,session.user_id,project,Role::Editor);
    RequireArchiveTaskScope(db,session,project,task,true);
    const auto rows=db.Query("SELECT id,revision,request_json,preview_json FROM archive_plans WHERE task_id=? FOR UPDATE",{SqlValue(task)});
    const auto items=PreviewItems(db,project,task,body);
    const uint64_t revision=rows.empty() ? 0 : rows[0].UInt64(1), expected=body["expected_revision"].get<uint64_t>();
    if(expected!=revision) {
        if(revision==expected+1 && !rows.empty() && Json::parse(rows[0].String(2))==body) {
            auto old=Json::parse(rows[0].String(3));
            if(old["items"]!=items) Conflict();
            transaction.Commit(); return old;
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
    transaction.Commit(); return preview;
}

nlohmann::json AgentTaskService::GetArchivePlan(const SessionContext& session,const std::string& project,const std::string& task) {
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db,session.user_id,project,Role::Reader);
    RequireArchiveTaskScope(db,session,project,task,false);
    const auto rows=db.Query("SELECT request_json,preview_json FROM archive_plans WHERE task_id=? FOR SHARE",{SqlValue(task)});
    if(rows.empty()) throw AppError(404,"resource_not_found","resource was not found");
    auto preview=Json::parse(rows[0].String(1));
    if(PreviewItems(db,project,task,Json::parse(rows[0].String(0)))!=preview["items"]) Conflict();
    transaction.Commit(); return preview;
}
