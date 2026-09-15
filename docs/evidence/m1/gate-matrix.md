# M1 gate coverage matrix

Status as reviewed on 2026-09-15: **M1 remains open.** This matrix describes
test design and the evidence still needed; it is not an acceptance result.

The source requirements are [F-01 through F-04 and T-01 through T-09](../../../团队文档管理与智能整理平台_需求文档.md).
The stage boundary is the same document's section 14: M1 covers project roles,
directories, upload/download, versions, resumable chunks, and preview; parsing,
search, citations, Agent tools, and reports belong to M2-M4.

## How to read the matrix

- **Direct** means a named test drives and asserts the M1 behavior.
- **Indirect** means only a component, static contract, simulation, or adjacent
  behavior is tested. It cannot close the gate.
- **Missing** means the required measured or end-to-end proof does not exist.
- **Deferred M2+** means the clause is outside M1 and is not counted as an M1
  pass or failure.
- **Current evidence pending** means the test design exists, but there is no
  accepted, complete run bound to the current HEAD. It applies to every M1 row
  below, including rows whose design coverage is direct.

## F-01: project space and file management

| ID | Requirement clause and stage | Design coverage | Concrete test or evidence | Run evidence |
| --- | --- | --- | --- | --- |
| F-01.1 | M1: record project, logical directory, name, size, creator, updated time, and current version | Mixed: direct for scope, directory, name, size, and current/exact versions; indirect/missing for creator and timestamps | `file_metadata_listing_is_scoped_paginated_and_deletion_explicit` and `file_metadata_versions_open_only_the_exact_authorized_object` in [file_metadata_test.cpp](../../../test/integration/file_metadata_test.cpp) (`:136-179`, `:209-247`) cover the directly asserted fields; creator and timestamps rely on schema/write-path structure rather than a named response-field assertion | Current evidence pending; creator/timestamp direct proof missing |
| F-01.2 | M1: logical directories; never use an untrusted user path to access content | Direct | `file_store_rejects_untrusted_paths_and_removes_unfinished_writes` in [file_store_test.cpp](../../../test/unit/file_store_test.cpp) (`:137-168`); the shell asset allowlist in [webserver_socket_test.cpp](../../../test/unit/webserver_socket_test.cpp) (`:134-166`) | Current evidence pending |
| F-01.3 | M1: move only within one project; keep file/version IDs stable | Direct | `file_mutation_enforces_roles_project_scope_conflicts_and_stable_ids` in [file_mutation_test.cpp](../../../test/integration/file_mutation_test.cpp) (`:153-188`) and HTTP T-08 in [m1_http_test.py](../../../test/e2e/m1_http_test.py) (`:473-490`); cross-project movement is rejected, not implemented | Current evidence pending |
| F-01.4 | M1: report same-directory name conflicts; attach to an existing file only after explicit new-version selection | Direct | `upload_part_creation_rejects_name_and_version_conflicts` in [upload_part_test.cpp](../../../test/integration/upload_part_test.cpp) (`:191-240`), completion conflict checks in [upload_complete_test.cpp](../../../test/integration/upload_complete_test.cpp) (`:290-316`), and the two explicit UI modes in [app.js](../../../resources/js/app.js) (`:618-643`) | Current evidence pending; no accepted TCP artifact for the conflict path |
| F-01.5a | M1: web soft-delete/restore; hide immediately from the default list; do not auto-purge | Mixed: direct for delete/restore/default-list hiding; indirect/missing for retention and no auto-purge | HTTP T-08 in [m1_http_test.py](../../../test/e2e/m1_http_test.py) (`:491-503`) and delete/restore controls in [app.html](../../../resources/app.html) (`:90-99`) cover the interactive behavior; no named test asserts retention over time or absence of automatic purge | Current evidence pending; retention/no-auto-purge direct proof missing |
| F-01.5b | M2: remove a deleted file from search immediately | Deferred M2+ | Search is assigned to M2 by requirement section 14; the [M1 API boundary](../../api/m1-http-api.md) explicitly excludes search | Not an M1 gate numerator |
| F-01.6 | M3: Agent must not delete, overwrite, move across projects, or run arbitrary system commands | Deferred M2+ | Agent/MCP is assigned to M3. M1's cross-project rejection in [file_mutation_test.cpp](../../../test/integration/file_mutation_test.cpp) (`:176-179`) is only a prerequisite, not Agent proof | Not an M1 gate numerator |

## F-02: chunked upload and resume

