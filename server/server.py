import csv
import io
import os
import re
import json
import random
import sqlite3
from flask import Flask, request, jsonify, g
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from werkzeug.security import generate_password_hash, check_password_hash
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
# JWT secret must be overridden via environment variable in production
app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "dev-secret-change-in-production")
JWTManager(app)

_SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("DB_PATH", os.path.join(_SERVER_DIR, "data", "data.db"))
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


_ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*")


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = _ALLOWED_ORIGIN
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    return response


@app.route("/api/<path:path>", methods=["OPTIONS"])
def handle_preflight(**_):
    # Browsers send an OPTIONS preflight before any cross-origin request with custom headers
    return "", 204


def get_db():
    # One SQLite connection per request, stored in Flask's request-scoped g object
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row  # Makes rows accessible as dicts
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with app.app_context():
        db = get_db()
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS datasets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                name TEXT NOT NULL,
                headers TEXT NOT NULL,
                rows TEXT NOT NULL,
                context TEXT NOT NULL DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
            # headers: JSON array of [col_name, col_type] pairs
            # rows:    JSON array of row objects
            # context: JSON object; "summary" key holds the LLM-maintained dataset summary
        )
        # Migration: add context column to databases created before this column existed
        try:
            db.execute("ALTER TABLE datasets ADD COLUMN context TEXT NOT NULL DEFAULT '{}'")
        except Exception:
            pass
        db.commit()


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.post("/api/auth/register")
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400
    db = get_db()
    if db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
        return jsonify({"error": "Username already taken"}), 409
    db.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        (username, generate_password_hash(password)),  # PBKDF2-SHA256 via Werkzeug
    )
    db.commit()
    return jsonify({"username": username, "access_token": create_access_token(identity=username)}), 201


@app.post("/api/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Invalid username or password"}), 401
    return jsonify({"username": username, "access_token": create_access_token(identity=username)})


# ── Dataset CRUD ──────────────────────────────────────────────────────────────

@app.post("/api/datasets")
@jwt_required()
def save_dataset():
    # Upserts by (username, name). The context column (LLM summary) is never
    # touched on update — only /api/ask writes to it.
    data = request.get_json(silent=True) or {}
    username = get_jwt_identity()
    name = (data.get("name") or "Untitled").strip()
    headers = data.get("headers", [])
    rows = data.get("rows", [])
    if not username:
        return jsonify({"error": "username is required"}), 400
    db = get_db()
    existing = db.execute(
        "SELECT id FROM datasets WHERE username = ? AND name = ?", (username, name)
    ).fetchone()
    if existing:
        # Preserve context so accumulated LLM summaries are not lost on re-save
        db.execute(
            "UPDATE datasets SET headers = ?, rows = ?, created_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(headers), json.dumps(rows), existing["id"]),
        )
        db.commit()
        return jsonify({"id": existing["id"], "name": name})
    cursor = db.execute(
        "INSERT INTO datasets (username, name, headers, rows, context) VALUES (?, ?, ?, ?, ?)",
        (username, name, json.dumps(headers), json.dumps(rows), "{}"),
    )
    db.commit()
    return jsonify({"id": cursor.lastrowid, "name": name}), 201


