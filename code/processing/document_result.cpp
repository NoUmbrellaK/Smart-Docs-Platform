#include "document_result.h"
#include "core/app_error.h"
#include "core/crypto.h"
#include <algorithm>
#include <vector>

namespace {
using Json = nlohmann::json;
[[noreturn]] void Invalid() { throw AppError(400, "invalid_result", "document result is invalid"); }
void Exact(const Json& value, std::initializer_list<const char*> fields) {
    if (!value.is_object() || value.size() != fields.size()) Invalid();
    for (const char* field : fields) if (!value.contains(field)) Invalid();
}
uint64_t Number(const Json& value, uint64_t maximum) {
    if (!value.is_number_integer() || (value.is_number_integer() && !value.is_number_unsigned() && value.get<int64_t>() < 0)) Invalid();
    const auto result = value.get<uint64_t>();
    if (result > maximum) Invalid();
    return result;
}
std::string Hex(const Json& value, size_t length) {
    if (!value.is_string()) Invalid();
    const auto text = value.get<std::string>();
    if (text.size() != length || text.find_first_not_of("0123456789abcdef") != std::string::npos) Invalid();
    return text;
}
bool Space(uint32_t c) {
    return c == 9 || c == 10 || c == 32 || c == 160 || c == 5760 ||
        (c >= 8192 && c <= 8202) || c == 8232 || c == 8233 || c == 8239 || c == 8287 || c == 12288;
}
uint64_t Characters(const std::string& text, bool* nonblank) {
    // JSON's strict encoder validates UTF-8 (including overlong/surrogate sequences).
    (void)Json(text).dump();
    uint64_t count = 0;
    for (size_t i = 0; i < text.size();) {
        uint32_t c = static_cast<unsigned char>(text[i++]);
        int tail = 0;
        if (c >= 0xf0) { c &= 7; tail = 3; }
        else if (c >= 0xe0) { c &= 15; tail = 2; }
        else if (c >= 0xc0) { c &= 31; tail = 1; }
        while (tail--) c = (c << 6) | (static_cast<unsigned char>(text[i++]) & 63);
        if ((c < 32 && c != 9 && c != 10) || (c >= 127 && c <= 159)) Invalid();
        *nonblank = *nonblank || !Space(c); ++count;
    }
    return count;
}
}

ValidatedDocument ValidateDocumentResult(const Json& result) {
    try {
        Exact(result, {"file_id", "version_id", "source_sha256", "parser_revision", "page_count", "pages_without_text", "chunks"});
        ValidatedDocument output;
        output.file_id = Hex(result.at("file_id"), 32);
        output.version_id = Hex(result.at("version_id"), 32);
        output.source_sha256 = Hex(result.at("source_sha256"), 64);
        if (!result.at("parser_revision").is_string()) Invalid();
        output.parser_revision = result.at("parser_revision").get<std::string>();
        const bool pdf = output.parser_revision == "pypdf-6.21.0-lines-v1";
        if (!pdf && output.parser_revision != "utf8-lines-v1") Invalid();
        const uint64_t page_count = Number(result.at("page_count"), 300);
        if (pdf != (page_count != 0)) Invalid();
        const auto& empty = result.at("pages_without_text");
        const auto& chunks = result.at("chunks");
        if (!empty.is_array() || empty.size() > page_count || !chunks.is_array() || chunks.empty() || chunks.size() > 2000) Invalid();
        std::vector<bool> covered(page_count + 1, false);
        uint64_t last = 0;
        for (const auto& value : empty) {
            const auto page = Number(value, page_count);
            if (page <= last) Invalid();
            covered[page] = true; last = page;
        }
        uint64_t previous_page = 0, end = 0, next_line = 1;
        bool page_nonblank = false;
        for (const auto& chunk : chunks) {
            if (pdf) Exact(chunk, {"chunk_id", "file_id", "version_id", "source_sha256", "parser_revision", "text", "char_start", "char_end", "line_start", "line_end", "page"});
            else Exact(chunk, {"chunk_id", "file_id", "version_id", "source_sha256", "parser_revision", "text", "char_start", "char_end", "line_start", "line_end"});
            for (const char* field : {"file_id", "version_id", "source_sha256", "parser_revision"})
                if (chunk.at(field) != result.at(field)) Invalid();
            const auto page = pdf ? Number(chunk.at("page"), page_count) : 0;
            if (pdf && (page == 0 || page < previous_page)) Invalid();
            if (page != previous_page) {
                if ((previous_page && !page_nonblank) || covered[page]) Invalid();
                covered[page] = true; previous_page = page;
                end = 0; next_line = 1; page_nonblank = false;
            }
            if (!chunk.at("text").is_string()) Invalid();
            const auto& text = chunk.at("text").get_ref<const std::string&>();
            if (text.empty() || text.size() > 4800) Invalid();
            const auto count = Characters(text, &page_nonblank);
            if (count > 1200 || (output.char_count += count) > 2000000) Invalid();
            const auto start = Number(chunk.at("char_start"), 2000000);
            const auto stop = Number(chunk.at("char_end"), 2000000);
            const auto lines = std::count(text.begin(), text.end(), '\n');
            if (start != end || stop != start + count ||
                Number(chunk.at("line_start"), 2000001) != next_line ||
                Number(chunk.at("line_end"), 2000001) != next_line + lines - (text.back() == '\n' ? 1 : 0)) Invalid();
            Json identity = Json::array({output.parser_revision, output.file_id, output.version_id, output.source_sha256});
            if (pdf) identity.push_back(page);
            identity.push_back(start); identity.push_back(stop);
            const auto bytes = identity.dump();
            if (Hex(chunk.at("chunk_id"), 64) != Sha256Hex(bytes.data(), bytes.size())) Invalid();
            end = stop; next_line += lines;
        }
        if (!page_nonblank) Invalid();
        for (uint64_t page = 1; page <= page_count; ++page) if (!covered[page]) Invalid();
        const auto canonical = result.dump();
        if (canonical.size() > 16 * 1024 * 1024) Invalid();
        output.fingerprint = Sha256Hex(canonical.data(), canonical.size());
        return output;
    } catch (const Json::exception&) { Invalid(); }
}
