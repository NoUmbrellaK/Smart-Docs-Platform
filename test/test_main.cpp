#include "test_support.h"

#include <cstdlib>
#include <exception>
#include <iostream>
#include <string>

#include <unistd.h>

std::vector<std::pair<std::string, TestFunction>>& TestRegistry() {
    static std::vector<std::pair<std::string, TestFunction>> tests;
    return tests;
}

TestRegistration::TestRegistration(const char* name, TestFunction test) {
    TestRegistry().emplace_back(name, std::move(test));
}

ScopedEnvironment::~ScopedEnvironment() {
    for (const auto& entry : originals_) {
        if (entry.second.first) {
            setenv(entry.first.c_str(), entry.second.second.c_str(), 1);
        } else {
            unsetenv(entry.first.c_str());
        }
    }
}

void ScopedEnvironment::Remember(const std::string& name) {
    if (originals_.count(name) != 0) {
        return;
    }
    const char* value = std::getenv(name.c_str());
    originals_[name] = value == nullptr
                           ? std::make_pair(false, std::string())
                           : std::make_pair(true, std::string(value));
}

void ScopedEnvironment::Set(const std::string& name, const std::string& value) {
    Remember(name);
    setenv(name.c_str(), value.c_str(), 1);
}

void ScopedEnvironment::Unset(const std::string& name) {
    Remember(name);
    unsetenv(name.c_str());
}

namespace {

std::string FilterFromArguments(int argc, char** argv) {
    const std::string prefix = "--filter=";
    for (int index = 1; index < argc; ++index) {
        const std::string argument(argv[index]);
        if (argument.compare(0, prefix.size(), prefix) == 0) {
            return argument.substr(prefix.size());
        }
    }
    const char* environment_filter = std::getenv("TEST_FILTER");
    return environment_filter == nullptr ? std::string() : environment_filter;
}

}  // namespace

int main(int argc, char** argv) {
    const std::string filter = FilterFromArguments(argc, argv);
    int failures = 0;
    int selected = 0;

    for (const auto& entry : TestRegistry()) {
        if (!filter.empty() && entry.first.find(filter) == std::string::npos) {
            continue;
        }
        ++selected;
        try {
            entry.second();
            std::cout << "PASS " << entry.first << '\n';
        } catch (const std::exception& error) {
            ++failures;
            std::cerr << "FAIL " << entry.first << ": " << error.what() << '\n';
        } catch (...) {
            ++failures;
            std::cerr << "FAIL " << entry.first << ": unknown exception\n";
        }
    }

    if (selected == 0) {
        std::cerr << "FAIL no tests matched filter: " << filter << '\n';
        return 2;
    }
    std::cout << "RESULT " << selected - failures << " passed, " << failures
              << " failed\n";
    return failures == 0 ? 0 : 1;
}
