#pragma once

#include "http/router.h"

#include <memory>

class AuthService;
class FileService;

void RegisterFileRoutes(Router& router,
                        const std::shared_ptr<AuthService>& auth,
                        const std::shared_ptr<FileService>& files);
