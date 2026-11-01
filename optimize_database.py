import sqlite3
from pathlib import Path

database = Path("online_retail.db")
if not database.exists():
    raise FileNotFoundError("online_retail.db not found. Run build_database.py first.")

with sqlite3.connect(database) as connection:
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_merchandise_stockcode_sales
        ON sales(stockcode, product_sales)
        WHERE is_product = 1
    """)
    connection.execute("PRAGMA optimize")

print("Product-ranking index is ready.")

