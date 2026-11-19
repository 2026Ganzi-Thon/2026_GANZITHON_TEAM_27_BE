"""Community integration checks against a dedicated PostgreSQL test database.

Apply db/schema-demo.sql (or all migrations) to the test database first, then run:
TEST_DATABASE_URL=... python -m unittest discover -s tests -v
Only synthetic records owned by a fresh random demo ID are created and removed.
"""

import asyncio
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import Database
from app.main import app


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is required")
class CommunityIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(os.environ, {
            "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
            "AI_PROVIDER": "mock",
            "DEMO_IDENTITY_MODE": "header",
            "MAX_CASES_PER_IP": "0",
            "BOARD_AUTO_PUBLISH": "false",
        })
        cls.environment.start()
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.db = Database(os.environ["TEST_DATABASE_URL"])

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.environment.stop()

    def setUp(self):
        self.owner = str(uuid4())
        self.guest = str(uuid4())
        self.owner_headers = {"X-Demo-Id": self.owner}
        self.guest_headers = {"X-Demo-Id": self.guest}

    def tearDown(self):
        # Every fixture uses one of these two fresh IDs, including standalone posts.
        self.sql("DELETE FROM court_demo.case_posts WHERE demo_id IN (%s, %s)",
                 (self.owner, self.guest))
        self.sql("DELETE FROM court_demo.cases WHERE demo_id IN (%s, %s)",
                 (self.owner, self.guest))
        self.sql("DELETE FROM court_demo.case_number_counters WHERE demo_id IN (%s, %s)",
                 (self.owner, self.guest))

    def sql(self, statement, params=(), *, rows=False):
        async def execute():
            async with self.db.connection() as connection:
                cursor = await connection.execute(statement, params)
                return await cursor.fetchall() if rows else None
        return asyncio.run(execute())

    def call(self, method, path, status=200, *, guest=False, body=None):
        response = self.client.request(
            method, "/api/v1" + path,
            headers=self.guest_headers if guest else self.owner_headers, json=body,
        )
        self.assertEqual(response.status_code, status, (method, path, response.text))
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        return response.json() if status != 204 else None

    def create_post(self, *, publish=True, protected=False, guest=False, title="전시용 예시"):
        body = {"title": title, "body": "오늘의 생각을 정리했어요.", "tags": ["일상"],
                "visibility": "password_protected" if protected else "public"}
        if protected:
            body["password"] = "synthetic-test-password"
        post = self.call("POST", "/board/posts", 201, guest=guest, body=body)
        self.assertEqual(post["status"], "pending_review")
        if publish:
            self.publish(post["id"])
        return post["id"]

    def publish(self, post_id):
        self.sql("UPDATE court_demo.case_posts SET status='published' WHERE id=%s", (post_id,))

    def create_comment(self, post_id, *, guest=False, body="저도 비슷했어요."):
        return self.call("POST", f"/board/posts/{post_id}/comments", 201,
                         guest=guest, body={"body": body})

    def test_standalone_ownership_feed_and_search(self):
        post_id = self.create_post(publish=False)
        path = f"/board/posts/{post_id}"
        own = self.call("GET", path)
        self.assertTrue(own["is_mine"])
        self.assertEqual(own["author_label"], "익명")
        self.assertNotIn("demo_id", own)
        self.assertNotIn("case_id", own)
        self.call("GET", path, 404, guest=True)
        self.call("DELETE", path, 404, guest=True)
        mine = self.call("GET", "/board/posts/mine")
        self.assertEqual([p["id"] for p in mine["items"]], [post_id])
        self.assertEqual(self.call("GET", "/board/posts/mine", guest=True)["items"], [])
        self.publish(post_id)
        published = self.call("GET", path, guest=True)
        self.assertFalse(published["is_mine"])
        self.assertEqual((published["like_count"], published["comment_count"]), (0, 0))
        feed = self.call("GET", "/board/posts?tag=일상&q=전시용", guest=True)
        self.assertIn(post_id, [p["id"] for p in feed["items"]])
        self.assertEqual(self.call("GET", "/board/posts?q=%27%20OR%201%3D1--")["items"], [])

    def test_post_likes_are_idempotent_and_viewer_specific(self):
        post_id = self.create_post()
        path = f"/board/posts/{post_id}"
        for _ in range(2):
            self.assertEqual(self.call("PUT", path + "/like", guest=True),
                             {"liked": True, "like_count": 1})
        self.assertTrue(self.call("GET", path, guest=True)["liked_by_me"])
        self.assertFalse(self.call("GET", path)["liked_by_me"])
        self.assertEqual(self.call("PUT", path + "/like")["like_count"], 2)
        for _ in range(2):
            self.assertEqual(self.call("DELETE", path + "/like", guest=True),
                             {"liked": False, "like_count": 1})
        self.assertEqual(self.call("DELETE", path + "/like")["like_count"], 0)

    def test_comments_pagination_edit_ownership_and_cascade(self):
        post_id = self.create_post()
        first = self.create_comment(post_id)
        second = self.create_comment(post_id, guest=True)
        third = self.create_comment(post_id, body="천천히 생각해 볼게요.")
        ids, cursor = [], None
        for _ in range(4):
            suffix = "?limit=1" + (f"&cursor={cursor}" if cursor else "")
            page = self.call("GET", f"/board/posts/{post_id}/comments" + suffix)
            ids.extend(item["id"] for item in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(ids, [first["id"], second["id"], third["id"]])
        self.assertEqual(self.call("GET", f"/board/posts/{post_id}")["comment_count"], 3)
        path = f"/board/comments/{first['id']}"
        self.call("PUT", path, 404, guest=True, body={"body": "타인의 수정"})
        self.call("DELETE", path, 404, guest=True)
        edited = self.call("PUT", path, body={"body": "내가 고친 문장"})
        self.assertEqual(edited["body"], "내가 고친 문장")
        self.assertEqual(edited["created_at"], first["created_at"])
        self.assertTrue(edited["is_mine"])
        for _ in range(2):
            self.assertEqual(self.call("PUT", path + "/like", guest=True),
                             {"liked": True, "like_count": 1})
        comment_page = self.call("GET", f"/board/posts/{post_id}/comments", guest=True)
        self.assertTrue(comment_page["items"][0]["liked_by_me"])
        self.assertFalse(comment_page["items"][0]["is_mine"])
        for _ in range(2):
            self.assertEqual(self.call("DELETE", path + "/like", guest=True),
                             {"liked": False, "like_count": 0})
        self.call("PUT", path + "/like", guest=True)
        self.call("DELETE", path, 204)
        self.call("PUT", path + "/like", 404, guest=True)
        count = self.sql("SELECT count(*) AS n FROM court_demo.comment_likes WHERE comment_id=%s",
                         (first["id"],), rows=True)[0]["n"]
        self.assertEqual(count, 0)
        self.call("GET", f"/board/posts/{post_id}/comments?cursor=invalid", 422)

    def test_bookmarks_are_private_paginated_and_hide_unpublished_posts(self):
        first, second = self.create_post(), self.create_post()
        for _ in range(2):
            self.assertEqual(self.call("PUT", f"/board/posts/{first}/bookmark", guest=True),
                             {"bookmarked": True})
        self.call("PUT", f"/board/posts/{second}/bookmark", guest=True)
        page = self.call("GET", "/board/bookmarks?limit=1", guest=True)
        self.assertEqual([p["id"] for p in page["items"]], [second])
        self.assertTrue(page["items"][0]["bookmarked_by_me"])
        next_page = self.call("GET", "/board/bookmarks?cursor=" + page["next_cursor"], guest=True)
        self.assertEqual([p["id"] for p in next_page["items"]], [first])
        self.assertEqual(self.call("GET", "/board/bookmarks")["items"], [])
        self.sql("UPDATE court_demo.case_posts SET status='hidden' WHERE id=%s", (first,))
        self.assertEqual([p["id"] for p in self.call("GET", "/board/bookmarks", guest=True)["items"]], [second])
        for _ in range(2):
            self.assertEqual(self.call("DELETE", f"/board/posts/{second}/bookmark", guest=True),
                             {"bookmarked": False})

    def test_edit_returns_to_review_and_preserves_visibility_and_tags(self):
        post_id = self.create_post()
        path = f"/board/posts/{post_id}"
        self.create_comment(post_id, guest=True)
        self.call("PUT", path, 404, guest=True, body={"title": "다른 제목", "body": "다른 본문"})
        edited = self.call("PUT", path, body={"title": "수정된 제목", "body": "수정된 본문"})
        self.assertEqual(edited, {"id": post_id, "status": "pending_review"})
        owner = self.call("GET", path)
        self.assertEqual(owner["tags"], ["일상"])
        self.assertEqual(owner["visibility"], "public")
        self.call("GET", path, 404, guest=True)
        self.call("GET", path + "/comments", 404)
        self.call("PUT", path + "/like", 404)
        self.publish(post_id)
        self.assertEqual(self.call("GET", path, guest=True)["comment_count"], 1)

    def test_protected_content_does_not_leak_through_interactions(self):
        post_id = self.create_post(protected=True)
        path = f"/board/posts/{post_id}"
        self.call("GET", path, 404, guest=True)
        for suffix in ("/like", "/bookmark"):
            self.call("PUT", path + suffix, 404, guest=True)
        self.call("GET", path + "/comments", 404, guest=True)
        self.call("POST", path + "/comments", 404, guest=True, body={"body": "댓글"})
        self.call("POST", path + "/unlock", 404, guest=True, body={"password": "wrong-password"})
        opened = self.call("POST", path + "/unlock", guest=True,
                           body={"password": "synthetic-test-password"})
        self.assertEqual(opened["body"], "오늘의 생각을 정리했어요.")
        self.assertNotIn("password_hash", opened)
        self.assertNotIn("demo_id", opened)

    def test_support_branch_does_not_save_submitted_text(self):
        outcome = self.call("POST", "/board/posts", body={
            "title": "예시", "body": "I want to kill myself", "visibility": "public",
        })
        self.assertEqual(outcome["outcome"], "support_required")
        self.assertEqual(self.call("GET", "/board/posts/mine")["items"], [])
        post_id = self.create_post()
        path = f"/board/posts/{post_id}"
        comment = self.call("POST", path + "/comments", body={"body": "I want to kill myself"})
        self.assertEqual(comment["outcome"], "support_required")
        self.assertEqual(self.call("GET", path)["comment_count"], 0)
        edit = self.call("PUT", path, body={"title": "예시", "body": "I want to kill myself"})
        self.assertEqual(edit["outcome"], "support_required")
        self.assertEqual(self.call("GET", path, guest=True)["status"], "published")

    def test_case_link_compatibility_and_cascade_leave_standalone_intact(self):
        case_id = str(self.sql(
            "INSERT INTO court_demo.cases (demo_id,original_thought,status,closed_at) "
            "VALUES (%s,'가상 사건','closed',now()) RETURNING id", (self.owner,), rows=True,
        )[0]["id"])
        payload = {"title": "사건 공유", "body": "선택한 문장", "visibility": "public"}
        self.call("POST", f"/cases/{case_id}/posts", 404, guest=True, body=payload)
        linked = self.call("POST", f"/cases/{case_id}/posts", 201, body=payload)["id"]
        self.publish(linked)
        standalone = self.create_post()
        comment_id = self.create_comment(linked, guest=True)["id"]
        self.call("PUT", f"/board/posts/{linked}/like", guest=True)
        self.call("PUT", f"/board/posts/{linked}/bookmark", guest=True)
        self.call("PUT", f"/board/comments/{comment_id}/like")
        self.call("DELETE", f"/cases/{case_id}", 204)
        self.call("GET", f"/board/posts/{linked}", 404)
        self.assertEqual(self.call("GET", f"/board/posts/{standalone}")["id"], standalone)
        for table, field, item_id in (
            ("post_comments", "post_id", linked), ("post_likes", "post_id", linked),
            ("post_bookmarks", "post_id", linked), ("comment_likes", "comment_id", comment_id),
        ):
            count = self.sql(f"SELECT count(*) AS n FROM court_demo.{table} WHERE {field}=%s",
                             (item_id,), rows=True)[0]["n"]
            self.assertEqual(count, 0, table)

    def test_validation_and_missing_resources(self):
        post_id = self.create_post()
        self.call("POST", f"/board/posts/{post_id}/comments", 422, body={"body": " "})
        self.call("POST", f"/board/posts/{post_id}/comments", 422, body={"body": "가" * 1001})
        self.call("GET", "/board/bookmarks?cursor=broken", 422)
        self.call("PUT", f"/board/comments/{uuid4()}/like", 404)
        self.call("POST", "/board/posts", 422, body={"title": "예시", "body": "본문",
                  "visibility": "password_protected", "password": "1234"})
        response = self.client.get("/api/v1/board/bookmarks")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "INVALID_DEMO_ID")


if __name__ == "__main__":
    unittest.main()
