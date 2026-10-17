"""Real local keyword/version regression; no browser or encoder required."""
import unittest

from m1_http_test import complete, create_upload, data, upload_parts
from m2_worker_test import WorkerE2E
from smartdocs import worker


class SearchVersionE2E(WorkerE2E):
    def test_ngram_extra_match_does_not_hide_current_version_or_restore_old_one(self):
        old = self.upload("recovery.txt",
            b"Database disaster recovery: restore the latest backup to recover lost data.", "text/plain")
        internal = self.upload("internal.txt",
            b"Internal backup material must not enter hybrid results.", "text/plain")
        for job in (old, internal):
            self.assertEqual(worker.run_once(self.client), "completed")

        path = f'/api/v1/projects/{self.project["id"]}/search'
        def search(mode):
            result = data(self.user.request("POST", path, {"mode": mode, "query": "Replacement"}))
            self.assertEqual(result["retrieval"], "keyword_only")
            self.assertFalse(result["has_more"])
            return result["items"]

        replacement = b"Replacement release notes without the prior recovery instructions."
        task = create_upload(self.user, self.project["id"], self.project["root_directory_id"],
            "recovery.txt", replacement, "text/plain", old["file_id"], old["version_id"])
        upload_parts(self.user, self.project["id"], task, replacement)
        latest = data(complete(self.user, self.project["id"], task["task_id"]))
        self.jobs.append(latest)
        self.assertEqual(self.document(latest)["parse_state"], "pending")
        # A new unparsed version must remove the old body, not reuse its index.
        self.assertNotIn(old["file_id"], [item["file_id"] for item in search("keyword")])
        self.assertEqual(worker.run_once(self.client), "completed")

        hits = search("keyword")
        identities = {(item["file_id"], item["version_id"]) for item in hits}
        self.assertIn((latest["file_id"], latest["version_id"]), identities)
        self.assertNotIn(old["version_id"], [item["version_id"] for item in hits])
        # Characterize the surprising fixed corpus: ngram is not exact substring.
        self.assertIn((internal["file_id"], internal["version_id"]), identities)
        self.assertEqual(len(hits), 2)
        self.assertEqual(identities, {(latest["file_id"], latest["version_id"]),
                                     (internal["file_id"], internal["version_id"])})
        self.assertEqual(search("hybrid"), [])
        print("PASS real keyword API: Replacement returns current recovery + internal ngram match; "
              "old version excluded before/after parsing; unapproved versions excluded from hybrid", flush=True)


if __name__ == "__main__":
    suite = unittest.TestSuite([SearchVersionE2E(
        "test_ngram_extra_match_does_not_hide_current_version_or_restore_old_one")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
