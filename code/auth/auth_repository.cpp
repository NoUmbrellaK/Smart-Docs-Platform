#include "auth_repository.h"

#include "core/id.h"

namespace {

StoredUser UserFromRow(const MySqlRow& row) {
    return StoredUser{row.String(0), row.String(1), row.String(2),
                      row.String(3), row.UInt64(4), row.String(5)};
}

}  // namespace

bool AuthRepository::FindUserByLogin(MySqlConnection& connection,
                                     const std::string& username,
                                     StoredUser* user) const {
    const std::vector<MySqlRow> rows = connection.Query(
        "SELECT id, login_name, password_salt, password_hash, "
        "password_iterations, status FROM users WHERE login_name=? LIMIT 1",
        {SqlValue(username)});
    if (rows.empty()) {
        return false;
    }
    *user = UserFromRow(rows[0]);
    return true;
}

bool AuthRepository::FindUserById(MySqlConnection& connection,
                                  const std::string& user_id,
                                  StoredUser* user) const {
    const std::vector<MySqlRow> rows = connection.Query(
        "SELECT id, login_name, password_salt, password_hash, "
        "password_iterations, status FROM users WHERE id=? LIMIT 1",
        {SqlValue(user_id)});
    if (rows.empty()) {
        return false;
    }
    *user = UserFromRow(rows[0]);
    return true;
}

void AuthRepository::InsertUser(MySqlConnection& connection,
                                const StoredUser& user) const {
    connection.Execute(
        "INSERT INTO users(id, login_name, password_salt, password_hash, "
        "password_iterations, status) VALUES (?, ?, ?, ?, ?, ?)",
        {SqlValue(user.id), SqlValue(user.username),
         SqlValue(user.password_salt), SqlValue(user.password_hash),
         SqlValue(user.password_iterations), SqlValue(user.status)});
}

std::string AuthRepository::InsertSession(
    MySqlConnection& connection, const std::string& session_id,
    const std::string& token_hash, const std::string& user_id,
    uint64_t lifetime_seconds) const {
    connection.Execute(
        "INSERT INTO auth_sessions(id, token_hash, user_id, expires_at) "
        "VALUES (?, ?, ?, DATE_ADD(UTC_TIMESTAMP(6), INTERVAL ? SECOND))",
        {SqlValue(session_id), SqlValue(token_hash), SqlValue(user_id),
         SqlValue(lifetime_seconds)});
    const std::vector<MySqlRow> rows = connection.Query(
        "SELECT DATE_FORMAT(expires_at, '%Y-%m-%dT%H:%i:%s.%fZ') "
        "FROM auth_sessions WHERE id=?",
        {SqlValue(session_id)});
    return rows[0].String(0);
}

bool AuthRepository::FindActiveSession(MySqlConnection& connection,
                                       const std::string& token_hash,
                                       StoredSession* session) const {
    const std::vector<MySqlRow> rows = connection.Query(
        "SELECT s.id, s.user_id, "
        "DATE_FORMAT(s.expires_at, '%Y-%m-%dT%H:%i:%s.%fZ') "
        "FROM auth_sessions s JOIN users u ON u.id=s.user_id "
        "WHERE s.token_hash=? AND s.revoked_at IS NULL "
        "AND s.expires_at>UTC_TIMESTAMP(6) AND u.status='active' LIMIT 1",
        {SqlValue(token_hash)});
    if (rows.empty()) {
        return false;
    }
    *session = StoredSession{rows[0].String(0), rows[0].String(1),
                             rows[0].String(2)};
    return true;
}

void AuthRepository::RevokeSession(MySqlConnection& connection,
                                   const std::string& token_hash) const {
    connection.Execute(
        "UPDATE auth_sessions SET revoked_at=COALESCE(revoked_at, "
        "UTC_TIMESTAMP(6)) WHERE token_hash=?",
        {SqlValue(token_hash)});
}

void AuthRepository::InsertAudit(MySqlConnection& connection,
                                 const std::string& actor_id,
                                 const std::string& action,
                                 const std::string& object_type,
                                 const std::string& object_id,
                                 const std::string& request_id) const {
    connection.Execute(
        "INSERT INTO audit_records(id, actor_user_id, action, object_type, "
        "object_id, request_id, result) VALUES (?, ?, ?, ?, ?, ?, 'success')",
        {SqlValue(GenerateId()),
         actor_id.empty() ? SqlValue::Null() : SqlValue(actor_id),
         SqlValue(action), SqlValue(object_type), SqlValue(object_id),
         SqlValue(request_id)});
}
