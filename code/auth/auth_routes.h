#pragma once

#include "http/router.h"

#include <cstdint>
#include <memory>
#include <string>

class AuthService;
class ProjectService;

void RegisterAuthRoutes(Router& router,
                        const std::shared_ptr<AuthService>& auth,
                        const std::shared_ptr<ProjectService>& projects,
                        uint64_t maximum_json_bytes, bool secure_cookie,
                        int session_seconds);
std::string RequireSessionCookie(const RequestHead& head);
void RequireSameOrigin(const RequestHead& head, bool secure_cookie);
