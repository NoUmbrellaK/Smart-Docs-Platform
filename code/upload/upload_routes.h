#pragma once

#include "http/router.h"

#include <cstdint>
#include <memory>

class AuthService;
class UploadService;

void RegisterUploadRoutes(Router& router,
                          const std::shared_ptr<AuthService>& auth,
                          const std::shared_ptr<UploadService>& uploads,
                          uint64_t maximum_json_bytes, bool secure_cookie);
