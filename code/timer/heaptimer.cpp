/*
 * @Author       : mark
 * @Date         : 2020-06-17
 * @copyleft Apache 2.0
 */ 
#include "heaptimer.h"

#include <climits>

void HeapTimer::siftup_(size_t i) {
    assert(i < heap_.size());
    while(i > 0) {
        size_t j = (i - 1) / 2;
        if(heap_[j] < heap_[i]) { break; }
        SwapNode_(i, j);
        i = j;
    }
}

void HeapTimer::SwapNode_(size_t i, size_t j) {
    assert(i < heap_.size());
    assert(j < heap_.size());
    std::swap(heap_[i], heap_[j]);
    ref_[heap_[i].id] = i;
    ref_[heap_[j].id] = j;
} 

bool HeapTimer::siftdown_(size_t index, size_t n) {
    assert(index < heap_.size());
    assert(n <= heap_.size());
    size_t i = index;
    size_t j = i * 2 + 1;
    while(j < n) {
        if(j + 1 < n && heap_[j + 1] < heap_[j]) j++;
        if(heap_[i] < heap_[j]) break;
        SwapNode_(i, j);
        i = j;
        j = i * 2 + 1;
    }
    return i > index;
}

void HeapTimer::add(int id, int timeout, const TimeoutCallBack& cb) {
    assert(id >= 0);
    std::lock_guard<std::mutex> lock(mutex_);
    size_t i;
    if(ref_.count(id) == 0) {
        /* 新节点：堆尾插入，调整堆 */
        i = heap_.size();
        ref_[id] = i;
        heap_.push_back({id, Clock::now() + MS(timeout), cb});
        siftup_(i);
    } 
    else {
        /* 已有结点：调整堆 */
        i = ref_[id];
        heap_[i].expires = Clock::now() + MS(timeout);
        heap_[i].cb = cb;
        if(!siftdown_(i, heap_.size())) {
            siftup_(i);
        }
    }
}

void HeapTimer::doWork(int id) {
    /* 删除指定id结点，并触发回调函数 */
    TimeoutCallBack callback;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if(heap_.empty() || ref_.count(id) == 0) {
            return;
        }
        size_t i = ref_[id];
        callback = heap_[i].cb;
        del_(i);
    }
    callback();
}

void HeapTimer::remove(int id) {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto found = ref_.find(id);
    if (found != ref_.end()) {
        del_(found->second);
    }
}

void HeapTimer::del_(size_t index) {
    /* 删除指定位置的结点 */
    assert(!heap_.empty() && index < heap_.size());
    /* 将要删除的结点换到队尾，然后调整堆 */
    size_t i = index;
    size_t n = heap_.size() - 1;
    assert(i <= n);
    if(i < n) {
        SwapNode_(i, n);
        if(!siftdown_(i, n)) {
            siftup_(i);
        }
    }
    /* 队尾元素删除 */
    ref_.erase(heap_.back().id);
    heap_.pop_back();
}

void HeapTimer::adjust(int id, int timeout) {
    /* 调整指定id的结点 */
    std::lock_guard<std::mutex> lock(mutex_);
    const auto found = ref_.find(id);
    if (found == ref_.end()) {
        return;
    }
    heap_[found->second].expires = Clock::now() + MS(timeout);
    if (!siftdown_(found->second, heap_.size())) {
        siftup_(found->second);
    }
}

void HeapTimer::tick() {
    /* 清除超时结点 */
    while(true) {
        TimeoutCallBack callback;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if(heap_.empty()) {
                return;
            }
            TimerNode node = heap_.front();
            if(std::chrono::duration_cast<MS>(node.expires - Clock::now()).count() > 0) {
                return;
            }
            callback = node.cb;
            del_(0);
        }
        callback();
    }
}

void HeapTimer::pop() {
    std::lock_guard<std::mutex> lock(mutex_);
    assert(!heap_.empty());
    del_(0);
}

void HeapTimer::clear() {
    std::lock_guard<std::mutex> lock(mutex_);
    ref_.clear();
    heap_.clear();
}

int HeapTimer::GetNextTick() {
    tick();
    std::lock_guard<std::mutex> lock(mutex_);
    int res = -1;
    if(!heap_.empty()) {
        const auto remaining = std::chrono::duration_cast<MS>(
            heap_.front().expires - Clock::now()).count();
        res = remaining > INT_MAX
            ? INT_MAX : static_cast<int>(remaining);
        if(res < 0) { res = 0; }
    }
    return res;
}
