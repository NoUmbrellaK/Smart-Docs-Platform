#include "schema.h"

#include "mysql.h"
#include "core/app_error.h"

namespace Schema {

int64_t CurrentVersion(MySqlConnection& connection) {
    return connection.ScalarInt("SELECT COALESCE(MAX(version), 0) FROM schema_migrations");
}

void RequireVersion(MySqlConnection& connection, int64_t required_version) {
    if (CurrentVersion(connection) != required_version) {
        throw AppError(500, "schema_version_mismatch",
                       "database schema version does not match this server", false);
    }
}

}  // namespace Schema
