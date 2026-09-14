#pragma once

#include "auth_repository.h"

#include <string>

class MySqlPool;

struct UserIdentity {
    std::string id;
    std::string username;
};

struct SessionContext {
    std::string user_id;
    std::string session_id;
    std::string expires_at;
};

struct SessionTokens {
    SessionContext session;
    std::string raw_token;
    UserIdentity user;
};

class AuthService {
public:
    AuthService(MySqlPool& pool, int session_seconds, int password_iterations);

    UserIdentity CreateUser(const std::string& username,
                            const std::string& password,
                            const std::string& request_id = std::string());
    SessionTokens Login(const std::string& username,
                        const std::string& password,
                        const std::string& request_id = std::string());
    void Logout(const std::string& raw_token,
                const std::string& request_id = std::string());
    SessionContext Authenticate(const std::string& raw_token);
    UserIdentity GetUser(const std::string& user_id);

private:
    MySqlPool& pool_;
    AuthRepository repository_;
    int session_seconds_;
    int password_iterations_;
};
