#include "file_routes.h"

#include "auth/auth_routes.h"
#include "auth/auth_service.h"
#include "core/app_error.h"
#include "core/id.h"
#include "file_service.h"
#include "http/jsonbody.h"

#include <cctype>
#include <limits>
#include <nlohmann/json.hpp>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>

namespace {

nlohmann::json FileJson(const FileSummary& file) {
    return {{"id", file.id},
            {"project_id", file.project_id},
            {"directory_id", file.directory_id},
            {"name", file.name},
            {"current_version_id", file.current_version_id},
            {"deleted", file.deleted},
            {"remote_ai_policy", file.remote_ai_policy}};
}

nlohmann::json VersionJson(const FileVersionSummary& version) {
    return {{"id", version.id},
            {"file_id", version.file_id},
            {"version_number", version.version_number},
            {"size", version.size},
            {"sha256", version.sha256},
            {"media_type", version.media_type},
            {"processing_state", version.processing_state},
            {"remote_ai_approved", version.remote_ai_approved}};
}

HttpResponse DataResponse(nlohmann::json data, const std::string& request_id,
                          bool keep_alive) {
    return HttpResponse::Json(200,
                              {{"data", std::move(data)},
                               {"request_id", request_id}},
                              keep_alive);
}

SessionContext Authenticate(AuthService& auth, const RequestHead& head) {
    return auth.Authenticate(RequireSessionCookie(head));
}

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

int HexValue(unsigned char ch) {
    if (ch >= '0' && ch <= '9') return ch - '0';
    if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
    if (ch >= 'A' && ch <= 'F') return ch - 'A' + 10;
    return -1;
}

std::string DecodeQueryComponent(const std::string& value) {
    std::string decoded;
    decoded.reserve(value.size());
    for (size_t index = 0; index < value.size(); ++index) {
        unsigned char ch = static_cast<unsigned char>(value[index]);
        if (ch == '+') {
            ch = ' ';
        } else if (ch == '%') {
            if (index + 2 >= value.size()) {
                throw AppError(400, "invalid_request",
                               "query escape is incomplete");
            }
            const int high =
                HexValue(static_cast<unsigned char>(value[index + 1]));
            const int low =
                HexValue(static_cast<unsigned char>(value[index + 2]));
            if (high < 0 || low < 0) {
                throw AppError(400, "invalid_request",
                               "query escape is invalid");
            }
            ch = static_cast<unsigned char>((high << 4) | low);
            index += 2;
        }
        if (ch == 0 || ch < 0x20 || ch == 0x7f) {
            throw AppError(400, "invalid_request",
                           "query contains a control character");
        }
        decoded.push_back(static_cast<char>(ch));
    }
    return decoded;
}

std::unordered_map<std::string, std::string> ParseQuery(
    const std::string& query) {
    std::unordered_map<std::string, std::string> result;
    if (query.empty()) return result;
    size_t start = 0;
    while (start <= query.size()) {
        const size_t ampersand = query.find('&', start);
        const size_t end = ampersand == std::string::npos
                               ? query.size()
                               : ampersand;
        const std::string item = query.substr(start, end - start);
        const size_t equals = item.find('=');
        if (item.empty() || equals == std::string::npos || equals == 0 ||
            item.find('=', equals + 1) != std::string::npos) {
            throw AppError(400, "invalid_request", "query is invalid");
        }
        const std::string name = DecodeQueryComponent(item.substr(0, equals));
        const std::string value =
            DecodeQueryComponent(item.substr(equals + 1));
        if (!result.emplace(name, value).second) {
            throw AppError(400, "invalid_request",
                           "query parameter is duplicated");
        }
        if (ampersand == std::string::npos) break;
        start = ampersand + 1;
    }
    return result;
}

uint64_t PositiveDecimal(const std::string& value) {
    if (value.empty()) {
        throw AppError(400, "invalid_request",
                       "pagination value is invalid");
    }
    uint64_t result = 0;
    for (unsigned char ch : value) {
        if (!std::isdigit(ch)) {
            throw AppError(400, "invalid_request",
                           "pagination value is invalid");
        }
        const uint64_t digit = static_cast<uint64_t>(ch - '0');
        if (result > (std::numeric_limits<uint64_t>::max() - digit) / 10) {
            throw AppError(400, "invalid_request",
                           "pagination value is invalid");
        }
        result = result * 10 + digit;
    }
    if (result == 0) {
        throw AppError(400, "invalid_request",
                       "pagination value is invalid");
    }
    return result;
}

FileListQuery FileQuery(const std::string& raw_query) {
    const std::unordered_map<std::string, std::string> values =
        ParseQuery(raw_query);
    static const std::unordered_set<std::string> allowed = {
        "directory_id", "name", "deleted", "page", "page_size"};
    for (const auto& value : values) {
        if (allowed.count(value.first) == 0) {
            throw AppError(400, "invalid_request",
                           "query parameter is not supported");
        }
    }
    FileListQuery query;
    const auto directory = values.find("directory_id");
    if (directory != values.end()) query.directory_id = EntityId(directory->second);
    const auto name = values.find("name");
    if (name != values.end()) query.name = name->second;
    const auto deleted = values.find("deleted");
    if (deleted != values.end()) {
        if (deleted->second == "true") {
            query.deleted = true;
        } else if (deleted->second != "false") {
            throw AppError(400, "invalid_request",
                           "deleted must be true or false");
        }
    }
    const auto page = values.find("page");
    if (page != values.end()) query.page = PositiveDecimal(page->second);
    const auto page_size = values.find("page_size");
    if (page_size != values.end()) {
        query.page_size = PositiveDecimal(page_size->second);
    }
    if (query.page_size > 100 || query.name.size() > 255) {
        throw AppError(400, "invalid_request", "file list query is invalid");
    }
    return query;
}

void RequireVersionPresent(const std::vector<FileVersionSummary>& versions,
                           const std::string& version_id) {
    for (const FileVersionSummary& version : versions) {
        if (version.id == version_id) return;
    }
    throw AppError(404, "resource_not_found", "resource was not found");
}

}  // namespace

