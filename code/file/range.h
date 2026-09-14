#pragma once

#include <cstdint>
#include <string>

struct ByteRange {
    uint64_t first;
    uint64_t last;
    uint64_t length() const { return last - first + 1; }
};

ByteRange ParseSingleRange(const std::string& header, uint64_t file_size);
