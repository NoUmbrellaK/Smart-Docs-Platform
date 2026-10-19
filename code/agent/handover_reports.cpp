#include "agent_tasks.h"
#include "core/app_error.h"
#include "core/id.h"
#include "db/mysql.h"
#include "http/jsonbody.h"
#include "project/project_service.h"
#include "project/project_repository.h"
#include <algorithm>
#include <map>
#include <set>
#include <vector>

namespace {
using Json=nlohmann::json;
[[noreturn]] void Invalid() { throw AppError(400,"invalid_request","report is invalid"); }
[[noreturn]] void SourceInvalid() { throw AppError(400,"invalid_source","report citation is invalid"); }
[[noreturn]] void Conflict() { throw AppError(409,"report_conflict","report operation differs or is unavailable"); }
void Text(const Json& value,size_t maximum) {
    if(!value.is_string()) Invalid();
    const auto& text=value.get_ref<const std::string&>();
    if(text.size()>maximum || text.find_first_not_of(" \t\r\n")==std::string::npos ||
       std::any_of(text.begin(),text.end(),[](unsigned char c){return (c<32 && c!=9 && c!=10 && c!=13) || c==127;})) Invalid();
}
std::string Url(const std::string& project,const std::string& task) {
    return "/api/v1/projects/"+project+"/agent-tasks/"+task+"/handover-report";
}
Json Sources(MySqlConnection& db,const std::string& task) {
    std::set<std::string> versions,citations;
    for(const auto& row:db.Query("SELECT source_json FROM agent_tool_calls WHERE task_id=? AND model_call IS NOT NULL "
        "ORDER BY sequence FOR SHARE",{SqlValue(task)})) {
        if(row.IsNull(0)) Conflict();
        const auto source=Json::parse(row.String(0));
        for(const auto& item:source.at("versions")) versions.insert(item.dump());
        for(const auto& item:source.at("citations")) citations.insert(item.dump());
    }
    Json result={{"versions",Json::array()},{"citations",Json::array()}};
    for(const auto& value:versions) result["versions"].push_back(Json::parse(value));
    for(const auto& value:citations) result["citations"].push_back(Json::parse(value));
    return result;
}
Json Citation(MySqlConnection& db,const std::string& project,const Json& ref) {
    RequireExactObject(ref,{"file_id","version_id","chunk_id"});
    for(const char* key:{"file_id","version_id","chunk_id"})
        if(!ref[key].is_string()) SourceInvalid();
    const auto file=ref["file_id"].get<std::string>(),version=ref["version_id"].get<std::string>(),chunk=ref["chunk_id"].get<std::string>();
    const auto rows=db.Query("SELECT f.name,c.page,c.line_start,c.line_end,c.body FROM files f JOIN file_versions v "
        "ON v.file_id=f.id JOIN document_chunks c ON c.version_id=v.id WHERE f.project_id=? AND f.id=? AND v.id=? AND c.id=? FOR SHARE",
        {SqlValue(project),SqlValue(file),SqlValue(version),SqlValue(chunk)});
    if(rows.empty()) SourceInvalid();
    const auto& row=rows[0]; Json result=ref;
    result["file_name"]=row.String(0); result["page"]=row.IsNull(1) ? Json(nullptr) : Json(row.UInt64(1));
    result["line_start"]=row.UInt64(2); result["line_end"]=row.UInt64(3); result["text"]=row.String(4);
    result["source_url"]="/api/v1/projects/"+project+"/files/"+file+"/versions/"+version+"/chunks/"+chunk;
    return result;
}
Json ValidateReport(MySqlConnection& db,const std::string& project,const Json& input,const Json& checklist,const Json& sources) {
    RequireExactObject(input,{"summary","limitations","recommendations","items"});
    Text(input["summary"],8000); Text(input["limitations"],4000); Text(input["recommendations"],4000);
    if(!input["items"].is_array() || input["items"].size()!=checklist["items"].size()) Invalid();
    std::map<std::string,Json> expected;
    for(const auto& item:checklist["items"]) expected.emplace(item["key"].get<std::string>(),item);
    std::set<std::string> allowed;
    for(const auto& ref:sources["citations"]) allowed.insert(ref.dump());
    size_t count=0; Json result=input;
    for(auto& item:result["items"]) {
        RequireExactObject(item,{"key","status","notes","citations"});
        if(!item["key"].is_string() || !item["status"].is_string() || !item["citations"].is_array() || item["citations"].size()>10) Invalid();
        const auto found=expected.find(item["key"].get<std::string>());
        if(found==expected.end()) Invalid();
        const auto status=item["status"].get<std::string>();
        if((status!="found" && status!="missing" && status!="uncertain") ||
            (status=="found" && item["citations"].empty()) || (status=="missing" && !item["citations"].empty())) Invalid();
        Text(item["notes"],2000); count+=item["citations"].size(); if(count>100) Invalid();
        std::set<std::string> seen;
        for(auto& ref:item["citations"]) {
            if(!allowed.count(ref.dump()) || !seen.insert(ref.dump()).second) SourceInvalid();
            ref=Citation(db,project,ref);
        }
        item["title"]=found->second["title"]; item["required"]=found->second["required"]; expected.erase(found);
    }
    return result;
}
std::string Escape(const std::string& input) {
    std::string output;
    for(char c:input) {
        if(c=='&') output+="&amp;";
        else if(c=='<') output+="&lt;";
        else if(c=='>') output+="&gt;";
        else { if(std::string("\\`*_{}[]()#+-.!|~").find(c)!=std::string::npos) output+='\\'; output+=c; }
    }
    return output;
}
}

