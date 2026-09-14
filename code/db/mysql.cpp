#include "mysql.h"

#include "app/config.h"
#include "core/app_error.h"

#include <algorithm>
#include <cerrno>
#include <condition_variable>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <limits>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <utility>

namespace {

[[noreturn]] void ThrowDatabaseError(unsigned int error) {
    if (error == 1062) {
        throw AppError(409, "constraint_conflict",
                       "database constraint conflict", false);
    }
    if (error == 1205 || error == 1213) {
        throw AppError(503, "database_busy", "database operation can be retried",
                       true);
    }
    throw AppError(503, "database_unavailable", "database operation failed",
                   true);
}

[[noreturn]] void ThrowInvalidResult() {
    throw AppError(500, "database_result_invalid",
                   "database returned an invalid result", false);
}

struct ResultMetadataDeleter {
    void operator()(MYSQL_RES* result) const {
        if (result != nullptr) {
            mysql_free_result(result);
        }
    }
};

}  // namespace

struct MySqlPoolState {
    ~MySqlPoolState() {
        for (MYSQL* handle : handles) {
            mysql_close(handle);
        }
    }

    void Return(MYSQL* handle) {
        const bool healthy = mysql_rollback(handle) == 0 &&
                             mysql_autocommit(handle, true) == 0;
        std::lock_guard<std::mutex> lock(mutex);
        if (!accepting || !healthy) {
            mysql_close(handle);
            broken = broken || !healthy;
        } else {
            handles.push_back(handle);
        }
        condition.notify_one();
    }

    mutable std::mutex mutex;
    std::condition_variable condition;
    std::deque<MYSQL*> handles;
    bool accepting = true;
    bool broken = false;
};

MySqlConfig MySqlConfig::FromAppConfig(const AppConfig& config) {
    MySqlConfig result;
    result.host = config.mysql_host;
    result.port = config.mysql_port;
    result.database = config.mysql_database;
    result.user = config.mysql_user;
    result.password = config.mysql_password;
    result.socket = config.mysql_socket;
    result.pool_size = config.mysql_pool_size;
    return result;
}

SqlValue::SqlValue(Kind kind) : kind_(kind) {}

SqlValue::SqlValue(std::string value)
    : kind_(Kind::String), text_(std::move(value)) {}

SqlValue::SqlValue(const char* value)
    : kind_(Kind::String), text_(value == nullptr ? "" : value) {
    if (value == nullptr) {
        throw std::invalid_argument("SqlValue string cannot be null");
    }
}

SqlValue::SqlValue(uint64_t value)
    : kind_(Kind::UInt64), unsigned_value_(value) {}

SqlValue::SqlValue(int64_t value)
    : kind_(Kind::Int64), signed_value_(value) {}

SqlValue::SqlValue(bool value)
    : kind_(Kind::Boolean), boolean_value_(value ? 1 : 0) {}

SqlValue SqlValue::Null() {
    return SqlValue(Kind::Null);
}

SqlValue SqlValue::Timestamp(std::string utc_text) {
    SqlValue value(Kind::Timestamp);
    value.text_ = std::move(utc_text);
    return value;
}

MySqlRow::MySqlRow(std::vector<Cell> cells) : cells_(std::move(cells)) {}

const MySqlRow::Cell& MySqlRow::CheckedCell(size_t column) const {
    if (column >= cells_.size() || cells_[column].is_null) {
        ThrowInvalidResult();
    }
    return cells_[column];
}

std::string MySqlRow::String(size_t column) const {
    return CheckedCell(column).value;
}

uint64_t MySqlRow::UInt64(size_t column) const {
    const std::string& value = CheckedCell(column).value;
    if (value.empty() || value.front() == '-') {
        ThrowInvalidResult();
    }
    errno = 0;
    char* end = nullptr;
    const unsigned long long parsed = std::strtoull(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0') {
        ThrowInvalidResult();
    }
    return static_cast<uint64_t>(parsed);
}

int64_t MySqlRow::Int64(size_t column) const {
    const std::string& value = CheckedCell(column).value;
    if (value.empty()) {
        ThrowInvalidResult();
    }
    errno = 0;
    char* end = nullptr;
    const long long parsed = std::strtoll(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0') {
        ThrowInvalidResult();
    }
    return static_cast<int64_t>(parsed);
}

