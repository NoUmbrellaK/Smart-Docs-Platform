#include "tool_sources.h"
#include "core/app_error.h"
#include <set>
#include <tuple>

namespace {
using Json = nlohmann::json;
std::string Id(const Json& value, size_t size) {
    if (!value.is_string() || value.get_ref<const std::string&>().size()!=size ||
        value.get_ref<const std::string&>().find_first_not_of("0123456789abcdef")!=std::string::npos)
        throw AppError(503,"agent_unavailable","tool source could not be recorded");
    return value.get<std::string>();
}
}

nlohmann::json ExtractToolSources(const Json& data) {
    std::set<std::pair<std::string,std::string>> versions;
    std::set<std::tuple<std::string,std::string,std::string>> citations;
    const auto add = [&](const Json& item, bool chunk) {
        const auto file=Id(item.at("file_id"),32), version=Id(item.at("version_id"),32);
        versions.emplace(file,version);
        if (chunk) citations.emplace(file,version,Id(item.at("chunk_id"),64));
    };
    if (data.contains("file_id")) add(data,false);
    if (data.contains("items")) for (const auto& item:data.at("items")) {
        add(item,false);
        if (item.contains("chunk")) add(item.at("chunk"),true);
    }
    if (data.contains("chunk")) add(data.at("chunk"),true);
    if (data.contains("chunks")) for (const auto& item:data.at("chunks")) add(item,true);
    Json result={{"versions",Json::array()},{"citations",Json::array()}};
    for (const auto& item:versions) result["versions"].push_back({{"file_id",item.first},{"version_id",item.second}});
    for (const auto& item:citations) result["citations"].push_back({{"file_id",std::get<0>(item)},
        {"version_id",std::get<1>(item)},{"chunk_id",std::get<2>(item)}});
    return result;
}
