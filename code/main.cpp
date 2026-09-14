#include "app/application.h"
#include "app/config.h"
#include "core/app_error.h"
#include "server/webserver.h"

#include <csignal>
#include <exception>
#include <iostream>
#include <memory>

int main() {
    std::signal(SIGPIPE, SIG_IGN);
    try {
        const AppConfig config = AppConfig::LoadFromEnvironment();
        std::shared_ptr<Application> application(new Application());
        WebServer server(config, application);
        server.Start();
        return 0;
    } catch (const AppError& error) {
        std::cerr << error.code << ": " << error.message << '\n';
    } catch (const std::exception&) {
        std::cerr << "internal_error: server startup failed\n";
    }
    return 1;
}
