from __future__ import annotations

import argparse
from decimal import Decimal
from getpass import getpass
import math
import numbers
import re
import time
from pathlib import Path
import tomllib

import pandas as pd

from query_engine import (
    DEFAULT_MODEL,
    RETAIL_SCHEMA_CONTEXT,
    ReadOnlyRequestError,
    generate_sql,
    postgres_schema_context,
    run_postgres_read_only_query,
    run_read_only_query,
)


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "online_retail.db"
ALLOWED_TABLES = {"sales"}

CASES = [
    {
        "id": "top_products",
        "question": (
            "Which five merchandise product codes had the highest product sales? "
            "Return each product code and its total sales."
        ),
        "reference_sql": """
            SELECT stockcode, ROUND(SUM(product_sales), 2) AS product_sales
            FROM sales
            WHERE is_product = 1
            GROUP BY stockcode
            ORDER BY SUM(product_sales) DESC
            LIMIT 5
        """,
    },
    {
        "id": "march_sales",
        "question": "What were merchandise product sales in March 2010?",
        "reference_sql": """
            SELECT ROUND(SUM(product_sales), 2) AS product_sales
            FROM sales
            WHERE is_product = 1
              AND invoicedate >= '2010-03-01'
              AND invoicedate < '2010-04-01'
        """,
    },
    {
        "id": "top_country",
        "question": (
            "Which country had the highest merchandise product sales? "
            "Return the country and its total."
        ),
        "reference_sql": """
            SELECT country, ROUND(SUM(product_sales), 2) AS product_sales
            FROM sales
            WHERE is_product = 1
            GROUP BY country
            ORDER BY SUM(product_sales) DESC
            LIMIT 1
        """,
    },
    {
        "id": "top_five_countries",
        "question": (
            "Show the top five countries by merchandise product sales, "
            "including each country and its total."
        ),
        "reference_sql": """
            SELECT country, ROUND(SUM(product_sales), 2) AS product_sales
            FROM sales
            WHERE is_product = 1
            GROUP BY country
            ORDER BY SUM(product_sales) DESC
            LIMIT 5
        """,
    },
    {
        "id": "top_customer",
        "question": (
            "Which customer ID generated the highest merchandise product sales? "
            "Include the ID and total sales."
        ),
        "reference_sql": """
            SELECT customer_id, ROUND(SUM(product_sales), 2) AS product_sales
            FROM sales
            WHERE is_product = 1 AND customer_id IS NOT NULL
            GROUP BY customer_id
            ORDER BY SUM(product_sales) DESC
            LIMIT 1
        """,
    },
]

# Kept separate from prompt-development examples. This set has been used for
# error analysis and must be reported as validation, not blind holdout.
VALIDATION_CASES = [
    {
        "id": "distinct_products",
        "question": "How many different merchandise product codes are in the sales data?",
        "reference_sql": "SELECT COUNT(DISTINCT stockcode) FROM sales WHERE is_product = 1",
    },
    {
        "id": "known_customers",
        "question": "How many distinct customers have a recorded customer ID?",
        "reference_sql": "SELECT COUNT(DISTINCT customer_id) FROM sales WHERE customer_id IS NOT NULL",
    },
    {
        "id": "country_invoice_counts",
        "question": "Show the three countries with the most distinct invoices and their invoice counts.",
        "reference_sql": """
            SELECT country, COUNT(DISTINCT invoice) AS invoice_count
            FROM sales
            GROUP BY country
            ORDER BY COUNT(DISTINCT invoice) DESC
            LIMIT 3
        """,
    },
    {
        "id": "most_units_product",
        "question": "Which merchandise product code had the greatest total quantity sold? Show the code and units.",
        "reference_sql": """
            SELECT stockcode, SUM(quantity) AS units_sold
            FROM sales
            WHERE is_product = 1
            GROUP BY stockcode
            ORDER BY SUM(quantity) DESC
            LIMIT 1
        """,
    },
    {
        "id": "non_product_line_amount",
        "question": "What is the total line amount from non-merchandise charges and adjustments?",
        "reference_sql": "SELECT SUM(line_amount) FROM sales WHERE is_product = 0",
    },
    {
        "id": "second_country",
        "question": "Which country ranked second in merchandise product sales? Return its name and total.",
        "reference_sql": """
            SELECT country, SUM(product_sales) AS product_sales
            FROM sales
            WHERE is_product = 1
            GROUP BY country
            ORDER BY SUM(product_sales) DESC
            LIMIT 1 OFFSET 1
        """,
    },
    {
        "id": "most_invoices_product",
        "question": "Which merchandise product appeared on the largest number of distinct invoices? Return the code and invoice count.",
        "reference_sql": """
            SELECT stockcode, COUNT(DISTINCT invoice) AS invoice_count
            FROM sales
            WHERE is_product = 1
            GROUP BY stockcode
            ORDER BY COUNT(DISTINCT invoice) DESC
            LIMIT 1
        """,
    },
    {
        "id": "december_net_units",
        "question": "What was the total merchandise quantity sold in December 2009?",
        "reference_sql": """
            SELECT SUM(quantity)
            FROM sales
            WHERE is_product = 1
              AND invoicedate >= '2009-12-01'
              AND invoicedate < '2010-01-01'
        """,
    },
]

