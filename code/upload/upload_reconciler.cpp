#include "upload_reconciler.h"

#include "core/app_error.h"
#include "db/mysql.h"
#include "file/file_store.h"
#include "upload_repository.h"

#include <ctime>
#include <vector>

UploadReconciler::UploadReconciler(MySqlPool& pool, FileStore& store)
    : pool_(pool), store_(store) {}

void UploadReconciler::RunAtStartup() {
    UploadRepository repository;
    std::vector<UploadTaskRecord> tasks;
    std::vector<PublishedVersionRecord> versions;
    {
        MySqlConnection connection = pool_.Acquire();
        MySqlTransaction transaction(connection);
        tasks = repository.ListInFlight(connection);
        versions = repository.ListAvailableVersions(connection);
        transaction.Commit();
    }

    for (const UploadTaskRecord& task : tasks) {
        store_.RemoveTaskTemporaryFiles(task.task.id);
        MySqlConnection connection = pool_.Acquire();
        MySqlTransaction transaction(connection);
        UploadTaskRecord current;
        if (repository.FindOwn(connection, task.task.project_id,
                               task.task.owner_id, task.task.id, true,
                               &current)) {
            if (current.task.state == "completed") {
                if (!repository.CompletedGraphValid(connection, current)) {
                    throw AppError(500, "database_result_invalid",
                                   "completed upload result graph is invalid");
                }
            } else if (current.task.state == "uploading" ||
                       current.task.state == "assembling" ||
                       current.task.state == "publishing") {
                repository.SetState(connection, current.task.id, "interrupted");
            }
        }
        transaction.Commit();
    }

    for (const PublishedVersionRecord& version : versions) {
        if (store_.ObjectExists(version.content_id)) continue;
        MySqlConnection connection = pool_.Acquire();
        MySqlTransaction transaction(connection);
        repository.MarkVersionUnavailable(connection, version);
        transaction.Commit();
    }

    const std::time_t cutoff = std::time(nullptr) - 24 * 60 * 60;
    for (const std::string& content_id : store_.ListObjectsOlderThan(cutoff)) {
        MySqlConnection connection = pool_.Acquire();
        MySqlTransaction transaction(connection);
        if (!repository.ContentReferenced(connection, content_id)) {
            store_.RemoveObject(content_id);
        }
        transaction.Commit();
    }
}
