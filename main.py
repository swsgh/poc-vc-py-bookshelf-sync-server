import sqlite3
import time
import json
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException, status, File, UploadFile, Form
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import bcrypt
import jwt

app = FastAPI(title="Bookshelf Sync Server")

# --- DATABASE SETUP ---
DB_FILE = "bookshelf.db"

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS books (
                isbn TEXT,
                user_id INTEGER,
                title TEXT NOT NULL,
                authors TEXT,
                engine_source TEXT,
                cover_blob BLOB,
                last_modified INTEGER,
                is_deleted INTEGER DEFAULT 0,
                PRIMARY KEY (isbn, user_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        conn.commit()

init_db()

@app.get("/health")
def health_check():
    return {"status": "ok"}

# --- SECURITY CONFIGURATION ---
JWT_SECRET = "super_secret_key_change_this_in_production"
# (REMOVED: pwd_context = CryptContext(...) line completely)
ALGORITHM = "HS256"
security = HTTPBearer()

class UserAuth(BaseModel):
    username: str
    password: str

def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(security)) -> int:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        user_id: int = payload.get("id")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token claims")
        return user_id
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired session token")

# =================================================================
# 1. ACCOUNT MANAGEMENT ENDPOINTS
# =================================================================

@app.post("/api/auth/register", status_code=201)
def register(user: UserAuth):
    password_bytes = user.password.encode('utf-8')
    salt = bcrypt.gensalt()
    hashed_password_bytes = bcrypt.hashpw(password_bytes, salt)
    hashed_password_str = hashed_password_bytes.decode('utf-8')

    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (user.username, hashed_password_str))
            conn.commit()
        return {"message": "Account created successfully"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Username already exists")

@app.post("/api/auth/login")
def login(user: UserAuth):
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (user.username,))
        db_user = cursor.fetchone()

    if db_user:
        password_bytes = user.password.encode('utf-8')
        db_hash_bytes = db_user["password_hash"].encode('utf-8')
        is_valid = bcrypt.checkpw(password_bytes, db_hash_bytes)
    else:
        is_valid = False

    if not is_valid:
        raise HTTPException(status_code=400, detail="Incorrect username or password")

    expires = int(time.time()) + (30 * 24 * 60 * 60)
    token = jwt.encode({"id": db_user["id"], "exp": expires}, JWT_SECRET, algorithm=ALGORITHM)
    return {"token": token}

# =================================================================
# 2. BOOKSHELF GRID SYNCING ENDPOINTS
# =================================================================

# Endpoint A: Differential Sync GET (Pull updates since a local time checkpoint)
@app.get("/api/books/sync")
def sync_books(since: int = 0, user_id: int = Depends(get_current_user_id)):
    import base64
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """SELECT isbn, title, authors, engine_source, cover_blob, last_modified, is_deleted 
               FROM books WHERE user_id = ? AND last_modified > ?""", 
            (user_id, since)
        )
        rows = cursor.fetchall()

    updates = []
    for row in rows:
        # Turn raw binary byte blobs into clean Base64 strings for structural JSON transfer safely
        cover_base64 = ""
        if row["cover_blob"]:
            cover_base64 = base64.b64encode(row["cover_blob"]).decode('utf-8')

        updates.append({
            "isbn": row["isbn"],
            "title": row["title"],
            "authors": row["authors"],
            "engineSource": row["engine_source"],
            "isDeleted": row["is_deleted"] == 1,
            "lastModified": row["last_modified"],
            "coverDataBase64": cover_base64
        })

    return {"serverTime": int(time.time()), "updates": updates}

# Endpoint B: Form-Data Upload POST (Push a local device scan up to cloud rows)
@app.post("/api/books/upload")
async def upload_book(
    metadata: str = Form(...), # Receives stringified JSON text block matching Qt client mapping strings
    cover: Optional[UploadFile] = File(None), # Optional incoming binary file stream
    user_id: int = Depends(get_current_user_id)
):
    try:
        meta_data = json.loads(metadata)
        isbn = meta_data.get("isbn")
        title = meta_data.get("title")
        authors = meta_data.get("authors", "")
        engine_source = meta_data.get("engineSource", "")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON text format inside metadata field")

    if not isbn or not title:
        raise HTTPException(status_code=400, detail="Missing required identity tags: isbn or title")

    current_timestamp = int(time.time())
    cover_bytes = await cover.read() if cover else None

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        # INSERT OR REPLACE keeps items synchronized. COALESCE retains existing covers if none were uploaded.
        cursor.execute(
            """INSERT OR REPLACE INTO books (isbn, user_id, title, authors, engine_source, cover_blob, last_modified, is_deleted)
               VALUES (?, ?, ?, ?, ?, COALESCE(?, (SELECT cover_blob FROM books WHERE isbn = ? AND user_id = ?)), ?, 0)""",
            (isbn, user_id, title, authors, engine_source, cover_bytes, isbn, user_id, current_timestamp)
        )
        conn.commit()

    return {"success": True, "timestamp": current_timestamp}

# Endpoint C: Logical Deletion MARK
@app.delete("/api/books/delete/{isbn}")
def delete_book(isbn: str, user_id: int = Depends(get_current_user_id)):
    current_timestamp = int(time.time())
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        # Mark as deleted and clear cover storage allocations to drop server resource burdens
        cursor.execute(
            "UPDATE books SET is_deleted = 1, last_modified = ?, cover_blob = NULL WHERE isbn = ? AND user_id = ?",
            (current_timestamp, isbn, user_id)
        )
        conn.commit()
        rows_affected = cursor.rowcount

    if rows_affected == 0:
        raise HTTPException(status_code=404, detail="Book record mapping not found")

    return {"success": True, "timestamp": current_timestamp}