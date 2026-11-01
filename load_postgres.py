"""Load QueryWise's prepared Online Retail II data into local PostgreSQL.

Run from the project virtual environment:
    python load_postgres.py

The script asks for the PostgreSQL administrator password and a new password
for the read-only QueryWise account. Passwords are entered without echo and
are not saved in this project.
"""

from getpass import getpass
from pathlib import Path

import pandas as pd
import psycopg
from psycopg import sql


PROJECT_DIR = Path(__file__).resolve().parent
PARQUET_FILE = PROJECT_DIR / "online_retail_clean.parquet"
HOST = "localhost"
PORT = 5432
ADMIN_USER = "postgres"
DATABASE = "querywise"
APP_USER = "querywise_reader"

COLUMNS = [
    "invoice",
    "stockcode",
    "description",
    "quantity",
    "invoicedate",
    "price",
    "customer_id",
    "country",
    "line_amount",
    "is_product",
    "product_sales",
]

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS public.sales (
    invoice TEXT NOT NULL,
    stockcode TEXT,
    description TEXT,
    quantity INTEGER,
    invoicedate TIMESTAMP,
    price DOUBLE PRECISION,
    customer_id BIGINT,
    country TEXT,
    line_amount DOUBLE PRECISION,
    is_product BOOLEAN,
    product_sales DOUBLE PRECISION
)
"""


def clean_value(column: str, value):
    """Convert pandas missing values and NumPy scalars to PostgreSQL values."""
    if pd.isna(value):
        return None
    if column == "customer_id":
        return int(value)
    if column == "quantity":
        return int(value)
    if column == "is_product":
        return bool(value)
    if column == "invoicedate":
        return value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    if hasattr(value, "item"):
        return value.item()
    return value


def main() -> None:
    if not PARQUET_FILE.is_file():
        raise FileNotFoundError(
            f"Prepared data not found: {PARQUET_FILE}\n"
            "Run prepare_data.py first."
        )

    print("This imports the project's prepared data into the local querywise database.")
    print("Enter passwords only into this terminal; they will not be displayed or saved.\n")
    admin_password = getpass("PostgreSQL postgres account password: ")
    reader_password = getpass("Choose a password for QueryWise's read-only account: ")
    reader_password_again = getpass("Enter the read-only account password again: ")

    if not reader_password:
        raise ValueError("The read-only account password cannot be blank.")
    if reader_password != reader_password_again:
        raise ValueError("The two read-only account passwords did not match.")

    admin_settings = {
        "host": HOST,
        "port": PORT,
        "user": ADMIN_USER,
        "password": admin_password,
        "dbname": "postgres",
        "connect_timeout": 5,
    }

    # CREATE DATABASE must run outside a transaction.
    with psycopg.connect(**admin_settings, autocommit=True) as connection:
        database_exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (DATABASE,)
        ).fetchone()
        if not database_exists:
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DATABASE))
            )
            print(f"Created database: {DATABASE}")
        else:
            print(f"Using existing database: {DATABASE}")

    target_settings = {**admin_settings, "dbname": DATABASE}
    with psycopg.connect(**target_settings) as connection:
        connection.execute(CREATE_TABLE)
        connection.execute("CREATE INDEX IF NOT EXISTS idx_sales_invoice ON public.sales(invoice)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_sales_date ON public.sales(invoicedate)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_sales_product ON public.sales(is_product, stockcode)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_sales_country ON public.sales(country)")

        role_exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_USER,)
        ).fetchone()
        role_password = sql.Literal(reader_password)
        if role_exists:
            connection.execute(
                sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                    sql.Identifier(APP_USER), role_password
                )
            )
        else:
            connection.execute(
                sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(
                    sql.Identifier(APP_USER), role_password
                )
            )

        existing_rows = connection.execute(
            "SELECT COUNT(*) FROM public.sales"
        ).fetchone()[0]
        if existing_rows:
            confirmation = input(
                f"public.sales already contains {existing_rows:,} rows. "
                "Type REPLACE to overwrite them with the prepared retail data: "
            )
            if confirmation != "REPLACE":
                raise SystemExit("Import cancelled; existing sales rows were kept.")

        # Replace the contents so rerunning this import does not duplicate rows.
        connection.execute("TRUNCATE TABLE public.sales")
        frame = pd.read_parquet(PARQUET_FILE, columns=COLUMNS)
        copy_statement = sql.SQL(
            "COPY public.sales ({}) FROM STDIN"
        ).format(sql.SQL(", ").join(map(sql.Identifier, COLUMNS)))

        with connection.cursor().copy(copy_statement) as copy:
            for row in frame.itertuples(index=False, name=None):
                copy.write_row(
                    tuple(clean_value(column, value) for column, value in zip(COLUMNS, row))
                )

        connection.execute("GRANT CONNECT ON DATABASE querywise TO querywise_reader")
        connection.execute("GRANT USAGE ON SCHEMA public TO querywise_reader")
        connection.execute("GRANT SELECT ON TABLE public.sales TO querywise_reader")
        connection.execute(
            "ALTER ROLE querywise_reader SET default_transaction_read_only = on"
        )

        imported_rows = connection.execute("SELECT COUNT(*) FROM public.sales").fetchone()[0]

    print(f"Imported rows: {imported_rows:,}")
    print(f"Database: {DATABASE}")
    print(f"Read-only QueryWise user: {APP_USER}")
    print("In QueryWise, choose Connect to PostgreSQL and enter these settings:")
    print(f"  Host: {HOST}\n  Port: {PORT}\n  Database: {DATABASE}\n  Username: {APP_USER}")
    print("Enter the read-only password you chose when the app asks for it.")


if __name__ == "__main__":
    main()
