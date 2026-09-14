#include "core/app_error.h"
#include "db/schema.h"
#include "../mysql_test_support.h"
#include "../test_support.h"

#include <cstdint>
#include <string>

namespace {

const char* kUser = "11111111111111111111111111111111";
const char* kProject = "22222222222222222222222222222222";
const char* kRoot = "33333333333333333333333333333333";
const char* kSalt = "0123456789abcdef0123456789abcdef";
const char* kHash = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
const char* kSha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

void InsertProjectFixture(MySqlConnection& connection) {
    connection.Execute(
        "INSERT INTO users(id, login_name, password_salt, password_hash, "
        "password_iterations, status) VALUES (?, ?, ?, ?, ?, ?)",
        {SqlValue(kUser), SqlValue("owner"), SqlValue(kSalt), SqlValue(kHash),
         SqlValue(static_cast<uint64_t>(210000)), SqlValue("active")});
    connection.Execute(
        "INSERT INTO projects(id, name, created_by) VALUES (?, ?, ?)",
        {SqlValue(kProject), SqlValue("Project"), SqlValue(kUser)});
    connection.Execute(
        "INSERT INTO directories(id, project_id, parent_id, name, created_by) "
        "VALUES (?, ?, ?, ?, ?)",
        {SqlValue(kRoot), SqlValue(kProject), SqlValue::Null(), SqlValue("Root"),
         SqlValue(kUser)});
    connection.Execute("UPDATE projects SET root_directory_id=? WHERE id=?",
                       {SqlValue(kRoot), SqlValue(kProject)});
}

}  // namespace

TEST_CASE(mysql_schema_version_is_two) {
    RequireMySqlTests();
    MySqlConnection connection = TestDatabase().Acquire();
    CHECK(Schema::CurrentVersion(connection) == 2);
    Schema::RequireVersion(connection, 2);
}

TEST_CASE(mysql_schema_rejects_two_root_directories_for_one_project) {
    RequireMySqlTests();
    ResetTestDatabase();
    MySqlConnection connection = TestDatabase().Acquire();
    InsertProjectFixture(connection);
    CHECK_THROWS_CODE(
        connection.Execute(
            "INSERT INTO directories(id, project_id, parent_id, name, created_by) "
            "VALUES (?, ?, ?, ?, ?)",
            {SqlValue("44444444444444444444444444444444"), SqlValue(kProject),
             SqlValue::Null(), SqlValue("Other root"), SqlValue(kUser)}),
        "constraint_conflict");
}

TEST_CASE(mysql_schema_rejects_duplicate_active_file_name) {
    RequireMySqlTests();
    ResetTestDatabase();
    MySqlConnection connection = TestDatabase().Acquire();
    InsertProjectFixture(connection);
    connection.Execute(
        "INSERT INTO files(id, project_id, directory_id, name, created_by) "
        "VALUES (?, ?, ?, ?, ?)",
        {SqlValue("44444444444444444444444444444444"), SqlValue(kProject),
         SqlValue(kRoot), SqlValue("handover.pdf"), SqlValue(kUser)});
    CHECK_THROWS_CODE(
        connection.Execute(
            "INSERT INTO files(id, project_id, directory_id, name, created_by) "
            "VALUES (?, ?, ?, ?, ?)",
            {SqlValue("55555555555555555555555555555555"), SqlValue(kProject),
             SqlValue(kRoot), SqlValue("handover.pdf"), SqlValue(kUser)}),
        "constraint_conflict");
}

TEST_CASE(mysql_schema_rejects_duplicate_upload_part_number) {
    RequireMySqlTests();
    ResetTestDatabase();
    MySqlConnection connection = TestDatabase().Acquire();
    InsertProjectFixture(connection);
    const std::string task = "66666666666666666666666666666666";
    connection.Execute(
        "INSERT INTO upload_tasks(id, owner_user_id, project_id, "
        "target_directory_id, mode, expected_name, expected_size_bytes, "
        "expected_sha256, chunk_size_bytes, total_parts, state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        {SqlValue(task), SqlValue(kUser), SqlValue(kProject), SqlValue(kRoot),
         SqlValue("create_file"), SqlValue("upload.bin"),
         SqlValue(static_cast<uint64_t>(3)), SqlValue(kSha),
         SqlValue(static_cast<uint64_t>(3)), SqlValue(static_cast<uint64_t>(1)),
         SqlValue("uploading")});
    connection.Execute(
        "INSERT INTO upload_parts(upload_task_id, part_number, size_bytes, "
        "sha256, staging_name) VALUES (?, ?, ?, ?, ?)",
        {SqlValue(task), SqlValue(static_cast<uint64_t>(1)),
         SqlValue(static_cast<uint64_t>(3)), SqlValue(kSha),
         SqlValue("part-1")});
    CHECK_THROWS_CODE(
        connection.Execute(
            "INSERT INTO upload_parts(upload_task_id, part_number, size_bytes, "
            "sha256, staging_name) VALUES (?, ?, ?, ?, ?)",
            {SqlValue(task), SqlValue(static_cast<uint64_t>(1)),
             SqlValue(static_cast<uint64_t>(3)), SqlValue(kSha),
             SqlValue("part-1-retry")}),
        "constraint_conflict");
}

TEST_CASE(mysql_schema_allows_zero_part_upload_tasks) {
    RequireMySqlTests();
    ResetTestDatabase();
    MySqlConnection connection = TestDatabase().Acquire();
    InsertProjectFixture(connection);
    connection.Execute(
        "INSERT INTO upload_tasks(id, owner_user_id, project_id, "
        "target_directory_id, mode, expected_name, expected_size_bytes, "
        "expected_sha256, chunk_size_bytes, total_parts, state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        {SqlValue("77777777777777777777777777777777"), SqlValue(kUser),
         SqlValue(kProject), SqlValue(kRoot), SqlValue("create_file"),
         SqlValue("empty.txt"), SqlValue(static_cast<uint64_t>(0)),
         SqlValue(kSha), SqlValue(static_cast<uint64_t>(256)),
         SqlValue(static_cast<uint64_t>(0)), SqlValue("uploading")});
}

TEST_CASE(mysql_schema_allows_zero_based_upload_parts) {
    RequireMySqlTests();
    ResetTestDatabase();
    MySqlConnection connection = TestDatabase().Acquire();
    InsertProjectFixture(connection);
    const std::string task = "88888888888888888888888888888888";
    connection.Execute(
        "INSERT INTO upload_tasks(id, owner_user_id, project_id, "
        "target_directory_id, mode, expected_name, expected_size_bytes, "
        "expected_sha256, chunk_size_bytes, total_parts, state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        {SqlValue(task), SqlValue(kUser), SqlValue(kProject), SqlValue(kRoot),
         SqlValue("create_file"), SqlValue("one-byte.bin"),
         SqlValue(static_cast<uint64_t>(1)), SqlValue(kSha),
         SqlValue(static_cast<uint64_t>(256)), SqlValue(static_cast<uint64_t>(1)),
         SqlValue("uploading")});
    connection.Execute(
        "INSERT INTO upload_parts(upload_task_id, part_number, size_bytes, "
        "sha256, staging_name) VALUES (?, ?, ?, ?, ?)",
        {SqlValue(task), SqlValue(static_cast<uint64_t>(0)),
         SqlValue(static_cast<uint64_t>(1)), SqlValue(kSha), SqlValue("0")});
}
