#include "app/application.h"
#include "auth/auth_service.h"
#include "core/app_error.h"
#include "core/crypto.h"
#include "file/file_service.h"
#include "file/file_store.h"
#include "project/project_service.h"
#include "upload/upload_reconciler.h"
#include "upload/upload_service.h"
#include "../mysql_test_support.h"
#include "../test_support.h"

#include <cerrno>
#include <atomic>
#include <chrono>
#include <ctime>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <string>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>

namespace {

const char* kPassword = "correct horse battery";
const char* kRequest = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

void RemoveTree(const std::string& path) {
    struct stat entry{};
    if (lstat(path.c_str(), &entry) != 0) return;
    if (!S_ISDIR(entry.st_mode) || S_ISLNK(entry.st_mode)) {
        unlink(path.c_str());
        return;
    }
    DIR* directory = opendir(path.c_str());
    CHECK(directory != nullptr);
    while (dirent* child = readdir(directory)) {
        if (std::strcmp(child->d_name, ".") == 0 ||
            std::strcmp(child->d_name, "..") == 0) continue;
        RemoveTree(path + "/" + child->d_name);
    }
    CHECK(closedir(directory) == 0);
    CHECK(rmdir(path.c_str()) == 0);
}

class TemporaryStorage {
public:
    TemporaryStorage() {
        char pattern[] = "/tmp/smart-docs-upload-recovery.XXXXXX";
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

void WriteFile(const std::string& path, const std::string& bytes) {
    const int fd = open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC,
                        0600);
    CHECK(fd >= 0);
    CHECK(write(fd, bytes.data(), bytes.size()) ==
          static_cast<ssize_t>(bytes.size()));
    CHECK(close(fd) == 0);
}

CreateUploadCommand NewFile(const Project& project, const std::string& name,
                            const std::string& bytes) {
    CreateUploadCommand command{};
    command.mode = UploadMode::CreateFile;
    command.directory_id = project.root_directory_id;
    command.name = name;
    command.size = bytes.size();
    command.sha256 = Sha256Hex(bytes.data(), bytes.size());
    command.media_type = "text/plain";
    return command;
}

void PutPart(UploadService& uploads, const SessionContext& session,
             const Project& project, const UploadTask& task,
             const std::string& bytes) {
    std::unique_ptr<PartWriter> writer = uploads.BeginPart(
        session, project.id, task.id, 0, bytes.size(),
        Sha256Hex(bytes.data(), bytes.size()));
    writer->Write(bytes.data(), bytes.size());
    uploads.ConfirmPart(session, project.id, task.id, writer->Finish(), kRequest);
}

PublishedObject Publish(FileStore& store, const std::string& task_id,
                        const std::string& content_id,
                        const std::string& bytes) {
    PartWriter writer = store.CreatePartWriter(task_id, 0);
    writer.Write(bytes.data(), bytes.size());
    const PartInfo part = writer.Finish();
    return store.PublishObject(store.Assemble(task_id, {part}), content_id);
}

void MakeOld(const std::string& path) {
    struct timespec times[2]{};
    times[0].tv_sec = times[1].tv_sec = time(nullptr) - 25 * 60 * 60;
    CHECK(utimensat(AT_FDCWD, path.c_str(), times, 0) == 0);
}

}  // namespace

TEST_CASE(upload_recovery_interrupts_inflight_tasks_and_only_removes_server_temps) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    FileStore store(storage.path());
    UploadService uploads(TestDatabase(), projects, store, 1024, 4);
    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const SessionContext session =
        auth.Login("admin", kPassword, kRequest).session;
    const Project project = projects.CreateProject(admin.id, "Alpha", kRequest);
    const UploadTask uploading = uploads.Create(
        session, project.id, NewFile(project, "uploading.txt", "datamore"),
        kRequest);
    const UploadTask publishing = uploads.Create(
        session, project.id, NewFile(project, "publishing.txt", ""), kRequest);
    PutPart(uploads, session, project, uploading, "data");
    const std::string task_path = storage.path() + "/staging/" + uploading.id;
    WriteFile(task_path + "/assembled.11111111111111111111111111111111.tmp",
              "partial");
    WriteFile(task_path + "/parts/22222222222222222222222222222222.tmp",
              "partial");
    WriteFile(task_path + "/user.tmp", "retain");
    Publish(store, "33333333333333333333333333333333", publishing.id,
            "orphan");
    const std::string orphan_path = storage.path() + "/objects/" +
        publishing.id.substr(0, 2) + "/" + publishing.id.substr(2, 2) + "/" +
        publishing.id;
    MakeOld(orphan_path);
    {
        MySqlConnection connection = TestDatabase().Acquire();
        connection.Execute(
            "UPDATE upload_tasks SET state='assembling' WHERE id=?",
            {SqlValue(uploading.id)});
        connection.Execute(
            "UPDATE upload_tasks SET state='publishing' WHERE id=?",
            {SqlValue(publishing.id)});
    }

