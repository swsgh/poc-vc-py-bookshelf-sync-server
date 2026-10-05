import json
import hashlib
import logging
import os
import sqlite3
import time
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Form, Request
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import bcrypt
import jwt
import requests

app = FastAPI(title="Bookshelf Sync Server")

# --- DATABASE SETUP ---
DB_FILE = "bookshelf.db"
COVER_CACHE_DIR = Path(os.environ.get("BOOK_COVER_CACHE_DIR", "covers"))
logger = logging.getLogger("bookshelf.metadata")

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
                cover_url TEXT,
                publication_date TEXT,
                publisher TEXT,
                page_count INTEGER,
                last_modified INTEGER,
                is_deleted INTEGER DEFAULT 0,
                PRIMARY KEY (isbn, user_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS book_metadata_cache (
                isbn TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                authors TEXT,
                cover_file TEXT,
                cover_content_type TEXT,
                publication_date TEXT,
                publisher TEXT,
                page_count INTEGER
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


class IsbnLookup(BaseModel):
    isbn: str


def _provider_user_agent():
    contact = os.environ.get("OPEN_LIBRARY_CONTACT_EMAIL", "").strip()
    return f"BookshelfSyncServer/1.0 ({contact})" if contact else "BookshelfSyncServer/1.0"


def _open_library_lookup(isbn):
    try:
        response = requests.get(
            "https://openlibrary.org/api/books",
            params={"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"},
            headers={"User-Agent": _provider_user_agent()},
            timeout=(4, 12),
        )
        if response.status_code != 200:
            if response.status_code == 404:
                return None, [], ""
            return None, [], f"Open Library request failed (HTTP {response.status_code})."
        info = response.json().get(f"ISBN:{isbn}")
        if not info:
            return None, [], ""

        cover = info.get("cover", {})
        cover_urls = [cover.get(size) for size in ("large", "medium", "small")]
        authors = ", ".join(
            author.get("name", "Unknown") for author in info.get("authors", [])
        ) or info.get("by_statement", "Unknown Author")
        publishers = info.get("publishers", [])
        publisher = publishers[0] if publishers else ""
        if isinstance(publisher, dict):
            publisher = publisher.get("name", "")

        metadata = {
            "isbn": isbn,
            "title": info.get("title", "Unknown Title"),
            "authors": authors,
            "publicationDate": info.get("publish_date", "") or "",
            "publisher": str(publisher),
            "pageCount": int(info.get("number_of_pages") or 0),
        }
        return metadata, cover_urls, ""
    except requests.Timeout:
        return None, [], "Open Library request timed out."
    except requests.RequestException as error:
        return None, [], f"Open Library network request failed: {error}"
    except (ValueError, TypeError) as error:
        return None, [], f"Open Library returned invalid data: {error}"


def _google_books_lookup(isbn):
    api_key = os.environ.get("GOOGLE_BOOKS_API_KEY", "").strip()
    if not api_key:
        return None, [], "Google Books API key is not configured on the server."
    try:
        response = requests.get(
            "https://www.googleapis.com/books/v1/volumes",
            params={"q": f"isbn:{isbn}", "key": api_key},
            headers={"User-Agent": _provider_user_agent()},
            timeout=(4, 12),
        )
        if response.status_code != 200:
            if response.status_code == 429:
                return None, [], "Google Books rate limit reached (HTTP 429)."
            return None, [], f"Google Books request failed (HTTP {response.status_code})."
        items = response.json().get("items", [])
        if not items:
            return None, [], ""

        volume = items[0].get("volumeInfo", {})
        image_links = volume.get("imageLinks", {})
        cover_urls = [
            image_links.get(size)
            for size in ("extraLarge", "large", "medium", "thumbnail", "small", "smallThumbnail")
        ]
        cover_urls = [
            url.replace("http://", "https://", 1) if url and url.startswith("http://") else url
            for url in cover_urls
        ]
        metadata = {
            "isbn": isbn,
            "title": volume.get("title", "Unknown Title"),
            "authors": ", ".join(volume.get("authors", [])) or "Unknown Author",
            "publicationDate": volume.get("publishedDate", "") or "",
            "publisher": volume.get("publisher", "") or "",
            "pageCount": int(volume.get("pageCount") or 0),
        }
        return metadata, cover_urls, ""
    except requests.Timeout:
        return None, [], "Google Books request timed out."
    except requests.RequestException as error:
        return None, [], f"Google Books network request failed: {error}"
    except (ValueError, TypeError) as error:
        return None, [], f"Google Books returned invalid data: {error}"


def _cover_file_path(filename):
    return COVER_CACHE_DIR / filename


def _cache_cover(isbn, cover_urls):
    COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    filename = hashlib.sha256(isbn.encode("utf-8")).hexdigest() + ".img"
    destination = _cover_file_path(filename)
    errors = []
    for url in cover_urls:
        if not url:
            continue
        try:
            response = requests.get(
                url,
                headers={"User-Agent": _provider_user_agent()},
                timeout=(4, 15),
            )
            if response.status_code != 200:
                errors.append(f"Cover request failed (HTTP {response.status_code}).")
                continue
            if not response.content or not response.headers.get("content-type", "").startswith("image/"):
                errors.append("Cover response was empty or not an image.")
                continue
            temporary = destination.with_suffix(".tmp")
            temporary.write_bytes(response.content)
            temporary.replace(destination)
            return filename, response.headers.get("content-type", "image/jpeg"), errors
        except requests.Timeout:
            errors.append("Cover image request timed out.")
        except requests.RequestException as error:
            errors.append(f"Cover image request failed: {error}")
        except OSError as error:
            errors.append(f"Could not cache cover image: {error}")
    return "", "", errors


def _cached_book(isbn):
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT isbn, title, authors, cover_file, cover_content_type, "
            "publication_date, publisher, page_count FROM book_metadata_cache WHERE isbn = ?",
            (isbn,),
        ).fetchone()


def _save_user_book(metadata, cover_url, user_id):
    current_timestamp = int(time.time())
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO books (
                   isbn, user_id, title, authors, cover_url, publication_date,
                   publisher, page_count, last_modified, is_deleted
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (metadata["isbn"], user_id, metadata["title"], metadata["authors"], cover_url,
             metadata["publicationDate"], metadata["publisher"], metadata["pageCount"],
             current_timestamp),
        )
        conn.commit()


def _lookup_book(isbn):
    cached = _cached_book(isbn)
    if cached and (not cached["cover_file"] or _cover_file_path(cached["cover_file"]).is_file()):
        metadata = {
            "isbn": cached["isbn"], "title": cached["title"], "authors": cached["authors"] or "",
            "publicationDate": cached["publication_date"] or "", "publisher": cached["publisher"] or "",
            "pageCount": cached["page_count"] or 0,
        }
        cover_file = cached["cover_file"] or ""
        return metadata, cover_file, []

    metadata, cover_urls, error = _open_library_lookup(isbn)
    warnings = [error] if error else []
    if not metadata:
        google_metadata, google_urls, error = _google_books_lookup(isbn)
        if error:
            warnings.append(error)
        if google_metadata:
            metadata, cover_urls = google_metadata, google_urls
    elif not cover_urls:
        google_metadata, google_urls, error = _google_books_lookup(isbn)
        if error:
            warnings.append(error)
        if google_metadata and google_urls:
            cover_urls = google_urls

    if not metadata:
        if warnings:
            raise HTTPException(status_code=502, detail=" ".join(warnings))
        raise HTTPException(status_code=404, detail=f"No book metadata found for ISBN {isbn}.")

    cover_file, content_type, cover_errors = _cache_cover(isbn, cover_urls)
    warnings.extend(cover_errors)
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO book_metadata_cache (
                   isbn, title, authors, cover_file, cover_content_type,
                   publication_date, publisher, page_count
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (isbn, metadata["title"], metadata["authors"], cover_file, content_type,
             metadata["publicationDate"], metadata["publisher"], metadata["pageCount"]),
        )
        conn.commit()
    return metadata, cover_file, warnings

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


@app.post("/api/books/lookup")
def lookup_book(book: IsbnLookup, request: Request,
                user_id: int = Depends(get_current_user_id)):
    isbn = book.isbn.strip().replace("-", "")
    if not isbn.isdigit() or len(isbn) not in (10, 13):
        raise HTTPException(status_code=400, detail="ISBN must contain 10 or 13 digits.")

    metadata, cover_file, warnings = _lookup_book(isbn)
    cover_url = (
        str(request.base_url).rstrip("/") + f"/api/books/cover/{isbn}"
        if cover_file else ""
    )
    _save_user_book(metadata, cover_url, user_id)
    metadata["coverUrl"] = cover_url
    metadata["warnings"] = warnings
    return metadata


@app.get("/api/books/cover/{isbn}")
def get_book_cover(isbn: str):
    cached = _cached_book(isbn)
    if not cached or not cached["cover_file"]:
        raise HTTPException(status_code=404, detail="No cached cover image for this ISBN.")
    image_path = _cover_file_path(cached["cover_file"])
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="Cached cover image is missing.")
    return FileResponse(
        image_path,
        media_type=cached["cover_content_type"] or "image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )

# =================================================================
# 2. BOOKSHELF GRID SYNCING ENDPOINTS
# =================================================================

# Endpoint A: Differential Sync GET (Pull updates since a local time checkpoint)
@app.get("/api/books/sync")
def sync_books(since: int = 0, user_id: int = Depends(get_current_user_id)):
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """SELECT isbn, title, authors, cover_url, publication_date,
                      publisher, page_count, last_modified, is_deleted
               FROM books WHERE user_id = ? AND last_modified > ?""", 
            (user_id, since)
        )
        rows = cursor.fetchall()

    updates = []
    for row in rows:
        updates.append({
            "isbn": row["isbn"],
            "title": row["title"],
            "authors": row["authors"],
            "isDeleted": row["is_deleted"] == 1,
            "lastModified": row["last_modified"],
            "coverUrl": row["cover_url"] or "",
            "publicationDate": row["publication_date"] or "",
            "publisher": row["publisher"] or "",
            "pageCount": row["page_count"] or 0,
        })

    return {"serverTime": int(time.time()), "updates": updates}

