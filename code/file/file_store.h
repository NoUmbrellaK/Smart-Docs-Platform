#pragma once

#include <openssl/evp.h>

#include <cstddef>
#include <cstdint>
#include <ctime>
#include <string>
#include <vector>

struct PartInfo {
    uint32_t part_number;
    uint64_t size;
    std::string sha256;
};

struct StoredTemp {
    std::string task_id;
    uint64_t size;
    std::string sha256;
};

struct PublishedObject {
    std::string content_id;
    uint64_t size;
    std::string sha256;
};

class PartWriter {
public:
    ~PartWriter();
    PartWriter(const PartWriter&) = delete;
    PartWriter& operator=(const PartWriter&) = delete;
    PartWriter(PartWriter&& other) noexcept;
    PartWriter& operator=(PartWriter&& other) noexcept;

    void Write(const char* data, size_t size);
    PartInfo Finish();

private:
    PartWriter(int directory_fd, int file_fd, std::string temporary_name,
               std::string final_name, uint32_t part_number);
    void Cleanup() noexcept;

    int directory_fd_;
    int file_fd_;
    std::string temporary_name_;
    std::string final_name_;
    uint32_t part_number_;
    uint64_t size_;
    EVP_MD_CTX* digest_;
    bool finished_;

    friend class FileStore;
};

class FileStore {
public:
    explicit FileStore(const std::string& root);
    ~FileStore();
    FileStore(const FileStore&) = delete;
    FileStore& operator=(const FileStore&) = delete;

    PartWriter CreatePartWriter(const std::string& task_id,
                                uint32_t part_number);
    StoredTemp Assemble(const std::string& task_id,
                        const std::vector<PartInfo>& parts);
    PublishedObject PublishObject(StoredTemp assembled,
                                  const std::string& content_id);
    int OpenObject(const std::string& content_id) const;
    bool ObjectExists(const std::string& content_id) const;
    std::vector<std::string> ListObjectsOlderThan(std::time_t cutoff) const;
    void RemoveObject(const std::string& content_id);
    void RemoveTaskTemporaryFiles(const std::string& task_id);

private:
    int root_fd_;
    int objects_fd_;
    int staging_fd_;
};
