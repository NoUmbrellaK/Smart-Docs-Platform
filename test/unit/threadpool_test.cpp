#include "pool/threadpool.h"
#include "../test_support.h"

#include <atomic>
#include <chrono>
#include <thread>

TEST_CASE(threadpool_destruction_waits_for_accepted_tasks) {
    std::atomic<bool> completed{false};
    {
        ThreadPool pool(1);
        pool.AddTask([&completed]() {
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
            completed.store(true);
        });
    }
    CHECK(completed.load());
}
