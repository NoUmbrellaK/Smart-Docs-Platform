#pragma once
#include <memory>
#include <string>
class Router;
class ProcessingService;
class FileStore;
void RegisterWorkerRoutes(Router&, std::shared_ptr<ProcessingService>,
                          std::shared_ptr<FileStore>, const std::string& token);
