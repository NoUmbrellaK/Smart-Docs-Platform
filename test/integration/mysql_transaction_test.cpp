#include "core/app_error.h"
#include "db/mysql.h"
#include "../mysql_test_support.h"
#include "../test_support.h"

#include <cstdint>
#include <string>
#include <utility>

namespace {

const char* kUserId = "11111111111111111111111111111111";
const char* kSalt = "0123456789abcdef0123456789abcdef";
const char* kHash = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

void InsertUser(MySqlConnection& connection, const std::string& id,
                const std::string& login_name) {
    connection.Execute(
        "INSERT INTO users(id, login_name, password_salt, password_hash, "
        "password_iterations, status) VALUES (?, ?, ?, ?, ?, ?)",
        {SqlValue(id), SqlValue(login_name), SqlValue(kSalt), SqlValue(kHash),
         SqlValue(static_cast<uint64_t>(210000)), SqlValue("active")});
}

}  // namespace

TEST_CASE(mysql_pool_connection_failure_does_not_publish_null_handles) {
    MySqlConfig config;
    config.host = "localhost";
    config.port = 3306;
    config.database = "missing";
    config.user = "missing";
    config.password = "missing";
    config.socket = "/tmp/smart-docs-no-such-mysql.sock";
    config.pool_size = 2;

    MySqlPool pool;
    CHECK_THROWS_CODE(pool.Initialize(config), "database_unavailable");
    CHECK(pool.Available() == 0);
}

TEST_CASE(mysql_connection_returns_to_pool_exactly_once_after_move) {
    RequireMySqlTests();
    MySqlPool pool;
    pool.Initialize(TestMySqlConfig(1));
    CHECK(pool.Available() == 1);
    {
        MySqlConnection first = pool.Acquire();
        CHECK(pool.Available() == 0);
        MySqlConnection moved = std::move(first);
        CHECK(pool.Available() == 0);
        CHECK(moved.ScalarInt("SELECT 1") == 1);
    }
    CHECK(pool.Available() == 1);
}

TEST_CASE(mysql_transaction_rolls_back_without_commit) {
    RequireMySqlTests();
    ResetTestDatabase();
    MySqlConnection connection = TestDatabase().Acquire();
    {
        MySqlTransaction transaction(connection);
        InsertUser(connection, kUserId, "rolled-back");
    }
    CHECK(connection.ScalarInt("SELECT COUNT(*) FROM users WHERE id=?",
                               {SqlValue(kUserId)}) == 0);
}

TEST_CASE(mysql_transaction_commit_persists_changes) {
    RequireMySqlTests();
    ResetTestDatabase();
    MySqlConnection connection = TestDatabase().Acquire();
    {
        MySqlTransaction transaction(connection);
        InsertUser(connection, kUserId, "committed");
        transaction.Commit();
    }
    CHECK(connection.ScalarInt("SELECT COUNT(*) FROM users WHERE id=?",
                               {SqlValue(kUserId)}) == 1);
}

TEST_CASE(mysql_typed_values_and_rows_round_trip) {
    RequireMySqlTests();
    MySqlConnection connection = TestDatabase().Acquire();
    const std::vector<MySqlRow> rows = connection.Query(
        "SELECT ?, ?, ?, ?, ?, CAST(? AS DATETIME(6))",
        {SqlValue("text"), SqlValue(static_cast<uint64_t>(42)),
         SqlValue(static_cast<int64_t>(-7)), SqlValue(true), SqlValue::Null(),
         SqlValue::Timestamp("2026-09-14 12:34:56.123456")});
    CHECK(rows.size() == 1);
    CHECK(rows[0].String(0) == "text");
    CHECK(rows[0].UInt64(1) == 42);
    CHECK(rows[0].Int64(2) == -7);
    CHECK(rows[0].Bool(3));
    CHECK(rows[0].IsNull(4));
    CHECK(rows[0].String(5) == "2026-09-14 12:34:56.123456");
}
