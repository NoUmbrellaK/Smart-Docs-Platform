#include "../test_support.h"

#include "nlohmann/json.hpp"

TEST_CASE(baseline_json_version) {
    CHECK(NLOHMANN_JSON_VERSION_MAJOR == 3);
    CHECK(NLOHMANN_JSON_VERSION_MINOR == 11);
    CHECK(NLOHMANN_JSON_VERSION_PATCH == 3);

    const nlohmann::json value = nlohmann::json::parse(R"({"ok":true})");
    CHECK(value.at("ok").get<bool>());
}
