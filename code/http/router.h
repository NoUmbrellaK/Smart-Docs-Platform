#pragma once

#include "httprequest.h"

#include <functional>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

class HttpResponse;

class RequestBodyHandler {
public:
    virtual ~RequestBodyHandler() = default;
    virtual void OnData(const char* data, size_t size) = 0;
    virtual HttpResponse Finish() = 0;
};

using RouteParams = std::unordered_map<std::string, std::string>;

class Router {
public:
    using Factory = std::function<std::unique_ptr<RequestBodyHandler>(
        const RequestHead&, const RouteParams&)>;

    void Add(std::string method, std::string pattern, Factory factory);
    std::unique_ptr<RequestBodyHandler> Prepare(const RequestHead& head) const;

private:
    struct Segment {
        std::string value;
        bool parameter;
    };

    struct Route {
        std::string method;
        std::string pattern;
        std::vector<Segment> segments;
        Factory factory;
    };

    std::vector<Route> routes_;
};
