/*
 * @Author       : mark
 * @Date         : 2020-06-15
 * @copyleft Apache 2.0
 * Modified for Smart Docs Platform, 2026.
 */
#pragma once

#include <cassert>
#include <condition_variable>
#include <cstddef>
#include <functional>
#include <memory>
#include <mutex>
#include <queue>
#include <stdexcept>
#include <thread>
#include <utility>
#include <vector>

class ThreadPool {
public:
    explicit ThreadPool(size_t thread_count = 8)
        : pool_(std::make_shared<Pool>()) {
        if (thread_count == 0) {
            throw std::invalid_argument("thread pool requires at least one worker");
        }
        workers_.reserve(thread_count);
        for (size_t i = 0; i < thread_count; ++i) {
            std::shared_ptr<Pool> pool = pool_;
            workers_.emplace_back([pool]() {
                while (true) {
                    std::function<void()> task;
                    {
                        std::unique_lock<std::mutex> lock(pool->mutex);
                        pool->condition.wait(lock, [pool]() {
                            return pool->closed || !pool->tasks.empty();
                        });
                        if (pool->closed && pool->tasks.empty()) {
                            return;
                        }
                        task = std::move(pool->tasks.front());
                        pool->tasks.pop();
                    }
                    task();
                }
            });
        }
    }

    ThreadPool(const ThreadPool&) = delete;
    ThreadPool& operator=(const ThreadPool&) = delete;
    ThreadPool(ThreadPool&&) = delete;
    ThreadPool& operator=(ThreadPool&&) = delete;

    ~ThreadPool() {
        Stop();
    }

    template<class Function>
    void AddTask(Function&& task) {
        {
            std::lock_guard<std::mutex> lock(pool_->mutex);
            if (pool_->closed) {
                throw std::logic_error("cannot add a task to a stopped thread pool");
            }
            pool_->tasks.emplace(std::forward<Function>(task));
        }
        pool_->condition.notify_one();
    }

    void Stop() {
        {
            std::lock_guard<std::mutex> lock(pool_->mutex);
            pool_->closed = true;
        }
        pool_->condition.notify_all();
        for (std::thread& worker : workers_) {
            if (worker.joinable()) {
                worker.join();
            }
        }
        workers_.clear();
    }

private:
    struct Pool {
        std::mutex mutex;
        std::condition_variable condition;
        bool closed = false;
        std::queue<std::function<void()>> tasks;
    };

    std::shared_ptr<Pool> pool_;
    std::vector<std::thread> workers_;
};
