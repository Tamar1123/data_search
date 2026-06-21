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
try:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
except OSError:
    pass


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

def _filter_is_active(f):
    return f.get("val", "") != "" or f.get("op") in ("is_true", "is_false", "is_empty", "not_empty")


def _filter_to_sql(f, col_map, params):
    """Translates one filter condition to a SQL fragment, appending bind params."""
    orig_col = f.get("col", "")
    sql_col = col_map.get(orig_col, orig_col)
    q = f'"{sql_col.replace(chr(34), chr(34) * 2)}"'  # double-quote and escape
    op = f.get("op", "")
    val = f.get("val", "")

    if op == "contains":
        params.append(f"%{val}%")
        return f"LOWER({q}) LIKE LOWER(?)"
    if op == "not_contains":
        params.append(f"%{val}%")
        return f"LOWER({q}) NOT LIKE LOWER(?)"
    if op in ("equals", "eq"):
        params.append(val)
        return f"LOWER({q}) = LOWER(?)"
    if op in ("not_equals", "neq"):
        params.append(val)
        return f"LOWER({q}) != LOWER(?)"
    if op == "starts_with":
        params.append(f"{val}%")
        return f"LOWER({q}) LIKE LOWER(?)"
    if op == "not_starts_with":
        params.append(f"{val}%")
        return f"LOWER({q}) NOT LIKE LOWER(?)"
    if op == "is_empty":
        return f"{q} = ''"
    if op == "not_empty":
        return f"{q} != ''"
    if op == "is_true":
        return f"LOWER({q}) = 'true'"
    if op == "is_false":
        return f"LOWER({q}) != 'true'"
    if op in ("gt", "gte", "lt", "lte"):
        params.append(val)
        sql_op = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
        return f"CAST({q} AS REAL) {sql_op} CAST(? AS REAL)"
    return "1=1"


def _filter_tree_to_sql(tree, col_map, params):
    """Converts the nested filter tree into a SQL WHERE fragment."""
    groups = tree.get("groups", [])
    top_logic = tree.get("logic", "AND").upper()
    group_clauses = []
    for group in groups:
        active = [f for f in group.get("filters", []) if _filter_is_active(f) and f.get("col")]
        if not active:
            continue
        conds = [_filter_to_sql(f, col_map, params) for f in active]
        group_logic = group.get("logic", "AND").upper()
        joined = f" {group_logic} ".join(conds)
        group_clauses.append(f"({joined})" if len(conds) > 1 else joined)
    if not group_clauses:
        return ""
    return f" {top_logic} ".join(group_clauses)


def _query_rows(rows, headers, search, filters_raw, page, page_size):
    """Loads rows into an in-memory SQLite table and applies search, filters, and pagination via SQL."""
    mapping = _sanitize_headers(headers)
    if not mapping:
        return [], [], 0

    col_map = {orig: sql for sql, orig in mapping}  # original name → sqlite column name

    conn = sqlite3.connect(":memory:")
    try:
        col_defs = ", ".join(f'"{sql}" TEXT' for sql, _ in mapping)
        conn.execute(f"CREATE TABLE data ({col_defs})")
        placeholders = ", ".join("?" for _ in mapping)
        conn.executemany(
            f"INSERT INTO data VALUES ({placeholders})",
            [[str(row.get(orig, "") or "") for _, orig in mapping] for row in rows],
        )

        where_parts = []
        params = []

        if search:
            clauses = [f'LOWER("{sql}") LIKE ?' for sql, _ in mapping]
            params.extend([f"%{search.lower()}%"] * len(mapping))
            where_parts.append(f"({' OR '.join(clauses)})")

        if filters_raw:
            try:
                tree_sql = _filter_tree_to_sql(json.loads(filters_raw), col_map, params)
                if tree_sql:
                    where_parts.append(f"({tree_sql})")
            except (json.JSONDecodeError, AttributeError):
                pass

        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

        total = conn.execute(f"SELECT COUNT(*) FROM data {where}", params).fetchone()[0]
        offset = (page - 1) * page_size
        cursor = conn.execute(f"SELECT * FROM data {where} LIMIT ? OFFSET ?", params + [page_size, offset])
        col_names = [d[0] for d in cursor.description]
        page_rows = [dict(zip(col_names, row)) for row in cursor.fetchall()]
        return page_rows, col_names, total
    finally:
        conn.close()