bool MySqlRow::Bool(size_t column) const {
    const std::string& value = CheckedCell(column).value;
    if (value == "0") return false;
    if (value == "1") return true;
    ThrowInvalidResult();
}

bool MySqlRow::IsNull(size_t column) const {
    if (column >= cells_.size()) {
        ThrowInvalidResult();
    }
    return cells_[column].is_null;
}

size_t MySqlRow::Size() const {
    return cells_.size();
}

MySqlConnection::MySqlConnection() : handle_(nullptr) {}

MySqlConnection::MySqlConnection(std::shared_ptr<MySqlPoolState> state,
                                 MYSQL* handle)
    : state_(std::move(state)), handle_(handle) {}

MySqlConnection::~MySqlConnection() {
    Release();
}

MySqlConnection::MySqlConnection(MySqlConnection&& other) noexcept
    : state_(std::move(other.state_)), handle_(other.handle_) {
    other.handle_ = nullptr;
}

MySqlConnection& MySqlConnection::operator=(MySqlConnection&& other) noexcept {
    if (this != &other) {
        Release();
        state_ = std::move(other.state_);
        handle_ = other.handle_;
        other.handle_ = nullptr;
    }
    return *this;
}

void MySqlConnection::Release() {
    if (handle_ != nullptr) {
        state_->Return(handle_);
        handle_ = nullptr;
    }
    state_.reset();
}

MYSQL* MySqlConnection::Raw() const {
    if (handle_ == nullptr) {
        throw AppError(503, "database_unavailable", "database connection is not available",
                       true);
    }
    return handle_;
}

uint64_t MySqlConnection::Execute(const std::string& sql,
                                  const std::vector<SqlValue>& values) {
    MySqlStatement statement(*this, sql);
    return statement.Execute(values);
}

std::vector<MySqlRow> MySqlConnection::Query(
    const std::string& sql, const std::vector<SqlValue>& values) {
    MySqlStatement statement(*this, sql);
    return statement.Query(values);
}

int64_t MySqlConnection::ScalarInt(const std::string& sql,
                                   const std::vector<SqlValue>& values) {
    const std::vector<MySqlRow> rows = Query(sql, values);
    if (rows.size() != 1 || rows[0].Size() != 1) {
        ThrowInvalidResult();
    }
    return rows[0].Int64(0);
}

void MySqlConnection::Ping() {
    MYSQL* handle = Raw();
    if (mysql_ping(handle) != 0) {
        ThrowDatabaseError(mysql_errno(handle));
    }
}

MySqlConnection::operator bool() const {
    return handle_ != nullptr;
}

MySqlStatement::MySqlStatement(MySqlConnection& connection,
                               const std::string& sql)
    : statement_(mysql_stmt_init(connection.Raw())) {
    if (statement_ == nullptr) {
        ThrowDatabaseError(mysql_errno(connection.Raw()));
    }
    if (mysql_stmt_prepare(statement_, sql.data(),
                           static_cast<unsigned long>(sql.size())) != 0) {
        const unsigned int error = mysql_stmt_errno(statement_);
        mysql_stmt_close(statement_);
        statement_ = nullptr;
        ThrowDatabaseError(error);
    }
}

MySqlStatement::~MySqlStatement() {
    if (statement_ != nullptr) {
        mysql_stmt_close(statement_);
    }
}

