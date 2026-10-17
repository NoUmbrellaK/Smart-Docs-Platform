#include "worker_routes.h"
#include "processing_service.h"
#include "core/app_error.h"
#include "core/id.h"
#include "file/file_service.h"
#include "http/jsonbody.h"
#include "http/router.h"
#include <openssl/crypto.h>

namespace {
void Authenticate(const RequestHead& head, const std::string& token) {
    const auto found = head.headers.find("authorization");
    const std::string expected = "Bearer " + token;
    if (token.empty() || head.headers.count("origin") || head.headers.count("cookie") ||
        found == head.headers.end() || found->second.size() != expected.size() ||
        CRYPTO_memcmp(found->second.data(), expected.data(), expected.size()) != 0)
        throw AppError(401, "worker_unauthorized", "local worker authentication required");
}
std::string Attempt(const RequestHead& head) {
    const auto found = head.headers.find("x-smartdocs-attempt");
    if (found == head.headers.end() || found->second.size() != 32 ||
        found->second.find_first_not_of("0123456789abcdef") != std::string::npos)
        throw AppError(400, "invalid_request", "processing attempt is invalid");
    return found->second;
}
void Empty(const RequestHead& head) {
    if (head.content_length) throw AppError(400, "body_not_allowed", "request body is not allowed");
}
HttpResponse Data(nlohmann::json data, bool keep_alive) {
    return HttpResponse::Json(200, {{"request_id", GenerateId()}, {"data", std::move(data)}}, keep_alive);
}
}

void RegisterWorkerRoutes(Router& router, std::shared_ptr<ProcessingService> jobs,
                          std::shared_ptr<FileStore> store, const std::string& token) {
    if (token.empty()) return;
    const std::string prefix = "/internal/v1/processing";
    router.Add("POST",prefix+"/vector/claim",[jobs,token](const RequestHead& head,const RouteParams&) {
        Authenticate(head,token); Empty(head); ProcessingClaim claim; nlohmann::json data=nullptr;
        if (jobs->ClaimNext(&claim,"vectorize")) data={{"id",claim.job.id},{"file_id",claim.job.file_id},
            {"version_id",claim.job.version_id},{"attempt_token",claim.attempt_token}};
        return ReadyResponse(Data(std::move(data),head.keep_alive));
    });
    router.Add("POST",prefix+"/vector/recover",[jobs,token](const RequestHead& head,const RouteParams&) {
        Authenticate(head,token); Empty(head);
        return ReadyResponse(Data({{"interrupted_count",jobs->RecoverInterrupted("vectorize")}},head.keep_alive));
    });
    router.Add("GET",prefix+"/vector/{job_id}/chunks",[jobs,token](const RequestHead& head,const RouteParams& params) {
        Authenticate(head,token); Empty(head);
        if (!head.query.empty() || head.headers.count("range")) throw AppError(400,"invalid_request","vector batches do not accept query or range");
        return ReadyResponse(Data(jobs->ReadVectorBatch(params.at("job_id"),Attempt(head)),head.keep_alive));
    });
    router.Add("POST",prefix+"/vector/{job_id}/result",[jobs,token](const RequestHead& head,const RouteParams& params) {
        Authenticate(head,token); const auto attempt=Attempt(head), id=params.at("job_id");
        return PrepareJsonBody(head,2*1024*1024+4096,[jobs,id,attempt,keep_alive=head.keep_alive](const nlohmann::json& body) {
            RequireExactObject(body,{"batch_start","result"}); const auto& start=body.at("batch_start");
            if (!start.is_number_integer() || (!start.is_number_unsigned() && start.get<int64_t>()<0) || start.get<uint64_t>()>=2000)
                throw AppError(400,"invalid_request","vector batch offset is invalid");
            return Data({{"state",jobs->PublishVectorBatch(id,attempt,start.get<uint64_t>(),body.at("result"))}},keep_alive);
        });
    });
    router.Add("POST", prefix + "/claim", [jobs, token](const RequestHead& head, const RouteParams&) {
        Authenticate(head, token); Empty(head); ProcessingClaim claim;
        nlohmann::json data = nullptr;
        if (jobs->ClaimNext(&claim)) data = {{"id", claim.job.id}, {"file_id", claim.job.file_id},
            {"version_id", claim.job.version_id}, {"attempt_token", claim.attempt_token},
            {"source_sha256", claim.source_sha256}, {"source_size", claim.source_size}, {"kind", claim.kind}};
        return ReadyResponse(Data(std::move(data), head.keep_alive));
    });
    router.Add("POST", prefix + "/recover", [jobs, token](const RequestHead& head, const RouteParams&) {
        Authenticate(head, token); Empty(head);
        return ReadyResponse(Data({{"interrupted_count", jobs->RecoverInterrupted()}}, head.keep_alive));
    });
    router.Add("GET", prefix + "/{job_id}/content", [jobs, store, token](const RequestHead& head, const RouteParams& params) {
        Authenticate(head, token); Empty(head);
        if (head.headers.count("range")) throw AppError(400, "invalid_request", "worker source does not accept ranges");
        auto input = jobs->OpenInput(params.at("job_id"), Attempt(head), *store);
        return ReadyResponse(HttpResponse::File(200, {input.ReleaseFd(), 0, input.version.size},
            {{"Content-Type", "application/octet-stream"}, {"Cache-Control", "no-store"}}, head.keep_alive));
    });
    for (const std::string action : {"result", "fail"}) {
        router.Add("POST", prefix + "/{job_id}/" + action,
            [jobs, token, action](const RequestHead& head, const RouteParams& params) {
                Authenticate(head, token); const auto attempt = Attempt(head); const auto id = params.at("job_id");
                return PrepareJsonBody(head, action == "result" ? 16*1024*1024 : 1024,
                    [jobs, id, attempt, action, keep_alive=head.keep_alive](const nlohmann::json& body) {
                        if (action == "result") jobs->Publish(id, attempt, body);
                        else {
                            RequireExactObject(body, {"error_code"});
                            if (!body.at("error_code").is_string()) throw AppError(400, "invalid_request", "error code must be a string");
                            jobs->Fail(id, attempt, body.at("error_code").get<std::string>());
                        }
                        return Data({{"state", action == "result" ? "completed" : "failed"}}, keep_alive);
                    });
            });
    }
}
