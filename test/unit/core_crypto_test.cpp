#include "core/crypto.h"
#include "core/id.h"
#include "../test_support.h"

#include <algorithm>
#include <string>

TEST_CASE(ids_are_lower_hex_and_unique) {
    const std::string first = GenerateId();
    const std::string second = GenerateId();
    CHECK(first.size() == 32);
    CHECK(first.find_first_not_of("0123456789abcdef") == std::string::npos);
    CHECK(first != second);
}

TEST_CASE(tokens_have_requested_random_byte_length) {
    const std::string token = GenerateTokenHex(32);
    CHECK(token.size() == 64);
    CHECK(token.find_first_not_of("0123456789abcdef") == std::string::npos);
}

TEST_CASE(sha256_matches_known_vector) {
    const std::string input = "abc";
    CHECK(Sha256Hex(input.data(), input.size()) ==
          "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
}

TEST_CASE(password_verification_rejects_wrong_password) {
    const PasswordHash stored = DerivePassword("correct horse", 1000);
    CHECK(stored.salt.size() == 16);
    CHECK(stored.digest.size() == 32);
    CHECK(stored.iterations == 1000);
    CHECK(VerifyPassword("correct horse", stored));
    CHECK(!VerifyPassword("wrong battery", stored));
}
