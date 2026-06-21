# Data Search

A full-stack web application for exploring CSV datasets. Upload a file, build nested filters, paginate rows server-side, and ask free-text questions answered by an LLM.

## Features

- **CSV Upload** — Upload a file or paste raw CSV; parsed and stored server-side via `POST /api/upload`
- **Nested filter groups** — Build AND/OR filter trees with multiple groups; filters only apply when you click **Apply filters**
- **Rich filter operators** — contains, not contains, equals, not equals, starts with, not starts with, `=`, `≠`, `>`, `<`, `≥`, `≤`, before/after (dates), is empty, is not empty, is true, is false
- **Collapsible filter panel** — Toggle filters open/closed; an active-count badge shows how many filters are running while the panel is hidden
- **Global search** — Substring match across all columns simultaneously
- **Pagination** — Server-side, 50 rows per page
- **LLM Q&A** — Ask natural-language questions about the dataset; the backend queries Groq, optionally runs a SQL query against all rows, and returns a structured answer
- **Dataset management** — Save datasets to your account and reload them later; requires JWT login
- **React + Vite** — Fast HMR frontend with API proxy to the Flask server
- **OpenAPI docs** — Interactive Swagger UI available at `/api/docs`; machine-readable spec at `/api/openapi.json`

## Tech Stack

- **Frontend:** React 19, Vite, JavaScript
- **Backend:** Flask (Python), Flask-JWT-Extended
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
    ├── server.py    → Flask API (all routes)
    └── data/
        └── data.db  → SQLite database (auto-created)
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
JWT_SECRET_KEY=change-this-in-production
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
python3 server.py
```

Server runs at `http://localhost:5000`

**Terminal 2 — Start the Vite dev server:**

```bash
cd client
npm run dev
```

Client runs at `http://localhost:5173`

The frontend automatically proxies `/api/*` calls to the Flask server.

## Filtering

Filters are organized into **groups**. Each group contains one or more conditions joined by the group's **AND / OR** logic. Groups themselves are joined by a top-level **AND / OR** connector.

```
Group 1  [AND|OR]  Name contains "Alice"
                   Age > 25
─── AND ───
Group 2  [AND|OR]  City equals "London"
```

- The **AND / OR** toggle inside a group only appears when the group has two or more conditions.
- Clicking **Apply filters** sends the current tree to the server — changes are not applied live.
- **Reset** clears all groups and removes the applied filter.
- When the filter panel is hidden, a badge shows how many conditions are currently active.

### Filter operators by type

| Type | Operators |
|------|-----------|
| String | contains, not contains, equals, not equals, starts with, not starts with, is empty, is not empty |
| Limited (≤ 10 unique values) | equals, not equals, is empty, is not empty |
| Number | =, ≠, >, <, ≥, ≤, is empty, is not empty |
| Date | equals, not equals, before, after, on or before, on or after, is empty, is not empty |
| Boolean | is true, is false |

## API Endpoints

