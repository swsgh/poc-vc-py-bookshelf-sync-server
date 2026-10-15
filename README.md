# Bookshelf Sync Server

A FastAPI service for account authentication and synchronizing book records between the Qt and Python ISBN scanner clients. SQLite is initialized automatically when the application starts.

## Get the source

Install Git with your distribution's package manager. For example, on Ubuntu or Debian:

```sh
sudo apt update
sudo apt install -y git
```

Then clone and check out the server branch:

```sh
git clone https://github.com/swsgh/poc-vc-py-bookshelf-sync-server.git
cd poc-vc-py-bookshelf-sync-server
git checkout master
```

## API

Lookup, bookshelf, and cached-cover routes require an `Authorization: Bearer <token>` header. Cover access is limited to ISBNs on the authenticated user's shelf. Authentication endpoints accept JSON request bodies.

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Check that the sync server is reachable. |
| `POST` | `/api/auth/register` | Create an account. JSON body: `username`, `password`. |
| `POST` | `/api/auth/login` | Authenticate and receive a JWT. JSON body: `username`, `password`. |
| `POST` | `/api/books/lookup` | Resolve an ISBN, cache its cover, add the book to the signed-in user's shelf, and return metadata. JSON body: `isbn`. |
| `GET` | `/api/books/cover/{isbn}` | Serve the cached cover image for an ISBN on the authenticated user's shelf. |
| `PUT` | `/api/books/cover/{isbn}` | Replace the cached cover image for an ISBN on the authenticated user's active shelf. Send raw JPEG, PNG, or WebP image bytes with the matching `Content-Type`; maximum upload size is 25 MB. |
| `GET` | `/api/books/sync?since=<unix-seconds>` | Fetch the authenticated user's updates after the checkpoint. |
| `POST` | `/api/books/upload` | Upload a book using multipart form fields. |
| `DELETE` | `/api/books/delete/{isbn}` | Mark an existing book as deleted for synchronization. |

### Book metadata

Uploads use the required multipart field `metadata`, containing JSON with:

| Field | Requirement |
| --- | --- |
| `isbn` | Required |
| `title` | Required |
| `authors` | Optional |
| `publicationDate` | Optional |
| `publisher` | Optional |
| `pageCount` | Optional |

ISBN lookup checks the server's metadata cache, then tries Open Library, Google Books, and the Deutsche Nationalbibliothek (DNB) SRU catalog in that order. The DNB fallback parses its public `oai_dc` XML response for metadata and does not provide a cover image. Cover files from providers that supply them are cached under the persistent data directory. The authenticated lookup stores the scanned book on the user's server shelf and returns metadata with a `hasCover` flag. Clients fetch available covers from the authenticated cover route and cache image bytes locally, refreshing cached images during authenticated sync. A client may replace a cached cover with `PUT /api/books/cover/{isbn}`; the upload requires that ISBN to be active on the caller's shelf, updates the shared ISBN cover cache, and advances the modification timestamp for every active shelf row with that ISBN so other owners receive a sync update. Provider warnings may be returned in the optional `warnings` list.

Sync responses contain `serverTime` and an `updates` list. Each update includes `isbn`, `title`, `authors`, `hasCover`, `publicationDate`, `publisher`, `pageCount`, `isDeleted`, and `lastModified`. Cover image bytes and URLs are not part of the sync payload. Deleted books are sent as tombstones so clients can remove them from their local shelves.

### Database compatibility

The current version expects a fresh database and does not migrate older server databases. Remove the old database before starting the updated service:

- Local run: `bookshelf.db` in the project directory.
- Docker Compose: `data/bookshelf.db`.

## Run locally

Requires Python 3.10 or newer.

```sh
python -m venv .venv
```

Activate the environment, then install dependencies:

```sh
python -m pip install -r requirements.txt
```

`JWT_SECRET` is required when starting the server and must be at least 32 characters.
Set it to a private random value; never use a checked-in or shared example value.
`OPEN_LIBRARY_CONTACT_EMAIL` and `GOOGLE_BOOKS_API_KEY` are optional provider
settings. They are used only by the server, never by scanner clients.

Start the development server from this directory:

```powershell
$env:JWT_SECRET = python -c "import secrets; print(secrets.token_urlsafe(48))"
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

On Linux, create and activate the environment, then set the secret in the current
shell before starting the server:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
export JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

The API is available at `http://127.0.0.1:8000`; interactive Swagger documentation is at `http://127.0.0.1:8000/docs`.

With Compose, `bookshelf.db` is stored in `./data` next to `compose.yaml` and persists across container recreation.

## Run with Docker Compose

Requires Docker Desktop or Docker Engine with Compose enabled. From this directory,
create `.env` from the example. Generate a fresh secret, then paste the output after
`JWT_SECRET=` in `.env`:

```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
notepad .env
```

`secrets.token_urlsafe(48)` generates a cryptographically secure value longer than
the server's 32-character minimum. Keep the value private and do not commit `.env`.

On Linux, use `cp` and your preferred editor. Replace the `JWT_SECRET` placeholder
with the generated value:

```sh
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
${EDITOR:-nano} .env
```

Provider settings in `.env` are optional. Build the image and start the service:

```powershell
docker compose up --build -d
docker compose logs -f bookshelf-sync-server
```

The same commands work on Linux with Docker Engine and the Compose plugin. If your
Linux user does not have permission to access Docker, prefix the Docker commands
with `sudo`.

The `./data` directory is mounted at `/data` and stores `bookshelf.db` and cached
cover files. Application code and installed dependencies are baked into the image;
the source tree is not mounted into the running container. The API is available at
`http://localhost:8000`.

Stop the container without deleting data:

```sh
docker compose down
```

Compose publishes port 8000 on all host interfaces. Use it only on a trusted
network; do not expose this development service directly to the public internet.

## Update the Docker deployment

From the server project directory, fetch the latest code, rebuild the image, and
recreate the service:

```sh
git pull --ff-only
docker compose up --build -d
docker compose logs --tail=100 bookshelf-sync-server
```

The SQLite database in `./data` is preserved. Do not remove that directory when updating.

## Development security

The server has no JWT-secret fallback: `JWT_SECRET` must be set for direct runs and
is required by Compose from `.env`. Keep `.env` private. Use a securely managed
secret and HTTPS before deploying beyond a trusted development network.