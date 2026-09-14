#include "range.h"

#include "core/app_error.h"

#include <cctype>
#include <limits>

namespace {

[[noreturn]] void InvalidRange() {
    throw AppError(416, "range_not_satisfiable",
                   "requested byte range is not satisfiable");
}

uint64_t Decimal(const std::string& value) {
    if (value.empty()) InvalidRange();
    uint64_t result = 0;
    for (unsigned char ch : value) {
        if (!std::isdigit(ch)) InvalidRange();
        const uint64_t digit = static_cast<uint64_t>(ch - '0');
        if (result > (std::numeric_limits<uint64_t>::max() - digit) / 10) {
            InvalidRange();
        }
        result = result * 10 + digit;
    }
    return result;
}

bool HasByteUnit(const std::string& header) {
    static const char unit[] = "bytes";
    if (header.size() < 6 || header[5] != '=') return false;
    for (size_t index = 0; index < 5; ++index) {
        unsigned char ch = static_cast<unsigned char>(header[index]);
        if (ch >= 'A' && ch <= 'Z') ch = static_cast<unsigned char>(ch + 32);
        if (ch != static_cast<unsigned char>(unit[index])) return false;
    }
    return true;
}

}  // namespace

ByteRange ParseSingleRange(const std::string& header, uint64_t file_size) {
    if (header.find(',') != std::string::npos) {
        throw AppError(501, "range_not_supported",
                       "multiple byte ranges are not supported");
    }
    const size_t prefix_size = 6;
    if (!HasByteUnit(header) || header.size() == prefix_size ||
        header.find_first_of(" \t\r\n") != std::string::npos ||
        file_size == 0) {
        InvalidRange();
    }
    const std::string value = header.substr(prefix_size);
    const size_t dash = value.find('-');
    if (dash == std::string::npos ||
        value.find('-', dash + 1) != std::string::npos) {
        InvalidRange();
    }
    const std::string first_text = value.substr(0, dash);
    const std::string last_text = value.substr(dash + 1);
    if (first_text.empty()) {
        const uint64_t suffix = Decimal(last_text);
        if (suffix == 0) InvalidRange();
        const uint64_t length = suffix < file_size ? suffix : file_size;
        return ByteRange{file_size - length, file_size - 1};
    }

    const uint64_t first = Decimal(first_text);
    if (first >= file_size) InvalidRange();
    if (last_text.empty()) return ByteRange{first, file_size - 1};
    uint64_t last = Decimal(last_text);
    if (last < first) InvalidRange();
    if (last >= file_size) last = file_size - 1;
    return ByteRange{first, last};
}
