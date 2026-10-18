#pragma once
#include "auth/auth_service.h"
#include <memory>
#include <nlohmann/json.hpp>

class MySqlConnection;
class MySqlPool;
class ProjectService;
class ProcessingService;
class Router;

class AgentTaskService {
public:
    AgentTaskService(MySqlPool& pool, ProjectService& projects) : pool_(pool), projects_(projects) {}
    nlohmann::json Create(const SessionContext&, const std::string& project, const nlohmann::json& body);
    nlohmann::json List(const SessionContext&, const std::string& project, const std::string& before="");
    nlohmann::json Get(const SessionContext&, const std::string& project, const std::string& task);
    nlohmann::json Cancel(const SessionContext&, const std::string& project, const std::string& task);
    nlohmann::json StartRun(const SessionContext&, const std::string& project, const std::string& task);
    nlohmann::json CheckRun(const SessionContext&, const std::string& project, const std::string& task);
    nlohmann::json FinishRun(const SessionContext&, const std::string& project, const std::string& task, const nlohmann::json& body);
    nlohmann::json GetResult(const SessionContext&, const std::string& project, const std::string& task);
    void RecoverInterrupted();
private:
    MySqlPool& pool_;
    ProjectService& projects_;
};

// Caller owns transaction and membership lock. Locks are held through content construction/commit.
void RequireAgentTaskScope(MySqlConnection&, const SessionContext&, const std::string& project,
    const std::string& task, const std::string& file="", const std::string& version="");
void RegisterAgentRoutes(Router&, std::shared_ptr<AuthService>, std::shared_ptr<AgentTaskService>,
    std::shared_ptr<ProcessingService>, bool secure_cookie, uint64_t max_json_bytes);
