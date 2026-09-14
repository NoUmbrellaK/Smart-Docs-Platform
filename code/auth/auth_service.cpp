#include "auth_service.h"

#include "core/app_error.h"
#include "core/crypto.h"
#include "core/id.h"

#include <cctype>
#include <cstdint>
#include <vector>

namespace {

std::string Trim(const std::string& value) {
    size_t first = 0;
    while (first < value.size() &&
           std::isspace(static_cast<unsigned char>(value[first]))) {
        ++first;
    }
    size_t last = value.size();
    while (last > first &&
           std::isspace(static_cast<unsigned char>(value[last - 1]))) {
        --last;
    }
    return value.substr(first, last - first);
}

bool IsLowerHex(const std::string& value, size_t size) {
    if (value.size() != size) {
        return false;
    }
    for (unsigned char ch : value) {
        if (!std::isdigit(ch) && (ch < 'a' || ch > 'f')) {
            return false;
        }
    }
    return true;
}

std::string RequestId(const std::string& supplied) {
    if (supplied.empty()) {
        return GenerateId();
    }
    if (!IsLowerHex(supplied, 32)) {
        throw AppError(400, "invalid_request", "request ID is invalid");
    }
    return supplied;
}

std::string Hex(const std::vector<unsigned char>& bytes) {
    static const char digits[] = "0123456789abcdef";
    std::string result(bytes.size() * 2, '0');
    for (size_t i = 0; i < bytes.size(); ++i) {
        result[i * 2] = digits[bytes[i] >> 4];
        result[i * 2 + 1] = digits[bytes[i] & 0x0f];
    }
    return result;
}

bool DecodeHex(const std::string& text, std::vector<unsigned char>* bytes) {
    if (text.size() % 2 != 0) {
        return false;
    }
    bytes->clear();
    bytes->reserve(text.size() / 2);
    for (size_t i = 0; i < text.size(); i += 2) {
        unsigned int value = 0;
        for (size_t offset = 0; offset < 2; ++offset) {
            const unsigned char ch = static_cast<unsigned char>(text[i + offset]);
            if (!std::isdigit(ch) && (ch < 'a' || ch > 'f')) {
                return false;
            }
            value = value * 16 +
                    (std::isdigit(ch) ? ch - '0' : ch - 'a' + 10);
        }
        bytes->push_back(static_cast<unsigned char>(value));
    }
    return true;
}

[[noreturn]] void InvalidCredentials() {
    throw AppError(401, "invalid_credentials",
                   "username or password is incorrect");
}

void ValidateNewUser(const std::string& username,
                     const std::string& password) {
    if (username.size() < 3 || username.size() > 64 ||
        username.find('\0') != std::string::npos ||
        password.size() < 12 || password.size() > 1024 ||
        password.find('\0') != std::string::npos) {
        throw AppError(400, "invalid_request",
                       "username or password does not meet requirements");
    }
}

}  // namespace

AuthService::AuthService(MySqlPool& pool, int session_seconds,
                         int password_iterations)
    : pool_(pool),
      session_seconds_(session_seconds),
      password_iterations_(password_iterations) {
    if (session_seconds <= 0 || password_iterations <= 0) {
        throw AppError(500, "config_invalid",
                       "authentication configuration is invalid");
    }
}

UserIdentity AuthService::CreateUser(const std::string& raw_username,
                                     const std::string& password,
                                     const std::string& request_id) {
    const std::string username = Trim(raw_username);
    ValidateNewUser(username, password);
    const PasswordHash password_hash =
        DerivePassword(password, password_iterations_);
    const StoredUser user{GenerateId(), username, Hex(password_hash.salt),
                          Hex(password_hash.digest),
                          static_cast<uint64_t>(password_hash.iterations),
                          "active"};
    try {
        MySqlConnection connection = pool_.Acquire();
        MySqlTransaction transaction(connection);
        repository_.InsertUser(connection, user);
        repository_.InsertAudit(connection, std::string(), "user.create",
                                "user", user.id, RequestId(request_id));
        transaction.Commit();
    } catch (const AppError& error) {
        if (error.code == "constraint_conflict") {
            throw AppError(409, "username_conflict",
                           "username is already in use");
        }
        throw;
    }
    return UserIdentity{user.id, user.username};
}

SessionTokens AuthService::Login(const std::string& raw_username,
                                 const std::string& password,
                                 const std::string& request_id) {
    const std::string username = Trim(raw_username);
    StoredUser user;
    MySqlConnection connection = pool_.Acquire();
    if (!repository_.FindUserByLogin(connection, username, &user) ||
        user.status != "active") {
        InvalidCredentials();
    }
    PasswordHash stored;
    if (!DecodeHex(user.password_salt, &stored.salt) ||
        !DecodeHex(user.password_hash, &stored.digest) ||
        user.password_iterations > static_cast<uint64_t>(INT32_MAX)) {
        throw AppError(500, "credential_data_invalid",
                       "stored credential data is invalid");
    }
    stored.iterations = static_cast<int>(user.password_iterations);
    if (!VerifyPassword(password, stored)) {
        InvalidCredentials();
    }

    const std::string session_id = GenerateId();
    const std::string raw_token = GenerateTokenHex(32);
    const std::string token_hash = Sha256Hex(raw_token.data(), raw_token.size());
    MySqlTransaction transaction(connection);
    const std::string expires_at = repository_.InsertSession(
        connection, session_id, token_hash, user.id,
        static_cast<uint64_t>(session_seconds_));
    repository_.InsertAudit(connection, user.id, "auth.login", "session",
                            session_id, RequestId(request_id));
    transaction.Commit();
    return SessionTokens{SessionContext{user.id, session_id, expires_at},
                         raw_token, UserIdentity{user.id, user.username}};
}

SessionContext AuthService::Authenticate(const std::string& raw_token) {
    if (!IsLowerHex(raw_token, 64)) {
        throw AppError(401, "authentication_required",
                       "a valid session is required");
    }
    const std::string token_hash = Sha256Hex(raw_token.data(), raw_token.size());
    MySqlConnection connection = pool_.Acquire();
    StoredSession stored;
    if (!repository_.FindActiveSession(connection, token_hash, &stored)) {
        throw AppError(401, "authentication_required",
                       "a valid session is required");
    }
    return SessionContext{stored.user_id, stored.id, stored.expires_at};
}

void AuthService::Logout(const std::string& raw_token,
                         const std::string& request_id) {
    const SessionContext session = Authenticate(raw_token);
    const std::string token_hash = Sha256Hex(raw_token.data(), raw_token.size());
    MySqlConnection connection = pool_.Acquire();
    MySqlTransaction transaction(connection);
    repository_.RevokeSession(connection, token_hash);
    repository_.InsertAudit(connection, session.user_id, "auth.logout",
                            "session", session.session_id,
                            RequestId(request_id));
    transaction.Commit();
}

UserIdentity AuthService::GetUser(const std::string& user_id) {
    MySqlConnection connection = pool_.Acquire();
    StoredUser user;
    if (!repository_.FindUserById(connection, user_id, &user) ||
        user.status != "active") {
        throw AppError(401, "authentication_required",
                       "a valid session is required");
    }
    return UserIdentity{user.id, user.username};
}