# This set is reserved for a final check after prompt changes are complete.
CROSS_BACKEND_CASES = [
    {
        "id": "top_product_descriptions",
        "question": "Which five product descriptions generated the highest merchandise sales?",
        "reference_sql": """
            SELECT description, SUM(product_sales) AS product_sales
            FROM sales
            WHERE is_product = 1
            GROUP BY description
            ORDER BY SUM(product_sales) DESC
            LIMIT 5
        """,
    },
    {
        "id": "germany_product_variety",
        "question": "How many distinct merchandise product codes were sold in Germany?",
        "reference_sql": """
            SELECT COUNT(DISTINCT stockcode)
            FROM sales
            WHERE is_product = 1 AND country = 'Germany'
        """,
    },
    {
        "id": "first_invoice_date",
        "question": "What is the earliest invoice date in the sales data?",
        "reference_sql": "SELECT MIN(invoicedate) FROM sales",
    },
    {
        "id": "largest_product_line_quantity",
        "question": "What was the largest quantity of merchandise on a single invoice line?",
        "reference_sql": "SELECT MAX(quantity) FROM sales WHERE is_product = 1",
    },
    {
        "id": "widest_product_variety_country",
        "question": "Which country had merchandise from the greatest number of distinct product codes? Return the country and count.",
        "reference_sql": """
            SELECT country, COUNT(DISTINCT stockcode) AS distinct_products
            FROM sales
            WHERE is_product = 1
            GROUP BY country
            ORDER BY COUNT(DISTINCT stockcode) DESC
            LIMIT 1
        """,
    },
]


def same_value(actual, expected) -> bool:
    if pd.isna(actual) and pd.isna(expected):
        return True
    numeric_types = (numbers.Number, Decimal)
    if isinstance(actual, numeric_types) and isinstance(expected, numeric_types):
        return math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=0.01)
    return str(actual).strip().casefold() == str(expected).strip().casefold()


def same_results(actual: pd.DataFrame, expected: pd.DataFrame) -> bool:
    if actual.shape != expected.shape:
        return False
    for actual_row, expected_row in zip(
        actual.itertuples(index=False, name=None),
        expected.itertuples(index=False, name=None),
    ):
        if not all(
            same_value(actual_value, expected_value)
            for actual_value, expected_value in zip(actual_row, expected_row)
        ):
            return False
    return True


def run_query(backend: str, sql: str, connection_settings: dict | None) -> pd.DataFrame:
    if backend == "postgres":
        frame, _ = run_postgres_read_only_query(connection_settings, sql)
        return frame
    frame, _ = run_read_only_query(DATABASE, sql, ALLOWED_TABLES)
    return frame


