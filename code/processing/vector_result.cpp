#include "vector_result.h"
#include "core/app_error.h"
#include "core/crypto.h"
#include <algorithm>
#include <cmath>

namespace {
using Json = nlohmann::json;
[[noreturn]] void Invalid() { throw AppError(400,"invalid_result","vector result is invalid"); }
void Exact(const Json& value, std::initializer_list<const char*> fields) {
    if (!value.is_object() || value.size()!=fields.size()) Invalid();
    for (const char* field : fields) if (!value.contains(field)) Invalid();
}
uint64_t Offset(const Json& value) {
    if (!value.is_number_integer() || (!value.is_number_unsigned() && value.get<int64_t>()<0)) Invalid();
    const auto number = value.get<uint64_t>();
    if (number>1200) Invalid();
    return number;
}
}

uint64_t ValidateVectorResult(const Json& result, const Json& chunks) {
    try {
        if (result.dump().size()>2*1024*1024) Invalid();
        Exact(result,{"embedding_revision","chunks"});
        if (result.at("embedding_revision")!=kEmbeddingRevision || !result.at("chunks").is_array() ||
            chunks.empty() || chunks.size()>8 || result.at("chunks").size()!=chunks.size()) Invalid();
        uint64_t total = 0;
        for (size_t i=0; i<chunks.size(); ++i) {
            const auto& source = chunks[i]; const auto& chunk = result.at("chunks")[i];
            Exact(chunk,{"chunk_id","text_sha256","windows"});
            const auto text = source.at("text").get<std::string>();
            const auto length = static_cast<uint64_t>(std::count_if(text.begin(),text.end(),
                [](unsigned char c) { return (c & 0xc0)!=0x80; }));
            if (chunk.at("chunk_id")!=source.at("chunk_id") ||
                chunk.at("text_sha256")!=Sha256Hex(text.data(),text.size())) Invalid();
            const auto& windows = chunk.at("windows");
            if (!windows.is_array() || windows.empty() || windows.size()>64 || (total+=windows.size())>128) Invalid();
            uint64_t cursor = 0;
            for (const auto& window : windows) {
                Exact(window,{"char_start","char_end","vector"});
                const auto start = Offset(window.at("char_start")), end = Offset(window.at("char_end"));
                const auto& vector = window.at("vector");
                if (start!=cursor || end<=start || end>length || !vector.is_array() || vector.size()!=384) Invalid();
                double norm = 0;
                for (const auto& coordinate : vector) {
                    if (!coordinate.is_number()) Invalid();
                    const auto value = coordinate.get<double>();
                    if (!std::isfinite(value) || std::abs(value)>1.00001) Invalid();
                    norm += value*value;
                }
                if (std::abs(norm-1.0)>1e-5) Invalid();
                cursor = end;
            }
            if (cursor!=length) Invalid();
        }
        return total;
    } catch (const Json::exception&) { Invalid(); }
}