void RegisterFileRoutes(Router& router,
                        const std::shared_ptr<AuthService>& auth,
                        const std::shared_ptr<FileService>& files) {
    router.Add("GET", "/api/v1/projects/{project_id}/files",
        [auth, files](const RequestHead& head, const RouteParams& params) {
        const SessionContext session = Authenticate(*auth, head);
        const std::string project_id = EntityId(Parameter(params, "project_id"));
        const Page<FileSummary> page =
            files->List(session, project_id, FileQuery(head.query));
        nlohmann::json items = nlohmann::json::array();
        for (const FileSummary& file : page.items) items.push_back(FileJson(file));
        const std::string request_id = GenerateId();
        return ReadyResponse(DataResponse(
            {{"items", std::move(items)},
             {"page", page.page},
             {"page_size", page.page_size},
             {"total", page.total}},
            request_id, head.keep_alive));
    });

    router.Add("GET",
        "/api/v1/projects/{project_id}/files/{file_id}/versions",
        [auth, files](const RequestHead& head, const RouteParams& params) {
        const SessionContext session = Authenticate(*auth, head);
        const std::string project_id = EntityId(Parameter(params, "project_id"));
        const std::string file_id = EntityId(Parameter(params, "file_id"));
        nlohmann::json items = nlohmann::json::array();
        for (const FileVersionSummary& version :
             files->ListVersions(session, project_id, file_id)) {
            items.push_back(VersionJson(version));
        }
        return ReadyResponse(DataResponse(
            {{"items", std::move(items)}}, GenerateId(), head.keep_alive));
    });

    router.Add("GET",
        "/api/v1/projects/{project_id}/files/{file_id}/content",
        [auth, files](const RequestHead& head, const RouteParams& params)
            -> std::unique_ptr<RequestBodyHandler> {
        const SessionContext session = Authenticate(*auth, head);
        files->ListVersions(session,
                            EntityId(Parameter(params, "project_id")),
                            EntityId(Parameter(params, "file_id")));
        throw AppError(501, "not_implemented",
                       "protected content delivery is not implemented yet");
    });

    router.Add("GET",
        "/api/v1/projects/{project_id}/files/{file_id}/versions/{version_id}/content",
        [auth, files](const RequestHead& head, const RouteParams& params)
            -> std::unique_ptr<RequestBodyHandler> {
        const SessionContext session = Authenticate(*auth, head);
        const std::string project_id = EntityId(Parameter(params, "project_id"));
        const std::string file_id = EntityId(Parameter(params, "file_id"));
        const std::string version_id = EntityId(Parameter(params, "version_id"));
        const std::vector<FileVersionSummary> versions =
            files->ListVersions(session, project_id, file_id);
        RequireVersionPresent(versions, version_id);
        throw AppError(501, "not_implemented",
                       "protected content delivery is not implemented yet");
    });
}