| ID | Requirement clause and stage | Design coverage | Concrete test or evidence | Run evidence |
| --- | --- | --- | --- | --- |
| F-02.1 | M1: create a task returning task ID, chunk size, and confirmed parts | Direct | Creation asserts task ownership/state, `chunk_size`, and empty confirmed parts in [upload_part_test.cpp](../../../test/integration/upload_part_test.cpp) (`:145-153`); `upload_part_http_streams_and_replays_identical_content` adds HTTP creation/detail context (`:243-275`, `:330-344`), and the [HTTP API task contract](../../api/m1-http-api.md) documents the response | Current evidence pending |
| F-02.2 | M1: query confirmed parts, send only missing parts after interruption, and resume after browser refresh by reselecting the original file | Indirect; browser proof missing | HTTP T-03 directly covers server restart, confirmed parts, missing-part upload, and final hash in [m1_http_test.py](../../../test/e2e/m1_http_test.py) (`:344-371`). [uploads.js](../../../resources/js/uploads.js) (`:208-234`, `:359-395`, `:421-435`) implements refresh restoration, file reselection checks, and missing-part enumeration, but no real-browser/real-service test covers the complete chain | Current evidence pending; real browser refresh-resume-reselect proof missing |
| F-02.3 | M1: validate task owner, project, file size, chunk metadata, and reject a mismatched reselected file | Direct for server; UI reselect is simulated/static | Ownership, scope, limits, length, type, digest, number, and non-owner checks in [upload_part_test.cpp](../../../test/integration/upload_part_test.cpp) (`:121-183`, `:302-328`); file size/SHA reselect checks in [uploads.js](../../../resources/js/uploads.js) (`:359-395`) | Current evidence pending |
| F-02.4 | M1: same task/number/content is reused; different content conflicts | Direct | HTTP T-02 in [m1_http_test.py](../../../test/e2e/m1_http_test.py) (`:325-342`) and replay/conflict integration coverage in [upload_part_test.cpp](../../../test/integration/upload_part_test.cpp) (`:280-300`) | Current evidence pending |
| F-02.5 | M1: acknowledge a part only after durable completion; never confirm a half-written part | Direct | `FAULT_ROUNDS` and `AfterPartTempFsync` recovery assertions in [m1_interrupt_test.py](../../../test/e2e/m1_interrupt_test.py) (`:20-27`, `:389-420`, `:454-461`) and unfinished-writer cleanup in [file_store_test.cpp](../../../test/unit/file_store_test.cpp) (`:137-153`) | Current evidence pending; the visible 20-round artifact is from an older commit |
| F-02.6 | M1: check part count/order and whole-file SHA-256; do not publish on failure | Direct | `file_store_detects_part_order_size_and_digest_mismatches` in [file_store_test.cpp](../../../test/unit/file_store_test.cpp) (`:171-195`) and `upload_complete_validates_parts_and_whole_file_before_publication` in [upload_complete_test.cpp](../../../test/integration/upload_complete_test.cpp) (`:137-201`) | Current evidence pending |
| F-02.7 | M1: completion is idempotent and returns stable file/version/job IDs | Direct | HTTP T-04 in [m1_http_test.py](../../../test/e2e/m1_http_test.py) (`:373-392`), idempotent/zero-byte and concurrent-single-graph cases in [upload_complete_test.cpp](../../../test/integration/upload_complete_test.cpp) (`:204-287`) | Current evidence pending |
| F-02.8 | M1: interrupted completion exposes no partial object; restart reconciles records and disk | Direct | Six fault points, interruption/publication/singleton proof, and the 20-round loop in [m1_interrupt_test.py](../../../test/e2e/m1_interrupt_test.py) (`:20-27`, `:361-503`, `:533-539`); the harness leak detector in [m1_harness_self_test.py](../../../test/e2e/m1_harness_self_test.py) (`:278-382`) | Current evidence pending; the visible 20-round artifact is from an older commit |
| F-02.9 | M1: upload, verification, and download stream without buffering whole files per concurrent request | Indirect; measured proof missing | Incremental writer/assembly in [file_store_test.cpp](../../../test/unit/file_store_test.cpp) (`:88-119`), chunked upload handler coverage in [upload_part_test.cpp](../../../test/integration/upload_part_test.cpp) (`:243-300`), and file-region download in [file_download_test.cpp](../../../test/integration/file_download_test.cpp) (`:265-290`) show streaming structure only | Current evidence pending; no peak RSS, file-size, chunk-size, and concurrency measurement |

## F-03: download and PDF preview

