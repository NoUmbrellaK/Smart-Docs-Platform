#pragma once

#include <array>
#include <cstddef>
#include <string>
#include <vector>

struct PasswordHash {
    std::vector<unsigned char> salt;
    std::vector<unsigned char> digest;
    int iterations;
};

std::array<unsigned char, 32> Sha256(const void* data, size_t size);
std::string Sha256Hex(const void* data, size_t size);
std::string GenerateTokenHex(size_t bytes);
PasswordHash DerivePassword(const std::string& password, int iterations);
bool VerifyPassword(const std::string& password, const PasswordHash& stored);
