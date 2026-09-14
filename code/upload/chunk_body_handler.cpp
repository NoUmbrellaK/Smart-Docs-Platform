#include "chunk_body_handler.h"

#include "core/app_error.h"
#include "file/file_store.h"
#include "http/httpresponse.h"
#include "upload_service.h"

#include <nlohmann/json.hpp>
#include <openssl/evp.h>
#include <utility>

namespace {

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

ChunkBodyHandler::ChunkBodyHandler(
    UploadService& uploads, SessionContext session, std::string project_id,
    std::string task_id, uint32_t part_number, uint64_t expected_size,
    std::string expected_sha256, std::unique_ptr<PartWriter> writer,
    std::string request_id, bool keep_alive)
    : uploads_(uploads),
      session_(std::move(session)),
      project_id_(std::move(project_id)),
      task_id_(std::move(task_id)),
      part_number_(part_number),
      expected_size_(expected_size),
      expected_sha256_(std::move(expected_sha256)),
      writer_(std::move(writer)),
      request_id_(std::move(request_id)),
      keep_alive_(keep_alive),
      received_(0),
      digest_(EVP_MD_CTX_new()),
      finished_(false) {
    if (!writer_ || digest_ == nullptr ||
        EVP_DigestInit_ex(digest_, EVP_sha256(), nullptr) != 1) {
        if (digest_ != nullptr) EVP_MD_CTX_free(digest_);
        digest_ = nullptr;
        throw AppError(500, "crypto_failed", "unable to initialize SHA-256");
    }
}

ChunkBodyHandler::~ChunkBodyHandler() {
    if (digest_ != nullptr) EVP_MD_CTX_free(digest_);
}

void ChunkBodyHandler::OnData(const char* data, size_t size) {
    if (finished_ || (data == nullptr && size != 0) ||
        size > expected_size_ - received_) {
        throw AppError(400, "invalid_request",
                       "chunk body exceeds its declared length");
    }
    if (size != 0 && EVP_DigestUpdate(digest_, data, size) != 1) {
        throw AppError(500, "crypto_failed", "SHA-256 update failed");
    }
    writer_->Write(data, size);
    received_ += size;
}

HttpResponse ChunkBodyHandler::Finish() {
    if (finished_) {
        throw AppError(400, "invalid_request",
                       "chunk request was already completed");
    }
    if (received_ != expected_size_) {
        throw AppError(400, "invalid_request",
                       "chunk body is shorter than Content-Length");
    }
    unsigned char bytes[EVP_MAX_MD_SIZE];
    unsigned int size = 0;
    if (EVP_DigestFinal_ex(digest_, bytes, &size) != 1 || size != 32) {
        throw AppError(500, "crypto_failed", "SHA-256 finalization failed");
    }
    EVP_MD_CTX_free(digest_);
    digest_ = nullptr;
    finished_ = true;
    if (Hex(bytes, size) != expected_sha256_) {
        throw AppError(422, "chunk_digest_mismatch",
                       "chunk SHA-256 does not match the request header");
    }

    PartInfo part;
    try {
        part = writer_->Finish();
    } catch (const AppError& error) {
        if (error.code == "storage_conflict") {
            throw AppError(409, "chunk_conflict",
                           "this part number already has different content");
        }
        throw;
    }
    const PartResult result =
        uploads_.ConfirmPart(session_, project_id_, task_id_, part,
                             request_id_);
    return HttpResponse::Json(
        200,
        {{"data", {{"part_number", result.part_number},
                   {"size", result.size},
                   {"sha256", result.sha256},
                   {"reused", result.reused}}},
         {"request_id", request_id_}},
        keep_alive_);
}
