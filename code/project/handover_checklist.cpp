#include "project_service.h"
#include "project_repository.h"
#include "core/app_error.h"
#include "db/mysql.h"
#include "http/jsonbody.h"
#include <algorithm>
#include <set>

namespace {
using Json = nlohmann::json;
[[noreturn]] void Invalid() {
    throw AppError(400, "invalid_request", "handover checklist is invalid");
}
void LockProject(MySqlConnection& db, const std::string& project, bool writing) {
    if (db.Query("SELECT id FROM projects WHERE id=? FOR " + std::string(writing ? "UPDATE" : "SHARE"),
                 {SqlValue(project)}).empty())
        throw AppError(404, "resource_not_found", "resource was not found");
}
Json Read(MySqlConnection& db, const std::string& project) {
    const auto rows = db.Query("SELECT revision,items_json,remote_ai_approved FROM handover_checklists "
                              "WHERE project_id=? FOR SHARE", {SqlValue(project)});
    if (rows.empty()) return {{"revision", 0}, {"items", Json::array()}, {"remote_ai_approved", false}};
    return {{"revision", rows[0].UInt64(0)}, {"items", Json::parse(rows[0].String(1))},
            {"remote_ai_approved", rows[0].Bool(2)}};
}
void Text(const Json& value, size_t maximum, bool required) {
    if (!value.is_string()) Invalid();
    const auto& text = value.get_ref<const std::string&>();
    if (text.size() > maximum || (required && text.find_first_not_of(" \t\r\n\f\v") == std::string::npos) ||
        std::any_of(text.begin(), text.end(), [](unsigned char c) { return c < 32 || c == 127; })) Invalid();
}
void Validate(const Json& body) {
    RequireExactObject(body, {"expected_revision", "confirm_remote_ai", "items"});
    if (!body["expected_revision"].is_number_unsigned() || body["expected_revision"].get<uint64_t>() >= 4294967295ULL ||
        !body["confirm_remote_ai"].is_boolean() || !body["items"].is_array() || body["items"].size() > 50) Invalid();
    std::set<std::string> seen;
    for (const auto& item : body["items"]) {
        RequireExactObject(item, {"key", "title", "description", "required"});
        if (!item["key"].is_string() || !item["required"].is_boolean()) Invalid();
        const auto& key = item["key"].get_ref<const std::string&>();
        if (key.empty() || key.size() > 64 || key[0] < 'a' || key[0] > 'z' ||
            key.find_first_not_of("abcdefghijklmnopqrstuvwxyz0123456789_") != std::string::npos ||
            !seen.insert(key).second) Invalid();
        Text(item["title"], 200, true);
        Text(item["description"], 1000, false);
    }
}
}

nlohmann::json ProjectService::HandoverChecklist(const SessionContext& session, const std::string& project) {
    auto db = pool_.Acquire(); MySqlTransaction transaction(db);
    RequireRole(db, session.user_id, project, Role::Reader);
    LockProject(db, project, false);
    auto result = Read(db, project);
    transaction.Commit(); return result;
}

nlohmann::json ProjectService::SetHandoverChecklist(const SessionContext& session, const std::string& project,
    const Json& body, const std::string& request_id) {
    Validate(body);
    auto db = pool_.Acquire(); MySqlTransaction transaction(db);
    RequireRole(db, session.user_id, project, Role::Admin);
    LockProject(db, project, true);
    const auto current = Read(db, project);
    const auto expected = body["expected_revision"].get<uint64_t>(), revision = current["revision"].get<uint64_t>();
    if (expected != revision) {
        const auto rows = db.Query("SELECT last_request_json FROM handover_checklists WHERE project_id=? FOR SHARE",
                                   {SqlValue(project)});
        if (revision == expected + 1 && !rows.empty() && Json::parse(rows[0].String(0)) == body) {
            transaction.Commit(); return current;
        }
        throw AppError(409, "handover_checklist_changed", "handover checklist must be reviewed");
    }
    const bool approved = body["confirm_remote_ai"].get<bool>();
    db.Execute("INSERT INTO handover_checklists(project_id,revision,items_json,remote_ai_approved,approved_by,approved_at,last_request_json) "
        "VALUES (?,?,?,?,?,IF(?,UTC_TIMESTAMP(6),NULL),?) ON DUPLICATE KEY UPDATE revision=VALUES(revision),"
        "items_json=VALUES(items_json),remote_ai_approved=VALUES(remote_ai_approved),approved_by=VALUES(approved_by),"
        "approved_at=VALUES(approved_at),last_request_json=VALUES(last_request_json)",
        {SqlValue(project), SqlValue(revision + 1), SqlValue(body["items"].dump()), SqlValue(approved),
         approved ? SqlValue(session.user_id) : SqlValue::Null(), SqlValue(approved), SqlValue(body.dump())});
    repository_->InsertAudit(db, session.user_id, project, "handover.checklist.set", "project", project, request_id);
    auto result = Read(db, project);
    transaction.Commit(); return result;
}
