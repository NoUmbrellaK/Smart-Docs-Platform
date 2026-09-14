#include "app/config.h"
#include "auth/auth_service.h"
#include "core/app_error.h"
#include "db/mysql.h"

#include <exception>
#include <iostream>
#include <openssl/crypto.h>
#include <string>

namespace {

int Usage() {
    std::cerr << "usage: smartdocs-admin create-user --username NAME "
                 "--password-stdin\n";
    return 2;
}

bool ReadOneTerminatedLine(std::string* value) {
    if (!std::getline(std::cin, *value) || std::cin.eof()) {
        return false;
    }
    char extra = 0;
    return !std::cin.get(extra);
}

void ClearSecret(std::string* value) {
    if (!value->empty()) {
        OPENSSL_cleanse(&(*value)[0], value->size());
        value->clear();
    }
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 5 || std::string(argv[1]) != "create-user" ||
        std::string(argv[2]) != "--username" ||
        std::string(argv[3]).empty() ||
        std::string(argv[4]) != "--password-stdin") {
        return Usage();
    }

    std::string password;
    if (!ReadOneTerminatedLine(&password)) {
        std::cerr << "invalid_password_input: expected exactly one "
                     "newline-terminated password\n";
        return 2;
    }

    try {
        const AppConfig config = AppConfig::LoadFromEnvironment();
        MySqlConfig mysql_config = MySqlConfig::FromAppConfig(config);
        mysql_config.pool_size = 1;
        MySqlPool database;
        database.Initialize(mysql_config);
        AuthService auth(database, config.session_seconds,
                         config.password_iterations);
        const UserIdentity user = auth.CreateUser(argv[3], password);
        ClearSecret(&password);
        std::cout << "created_user_id=" << user.id << '\n';
        return 0;
    } catch (const AppError& error) {
        ClearSecret(&password);
        std::cerr << error.code << ": " << error.message << '\n';
        return error.http_status == 400 || error.http_status == 409 ? 2 : 1;
    } catch (const std::exception&) {
        ClearSecret(&password);
        std::cerr << "internal_error: user creation failed\n";
        return 1;
    }
}
