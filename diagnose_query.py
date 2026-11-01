import sqlite3
import time

connection = sqlite3.connect(
    "file:online_retail.db?mode=ro",
    uri=True,
)

print("Indexes on sales:")
for index in connection.execute("PRAGMA index_list(sales)"):
    print(index)

query = """
    SELECT stockcode, SUM(product_sales) AS total_sales
    FROM sales
    WHERE is_product = 1
    GROUP BY stockcode
    ORDER BY total_sales DESC
    LIMIT 5
"""

print("\nQuery plan:")
for row in connection.execute("EXPLAIN QUERY PLAN " + query):
    print(row)

print("\nRunning query without the app's time limit...")
start = time.monotonic()
for row in connection.execute(query):
    print(row)
print(f"Elapsed: {time.monotonic() - start:.1f} seconds")

connection.close()