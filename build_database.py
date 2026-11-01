from pathlib import Path
import sqlite3

import pandas as pd


DATA_FILE = Path("online_retail_clean.parquet")
DATABASE_FILE = Path("online_retail.db")

if not DATA_FILE.exists():
    raise FileNotFoundError(
        f"Could not find {DATA_FILE}. Run prepare_data.py first."
    )

print(f"Reading prepared data from {DATA_FILE}...")
frame = pd.read_parquet(DATA_FILE)
print(f"Loading {len(frame):,} rows into SQLite...")

with sqlite3.connect(DATABASE_FILE) as connection:
    frame.to_sql(
        "sales",
        connection,
        if_exists="replace",
        index=False,
        chunksize=10_000,
    )

    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(invoicedate)",
        "CREATE INDEX IF NOT EXISTS idx_sales_product ON sales(stockcode)",
        "CREATE INDEX IF NOT EXISTS idx_sales_invoice ON sales(invoice)",
        "CREATE INDEX IF NOT EXISTS idx_sales_customer ON sales(customer_id)",
        """
        CREATE INDEX IF NOT EXISTS idx_merchandise_stockcode_sales
        ON sales(stockcode, product_sales)
        WHERE is_product = 1
        """,
    ]
    for statement in indexes:
        connection.execute(statement)

    connection.execute("PRAGMA optimize")

    summary = connection.execute(
        """
        SELECT
            COUNT(*),
            ROUND(SUM(line_amount), 2),
            ROUND(SUM(product_sales), 2)
        FROM sales
        """
    ).fetchone()

print(f"Database created: {DATABASE_FILE}")
print(f"Rows in sales table: {summary[0]:,}")
print(f"Total line amount: £{summary[1]:,.2f}")
print(f"Merchandise product sales: £{summary[2]:,.2f}")