| ID | Requirement clause and stage | Design coverage | Concrete test or evidence | Run evidence |
| --- | --- | --- | --- | --- |
| F-03.1 | M1: authorize ordinary and Range downloads; no public-static bypass | Direct | HTTP T-01 in [m1_http_test.py](../../../test/e2e/m1_http_test.py) (`:301-323`) and shell asset allowlist in [webserver_socket_test.cpp](../../../test/unit/webserver_socket_test.cpp) (`:134-166`) | Current evidence pending |
| F-03.2 | M1: support closed, open-ended, and suffix single ranges with correct range/total length | Direct | HTTP T-05 in [m1_http_test.py](../../../test/e2e/m1_http_test.py) (`:394-412`) and `range_parses_closed_open_and_suffix_forms_with_clamping` in [range_test.cpp](../../../test/unit/range_test.cpp) (`:34-45`) | Current evidence pending |
| F-03.3 | M1: return an explicit error for invalid ranges and do not fake multi-range success | Direct | HTTP T-05 in [m1_http_test.py](../../../test/e2e/m1_http_test.py) (`:413-423`) and response coverage in [file_download_test.cpp](../../../test/integration/file_download_test.cpp) (`:301-312`) | Current evidence pending |
| F-03.4 | M1: bind a transfer/preview to one immutable version | Direct | `file_download_current_binds_one_immutable_version_and_history_is_exact` in [file_download_test.cpp](../../../test/integration/file_download_test.cpp) (`:153-193`) and HTTP T-06 in [m1_http_test.py](../../../test/e2e/m1_http_test.py) (`:433-456`) | Current evidence pending |
| F-03.5 | M1: a real PDF component renders and navigates to a requested page; Range support alone is insufficient | Indirect; browser proof missing | The iframe/page controls in [app.html](../../../resources/app.html) (`:100-110`), URL fragment assignment in [app.js](../../../resources/js/app.js) (`:720-730`), and HTTP T-05 bytes/header/fragment checks in [m1_http_test.py](../../../test/e2e/m1_http_test.py) (`:394-431`) do not demonstrate actual browser rendering or page navigation | Current evidence pending; real browser PDF render/page-flip proof missing |
| F-03.6 | M1: non-preview content is an attachment; HTML/script uploads are not executable platform pages | Direct | `file_download_http_serves_ranges_safe_disposition_pdf_and_errors` in [file_download_test.cpp](../../../test/integration/file_download_test.cpp) (`:229-290`) checks attachment plus `nosniff`; [webserver_socket_test.cpp](../../../test/unit/webserver_socket_test.cpp) (`:134-166`) checks the static allowlist | Current evidence pending |

## F-04: file versions

| ID | Requirement clause and stage | Design coverage | Concrete test or evidence | Run evidence |
| --- | --- | --- | --- | --- |
| F-04.1 | M1: never overwrite an original version; advance current; keep old versions downloadable | Direct | HTTP T-06 and T-07 in [m1_http_test.py](../../../test/e2e/m1_http_test.py) (`:433-471`) and immutable-version coverage in [file_download_test.cpp](../../../test/integration/file_download_test.cpp) (`:153-193`) | Current evidence pending |
| F-04.2 | M2/M3: chunks, keyword index, vectors, and citations bind file ID plus version ID | Deferred M2+ | Parsing/index/search/citations start in M2 and Agent usage in M3. The [M1 API](../../api/m1-http-api.md) creates only a pending processing job | Not an M1 gate numerator |
| F-04.3 | M2/M3: historical citations open the original version and never silently jump to a newer one | Deferred M2+ | HTTP T-06 in [m1_http_test.py](../../../test/e2e/m1_http_test.py) (`:433-456`) proves the exact-version URL prerequisite only; M1 has no citation object or citation-click flow | Not an M1 gate numerator |
| F-04.4a | M1: delete, restore, move, and permission changes affect subsequent file access checks | Direct | HTTP T-08 in [m1_http_test.py](../../../test/e2e/m1_http_test.py) (`:473-521`) and deleted historical-content rejection in [file_download_test.cpp](../../../test/integration/file_download_test.cpp) (`:196-226`) | Current evidence pending |
| F-04.4b | M2-M4: a historical citation to a deleted file displays unavailable state | Deferred M2+ | M1's content rejection is only a prerequisite; no citation UI/state object exists yet | Not an M1 gate numerator |

## T-01 through T-09