@app.get("/api/rows")
def get_rows():
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
    search = request.args.get("search", "").strip()
    filters_raw = request.args.get("filters", "")
    page = max(1, request.args.get("page", 1, type=int))
    page_size = min(500, max(1, request.args.get("page_size", 50, type=int)))

    page_rows, col_names, total = _query_rows(rows, headers, search, filters_raw, page, page_size)

    return jsonify({
        "rows": page_rows,
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


def build_llm_messages(user_query, table_schema, summary, history, rows=None, headers=None):
    # Static context → system message so Groq can cache it across turns.
    # Sample is only sent on the first question; follow-ups rely on the summary + history.
    is_followup = len(history) > 0

    sample_section = ""
    if not is_followup and rows and headers:
        col_names = [str(h[0]) for h in headers if h and str(h[0]).strip()]
        sample = random.sample(rows, min(_SAMPLE_ROWS, len(rows)))
        trimmed = [
            {k: (v[:_SAMPLE_VAL_MAX] + "…" if isinstance(v, str) and len(v) > _SAMPLE_VAL_MAX else v)
             for k, v in row.items()}
            for row in sample
        ]
        sample_section = f"Sample ({len(sample)}/{len(rows)} rows):\n{_rows_to_csv(trimmed, col_names)}\n\n"

    summary_line = f"Summary: {summary}\n" if summary else ""

    system_content = (
        "Friendly data analyst in a CSV app. Reply ONLY with JSON (no markdown):\n"
        '{"answer_template":"...","sql":...,"updated_summary":"..."}\n\n'
        "RULES\n"
        "answer_template: 1-3 sentences.\n"
        "• Small talk/off-topic → warm reply + invite a dataset question, sql=null.\n"
        "• Single result needed → include {result} once, sql=string (1 scalar).\n"
        "• Multiple results needed → use {result_0},{result_1},… sql=[array of scalar queries].\n"
        "• sql=null → write the full answer directly, ZERO placeholders.\n"
        "• NEVER mix: if sql=null there must be no {result} in the answer; if {result} appears, sql must exist.\n"
        "• NEVER hardcode a data value you haven't verified — use SQL instead.\n"
        "• Range questions always need TWO queries: [MIN, MAX] with {result_0} and {result_1}.\n"
        "sql: SQLite SELECT on table 'data' (all rows). Double-quote column names. Each query returns 1 scalar.\n"
        "• null only for small talk or conclusions already stated in conversation history.\n"
        "• Prefer one CASE expr over an array for simple A-vs-B comparisons.\n"
        "• WHERE \"col\"!='' for nullable cols; CAST(\"col\" AS REAL) for numeric ops on text cols.\n"
        "updated_summary: ≤3 sentences. Write from scratch if empty; extend with new insight if not. Unchanged for small talk.\n\n"
        f"Schema: {table_schema}\n"
        f"{summary_line}"
        f"{sample_section}"
    )

    messages = [{"role": "system", "content": system_content}]
    for turn in history[-_HISTORY_TURNS:]:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": user_query})
    return messages


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
        messages = build_llm_messages(question, schema, summary, history, rows, headers)
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=512,
            messages=messages,
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


from openapi import docs_bp  # noqa: E402 — imported after app is created
app.register_blueprint(docs_bp)



init_db()

if __name__ == "__main__":
    app.run(port=int(os.getenv("PORT", 5000)), debug=True)
