#include "upload_service.h"

#include "core/app_error.h"
#include "core/fault_injector.h"
#include "core/id.h"
#include "project/project_service.h"
#include "upload_repository.h"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <limits>
#include <thread>
#include <utility>

namespace {

const uint64_t kMaximumParts = 1000000;
const int kCompletionWaitAttempts = 5000;

bool IsMediaTypeToken(unsigned char ch) {
    if (std::isalnum(ch)) return true;
    const std::string punctuation("!#$%&'*+-.^_`|~");
    return punctuation.find(static_cast<char>(ch)) != std::string::npos;
}

bool IsLowerHexId(const std::string& value) {
    return value.size() == 32 &&
           value.find_first_not_of("0123456789abcdef") == std::string::npos;
}

void RequireId(const std::string& value) {
    if (!IsLowerHexId(value)) {
        throw AppError(400, "invalid_request", "entity ID is invalid");
    }
}

std::string CheckedRequestId(const std::string& value) {
    if (value.empty()) return GenerateId();
    RequireId(value);
    return value;
}

[[noreturn]] void ResourceNotFound() {
    throw AppError(404, "resource_not_found", "resource was not found");
}

void RequireUploading(const UploadTaskRecord& record) {
    if (record.task.state != "uploading") {
        throw AppError(409, "upload_state_conflict",
                       "upload task is not accepting parts");
    }
}

CompleteUploadResult StoredResult(const UploadTaskRecord& record,
                                  bool reused) {
    return CompleteUploadResult{record.task.file_id, record.task.version_id,
                                record.task.processing_job_id, reused};
}

void ValidateListQuery(const UploadListQuery& query) {
    static const char* states[] = {"uploading", "assembling", "publishing",
                                   "completed", "failed", "cancelled",
                                   "interrupted"};
    if (!query.state.empty()) {
        bool valid = false;
        for (const char* state : states) valid = valid || query.state == state;
        if (!valid) {
            throw AppError(400, "invalid_request",
                           "upload state filter is invalid");
        }
    }
    if (query.page == 0 || query.page_size == 0 || query.page_size > 100 ||
        query.page - 1 >
            std::numeric_limits<uint64_t>::max() / query.page_size) {
        throw AppError(400, "invalid_request",
                       "upload list query is invalid");
    }
}

}  // namespace

uint64_t UploadPartCount(uint64_t file_size, uint64_t chunk_size) {
    if (chunk_size == 0) {
        throw AppError(500, "invalid_configuration",
                       "upload chunk size must be positive");
    }
    const uint64_t count =
        file_size / chunk_size + (file_size % chunk_size == 0 ? 0 : 1);
    if (count > kMaximumParts) {
        throw AppError(413, "too_many_parts",
                       "file requires too many upload parts");
    }
    return count;
}

uint64_t ExpectedUploadPartSize(uint64_t file_size, uint64_t chunk_size,
                                uint32_t part_number) {
    const uint64_t count = UploadPartCount(file_size, chunk_size);
    if (part_number >= count) {
        throw AppError(400, "invalid_part_number",
                       "upload part number is out of range");
    }
    const uint64_t offset = static_cast<uint64_t>(part_number) * chunk_size;
    return part_number + 1 == count ? file_size - offset : chunk_size;
}

void ValidateUploadSha256(const std::string& value) {
    if (value.size() != 64 ||
        value.find_first_not_of("0123456789abcdef") != std::string::npos) {
        throw AppError(400, "invalid_request",
                       "SHA-256 must be 64 lowercase hexadecimal characters");
    }
}

