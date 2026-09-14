#include "crypto.h"

#include "app_error.h"

#include <cerrno>
#include <climits>
#include <cstring>
#include <limits>

#include <openssl/crypto.h>
#include <openssl/evp.h>
#include <sys/random.h>

namespace {

std::vector<unsigned char> RandomBytes(size_t size) {
    std::vector<unsigned char> bytes(size);
    size_t offset = 0;
    while (offset < bytes.size()) {
        const ssize_t count = getrandom(bytes.data() + offset, bytes.size() - offset, 0);
        if (count < 0 && errno == EINTR) {
            continue;
        }
        if (count <= 0) {
            throw AppError(500, "random_failed", "Secure random generation failed");
        }
        offset += static_cast<size_t>(count);
    }
    return bytes;
}

std::string Hex(const unsigned char* bytes, size_t size) {
    static const char digits[] = "0123456789abcdef";
    std::string result(size * 2, '0');
    for (size_t index = 0; index < size; ++index) {
        result[index * 2] = digits[bytes[index] >> 4];
        result[index * 2 + 1] = digits[bytes[index] & 0x0f];
    }
    return result;
}

}  // namespace

std::array<unsigned char, 32> Sha256(const void* data, size_t size) {
    std::array<unsigned char, 32> digest{};
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (context == nullptr) {
        throw AppError(500, "crypto_failed", "Unable to allocate digest context");
    }

    unsigned int digest_size = 0;
    const bool ok = EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1 &&
                    EVP_DigestUpdate(context, data, size) == 1 &&
                    EVP_DigestFinal_ex(context, digest.data(), &digest_size) == 1;
    EVP_MD_CTX_free(context);
    if (!ok || digest_size != digest.size()) {
        throw AppError(500, "crypto_failed", "SHA-256 calculation failed");
    }
    return digest;
}

std::string Sha256Hex(const void* data, size_t size) {
    const std::array<unsigned char, 32> digest = Sha256(data, size);
    return Hex(digest.data(), digest.size());
}

std::string GenerateTokenHex(size_t bytes) {
    const std::vector<unsigned char> random = RandomBytes(bytes);
    return Hex(random.data(), random.size());
}

PasswordHash DerivePassword(const std::string& password, int iterations) {
    if (iterations <= 0 || password.size() > static_cast<size_t>(INT_MAX)) {
        throw AppError(500, "crypto_invalid", "Invalid password derivation parameters");
    }
    PasswordHash result;
    result.salt = RandomBytes(16);
    result.digest.resize(32);
    result.iterations = iterations;
    if (PKCS5_PBKDF2_HMAC(password.data(), static_cast<int>(password.size()),
                          result.salt.data(), static_cast<int>(result.salt.size()),
                          iterations, EVP_sha256(), static_cast<int>(result.digest.size()),
                          result.digest.data()) != 1) {
        throw AppError(500, "crypto_failed", "Password derivation failed");
    }
    return result;
}

bool VerifyPassword(const std::string& password, const PasswordHash& stored) {
    if (stored.iterations <= 0 || stored.salt.empty() || stored.digest.size() != 32 ||
        password.size() > static_cast<size_t>(INT_MAX)) {
        return false;
    }
    std::vector<unsigned char> candidate(stored.digest.size());
    if (PKCS5_PBKDF2_HMAC(password.data(), static_cast<int>(password.size()),
                          stored.salt.data(), static_cast<int>(stored.salt.size()),
                          stored.iterations, EVP_sha256(), static_cast<int>(candidate.size()),
                          candidate.data()) != 1) {
        throw AppError(500, "crypto_failed", "Password verification failed");
    }
    return CRYPTO_memcmp(candidate.data(), stored.digest.data(), candidate.size()) == 0;
}