nlohmann::json HandoverReportReceipt(const Json& report) {
    return {{"id",report["id"]},{"version",report["version"]},{"url",report["url"]},{"markdown_url",report["markdown_url"]}};
}
nlohmann::json ReadHandoverReport(MySqlConnection& db,const std::string& project,const std::string& task) {
    const auto rows=db.Query("SELECT id,version,content_json,sources_json,checklist_json,created_at FROM handover_reports WHERE task_id=? FOR SHARE",{SqlValue(task)});
    if(rows.empty()) return nullptr;
    const auto& row=rows[0]; const auto sources=Json::parse(row.String(3)),checklist=Json::parse(row.String(4));
    RequireReportDependencies(db,project,sources,checklist);
    auto result=Json::parse(row.String(2));
    for(const auto& item:result["report"]["items"]) for(const auto& ref:item["citations"])
        if(db.Query("SELECT c.id FROM document_chunks c JOIN file_versions v ON v.id=c.version_id "
            "WHERE c.id=? AND c.version_id=? AND v.file_id=? FOR SHARE",
            {SqlValue(ref["chunk_id"].get<std::string>()),SqlValue(ref["version_id"].get<std::string>()),SqlValue(ref["file_id"].get<std::string>())}).empty())
            throw AppError(409,"task_scope_changed","report citation is unavailable");
    const auto url=Url(project,task);
    result["id"]=row.String(0); result["version"]=row.UInt64(1); result["created_at"]=row.String(5);
    result["url"]=url; result["markdown_url"]=url+"/markdown";
    result["checklist"]=checklist; result["source_versions"]=sources["versions"];
    return result;
}
nlohmann::json AgentTaskService::SaveHandoverReport(const SessionContext& session,const std::string& project,
    const std::string& task,const Json& body,const std::string& request_id) {
    RequireExactObject(body,{"operation_id","report"});
    if(!body["operation_id"].is_string()) Invalid();
    const auto operation=body["operation_id"].get<std::string>();
    if(operation.size()!=32 || operation.find_first_not_of("0123456789abcdef")!=std::string::npos) Invalid();
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db,session.user_id,project,Role::Reader);
    const auto state=LockArchiveTask(db,session,project,task);
    const auto existing=db.Query("SELECT operation_id,request_json FROM handover_reports WHERE task_id=? FOR UPDATE",{SqlValue(task)});
    if(!existing.empty()) {
        if(existing[0].String(0)!=operation || Json::parse(existing[0].String(1))!=body["report"]) Conflict();
        const auto receipt=HandoverReportReceipt(ReadHandoverReport(db,project,task)); transaction.Commit(); return receipt;
    }
    if(state!="running") throw AppError(409,"task_inactive","task is not running");
    RequireArchiveTaskScope(db,session,project,task,true);
    const auto runs=db.Query("SELECT model_calls,report_operation_id FROM agent_runs WHERE task_id=? FOR UPDATE",{SqlValue(task)});
    if(runs.empty() || runs[0].UInt64(0)==0 || runs[0].IsNull(1) || runs[0].String(1)!=operation) Conflict();
    const auto context=db.Query("SELECT handover_context FROM agent_tasks WHERE id=? FOR SHARE",{SqlValue(task)});
    if(context[0].IsNull(0)) Conflict();
    const auto checklist=Json::parse(context[0].String(0)),sources=Sources(db,task);
    RequireReportDependencies(db,project,sources,checklist);
    Json selected=Json::array();
    for(const auto& row:db.Query("SELECT file_id,version_id FROM agent_task_versions WHERE task_id=? ORDER BY file_id FOR SHARE",{SqlValue(task)}))
        selected.push_back({{"file_id",row.String(0)},{"version_id",row.String(1)}});
    const Json content={{"project_id",project},{"task_id",task},{"state","draft"},
        {"verification","citations_validated_not_semantically_reviewed"},{"selected_versions",selected},
        {"report",ValidateReport(db,project,body["report"],checklist,sources)}};
    const auto id=GenerateId();
    db.Execute("INSERT INTO handover_reports(id,task_id,operation_id,request_json,content_json,sources_json,checklist_json) VALUES (?,?,?,?,?,?,?)",
        {SqlValue(id),SqlValue(task),SqlValue(operation),SqlValue(body["report"].dump()),SqlValue(content.dump()),SqlValue(sources.dump()),SqlValue(checklist.dump())});
    ProjectRepository().InsertAudit(db,session.user_id,project,"handover.report.save","handover_report",id,request_id);
    db.Execute("UPDATE agent_tasks SET state='completed' WHERE id=?",{SqlValue(task)});
    const auto receipt=HandoverReportReceipt(ReadHandoverReport(db,project,task)); transaction.Commit(); return receipt;
}
nlohmann::json AgentTaskService::GetHandoverReport(const SessionContext& session,const std::string& project,const std::string& task) {
    auto db=pool_.Acquire(); MySqlTransaction transaction(db);
    projects_.RequireRole(db,session.user_id,project,Role::Reader); LockArchiveTask(db,session,project,task);
    auto result=ReadHandoverReport(db,project,task);
    if(result.is_null()) throw AppError(404,"resource_not_found","report was not found");
    transaction.Commit(); return result;
}
std::string RenderHandoverMarkdown(const Json& report) {
    std::string text="# 个人交接报告草稿\n\n引用定位已校验；结论尚未人工验收。\n\n项目："+report["project_id"].get<std::string>()+
        "\n\n任务："+report["task_id"].get<std::string>()+"\n\n报告："+report["id"].get<std::string>()+"，版本：1\n\n生成时间（UTC）："+report["created_at"].get<std::string>()+
        "\n\n清单修订："+std::to_string(report["checklist"]["revision"].get<uint64_t>())+"\n\n## 核查范围（固定文件/版本）\n\n";
    for(const auto& pair:report["selected_versions"]) text+="- "+pair["file_id"].get<std::string>()+" / "+pair["version_id"].get<std::string>()+"\n";
    text+="\n## 实际准入来源（包括未引用资料）\n\n";
    if(report["source_versions"].empty()) text+="无实际准入资料；不得据此推断资料齐备。\n";
    for(const auto& pair:report["source_versions"]) text+="- "+pair["file_id"].get<std::string>()+" / "+pair["version_id"].get<std::string>()+"\n";
    for(const auto& field:std::vector<std::pair<std::string,std::string>>{{"summary","摘要"},{"limitations","检索限制"},{"recommendations","交接建议"}})
        text+="\n## "+field.second+"\n\n"+Escape(report["report"][field.first])+"\n";
    for(const auto& item:report["report"]["items"]) {
        const std::string status=item["status"]=="found" ? "已找到" : item["status"]=="missing" ? "当前检索范围内待补充" : "待确认";
        text+="\n## "+Escape(item["title"])+"（"+(item["required"].get<bool>() ? "必需" : "可选")+"，"+status+"）\n\n"+Escape(item["notes"])+"\n";
        for(const auto& ref:item["citations"]) {
            text+="\n- ["+Escape(ref["file_name"])+"]("+ref["source_url"].get<std::string>()+")，版本 "+ref["version_id"].get<std::string>();
            if(!ref["page"].is_null()) text+="，物理页 "+std::to_string(ref["page"].get<uint64_t>());
            text+="，行 "+std::to_string(ref["line_start"].get<uint64_t>())+"–"+std::to_string(ref["line_end"].get<uint64_t>())+"\n\n"+Escape(ref["text"])+"\n";
        }
    }
    return text;
}
