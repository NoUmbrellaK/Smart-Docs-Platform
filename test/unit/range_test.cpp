#include "core/app_error.h"
#include "file/range.h"
#include "../test_support.h"

#include <cstdint>
#include <limits>
#include <string>

namespace {

void CheckRange(const std::string& header, uint64_t size, uint64_t first,
                uint64_t last) {
    const ByteRange range = ParseSingleRange(header, size);
    CHECK(range.first == first);
    CHECK(range.last == last);
    CHECK(range.length() == last - first + 1);
}

void CheckRangeError(const std::string& header, uint64_t size, int status,
                     const std::string& code) {
    bool caught = false;
    try {
        (void)ParseSingleRange(header, size);
    } catch (const AppError& error) {
        caught = true;
        CHECK(error.http_status == status);
        CHECK(error.code == code);
    }
    CHECK(caught);
}

}  // namespace

TEST_CASE(range_parses_closed_open_and_suffix_forms_with_clamping) {
    CheckRange("bytes=0-9", 100, 0, 9);
    CheckRange("Bytes=0-9", 100, 0, 9);
    CheckRange("bYtEs=10-", 100, 10, 99);
    CheckRange("bytes=10-", 100, 10, 99);
    CheckRange("bytes=-10", 100, 90, 99);
    CheckRange("bytes=0-999", 100, 0, 99);
    CheckRange("bytes=-999", 100, 0, 99);
    CheckRange("bytes=0-18446744073709551615",
               std::numeric_limits<uint64_t>::max(), 0,
               std::numeric_limits<uint64_t>::max() - 1);
}

TEST_CASE(range_rejects_empty_unsatisfiable_overflow_and_malformed_values) {
    const char* invalid[] = {
        "", "items=0-9", "bytes=", "bytes=-", "bytes=9-0",
        "bytes=100-", "bytes=100-200", "bytes=-0",
        "bytes=18446744073709551616-", "bytes=0-18446744073709551616",
        "bytes=-18446744073709551616", " bytes=0-9", "bytes =0-9",
        "bytes= 0-9", "bytes=0 -9", "bytes=0- 9", "bytes=0-9 ",
        "bytes=+0-9", "bytes=00x-9"};
    for (const char* header : invalid) {
        CheckRangeError(header, 100, 416, "range_not_satisfiable");
    }
    CheckRangeError("bytes=0-0", 0, 416, "range_not_satisfiable");
    CheckRangeError("bytes=-1", 0, 416, "range_not_satisfiable");
}

TEST_CASE(range_rejects_every_multi_range_with_not_supported) {
    CheckRangeError("bytes=0-1,2-3", 100, 501, "range_not_supported");
    CheckRangeError("garbage,bytes=0-1", 100, 501,
                    "range_not_supported");
    CheckRangeError(",", 0, 501, "range_not_supported");
}
