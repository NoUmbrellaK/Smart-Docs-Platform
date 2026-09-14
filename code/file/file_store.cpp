#include "file_store.h"

#include "core/app_error.h"
#include "core/fault_injector.h"
#include "core/id.h"

#include <cerrno>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <limits>
#include <sys/stat.h>
#include <unistd.h>
#include <utility>

namespace {

[[noreturn]] void StorageUnavailable() {
    throw AppError(503, "storage_unavailable",
                   "controlled storage is unavailable", true);
}

[[noreturn]] void IntegrityFailed() {
    throw AppError(409, "storage_integrity_failed",
                   "stored content failed integrity validation");
}

[[noreturn]] void StorageConflict() {
    throw AppError(409, "storage_conflict",
                   "immutable storage name already contains other content");
}

bool IsLowerHex(const std::string& value, size_t size) {
    return value.size() == size &&
           value.find_first_not_of("0123456789abcdef") == std::string::npos;
}

void RequireId(const std::string& value) {
    if (!IsLowerHex(value, 32)) {
        throw AppError(400, "invalid_request", "storage ID is invalid");
    }
}

void RequireDigest(const std::string& value) {
    if (!IsLowerHex(value, 64)) {
        IntegrityFailed();
    }
}

bool IsSecureDirectory(int fd) {
    struct stat entry{};
    return fstat(fd, &entry) == 0 && S_ISDIR(entry.st_mode) &&
           (entry.st_mode & (S_IWGRP | S_IWOTH)) == 0;
}

int OpenDirectoryAt(int parent_fd, const std::string& name) {
    const int fd = openat(parent_fd, name.c_str(),
                          O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0 || !IsSecureDirectory(fd)) {
        if (fd >= 0) close(fd);
        StorageUnavailable();
    }
    return fd;
}

int EnsureDirectoryAt(int parent_fd, const std::string& name) {
    const bool created = mkdirat(parent_fd, name.c_str(), 0700) == 0;
    if (!created && errno != EEXIST) {
        StorageUnavailable();
    }
    if (created && fsync(parent_fd) != 0) StorageUnavailable();
    return OpenDirectoryAt(parent_fd, name);
}

int OpenAbsoluteDirectory(const std::string& path) {
    if (path.empty() || path.front() != '/' ||
        path.find('\0') != std::string::npos) {
        StorageUnavailable();
    }
    int current = open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (current < 0) StorageUnavailable();
    size_t start = 1;
    try {
        while (start <= path.size()) {
            const size_t slash = path.find('/', start);
            const size_t end =
                slash == std::string::npos ? path.size() : slash;
            const std::string component = path.substr(start, end - start);
            if (!component.empty()) {
                if (component == "." || component == "..") {
                    StorageUnavailable();
                }
                const int next = openat(
                    current, component.c_str(),
                    O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
                if (next < 0) StorageUnavailable();
                close(current);
                current = next;
            }
            if (slash == std::string::npos) break;
            start = slash + 1;
        }
    } catch (...) {
        close(current);
        throw;
    }
    return current;
}

void CloseNoThrow(int* fd) noexcept {
    if (*fd >= 0) {
        close(*fd);
        *fd = -1;
    }
}

std::string DigestHex(const unsigned char* bytes, size_t size) {
    static const char digits[] = "0123456789abcdef";
    std::string result(size * 2, '0');
    for (size_t index = 0; index < size; ++index) {
        result[index * 2] = digits[bytes[index] >> 4];
        result[index * 2 + 1] = digits[bytes[index] & 0x0f];
    }
    return result;
}

class DigestContext {
public:
    DigestContext() : context_(EVP_MD_CTX_new()) {
        if (context_ == nullptr ||
            EVP_DigestInit_ex(context_, EVP_sha256(), nullptr) != 1) {
            if (context_ != nullptr) EVP_MD_CTX_free(context_);
            throw AppError(500, "crypto_failed",
                           "unable to initialize SHA-256");
        }
    }

    ~DigestContext() { EVP_MD_CTX_free(context_); }

    void Update(const void* data, size_t size) {
        if (EVP_DigestUpdate(context_, data, size) != 1) {
            throw AppError(500, "crypto_failed", "SHA-256 update failed");
        }
    }

    std::string Finish() {
        unsigned char bytes[EVP_MAX_MD_SIZE];
        unsigned int size = 0;
        if (EVP_DigestFinal_ex(context_, bytes, &size) != 1 || size != 32) {
            throw AppError(500, "crypto_failed", "SHA-256 finalization failed");
        }
        return DigestHex(bytes, size);
    }

private:
    EVP_MD_CTX* context_;
};

struct FileDigest {
    uint64_t size;
    std::string sha256;
};

FileDigest ReadAndDigest(int fd) {
    DigestContext digest;
    uint64_t total = 0;
    char buffer[64 * 1024];
    while (true) {
        ssize_t count = read(fd, buffer, sizeof(buffer));
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) StorageUnavailable();
        if (count == 0) break;
        const size_t received = static_cast<size_t>(count);
        if (total > std::numeric_limits<uint64_t>::max() - received) {
            IntegrityFailed();
        }
        digest.Update(buffer, received);
        total += received;
    }
    return FileDigest{total, digest.Finish()};
}

int OpenRegularAt(int directory_fd, const std::string& name) {
    const int fd = openat(directory_fd, name.c_str(),
                          O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) StorageUnavailable();
    struct stat entry{};
    if (fstat(fd, &entry) != 0 || !S_ISREG(entry.st_mode) || entry.st_size < 0) {
        close(fd);
        StorageUnavailable();
    }
    return fd;
}

bool ExistingMatches(int directory_fd, const std::string& name,
                     uint64_t size, const std::string& sha256) {
    const int fd = OpenRegularAt(directory_fd, name);
    FileDigest actual;
    try {
        actual = ReadAndDigest(fd);
    } catch (...) {
        close(fd);
        throw;
    }
    close(fd);
    return actual.size == size && actual.sha256 == sha256;
}

void PublishNoReplace(int source_fd, const std::string& source,
                      int target_fd, const std::string& target,
                      uint64_t size, const std::string& sha256) {
    if (linkat(source_fd, source.c_str(), target_fd, target.c_str(), 0) != 0) {
        if (errno != EEXIST && errno != ENOENT) StorageUnavailable();
        if (!ExistingMatches(target_fd, target, size, sha256)) {
            StorageConflict();
        }
    }
    if (unlinkat(source_fd, source.c_str(), 0) != 0 && errno != ENOENT) {
        StorageUnavailable();
    }
    if (fsync(target_fd) != 0 ||
        (source_fd != target_fd && fsync(source_fd) != 0)) {
        StorageUnavailable();
    }
}

bool EndsWith(const std::string& value, const char* suffix) {
    const size_t suffix_size = std::strlen(suffix);
    return value.size() >= suffix_size &&
           value.compare(value.size() - suffix_size, suffix_size, suffix) == 0;
}

bool IsServerTemporary(const std::string& name) {
    if (name == "assembled.tmp") return true;
    if (name.size() == 36 && EndsWith(name, ".tmp")) {
        return IsLowerHex(name.substr(0, 32), 32);
    }
    const std::string prefix = "assembled.";
    return name.size() == prefix.size() + 32 + 4 &&
           name.compare(0, prefix.size(), prefix) == 0 &&
           IsLowerHex(name.substr(prefix.size(), 32), 32) &&
           EndsWith(name, ".tmp");
}

std::vector<std::string> DirectoryNames(int directory_fd) {
    const int scan_fd = openat(directory_fd, ".",
                               O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (scan_fd < 0 || !IsSecureDirectory(scan_fd)) {
        if (scan_fd >= 0) close(scan_fd);
        StorageUnavailable();
    }
    DIR* directory = fdopendir(scan_fd);
    if (directory == nullptr) {
        close(scan_fd);
        StorageUnavailable();
    }
    std::vector<std::string> names;
    errno = 0;
    while (dirent* entry = readdir(directory)) {
        const std::string name(entry->d_name);
        if (name != "." && name != "..") names.push_back(name);
        errno = 0;
    }
    const int saved_errno = errno;
    if (closedir(directory) != 0 || saved_errno != 0) StorageUnavailable();
    return names;
}

void RemoveTemporaryEntries(int directory_fd) {
    for (const std::string& name : DirectoryNames(directory_fd)) {
        if (!IsServerTemporary(name)) continue;
        if (unlinkat(directory_fd, name.c_str(), 0) != 0 && errno != ENOENT) {
            StorageUnavailable();
        }
    }
    if (fsync(directory_fd) != 0) StorageUnavailable();
}

}  // namespace

PartWriter::PartWriter(int directory_fd, int file_fd,
                       std::string temporary_name, std::string final_name,
                       uint32_t part_number)
    : directory_fd_(directory_fd),
      file_fd_(file_fd),
      temporary_name_(std::move(temporary_name)),
      final_name_(std::move(final_name)),
      part_number_(part_number),
      size_(0),
      digest_(EVP_MD_CTX_new()),
      finished_(false) {
    if (digest_ == nullptr ||
        EVP_DigestInit_ex(digest_, EVP_sha256(), nullptr) != 1) {
        Cleanup();
        throw AppError(500, "crypto_failed", "unable to initialize SHA-256");
    }
}

PartWriter::~PartWriter() { Cleanup(); }

PartWriter::PartWriter(PartWriter&& other) noexcept
    : directory_fd_(other.directory_fd_),
      file_fd_(other.file_fd_),
      temporary_name_(std::move(other.temporary_name_)),
      final_name_(std::move(other.final_name_)),
      part_number_(other.part_number_),
      size_(other.size_),
      digest_(other.digest_),
      finished_(other.finished_) {
    other.directory_fd_ = -1;
    other.file_fd_ = -1;
    other.digest_ = nullptr;
    other.finished_ = true;
}

PartWriter& PartWriter::operator=(PartWriter&& other) noexcept {
    if (this != &other) {
        Cleanup();
        directory_fd_ = other.directory_fd_;
        file_fd_ = other.file_fd_;
        temporary_name_ = std::move(other.temporary_name_);
        final_name_ = std::move(other.final_name_);
        part_number_ = other.part_number_;
        size_ = other.size_;
        digest_ = other.digest_;
        finished_ = other.finished_;
        other.directory_fd_ = -1;
        other.file_fd_ = -1;
        other.digest_ = nullptr;
        other.finished_ = true;
    }
    return *this;
}

void PartWriter::Cleanup() noexcept {
    CloseNoThrow(&file_fd_);
    if (!finished_ && directory_fd_ >= 0 && !temporary_name_.empty()) {
        unlinkat(directory_fd_, temporary_name_.c_str(), 0);
    }
    CloseNoThrow(&directory_fd_);
    if (digest_ != nullptr) {
        EVP_MD_CTX_free(digest_);
        digest_ = nullptr;
    }
}

void PartWriter::Write(const char* data, size_t size) {
    if (finished_ || file_fd_ < 0 || (data == nullptr && size != 0)) {
        throw AppError(400, "invalid_request", "part writer is not writable");
    }
    size_t offset = 0;
    while (offset < size) {
        const ssize_t written = write(file_fd_, data + offset, size - offset);
        if (written < 0 && errno == EINTR) continue;
        if (written <= 0) StorageUnavailable();
        const size_t accepted = static_cast<size_t>(written);
        if (size_ > std::numeric_limits<uint64_t>::max() - accepted) {
            IntegrityFailed();
        }
        if (EVP_DigestUpdate(digest_, data + offset, accepted) != 1) {
            throw AppError(500, "crypto_failed", "SHA-256 update failed");
        }
        size_ += accepted;
        offset += accepted;
    }
}

PartInfo PartWriter::Finish() {
    if (finished_ || file_fd_ < 0 || digest_ == nullptr) {
        throw AppError(400, "invalid_request", "part writer is already finished");
    }
    if (fsync(file_fd_) != 0) StorageUnavailable();
    FaultInjector::Hit(FaultPoint::AfterPartTempFsync);
    unsigned char bytes[EVP_MAX_MD_SIZE];
    unsigned int digest_size = 0;
    if (EVP_DigestFinal_ex(digest_, bytes, &digest_size) != 1 ||
        digest_size != 32) {
        throw AppError(500, "crypto_failed", "SHA-256 finalization failed");
    }
    const std::string sha256 = DigestHex(bytes, digest_size);
    CloseNoThrow(&file_fd_);
    PublishNoReplace(directory_fd_, temporary_name_, directory_fd_,
                     final_name_, size_, sha256);
    finished_ = true;
    EVP_MD_CTX_free(digest_);
    digest_ = nullptr;
    CloseNoThrow(&directory_fd_);
    return PartInfo{part_number_, size_, sha256};
}

FileStore::FileStore(const std::string& root)
    : root_fd_(-1), objects_fd_(-1), staging_fd_(-1) {
    root_fd_ = OpenAbsoluteDirectory(root);
    if (!IsSecureDirectory(root_fd_)) {
        CloseNoThrow(&root_fd_);
        StorageUnavailable();
    }
    try {
        objects_fd_ = OpenDirectoryAt(root_fd_, "objects");
        staging_fd_ = OpenDirectoryAt(root_fd_, "staging");
    } catch (...) {
        CloseNoThrow(&objects_fd_);
        CloseNoThrow(&staging_fd_);
        CloseNoThrow(&root_fd_);
        throw;
    }
}

FileStore::~FileStore() {
    CloseNoThrow(&objects_fd_);
    CloseNoThrow(&staging_fd_);
    CloseNoThrow(&root_fd_);
}

PartWriter FileStore::CreatePartWriter(const std::string& task_id,
                                       uint32_t part_number) {
    RequireId(task_id);
    int task_fd = EnsureDirectoryAt(staging_fd_, task_id);
    int parts_fd = -1;
    try {
        parts_fd = EnsureDirectoryAt(task_fd, "parts");
        close(task_fd);
        task_fd = -1;
        const std::string temporary = GenerateId() + ".tmp";
        const int file_fd = openat(parts_fd, temporary.c_str(),
                                   O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC |
                                       O_NOFOLLOW,
                                   0600);
        if (file_fd < 0) StorageUnavailable();
        return PartWriter(parts_fd, file_fd, temporary,
                          std::to_string(part_number), part_number);
    } catch (...) {
        CloseNoThrow(&task_fd);
        CloseNoThrow(&parts_fd);
        throw;
    }
}

StoredTemp FileStore::Assemble(const std::string& task_id,
                               const std::vector<PartInfo>& parts) {
    RequireId(task_id);
    const int task_fd = EnsureDirectoryAt(staging_fd_, task_id);
    int parts_fd = -1;
    if (!parts.empty()) parts_fd = OpenDirectoryAt(task_fd, "parts");
    const std::string temporary = "assembled." + GenerateId() + ".tmp";
    int output_fd = openat(task_fd, temporary.c_str(),
                           O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC |
                               O_NOFOLLOW,
                           0600);
    if (output_fd < 0) {
        CloseNoThrow(&parts_fd);
        close(task_fd);
        StorageUnavailable();
    }

    try {
        DigestContext combined;
        uint64_t total = 0;
        char buffer[64 * 1024];
        for (size_t index = 0; index < parts.size(); ++index) {
            const PartInfo& expected = parts[index];
            if (expected.part_number != index) IntegrityFailed();
            RequireDigest(expected.sha256);
            const int part_fd = OpenRegularAt(parts_fd, std::to_string(index));
            DigestContext part_digest;
            uint64_t part_size = 0;
            try {
                while (true) {
                    const ssize_t count = read(part_fd, buffer, sizeof(buffer));
                    if (count < 0 && errno == EINTR) continue;
                    if (count < 0) StorageUnavailable();
                    if (count == 0) break;
                    const size_t received = static_cast<size_t>(count);
                    if (part_size >
                            std::numeric_limits<uint64_t>::max() - received ||
                        total >
                            std::numeric_limits<uint64_t>::max() - received) {
                        IntegrityFailed();
                    }
                    part_digest.Update(buffer, received);
                    combined.Update(buffer, received);
                    part_size += received;
                    total += received;
                    size_t offset = 0;
                    while (offset < received) {
                        const ssize_t written = write(
                            output_fd, buffer + offset, received - offset);
                        if (written < 0 && errno == EINTR) continue;
                        if (written <= 0) StorageUnavailable();
                        offset += static_cast<size_t>(written);
                    }
                }
            } catch (...) {
                close(part_fd);
                throw;
            }
            close(part_fd);
            if (part_size != expected.size ||
                part_digest.Finish() != expected.sha256) {
                IntegrityFailed();
            }
        }
        if (fsync(output_fd) != 0) StorageUnavailable();
        FaultInjector::Hit(FaultPoint::AfterAssembledFsync);
        CloseNoThrow(&output_fd);
        const std::string sha256 = combined.Finish();
        PublishNoReplace(task_fd, temporary, task_fd, "assembled.tmp", total,
                         sha256);
        CloseNoThrow(&parts_fd);
        close(task_fd);
        return StoredTemp{task_id, total, sha256};
    } catch (...) {
        CloseNoThrow(&output_fd);
        unlinkat(task_fd, temporary.c_str(), 0);
        CloseNoThrow(&parts_fd);
        close(task_fd);
        throw;
    }
}

PublishedObject FileStore::PublishObject(StoredTemp assembled,
                                         const std::string& content_id) {
    RequireId(assembled.task_id);
    RequireId(content_id);
    RequireDigest(assembled.sha256);
    const int task_fd = OpenDirectoryAt(staging_fd_, assembled.task_id);
    int assembled_fd = -1;
    int first_fd = -1;
    int second_fd = -1;
    try {
        first_fd = EnsureDirectoryAt(objects_fd_, content_id.substr(0, 2));
        second_fd = EnsureDirectoryAt(first_fd, content_id.substr(2, 2));
        assembled_fd = openat(task_fd, "assembled.tmp",
                              O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
        if (assembled_fd < 0 && errno == ENOENT) {
            if (!ExistingMatches(second_fd, content_id, assembled.size,
                                 assembled.sha256)) {
                StorageConflict();
            }
            close(second_fd);
            close(first_fd);
            close(task_fd);
            return PublishedObject{content_id, assembled.size,
                                   assembled.sha256};
        }
        if (assembled_fd < 0) StorageUnavailable();
        struct stat assembled_stat{};
        if (fstat(assembled_fd, &assembled_stat) != 0 ||
            !S_ISREG(assembled_stat.st_mode) || assembled_stat.st_size < 0) {
            StorageUnavailable();
        }
        const FileDigest actual = ReadAndDigest(assembled_fd);
        if (actual.size != assembled.size ||
            actual.sha256 != assembled.sha256) {
            IntegrityFailed();
        }
        if (fchmod(assembled_fd, 0400) != 0 || fsync(assembled_fd) != 0) {
            StorageUnavailable();
        }
        close(assembled_fd);
        assembled_fd = -1;
        PublishNoReplace(task_fd, "assembled.tmp", second_fd, content_id,
                         assembled.size, assembled.sha256);
        FaultInjector::Hit(FaultPoint::AfterObjectRename);
        close(second_fd);
        close(first_fd);
        close(task_fd);
        return PublishedObject{content_id, assembled.size, assembled.sha256};
    } catch (...) {
        CloseNoThrow(&assembled_fd);
        CloseNoThrow(&second_fd);
        CloseNoThrow(&first_fd);
        close(task_fd);
        throw;
    }
}

int FileStore::OpenObject(const std::string& content_id) const {
    RequireId(content_id);
    int first_fd = -1;
    int second_fd = -1;
    try {
        first_fd = OpenDirectoryAt(objects_fd_, content_id.substr(0, 2));
        second_fd = OpenDirectoryAt(first_fd, content_id.substr(2, 2));
        const int object_fd = OpenRegularAt(second_fd, content_id);
        close(second_fd);
        close(first_fd);
        return object_fd;
    } catch (const AppError& error) {
        CloseNoThrow(&second_fd);
        CloseNoThrow(&first_fd);
        if (error.code == "storage_unavailable") {
            throw AppError(503, "content_unavailable",
                           "file content is unavailable", true);
        }
        throw;
    }
}

bool FileStore::ObjectExists(const std::string& content_id) const {
    RequireId(content_id);
    const std::string first = content_id.substr(0, 2);
    const int first_fd = openat(objects_fd_, first.c_str(),
                                O_RDONLY | O_DIRECTORY | O_CLOEXEC |
                                    O_NOFOLLOW);
    if (first_fd < 0 && errno == ENOENT) return false;
    if (first_fd < 0 || !IsSecureDirectory(first_fd)) {
        if (first_fd >= 0) close(first_fd);
        StorageUnavailable();
    }
    const std::string second = content_id.substr(2, 2);
    const int second_fd = openat(first_fd, second.c_str(),
                                 O_RDONLY | O_DIRECTORY | O_CLOEXEC |
                                     O_NOFOLLOW);
    const int second_error = errno;
    close(first_fd);
    if (second_fd < 0 && second_error == ENOENT) return false;
    if (second_fd < 0 || !IsSecureDirectory(second_fd)) {
        if (second_fd >= 0) close(second_fd);
        StorageUnavailable();
    }
    const int object_fd = openat(second_fd, content_id.c_str(),
                                 O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    const int object_error = errno;
    close(second_fd);
    if (object_fd < 0 && object_error == ENOENT) return false;
    if (object_fd < 0) StorageUnavailable();
    struct stat entry{};
    const bool regular = fstat(object_fd, &entry) == 0 &&
                         S_ISREG(entry.st_mode) && entry.st_size >= 0;
    close(object_fd);
    if (!regular) StorageUnavailable();
    return true;
}

std::vector<std::string> FileStore::ListObjectsOlderThan(
    std::time_t cutoff) const {
    std::vector<std::string> objects;
    for (const std::string& first : DirectoryNames(objects_fd_)) {
        if (!IsLowerHex(first, 2)) continue;
        int first_fd = OpenDirectoryAt(objects_fd_, first);
        try {
            for (const std::string& second : DirectoryNames(first_fd)) {
                if (!IsLowerHex(second, 2)) continue;
                int second_fd = OpenDirectoryAt(first_fd, second);
                try {
                    for (const std::string& name : DirectoryNames(second_fd)) {
                        if (!IsLowerHex(name, 32) ||
                            name.compare(0, 2, first) != 0 ||
                            name.compare(2, 2, second) != 0) {
                            continue;
                        }
                        struct stat entry{};
                        if (fstatat(second_fd, name.c_str(), &entry,
                                    AT_SYMLINK_NOFOLLOW) != 0 ||
                            !S_ISREG(entry.st_mode)) {
                            StorageUnavailable();
                        }
                        if (entry.st_mtime < cutoff) objects.push_back(name);
                    }
                    close(second_fd);
                } catch (...) {
                    close(second_fd);
                    throw;
                }
            }
            close(first_fd);
        } catch (...) {
            close(first_fd);
            throw;
        }
    }
    return objects;
}

void FileStore::RemoveObject(const std::string& content_id) {
    RequireId(content_id);
    int first_fd = OpenDirectoryAt(objects_fd_, content_id.substr(0, 2));
    int second_fd = -1;
    try {
        second_fd = OpenDirectoryAt(first_fd, content_id.substr(2, 2));
        if (unlinkat(second_fd, content_id.c_str(), 0) != 0 && errno != ENOENT) {
            StorageUnavailable();
        }
        if (fsync(second_fd) != 0) StorageUnavailable();
        close(second_fd);
        close(first_fd);
    } catch (...) {
        CloseNoThrow(&second_fd);
        close(first_fd);
        throw;
    }
}

void FileStore::RemoveTaskTemporaryFiles(const std::string& task_id) {
    RequireId(task_id);
    const int task_fd = openat(staging_fd_, task_id.c_str(),
                               O_RDONLY | O_DIRECTORY | O_CLOEXEC |
                                   O_NOFOLLOW);
    if (task_fd < 0 && errno == ENOENT) return;
    if (task_fd < 0 || !IsSecureDirectory(task_fd)) {
        if (task_fd >= 0) close(task_fd);
        StorageUnavailable();
    }
    int parts_fd = -1;
    try {
        RemoveTemporaryEntries(task_fd);
        parts_fd = openat(task_fd, "parts",
                          O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        if (parts_fd >= 0) {
            RemoveTemporaryEntries(parts_fd);
            close(parts_fd);
            parts_fd = -1;
        } else if (errno != ENOENT) {
            StorageUnavailable();
        }
        close(task_fd);
    } catch (...) {
        CloseNoThrow(&parts_fd);
        close(task_fd);
        throw;
    }
}
