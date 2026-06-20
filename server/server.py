import os
import json
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
_SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_SERVER_DIR, "data", "data.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    return response


@app.route("/api/<path:path>", methods=["OPTIONS"])
def handle_preflight(**_):
    return "", 204


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
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
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
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
        )
        try:
            db.execute("ALTER TABLE datasets ADD COLUMN context TEXT NOT NULL DEFAULT '{}'")
        except Exception:
            pass
        db.commit()


@app.get("/api/items")
def list_items():
    rows = get_db().execute("SELECT * FROM items ORDER BY created_at DESC").fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/items")
def create_item():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    db = get_db()
    cursor = db.execute("INSERT INTO items (name) VALUES (?)", (name,))
    db.commit()
    row = db.execute("SELECT * FROM items WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.delete("/api/items/<int:item_id>")
def delete_item(item_id):
    db = get_db()
    db.execute("DELETE FROM items WHERE id = ?", (item_id,))
    db.commit()
    return "", 204


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
        (username, generate_password_hash(password)),
    )
    db.commit()
    return jsonify({"username": username}), 201


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
    return jsonify({"username": username})


@app.post("/api/datasets")
def save_dataset():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    name = (data.get("name") or "Untitled").strip()
    headers = data.get("headers", [])
    rows = data.get("rows", [])
    context = data.get("context", {})
    if not username:
        return jsonify({"error": "username is required"}), 400
    db = get_db()
    existing = db.execute(
        "SELECT id FROM datasets WHERE username = ? AND name = ?", (username, name)
    ).fetchone()
    if existing:
        db.execute(
            "UPDATE datasets SET headers = ?, rows = ?, context = ?, created_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(headers), json.dumps(rows), json.dumps(context), existing["id"]),
        )
        db.commit()
        return jsonify({"id": existing["id"], "name": name})
    cursor = db.execute(
        "INSERT INTO datasets (username, name, headers, rows, context) VALUES (?, ?, ?, ?, ?)",
        (username, name, json.dumps(headers), json.dumps(rows), json.dumps(context)),
    )
    db.commit()
    return jsonify({"id": cursor.lastrowid, "name": name}), 201


@app.get("/api/datasets")
def list_datasets():
    username = (request.args.get("username") or "").strip()
    if not username:
        return jsonify({"error": "username is required"}), 400
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
def delete_dataset(dataset_id):
    db = get_db()
    db.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
    db.commit()
    return "", 204


def _rows_to_csv(rows, col_names):
    lines = [",".join(col_names)]
    for row in rows:
        fields = []
        for col in col_names:
            val = str(row.get(col, ""))
            fields.append(f'"{val}"' if ("," in val or '"' in val) else val)
        lines.append(",".join(fields))
    return "\n".join(lines)