    UploadReconciler(TestDatabase(), store).RunAtStartup();
    CHECK(uploads.GetOwn(session, project.id, uploading.id).task.state ==
          "interrupted");
    CHECK(uploads.GetOwn(session, project.id, publishing.id).task.state ==
          "interrupted");
    CHECK(!store.ObjectExists(publishing.id));
    struct stat entry{};
    CHECK(lstat((task_path + "/parts/0").c_str(), &entry) == 0);
    CHECK(lstat((task_path + "/user.tmp").c_str(), &entry) == 0);
    errno = 0;
    CHECK(lstat((task_path +
                 "/parts/22222222222222222222222222222222.tmp").c_str(),
                &entry) != 0);
    CHECK(errno == ENOENT);
    errno = 0;
    CHECK(lstat((task_path +
                 "/assembled.11111111111111111111111111111111.tmp").c_str(),
                &entry) != 0);
    CHECK(errno == ENOENT);

    std::unique_ptr<PartWriter> resumed = uploads.BeginPart(
        session, project.id, uploading.id, 1, 4, Sha256Hex("more", 4));
    resumed->Write("more", 4);
    uploads.ConfirmPart(session, project.id, uploading.id, resumed->Finish(),
                        kRequest);
    CHECK(uploads.GetOwn(session, project.id, uploading.id).task.state ==
          "uploading");
    CHECK(!uploads.Complete(session, project.id, uploading.id, kRequest).reused);
}

TEST_CASE(upload_recovery_keeps_committed_graph_and_marks_missing_object) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    FileStore store(storage.path());
    UploadService uploads(TestDatabase(), projects, store, 1024, 4);
    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const SessionContext session =
        auth.Login("admin", kPassword, kRequest).session;
    const Project project = projects.CreateProject(admin.id, "Alpha", kRequest);
    const UploadTask task = uploads.Create(
        session, project.id, NewFile(project, "committed.txt", "data"), kRequest);
    PutPart(uploads, session, project, task, "data");
    const CompleteUploadResult completed =
        uploads.Complete(session, project.id, task.id, kRequest);
    UploadReconciler(TestDatabase(), store).RunAtStartup();
    CHECK(uploads.GetOwn(session, project.id, task.id).task.state == "completed");

    const std::string object_path = storage.path() + "/objects/" +
        task.id.substr(0, 2) + "/" + task.id.substr(2, 2) + "/" + task.id;
    CHECK(unlink(object_path.c_str()) == 0);
    UploadReconciler(TestDatabase(), store).RunAtStartup();
    MySqlConnection connection = TestDatabase().Acquire();
    CHECK(connection.ScalarInt(
              "SELECT COUNT(*) FROM file_versions WHERE id=? AND "
              "availability_state='unavailable'",
              {SqlValue(completed.version_id)}) == 1);
    CHECK(connection.ScalarInt(
              "SELECT COUNT(*) FROM audit_records WHERE "
              "action='file.version.missing_object' AND object_id=? AND "
              "result='error' AND detail_code='content_missing'",
              {SqlValue(completed.version_id)}) == 1);
    UploadReconciler(TestDatabase(), store).RunAtStartup();
    CHECK(connection.ScalarInt(
              "SELECT COUNT(*) FROM audit_records WHERE "
              "action='file.version.missing_object' AND object_id=? AND "
              "result='error' AND detail_code='content_missing'",
              {SqlValue(completed.version_id)}) == 1);
}

