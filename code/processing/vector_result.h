#pragma once
#include <cstdint>
#include <nlohmann/json.hpp>

constexpr const char* kEmbeddingRevision = "minilm-634d0f66-fastembed0.9.0-ort1.23.2-halves128-l2-v1";
// Validates against authoritative [{chunk_id,text}]; returns total windows.
// This validates the worker contract, not the semantic quality of its vectors.
uint64_t ValidateVectorResult(const nlohmann::json& result, const nlohmann::json& chunks);