def validate_required_filters(case: dict, generated_sql: str) -> None:
    """Catch missing product-scope filters that can coincidentally match results."""
    question = case["question"].casefold()
    if "non-merchandise" in question or "non merchandise" in question:
        expected_product_filter = r"\bis_product\s*(?:=\s*(?:0|FALSE)\b|IS\s+FALSE\b)"
        description = "is_product = 0/FALSE for non-merchandise rows"
    elif (
        re.search(r"\b(merchandise|product)\b", question)
        and re.search(r"\b(quantity|units|variety|distinct|codes?|descriptions?)\b", question)
    ):
        expected_product_filter = r"\bis_product\s*(?:=\s*(?:1|TRUE)\b|IS\s+TRUE\b)"
        description = "is_product = 1/TRUE for merchandise rows"
    else:
        return

    where_clause = re.search(
        r"\bWHERE\b(.*?)(?:\bGROUP\s+BY\b|\bORDER\s+BY\b|\bLIMIT\b|$)",
        generated_sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not where_clause or not re.search(
        expected_product_filter,
        where_clause.group(1),
        flags=re.IGNORECASE,
    ):
        raise ValueError(f"Generated SQL is missing the required filter: {description}.")


def evaluate_case(
    case: dict,
    backend: str,
    schema_context: str,
    connection_settings: dict | None,
) -> dict:
    started = time.monotonic()
    generated_sql = ""
    expected_frame = pd.DataFrame()
    actual_frame = pd.DataFrame()
    error = ""
    error_stage = ""
    stage = "SQL generation"

    try:
        generated = generate_sql(
            case["question"],
            schema_context,
            DEFAULT_MODEL,
            dialect="PostgreSQL" if backend == "postgres" else "SQLite",
        )
        if generated["kind"] != "sql":
            raise ValueError(f"Model asked for clarification: {generated['text']}")

        generated_sql = generated["sql"]
        stage = "SQL semantic validation"
        validate_required_filters(case, generated_sql)
        stage = "Query execution"
        actual_frame = run_query(backend, generated_sql, connection_settings)
        reference_sql = case["reference_sql"]
        if backend == "postgres":
            # PostgreSQL stores is_product as BOOLEAN and requires a NUMERIC
            # argument for ROUND(value, decimal_places).
            reference_sql = reference_sql.replace("is_product = 1", "is_product = TRUE")
            reference_sql = reference_sql.replace("is_product = 0", "is_product = FALSE")
            reference_sql = reference_sql.replace(
                "ROUND(SUM(product_sales), 2)",
                "ROUND(SUM(product_sales)::numeric, 2)",
            )
            reference_sql = re.sub(
                r"ROUND\(SUM\(([^)]+)\),\s*2\)",
                r"ROUND(SUM(\1)::numeric, 2)",
                reference_sql,
            )
        stage = "Reference query validation"
        expected_frame = run_query(backend, reference_sql, connection_settings)
        if expected_frame.empty:
            raise ValueError(
                "The reference query returned no rows; this evaluation case "
                "does not have a valid expected result for the prepared data."
            )
        stage = "Result comparison"
        passed = same_results(actual_frame, expected_frame)
        if not passed:
            error = "Generated query result differed from the reference result."
            error_stage = stage
    except Exception as exception:
        passed = False
        error_stage = stage
        error = f"{type(exception).__name__}: {exception}"

    return {
        "case_id": case["id"],
        "question": case["question"],
        "passed": passed,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "generated_sql": generated_sql,
        "expected_rows": repr(expected_frame.values.tolist()),
        "actual_rows": repr(actual_frame.values.tolist()),
        "error_stage": error_stage,
        "error": error,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare QueryWise's generated SQL results with trusted reference queries."
    )
    parser.add_argument(
        "--backend",
        choices=("sqlite", "postgres"),
        default="sqlite",
        help="Database backend to evaluate (default: sqlite).",
    )
    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="Run one case ID. Repeat the option to run multiple cases.",
    )
    parser.add_argument(
        "--suite",
        choices=("development", "validation", "cross_backend"),
        default="development",
        help="Choose development, post-fix validation, or cross-backend validation questions.",
    )
    args = parser.parse_args()

    connection_settings = None
    schema_context = RETAIL_SCHEMA_CONTEXT
    if args.backend == "sqlite" and not DATABASE.exists():
        raise FileNotFoundError(
            "online_retail.db is missing. Build it with build_database.py first."
        )
    if args.backend == "postgres":
        config_path = ROOT / ".streamlit" / "secrets.toml"
        config = tomllib.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        postgres = config.get("postgres", {})
        connection_settings = {
            "host": postgres.get("host", "localhost"),
            "port": int(postgres.get("port", 5432)),
            "dbname": postgres.get("database", "querywise"),
            "user": postgres.get("username", "querywise_reader"),
            "password": getpass("PostgreSQL querywise_reader password: "),
        }
        schema_context = postgres_schema_context(
            connection_settings,
            [("public", "sales")],
        )

    suite_cases = {
        "development": CASES,
        "validation": VALIDATION_CASES,
        "cross_backend": CROSS_BACKEND_CASES,
    }[args.suite]
    selected_cases = suite_cases
    if args.case_ids:
        selected_cases = [
            case for case in suite_cases
            if case["id"] in set(args.case_ids)
        ]
        unknown = set(args.case_ids) - {case["id"] for case in suite_cases}
        if unknown:
            raise ValueError(f"Unknown evaluation case IDs: {sorted(unknown)}")
        if not selected_cases:
            raise ValueError("No evaluation cases selected.")

    results = []
    passed = 0

    for number, case in enumerate(selected_cases, start=1):
        print(
            f"\n[{number}/{len(selected_cases)}] {case['id']}: {case['question']}",
            flush=True,
        )
        result = evaluate_case(case, args.backend, schema_context, connection_settings)
        results.append(result)
        if result["passed"]:
            passed += 1

        print("Status:", "PASS" if result["passed"] else "FAIL", flush=True)
        print(f"Elapsed: {result['elapsed_seconds']:.2f}s", flush=True)
        if result["generated_sql"]:
            print("Generated SQL:", result["generated_sql"], flush=True)
        if result["error"]:
            print("Reason:", result["error"], flush=True)

    safety_passed = False
    safety_error = ""
    try:
        generate_sql(
            "Delete all sales rows",
            schema_context,
            DEFAULT_MODEL,
            dialect="PostgreSQL" if args.backend == "postgres" else "SQLite",
        )
    except ReadOnlyRequestError:
        safety_passed = True
    except Exception as exception:
        safety_error = f"{type(exception).__name__}: {exception}"

    print(f"\nCorrectness cases: {passed}/{len(selected_cases)}", flush=True)
    print("Write-request guard:", "PASS" if safety_passed else "FAIL", flush=True)
    if safety_error:
        print("Safety issue:", safety_error, flush=True)

    results.append(
        {
            "case_id": "write_request_guard",
            "question": "Delete all sales rows",
            "passed": safety_passed,
            "elapsed_seconds": 0,
            "generated_sql": "",
            "expected_rows": "Blocked before model call",
            "actual_rows": "Blocked" if safety_passed else "Not blocked",
            "error_stage": "",
            "error": safety_error,
        }
    )

    if args.case_ids:
        output_name = f"evaluation_results_{args.suite}_{args.backend}_selected.csv"
    elif args.backend == "sqlite" and args.suite == "development":
        output_name = "evaluation_results.csv"
    else:
        output_name = f"evaluation_results_{args.suite}_{args.backend}.csv"
    output = ROOT / output_name
    pd.DataFrame(results).to_csv(output, index=False)
    print(f"Detailed results saved to {output}", flush=True)


if __name__ == "__main__":
    main()

