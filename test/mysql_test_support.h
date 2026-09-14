#pragma once

#include "app/config.h"
#include "db/mysql.h"

bool MySqlTestsConfigured();
void RequireMySqlTests();
MySqlConfig TestMySqlConfig(int pool_size = 2);
AppConfig TestAppConfig(const std::string& storage_root, int pool_size = 2);
MySqlPool& TestDatabase();
void ResetTestDatabase();
