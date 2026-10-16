import atexit
import contextlib
import gc
import hashlib
import os
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path


_test_data = tempfile.TemporaryDirectory(prefix="bookshelf-auth-test-")
atexit.register(_test_data.cleanup)
os.environ["JWT_SECRET"] = "bookshelf-auth-test-secret-32-bytes-minimum"
os.environ["BOOKSHELF_DB_PATH"] = str(Path(_test_data.name) / "bookshelf.db")
os.environ["BOOK_COVER_CACHE_DIR"] = str(Path(_test_data.name) / "covers")

from fastapi.testclient import TestClient

import main


class AuthSessionTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.username = "session-" + uuid.uuid4().hex[:12]
        self.password = "test-password-long-enough"
        response = self.client.post(
            "/api/auth/register",
            json={"username": self.username, "password": self.password},
        )
        self.assertEqual(response.status_code, 201, response.text)
        with contextlib.closing(sqlite3.connect(main.DB_FILE)) as connection:
            self.user_id = connection.execute(
                "SELECT id FROM users WHERE username = ?", (self.username,)
            ).fetchone()[0]

    def tearDown(self):
        self.client.close()
        gc.collect()

    def login(self, stay_signed_in):
        return self.client.post(
            "/api/auth/login",
            json={
                "username": self.username,
                "password": self.password,
                "staySignedIn": stay_signed_in,
            },
        )

    def test_opt_out_login_issues_only_short_lived_access_token(self):
        response = self.login(False)

        self.assertEqual(response.status_code, 200, response.text)
        payload = main.jwt.decode(
            response.json()["token"],
            options={"verify_signature": False},
            algorithms=[main.ALGORITHM],
        )
        self.assertEqual(payload["exp"] - payload["iat"], 15 * 60)
        self.assertNotIn("refreshToken", response.json())
        with contextlib.closing(sqlite3.connect(main.DB_FILE)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM refresh_tokens WHERE user_id = ?",
                (self.user_id,),
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_refresh_rotates_token_and_replay_revokes_family(self):
        login = self.login(True)
        self.assertEqual(login.status_code, 200, login.text)
        first_token = login.json()["refreshToken"]
        with contextlib.closing(sqlite3.connect(main.DB_FILE)) as connection:
            hashes = [
                row[0]
                for row in connection.execute(
                    "SELECT token_hash FROM refresh_tokens WHERE user_id = ?",
                    (self.user_id,),
                )
            ]
        self.assertIn(hashlib.sha256(first_token.encode("utf-8")).hexdigest(), hashes)
        self.assertNotIn(first_token, hashes)

        refresh = self.client.post(
            "/api/auth/refresh", json={"refreshToken": first_token}
        )
        self.assertEqual(refresh.status_code, 200, refresh.text)
        second_token = refresh.json()["refreshToken"]
        self.assertNotEqual(first_token, second_token)

        replay = self.client.post(
            "/api/auth/refresh", json={"refreshToken": first_token}
        )
        self.assertEqual(replay.status_code, 401)
        family_reuse = self.client.post(
            "/api/auth/refresh", json={"refreshToken": second_token}
        )
        self.assertEqual(family_reuse.status_code, 401)

    def test_logout_revokes_refresh_family(self):
        login = self.login(True)
        self.assertEqual(login.status_code, 200, login.text)
        refresh_token = login.json()["refreshToken"]

        logout = self.client.post(
            "/api/auth/logout", json={"refreshToken": refresh_token}
        )
        self.assertEqual(logout.status_code, 200, logout.text)
        self.assertTrue(logout.json()["revoked"])
        refresh = self.client.post(
            "/api/auth/refresh", json={"refreshToken": refresh_token}
        )
        self.assertEqual(refresh.status_code, 401)

    def test_expired_refresh_token_is_rejected(self):
        login = self.login(True)
        self.assertEqual(login.status_code, 200, login.text)
        refresh_token = login.json()["refreshToken"]
        token_hash = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()
        with contextlib.closing(sqlite3.connect(main.DB_FILE)) as connection:
            connection.execute(
                "UPDATE refresh_tokens SET expires_at = 0 WHERE token_hash = ?",
                (token_hash,),
            )
            connection.commit()

        response = self.client.post(
            "/api/auth/refresh", json={"refreshToken": refresh_token}
        )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()