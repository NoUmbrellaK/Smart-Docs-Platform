#include "core/app_error.h"
#include "upload/upload_service.h"
#include "../test_support.h"

#include <cstdint>
#include <limits>
#include <string>

TEST_CASE(upload_validation_calculates_zero_and_final_part_layout) {
    CHECK(UploadPartCount(0, 256) == 0);
    CHECK(UploadPartCount(1, 256) == 1);
    CHECK(UploadPartCount(256, 256) == 1);
    CHECK(UploadPartCount(257, 256) == 2);
    CHECK(ExpectedUploadPartSize(257, 256, 0) == 256);
    CHECK(ExpectedUploadPartSize(257, 256, 1) == 1);
}

TEST_CASE(upload_validation_rejects_invalid_part_layout) {
    CHECK_THROWS_CODE(UploadPartCount(1, 0), "invalid_configuration");
    CHECK_THROWS_CODE(UploadPartCount(1000001, 1), "too_many_parts");
    CHECK_THROWS_CODE(ExpectedUploadPartSize(0, 256, 0),
                      "invalid_part_number");
    CHECK_THROWS_CODE(ExpectedUploadPartSize(257, 256, 2),
                      "invalid_part_number");
    CHECK_THROWS_CODE(
        ExpectedUploadPartSize(std::numeric_limits<uint64_t>::max(), 1,
                               999999),
        "too_many_parts");
}

TEST_CASE(upload_validation_rejects_invalid_digests_and_media_types) {
    const std::string digest(64, 'a');
    ValidateUploadSha256(digest);
    CHECK(ValidateUploadMediaType("application/pdf") == "application/pdf");
    CHECK(ValidateUploadMediaType("text/plain") == "text/plain");

    CHECK_THROWS_CODE(ValidateUploadSha256(std::string(63, 'a')),
                      "invalid_request");
    CHECK_THROWS_CODE(ValidateUploadSha256(std::string(64, 'A')),
                      "invalid_request");
    CHECK_THROWS_CODE(ValidateUploadMediaType("text /plain"),
                      "invalid_request");
    CHECK_THROWS_CODE(ValidateUploadMediaType("text/plain; charset=utf-8"),
                      "invalid_request");
    CHECK_THROWS_CODE(ValidateUploadMediaType(std::string("text/\0plain", 10)),
                      "invalid_request");
}
