#pragma once

#ifdef SMARTDOCS_ENABLE_FAULT_OBSERVER
#include <functional>
#endif

enum class FaultPoint {
    AfterPartTempFsync,
    AfterAssembledFsync,
    AfterObjectRename,
    BeforeDatabaseCommit,
    AfterDatabaseCommit,
    BeforeHttpResponse
};

class FaultInjector {
public:
    static void Hit(FaultPoint point);
    static void RejectEnvironmentInNormalBuild();
#ifdef SMARTDOCS_ENABLE_FAULT_OBSERVER
    static void SetObserverForTesting(std::function<void(FaultPoint)> observer);
    static void ClearObserverForTesting();
#endif
};