void MySqlStatement::BindAndExecute(const std::vector<SqlValue>& values) {
    if (mysql_stmt_param_count(statement_) != values.size()) {
        throw AppError(500, "database_query_invalid",
                       "database parameter count mismatch", false);
    }

    std::vector<MYSQL_BIND> bindings(values.size());
    std::vector<unsigned long> lengths(values.size());
    for (size_t i = 0; i < values.size(); ++i) {
        MYSQL_BIND& binding = bindings[i];
        const SqlValue& value = values[i];
        switch (value.kind_) {
        case SqlValue::Kind::Null:
            binding.buffer_type = MYSQL_TYPE_NULL;
            break;
        case SqlValue::Kind::String:
        case SqlValue::Kind::Timestamp:
            lengths[i] = static_cast<unsigned long>(value.text_.size());
            binding.buffer_type = MYSQL_TYPE_STRING;
            binding.buffer = const_cast<char*>(value.text_.data());
            binding.buffer_length = lengths[i];
            binding.length = &lengths[i];
            break;
        case SqlValue::Kind::UInt64:
            binding.buffer_type = MYSQL_TYPE_LONGLONG;
            binding.buffer = const_cast<uint64_t*>(&value.unsigned_value_);
            binding.is_unsigned = true;
            break;
        case SqlValue::Kind::Int64:
            binding.buffer_type = MYSQL_TYPE_LONGLONG;
            binding.buffer = const_cast<int64_t*>(&value.signed_value_);
            binding.is_unsigned = false;
            break;
        case SqlValue::Kind::Boolean:
            binding.buffer_type = MYSQL_TYPE_TINY;
            binding.buffer = const_cast<unsigned char*>(&value.boolean_value_);
            binding.is_unsigned = true;
            break;
        }
    }

    if (!bindings.empty() && mysql_stmt_bind_param(statement_, bindings.data()) != 0) {
        ThrowDatabaseError(mysql_stmt_errno(statement_));
    }
    if (mysql_stmt_execute(statement_) != 0) {
        ThrowDatabaseError(mysql_stmt_errno(statement_));
    }
}

uint64_t MySqlStatement::Execute(const std::vector<SqlValue>& values) {
    BindAndExecute(values);
    const my_ulonglong affected = mysql_stmt_affected_rows(statement_);
    if (affected == static_cast<my_ulonglong>(-1)) {
        ThrowDatabaseError(mysql_stmt_errno(statement_));
    }
    return static_cast<uint64_t>(affected);
}

std::vector<MySqlRow> MySqlStatement::Query(
    const std::vector<SqlValue>& values) {
    bool update_max_length = true;
    if (mysql_stmt_attr_set(statement_, STMT_ATTR_UPDATE_MAX_LENGTH,
                            &update_max_length) != 0) {
        ThrowDatabaseError(mysql_stmt_errno(statement_));
    }
    BindAndExecute(values);

    std::unique_ptr<MYSQL_RES, ResultMetadataDeleter> metadata(
        mysql_stmt_result_metadata(statement_));
    if (!metadata) {
        ThrowDatabaseError(mysql_stmt_errno(statement_));
    }
    if (mysql_stmt_store_result(statement_) != 0) {
        ThrowDatabaseError(mysql_stmt_errno(statement_));
    }

    const unsigned int column_count = mysql_num_fields(metadata.get());
    MYSQL_FIELD* fields = mysql_fetch_fields(metadata.get());
    std::vector<std::vector<char>> buffers(column_count);
    std::vector<unsigned long> lengths(column_count);
    std::unique_ptr<bool[]> nulls(new bool[column_count]());
    std::unique_ptr<bool[]> errors(new bool[column_count]());
    std::vector<MYSQL_BIND> bindings(column_count);
    for (unsigned int i = 0; i < column_count; ++i) {
        const unsigned long size = std::max<unsigned long>(fields[i].max_length, 1);
        buffers[i].resize(size);
        bindings[i].buffer_type = MYSQL_TYPE_STRING;
        bindings[i].buffer = buffers[i].data();
        bindings[i].buffer_length = size;
        bindings[i].length = &lengths[i];
        bindings[i].is_null = &nulls[i];
        bindings[i].error = &errors[i];
    }
    if (column_count != 0 &&
        mysql_stmt_bind_result(statement_, bindings.data()) != 0) {
        ThrowDatabaseError(mysql_stmt_errno(statement_));
    }

    std::vector<MySqlRow> rows;
    while (true) {
        const int status = mysql_stmt_fetch(statement_);
        if (status == MYSQL_NO_DATA) {
            break;
        }
        if (status != 0) {
            ThrowDatabaseError(mysql_stmt_errno(statement_));
        }
        std::vector<MySqlRow::Cell> cells;
        cells.reserve(column_count);
        for (unsigned int i = 0; i < column_count; ++i) {
            if (errors[i] || lengths[i] > buffers[i].size()) {
                ThrowInvalidResult();
            }
            cells.push_back(MySqlRow::Cell{
                nulls[i], nulls[i]
                              ? std::string()
                              : std::string(buffers[i].data(), lengths[i])});
        }
        rows.emplace_back(MySqlRow(std::move(cells)));
    }
    mysql_stmt_free_result(statement_);
    return rows;
}

