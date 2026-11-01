from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests


OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "gemma4:e2b"

WRITE_INTENT = re.compile(
    r"\b(delete|drop|update|insert|alter|truncate|erase|remove)\b",
    flags=re.IGNORECASE,
)


class ReadOnlyRequestError(ValueError):
    """Raised when a request asks the app to change data."""


class ModelResponseError(ValueError):
    """Raised when the model response does not contain usable SQL."""


RETAIL_SCHEMA_CONTEXT = """
Dataset: UCI Online Retail II, a retail transaction-line dataset from 2009–2011.
Table: sales. Each row is a prepared invoice line.
Columns:
- invoice (text): invoice identifier.
- stockcode (text): product or charge code.
- description (text): product or charge description.
- quantity (number): units on each invoice line. This prepared demo has no return rows.
- invoicedate (timestamp text): invoice date and time.
- price (number): unit price.
- customer_id (number, nullable): customer identifier; NULL means unknown.
- country (text): customer country.
- line_amount (number): quantity multiplied by price, including eligible charges.
- is_product (integer): 1 for merchandise, 0 for charges/adjustments.
- product_sales (number): merchandise amount only; zero for non-product entries.

Business rules:
- Merchandise analysis uses product_sales and filters is_product = 1.
- Overall transaction-line analysis uses line_amount.
- Exclude NULL customer_id values from customer-level rankings.
- Cancelled invoices, exact duplicate rows, and rows with non-positive prices
  were excluded during preparation. In this workbook, negative-quantity lines
  are either cancellations or have non-positive prices, so the prepared table
  contains no returns.
- Use the table name exactly as listed in this schema; do not add a schema prefix.
- Dates include times. Use inclusive start and exclusive next-day/month boundaries.
- A top product ranking groups by stockcode and sorts by SUM(product_sales) DESC.
"""


def generate_sql(
    question: str,
    schema_context: str,
    model: str = DEFAULT_MODEL,
    dialect: str = "SQLite",
) -> dict[str, str]:
    """Ask the local Ollama model for a read-only SQL query or clarification."""
    if WRITE_INTENT.search(question):
        raise ReadOnlyRequestError(
            "QueryWise only supports read-only analytics; it cannot change or delete data."
        )

    product_true = "TRUE" if dialect.casefold() == "postgresql" else "1"
    system_prompt = f"""
You translate a user's question into {dialect} SQL for data analysis.

Use only the table and columns described below. Do not invent tables, columns,
business definitions, values, or relationships. If the question is ambiguous,
cannot be answered from the schema, or does not define which metric to use,
return exactly: CLARIFY: followed by one short question.

For an answerable request, return exactly one {dialect} SELECT query and nothing
else. Never return INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, or database-setting statements.
Use table names exactly as listed. Do not invent or add schema prefixes to bare table names.
Use aggregation when the user asks for totals or rankings. When ranking groups,
put the same aggregate expression in ORDER BY that you selected, for example
ORDER BY SUM(amount) DESC. Never order a grouped ranking by the raw row-level
measure. Only add GROUP BY dimensions the user asks to see. If the user asks
for one total with filters or a date range, return one aggregate row without
grouping by products, customers, or countries. Use explicit date ranges with an inclusive start and exclusive next
boundary; avoid BETWEEN with a date-only end when the stored date includes a time.
For any customer ranking or customer-level grouping, always filter out rows
where customer_id IS NULL; a missing ID is not a customer.
Apply the customer_id filter only when the question is about customers. Do not
add customer_id IS NOT NULL to product, country, date, or overall-sales queries.
For PostgreSQL, when a column is declared BOOLEAN, compare it with TRUE or
FALSE, never with integer 1 or 0. For example, filter merchandise with
is_product = TRUE, not is_product = 1.
Whenever a question asks for merchandise, products, product codes, product
quantities, or product variety, filter out charges with is_product = 1 in
SQLite or is_product = TRUE in PostgreSQL. Apply this rule to counts and
maximum quantities as well as sales totals. Use is_product = 0 or FALSE only
when the user specifically asks about non-merchandise charges or adjustments.
Never use FALSE when a question asks for products or merchandise. Example:
for "How many different merchandise product codes were sold in Germany?"
use SELECT COUNT(DISTINCT stockcode) FROM sales WHERE is_product = TRUE
AND country = 'Germany'.

Keep these filters tied to the question. Product ranking example:
SELECT stockcode, SUM(product_sales) FROM sales WHERE is_product = {product_true}
GROUP BY stockcode ORDER BY SUM(product_sales) DESC LIMIT 5. Do not add a
customer_id filter to this product query.

Customer ranking example: when asked for the customer with the highest product
sales, select customer_id and SUM(product_sales), include
WHERE is_product = {product_true} AND customer_id IS NOT NULL, group by customer_id,
order by SUM(product_sales) DESC, and return one row. Never rank the NULL ID group.

Dataset schema and business definitions:
{schema_context}
"""

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
            "stream": False,
            "think": False,
            "keep_alive": "10m",
            "options": {
                "temperature": 0,
                "num_ctx": 8192,
                "num_predict": 384,
            },
        },
        timeout=180,
    )
    response.raise_for_status()

    raw = response.json()["message"]["content"].strip()
    if raw.upper().startswith("CLARIFY:"):
        return {
            "kind": "clarify",
            "text": raw.split(":", 1)[1].strip(),
        }

    fence = chr(96) * 3
    if fence in raw:
        start = raw.find(fence) + len(fence)
        end = raw.find(fence, start)
        candidate = raw[start:] if end == -1 else raw[start:end]
        candidate = re.sub(
            r"^\s*(?:sqlite|sql)\s*",
            "",
            candidate,
            flags=re.IGNORECASE,
        ).strip()
    else:
        candidate = raw

    query_start = re.search(r"\b(SELECT|WITH)\b", candidate, flags=re.IGNORECASE)
    if not query_start:
        raise ModelResponseError(
            f"The model did not return a SELECT query. Response: {raw[:400]}"
        )

    sql = candidate[query_start.start():].strip().rstrip(";").strip()
    if not re.match(r"^(SELECT|WITH)\b", sql, flags=re.IGNORECASE):
        raise ModelResponseError("Could not extract a SELECT query from the model response.")

    return {"kind": "sql", "sql": sql}


