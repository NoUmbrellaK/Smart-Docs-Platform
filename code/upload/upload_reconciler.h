#pragma once

class FileStore;
class MySqlPool;

class UploadReconciler {
public:
    UploadReconciler(MySqlPool& pool, FileStore& store);
    void RunAtStartup();

private:
    MySqlPool& pool_;
    FileStore& store_;
};
