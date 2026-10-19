#include "project_service.h"
#include "project_repository.h"
#include "core/app_error.h"
#include "http/jsonbody.h"
#include <set>

namespace {
using Json = nlohmann::json;
void Invalid() { throw AppError(400,"invalid_request","archive policy is invalid"); }
void Conflict() { throw AppError(409,"archive_policy_changed","archive policy must be reviewed"); }
void LockProject(MySqlConnection& db, const std::string& project, bool writing) {
    if(db.Query("SELECT id FROM projects WHERE id=? FOR "+std::string(writing ? "UPDATE" : "SHARE"),
        {SqlValue(project)}).empty())
        throw AppError(404,"resource_not_found","resource was not found");
}
Json Policy(MySqlConnection& db,const std::string& project) {
    const auto rows=db.Query("SELECT revision,targets_json,remote_ai_approved FROM archive_policies WHERE project_id=? FOR SHARE",
        {SqlValue(project)});
    if(rows.empty()) return {{"revision",0},{"targets",Json::array()},{"remote_ai_approved",false}};
    return {{"revision",rows[0].UInt64(0)},{"targets",Json::parse(rows[0].String(1))},{"remote_ai_approved",rows[0].Bool(2)}};
}
void CheckTargets(MySqlConnection& db,const std::string& project,const Json& targets) {
    for(const auto& target:targets) {
        const auto rows=db.Query("SELECT name FROM directories WHERE project_id=? AND id=? FOR SHARE",
            {SqlValue(project),SqlValue(target["directory_id"].get<std::string>())});
        if(rows.empty()) throw AppError(404,"resource_not_found","resource was not found");
        if(rows[0].String(0)!=target["name"].get<std::string>()) Conflict();
    }
}
}

nlohmann::json ProjectService::ArchivePolicy(const SessionContext& session,const std::string& project) {
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    RequireRole(db,session.user_id,project,Role::Reader); LockProject(db,project,false);
    auto result=Policy(db,project); transaction.Commit(); return result;
}

nlohmann::json ProjectService::SetArchivePolicy(const SessionContext& session,const std::string& project,
    const Json& body,const std::string& request_id) {
    RequireExactObject(body,{"expected_revision","targets","confirm_remote_ai"});
    if(!body["expected_revision"].is_number_unsigned() || body["expected_revision"].get<uint64_t>()>=4294967295ULL ||
        !body["confirm_remote_ai"].is_boolean() || !body["targets"].is_array() || body["targets"].size()>100) Invalid();
    std::set<std::string> seen;
    for(const auto& target:body["targets"]) {
        RequireExactObject(target,{"directory_id","name"});
        if(!target["directory_id"].is_string() || !target["name"].is_string()) Invalid();
        const auto id=target["directory_id"].get<std::string>();
        if(id.size()!=32 || id.find_first_not_of("0123456789abcdef")!=std::string::npos || !seen.insert(id).second ||
            target["name"].get_ref<const std::string&>().empty() || target["name"].get_ref<const std::string&>().size()>255) Invalid();
    }
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    RequireRole(db,session.user_id,project,Role::Admin); LockProject(db,project,true);
    const auto current=Policy(db,project);
    const auto expected=body["expected_revision"].get<uint64_t>(), revision=current["revision"].get<uint64_t>();
    CheckTargets(db,project,body["targets"]);
    if(expected!=revision) {
        const auto old=db.Query("SELECT last_request_json FROM archive_policies WHERE project_id=? FOR SHARE",{SqlValue(project)});
        if(revision==expected+1 && !old.empty() && !old[0].IsNull(0) && Json::parse(old[0].String(0))==body) {
            transaction.Commit(); return current;
        }
        Conflict();
    }
    const bool approved=body["confirm_remote_ai"].get<bool>();
    db.Execute("INSERT INTO archive_policies(project_id,revision,targets_json,remote_ai_approved,approved_by,approved_at,last_request_json) "
        "VALUES (?,?,?,?,?,IF(?,UTC_TIMESTAMP(6),NULL),?) ON DUPLICATE KEY UPDATE revision=VALUES(revision),"
        "targets_json=VALUES(targets_json),remote_ai_approved=VALUES(remote_ai_approved),approved_by=VALUES(approved_by),"
        "approved_at=VALUES(approved_at),last_request_json=VALUES(last_request_json)",
        {SqlValue(project),SqlValue(revision+1),SqlValue(body["targets"].dump()),SqlValue(approved),
         approved ? SqlValue(session.user_id) : SqlValue::Null(),SqlValue(approved),SqlValue(body.dump())});
    repository_->InsertAudit(db,session.user_id,project,"archive.policy.set","project",project,request_id);
    auto result=Policy(db,project); transaction.Commit(); return result;
}

nlohmann::json ProjectService::ApprovedArchivePolicy(MySqlConnection& db,const std::string& project,uint64_t revision) {
    LockProject(db,project,false);
    auto policy=Policy(db,project);
    if(!policy["remote_ai_approved"].get<bool>() || policy["revision"]!=revision) Conflict();
    CheckTargets(db,project,policy["targets"]);
    return policy;
}

void ProjectService::InvalidateArchiveTarget(MySqlConnection& db,const std::string& project,const std::string& directory) {
    const auto policy=Policy(db,project);
    for(const auto& target:policy["targets"]) if(target["directory_id"]==directory) {
        db.Execute("UPDATE archive_policies SET revision=revision+1,remote_ai_approved=FALSE,approved_by=NULL,"
            "approved_at=NULL,last_request_json=NULL WHERE project_id=?",{SqlValue(project)});
        return;
    }
}