def list_postgres_tables(connection_settings: dict[str, Any]) -> list[tuple[str, str]]:
    """List non-system PostgreSQL tables using the supplied database login."""
    try:
        import psycopg
    except ImportError as error:
        raise RuntimeError(
            "PostgreSQL support is not installed yet. Run: "
            "python -m pip install 'psycopg[binary]>=3.2,<4'"
        ) from error

    connection = psycopg.connect(
        **connection_settings,
        connect_timeout=5,
        options="-c default_transaction_read_only=on -c statement_timeout=60000",
    )
    try:
        rows = connection.execute(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_type = 'BASE TABLE'
              AND table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY table_schema, table_name
            """
        ).fetchall()
        connection.rollback()
        return [(row[0], row[1]) for row in rows]
    finally:
        connection.close()


def postgres_schema_context(
    connection_settings: dict[str, Any],
    selected_tables: list[tuple[str, str]],
) -> str:
    """Describe PostgreSQL columns, small samples, and declared foreign keys."""
    try:
        import psycopg
        from psycopg import sql as pg_sql
    except ImportError as error:
        raise RuntimeError(
            "PostgreSQL support is not installed yet. Run: "
            "python -m pip install 'psycopg[binary]>=3.2,<4'"
        ) from error

    connection = psycopg.connect(
        **connection_settings,
        connect_timeout=5,
        options="-c default_transaction_read_only=on -c statement_timeout=60000",
    )
    lines = [
        "Database dialect: PostgreSQL.",
        "Use only the selected tables and columns listed below.",
        "Only use joins supported by the declared foreign-key relationships listed below.",
    ]
    try:
        for schema_name, table_name in selected_tables:
            columns = connection.execute(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
                """,
                (schema_name, table_name),
            ).fetchall()
            lines.append(f"\nTable: {schema_name}.{table_name}")
            lines.append("Columns:")
            for column_name, data_type, nullable in columns:
                lines.append(
                    f"- {column_name} ({data_type}, "
                    f"{'nullable' if nullable == 'YES' else 'not nullable'})"
                )

            foreign_keys = connection.execute(
                """
                SELECT kcu.column_name, ccu.table_schema, ccu.table_name,
                       ccu.column_name
                FROM information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage AS ccu
                  ON ccu.constraint_name = tc.constraint_name
                 AND ccu.constraint_schema = tc.constraint_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = %s AND tc.table_name = %s
                """,
                (schema_name, table_name),
            ).fetchall()
            for source_column, target_schema, target_table, target_column in foreign_keys:
                lines.append(
                    f"Declared relationship: {schema_name}.{table_name}.{source_column} -> "
                    f"{target_schema}.{target_table}.{target_column}"
                )

            if columns:
                relation = pg_sql.Identifier(schema_name, table_name)
                samples = connection.execute(
                    pg_sql.SQL("SELECT * FROM {} LIMIT 2").format(relation)
                ).fetchall()
                if samples:
                    lines.append("Sample values:")
                    column_names = [column[0] for column in columns]
                    for sample in samples:
                        values = []
                        for name, value in zip(column_names, sample):
                            text_value = "NULL" if value is None else str(value)
                            if len(text_value) > 80:
                                text_value = text_value[:77] + "..."
                            values.append(f"{name}={text_value!r}")
                        lines.append("- " + "; ".join(values))

        connection.rollback()
    finally:
        connection.close()
    return "\n".join(lines)


