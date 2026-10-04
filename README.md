# 📚 Bookshelf Sync Server (FastAPI Backend)

A lightweight, high-performance **REST API sync server** designed to accompany the ISBN Book Scanner desktop client. It enables seamless differential library synchronization, user account registration, and secure cover image storage across multiple client app instances.

Built natively on **Python**, **FastAPI**, and **SQLite**, it runs identically on both **Windows** and **Linux** with zero database setup overhead.

---

## ✨ Features

- **User Authentication:** Multi-tenant support featuring salted, injection-safe password hashing utilizing native `bcrypt` cryptography and time-expiring `JSON Web Tokens (JWT)`.
- **Differential Synchronization:** Optimizes bandwidth and client latency by using Unix epoch checkpoints (`?since=TIMESTAMP`). It only tracks and delivers data changed since the client's last connection.
- **Logical Deletions:** Tracks removed bookshelf items natively (`is_deleted` column rules) so secondary apps know exactly when to prune local cache blocks.
- **Binary Stream Tracking:** Natively parses Multipart Form submissions to store binary cover artwork directly in SQLite database columns (`BLOB`).
- **Interactive Documentation:** Out-of-the-box GUI endpoint playground powered by Swagger UI.

---

## 🏗️ Architecture & Endpoint Tree

| Route | HTTP Method | Auth Required | Content Type | Purpose |
| :--- | :---: | :---: | :--- | :--- |
| `/api/auth/register` | **POST** | No | `application/json` | Creates a unique user profile profile string block. |
| `/api/auth/login` | **POST** | No | `application/json` | Validates passwords and issues a session access token string. |
| `/api/api/books/sync` | **GET** | Yes | `Query Params` | Pulls updates or sync deletions made since a target checkpoint timestamp. |
| `/api/books/upload` | **POST** | Yes | `multipart/form-data` | Uploads combined JSON metadata and binary image file structures. |
| `/api/books/delete/{isbn}`| **DELETE** | Yes | `URL Path` | Marks a book record as logically deleted across connected applications. |

---

## 🚀 Installation & Running Local Environments

Ensure you have **Python 3.10+** installed on your operating system.

### 1. Initialize the Environment & Dependencies
Clone your repository and run these steps inside the folder context:

```bash
# Create a localized virtual environment layout
python -m venv venv

# Activate the workspace environment loop:
# -> On Windows (PowerShell/CMD):
venv\Scripts\activate
# -> On Linux / macOS:
source venv/bin/activate

# Install the required packages using the requirements file
pip install -r requirements.txt
```

### 2. Boot Up the Server Engine
Launch the server runtime tracking context using the built-in compiler command line structure:

```bash
fastapi run main.py
```
Upon a successful boot sequence, the console output will alert you that the REST API server framework is actively running locally on:
👉 **`http://127.0.0.1:8000`**

---

## 🧪 Testing with Interactive Swagger UI

One of FastAPI's greatest strengths is its auto-generated interactive documentation. You do not need Postman or cURL to check or initialize your tables!

1. Open your browser and navigate to **`http://127.0.0`**.
2. Click on the `/api/auth/register` panel row.
3. Click **"Try it out"**, pass a testing username/password JSON configuration package, and click **"Execute"**.
4. Log into that account via `/api/auth/login` to copy your authorization token string.
5. Click **"Authorize"** at the top right of the Swagger UI page, type `Bearer <YOUR_TOKEN_STRING>`, and test data uploads securely inside the web view!

---

## 🔒 Production Security Notices
- **`JWT_SECRET` Key:** The token signature key inside `main.py` is hardcoded to a default string for testing clarity. **Ensure you replace this** with an environmental variable (`os.getenv("JWT_SECRET")`) before pushing production releases to public target host loops.
- **SQLite Mapping Footprint:** The localized database handles multiple thousands of entries gracefully. If your project expands to handle global production scale, simply replace the `sqlite3` driver link block with `psycopg2` targeting a robust **PostgreSQL** instance without altering your central FastAPI endpoint route structure definitions.