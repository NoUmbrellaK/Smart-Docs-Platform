#include "processing_service.h"
#include "core/app_error.h"
#include "core/fault_injector.h"
#include "db/mysql.h"
#include "project/project_service.h"
#include "upload/upload_service.h"
#include "vector_result.h"
#include "core/crypto.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <map>
#include <mutex>
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
struct Candidate {
    std::string file, version, chunk, job, attempt;
    double similarity = 0, score = 0;
    bool lexical = false, semantic = false;
};
const size_t candidate_limit = 200;
std::mutex vector_scan_mutex;

// ponytail: exact scan for <=4096 windows, one scan at a time. Replace with an
// external vector index only when measured corpus size justifies it; never truncate a scan silently.
std::vector<Candidate> VectorCandidates(MySqlConnection& db, const std::string& scope,
    const std::vector<SqlValue>& values, const Json& embedding, std::string& failure) {
    std::unique_lock<std::mutex> lock(vector_scan_mutex, std::try_to_lock);
    if (!lock.owns_lock()) { failure="vector_search_busy"; return {}; }
    const auto started=std::chrono::steady_clock::now();
    const std::string from=joins+"JOIN document_chunks c ON c.version_id=v.id "
        "JOIN document_embeddings e ON e.chunk_id=c.id "
        "JOIN processing_jobs j ON j.id=e.job_id AND j.file_version_id=v.id "
        "WHERE "+scope+" AND "+ready+" AND v.vector_state='completed' "
        "AND j.task_type='vectorize' AND j.state='completed' AND j.attempt_token IS NOT NULL "
        "AND e.embedding_revision=?";
    auto parameters=values; parameters.emplace_back(kEmbeddingRevision);
    const auto count=db.Query("SELECT /*+ MAX_EXECUTION_TIME(5000) */ e.chunk_id"+from+" LIMIT 4097",parameters).size();
    if (count==0) { failure="vector_index_unavailable"; return {}; }
    if (count>4096) { failure="vector_scan_limit"; return {}; }
    std::vector<std::vector<double>> queries;
    for (const auto& window : embedding.at("chunks")[0].at("windows"))
        queries.push_back(window.at("vector").get<std::vector<double>>());
    std::map<std::string,Candidate> unique;
    try {
        for (size_t offset=0; offset<count; offset+=128) {
            if (std::chrono::steady_clock::now()-started>std::chrono::seconds(5)) {
                failure="vector_scan_timeout"; return {};
            }
            auto page_parameters=parameters; page_parameters.emplace_back(uint64_t(offset));
            const auto rows=db.Query("SELECT /*+ MAX_EXECUTION_TIME(5000) */ f.id,v.id,c.id,j.id,j.attempt_token,e.vector"+
                from+" ORDER BY e.chunk_id,e.window_ordinal LIMIT 128 OFFSET ?",page_parameters);
            for (const auto& row : rows) {
                const auto vector=Json::parse(row.String(5));
                if (!vector.is_array() || vector.size()!=384) { failure="vector_index_invalid"; return {}; }
                std::vector<double> coordinates; double norm=0;
                for (const auto& coordinate : vector) {
                    if (!coordinate.is_number()) { failure="vector_index_invalid"; return {}; }
                    const auto x=coordinate.get<double>();
                    if (!std::isfinite(x) || std::abs(x)>1.00001) { failure="vector_index_invalid"; return {}; }
                    norm+=x*x; coordinates.push_back(x);
                }
                if (std::abs(norm-1)>1e-5) { failure="vector_index_invalid"; return {}; }
                double similarity=0;
                for (const auto& query : queries) {
                    double dot=0; for (size_t i=0; i<384; ++i) dot+=query[i]*coordinates[i];
                    similarity=std::max(similarity,dot);
                }
                if (similarity<=0) continue;
                auto& candidate=unique[row.String(2)];
                candidate.file=row.String(0); candidate.version=row.String(1); candidate.chunk=row.String(2);
                candidate.job=row.String(3); candidate.attempt=row.String(4); candidate.semantic=true;
                candidate.similarity=std::max(candidate.similarity,similarity);
            }
        }
    } catch (const Json::exception&) { failure="vector_index_invalid"; return {}; }
    if (std::chrono::steady_clock::now()-started>std::chrono::seconds(5)) {
        failure="vector_scan_timeout"; return {};
    }
    std::vector<Candidate> result;
    for (auto& entry : unique) result.push_back(std::move(entry.second));
    std::sort(result.begin(),result.end(),[](const Candidate& a,const Candidate& b) {
        return a.similarity!=b.similarity ? a.similarity>b.similarity : a.chunk<b.chunk;
    });
    return result;
}
}

