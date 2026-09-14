#include "id.h"

#include "crypto.h"

std::string GenerateId() {
    return GenerateTokenHex(16);
}
