from flask import Blueprint, jsonify

docs_bp = Blueprint("docs", __name__)

SPEC = {
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


@docs_bp.get("/api/openapi.json")
def openapi_spec():
    return jsonify(SPEC)


@docs_bp.get("/api/docs")
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
