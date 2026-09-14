#include "router.h"

#include "../core/app_error.h"

#include <cctype>
#include <stdexcept>
#include <unordered_set>
#include <utility>

namespace {

int HexValue(unsigned char ch) {
    if (ch >= '0' && ch <= '9') {
        return ch - '0';
    }
    if (ch >= 'a' && ch <= 'f') {
        return ch - 'a' + 10;
    }
    if (ch >= 'A' && ch <= 'F') {
        return ch - 'A' + 10;
    }
    return -1;
}

std::vector<std::string> SplitPattern(const std::string& pattern) {
    if (pattern.empty() || pattern[0] != '/' ||
        pattern.find('?') != std::string::npos ||
        pattern.find('#') != std::string::npos) {
        throw std::invalid_argument("route pattern must be an absolute path");
    }
    std::vector<std::string> segments;
    size_t start = 1;
    while (start <= pattern.size()) {
        const size_t slash = pattern.find('/', start);
        const size_t end = slash == std::string::npos ? pattern.size() : slash;
        if (end != start) {
            segments.push_back(pattern.substr(start, end - start));
        } else if (start != pattern.size()) {
            throw std::invalid_argument("route pattern cannot contain empty segments");
        }
        if (slash == std::string::npos) {
            break;
        }
        start = slash + 1;
    }
    return segments;
}

std::string DecodeSegment(const std::string& encoded) {
    std::string decoded;
    decoded.reserve(encoded.size());
    for (size_t i = 0; i < encoded.size(); ++i) {
        unsigned char ch = static_cast<unsigned char>(encoded[i]);
        if (ch == '%') {
            if (i + 2 >= encoded.size()) {
                throw AppError(400, "invalid_path", "path contains an incomplete percent escape");
            }
            const int high = HexValue(static_cast<unsigned char>(encoded[i + 1]));
            const int low = HexValue(static_cast<unsigned char>(encoded[i + 2]));
            if (high < 0 || low < 0) {
                throw AppError(400, "invalid_path", "path contains an invalid percent escape");
            }
            ch = static_cast<unsigned char>((high << 4) | low);
            i += 2;
            if (ch == '/' || ch == 0) {
                throw AppError(400, "invalid_path", "encoded slash and NUL are not allowed in paths");
            }
        }
        if (ch < 0x20 || ch == 0x7f) {
            throw AppError(400, "invalid_path", "control characters are not allowed in paths");
        }
        decoded.push_back(static_cast<char>(ch));
    }
    if (decoded == "." || decoded == "..") {
        throw AppError(400, "invalid_path", "dot path segments are not allowed");
    }
    return decoded;
}

std::vector<std::string> DecodePath(const std::string& path) {
    if (path.empty() || path[0] != '/' || path.find('?') != std::string::npos ||
        path.find('#') != std::string::npos) {
        throw AppError(400, "invalid_path", "request path must be an absolute path without query or fragment");
    }

    std::vector<std::string> segments;
    size_t start = 1;
    while (start <= path.size()) {
        const size_t slash = path.find('/', start);
        const size_t end = slash == std::string::npos ? path.size() : slash;
        if (end != start) {
            segments.push_back(DecodeSegment(path.substr(start, end - start)));
        } else if (start != path.size()) {
            throw AppError(400, "invalid_path", "empty path segments are not allowed");
        }
        if (slash == std::string::npos) {
            break;
        }
        start = slash + 1;
    }
    return segments;
}

}  // namespace

void Router::Add(std::string method, std::string pattern, Factory factory) {
    if (method.empty() || !factory) {
        throw std::invalid_argument("route method and factory are required");
    }

    std::vector<std::string> raw_segments = SplitPattern(pattern);
    std::vector<Segment> segments;
    std::unordered_set<std::string> parameter_names;
    for (const std::string& raw : raw_segments) {
        const bool starts_brace = !raw.empty() && raw.front() == '{';
        const bool ends_brace = !raw.empty() && raw.back() == '}';
        if (starts_brace || ends_brace) {
            if (!starts_brace || !ends_brace || raw.size() < 3 ||
                raw.find('{', 1) != std::string::npos ||
                raw.find('}') != raw.size() - 1) {
                throw std::invalid_argument("route parameter syntax is invalid");
            }
            const std::string name = raw.substr(1, raw.size() - 2);
            if (!parameter_names.insert(name).second) {
                throw std::invalid_argument("route parameter names must be unique");
            }
            segments.push_back(Segment{name, true});
        } else {
            if (raw.find('{') != std::string::npos || raw.find('}') != std::string::npos ||
                raw.find('%') != std::string::npos) {
                throw std::invalid_argument("route literal segment is invalid");
            }
            segments.push_back(Segment{raw, false});
        }
    }

    for (const Route& route : routes_) {
        if (route.method == method && route.pattern == pattern) {
            throw std::invalid_argument("duplicate route");
        }
    }
    routes_.push_back(Route{std::move(method), std::move(pattern),
                            std::move(segments), std::move(factory)});
}

std::unique_ptr<RequestBodyHandler> Router::Prepare(const RequestHead& head) const {
    const std::vector<std::string> path_segments = DecodePath(head.path);
    bool path_matched = false;

    for (const Route& route : routes_) {
        if (route.segments.size() != path_segments.size()) {
            continue;
        }
        RouteParams params;
        bool matches = true;
        for (size_t i = 0; i < route.segments.size(); ++i) {
            if (route.segments[i].parameter) {
                params.emplace(route.segments[i].value, path_segments[i]);
            } else if (route.segments[i].value != path_segments[i]) {
                matches = false;
                break;
            }
        }
        if (!matches) {
            continue;
        }
        path_matched = true;
        if (route.method != head.method) {
            continue;
        }
        std::unique_ptr<RequestBodyHandler> handler = route.factory(head, params);
        if (!handler) {
            throw AppError(500, "route_handler_unavailable", "route did not create a request handler");
        }
        return handler;
    }

    if (path_matched) {
        throw AppError(405, "method_not_allowed", "method is not allowed for this route");
    }
    throw AppError(404, "route_not_found", "route was not found");
}