| ID | M1 obligation or precursor | Design coverage | Deferred clause | Run evidence |
| --- | --- | --- | --- | --- |
| T-01 | Project/role restrictions and ID substitution: HTTP `T-01` in [m1_http_test.py](../../../test/e2e/m1_http_test.py) (`:301-323`) directly rejects unauthorized list, download, Range, and the exact-version content route used by preview; [auth_project_test.cpp](../../../test/integration/auth_project_test.cpp) (`:164-237`) covers project permissions; [file_metadata_test.cpp](../../../test/integration/file_metadata_test.cpp) (`:269-275`) rejects unauthorized exact-version opening at the service layer | Direct for list/download/Range, ID substitution, and authorization of the exact-version content route used by preview over HTTP; this does not prove real-browser PDF rendering | Search, tools, and reports are M2-M4 | Current evidence pending under the current-HEAD full-artifact blocker; real-browser PDF rendering/page navigation remains under blocker 3 |
| T-02 | Replayed identical chunk is reused; different content conflicts: HTTP `T-02` (`:325-342`) in [m1_http_test.py](../../../test/e2e/m1_http_test.py) | Direct | None | Current evidence pending |
| T-03 | Server restart, true confirmed-parts state, missing-part upload, final hash, and half-part retry: HTTP `T-03` in [m1_http_test.py](../../../test/e2e/m1_http_test.py) (`:344-371`) plus `AfterPartTempFsync` in [m1_interrupt_test.py](../../../test/e2e/m1_interrupt_test.py) (`:389-420`, `:454-461`) | Direct for server restart; indirect/missing for browser refresh and reselect | None; browser refresh is an M1 obligation, not deferred | Current evidence pending; real browser refresh-resume-reselect proof missing |
| T-04 | Repeated/lost completion returns the same IDs and hides partial content: HTTP `T-04` in [m1_http_test.py](../../../test/e2e/m1_http_test.py) (`:373-392`) plus commit/response fault points in [m1_interrupt_test.py](../../../test/e2e/m1_interrupt_test.py) (`:361-503`) | Direct | “Not searchable” is M2; M1 proves list/download invisibility only | Current evidence pending |
| T-05 | Closed/open/suffix and invalid Range behavior: HTTP `T-05` in [m1_http_test.py](../../../test/e2e/m1_http_test.py) (`:394-423`) | Direct for Range; indirect/missing for PDF rendering/page navigation | None; real PDF UI is an M1 obligation | Current evidence pending; real browser PDF render/page-flip proof missing |
| T-06 | Exact historical/current version URLs and immutable transfer: HTTP `T-06` in [m1_http_test.py](../../../test/e2e/m1_http_test.py) (`:433-456`) plus [file_download_test.cpp](../../../test/integration/file_download_test.cpp) (`:153-193`) | Direct for the M1 version prerequisite | Default search and citation behavior are M2/M3 | Current evidence pending |
| T-07 | M1 precursor: an older upload cannot replace current, HTTP `T-07` in [m1_http_test.py](../../../test/e2e/m1_http_test.py) (`:458-471`) | Direct precursor only | The specified stale parsing/default-index scenario is M2 | Current evidence pending for the precursor; deferred clause is not an M1 numerator |
| T-08 | List/download follow move, delete, restore, and membership changes: HTTP `T-08` in [m1_http_test.py](../../../test/e2e/m1_http_test.py) (`:473-521`) | Direct for M1 list/download state | Search/cache are M2, tools are M3, and reports/implicit report context are M4 | Current evidence pending |
| T-09 | M1 exposes pending/processing/failed status and does not claim parsing success: HTTP `T-09` in [m1_http_test.py](../../../test/e2e/m1_http_test.py) (`:523-538`) | Direct precursor only | Scanned/damaged/oversized parsing and keyword/vector fallback are M2 | Current evidence pending for the precursor; deferred clause is not an M1 numerator |

## Current closure blockers

| # | Status | Blocker and evidence |
| --- | --- | --- |
| 1 | Missing | No accepted full gate artifact exists for the checkout being documented. The only visible `docs/evidence/m1/latest/results.json` is bound to `a33fb99715ddea0c657f61ca5a2914baca2cb6d8`; the documented checkout is newer/current and lacks an accepted matching artifact. `latest/environment.txt`, `latest/summary.md`, and all reviewed `verified/` files are absent. |
| 2 | Missing | No measured streaming evidence reports peak RSS together with file size, chunk size, and concurrency, so F-02.9 cannot be closed. |
| 3 | Missing | No real-browser evidence shows the PDF fixture rendered or proves page-flip/page-location behavior, so F-03.5/T-05 cannot be closed. |
| 4 | Missing | No real-browser + real-HTTP proof covers reload, task recovery, original-file reselection, missing-part-only upload, mismatch rejection, and final hash, so F-02.2/T-03 cannot be closed. |
| 5 | Pending | The [stage runner](../../../test/e2e/run_m1.sh) now runs the MySQL-enabled `bin/smartdocs_tests`, Python UI contract, and Node UI behavior suites before production scenarios, aggregates each suite's passed/failed/skipped/total counts, and requires every selected test to pass without skips. Runtime proof remains pending until a complete current-HEAD artifact is accepted. |
| 6 | Missing | F-01.1 has no named direct assertion for creator and updated-time fields in the returned file metadata. Schema/write-path presence is indirect evidence only. |
| 7 | Missing | F-01.5a has no direct retention/no-auto-purge assertion. Delete, restore, and immediate default-list hiding do not prove that a background or later path never permanently removes the record/content. |

Until all M1 obligations have direct proof and an accepted current-HEAD evidence
set, this matrix must continue to report **M1 open**.
