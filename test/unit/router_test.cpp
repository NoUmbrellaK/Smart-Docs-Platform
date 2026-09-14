#include "http/router.h"
#include "http/httpresponse.h"
#include "core/app_error.h"
#include "../test_support.h"

#include <memory>
#include <string>

namespace {

class EmptyHandler : public RequestBodyHandler {
public:
    void OnData(const char*, size_t) override {}

    HttpResponse Finish() override {
        return HttpResponse();
    }
};

RequestHead Head(const std::string& method, const std::string& path,
                 const std::string& query = std::string()) {
    RequestHead head;
    head.method = method;
    head.path = path;
    head.query = query;
    return head;
}

}  // namespace

TEST_CASE(router_matches_literals_and_decoded_parameters) {
    Router router;
    std::string captured_project;
    std::string captured_file;
    router.Add("GET", "/api/v1/projects/{project_id}/files/{file_id}",
               [&](const RequestHead&, const RouteParams& params) {
        captured_project = params.at("project_id");
        captured_file = params.at("file_id");
        return std::unique_ptr<RequestBodyHandler>(new EmptyHandler());
    });

    std::unique_ptr<RequestBodyHandler> handler =
        router.Prepare(Head("GET", "/api/v1/projects/team%2Dblue/files/report%201"));
    CHECK(handler.get() != nullptr);
    CHECK(captured_project == "team-blue");
    CHECK(captured_file == "report 1");
}

TEST_CASE(router_does_not_treat_query_as_path) {
    Router router;
    std::string captured_query;
    router.Add("GET", "/api/v1/files", [&](const RequestHead& head,
                                             const RouteParams&) {
        captured_query = head.query;
        return std::unique_ptr<RequestBodyHandler>(new EmptyHandler());
    });

    CHECK(router.Prepare(Head("GET", "/api/v1/files", "page=2")).get() != nullptr);
    CHECK(captured_query == "page=2");
}

TEST_CASE(router_rejects_invalid_or_dangerous_path_segments) {
    Router router;
    router.Add("GET", "/api/v1/files/{file_id}",
               [](const RequestHead&, const RouteParams&) {
        return std::unique_ptr<RequestBodyHandler>(new EmptyHandler());
    });

    CHECK_THROWS_CODE(router.Prepare(Head("GET", "/api/v1/files/bad%")), "invalid_path");
    CHECK_THROWS_CODE(router.Prepare(Head("GET", "/api/v1/files/a%2Fb")), "invalid_path");
    CHECK_THROWS_CODE(router.Prepare(Head("GET", "/api/v1/files/%00")), "invalid_path");
    CHECK_THROWS_CODE(router.Prepare(Head("GET", "/api/v1/files/.")), "invalid_path");
    CHECK_THROWS_CODE(router.Prepare(Head("GET", "/api/v1/files/%2e%2e")), "invalid_path");
}

TEST_CASE(router_reports_method_and_route_misses) {
    Router router;
    router.Add("GET", "/api/v1/files/{file_id}",
               [](const RequestHead&, const RouteParams&) {
        return std::unique_ptr<RequestBodyHandler>(new EmptyHandler());
    });

    CHECK_THROWS_CODE(router.Prepare(Head("POST", "/api/v1/files/f1")), "method_not_allowed");
    CHECK_THROWS_CODE(router.Prepare(Head("GET", "/api/v1/projects")), "route_not_found");
}
