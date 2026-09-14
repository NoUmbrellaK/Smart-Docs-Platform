#pragma once

#include <functional>
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
