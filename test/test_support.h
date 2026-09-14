#pragma once

#include <functional>
#include <map>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

using TestFunction = std::function<void()>;

std::vector<std::pair<std::string, TestFunction>>& TestRegistry();

struct TestRegistration {
    TestRegistration(const char* name, TestFunction test);
};

#define TEST_CASE(name)                                                        \
    static void name();                                                        \
    static TestRegistration name##_registration(#name, name);                 \
    static void name()

#define CHECK(expression)                                                      \
    do {                                                                       \
        if (!(expression)) {                                                   \
            throw std::runtime_error(std::string(__FILE__) + ":" +            \
                                     std::to_string(__LINE__) +                \
                                     ": CHECK failed: " #expression);          \
        }                                                                      \
    } while (false)

#define CHECK_THROWS_CODE(expression, expected_code)                           \
    do {                                                                       \
        bool caught_app_error = false;                                         \
        try {                                                                  \
            (void)(expression);                                                \
        } catch (const AppError& error) {                                      \
            caught_app_error = true;                                           \
            CHECK(error.code == (expected_code));                              \
        }                                                                      \
        CHECK(caught_app_error);                                               \
    } while (false)

class ScopedEnvironment {
public:
    ScopedEnvironment() = default;
    ScopedEnvironment(const ScopedEnvironment&) = delete;
    ScopedEnvironment& operator=(const ScopedEnvironment&) = delete;
    ~ScopedEnvironment();

    void Set(const std::string& name, const std::string& value);
    void Unset(const std::string& name);

private:
    void Remember(const std::string& name);
    std::map<std::string, std::pair<bool, std::string>> originals_;
};
