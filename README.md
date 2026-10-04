# Bookshelf Sync Server

A FastAPI service for account authentication and synchronizing book records with the Qt ISBN scanner client. SQLite is initialized automatically when the application starts.

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

All book routes require an `Authorization: Bearer <token>` header.

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Check that the sync server is reachable. |
| `POST` | `/api/auth/register` | Create an account. JSON body: `username`, `password`. |
| `POST` | `/api/auth/login` | Authenticate and receive a JWT. JSON body: `username`, `password`. |
| `GET` | `/api/books/sync?since=<unix-seconds>` | Fetch the authenticated user's updates after the checkpoint. |
| `POST` | `/api/books/upload` | Upload a book using multipart form fields. |
| `DELETE` | `/api/books/delete/{isbn}` | Mark an existing book as deleted for synchronization. |

For uploads, the required multipart field `metadata` contains JSON with `isbn`, `title`, and optional `authors` and `engineSource` fields. An optional `cover` file contains the cover image. Sync responses include `serverTime` and an `updates` list; cover bytes are returned as Base64 in `coverDataBase64`.

## Run locally

Requires Python 3.10 or newer.

```sh
python -m venv .venv
```

Activate the environment, then install dependencies:

```sh
python -m pip install -r requirements.txt
```

Start the development server from this directory:

```sh
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

The API is available at `http://127.0.0.1:8000`; interactive Swagger documentation is at `http://127.0.0.1:8000/docs`. With Compose, `bookshelf.db` is stored in `./data` next to `compose.yaml` and persists across container recreation.

## Run with Docker Compose on Linux

Requires Docker Engine and the Docker Compose plugin to be installed and running.

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
sudo docker compose logs -f sync-server
sudo docker compose down
```

The API is available at `http://localhost:8000`. Compose publishes port 8000 on all host interfaces; use only on a trusted network with the current development server configuration.

## Development security

The current `JWT_SECRET` in `main.py` is a hard-coded development value. Replace it with a securely managed secret before use outside a local development environment. The development server uses plain HTTP and should not be exposed directly to the public internet.