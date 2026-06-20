import csv
import io
import os
import re
import json
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, g
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from werkzeug.security import generate_password_hash, check_password_hash
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "dev-secret-change-in-production")
JWTManager(app)

_SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_SERVER_DIR, "data", "data.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
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


@app.post("/api/datasets")
@jwt_required()
def save_dataset():
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
        # context (including summary) is managed server-side; never overwrite it from the client
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
        '{"answer_template": "...", "sql": "...", "updated_summary": "..."}\n\n'
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
        '- "updated_summary": 1-3 sentences describing the dataset. '
        'If "Current summary" below is empty, write one from scratch using the schema and sample. '
        'If it exists, keep its facts and append any new insight from this Q&A. Never repeat the same insight twice.\n'
        f"Table schema:\n{table_schema}\n\n"
        f"Current summary: {summary}\n\n"
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
    history = data.get("history", [])
    rows = data.get("rows", [])
    headers = data.get("headers", [])
    dataset_id = data.get("dataset_id")
    summary = ""

    if not query:
        return jsonify({"error": "query is required"}), 400

    if dataset_id:
        row_data = get_db().execute(
            "SELECT rows, headers, context FROM datasets WHERE id = ?", (dataset_id,)
        ).fetchone()
        if row_data:
            rows = json.loads(row_data["rows"])
            headers = json.loads(row_data["headers"])
            summary = json.loads(row_data["context"] or "{}").get("summary", "")

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


@app.post("/api/upload")
def upload_csv():
    if request.content_type and "multipart" in request.content_type:
        f = request.files.get("file")
        if not f:
            return jsonify({"error": "file is required"}), 400
        filename = f.filename or "upload.csv"
        content = f.read().decode("utf-8-sig")
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


def _infer_type(values):
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


def _apply_filter(value, op, filter_val):
    v = str(value or "").strip()
    fv = filter_val.strip()
    if op == "contains":
        return fv.lower() in v.lower()
    if op in ("equals", "eq"):
        return v.lower() == fv.lower()
    if op == "starts_with":
        return v.lower().startswith(fv.lower())
    if op == "is_true":
        return v.lower() == "true"
    if op == "is_false":
        return v.lower() != "true"
    if op in ("gt", "gte", "lt", "lte"):
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


@app.get("/api/columns")
def get_column_meta():
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
    col_names = [h[0] for h in headers]

    search = request.args.get("search", "").strip().lower()
    if search:
        rows = [r for r in rows if any(search in str(v).lower() for v in r.values())]

    filters_raw = request.args.get("filters", "")
    if filters_raw:
        try:
            for f in json.loads(filters_raw):
                col, op, val = f.get("col", ""), f.get("op", ""), f.get("val", "")
                if col not in col_names:
                    continue
                rows = [r for r in rows if _apply_filter(r.get(col, ""), op, val)]
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


@app.post("/api/ask")
def ask():
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

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        parsed = json.loads(raw)
        answer_template = parsed.get("answer_template", raw)
        sql = parsed.get("sql")
        sql_result = None

        if sql and answer_template.count("{result}") == 1 and rows and headers:
            try:
                results, out_cols = execute_data_query(rows, headers, sql)
                sql_result = format_query_result(results, out_cols)
                answer = answer_template.replace("{result}", sql_result)
            except Exception as sql_err:
                answer = answer_template.replace("{result}", f"(query error: {sql_err})")
        else:
            answer = answer_template.replace("{result}", "")

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
                pass

        return jsonify({"answer": answer, "sql": sql, "sql_result": sql_result})
    except (json.JSONDecodeError, KeyError):
        return jsonify({"answer": raw, "sql": None, "sql_result": None})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    init_db()
    app.run(port=5000, debug=True)
