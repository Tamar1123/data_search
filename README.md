# Data Search

A full-stack web application for exploring CSV datasets. Upload a file, filter and paginate rows server-side, and ask free-text questions answered by an LLM.

## Features

- **CSV Upload** — Upload a file or paste raw CSV; parsed and stored server-side via `POST /api/upload`
- **Server-side filtering** — Search and column/value filtering with pagination via `GET /api/rows`
- **LLM Q&A** — Ask natural-language questions about the dataset; the backend queries Groq and returns a structured answer via `POST /api/ask`
- **Dataset management** — Save datasets to your account, reload them later
- **React + Vite** — Fast HMR frontend with API proxy to the Flask server

## Tech Stack

- **Frontend:** React 19, Vite, JavaScript
- **Backend:** Flask (Python)
- **Database:** SQLite (file-based, auto-created)
- **LLM:** Groq (`llama-3.3-70b-versatile`) via the OpenAI-compatible API

## Project Structure

```
├── client/          → React + Vite frontend
│   └── src/
│       ├── App.jsx               → auth, dataset load/save orchestration
│       └── components/
│           ├── Table.jsx         → upload, filter, pagination, chat UI
│           └── DatasetList.jsx   → saved dataset browser
└── server/
    ├── server.py    → Flask API
    └── data/
        └── data.db  → SQLite database
```

## Getting Started

### Prerequisites

- Node.js 18+ and npm
- Python 3.10+

### Environment Setup

Create a `.env` file in the `server/` directory:

```bash
cd server
cat > .env << EOF
GROQ_API_KEY=your_groq_api_key_here
FLASK_ENV=development
EOF
```

Get your Groq API key: https://console.groq.com/keys

### Install Dependencies

```bash
# Frontend
cd client
npm install

# Backend
cd server
pip install -r requirements.txt
```

### Run Locally

**Terminal 1 — Start the Flask server:**

```bash
cd server
python server.py
```

Server runs at `http://localhost:5000`

**Terminal 2 — Start the Vite dev server:**

```bash
cd client
npm run dev
```

Client runs at `http://localhost:5173`

The frontend automatically proxies `/api/*` calls to the Flask server.

## API Endpoints

### CSV / Data

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/upload` | Upload a CSV file (multipart) or `{ "csv": "...", "name": "..." }` (JSON). Returns `{ dataset_id, name, columns, row_count }`. |
| `GET` | `/api/rows` | Fetch a page of rows from a stored dataset. |
| `POST` | `/api/ask` | Ask a free-text question about a dataset; returns an LLM-generated answer. |

#### `GET /api/rows` query parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `dataset_id` | required | ID returned by `/api/upload` or dataset management endpoints |
| `page` | `1` | Page number (1-indexed) |
| `page_size` | `50` | Rows per page (max 500) |
| `search` | — | Substring match across all column values |
| `filter_col` | — | Column name for exact-match filter |
| `filter_val` | — | Value to match (used with `filter_col`) |

#### `POST /api/ask` body

```json
{
  "dataset_id": 1,
  "question": "Which country has the highest average salary?",
  "history": []
}
```

Returns `{ "answer": "...", "sql": "...", "sql_result": "..." }`.

### Auth & Dataset Management

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/auth/register` | Register `{ username, password }` |
| `POST` | `/api/auth/login` | Login `{ username, password }` |
| `GET` | `/api/datasets` | List saved datasets for `?username=` |
| `POST` | `/api/datasets` | Save or update a dataset under a user account |
| `GET` | `/api/datasets/<id>` | Fetch a single dataset with all rows |
| `DELETE` | `/api/datasets/<id>` | Delete a dataset |

## Build for Production

```bash
cd client
npm run build
# Output in client/dist/
```

## Environment Variables

`server/.env`:

```
GROQ_API_KEY=<your_groq_api_key>
FLASK_ENV=development
```

## Development Notes

- Backend: `server/server.py` — all Flask routes in one file
- Frontend: `client/src/` — Table component owns upload, filtering, pagination, and chat state
- Filtering and pagination happen server-side; the frontend only holds the current page of rows
- The LLM prompt instructs the model to return `{ answer_template, sql }`. If a SQL query is returned, it runs against an in-memory SQLite table built from the dataset rows and the result is interpolated into the answer.
- Database file is created automatically at `server/data/data.db` on first run
