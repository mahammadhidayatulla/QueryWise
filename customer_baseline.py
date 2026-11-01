import sqlite3

connection = sqlite3.connect(
    "file:online_retail.db?mode=ro",
    uri=True,
)

query = """
    SELECT customer_id, ROUND(SUM(product_sales), 2) AS product_sales
    FROM sales
    WHERE is_product = 1
      AND customer_id IS NOT NULL
    GROUP BY customer_id
    ORDER BY SUM(product_sales) DESC
    LIMIT 1
"""

for row in connection.execute(query):
    print(row)

connection.close()