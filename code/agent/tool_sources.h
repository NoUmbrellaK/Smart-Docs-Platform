#pragma once
#include <nlohmann/json.hpp>

// Only C++-constructed tool data, never model parameters or document-embedded JSON.
nlohmann::json ExtractToolSources(const nlohmann::json& data);
