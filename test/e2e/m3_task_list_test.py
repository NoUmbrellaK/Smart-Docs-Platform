#!/usr/bin/env python3
"""Real HTTP task discovery, owner isolation and keyset pagination; no model calls."""
import os
import secrets
import subprocess
import unittest

from m1_http_test import HttpClient, create_user, data, login
from m2_worker_test import ROOT, WorkerE2E


class TaskListE2E(WorkerE2E):
    def tasks_path(self):
        return f'/api/v1/projects/{self.project["id"]}/agent-tasks'

    def approved_file(self):
        job = self.upload("PRIVATE-FILENAME.txt", b"SOURCE-MARKER", "text/plain")
        self.policy(job, True)
        return job

    def policy(self, job, approved):
        path = f'/api/v1/projects/{self.project["id"]}/files/{job["file_id"]}/remote-ai-policy'
        data(self.user.request("PUT", path, {"policy": "remote_ai_allowed",
             "approved_version_ids": [job["version_id"]] if approved else []}))

    def create_task(self, job, client=None):
        return data((client or self.user).request("POST", self.tasks_path(), {
            "question": "QUESTION-MARKER", "confirm_remote_ai": True,
            "versions": [{"file_id": job["file_id"], "version_id": job["version_id"]}]}), 201)

    def sql(self, statement):
        # WorkerE2E requires a disposable /tmp MySQL socket before this is reachable.
        subprocess.run(["mysql", "--protocol=SOCKET", "--socket="+os.environ["SMARTDOCS_MYSQL_SOCKET"],
                        "--user=root", "smart_docs_test", "--execute", statement], check=True,
                       capture_output=True, text=True, timeout=10)

    def test_task_list_stable_pages_and_restart_without_content_or_claim(self):
        job = self.approved_file()
        tasks = [self.create_task(job) for _ in range(23)]
        # Equal timestamps exercise the ID tie-breaker independently of clock precision.
        self.sql("UPDATE agent_tasks SET created_at='2020-01-01 00:00:00.000000' "
                 "WHERE project_id='"+self.project["id"]+"'")
        ids = sorted((task["id"] for task in tasks), reverse=True)
        older = [self.create_task(job) for _ in range(2)]
        for task in older:
            self.sql("UPDATE agent_tasks SET created_at='2019-01-01 00:00:00.000000' WHERE id='"+task["id"]+"'")
        ids += sorted((task["id"] for task in older), reverse=True)
        path = self.tasks_path()
        first = data(self.user.request("GET", path))
        self.assertEqual(set(first), {"items", "page_size", "has_more", "next_before"})
        self.assertEqual([item["id"] for item in first["items"]], ids[:20])
        self.assertEqual((first["page_size"], first["has_more"], first["next_before"]), (20, True, ids[19]))
        newer = self.create_task(job)
        second = data(self.user.request("GET", path+"?before="+first["next_before"]))
        self.assertEqual([item["id"] for item in second["items"]], ids[20:])
        self.assertEqual((second["has_more"], second["next_before"]), (False, None))
        for item in first["items"]+second["items"]:
            self.assertEqual(set(item), {"id", "project_id", "state", "created_at", "updated_at", "confirmed_at", "versions"})
            self.assertEqual(item["state"], "pending")
            self.assertEqual(item["project_id"], self.project["id"])
            self.assertEqual(item["versions"], [{"file_id": job["file_id"], "version_id": job["version_id"]}])
        data(self.user.request("POST", path+"/"+newer["id"]+"/cancel"))
        self.policy(job, False)
        self.server.stop()
        self.server.start(environment_overrides={"SMARTDOCS_WORKER_TOKEN": self.token})
        self.user = HttpClient(self.server.port)
        login(self.user, "worker-e2e-admin", self.password)
        response = self.user.request("GET", path)
        items = data(response)["items"]
        self.assertEqual((items[0]["id"], items[0]["state"]), (newer["id"], "cancelled"))
        self.assertTrue(all(item["state"] == "pending" for item in items[1:]))
        for marker in (b"QUESTION-MARKER", b"SOURCE-MARKER", b"PRIVATE-FILENAME", self.token.encode()):
            self.assertNotIn(marker, response.body)
        tail = data(self.user.request("GET", path+"?before="+ids[-1]))
        self.assertEqual(tail, {"items": [], "page_size": 20, "has_more": False, "next_before": None})

    def test_task_list_owner_project_and_membership_isolation(self):
        job = self.approved_file()
        own = self.create_task(job)
        name = "task-reader-"+secrets.token_hex(4)
        uid = create_user(str(ROOT/"bin/smartdocs-admin"), name, self.password)
        member = f'/api/v1/projects/{self.project["id"]}/members/{uid}'
        data(self.user.request("PUT", member, {"role": "reader"}))
        reader = HttpClient(self.server.port)
        login(reader, name, self.password)
        other = self.create_task(job, reader)
        path = self.tasks_path()
        self.assertEqual([x["id"] for x in data(self.user.request("GET", path))["items"]], [own["id"]])
        self.assertEqual([x["id"] for x in data(reader.request("GET", path))["items"]], [other["id"]])
        foreign = self.user.request("GET", path+"?before="+other["id"])
        missing = self.user.request("GET", path+"?before="+"0"*32)
        self.assertEqual((foreign.status, missing.status), (404, 404))
        self.assertEqual(foreign.json()["error"], missing.json()["error"])
        self.assertEqual(reader.request("GET", path+"?before="+own["id"]).status, 404)
        project2 = data(self.user.request("POST", "/api/v1/projects", {"name": "Other"}), 201)
        path2 = f'/api/v1/projects/{project2["id"]}/agent-tasks'
        self.assertEqual(data(self.user.request("GET", path2))["items"], [])
        self.assertEqual(self.user.request("GET", path2+"?before="+own["id"]).status, 404)
        self.assertEqual(reader.request("GET", path2).status, 404)
        self.sql("DELETE FROM project_members WHERE project_id='"+self.project["id"]+"' AND user_id='"+uid+"'")
        self.assertEqual(reader.request("GET", path).status, 404)
        self.assertEqual(HttpClient(self.server.port).request("GET", path).status, 401)

    def test_task_list_rejects_ambiguous_queries_and_bodies(self):
        path = self.tasks_path()
        self.assertEqual(data(self.user.request("GET", path))["items"], [])
        queries = ["before=", "before=abc", "before="+"A"*32, "before="+"0"*31,
                   "before="+"0"*33, "before=%30"+"0"*31,
                   "before="+"0"*32+"&before="+"1"*32, "before="+"0"*32+"&",
                   "user_id="+"0"*32, "project_id="+"0"*32, "page=1", "page_size=100"]
        for query in queries:
            with self.subTest(query=query):
                self.assertEqual(self.user.request("GET", path+"?"+query).status, 400)
        self.assertEqual(self.user.request("GET", path, {"before": "0"*32}).status, 400)


if __name__ == "__main__":
    suite = unittest.TestSuite(TaskListE2E(name) for name in dir(TaskListE2E) if name.startswith("test_task_list_"))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