MySqlTransaction::MySqlTransaction(MySqlConnection& connection)
    : connection_(&connection), active_(true) {
    MYSQL* handle = connection_->Raw();
    if (mysql_autocommit(handle, false) != 0) {
        active_ = false;
        ThrowDatabaseError(mysql_errno(handle));
    }
}

MySqlTransaction::~MySqlTransaction() {
    if (active_ && connection_ != nullptr && connection_->handle_ != nullptr) {
        MYSQL* handle = connection_->handle_;
        (void)mysql_rollback(handle);
        (void)mysql_autocommit(handle, true);
    }
}

void MySqlTransaction::Commit() {
    if (!active_) {
        throw std::logic_error("transaction is no longer active");
    }
    MYSQL* handle = connection_->Raw();
    if (mysql_commit(handle) != 0) {
        ThrowDatabaseError(mysql_errno(handle));
    }
    active_ = false;
    if (mysql_autocommit(handle, true) != 0) {
        ThrowDatabaseError(mysql_errno(handle));
    }
}

void MySqlTransaction::Rollback() {
    if (!active_) {
        return;
    }
    MYSQL* handle = connection_->Raw();
    if (mysql_rollback(handle) != 0) {
        ThrowDatabaseError(mysql_errno(handle));
    }
    active_ = false;
    if (mysql_autocommit(handle, true) != 0) {
        ThrowDatabaseError(mysql_errno(handle));
    }
}

MySqlPool::MySqlPool() = default;

MySqlPool::~MySqlPool() {
    if (!state_) {
        return;
    }
    {
        std::lock_guard<std::mutex> lock(state_->mutex);
        state_->accepting = false;
        for (MYSQL* handle : state_->handles) {
            mysql_close(handle);
        }
        state_->handles.clear();
    }
    state_->condition.notify_all();
}

void MySqlPool::Initialize(const MySqlConfig& config) {
    if (state_) {
        throw std::logic_error("MySqlPool is already initialized");
    }
    if (config.host.empty() || config.database.empty() || config.user.empty() ||
        config.password.empty() || config.pool_size <= 0 ||
        (config.socket.empty() && config.port == 0)) {
        throw AppError(500, "config_invalid", "database configuration is invalid",
                       false);
    }

    std::deque<MYSQL*> opened;
    for (int i = 0; i < config.pool_size; ++i) {
        MYSQL* handle = mysql_init(nullptr);
        if (handle == nullptr) {
            for (MYSQL* existing : opened) mysql_close(existing);
            throw AppError(503, "database_unavailable",
                           "database connection failed", true);
        }
        unsigned int timeout_seconds = 2;
        (void)mysql_options(handle, MYSQL_OPT_CONNECT_TIMEOUT, &timeout_seconds);
        (void)mysql_options(handle, MYSQL_SET_CHARSET_NAME, "utf8mb4");
        MYSQL* connected = mysql_real_connect(
            handle, config.host.c_str(), config.user.c_str(),
            config.password.c_str(), config.database.c_str(), config.port,
            config.socket.empty() ? nullptr : config.socket.c_str(), 0);
        if (connected == nullptr ||
            mysql_query(handle, "SET time_zone = '+00:00'") != 0) {
            mysql_close(handle);
            for (MYSQL* existing : opened) mysql_close(existing);
            throw AppError(503, "database_unavailable",
                           "database connection failed", true);
        }
        opened.push_back(handle);
    }

    std::shared_ptr<MySqlPoolState> state(new MySqlPoolState());
    state->handles.swap(opened);
    state_ = std::move(state);
}

MySqlConnection MySqlPool::Acquire() {
    if (!state_) {
        throw AppError(503, "database_unavailable",
                       "database pool is not initialized", true);
    }
    std::shared_ptr<MySqlPoolState> state = state_;
    std::unique_lock<std::mutex> lock(state->mutex);
    state->condition.wait(lock, [state]() {
        return !state->handles.empty() || !state->accepting || state->broken;
    });
    if (!state->accepting || (state->handles.empty() && state->broken)) {
        throw AppError(503, "database_unavailable",
                       "database connection is not available", true);
    }
    MYSQL* handle = state->handles.front();
    state->handles.pop_front();
    return MySqlConnection(std::move(state), handle);
}

size_t MySqlPool::Available() const {
    if (!state_) {
        return 0;
    }
    std::lock_guard<std::mutex> lock(state_->mutex);
    return state_->handles.size();
}
