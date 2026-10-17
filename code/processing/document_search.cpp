#include "processing_service.h"
#include "core/app_error.h"
#include "core/fault_injector.h"
#include "db/mysql.h"
#include "project/project_service.h"
#include "upload/upload_service.h"
#include <algorithm>
#include <set>

namespace {
using Json = nlohmann::json;
[[noreturn]] void Invalid() { throw AppError(400, "invalid_request", "search parameters are invalid"); }
bool IsEntityId(const std::string& value) {
    return value.size() == 32 && value.find_first_not_of("0123456789abcdef") == std::string::npos;
}
uint64_t Number(const Json& body, const char* key, uint64_t fallback, uint64_t maximum) {
    if (!body.contains(key)) return fallback;
    const auto& value = body.at(key);
    if (!value.is_number_integer() || (!value.is_number_unsigned() && value.get<int64_t>() <= 0)) Invalid();
    const auto result = value.get<uint64_t>();
    if (result == 0 || result > maximum) Invalid();
    return result;
}
std::string Text(const Json& body, const char* key, size_t maximum, bool optional = false) {
    if (optional && !body.contains(key)) return "";
    if (!body.contains(key) || !body.at(key).is_string()) Invalid();
    const auto value = body.at(key).get<std::string>();
    if (value.size() > maximum || std::any_of(value.begin(), value.end(), [](unsigned char c) { return c < 32 || c == 127; })) Invalid();
    try { (void)Json(value).dump(); } catch (const Json::exception&) { Invalid(); }
    return value;
}
const std::string joins = " FROM files f JOIN file_versions v ON v.id=f.current_version_id AND v.file_id=f.id "
    "LEFT JOIN document_results r ON r.version_id=v.id ";
const std::string ready = "v.availability_state='available' AND v.parse_state='completed' "
    "AND v.keyword_state='completed' AND r.source_sha256=v.sha256";
}

