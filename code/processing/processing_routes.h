#pragma once
#include <memory>
#include <cstdint>
class Router;
class AuthService;
class ProcessingService;
void RegisterProcessingRoutes(Router&, std::shared_ptr<AuthService>,
                               std::shared_ptr<ProcessingService>, bool secure_cookie,
                               uint64_t max_json_bytes);