def run_postgres_read_only_query(
    connection_settings: dict[str, Any],
    sql: str,
    timeout_seconds: int = 60,
    max_rows: int = 500,
) -> tuple[pd.DataFrame, bool]:
    """Run one PostgreSQL SELECT in a read-only, time-limited transaction."""
    try:
        import psycopg
    except ImportError as error:
        raise RuntimeError(
            "PostgreSQL support is not installed yet. Run: "
            "python -m pip install 'psycopg[binary]>=3.2,<4'"
        ) from error

    cleaned_sql = sql.strip()
    if cleaned_sql.endswith(";"):
        cleaned_sql = cleaned_sql[:-1].rstrip()
    if ";" in cleaned_sql:
        raise ReadOnlyRequestError("Run one SQL statement at a time.")
    if not re.match(r"^(SELECT|WITH)\b", cleaned_sql, flags=re.IGNORECASE):
        raise ReadOnlyRequestError("Only SELECT queries are allowed.")

    connection = psycopg.connect(
        **connection_settings,
        connect_timeout=5,
        options=(
            "-c default_transaction_read_only=on "
            f"-c statement_timeout={int(timeout_seconds * 1000)}"
        ),
    )
    try:
        connection.execute("SET TRANSACTION READ ONLY")
        cursor = connection.execute(cleaned_sql)
        if cursor.description is None:
            raise ValueError("The query did not return a result table.")
        columns = [column.name for column in cursor.description]
        rows = cursor.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        return pd.DataFrame(rows[:max_rows], columns=columns), truncated
    finally:
        connection.rollback()
        connection.close()


def run_read_only_query(
    database_path: str | Path,
    sql: str,
    allowed_tables: set[str],
    timeout_seconds: int = 60,
    max_rows: int = 500,
) -> tuple[pd.DataFrame, bool]:
    """Run one query on a read-only SQLite connection, restricted to known tables."""
    path = Path(database_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")

    uri = f"{path.as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.execute("PRAGMA query_only = ON")

    allowed_actions = {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
    }
    if hasattr(sqlite3, "SQLITE_RECURSIVE"):
        allowed_actions.add(sqlite3.SQLITE_RECURSIVE)

    def authorize(action: int, arg1: Any, arg2: Any, _database: Any, _trigger: Any) -> int:
        if action == sqlite3.SQLITE_READ:
            return sqlite3.SQLITE_OK if arg1 in allowed_tables else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            function_name = str(arg2 or arg1 or "").lower()
            if function_name == "load_extension":
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK if action in allowed_actions else sqlite3.SQLITE_DENY

    connection.set_authorizer(authorize)
    started = time.monotonic()
    connection.set_progress_handler(
        lambda: 1 if time.monotonic() - started > timeout_seconds else 0,
        1000,
    )

    try:
        cursor = connection.execute(sql)
        if cursor.description is None:
            raise ValueError("The query did not return a result table.")

        columns = [description[0] for description in cursor.description]
        rows = cursor.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        frame = pd.DataFrame(rows[:max_rows], columns=columns)
        return frame, truncated
    finally:
        connection.close()

