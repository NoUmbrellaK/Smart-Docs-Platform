#include "upload_routes.h"

#include "auth/auth_routes.h"
#include "auth/auth_service.h"
#include "chunk_body_handler.h"
#include "core/app_error.h"
#include "core/fault_injector.h"
#include "core/id.h"
#include "http/jsonbody.h"
#include "upload_service.h"

#include <algorithm>
#include <cctype>
#include <limits>
#include <nlohmann/json.hpp>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>

namespace {

std::string Parameter(const RouteParams& params, const char* name) {
    const auto found = params.find(name);
    if (found == params.end()) {
        throw AppError(500, "route_parameter_missing",
                       "route parameter is unavailable");
    }
    return found->second;
}

std::string EntityId(const std::string& value) {
    if (value.size() != 32 ||
        value.find_first_not_of("0123456789abcdef") != std::string::npos) {
        throw AppError(400, "invalid_request", "entity ID is invalid");
    }
    return value;
}

SessionContext Authenticate(AuthService& auth, const RequestHead& head) {
    return auth.Authenticate(RequireSessionCookie(head));
}

nlohmann::json Optional(const std::string& value) {
    return value.empty() ? nlohmann::json(nullptr) : nlohmann::json(value);
}

nlohmann::json TaskJson(const UploadTask& task) {
    return {{"task_id", task.id},
            {"project_id", task.project_id},
            {"owner_id", task.owner_id},
            {"state", task.state},
            {"chunk_size", task.chunk_size},
            {"part_count", task.part_count},
            {"received_bytes", task.received_bytes},
            {"file_id", Optional(task.file_id)},
            {"version_id", Optional(task.version_id)},
            {"processing_job_id", Optional(task.processing_job_id)},
            {"failure", Optional(task.failure)}};
}

nlohmann::json DetailJson(const UploadTaskDetail& detail) {
    nlohmann::json parts = nlohmann::json::array();
    for (const PartInfo& part : detail.confirmed_parts) {
        parts.push_back({{"part_number", part.part_number},
                         {"size", part.size},
                         {"sha256", part.sha256}});
    }
    nlohmann::json result = TaskJson(detail.task);
    result["confirmed_parts"] = std::move(parts);
    return result;
}

HttpResponse DataResponse(int status, nlohmann::json data,
                          const std::string& request_id, bool keep_alive) {
    return HttpResponse::Json(status,
                              {{"data", std::move(data)},
                               {"request_id", request_id}},
                              keep_alive);
}

uint64_t UnsignedField(const nlohmann::json& body, const char* field) {
    if (!body[field].is_number_unsigned()) {
        throw AppError(400, "invalid_request",
                       std::string(field) + " must be an unsigned integer");
    }
    return body[field].get<uint64_t>();
}

std::string StringField(const nlohmann::json& body, const char* field) {
    if (!body[field].is_string()) {
        throw AppError(400, "invalid_request",
                       std::string(field) + " must be a string");
    }
    return body[field].get<std::string>();
}

CreateUploadCommand CreateCommand(const nlohmann::json& body) {
    if (!body.is_object() || !body.contains("mode") ||
        !body["mode"].is_string()) {
        throw AppError(400, "invalid_request", "upload mode is required");
    }
    CreateUploadCommand command{};
    const std::string mode = body["mode"].get<std::string>();
    if (mode == "create_file") {
        RequireExactObject(body, {"mode", "directory_id", "name", "size",
                                  "sha256", "media_type"});
        command.mode = UploadMode::CreateFile;
        command.directory_id = EntityId(StringField(body, "directory_id"));
        command.name = StringField(body, "name");
    } else if (mode == "create_version") {
        RequireExactObject(body, {"mode", "file_id",
                                  "observed_current_version_id", "size",
                                  "sha256", "media_type"});
        command.mode = UploadMode::CreateVersion;
        command.file_id = EntityId(StringField(body, "file_id"));
        command.observed_current_version_id =
            EntityId(StringField(body, "observed_current_version_id"));
    } else {
        throw AppError(400, "invalid_request", "upload mode is invalid");
    }
    command.size = UnsignedField(body, "size");
    command.sha256 = StringField(body, "sha256");
    command.media_type = StringField(body, "media_type");
    return command;
}

uint64_t PositiveDecimal(const std::string& value) {
    if (value.empty()) {
        throw AppError(400, "invalid_request", "pagination is invalid");
    }
    uint64_t result = 0;
    for (unsigned char ch : value) {
        if (!std::isdigit(ch)) {
            throw AppError(400, "invalid_request", "pagination is invalid");
        }
        const uint64_t digit = static_cast<uint64_t>(ch - '0');
        if (result > (std::numeric_limits<uint64_t>::max() - digit) / 10) {
            throw AppError(400, "invalid_request", "pagination is invalid");
        }
        result = result * 10 + digit;
    }
    if (result == 0) {
        throw AppError(400, "invalid_request", "pagination is invalid");
    }
    return result;
}

std::unordered_map<std::string, std::string> QueryValues(
    const std::string& query) {
    std::unordered_map<std::string, std::string> values;
    if (query.empty()) return values;
    size_t start = 0;
    while (start <= query.size()) {
        const size_t separator = query.find('&', start);
        const size_t end = separator == std::string::npos
                               ? query.size()
                               : separator;
        const std::string item = query.substr(start, end - start);
        const size_t equals = item.find('=');
        if (equals == std::string::npos || equals == 0 ||
            item.find('=', equals + 1) != std::string::npos ||
            !values.emplace(item.substr(0, equals), item.substr(equals + 1))
                 .second) {
            throw AppError(400, "invalid_request", "query is invalid");
        }
        if (separator == std::string::npos) break;
        start = separator + 1;
    }
    return values;
}

UploadListQuery ListQuery(const std::string& raw) {
    const std::unordered_map<std::string, std::string> values =
        QueryValues(raw);
    static const std::unordered_set<std::string> allowed = {
        "state", "page", "page_size"};
    UploadListQuery query;
    for (const auto& value : values) {
        if (allowed.count(value.first) == 0) {
            throw AppError(400, "invalid_request",
                           "query parameter is not supported");
        }
        if (value.first == "state") query.state = value.second;
        if (value.first == "page") query.page = PositiveDecimal(value.second);
        if (value.first == "page_size") {
            query.page_size = PositiveDecimal(value.second);
        }
    }
    return query;
}

uint32_t PartNumber(const std::string& value) {
    if (value.empty()) {
        throw AppError(400, "invalid_part_number",
                       "upload part number is invalid");
    }
    uint64_t result = 0;
    for (unsigned char ch : value) {
        if (!std::isdigit(ch) || result >
                (std::numeric_limits<uint32_t>::max() - (ch - '0')) / 10) {
            throw AppError(400, "invalid_part_number",
                           "upload part number is invalid");
        }
        result = result * 10 + static_cast<uint64_t>(ch - '0');
    }
    return static_cast<uint32_t>(result);
}

std::string RequiredHeader(const RequestHead& head, const char* name,
                           const char* message) {
    const auto found = head.headers.find(name);
    if (found == head.headers.end()) {
        throw AppError(400, "invalid_request", message);
    }
    return found->second;
}

void RequireOctetStream(const RequestHead& head) {
    std::string value = RequiredHeader(
        head, "content-type", "Content-Type must be application/octet-stream");
    std::transform(value.begin(), value.end(), value.begin(),
                   [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    if (value != "application/octet-stream") {
        throw AppError(400, "invalid_request",
                       "Content-Type must be application/octet-stream");
    }
}

}  // namespace

void RegisterUploadRoutes(Router& router,
                          const std::shared_ptr<AuthService>& auth,
                          const std::shared_ptr<UploadService>& uploads,
                          uint64_t maximum_json_bytes, bool secure_cookie) {
    router.Add("POST", "/api/v1/projects/{project_id}/uploads",
        [auth, uploads, maximum_json_bytes, secure_cookie](
            const RequestHead& head, const RouteParams& params) {
        RequireSameOrigin(head, secure_cookie);
        const SessionContext session = Authenticate(*auth, head);
        const std::string project_id = EntityId(Parameter(params, "project_id"));
        return PrepareJsonBody(
            head, maximum_json_bytes,
            [uploads, session, project_id, keep_alive = head.keep_alive](
                const nlohmann::json& body) {
            const std::string request_id = GenerateId();
            const UploadTask task = uploads->Create(
                session, project_id, CreateCommand(body), request_id);
            return DataResponse(
                201, DetailJson(UploadTaskDetail{task, {}}), request_id,
                keep_alive);
        });
    });

    router.Add("GET", "/api/v1/projects/{project_id}/uploads",
        [auth, uploads](const RequestHead& head, const RouteParams& params) {
        const Page<UploadTask> page = uploads->ListOwn(
            Authenticate(*auth, head), EntityId(Parameter(params, "project_id")),
            ListQuery(head.query));
        nlohmann::json items = nlohmann::json::array();
        for (const UploadTask& task : page.items) items.push_back(TaskJson(task));
        return ReadyResponse(DataResponse(
            200, {{"items", std::move(items)}, {"page", page.page},
                  {"page_size", page.page_size}, {"total", page.total}},
            GenerateId(), head.keep_alive));
    });

    router.Add("GET", "/api/v1/projects/{project_id}/uploads/{task_id}",
        [auth, uploads](const RequestHead& head, const RouteParams& params) {
        const UploadTaskDetail detail = uploads->GetOwn(
            Authenticate(*auth, head), EntityId(Parameter(params, "project_id")),
            EntityId(Parameter(params, "task_id")));
        return ReadyResponse(DataResponse(200, DetailJson(detail), GenerateId(),
                                          head.keep_alive));
    });

    router.Add("POST",
        "/api/v1/projects/{project_id}/uploads/{task_id}/cancel",
        [auth, uploads, secure_cookie](const RequestHead& head,
                                      const RouteParams& params) {
        RequireSameOrigin(head, secure_cookie);
        const SessionContext session = Authenticate(*auth, head);
        const std::string project_id = EntityId(Parameter(params, "project_id"));
        const std::string task_id = EntityId(Parameter(params, "task_id"));
        const std::string request_id = GenerateId();
        uploads->Cancel(session, project_id, task_id, request_id);
        return ReadyResponse(HttpResponse::Empty(
            204, head.keep_alive, {{"X-Request-ID", request_id}}));
    });

    router.Add("POST",
        "/api/v1/projects/{project_id}/uploads/{task_id}/complete",
        [auth, uploads, secure_cookie](const RequestHead& head,
                                      const RouteParams& params) {
        RequireSameOrigin(head, secure_cookie);
        if (head.content_length != 0) {
            throw AppError(400, "body_not_allowed",
                           "this route does not accept a request body");
        }
        const SessionContext session = Authenticate(*auth, head);
        const std::string project_id = EntityId(Parameter(params, "project_id"));
        const std::string task_id = EntityId(Parameter(params, "task_id"));
        const std::string request_id = GenerateId();
        const CompleteUploadResult result =
            uploads->Complete(session, project_id, task_id, request_id);
        FaultInjector::Hit(FaultPoint::BeforeHttpResponse);
        return ReadyResponse(DataResponse(
            200,
            {{"file_id", result.file_id},
             {"version_id", result.version_id},
             {"processing_job_id", result.processing_job_id},
             {"reused", result.reused}},
            request_id, head.keep_alive));
    });

    router.Add("PUT",
        "/api/v1/projects/{project_id}/uploads/{task_id}/parts/{part_number}",
        [auth, uploads, secure_cookie](const RequestHead& head,
                                      const RouteParams& params)
            -> std::unique_ptr<RequestBodyHandler> {
        RequireSameOrigin(head, secure_cookie);
        const SessionContext session = Authenticate(*auth, head);
        RequireOctetStream(head);
        const std::string digest = RequiredHeader(
            head, "x-chunk-sha256", "X-Chunk-SHA256 is required");
        const std::string project_id = EntityId(Parameter(params, "project_id"));
        const std::string task_id = EntityId(Parameter(params, "task_id"));
        const uint32_t part_number =
            PartNumber(Parameter(params, "part_number"));
        const std::string request_id = GenerateId();
        std::unique_ptr<PartWriter> writer = uploads->BeginPart(
            session, project_id, task_id, part_number, head.content_length,
            digest);
        return std::unique_ptr<RequestBodyHandler>(new ChunkBodyHandler(
            *uploads, session, project_id, task_id, part_number,
            head.content_length, digest, std::move(writer), request_id,
            head.keep_alive));
    });
}