Json ProcessingService::Search(const SessionContext& session, const std::string& project, const Json& request) {
    if (!IsEntityId(project) || !request.is_object()) Invalid();
    const std::set<std::string> keys{"mode","query","directory_id","media_type","page","page_size","query_embedding"};
    for (auto it = request.begin(); it != request.end(); ++it) if (!keys.count(it.key())) Invalid();
    const auto mode = Text(request, "mode", 16), query = Text(request, "query", 256);
    if (mode != "metadata" && mode != "keyword" && mode != "hybrid") Invalid();
    const bool hybrid=mode=="hybrid", keyword=mode!="metadata";
    if (keyword && query.find_first_not_of(' ') == std::string::npos) Invalid();
    const auto directory = Text(request, "directory_id", 32, true);
    if (request.contains("directory_id") && !IsEntityId(directory)) Invalid();
    const auto raw_type = Text(request, "media_type", 255, true);
    const auto type = request.contains("media_type") ? ValidateUploadMediaType(raw_type) : "";
    const auto page = Number(request, "page", 1, 1000), limit = Number(request, "page_size", 10, 20);
    if (hybrid && (page-1)*limit>=candidate_limit) Invalid();
    if (request.contains("query_embedding")) {
        if (!hybrid || request.dump().size()>128*1024) Invalid();
        try {
            const auto id=Sha256Hex(query.data(),query.size());
            if (ValidateVectorResult(request.at("query_embedding"),Json::array({{{"chunk_id",id},{"text",query}}}))>8) Invalid();
        } catch (const AppError&) { Invalid(); }
    }

    auto db = pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db, session.user_id, project, Role::Reader);
    if (!directory.empty() && db.Query("SELECT id FROM directories WHERE id=? AND project_id=?",
        {SqlValue(directory), SqlValue(project)}).empty())
        throw AppError(404, "resource_not_found", "resource was not found");
    std::string scope = "f.project_id=? AND f.deleted_at IS NULL";
    if (hybrid) scope+=" AND f.remote_ai_policy='remote_ai_allowed' AND v.remote_ai_approved";
    std::vector<SqlValue> scope_values{SqlValue(project)};
    if (!directory.empty()) { scope += " AND f.directory_id=?"; scope_values.emplace_back(directory); }
    if (!type.empty()) { scope += " AND v.media_type=?"; scope_values.emplace_back(type); }
    const auto count = db.Query("SELECT /*+ MAX_EXECUTION_TIME(5000) */ COUNT(*),COALESCE(SUM(" + ready + "),0),"+
        "COALESCE(SUM("+ready+" AND v.vector_state='completed'),0)" +
        joins + " WHERE " + scope, scope_values)[0];
    const uint64_t total = count.UInt64(0), indexed = count.UInt64(1);

    std::string filter = scope + (keyword ? " AND " + ready : " AND LOCATE(?,f.name)>0");
    auto values = scope_values;
    values.emplace_back(query);
    const std::string match = "MATCH(c.body) AGAINST(? IN NATURAL LANGUAGE MODE)";
    // ponytail: bounded offset pagination; use a snapshot/keyset protocol if stable multi-page exports are needed.
    const auto rows = db.Query("SELECT /*+ MAX_EXECUTION_TIME(5000) */ f.id,v.id," +
        (keyword ? std::string("c.id") : std::string("''")) + joins +
        (keyword ? "JOIN document_chunks c ON c.version_id=v.id " : "") + " WHERE " + filter +
        (keyword ? " AND " + match + " > 0 ORDER BY " + match + " DESC,c.id" : " ORDER BY f.id") +
        " LIMIT ? OFFSET ?", [&]() {
            auto parameters = values;
            if (keyword) parameters.emplace_back(query);
            parameters.emplace_back(hybrid ? candidate_limit+1 : limit+1);
            parameters.emplace_back(hybrid ? 0 : (page-1)*limit);
            return parameters;
        }());
    std::vector<Candidate> candidates;
    for (const auto& row : rows) {
        Candidate candidate; candidate.file=row.String(0); candidate.version=row.String(1); candidate.chunk=row.String(2);
        candidate.lexical=keyword; candidate.score=1.0/(61+candidates.size()); candidates.push_back(candidate);
    }
    bool used_vectors=false, capped=false;
    Json warnings=Json::array({"live_pagination"});
    if (hybrid) {
        std::string failure="query_embedding_unavailable";
        std::vector<Candidate> vectors;
        if (request.contains("query_embedding")) {
            failure.clear();
            try {
                FaultInjector::Hit(FaultPoint::BeforeVectorSearch);
                vectors=VectorCandidates(db,scope,scope_values,request.at("query_embedding"),failure);
            } catch (const AppError& error) {
                if (error.code!="database_query_timeout") throw;
                failure="vector_scan_timeout";
            }
        }
        used_vectors=failure.empty();
        if (!failure.empty()) warnings.push_back(failure);
        capped=candidates.size()>candidate_limit || vectors.size()>candidate_limit;
        if (candidates.size()>candidate_limit) candidates.resize(candidate_limit);
        std::map<std::string,Candidate> fused;
        for (const auto& candidate : candidates) fused[candidate.chunk]=candidate;
        for (size_t i=0; i<std::min(candidate_limit,vectors.size()); ++i) {
            auto& candidate=fused[vectors[i].chunk]; const auto lexical=candidate.lexical;
            const auto score=candidate.score; candidate=vectors[i]; candidate.lexical=lexical;
            candidate.score=score+1.0/(61+i);
        }
        candidates.clear(); for (auto& entry : fused) candidates.push_back(std::move(entry.second));
        std::sort(candidates.begin(),candidates.end(),[](const Candidate& a,const Candidate& b) {
            return a.score!=b.score ? a.score>b.score : a.chunk<b.chunk;
        });
        capped=capped || candidates.size()>candidate_limit;
        if (candidates.size()>candidate_limit) candidates.resize(candidate_limit);
        const auto offset=std::min<size_t>((page-1)*limit,candidates.size());
        candidates.erase(candidates.begin(),candidates.begin()+offset);
        if (candidates.size()>limit+1) candidates.resize(limit+1);
        if (capped) warnings.push_back("candidate_limit_reached");
    }
    FaultInjector::Hit(FaultPoint::AfterSearchCandidates);

    // Snapshot candidates are never authority. Lock files first in a stable order,
    // then current-read version/result rows; keep membership/file locks until commit.
    std::set<std::string> file_ids;
    for (const auto& candidate : candidates) file_ids.insert(candidate.file);
    for (const auto& file : file_ids)
        db.Query("SELECT id FROM files WHERE id=? AND project_id=? FOR SHARE", {SqlValue(file), SqlValue(project)});
    Json items = Json::array(); bool changed = false;
    const bool more = candidates.size() > limit;
    for (size_t i = 0; i < std::min<size_t>(limit, candidates.size()); ++i) {
        const auto& candidate = candidates[i];
        auto current_values = scope_values;
        if (!keyword) current_values.emplace_back(query);
        current_values.emplace_back(candidate.file); current_values.emplace_back(candidate.version);
        const auto current = db.Query("SELECT f.name,f.directory_id,v.media_type,v.parse_state,v.keyword_state,"
            "v.vector_state,v.availability_state,v.sha256,r.parser_revision,r.page_count,r.pages_without_text" +
            joins + " WHERE " + filter + " AND f.id=? AND v.id=? FOR SHARE", current_values);
        if (current.empty()) { changed = true; continue; }
        if (candidate.semantic && (current[0].String(5)!="completed" || db.Query("SELECT id FROM processing_jobs WHERE id=? AND file_version_id=? "
                "AND state='completed' AND attempt_token=? FOR SHARE",
                {SqlValue(candidate.job),SqlValue(candidate.version),SqlValue(candidate.attempt)}).empty())) {
            changed=true; continue;
        }
        const auto& v = current[0];
        Json item = {{"file_id",candidate.file},{"version_id",candidate.version},
            {"name",v.String(0)},{"directory_id",v.String(1)},{"media_type",v.String(2)},
            {"parse_state",v.String(3)},{"keyword_state",v.String(4)},{"vector_state",v.String(5)},
            {"availability_state",v.String(6)}};
        if (keyword) {
            const auto chunks = db.Query("SELECT page,char_start,char_end,line_start,line_end,body FROM document_chunks "
                "WHERE id=? AND version_id=? FOR SHARE", {SqlValue(candidate.chunk), SqlValue(candidate.version)});
            if (chunks.empty()) { changed = true; continue; }
            const auto& c = chunks[0];
            item["chunk"] = {{"chunk_id",candidate.chunk},{"file_id",candidate.file},
                {"version_id",candidate.version},{"source_sha256",v.String(7)},{"parser_revision",v.String(8)},
                {"char_start",c.UInt64(1)},{"char_end",c.UInt64(2)},{"line_start",c.UInt64(3)},
                {"line_end",c.UInt64(4)},{"text",c.String(5)}};
            if (!c.IsNull(0)) item["chunk"]["page"] = c.UInt64(0);
            item["coverage"] = {{"page_count",v.UInt64(9)},{"pages_without_text",Json::parse(v.String(10))}};
        }
        if (hybrid) {
            item["match_sources"]=Json::array();
            if (candidate.lexical) item["match_sources"].push_back("keyword");
            if (candidate.semantic) item["match_sources"].push_back("vector");
        }
        items.push_back(std::move(item));
    }
    if (keyword) {
        warnings.push_back("ngram_stopwords_apply");
        if (!used_vectors) warnings.push_back("vector_search_unavailable");
        if (total > indexed) warnings.push_back("unindexed_content_not_searched");
    }
    if (changed) warnings.push_back("scope_changed");
    if (more && page == 1000) warnings.push_back("page_limit_reached");
    Json response = {{"mode",mode},{"retrieval",used_vectors ? "hybrid" : keyword ? "keyword_only" : "metadata"},{"items",std::move(items)},
        {"page",page},{"page_size",limit},{"has_more",more},{"next_page",more && page < 1000 ? Json(page+1) : Json(nullptr)},
        {"truncated",more || changed || capped},{"warnings",warnings},
        {"coverage",{{"current_files",total},{"keyword_ready_files",indexed},
                     {"unsearched_files",total-indexed},{"counts_are_snapshot",true}}}};
    if (hybrid) {
        response["coverage"]["vector_completed_files"]=count.UInt64(2);
        if (count.UInt64(2)<indexed) response["warnings"].push_back("vector_index_partial");
    }
    transaction.Commit(); return response;
}
