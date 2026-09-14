#include "fault_injector.h"

#include "app_error.h"

#include <cstdlib>
#include <string>
#include <unistd.h>

namespace {

#ifdef SMARTDOCS_ENABLE_FAULT_INJECTION
const char* Name(FaultPoint point) {
    switch (point) {
    case FaultPoint::AfterPartTempFsync: return "AfterPartTempFsync";
    case FaultPoint::AfterAssembledFsync: return "AfterAssembledFsync";
    case FaultPoint::AfterObjectRename: return "AfterObjectRename";
    case FaultPoint::BeforeDatabaseCommit: return "BeforeDatabaseCommit";
    case FaultPoint::AfterDatabaseCommit: return "AfterDatabaseCommit";
    case FaultPoint::BeforeHttpResponse: return "BeforeHttpResponse";
    }
    return "";
}
#endif

}  // namespace

void FaultInjector::Hit(FaultPoint point) {
#ifdef SMARTDOCS_ENABLE_FAULT_INJECTION
    const char* configured = std::getenv("SMARTDOCS_FAULT_POINT");
    if (configured != nullptr && std::string(configured) == Name(point)) {
        _exit(86);
    }
#else
    (void)point;
#endif
}

void FaultInjector::RejectEnvironmentInNormalBuild() {
#ifndef SMARTDOCS_ENABLE_FAULT_INJECTION
    const char* configured = std::getenv("SMARTDOCS_FAULT_POINT");
    if (configured != nullptr && *configured != '\0') {
        throw AppError(500, "fault_injection_forbidden",
                       "SMARTDOCS_FAULT_POINT is only allowed in the test server");
    }
#endif
}
