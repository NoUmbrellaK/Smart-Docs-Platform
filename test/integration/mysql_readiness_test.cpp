#include "app/application.h"
#include "core/app_error.h"
#include "../mysql_test_support.h"
#include "../test_support.h"

#include <cstdlib>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

namespace {

class TemporaryStorage {
public:
    TemporaryStorage() {
        char pattern[] = "/tmp/smart-docs-storage-test.XXXXXX";
        char* created = mkdtemp(pattern);
        CHECK(created != nullptr);
        path_ = created;
    }

    ~TemporaryStorage() {
        unlink((path_ + "/link").c_str());
        chmod((path_ + "/real/staging").c_str(), 0700);
        chmod((path_ + "/staging").c_str(), 0700);
        rmdir((path_ + "/real/objects").c_str());
        rmdir((path_ + "/real/staging").c_str());
        rmdir((path_ + "/real").c_str());
        rmdir((path_ + "/objects").c_str());
        rmdir((path_ + "/staging").c_str());
        rmdir(path_.c_str());
    }

    void CreateRequiredDirectories(const std::string& root = "") {
        const std::string base = root.empty() ? path_ : root;
        CHECK(mkdir((base + "/objects").c_str(), 0700) == 0);
        CHECK(mkdir((base + "/staging").c_str(), 0700) == 0);
    }

    const std::string& path() const { return path_; }

private:
    std::string path_;
};

}  // namespace

TEST_CASE(mysql_application_is_ready_with_schema_and_secure_storage) {
    RequireMySqlTests();
    TemporaryStorage storage;
    storage.CreateRequiredDirectories();
    Application application(TestAppConfig(storage.path(), 1));
    CHECK(application.Ready());
    RequestHead request{};
    request.method = "GET";
    request.path = "/api/v1/health/ready";
    HttpResponse response = application.Prepare(request)->Finish();
    CHECK(response.status() == 200);
    CHECK(response.head_and_body().find("\"status\":\"ready\"") !=
          std::string::npos);

    CHECK(chmod((storage.path() + "/staging").c_str(), 0770) == 0);
    CHECK(!application.Ready());
}

TEST_CASE(mysql_application_rejects_missing_storage_directory_at_startup) {
    RequireMySqlTests();
    TemporaryStorage storage;
    CHECK_THROWS_CODE(Application(TestAppConfig(storage.path(), 1)),
                      "storage_unavailable");
}

TEST_CASE(mysql_application_rejects_symlink_storage_root_at_startup) {
    RequireMySqlTests();
    TemporaryStorage storage;
    const std::string real = storage.path() + "/real";
    CHECK(mkdir(real.c_str(), 0700) == 0);
    storage.CreateRequiredDirectories(real);
    CHECK(symlink("real", (storage.path() + "/link").c_str()) == 0);
    CHECK_THROWS_CODE(
        Application(TestAppConfig(storage.path() + "/link", 1)),
        "storage_unavailable");
}
