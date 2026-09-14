#pragma once

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
};
