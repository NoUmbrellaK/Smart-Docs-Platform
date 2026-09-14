#include "timer/heaptimer.h"
#include "../test_support.h"

TEST_CASE(timer_first_insert_and_cancel_are_safe) {
    HeapTimer timer;
    int calls = 0;
    timer.add(1, 10000, [&calls]() { ++calls; });
    CHECK(timer.GetNextTick() >= 0);
    timer.remove(1);
    CHECK(timer.GetNextTick() == -1);
    CHECK(calls == 0);
}

TEST_CASE(timer_callback_can_reuse_its_identifier) {
    HeapTimer timer;
    int calls = 0;
    timer.add(7, 0, [&timer, &calls]() {
        ++calls;
        timer.add(7, 10000, [&calls]() { ++calls; });
    });
    timer.tick();
    CHECK(calls == 1);
    CHECK(timer.GetNextTick() >= 0);
    timer.remove(7);
}
