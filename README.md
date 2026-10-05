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

ISBN lookup checks the server's metadata cache, queries Open Library, then falls back to Google Books when needed. Cover files are cached under the persistent data directory. The authenticated lookup stores the scanned book on the user's server shelf and returns metadata with a `hasCover` flag. Clients fetch available covers from the authenticated cover route and cache image bytes locally. Provider warnings may be returned in the optional `warnings` list.

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

Set `OPEN_LIBRARY_CONTACT_EMAIL` and `GOOGLE_BOOKS_API_KEY` in the server environment before starting it. These provider credentials are used only by the server, never by scanner clients.

Start the development server from this directory:

```sh
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

The API is available at `http://127.0.0.1:8000`; interactive Swagger documentation is at `http://127.0.0.1:8000/docs`.

With Compose, `bookshelf.db` is stored in `./data` next to `compose.yaml` and persists across container recreation.

## Run with Docker Compose on Linux

Requires Docker Engine and the Docker Compose plugin to be installed and running.

Create a `.env` file next to `compose.yaml` with these settings:

```dotenv
OPEN_LIBRARY_CONTACT_EMAIL=you@example.org
GOOGLE_BOOKS_API_KEY=replace-with-your-google-books-key
```

Compose passes these values only to the server. The existing `./data` volume stores both the SQLite database and cached cover files.

1. Verify Docker and Compose are available:

```sh
sudo docker version
sudo docker compose version
```

2. From the cloned project directory, build and start the service:

```sh
sudo docker compose up --build -d
```

3. Follow the service logs or stop it when finished:

```sh
sudo docker compose logs -f bookshelf-sync-server
sudo docker compose down
```

The API is available at `http://localhost:8000`.

Compose publishes port 8000 on all host interfaces. With the current development server configuration, use it only on a trusted network.

## Update the Docker deployment

From the server project directory, fetch the latest checked-out server code and base image, then recreate the service:

```sh
git pull --ff-only
sudo docker compose pull
sudo docker compose up -d --force-recreate
sudo docker compose logs --tail=100 bookshelf-sync-server
```

The SQLite database in `./data` is preserved. Do not remove that directory when updating.

## Development security

The current `JWT_SECRET` in `main.py` is a hard-coded development value. Replace it with a securely managed secret before use outside a local development environment. The development server uses plain HTTP and should not be exposed directly to the public internet.