Json ProcessingService::Search(const SessionContext& session, const std::string& project, const Json& request) {
    if (!IsEntityId(project) || !request.is_object()) Invalid();
    const std::set<std::string> keys{"mode","query","directory_id","media_type","page","page_size"};
    for (auto it = request.begin(); it != request.end(); ++it) if (!keys.count(it.key())) Invalid();
    const auto mode = Text(request, "mode", 16), query = Text(request, "query", 256);
    if (mode != "metadata" && mode != "keyword") Invalid();
    const bool keyword = mode == "keyword";
    if (keyword && query.find_first_not_of(' ') == std::string::npos) Invalid();
    const auto directory = Text(request, "directory_id", 32, true);
    if (request.contains("directory_id") && !IsEntityId(directory)) Invalid();
    const auto raw_type = Text(request, "media_type", 255, true);
    const auto type = request.contains("media_type") ? ValidateUploadMediaType(raw_type) : "";
    const auto page = Number(request, "page", 1, 1000), limit = Number(request, "page_size", 10, 20);

    auto db = pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db, session.user_id, project, Role::Reader);
    if (!directory.empty() && db.Query("SELECT id FROM directories WHERE id=? AND project_id=?",
        {SqlValue(directory), SqlValue(project)}).empty())
        throw AppError(404, "resource_not_found", "resource was not found");
    std::string scope = "f.project_id=? AND f.deleted_at IS NULL";
    std::vector<SqlValue> scope_values{SqlValue(project)};
    if (!directory.empty()) { scope += " AND f.directory_id=?"; scope_values.emplace_back(directory); }
    if (!type.empty()) { scope += " AND v.media_type=?"; scope_values.emplace_back(type); }
    const auto count = db.Query("SELECT /*+ MAX_EXECUTION_TIME(5000) */ COUNT(*),COALESCE(SUM(" + ready + "),0)" +
        joins + " WHERE " + scope, scope_values)[0];
    const uint64_t total = count.UInt64(0), indexed = count.UInt64(1);

    std::string filter = scope + (keyword ? " AND " + ready : " AND LOCATE(?,f.name)>0");
    auto values = scope_values;
    values.emplace_back(query);
    const std::string match = "MATCH(c.body) AGAINST(? IN NATURAL LANGUAGE MODE)";
    // ponytail: bounded offset pagination; use a snapshot/keyset protocol if stable multi-page exports are needed.
    const auto candidates = db.Query("SELECT /*+ MAX_EXECUTION_TIME(5000) */ f.id,v.id," +
        (keyword ? std::string("c.id") : std::string("''")) + joins +
        (keyword ? "JOIN document_chunks c ON c.version_id=v.id " : "") + " WHERE " + filter +
        (keyword ? " AND " + match + " > 0 ORDER BY " + match + " DESC,c.id" : " ORDER BY f.id") +
        " LIMIT ? OFFSET ?", [&]() {
            auto parameters = values;
            if (keyword) parameters.emplace_back(query);
            parameters.emplace_back(limit + 1); parameters.emplace_back((page-1)*limit);
            return parameters;
        }());
    FaultInjector::Hit(FaultPoint::AfterSearchCandidates);

    // Snapshot candidates are never authority. Lock files first in a stable order,
    // then current-read version/result rows; keep membership/file locks until commit.
    std::set<std::string> file_ids;
    for (const auto& candidate : candidates) file_ids.insert(candidate.String(0));
    for (const auto& file : file_ids)
        db.Query("SELECT id FROM files WHERE id=? AND project_id=? FOR SHARE", {SqlValue(file), SqlValue(project)});
    Json items = Json::array(); bool changed = false;
    const bool more = candidates.size() > limit;
    for (size_t i = 0; i < std::min<size_t>(limit, candidates.size()); ++i) {
        const auto& candidate = candidates[i];
        auto current_values = scope_values;
        if (!keyword) current_values.emplace_back(query);
        current_values.emplace_back(candidate.String(0)); current_values.emplace_back(candidate.String(1));
        const auto current = db.Query("SELECT f.name,f.directory_id,v.media_type,v.parse_state,v.keyword_state,"
            "v.vector_state,v.availability_state,v.sha256,r.parser_revision,r.page_count,r.pages_without_text" +
            joins + " WHERE " + filter + " AND f.id=? AND v.id=? FOR SHARE", current_values);
        if (current.empty()) { changed = true; continue; }
        const auto& v = current[0];
        Json item = {{"file_id",candidate.String(0)},{"version_id",candidate.String(1)},
            {"name",v.String(0)},{"directory_id",v.String(1)},{"media_type",v.String(2)},
            {"parse_state",v.String(3)},{"keyword_state",v.String(4)},{"vector_state",v.String(5)},
            {"availability_state",v.String(6)}};
        if (keyword) {
            const auto chunks = db.Query("SELECT page,char_start,char_end,line_start,line_end,body FROM document_chunks "
                "WHERE id=? AND version_id=? FOR SHARE", {SqlValue(candidate.String(2)), SqlValue(candidate.String(1))});
            if (chunks.empty()) { changed = true; continue; }
            const auto& c = chunks[0];
            item["chunk"] = {{"chunk_id",candidate.String(2)},{"file_id",candidate.String(0)},
                {"version_id",candidate.String(1)},{"source_sha256",v.String(7)},{"parser_revision",v.String(8)},
                {"char_start",c.UInt64(1)},{"char_end",c.UInt64(2)},{"line_start",c.UInt64(3)},
                {"line_end",c.UInt64(4)},{"text",c.String(5)}};
            if (!c.IsNull(0)) item["chunk"]["page"] = c.UInt64(0);
            item["coverage"] = {{"page_count",v.UInt64(9)},{"pages_without_text",Json::parse(v.String(10))}};
        }
        items.push_back(std::move(item));
    }
    Json warnings = Json::array({"live_pagination"});
    if (keyword) {
        warnings.push_back("ngram_stopwords_apply"); warnings.push_back("vector_search_unavailable");
        if (total > indexed) warnings.push_back("unindexed_content_not_searched");
    }
    if (changed) warnings.push_back("scope_changed");
    if (more && page == 1000) warnings.push_back("page_limit_reached");
    Json response = {{"mode",mode},{"retrieval",keyword ? "keyword_only" : "metadata"},{"items",std::move(items)},
        {"page",page},{"page_size",limit},{"has_more",more},{"next_page",more && page < 1000 ? Json(page+1) : Json(nullptr)},
        {"truncated",more || changed},{"warnings",warnings},
        {"coverage",{{"current_files",total},{"keyword_ready_files",indexed},
                     {"unsearched_files",total-indexed},{"counts_are_snapshot",true}}}};
    transaction.Commit(); return response;
}
