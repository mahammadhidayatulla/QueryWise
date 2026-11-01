import sqlite3

connection = sqlite3.connect(
    "file:online_retail.db?mode=ro",
    uri=True,
)

queries = {
    "Total line amount": """
        SELECT ROUND(SUM(line_amount), 2)
        FROM sales
    """,
    "Merchandise product sales": """
        SELECT ROUND(SUM(product_sales), 2)
        FROM sales
    """,
    "Top five products by merchandise sales": """
        SELECT
        stockcode,
        MAX(description) AS description,
        SUM(product_sales) AS total_sales
        FROM sales
        WHERE is_product = 1
        GROUP BY stockcode
        ORDER BY total_sales DESC
        LIMIT 5;
    """,
    "First five months of merchandise sales": """
        SELECT SUBSTR(invoicedate, 1, 7) AS month,
               ROUND(SUM(product_sales), 2) AS product_sales
        FROM sales
        GROUP BY month
        ORDER BY month
        LIMIT 5
    """,
}

for title, query in queries.items():
    print(f"\n{title}")
    for row in connection.execute(query):
        print(row)

connection.close()