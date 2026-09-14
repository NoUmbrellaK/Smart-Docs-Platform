#include "core/app_error.h"
#include "file/file_store.h"
#include "../test_support.h"

#include <cerrno>
#include <cstring>
#include <dirent.h>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

namespace {

void RemoveTree(const std::string& path) {
    struct stat entry{};
    if (lstat(path.c_str(), &entry) != 0) {
        return;
    }
    if (!S_ISDIR(entry.st_mode) || S_ISLNK(entry.st_mode)) {
        unlink(path.c_str());
        return;
    }
    DIR* directory = opendir(path.c_str());
    CHECK(directory != nullptr);
    while (dirent* child = readdir(directory)) {
        if (std::strcmp(child->d_name, ".") == 0 ||
            std::strcmp(child->d_name, "..") == 0) {
            continue;
        }
        RemoveTree(path + "/" + child->d_name);
    }
    CHECK(closedir(directory) == 0);
    CHECK(rmdir(path.c_str()) == 0);
}

class TemporaryStorage {
public:
    TemporaryStorage() {
        char pattern[] = "/tmp/smart-docs-file-store.XXXXXX";
        char* created = mkdtemp(pattern);
        CHECK(created != nullptr);
        path_ = created;
        CHECK(mkdir((path_ + "/objects").c_str(), 0700) == 0);
        CHECK(mkdir((path_ + "/staging").c_str(), 0700) == 0);
    }

    ~TemporaryStorage() { RemoveTree(path_); }

    const std::string& path() const { return path_; }

private:
    std::string path_;
};

std::string ReadAll(int fd) {
    std::string result;
    char bytes[4];
    while (true) {
        const ssize_t count = read(fd, bytes, sizeof(bytes));
        if (count < 0 && errno == EINTR) {
            continue;
        }
        CHECK(count >= 0);
        if (count == 0) {
            return result;
        }
        result.append(bytes, static_cast<size_t>(count));
    }
}

size_t DirectoryEntryCount(const std::string& path) {
    DIR* directory = opendir(path.c_str());
    CHECK(directory != nullptr);
    size_t count = 0;
    while (dirent* child = readdir(directory)) {
        if (std::strcmp(child->d_name, ".") != 0 &&
            std::strcmp(child->d_name, "..") != 0) {
            ++count;
        }
    }
    CHECK(closedir(directory) == 0);
    return count;
}

}  // namespace

TEST_CASE(file_store_streams_parts_into_an_immutable_object) {
    TemporaryStorage storage;
    FileStore store(storage.path());
    const std::string task_id = "11111111111111111111111111111111";
    const std::string content_id = "aabb2222222222222222222222222222";

    PartWriter writer = store.CreatePartWriter(task_id, 0);
    writer.Write("hello ", 6);
    writer.Write("world", 5);
    const PartInfo part = writer.Finish();
    CHECK(part.part_number == 0);
    CHECK(part.size == 11);
    CHECK(part.sha256 ==
          "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9");

    const StoredTemp assembled = store.Assemble(task_id, {part});
    CHECK(assembled.task_id == task_id);
    CHECK(assembled.size == part.size);
    CHECK(assembled.sha256 == part.sha256);

    const PublishedObject published =
        store.PublishObject(assembled, content_id);
    CHECK(published.content_id == content_id);
    CHECK(published.size == 11);
    CHECK(published.sha256 == part.sha256);
    CHECK(store.ObjectExists(content_id));

    const int fd = store.OpenObject(content_id);
    struct stat published_stat{};
    CHECK(fstat(fd, &published_stat) == 0);
    CHECK((published_stat.st_mode & (S_IWUSR | S_IWGRP | S_IWOTH)) == 0);
    CHECK(ReadAll(fd) == "hello world");
    CHECK(close(fd) == 0);
    CHECK(!store.ObjectExists("aabb9999999999999999999999999999"));

    const std::string second_task = "55555555555555555555555555555555";
    PartWriter conflicting = store.CreatePartWriter(second_task, 0);
    conflicting.Write("other bytes", 11);
    const PartInfo conflicting_part = conflicting.Finish();
    const StoredTemp conflicting_assembled =
        store.Assemble(second_task, {conflicting_part});
    CHECK_THROWS_CODE(store.PublishObject(conflicting_assembled, content_id),
                      "storage_conflict");
    const int unchanged_fd = store.OpenObject(content_id);
    CHECK(ReadAll(unchanged_fd) == "hello world");
    CHECK(close(unchanged_fd) == 0);
    store.RemoveTaskTemporaryFiles(task_id);
}

TEST_CASE(file_store_rejects_untrusted_paths_and_removes_unfinished_writes) {
    TemporaryStorage storage;
    FileStore store(storage.path());
    const std::string task_id = "33333333333333333333333333333333";

    CHECK_THROWS_CODE(store.CreatePartWriter("../../resources/index.html", 0),
                      "invalid_request");
    CHECK_THROWS_CODE(store.OpenObject("not-lower-hex"), "invalid_request");
    CHECK_THROWS_CODE(store.ObjectExists("AA444444444444444444444444444444"),
                      "invalid_request");

    {
        PartWriter writer = store.CreatePartWriter(task_id, 7);
        writer.Write("partial", 7);
    }
    CHECK(DirectoryEntryCount(storage.path() + "/staging/" + task_id +
                              "/parts") == 0);

    const std::string link = storage.path() + "/linked-root";
    CHECK(symlink(storage.path().c_str(), link.c_str()) == 0);
    CHECK_THROWS_CODE(FileStore(link), "storage_unavailable");

    const std::string real_parent = storage.path() + "/real-parent";
    const std::string nested_root = real_parent + "/root";
    CHECK(mkdir(real_parent.c_str(), 0700) == 0);
    CHECK(mkdir(nested_root.c_str(), 0700) == 0);
    CHECK(mkdir((nested_root + "/objects").c_str(), 0700) == 0);
    CHECK(mkdir((nested_root + "/staging").c_str(), 0700) == 0);
    const std::string parent_link = storage.path() + "/parent-link";
    CHECK(symlink("real-parent", parent_link.c_str()) == 0);
    CHECK_THROWS_CODE(FileStore(parent_link + "/root"),
                      "storage_unavailable");
}

TEST_CASE(file_store_detects_part_order_size_and_digest_mismatches) {
    TemporaryStorage storage;
    FileStore store(storage.path());
    const std::string task_id = "55555555555555555555555555555555";

    PartWriter writer = store.CreatePartWriter(task_id, 0);
    writer.Write("abc", 3);
    const PartInfo part = writer.Finish();

    PartInfo wrong_number = part;
    wrong_number.part_number = 1;
    CHECK_THROWS_CODE(store.Assemble(task_id, {wrong_number}),
                      "storage_integrity_failed");
    PartInfo wrong_size = part;
    wrong_size.size = 4;
    CHECK_THROWS_CODE(store.Assemble(task_id, {wrong_size}),
                      "storage_integrity_failed");
    PartInfo wrong_digest = part;
    wrong_digest.sha256.assign(64, '0');
    CHECK_THROWS_CODE(store.Assemble(task_id, {wrong_digest}),
                      "storage_integrity_failed");

    const StoredTemp assembled = store.Assemble(task_id, {part});
    CHECK(assembled.sha256 ==
          "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    store.RemoveTaskTemporaryFiles(task_id);
}
