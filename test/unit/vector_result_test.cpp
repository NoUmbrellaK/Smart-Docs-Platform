#include "processing/vector_result.h"
#include "core/app_error.h"
#include "core/crypto.h"
#include "../test_support.h"
#include <limits>

namespace {
using Json=nlohmann::json;
Json Sources(size_t count=1) {
    Json chunks=Json::array();
    for (size_t i=0;i<count;++i) chunks.push_back({{"chunk_id",std::string(63,'a')+char('0'+i)},{"text",std::string(64,'x')}});
    return chunks;
}
Json Result(const Json& chunks,size_t windows) {
    Json result={{"embedding_revision",kEmbeddingRevision},{"chunks",Json::array()}};
    for (const auto& source:chunks) {
        const auto text=source.at("text").get<std::string>();
        Json chunk={{"chunk_id",source.at("chunk_id")},{"text_sha256",Sha256Hex(text.data(),text.size())},{"windows",Json::array()}};
        Json vector=std::vector<double>(384,0);vector[0]=1.0;
        for(size_t i=0;i<windows;++i) chunk["windows"].push_back({{"char_start",i*64/windows},{"char_end",(i+1)*64/windows},{"vector",vector}});
        result["chunks"].push_back(chunk);
    }
    return result;
}
}

TEST_CASE(vector_result_rejects_window_budgets_nonfinite_and_reordered_chunks) {
    const auto sources=Sources(2); const auto valid=Result(sources,64);
    CHECK(ValidateVectorResult(valid,sources)==128);
    CHECK_THROWS_CODE(ValidateVectorResult(Result(Sources(3),64),Sources(3)),"invalid_result");
    CHECK_THROWS_CODE(ValidateVectorResult(Result(Sources(),65),Sources()),"invalid_result");
    auto bad=valid; std::swap(bad["chunks"][0],bad["chunks"][1]);
    CHECK_THROWS_CODE(ValidateVectorResult(bad,sources),"invalid_result");
    bad=valid; bad["chunks"][0]["windows"][0]["vector"][0]=std::numeric_limits<double>::quiet_NaN();
    CHECK_THROWS_CODE(ValidateVectorResult(bad,sources),"invalid_result");
    bad=valid; bad["chunks"][0]["windows"][0]["char_start"]=false;
    CHECK_THROWS_CODE(ValidateVectorResult(bad,sources),"invalid_result");
    bad=valid; bad["chunks"][0]["windows"][0]["char_end"]=1.0;
    CHECK_THROWS_CODE(ValidateVectorResult(bad,sources),"invalid_result");
    bad=valid; bad["chunks"][0]["windows"][0]["extra"]=0;
    CHECK_THROWS_CODE(ValidateVectorResult(bad,sources),"invalid_result");
}

TEST_CASE(vector_result_counts_unicode_codepoints_not_bytes) {
    auto sources=Sources();sources[0]["text"]="中🙂e\u0301\n";
    auto result=Result(sources,1); result["chunks"][0]["windows"][0]["char_end"]=5;
    CHECK(ValidateVectorResult(result,sources)==1);
    result["chunks"][0]["windows"][0]["char_end"]=sources[0]["text"].get<std::string>().size();
    CHECK_THROWS_CODE(ValidateVectorResult(result,sources),"invalid_result");
}
