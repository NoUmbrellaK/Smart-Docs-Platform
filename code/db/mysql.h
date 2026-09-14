#pragma once

#include <mysql/mysql.h>

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

struct AppConfig;
struct MySqlPoolState;

struct MySqlConfig {
    std::string host;
    uint16_t port = 3306;
    std::string database;
    std::string user;
    std::string password;
    std::string socket;
    int pool_size = 1;

    static MySqlConfig FromAppConfig(const AppConfig& config);
};

class SqlValue {
public:
    enum class Kind { Null, String, UInt64, Int64, Boolean, Timestamp };

    explicit SqlValue(std::string value);
    explicit SqlValue(const char* value);
    explicit SqlValue(uint64_t value);
    explicit SqlValue(int64_t value);
    explicit SqlValue(bool value);

    static SqlValue Null();
    static SqlValue Timestamp(std::string utc_text);

private:
    explicit SqlValue(Kind kind);

    Kind kind_;
    std::string text_;
    uint64_t unsigned_value_ = 0;
    int64_t signed_value_ = 0;
    unsigned char boolean_value_ = 0;

    friend class MySqlStatement;
};

class MySqlRow {
public:
    std::string String(size_t column) const;
    uint64_t UInt64(size_t column) const;
    int64_t Int64(size_t column) const;
    bool Bool(size_t column) const;
    bool IsNull(size_t column) const;
    size_t Size() const;

private:
    struct Cell {
        bool is_null;
        std::string value;
    };
    explicit MySqlRow(std::vector<Cell> cells);
    const Cell& CheckedCell(size_t column) const;

    std::vector<Cell> cells_;
    friend class MySqlStatement;
};

class MySqlConnection {
public:
    MySqlConnection();
    ~MySqlConnection();
    MySqlConnection(const MySqlConnection&) = delete;
    MySqlConnection& operator=(const MySqlConnection&) = delete;
    MySqlConnection(MySqlConnection&& other) noexcept;
    MySqlConnection& operator=(MySqlConnection&& other) noexcept;

    uint64_t Execute(const std::string& sql,
                     const std::vector<SqlValue>& values = {});
    std::vector<MySqlRow> Query(const std::string& sql,
                               const std::vector<SqlValue>& values = {});
    int64_t ScalarInt(const std::string& sql,
                      const std::vector<SqlValue>& values = {});
    void Ping();
    explicit operator bool() const;

private:
    MySqlConnection(std::shared_ptr<MySqlPoolState> state, MYSQL* handle);
    MYSQL* Raw() const;
    void Release();

    std::shared_ptr<MySqlPoolState> state_;
    MYSQL* handle_;

    friend class MySqlPool;
    friend class MySqlStatement;
    friend class MySqlTransaction;
};

class MySqlStatement {
public:
    MySqlStatement(MySqlConnection& connection, const std::string& sql);
    ~MySqlStatement();
    MySqlStatement(const MySqlStatement&) = delete;
    MySqlStatement& operator=(const MySqlStatement&) = delete;

    uint64_t Execute(const std::vector<SqlValue>& values = {});
    std::vector<MySqlRow> Query(const std::vector<SqlValue>& values = {});

private:
    void BindAndExecute(const std::vector<SqlValue>& values);

    MYSQL_STMT* statement_;
};

class MySqlTransaction {
public:
    explicit MySqlTransaction(MySqlConnection& connection);
    ~MySqlTransaction();
    MySqlTransaction(const MySqlTransaction&) = delete;
    MySqlTransaction& operator=(const MySqlTransaction&) = delete;

    void Commit();
    void Rollback();

private:
    MySqlConnection* connection_;
    bool active_;
};

class MySqlPool {
public:
    MySqlPool();
    ~MySqlPool();
    MySqlPool(const MySqlPool&) = delete;
    MySqlPool& operator=(const MySqlPool&) = delete;

    void Initialize(const MySqlConfig& config);
    MySqlConnection Acquire();
    size_t Available() const;

private:
    std::shared_ptr<MySqlPoolState> state_;
};
