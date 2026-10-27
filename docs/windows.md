# Run Ekalavya on Windows

This guide starts Ekalavya from a clean checkout on **Windows 10/11, 64-bit**. It creates fresh local PostgreSQL, Qdrant, and Temporal state; do not copy the Linux `.local/` directory to Windows.

## Prerequisites

Install the following before continuing:

- [Miniforge or Miniconda](https://docs.conda.io/projects/miniconda/en/latest/)
- [PostgreSQL 18 for Windows](https://www.postgresql.org/download/windows/)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) for Qdrant
- The Windows Temporal CLI from [Temporal's download instructions](https://docs.temporal.io/cli) and add `temporal.exe` to `PATH`

Docker Desktop must be running. PostgreSQL's installer normally registers PostgreSQL as a Windows service; start that service before continuing.

## 1. Create the Python environment

Open **Miniforge Prompt** (or a terminal where `conda` is on `PATH`) in the repository root and run:

```powershell
conda env create -f environment.yml
conda activate scholarmotion
```

The Conda environment installs Python 3.11, FFmpeg, and Manim. If Manim fails to install or render, update Conda and retry the environment creation before changing the application code.

## 2. Create the PostgreSQL user and database

Open the **SQL Shell (psql)** installed with PostgreSQL, connect as the `postgres` superuser, then run:

```sql
CREATE USER scholarmotion WITH PASSWORD 'scholarmotion';
CREATE DATABASE scholarmotion OWNER scholarmotion;
```

If either already exists, do not run that statement again. Instead, use the existing credentials in the next step. The default application URL is:

```text
postgresql+asyncpg://scholarmotion:scholarmotion@127.0.0.1:5432/scholarmotion
```

## 3. Configure the application and migrate the database

Back in the activated Conda terminal, copy the template and edit the new `.env` file:

```powershell
Copy-Item .env.example .env
notepad .env
```

At minimum, set `APP_API_KEY` to a random value. If the database credentials differ from the defaults, update `DATABASE_URL` too.

For a real lesson-generation setup, configure the provider/API-key variables in `.env` (for example, the documented Gemini or OpenAI-compatible configuration in `README.md`). The default mock providers are suitable only for smoke testing. Do not commit or share `.env`.

Run the schema migrations:

```powershell
alembic upgrade head
```

## 4. Start the supporting services

Use a separate PowerShell window for each command.

Start Qdrant from the repository root:

```powershell
docker run --rm --name scholarmotion-qdrant -p 6333:6333 -p 6334:6334 -v "${PWD}\qdrant-storage:/qdrant/storage" qdrant/qdrant
```

Qdrant is available at `http://127.0.0.1:6333`. The first run creates empty collections as Ekalavya indexes content.

Start Temporal's local development server:

```powershell
temporal server start-dev --db-filename .\temporal.db
```

This starts the Temporal endpoint at `127.0.0.1:7233` and its web UI at `http://127.0.0.1:8233`.

## 5. Start Ekalavya

In two more activated Conda terminals in the repository root, run the worker and API:

```powershell
python -m scholarmotion.workflows.worker
```

```powershell
uvicorn scholarmotion.api.main:app --host 127.0.0.1 --port 8000
```

The API docs are now at `http://127.0.0.1:8000/docs`.

To start the optional web UI, use another activated Conda terminal. Set the key to the same value as `APP_API_KEY` in `.env`:

```powershell
$env:SCHOLARMOTION_API_KEY = 'your-app-api-key'
streamlit run frontend/app.py
```

## Notes

- This is a local development setup. `temporal server start-dev` is not a production Temporal deployment.
- The renderer works on Windows, but the optional Linux `bubblewrap` isolation is unavailable. Leave `SANDBOX_ISOLATION=auto` (the default) or set it to `disabled`; do not set it to `required`.
- Qdrant is an index, not the source of truth. To move existing application data from another machine, export/import PostgreSQL separately and then run the Qdrant reconciliation workflow. This code-only archive intentionally contains no existing databases, API keys, or generated lesson artifacts.
- Stop each development process with `Ctrl+C`. The Qdrant container is automatically removed because the command uses `--rm`; its `qdrant-storage` directory remains on disk.
