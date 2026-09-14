#pragma once

#include "db/mysql.h"

#include <cstdint>
#include <string>

struct StoredUser {
    std::string id;
    std::string username;
    std::string password_salt;
    std::string password_hash;
    uint64_t password_iterations;
    std::string status;
};

struct StoredSession {
    std::string id;
    std::string user_id;
    std::string expires_at;
};

class AuthRepository {
public:
    bool FindUserByLogin(MySqlConnection& connection,
                         const std::string& username,
                         StoredUser* user) const;
    bool FindUserById(MySqlConnection& connection, const std::string& user_id,
                      StoredUser* user) const;
    void InsertUser(MySqlConnection& connection, const StoredUser& user) const;
    std::string InsertSession(MySqlConnection& connection,
                              const std::string& session_id,
                              const std::string& token_hash,
                              const std::string& user_id,
                              uint64_t lifetime_seconds) const;
    bool FindActiveSession(MySqlConnection& connection,
                           const std::string& token_hash,
                           StoredSession* session) const;
    void RevokeSession(MySqlConnection& connection,
                       const std::string& token_hash) const;
    void InsertAudit(MySqlConnection& connection, const std::string& actor_id,
                     const std::string& action, const std::string& object_type,
                     const std::string& object_id,
                     const std::string& request_id) const;
};