@app.get("/api/datasets")
@jwt_required()
def list_datasets():
    # Returns only metadata (no rows/headers) to keep the response small
    username = get_jwt_identity()
    rows = get_db().execute(
        "SELECT id, name, created_at FROM datasets WHERE username = ? ORDER BY created_at DESC",
        (username,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/api/datasets/<int:dataset_id>")
def get_dataset(dataset_id):
    row = get_db().execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
    if not row:
        return jsonify({"error": "Dataset not found"}), 404
    d = dict(row)
    d["headers"] = json.loads(d["headers"])
    d["rows"] = json.loads(d["rows"])
    d["context"] = json.loads(d.get("context") or "{}")
    return jsonify(d)


@app.delete("/api/datasets/<int:dataset_id>")
@jwt_required()
def delete_dataset(dataset_id):
    db = get_db()
    db.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
    db.commit()
    return "", 204


# ── CSV upload ────────────────────────────────────────────────────────────────

@app.post("/api/upload")
def upload_csv():
    # Accepts either a multipart file upload or a JSON body with {"csv": "...", "name": "..."}
    if request.content_type and "multipart" in request.content_type:
        f = request.files.get("file")
        if not f:
            return jsonify({"error": "file is required"}), 400
        filename = f.filename or "upload.csv"
        content = f.read().decode("utf-8-sig")  # utf-8-sig strips the BOM that Excel adds
    else:
        body = request.get_json(silent=True) or {}
        content = body.get("csv", "")
        filename = body.get("name", "upload.csv")

    if not content.strip():
        return jsonify({"error": "CSV content is empty"}), 400

    reader = csv.DictReader(io.StringIO(content))
    col_names = reader.fieldnames or []
    if not col_names:
        return jsonify({"error": "CSV has no columns"}), 400
    rows = [dict(row) for row in reader]
    headers = [[col, "string"] for col in col_names]

    # Store under "anonymous" since upload does not require authentication
    db = get_db()
    cursor = db.execute(
        "INSERT INTO datasets (username, name, headers, rows, context) VALUES (?, ?, ?, ?, ?)",
        ("anonymous", filename, json.dumps(headers), json.dumps(rows), "{}"),
    )
    db.commit()
    return jsonify({
        "dataset_id": cursor.lastrowid,
        "name": filename,
        "columns": col_names,
        "row_count": len(rows),
    }), 201


# ── Column metadata ───────────────────────────────────────────────────────────

def _infer_type(values):
    # Infers the most specific type that fits all non-empty values in a column.
    # Priority order: boolean > number > date > string
    non_empty = [v for v in values if v.strip()]
    if not non_empty:
        return "string"
    if all(v.lower() in ("true", "false") for v in non_empty):
        return "boolean"
    try:
        for v in non_empty:
            float(v)
        return "number"
    except ValueError:
        pass
    if all(re.match(r"^\d{4}-\d{2}-\d{2}$", v) for v in non_empty):
        return "date"
    return "string"


@app.get("/api/columns")
def get_column_meta():
    # Scans all rows to infer each column's type. String columns with ≤10 unique
    # values are promoted to "limited" so the UI can render a dropdown instead of a text input.
    dataset_id = request.args.get("dataset_id", type=int)
    if not dataset_id:
        return jsonify({"error": "dataset_id is required"}), 400

    row_data = get_db().execute(
        "SELECT rows, headers FROM datasets WHERE id = ?", (dataset_id,)
    ).fetchone()
    if not row_data:
        return jsonify({"error": "Dataset not found"}), 404

    rows = json.loads(row_data["rows"])
    headers = json.loads(row_data["headers"])

    result = []
    for h in headers:
        col = h[0]
        values = [str(r.get(col, "") or "") for r in rows]
        col_type = _infer_type(values)
        unique_vals = sorted({v for v in values if v.strip()})
        entry = {"name": col, "type": col_type, "options": None}
        if col_type == "string" and 0 < len(unique_vals) <= 10:
            entry["type"] = "limited"
            entry["options"] = unique_vals
        result.append(entry)

    return jsonify({"columns": result})


# ── Row retrieval ─────────────────────────────────────────────────────────────

def _apply_filter(value, op, filter_val):
    # Returns True if `value` satisfies the filter condition.
    # Numeric and date comparisons fall back to False when parsing fails.
    v = str(value or "").strip()
    fv = filter_val.strip()
    if op == "contains":
        return fv.lower() in v.lower()
    if op == "not_contains":
        return fv.lower() not in v.lower()
    if op in ("equals", "eq"):
        return v.lower() == fv.lower()
    if op in ("not_equals", "neq"):
        return v.lower() != fv.lower()
    if op == "starts_with":
        return v.lower().startswith(fv.lower())
    if op == "not_starts_with":
        return not v.lower().startswith(fv.lower())
    if op == "is_empty":
        return v == ""
    if op == "not_empty":
        return v != ""
    if op == "is_true":
        return v.lower() == "true"
    if op == "is_false":
        return v.lower() != "true"
    if op in ("gt", "gte", "lt", "lte"):
        # Try numeric comparison first, then date comparison
        try:
            n, fn = float(v), float(fv)
            return {"gt": n > fn, "gte": n >= fn, "lt": n < fn, "lte": n <= fn}[op]
        except ValueError:
            pass
        try:
            from datetime import datetime as _dt
            d, fd = _dt.strptime(v, "%Y-%m-%d"), _dt.strptime(fv, "%Y-%m-%d")
            return {"gt": d > fd, "gte": d >= fd, "lt": d < fd, "lte": d <= fd}[op]
        except ValueError:
            pass
        return False
    return True


def _filter_is_active(f):
    return f.get("val", "") != "" or f.get("op") in ("is_true", "is_false", "is_empty", "not_empty")


def _apply_filter_tree(rows, tree):
    # Evaluates a nested filter tree: groups connected by a top-level AND/OR,
    # each group containing filters connected by the group's own AND/OR logic.
    if not tree or not tree.get("groups"):
        return rows

    groups = tree.get("groups", [])
    top_logic = tree.get("logic", "AND").upper()

    def row_passes(row):
        group_results = []
        for group in groups:
            group_logic = group.get("logic", "AND").upper()
            active = [f for f in group.get("filters", []) if _filter_is_active(f) and f.get("col")]
            if not active:
                group_results.append(True)
                continue
            results = [_apply_filter(row.get(f["col"], ""), f.get("op", ""), f.get("val", "")) for f in active]
            group_results.append(any(results) if group_logic == "OR" else all(results))

        if not group_results:
            return True
        return any(group_results) if top_logic == "OR" else all(group_results)

    return [r for r in rows if row_passes(r)]


@app.get("/api/rows")
def get_rows():
    # Returns a paginated, optionally searched and filtered slice of a dataset's rows.
    dataset_id = request.args.get("dataset_id", type=int)
    if not dataset_id:
        return jsonify({"error": "dataset_id is required"}), 400

    row_data = get_db().execute(
        "SELECT rows, headers FROM datasets WHERE id = ?", (dataset_id,)
    ).fetchone()
    if not row_data:
        return jsonify({"error": "Dataset not found"}), 404

    rows = json.loads(row_data["rows"])
    headers = json.loads(row_data["headers"])
    col_names = [h[0] for h in headers]

    # Global search: keep rows where any column value contains the search string
    search = request.args.get("search", "").strip().lower()
    if search:
        rows = [r for r in rows if any(search in str(v).lower() for v in r.values())]

    # Nested filter tree: {logic, groups: [{logic, filters: [{col, op, val}]}]}
    filters_raw = request.args.get("filters", "")
    if filters_raw:
        try:
            rows = _apply_filter_tree(rows, json.loads(filters_raw))
        except (json.JSONDecodeError, AttributeError):
            pass

    total = len(rows)
    page = max(1, request.args.get("page", 1, type=int))
    page_size = min(500, max(1, request.args.get("page_size", 50, type=int)))
    start = (page - 1) * page_size
    return jsonify({
        "rows": rows[start:start + page_size],
        "columns": col_names,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    })


# ── LLM Q&A ──────────────────────────────────────────────────────────────────

def _rows_to_csv(rows, col_names):
    # Converts a list of row dicts to CSV text for inclusion in the LLM prompt
    lines = [",".join(col_names)]
    for row in rows:
        fields = []
        for col in col_names:
            val = str(row.get(col, ""))
            fields.append(f'"{val}"' if ("," in val or '"' in val) else val)
        lines.append(",".join(fields))
    return "\n".join(lines)


def _sanitize_headers(headers):
    # Returns (sqlite_col_name, original_col_name) pairs, skipping blanks and deduplicating
    seen = {}
    result = []
    for item in headers:
        orig = str(item[0]).strip() if item and item[0] is not None else ""
        if not orig:
            continue
        if orig not in seen:
            seen[orig] = 1
            result.append((orig, item[0]))
        else:
            seen[orig] += 1
            result.append((f"{orig}_{seen[orig]}", item[0]))
    return result


def execute_data_query(rows, headers, sql):
    # Loads all dataset rows into an in-memory SQLite table and runs the given SQL.
    # All values are stored as TEXT; the LLM is instructed to use CAST when needed.
    mapping = _sanitize_headers(headers)
    if not mapping:
        raise ValueError("No valid columns in dataset headers")

    conn = sqlite3.connect(":memory:")
    try:
        col_defs = ", ".join(f'"{tbl}" TEXT' for tbl, _ in mapping)
        conn.execute(f"CREATE TABLE data ({col_defs})")
        placeholders = ", ".join("?" for _ in mapping)
        conn.executemany(
            f"INSERT INTO data VALUES ({placeholders})",
            [[str(row.get(orig, "") or "") for _, orig in mapping] for row in rows],
        )
        conn.commit()
        cursor = conn.execute(sql)
        return cursor.fetchall(), [d[0] for d in cursor.description]
    finally:
        conn.close()


def format_query_result(results, col_names):
    # Formats SQL output into a human-readable string for the LLM answer template.
    # Scalar numbers get thousands separators; multi-row results use pipe separators.
    if not results:
        return "no data found"
    if len(results) == 1 and len(results[0]) == 1:
        val = results[0][0]
        if val is None:
            return "N/A"
        try:
            f = float(val)
            return f"{f:,.2f}".rstrip("0").rstrip(".") if "." in str(val) else f"{int(f):,}"
        except (ValueError, TypeError):
            return str(val)
    if len(results[0]) == 1:
        return ", ".join(str(r[0]) for r in results if r[0] is not None)
    header = " | ".join(col_names)
    rows_fmt = "\n".join(" | ".join(str(v) for v in row) for row in results)
    return f"{header}\n{rows_fmt}"


_SAMPLE_ROWS = 20
_SAMPLE_VAL_MAX = 60
_HISTORY_TURNS = 4


def build_llm_prompt(user_query, table_schema, summary, history, rows=None, headers=None):
    formatted_history = "".join(
        f"{t['role'].upper()}: {t['content']}\n" for t in history[-_HISTORY_TURNS:]
    )

    sample_section = ""
    if rows and headers:
        col_names = [str(h[0]) for h in headers if h and str(h[0]).strip()]
        sample = random.sample(rows, min(_SAMPLE_ROWS, len(rows)))
        # Truncate long cell values so wide text columns don't bloat the prompt
        trimmed = [
            {k: (v[:_SAMPLE_VAL_MAX] + "…" if isinstance(v, str) and len(v) > _SAMPLE_VAL_MAX else v)
             for k, v in row.items()}
            for row in sample
        ]
        sample_section = f"Sample ({len(sample)}/{len(rows)} rows):\n{_rows_to_csv(trimmed, col_names)}\n\n"

    summary_line = f"Summary: {summary}\n" if summary else ""

    return (
        "Friendly data analyst in a CSV app. Reply ONLY with JSON (no markdown):\n"
        '{"answer_template":"...","sql":...,"updated_summary":"..."}\n\n'
        "RULES\n"
        "answer_template: 1-3 sentences.\n"
        "• Small talk/off-topic → warm reply + invite a dataset question, sql=null.\n"
        "• Single result needed → include {result} once, sql=string (1 scalar).\n"
        "• Multiple results needed → use {result_0},{result_1},… sql=[array of scalar queries].\n"
        "• sql=null → full answer, no placeholders.\n"
        "sql: SQLite SELECT on table 'data' (all rows). Double-quote column names. Each query returns 1 scalar.\n"
        "• null for small talk, conclusions, or answers visible in the sample.\n"
        "• Prefer one CASE expr over an array for simple A-vs-B comparisons.\n"
        "• WHERE \"col\"!='' for nullable cols; CAST(\"col\" AS REAL) for numeric ops on text cols.\n"
        "updated_summary: ≤3 sentences. Write from scratch if empty; extend with new insight if not. Unchanged for small talk.\n\n"
        f"Schema: {table_schema}\n"
        f"{summary_line}"
        f"{sample_section}"
        f"History:\n{formatted_history}"
        f"USER: {user_query}\n"
    )


@app.post("/api/ask")
def ask():
    # Reads the current context/summary from the DB, sends the question to the LLM,
    # optionally runs the returned SQL against an in-memory copy of the data to get
    # a precise result, then writes the updated summary back to the DB.
    data = request.get_json(silent=True) or {}
    dataset_id = data.get("dataset_id")
    question = (data.get("question") or "").strip()
    history = data.get("history", [])

    if not dataset_id:
        return jsonify({"error": "dataset_id is required"}), 400
    if not question:
        return jsonify({"error": "question is required"}), 400

    row_data = get_db().execute(
        "SELECT rows, headers, context FROM datasets WHERE id = ?", (dataset_id,)
    ).fetchone()
    if not row_data:
        return jsonify({"error": "Dataset not found"}), 404

    rows = json.loads(row_data["rows"])
    headers = json.loads(row_data["headers"])
    context = json.loads(row_data["context"] or "{}")
    schema = ", ".join(f'"{h[0]}"' for h in headers)
    summary = context.get("summary", "")

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return jsonify({"error": "GROQ_API_KEY not configured"}), 503

    try:
        client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
        prompt = build_llm_prompt(question, schema, summary, history, rows, headers)
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        raw = completion.choices[0].message.content.strip()

        # Strip markdown fences the model sometimes wraps around JSON
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        parsed = json.loads(raw)
        answer_template = parsed.get("answer_template", raw)
        sql = parsed.get("sql")
        sql_result = None
        answer = answer_template

        if sql and rows and headers:
            if isinstance(sql, list):
                # Multiple queries: execute each and substitute {result_0}, {result_1}, …
                parts = []
                for i, query in enumerate(sql):
                    try:
                        res, cols = execute_data_query(rows, headers, query)
                        parts.append(format_query_result(res, cols))
                    except Exception as e:
                        parts.append(f"(query error: {e})")
                for i, val in enumerate(parts):
                    answer = answer.replace(f"{{result_{i}}}", val)
                sql_result = " | ".join(parts)
            elif isinstance(sql, str) and "{result}" in answer_template:
                # Single query
                try:
                    res, cols = execute_data_query(rows, headers, sql)
                    sql_result = format_query_result(res, cols)
                    answer = answer.replace("{result}", sql_result)
                except Exception as e:
                    answer = answer.replace("{result}", f"(query error: {e})")

        # Clean up any leftover placeholders
        answer = re.sub(r"\{result(?:_\d+)?\}", "", answer)

        # Persist the updated summary so the next question starts with richer context
        updated_summary = parsed.get("updated_summary", "").strip()
        if updated_summary:
            try:
                db = get_db()
                db.execute(
                    "UPDATE datasets SET context = ? WHERE id = ?",
                    (json.dumps({"summary": updated_summary}), dataset_id),
                )
                db.commit()
            except Exception:
                pass  # A failed summary write must not break the answer response

        return jsonify({"answer": answer, "sql": sql, "sql_result": sql_result})
    except (json.JSONDecodeError, KeyError):
        return jsonify({"answer": raw, "sql": None, "sql_result": None})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── OpenAPI documentation ─────────────────────────────────────────────────────

_OPENAPI_SPEC = {
    "openapi": "3.0.3",
    "info": {
        "title": "Data Search API",
        "version": "1.0.0",
        "description": (
            "REST API for uploading CSV datasets, applying nested filters, "
            "paginating rows, and asking LLM-powered questions about the data."
        ),
    },
    "servers": [
        {"url": "http://localhost:5000", "description": "Local development server"},
    ],
    "components": {
        "securitySchemes": {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "JWT token obtained from /api/auth/login or /api/auth/register",
            }
        },
        "schemas": {
            "Error": {
                "type": "object",
                "properties": {"error": {"type": "string"}},
                "example": {"error": "Dataset not found"},
            },
            "AuthRequest": {
                "type": "object",
                "required": ["username", "password"],
                "properties": {
                    "username": {"type": "string", "example": "alice"},
                    "password": {"type": "string", "format": "password", "example": "s3cr3t"},
                },
            },
            "AuthResponse": {
                "type": "object",
                "properties": {
                    "username": {"type": "string"},
                    "access_token": {"type": "string"},
                },
            },
            "FilterCondition": {
                "type": "object",
                "required": ["col", "op"],
                "properties": {
                    "col": {"type": "string", "example": "Name"},
                    "op": {
                        "type": "string",
                        "enum": [
                            "contains", "not_contains",
                            "equals", "not_equals",
                            "starts_with", "not_starts_with",
                            "is_empty", "not_empty",
                            "eq", "neq", "gt", "gte", "lt", "lte",
                            "is_true", "is_false",
                        ],
                        "example": "contains",
                    },
                    "val": {
                        "type": "string",
                        "example": "Alice",
                        "description": "Omit or leave empty for value-free operators (is_empty, not_empty, is_true, is_false)",
                    },
                },
            },
            "FilterGroup": {
                "type": "object",
                "required": ["logic", "filters"],
                "properties": {
                    "logic": {
                        "type": "string",
                        "enum": ["AND", "OR"],
                        "description": "How conditions within this group are combined",
                    },
                    "filters": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/FilterCondition"},
                    },
                },
            },
            "FilterTree": {
                "type": "object",
                "required": ["logic", "groups"],
                "description": "Two-level filter tree. Groups are combined by the top-level logic; conditions within each group are combined by the group's logic.",
                "properties": {
                    "logic": {
                        "type": "string",
                        "enum": ["AND", "OR"],
                        "description": "How groups are combined",
                    },
                    "groups": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/FilterGroup"},
                    },
                },
                "example": {
                    "logic": "AND",
                    "groups": [
                        {
                            "logic": "OR",
                            "filters": [
                                {"col": "Name", "op": "contains", "val": "Mr."},
                                {"col": "Name", "op": "contains", "val": "Mrs."},
                            ],
                        },
                        {
                            "logic": "AND",
                            "filters": [{"col": "Age", "op": "gt", "val": "25"}],
                        },
                    ],
                },
            },
            "ColumnMeta": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["string", "limited", "number", "date", "boolean"],
                        "description": "'limited' means a string column with ≤10 unique values",
                    },
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "nullable": True,
                        "description": "Unique values for 'limited' columns; null otherwise",
                    },
                },
            },
            "RowsResponse": {
                "type": "object",
                "properties": {
                    "rows": {"type": "array", "items": {"type": "object"}},
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "total": {"type": "integer"},
                    "page": {"type": "integer"},
                    "page_size": {"type": "integer"},
                    "pages": {"type": "integer"},
                },
            },
            "UploadResponse": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "integer"},
                    "name": {"type": "string"},
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "row_count": {"type": "integer"},
                },
            },
            "DatasetMeta": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                    "created_at": {"type": "string", "format": "date-time"},
                },
            },
            "DatasetFull": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "username": {"type": "string"},
                    "name": {"type": "string"},
                    "headers": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "string"}},
                        "description": "Array of [column_name, column_type] pairs",
                    },
                    "rows": {"type": "array", "items": {"type": "object"}},
                    "context": {"type": "object", "description": "LLM-maintained summary context"},
                    "created_at": {"type": "string", "format": "date-time"},
                },
            },
            "SaveDatasetRequest": {
                "type": "object",
                "required": ["name", "headers", "rows"],
                "properties": {
                    "name": {"type": "string", "example": "titanic.csv"},
                    "headers": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "string"}},
                        "example": [["Name", "string"], ["Age", "string"]],
                    },
                    "rows": {"type": "array", "items": {"type": "object"}},
                },
            },
            "ChatMessage": {
                "type": "object",
                "required": ["role", "content"],
                "properties": {
                    "role": {"type": "string", "enum": ["user", "assistant"]},
                    "content": {"type": "string"},
                },
            },
            "AskRequest": {
                "type": "object",
                "required": ["dataset_id", "question"],
                "properties": {
                    "dataset_id": {"type": "integer", "example": 1},
                    "question": {
                        "type": "string",
                        "example": "How many passengers are Mr. and how many are Mrs.?",
                    },
                    "history": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/ChatMessage"},
                        "description": "Previous conversation turns for context (up to last 4 are used)",
                    },
                },
            },
            "AskResponse": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string"},
                    "sql": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                            {"type": "null"},
                        ],
                        "description": "SQL query or queries that were executed, or null",
                    },
                    "sql_result": {
                        "type": "string",
                        "nullable": True,
                        "description": "Formatted query result(s), or null if no SQL was run",
                    },
                },
            },
        },
    },
    "paths": {
        "/api/auth/register": {
            "post": {
                "tags": ["Auth"],
                "summary": "Register a new user",
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AuthRequest"}}},
                },
                "responses": {
                    "201": {
                        "description": "User created",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AuthResponse"}}},
                    },
                    "400": {"description": "Missing username or password", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                    "409": {"description": "Username already taken", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                },
            }
        },
        "/api/auth/login": {
            "post": {
                "tags": ["Auth"],
                "summary": "Login and receive a JWT token",
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AuthRequest"}}},
                },
                "responses": {
                    "200": {
                        "description": "Login successful",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AuthResponse"}}},
                    },
                    "400": {"description": "Missing credentials", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                    "401": {"description": "Invalid credentials", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                },
            }
        },
        "/api/upload": {
            "post": {
                "tags": ["Data"],
                "summary": "Upload a CSV dataset",
                "description": (
                    "Accepts either a multipart file upload (field name `file`) "
                    "or a JSON body with `{csv, name}`. No authentication required; "
                    "stored under the 'anonymous' user."
                ),
                "requestBody": {
                    "required": True,
                    "content": {
                        "multipart/form-data": {
                            "schema": {
                                "type": "object",
                                "properties": {"file": {"type": "string", "format": "binary"}},
                            }
                        },
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["csv"],
                                "properties": {
                                    "csv": {"type": "string", "description": "Raw CSV text"},
                                    "name": {"type": "string", "example": "data.csv"},
                                },
                            }
                        },
                    },
                },
                "responses": {
                    "201": {
                        "description": "Dataset stored",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/UploadResponse"}}},
                    },
                    "400": {"description": "Empty or invalid CSV", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                },
            }
        },
        "/api/columns": {
            "get": {
                "tags": ["Data"],
                "summary": "Get column metadata for a dataset",
                "description": (
                    "Scans all rows to infer each column's type. "
                    "String columns with ≤10 unique values are returned as type 'limited' "
                    "with their unique values listed in 'options'."
                ),
                "parameters": [
                    {
                        "name": "dataset_id",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "integer"},
                        "description": "Dataset ID returned by /api/upload or /api/datasets",
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Column metadata",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "columns": {
                                            "type": "array",
                                            "items": {"$ref": "#/components/schemas/ColumnMeta"},
                                        }
                                    },
                                }
                            }
                        },
                    },
                    "400": {"description": "Missing dataset_id", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                    "404": {"description": "Dataset not found", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                },
            }
        },
        "/api/rows": {
            "get": {
                "tags": ["Data"],
                "summary": "Fetch a paginated, filtered page of rows",
                "parameters": [
                    {"name": "dataset_id", "in": "query", "required": True, "schema": {"type": "integer"}},
                    {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}},
                    {"name": "page_size", "in": "query", "schema": {"type": "integer", "default": 50, "maximum": 500}},
                    {
                        "name": "search",
                        "in": "query",
                        "schema": {"type": "string"},
                        "description": "Substring match applied across all column values",
                    },
                    {
                        "name": "filters",
                        "in": "query",
                        "schema": {"type": "string"},
                        "description": "JSON-encoded FilterTree. See the FilterTree schema for the structure.",
                        "example": '{"logic":"AND","groups":[{"logic":"AND","filters":[{"col":"Age","op":"gt","val":"25"}]}]}',
                    },
                ],
                "responses": {
                    "200": {
                        "description": "Paginated rows",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RowsResponse"}}},
                    },
                    "400": {"description": "Missing dataset_id", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                    "404": {"description": "Dataset not found", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                },
            }
        },
        "/api/ask": {
            "post": {
                "tags": ["LLM"],
                "summary": "Ask a natural-language question about a dataset",
                "description": (
                    "Sends the question to Groq (llama-3.3-70b-versatile). "
                    "The model may return a SQL query which is executed server-side against all rows; "
                    "the result is interpolated into the answer. "
                    "For multi-part questions (e.g. 'how many are X and how many are Y'), "
                    "the model may return an array of SQL queries with numbered {result_0}, {result_1} placeholders. "
                    "Conversation history (up to the last 4 turns) is included for context. "
                    "An LLM-maintained dataset summary is persisted across questions."
                ),
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AskRequest"}}},
                },
                "responses": {
                    "200": {
                        "description": "LLM answer",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AskResponse"}}},
                    },
                    "400": {"description": "Missing dataset_id or question", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                    "404": {"description": "Dataset not found", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                    "503": {"description": "GROQ_API_KEY not configured", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                },
            }
        },
        "/api/datasets": {
            "get": {
                "tags": ["Datasets"],
                "summary": "List saved datasets for the authenticated user",
                "security": [{"bearerAuth": []}],
                "responses": {
                    "200": {
                        "description": "Dataset list (metadata only, no rows)",
                        "content": {
                            "application/json": {
                                "schema": {"type": "array", "items": {"$ref": "#/components/schemas/DatasetMeta"}}
                            }
                        },
                    },
                    "401": {"description": "Missing or invalid JWT"},
                },
            },
            "post": {
                "tags": ["Datasets"],
                "summary": "Save or update a dataset",
                "description": "Upserts by (username, name). Re-saving an existing name overwrites rows/headers but preserves the LLM summary context.",
                "security": [{"bearerAuth": []}],
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SaveDatasetRequest"}}},
                },
                "responses": {
                    "200": {"description": "Updated existing dataset", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/DatasetMeta"}}}},
                    "201": {"description": "Created new dataset", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/DatasetMeta"}}}},
                    "401": {"description": "Missing or invalid JWT"},
                },
            },
        },
        "/api/datasets/{dataset_id}": {
            "get": {
                "tags": ["Datasets"],
                "summary": "Fetch a single dataset with all rows",
                "parameters": [{"name": "dataset_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                "responses": {
                    "200": {
                        "description": "Full dataset",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/DatasetFull"}}},
                    },
                    "404": {"description": "Dataset not found", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                },
            },
            "delete": {
                "tags": ["Datasets"],
                "summary": "Delete a dataset",
                "security": [{"bearerAuth": []}],
                "parameters": [{"name": "dataset_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                "responses": {
                    "204": {"description": "Deleted"},
                    "401": {"description": "Missing or invalid JWT"},
                },
            },
        },
    },
}


@app.get("/api/openapi.json")
def openapi_spec():
    return jsonify(_OPENAPI_SPEC)


@app.get("/api/docs")
def swagger_ui():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Data Search API – Docs</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    SwaggerUIBundle({
      url: "/api/openapi.json",
      dom_id: "#swagger-ui",
      presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
      layout: "BaseLayout",
      deepLinking: true,
    });
  </script>
</body>
</html>""", 200, {"Content-Type": "text/html"}


init_db()

if __name__ == "__main__":
    app.run(port=int(os.getenv("PORT", 5000)), debug=True)
