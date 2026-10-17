#pragma once
#include <memory>
class Router;
class AuthService;
class ProcessingService;
void RegisterProcessingRoutes(Router&, std::shared_ptr<AuthService>,
                               std::shared_ptr<ProcessingService>, bool secure_cookie);