### Data

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/upload` | Upload a CSV file (multipart) or `{ "csv": "...", "name": "..." }` (JSON). Returns `{ dataset_id, name, columns, row_count }`. |
| `GET` | `/api/columns` | Infer column types and unique values for a dataset. |
| `GET` | `/api/rows` | Fetch a paginated, filtered slice of a dataset's rows. |
| `POST` | `/api/ask` | Ask a free-text question; returns an LLM-generated answer. |
| `GET` | `/api/openapi.json` | OpenAPI 3.0 spec for all endpoints. |
| `GET` | `/api/docs` | Interactive Swagger UI. |

#### `GET /api/columns` query parameters

| Parameter | Description |
|-----------|-------------|
| `dataset_id` | required |

Returns `{ columns: [{ name, type, options }] }`. `type` is one of `string`, `limited`, `number`, `date`, `boolean`. `options` is the list of unique values for `limited` columns.

#### `GET /api/rows` query parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `dataset_id` | required | ID returned by `/api/upload` or dataset endpoints |
| `page` | `1` | Page number (1-indexed) |
| `page_size` | `50` | Rows per page (max 500) |
| `search` | — | Substring match across all column values |
| `filters` | — | JSON filter tree (see below) |

#### Filter tree format

```json
{
  "logic": "AND",
  "groups": [
    {
      "logic": "OR",
      "filters": [
        { "col": "Name", "op": "contains", "val": "Alice" },
        { "col": "Name", "op": "contains", "val": "Bob" }
      ]
    },
    {
      "logic": "AND",
      "filters": [
        { "col": "Age", "op": "gt", "val": "25" }
      ]
    }
  ]
}
```

`logic` at the top level controls how groups are combined. `logic` inside each group controls how its filters are combined. Filters where `val` is empty and `op` does not imply a value (`is_empty`, `not_empty`, `is_true`, `is_false`) are ignored unless their op is value-free.

#### `POST /api/ask` body

```json
{
  "dataset_id": 1,
  "question": "Which country has the highest average salary?",
  "history": [
    { "role": "user", "content": "previous question" },
    { "role": "assistant", "content": "previous answer" }
  ]
}
```

`history` is optional. Up to the last 4 turns are included in the LLM prompt for context.

Returns `{ "answer": "...", "sql": "...", "sql_result": "..." }`.

- `sql` can be a string (single query), an array of strings (multi-query), or `null`.
- For multi-part questions (e.g. "how many are Mr. and how many are Mrs.?") the model may return an array of SQL queries. Each runs independently and its result is interpolated into the answer via `{result_0}`, `{result_1}`, … placeholders.
- `sql_result` contains the formatted result(s), or `null` if no SQL was executed.

### Auth & Dataset Management

All dataset management endpoints require a `Bearer` JWT token in the `Authorization` header.

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/auth/register` | — | Register `{ username, password }` |
| `POST` | `/api/auth/login` | — | Login `{ username, password }`, returns `{ access_token }` |
| `GET` | `/api/datasets` | required | List saved datasets for the logged-in user |
| `POST` | `/api/datasets` | required | Save or update a dataset (upserts by name) |
| `GET` | `/api/datasets/<id>` | — | Fetch a single dataset with all rows |
| `DELETE` | `/api/datasets/<id>` | required | Delete a dataset |

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
JWT_SECRET_KEY=<strong_random_secret>
```

## Development Notes

- All Flask routes live in `server/server.py`
- `Table.jsx` owns upload, filter tree state, pagination, and chat state
- Filtering and pagination are fully server-side; the client holds only the current page
- Column types are inferred at query time by scanning all rows; string columns with ≤ 10 unique values are promoted to `limited` and rendered as dropdowns in the filter UI
- The LLM prompt instructs the model to return `{ answer_template, sql, updated_summary }`. `sql` can be a string or an array; each query runs against an in-memory SQLite table built from all dataset rows and its result is interpolated into the answer template via `{result}` or `{result_0}`, `{result_1}`, … placeholders. The prompt also handles small talk gracefully, encouraging users to ask data questions.
- The dataset `context` column stores a running LLM-maintained summary that is prepended to each subsequent question for continuity
- SQLite database is created automatically at `server/data/data.db` on first run

## What I'd Do Next

- **Complex filter tree in the backend** — The current filter structure is two levels deep (groups of conditions). A proper recursive tree would allow arbitrary nesting (e.g. `(A AND B) OR (C AND (D OR E))`), giving users full boolean expressiveness without UI constraints.

- **Scalability** — The current design loads all rows into memory per request and filters in Python. For large datasets this won't hold. The natural next step is to persist rows in a real database (PostgreSQL), push filtering and pagination into SQL, and add an index on commonly filtered columns. For very large files, streaming the CSV parse and chunked inserts would also be needed.

- **Email authentication and password management** — Right now the auth is username/password with no recovery path. Adding email registration, email verification, and a forgot-password flow (token-based reset link) would make the auth production-grade. OAuth (Google / GitHub) via an identity provider would remove the need to store passwords at all.

- **LLM questions scoped to the active filter** — Today the LLM always sees all rows. When a user has applied filters and narrowed to a subset, questions like "what's the average age?" should run against that filtered view, not the whole dataset. This means forwarding the active filter tree to the `/api/ask` endpoint and applying it before building the in-memory SQLite table the LLM queries.

- **Collaborative datasets** — Allow users to share a dataset with teammates by link or by adding collaborators. A shared dataset could support concurrent annotations — each user marking rows with a label or note — backed by a simple `annotations` table keyed by `(dataset_id, row_index, username)`. This turns the tool from a personal explorer into a lightweight data-labeling workspace.

