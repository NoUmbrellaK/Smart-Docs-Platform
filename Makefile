CXX ?= g++
CPPFLAGS := -I. -Icode -Ithird_party
CXXFLAGS := -std=c++14 -O2 -g -Wall -Wextra -Wpedantic -MMD -MP
LDLIBS := -pthread -lmysqlclient -lcrypto

APP_SOURCES := $(filter-out code/main.cpp code/admin/main.cpp,$(shell find code -name '*.cpp' -print))
SERVER_SOURCES := $(APP_SOURCES) code/main.cpp
TEST_SOURCES := test/test_main.cpp test/mysql_test_support.cpp \
                $(shell find test/unit test/integration -name '*_test.cpp' -print 2>/dev/null)

SERVER_OBJECTS := $(patsubst %.cpp,build/obj/server/%.o,$(SERVER_SOURCES))
TEST_APP_OBJECTS := $(patsubst %.cpp,build/obj/tests/%.o,$(APP_SOURCES))
TEST_OBJECTS := $(patsubst %.cpp,build/obj/tests/%.o,$(TEST_SOURCES))
TEST_SERVER_OBJECTS := $(patsubst %.cpp,build/obj/test-server/%.o,$(SERVER_SOURCES))
DEPENDENCY_FILES := $(SERVER_OBJECTS:.o=.d) $(TEST_APP_OBJECTS:.o=.d) \
                    $(TEST_OBJECTS:.o=.d) $(TEST_SERVER_OBJECTS:.o=.d)

.PHONY: all server admin test test-server clean

all: server

server: bin/server

bin/server: $(SERVER_OBJECTS)
	@mkdir -p $(@D)
	$(CXX) $(CXXFLAGS) $^ -o $@ $(LDLIBS)

test: bin/smartdocs_tests
	@./bin/smartdocs_tests $(if $(TEST_FILTER),--filter=$(TEST_FILTER),)

bin/smartdocs_tests: $(TEST_APP_OBJECTS) $(TEST_OBJECTS)
	@mkdir -p $(@D)
	$(CXX) $(CXXFLAGS) $^ -o $@ $(LDLIBS)

test-server: bin/smartdocs_test_server

bin/smartdocs_test_server: $(TEST_SERVER_OBJECTS)
	@mkdir -p $(@D)
	$(CXX) $(CXXFLAGS) $^ -o $@ $(LDLIBS)

build/obj/server/%.o: %.cpp
	@mkdir -p $(@D)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -c $< -o $@

build/obj/tests/%.o: %.cpp
	@mkdir -p $(@D)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -c $< -o $@

build/obj/test-server/%.o: %.cpp
	@mkdir -p $(@D)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -DSMARTDOCS_ENABLE_FAULT_INJECTION=1 -c $< -o $@

ifeq ($(wildcard code/admin/main.cpp),)
admin:
	@echo "admin target not available before Task 5" >&2
	@exit 1
else
ADMIN_SOURCES := $(APP_SOURCES) code/admin/main.cpp
ADMIN_OBJECTS := $(patsubst %.cpp,build/obj/admin/%.o,$(ADMIN_SOURCES))
DEPENDENCY_FILES += $(ADMIN_OBJECTS:.o=.d)

admin: bin/smartdocs-admin

bin/smartdocs-admin: $(ADMIN_OBJECTS)
	@mkdir -p $(@D)
	$(CXX) $(CXXFLAGS) $^ -o $@ $(LDLIBS)

build/obj/admin/%.o: %.cpp
	@mkdir -p $(@D)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -c $< -o $@
endif

clean:
	$(RM) -r build/obj bin/server bin/smartdocs-admin bin/smartdocs_tests bin/smartdocs_test_server

-include $(DEPENDENCY_FILES)