std::string ValidateUploadMediaType(const std::string& value) {
    const size_t slash = value.find('/');
    if (slash == std::string::npos || slash == 0 ||
        slash + 1 == value.size() || value.find('/', slash + 1) != std::string::npos ||
        value.size() > 255) {
        throw AppError(400, "invalid_request", "media type is invalid");
    }
    for (unsigned char ch : value) {
        if (ch != '/' && !IsMediaTypeToken(ch)) {
            throw AppError(400, "invalid_request", "media type is invalid");
        }
    }
    std::string normalized = value;
    std::transform(normalized.begin(), normalized.end(), normalized.begin(),
                   [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return normalized;
}

UploadService::UploadService(MySqlPool& pool, ProjectService& projects,
                             FileStore& store, uint64_t maximum_file_bytes,
                             uint64_t chunk_size)
    : pool_(pool),
      projects_(projects),
      store_(store),
      maximum_file_bytes_(maximum_file_bytes),
      chunk_size_(chunk_size),
      repository_(new UploadRepository()) {
    UploadPartCount(0, chunk_size_);
}

UploadService::~UploadService() = default;

UploadTask UploadService::Create(const SessionContext& session,
                                 const std::string& project_id,
                                 const CreateUploadCommand& raw_command,
                                 const std::string& request_id) {
    RequireId(project_id);
    if (raw_command.size > maximum_file_bytes_) {
        throw AppError(413, "file_too_large",
                       "file exceeds the configured upload limit");
    }
    ValidateUploadSha256(raw_command.sha256);
    CreateUploadCommand command = raw_command;
    command.media_type = ValidateUploadMediaType(command.media_type);
    if (command.mode == UploadMode::CreateFile) {
        RequireId(command.directory_id);
        command.name = ValidateFileName(command.name);
        if (!command.file_id.empty() ||
            !command.observed_current_version_id.empty()) {
            throw AppError(400, "invalid_request",
                           "new-file upload targets are invalid");
        }
    } else {
        RequireId(command.file_id);
        RequireId(command.observed_current_version_id);
        if (!command.directory_id.empty() || !command.name.empty()) {
            throw AppError(400, "invalid_request",
                           "new-version upload targets are invalid");
        }
    }

    UploadTaskRecord record{
        UploadTask{GenerateId(), project_id, session.user_id, "uploading",
                   chunk_size_, UploadPartCount(command.size, chunk_size_), 0,
                   std::string(), std::string(), std::string(), std::string()},
        command.mode, command.directory_id, command.name, command.size,
        command.sha256, command.media_type, command.file_id,
        command.observed_current_version_id};
    MySqlConnection connection = pool_.Acquire();
    MySqlTransaction transaction(connection);
    projects_.RequireRole(connection, session.user_id, project_id,
                          Role::Editor);
    if (command.mode == UploadMode::CreateFile) {
        if (!repository_->DirectoryExists(connection, project_id,
                                          command.directory_id)) {
            ResourceNotFound();
        }
        if (repository_->ActiveNameExists(connection, project_id,
                                          command.directory_id,
                                          command.name)) {
            throw AppError(409, "name_conflict",
                           "an active file with this name already exists");
        }
    } else {
        std::string current_version;
        if (!repository_->FindActiveFileCurrentVersion(
                connection, project_id, command.file_id, &current_version)) {
            ResourceNotFound();
        }
        if (current_version != command.observed_current_version_id) {
            throw AppError(409, "version_conflict",
                           "the file current version has changed");
        }
    }
    repository_->InsertTask(connection, record);
    repository_->InsertAudit(connection, session.user_id, project_id,
                             "upload.create", record.task.id,
                             CheckedRequestId(request_id));
    transaction.Commit();
    return record.task;
}

Page<UploadTask> UploadService::ListOwn(const SessionContext& session,
                                        const std::string& project_id,
                                        const UploadListQuery& query) {
    RequireId(project_id);
    ValidateListQuery(query);
    MySqlConnection connection = pool_.Acquire();
    MySqlTransaction transaction(connection);
    projects_.RequireRole(connection, session.user_id, project_id,
                          Role::Reader);
    Page<UploadTask> result = repository_->ListOwn(
        connection, project_id, session.user_id, query);
    transaction.Commit();
    return result;
}

UploadTaskDetail UploadService::GetOwn(const SessionContext& session,
                                       const std::string& project_id,
                                       const std::string& task_id) {
    RequireId(project_id);
    RequireId(task_id);
    MySqlConnection connection = pool_.Acquire();
    MySqlTransaction transaction(connection);
    projects_.RequireRole(connection, session.user_id, project_id,
                          Role::Reader);
    UploadTaskRecord record;
    if (!repository_->FindOwn(connection, project_id, session.user_id, task_id,
                              false, &record)) {
        ResourceNotFound();
    }
    UploadTaskDetail result{record.task,
                            repository_->ListParts(connection, task_id)};
    transaction.Commit();
    return result;
}

void UploadService::Cancel(const SessionContext& session,
                           const std::string& project_id,
                           const std::string& task_id,
                           const std::string& request_id) {
    RequireId(project_id);
    RequireId(task_id);
    MySqlConnection connection = pool_.Acquire();
    MySqlTransaction transaction(connection);
    projects_.RequireRole(connection, session.user_id, project_id,
                          Role::Editor);
    UploadTaskRecord record;
    if (!repository_->FindOwn(connection, project_id, session.user_id, task_id,
                              true, &record)) {
        ResourceNotFound();
    }
    if (record.task.state == "cancelled") {
        transaction.Commit();
        return;
    }
    if (record.task.state != "uploading" && record.task.state != "failed" &&
        record.task.state != "interrupted") {
        throw AppError(409, "upload_state_conflict",
                       "upload task cannot be cancelled in its current state");
    }
    repository_->Cancel(connection, task_id);
    repository_->InsertAudit(connection, session.user_id, project_id,
                             "upload.cancel", task_id,
                             CheckedRequestId(request_id));
    transaction.Commit();
}

std::unique_ptr<PartWriter> UploadService::BeginPart(
    const SessionContext& session, const std::string& project_id,
    const std::string& task_id, uint32_t part_number, uint64_t content_length,
    const std::string& sha256) {
    RequireId(project_id);
    RequireId(task_id);
    ValidateUploadSha256(sha256);
    MySqlConnection connection = pool_.Acquire();
    MySqlTransaction transaction(connection);
    projects_.RequireRole(connection, session.user_id, project_id,
                          Role::Editor);
    UploadTaskRecord record;
    if (!repository_->FindOwn(connection, project_id, session.user_id, task_id,
                              true, &record)) {
        ResourceNotFound();
    }
    if (record.task.state != "uploading" &&
        record.task.state != "interrupted") {
        throw AppError(409, "upload_state_conflict",
                       "upload task is not accepting parts");
    }
    if (ExpectedUploadPartSize(record.expected_size, record.task.chunk_size,
                               part_number) != content_length) {
        throw AppError(400, "invalid_request",
                       "Content-Length does not match the expected part size");
    }
    PartInfo existing;
    if (repository_->FindPart(connection, task_id, part_number, &existing) &&
        (existing.size != content_length || existing.sha256 != sha256)) {
        throw AppError(409, "chunk_conflict",
                       "this part number already has different content");
    }
    if (record.task.state == "interrupted") {
        repository_->SetState(connection, task_id, "uploading");
    }
    transaction.Commit();
    return std::unique_ptr<PartWriter>(
        new PartWriter(store_.CreatePartWriter(task_id, part_number)));
}

PartResult UploadService::ConfirmPart(const SessionContext& session,
                                      const std::string& project_id,
                                      const std::string& task_id,
                                      const PartInfo& part,
                                      const std::string& request_id) {
    RequireId(project_id);
    RequireId(task_id);
    ValidateUploadSha256(part.sha256);
    MySqlConnection connection = pool_.Acquire();
    MySqlTransaction transaction(connection);
    projects_.RequireRole(connection, session.user_id, project_id,
                          Role::Editor);
    UploadTaskRecord record;
    if (!repository_->FindOwn(connection, project_id, session.user_id, task_id,
                              true, &record)) {
        ResourceNotFound();
    }
    RequireUploading(record);
    if (ExpectedUploadPartSize(record.expected_size, record.task.chunk_size,
                               part.part_number) != part.size) {
        throw AppError(409, "chunk_conflict",
                       "stored part size does not match the upload task");
    }
    PartInfo existing;
    if (repository_->FindPart(connection, task_id, part.part_number,
                              &existing)) {
        if (existing.size != part.size || existing.sha256 != part.sha256) {
            throw AppError(409, "chunk_conflict",
                           "this part number already has different content");
        }
        repository_->InsertAudit(connection, session.user_id, project_id,
                                 "upload.part", task_id,
                                 CheckedRequestId(request_id));
        transaction.Commit();
        return PartResult{existing.part_number, existing.size, existing.sha256,
                          true};
    }
    repository_->InsertPart(connection, task_id, part);
    repository_->InsertAudit(connection, session.user_id, project_id,
                             "upload.part", task_id,
                             CheckedRequestId(request_id));
    transaction.Commit();
    return PartResult{part.part_number, part.size, part.sha256, false};
}

CompleteUploadResult UploadService::Complete(
    const SessionContext& session, const std::string& project_id,
    const std::string& task_id, const std::string& request_id) {
    RequireId(project_id);
    RequireId(task_id);
    UploadTaskRecord task;
    std::vector<PartInfo> parts;
    bool claimed = false;
    for (int attempt = 0; attempt < kCompletionWaitAttempts; ++attempt) {
        {
            MySqlConnection connection = pool_.Acquire();
            MySqlTransaction transaction(connection);
            projects_.RequireRole(connection, session.user_id, project_id,
                                  Role::Editor);
            if (!repository_->FindOwn(connection, project_id, session.user_id,
                                      task_id, true, &task)) {
                ResourceNotFound();
            }
            if (task.task.state == "completed") {
                if (!repository_->CompletedGraphValid(connection, task)) {
                    throw AppError(500, "database_result_invalid",
                                   "completed upload result graph is invalid");
                }
                transaction.Commit();
                return StoredResult(task, true);
            }
            if (task.task.state == "assembling" ||
                task.task.state == "publishing") {
                transaction.Commit();
            } else {
                if (task.task.state != "uploading" &&
                    task.task.state != "interrupted") {
                    throw AppError(
                        409, "upload_state_conflict",
                        "upload task cannot be completed in its current state");
                }
                parts = repository_->ListParts(connection, task_id);
                if (parts.size() != task.task.part_count) {
                    throw AppError(409, "upload_parts_incomplete",
                                   "not all upload parts have been confirmed");
                }
                uint64_t total = 0;
                for (size_t index = 0; index < parts.size(); ++index) {
                    if (parts[index].part_number != index ||
                        parts[index].size != ExpectedUploadPartSize(
                            task.expected_size, task.task.chunk_size,
                            static_cast<uint32_t>(index)) ||
                        total > std::numeric_limits<uint64_t>::max() -
                                    parts[index].size) {
                        throw AppError(
                            409, "upload_parts_invalid",
                            "confirmed upload parts do not match the task");
                    }
                    total += parts[index].size;
                }
                if (total != task.expected_size) {
                    throw AppError(
                        409, "upload_parts_invalid",
                        "confirmed upload size does not match the task");
                }
                repository_->SetState(connection, task_id, "assembling");
                transaction.Commit();
                claimed = true;
            }
        }
        if (claimed) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    if (!claimed) {
        throw AppError(503, "upload_completion_in_progress",
                       "upload completion is still in progress", true);
    }

    StoredTemp assembled{};
    bool mismatch = false;
    {
        MySqlConnection connection = pool_.Acquire();
        MySqlTransaction transaction(connection);
        UploadTaskRecord current;
        if (!repository_->FindOwn(connection, project_id, session.user_id,
                                  task_id, true, &current)) {
            ResourceNotFound();
        }
        if (current.task.state != "assembling") {
            throw AppError(409, "upload_interrupted",
                           "upload completion was interrupted", true);
        }
        projects_.RequireRole(connection, session.user_id, project_id,
                              Role::Editor);
        assembled = store_.Assemble(task_id, parts);
        mismatch = assembled.size != current.expected_size ||
                   assembled.sha256 != current.expected_sha256;
        if (mismatch) {
            store_.RemoveTaskTemporaryFiles(task_id);
            repository_->SetFailed(connection, task_id, "whole_file_mismatch",
                                   "assembled upload size or SHA-256 did not match");
        } else {
            repository_->SetState(connection, task_id, "publishing");
        }
        transaction.Commit();
        task = current;
    }
    if (mismatch) {
        throw AppError(422, "whole_file_mismatch",
                       "assembled upload size or SHA-256 did not match");
    }

    try {
        MySqlConnection connection = pool_.Acquire();
        MySqlTransaction transaction(connection);
        UploadTaskRecord current;
        if (!repository_->FindOwn(connection, project_id, session.user_id,
                                  task_id, true, &current)) {
            ResourceNotFound();
        }
        if (current.task.state == "completed") {
            if (!repository_->CompletedGraphValid(connection, current)) {
                throw AppError(500, "database_result_invalid",
                               "completed upload result graph is invalid");
            }
            transaction.Commit();
            return StoredResult(current, true);
        }
        if (current.task.state != "publishing") {
            throw AppError(409, "upload_interrupted",
                           "upload publication was interrupted", true);
        }
        store_.PublishObject(assembled, task_id);
        projects_.RequireRole(connection, session.user_id, project_id,
                              Role::Editor);

        std::string file_id;
        uint64_t version_number = 1;
        if (current.mode == UploadMode::CreateFile) {
            if (!repository_->DirectoryExists(connection, project_id,
                                              current.directory_id)) {
                ResourceNotFound();
            }
            if (repository_->ActiveNameExists(connection, project_id,
                                              current.directory_id,
                                              current.expected_name)) {
                throw AppError(409, "name_conflict",
                               "an active file with this name already exists");
            }
            file_id = GenerateId();
            repository_->InsertFile(connection, file_id, current,
                                    session.user_id);
        } else {
            file_id = current.target_file_id;
            std::string current_version;
            if (!repository_->FindActiveFileCurrentVersion(
                    connection, project_id, file_id, &current_version, true)) {
                ResourceNotFound();
            }
            if (current_version != current.observed_current_version_id) {
                throw AppError(409, "version_conflict",
                               "the file current version has changed");
            }
            version_number =
                repository_->NextVersionNumber(connection, file_id);
        }

        const std::string version_id = GenerateId();
        const std::string job_id = GenerateId();
        repository_->InsertVersion(connection, version_id, file_id,
                                   version_number, task_id, current,
                                   session.user_id);
        repository_->SetCurrentVersion(connection, file_id, version_id);
        repository_->InsertProcessingJob(connection, job_id, version_id);
        repository_->InsertAudit(connection, session.user_id, project_id,
                                 "upload.complete", task_id,
                                 CheckedRequestId(request_id));
        repository_->SetCompleted(connection, task_id, file_id, version_id,
                                  job_id);
        FaultInjector::Hit(FaultPoint::BeforeDatabaseCommit);
        transaction.Commit();
        FaultInjector::Hit(FaultPoint::AfterDatabaseCommit);
        return CompleteUploadResult{file_id, version_id, job_id, false};
    } catch (const AppError& error) {
        if (error.code == "constraint_conflict" &&
            task.mode == UploadMode::CreateFile) {
            throw AppError(409, "name_conflict",
                           "an active file with this name already exists");
        }
        throw;
    }
}
