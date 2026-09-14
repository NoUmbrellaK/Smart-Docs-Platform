#pragma once

#include "http/router.h"

#include <cstdint>
#include <memory>

class AuthService;
class FileService;

void RegisterFileRoutes(Router& router,
                        const std::shared_ptr<AuthService>& auth,
                        const std::shared_ptr<FileService>& files,
                        uint64_t maximum_json_bytes, bool secure_cookie);
