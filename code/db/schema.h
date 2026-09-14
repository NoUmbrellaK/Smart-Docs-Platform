#pragma once

#include <cstdint>

class MySqlConnection;

namespace Schema {

int64_t CurrentVersion(MySqlConnection& connection);
void RequireVersion(MySqlConnection& connection, int64_t required_version);

}  // namespace Schema
