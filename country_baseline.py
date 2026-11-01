import sqlite3

connection = sqlite3.connect(
    "file:online_retail.db?mode=ro",
    uri=True,
)

query = """
    SELECT country, ROUND(SUM(product_sales), 2) AS product_sales
    FROM sales
    WHERE is_product = 1
    GROUP BY country
    ORDER BY SUM(product_sales) DESC
    LIMIT 1
"""

for row in connection.execute(query):
    print(row)

connection.close()