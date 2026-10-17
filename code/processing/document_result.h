#pragma once
#include <cstdint>
#include <string>
#include <nlohmann/json.hpp>

struct ValidatedDocument {
    std::string file_id, version_id, source_sha256, parser_revision, fingerprint;
    uint64_t char_count = 0;
};

// Local parser output only. Does not authorize a caller or prove text extraction.
ValidatedDocument ValidateDocumentResult(const nlohmann::json& result);