def build_llm_prompt(user_query, table_schema, summary, history, rows=None, headers=None):
    formatted_history = ""
    for turn in history[-10:]:
        formatted_history += f"{turn['role'].upper()}: {turn['content']}\n"

    sample_section = ""
    if rows and headers:
        col_names = [str(h[0]) for h in headers if h and str(h[0]).strip()]
        sample = rows[:100]
        total = len(rows)
        sample_section = (
            f"--- DATA SAMPLE (first {len(sample)} of {total} rows) ---\n"
            f"{_rows_to_csv(sample, col_names)}\n\n"
        )

    return (
        "You are a data analyst for a CSV exploration app.\n"
        "Respond ONLY with a JSON object — no markdown, no extra text:\n"
        '{"answer_template": "...", "sql": "..."}\n\n'
        "Rules:\n"
        '- "answer_template": A concise natural-language answer (1-3 sentences). '
        'If you set sql to a query, include exactly ONE {result} placeholder. '
        'If sql is null, write the complete answer directly — no placeholder.\n'
        '- "sql": A SQLite SELECT query against a table named "data" that covers ALL rows (not just the sample). '
        'The query MUST return exactly one row and one column (a scalar). '
        'Always double-quote column names. '
        'Set to null when the sample data above is already sufficient to answer precisely.\n'
        "- For comparison questions (e.g. 'more A or B?'), use a CASE expression to produce a single descriptive string, "
        "e.g.: SELECT CASE WHEN SUM(\"col\"='a') > SUM(\"col\"='b') THEN 'a (' || SUM(\"col\"='a') || ')' ELSE 'b (' || SUM(\"col\"='b') || ')' END FROM data\n"
        "- Prefer null sql for questions about general conclusions, dataset summery, examples, or things visible in the sample.\n"
        "- Use sql for aggregations (counts, averages, max/min) that require scanning all rows.\n"
        "- Handle empty/missing values: add WHERE \"col\" != '' for aggregations on nullable columns.\n"
        "- For numeric operations on string-typed columns, use CAST(\"col\" AS REAL) and filter non-numeric rows.\n"
        f"Table schema:\n{table_schema}\n\n"
        f"Context: {summary}\n\n"
        f"{sample_section}"
        f"Recent conversation:\n{formatted_history}"
        f"USER: {user_query}\n"
    )


def _sanitize_headers(headers):
    """Return list of (sqlite_col_name, original_col_name), skipping blank names and deduplicating."""
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


@app.post("/api/chat")
def chat():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    schema = data.get("schema", "")
    summary = data.get("summary", "")
    history = data.get("history", [])
    rows = data.get("rows", [])
    headers = data.get("headers", [])
    dataset_id = data.get("dataset_id")

    if not query:
        return jsonify({"error": "query is required"}), 400

    if dataset_id:
        row_data = get_db().execute(
            "SELECT rows, headers FROM datasets WHERE id = ?", (dataset_id,)
        ).fetchone()
        if row_data:
            rows = json.loads(row_data["rows"])
            headers = json.loads(row_data["headers"])

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return jsonify({"error": "GROQ_API_KEY not configured"}), 503

    try:
        client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
        prompt = build_llm_prompt(query, schema, summary, history, rows, headers)
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

        if sql and answer_template.count("{result}") == 1 and rows and headers:
            try:
                results, col_names = execute_data_query(rows, headers, sql)
                result_str = format_query_result(results, col_names)
                answer = answer_template.replace("{result}", result_str)
            except Exception as sql_err:
                answer = answer_template.replace("{result}", f"(query error: {sql_err})")
        else:
            # If the model generated multiple {result} placeholders or no sql, strip placeholders
            answer = answer_template.replace("{result}", "")

        return jsonify({"response": answer})
    except (json.JSONDecodeError, KeyError):
        return jsonify({"response": raw})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/api/validate-types")
def validate_types():
    data = request.get_json(silent=True) or {}
    headers = data.get("headers")
    rows = data.get("rows")
    if headers is None or rows is None:
        return jsonify({"error": "headers and rows are required"}), 400

    errors = []
    for row_idx, row in enumerate(rows, start=1):
        for col_name, col_type in headers:
            str_value = str(row.get(col_name, "")).strip()
            if not str_value:
                continue

            if col_type == "number":
                try:
                    float(str_value)
                except (ValueError, TypeError):
                    errors.append({"row": row_idx, "column": col_name, "expected": "number", "value": str_value})

            elif col_type == "date":
                try:
                    datetime.strptime(str_value, "%Y-%m-%d")
                except (ValueError, TypeError):
                    errors.append({"row": row_idx, "column": col_name, "expected": "date (YYYY-MM-DD)", "value": str_value})

            elif col_type == "boolean":
                if str_value.lower() not in ("true", "false"):
                    errors.append({"row": row_idx, "column": col_name, "expected": "boolean (true/false)", "value": str_value})

    return jsonify({"errors": errors})


if __name__ == "__main__":
    init_db()
    app.run(port=5000, debug=True)