TEST_CASE(upload_recovery_rejects_completed_graph_that_mismatches_upload_intent) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    FileStore store(storage.path());
    UploadService uploads(TestDatabase(), projects, store, 1024, 4);
    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const SessionContext session =
        auth.Login("admin", kPassword, kRequest).session;
    const Project project = projects.CreateProject(admin.id, "Alpha", kRequest);
    const UploadTask task = uploads.Create(
        session, project.id, NewFile(project, "malformed.txt", "data"), kRequest);
    PutPart(uploads, session, project, task, "data");
    const CompleteUploadResult completed =
        uploads.Complete(session, project.id, task.id, kRequest);
    MySqlConnection connection = TestDatabase().Acquire();
    connection.Execute(
        "UPDATE file_versions SET media_type='application/octet-stream' "
        "WHERE id=?",
        {SqlValue(completed.version_id)});
    CHECK_THROWS_CODE(UploadReconciler(TestDatabase(), store).RunAtStartup(),
                      "database_result_invalid");
}

TEST_CASE(upload_recovery_accepts_completed_create_file_after_supported_mutation) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    FileStore store(storage.path());
    FileService files(TestDatabase(), projects, store);
    UploadService uploads(TestDatabase(), projects, store, 1024, 4);
    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const SessionContext session =
        auth.Login("admin", kPassword, kRequest).session;
    const Project project = projects.CreateProject(admin.id, "Alpha", kRequest);
    const Directory other = projects.CreateDirectory(
        session, project.id, project.root_directory_id, "Other", kRequest);
    const UploadTask task = uploads.Create(
        session, project.id, NewFile(project, "intent.txt", "data"), kRequest);
    PutPart(uploads, session, project, task, "data");
    const CompleteUploadResult completed =
        uploads.Complete(session, project.id, task.id, kRequest);
    files.Update(session, project.id, completed.file_id,
                 UpdateFileCommand{"renamed.txt", other.id}, kRequest);
    UploadReconciler(TestDatabase(), store).RunAtStartup();
    CHECK(uploads.GetOwn(session, project.id, task.id).task.state == "completed");
}

TEST_CASE(upload_recovery_locks_upload_rows_before_destructive_cleanup) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    FileStore store(storage.path());
    UploadService uploads(TestDatabase(), projects, store, 1024, 4);
    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const SessionContext session =
        auth.Login("admin", kPassword, kRequest).session;
    const Project project = projects.CreateProject(admin.id, "Alpha", kRequest);
    const UploadTask task = uploads.Create(
        session, project.id, NewFile(project, "locked.txt", "data"), kRequest);
    PutPart(uploads, session, project, task, "data");
    const std::string temporary = storage.path() + "/staging/" + task.id +
        "/assembled.77777777777777777777777777777777.tmp";
    WriteFile(temporary, "partial");
    {
        MySqlConnection connection = TestDatabase().Acquire();
        connection.Execute("UPDATE upload_tasks SET state='assembling' WHERE id=?",
                           {SqlValue(task.id)});
    }

    MySqlConnection blocker = TestDatabase().Acquire();
    MySqlTransaction blocker_transaction(blocker);
    blocker.Query("SELECT id FROM upload_tasks WHERE id=? FOR UPDATE",
                  {SqlValue(task.id)});
    std::atomic<bool> started(false);
    std::thread reconciliation([&]() {
        started.store(true);
        UploadReconciler(TestDatabase(), store).RunAtStartup();
    });
    while (!started.load()) std::this_thread::yield();
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    struct stat entry{};
    CHECK(lstat(temporary.c_str(), &entry) == 0);
    blocker_transaction.Commit();
    reconciliation.join();
    errno = 0;
    CHECK(lstat(temporary.c_str(), &entry) != 0);
    CHECK(errno == ENOENT);
}