# Endpoint B: Form-Data Upload POST (Push a local device scan up to cloud rows)
@app.post("/api/books/upload")
async def upload_book(
    metadata: str = Form(...), # Receives stringified JSON text block matching Qt client mapping strings
    user_id: int = Depends(get_current_user_id)
):
    try:
        meta_data = json.loads(metadata)
        isbn = meta_data.get("isbn")
        title = meta_data.get("title")
        authors = meta_data.get("authors", "")
        cover_url = meta_data.get("coverUrl", "")
        publication_date = meta_data.get("publicationDate") or None
        publisher = meta_data.get("publisher") or None
        raw_page_count = meta_data.get("pageCount")
        try:
            page_count = int(raw_page_count) if raw_page_count not in (None, "", 0) else None
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="pageCount must be a whole number")
        if page_count is not None and page_count < 0:
            raise HTTPException(status_code=400, detail="pageCount cannot be negative")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON text format inside metadata field")

    if not isbn or not title:
        raise HTTPException(status_code=400, detail="Missing required identity tags: isbn or title")

    current_timestamp = int(time.time())

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        # Preserve an existing URL when the client has no replacement cover URL.
        cursor.execute(
            """INSERT OR REPLACE INTO books (
                   isbn, user_id, title, authors, cover_url,
                   publication_date, publisher, page_count, last_modified, is_deleted
               ) VALUES (
                   ?, ?, ?, ?,
                   COALESCE(NULLIF(?, ''), (SELECT cover_url FROM books WHERE isbn = ? AND user_id = ?)),
                   COALESCE(?, (SELECT publication_date FROM books WHERE isbn = ? AND user_id = ?)),
                   COALESCE(?, (SELECT publisher FROM books WHERE isbn = ? AND user_id = ?)),
                   COALESCE(?, (SELECT page_count FROM books WHERE isbn = ? AND user_id = ?)),
                   ?, 0
               )""",
            (isbn, user_id, title, authors,
             cover_url, isbn, user_id,
             publication_date, isbn, user_id,
             publisher, isbn, user_id,
             page_count, isbn, user_id,
             current_timestamp)
        )
        conn.commit()

    return {"success": True, "timestamp": current_timestamp}

# Endpoint C: Logical Deletion MARK
@app.delete("/api/books/delete/{isbn}")
def delete_book(isbn: str, user_id: int = Depends(get_current_user_id)):
    current_timestamp = int(time.time())
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        # Mark as deleted and clear the cover URL.
        cursor.execute(
            "UPDATE books SET is_deleted = 1, last_modified = ?, cover_url = NULL, "
            "publication_date = NULL, publisher = NULL, page_count = NULL "
            "WHERE isbn = ? AND user_id = ?",
            (current_timestamp, isbn, user_id)
        )
        conn.commit()
        rows_affected = cursor.rowcount

    if rows_affected == 0:
        raise HTTPException(status_code=404, detail="Book record mapping not found")

    return {"success": True, "timestamp": current_timestamp}