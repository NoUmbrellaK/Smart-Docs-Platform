#include "project/project_service.h"
#include "../test_support.h"

TEST_CASE(authorization_role_matrix_is_explicit) {
    CHECK(Authorize(Role::Reader, Action::ReadProject));
    CHECK(!Authorize(Role::Reader, Action::UploadFile));
    CHECK(!Authorize(Role::Reader, Action::MutateFile));
    CHECK(!Authorize(Role::Reader, Action::ManageMembers));
    CHECK(!Authorize(Role::Reader, Action::ManageDirectories));
    CHECK(!Authorize(Role::Reader, Action::ManageRemoteAiPolicy));

    CHECK(Authorize(Role::Editor, Action::ReadProject));
    CHECK(Authorize(Role::Editor, Action::UploadFile));
    CHECK(Authorize(Role::Editor, Action::MutateFile));
    CHECK(!Authorize(Role::Editor, Action::ManageMembers));
    CHECK(!Authorize(Role::Editor, Action::ManageDirectories));
    CHECK(!Authorize(Role::Editor, Action::ManageRemoteAiPolicy));

    CHECK(Authorize(Role::Admin, Action::ReadProject));
    CHECK(Authorize(Role::Admin, Action::UploadFile));
    CHECK(Authorize(Role::Admin, Action::MutateFile));
    CHECK(Authorize(Role::Admin, Action::ManageMembers));
    CHECK(Authorize(Role::Admin, Action::ManageDirectories));
    CHECK(Authorize(Role::Admin, Action::ManageRemoteAiPolicy));
}