TEST_CASE(upload_recovery_locks_task_named_orphans_before_unlinking) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    FileStore store(storage.path());
    UploadService uploads(TestDatabase(), projects, store, 1024, 4);
    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const SessionContext session =
        auth.Login("admin", kPassword, kRequest).session;
    const Project project = projects.CreateProject(admin.id, "Alpha", kRequest);
    const UploadTask task = uploads.Create(
        session, project.id, NewFile(project, "orphan.txt", ""), kRequest);
    {
        MySqlConnection connection = TestDatabase().Acquire();
        connection.Execute("UPDATE upload_tasks SET state='interrupted' WHERE id=?",
                           {SqlValue(task.id)});
    }
    Publish(store, "88888888888888888888888888888888", task.id, "orphan");
    const std::string object_path = storage.path() + "/objects/" +
        task.id.substr(0, 2) + "/" + task.id.substr(2, 2) + "/" + task.id;
    MakeOld(object_path);

    MySqlConnection blocker = TestDatabase().Acquire();
    MySqlTransaction blocker_transaction(blocker);
    blocker.Query("SELECT id FROM upload_tasks WHERE id=? FOR UPDATE",
                  {SqlValue(task.id)});
    std::atomic<bool> started(false);
    std::thread reconciliation([&]() {
        started.store(true);
        UploadReconciler(TestDatabase(), store).RunAtStartup();
    });
    while (!started.load()) std::this_thread::yield();
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    CHECK(store.ObjectExists(task.id));
    blocker_transaction.Commit();
    reconciliation.join();
    CHECK(!store.ObjectExists(task.id));
}

TEST_CASE(upload_recovery_removes_only_old_unreferenced_objects) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    FileStore store(storage.path());
    const std::string old_id = "aa111111111111111111111111111111";
    const std::string recent_id = "bb222222222222222222222222222222";
    const std::string later_old_id = "ee555555555555555555555555555555";
    Publish(store, "cc333333333333333333333333333333", old_id, "old");
    Publish(store, "dd444444444444444444444444444444", recent_id, "new");
    const std::string old_path = storage.path() + "/objects/aa/11/" + old_id;
    MakeOld(old_path);

    UploadReconciler(TestDatabase(), store).RunAtStartup();
    CHECK(!store.ObjectExists(old_id));
    CHECK(store.ObjectExists(recent_id));

    Publish(store, "ff666666666666666666666666666666", later_old_id, "later");
    const std::string later_old_path =
        storage.path() + "/objects/ee/55/" + later_old_id;
    MakeOld(later_old_path);
    UploadReconciler(TestDatabase(), store).RunAtStartup();
    CHECK(!store.ObjectExists(later_old_id));
}

TEST_CASE(upload_recovery_runs_before_application_readiness_and_rejects_fault_env) {
    RequireMySqlTests();
    ResetTestDatabase();
    TemporaryStorage storage;
    AuthService auth(TestDatabase(), 60, 1000);
    ProjectService projects(TestDatabase());
    FileStore store(storage.path());
    UploadService uploads(TestDatabase(), projects, store, 1024, 4);
    const UserIdentity admin = auth.CreateUser("admin", kPassword, kRequest);
    const SessionContext session =
        auth.Login("admin", kPassword, kRequest).session;
    const Project project = projects.CreateProject(admin.id, "Alpha", kRequest);
    const UploadTask task = uploads.Create(
        session, project.id, NewFile(project, "restart.txt", "data"), kRequest);

    Application application(TestAppConfig(storage.path(), 4));
    CHECK(application.Ready());
    CHECK(uploads.GetOwn(session, project.id, task.id).task.state ==
          "interrupted");

    ScopedEnvironment environment;
    environment.Set("SMARTDOCS_FAULT_POINT", "BeforeDatabaseCommit");
    CHECK_THROWS_CODE(Application(TestAppConfig(storage.path(), 4)),
                      "fault_injection_forbidden");
}
