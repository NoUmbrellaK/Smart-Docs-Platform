#pragma once

#include "http/router.h"

#include <cstdint>
#include <memory>

class AuthService;
class ProjectService;

void RegisterProjectRoutes(Router& router,
                           const std::shared_ptr<AuthService>& auth,
                           const std::shared_ptr<ProjectService>& projects,
                           uint64_t maximum_json_bytes, bool secure_cookie);
