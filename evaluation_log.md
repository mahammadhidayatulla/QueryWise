# QueryWise Evaluation Log

These are local development and validation checks for the prompts and application. The question sets are small and locally authored; they are not a general accuracy benchmark.

## Manual development checks

| Case | Question | Expected result | Outcome |
|---|---|---|---|
| 1 | Which five products had the highest sales? | Top five product codes and sales matched the trusted SQL query | Pass |
| 2 | What were merchandise sales in March 2010? | £762,148.53 | Pass after adding the date-boundary rule |
| 3 | Which country had the highest product sales? | United Kingdom | Pass |
| 4 | Show the top five countries by product sales with totals | UK, EIRE, Netherlands, Germany, France and amounts matched | Pass |
| 5 | Which customer generated the highest product sales, and what was the total? | Customer 18102, £580,987.04 | Pass after excluding missing IDs and selecting the total |
| 6 | Delete all sales rows | Refused before model call; database remained unchanged | Safety pass |

## Repeatable local evaluation

The final SQLite evaluation on 2026-11-01 produced:

- Development suite: 5 of 5 correctness cases matched trusted reference results; the write-request guard passed. Model time was about 122 seconds.
- The five cross-backend validation questions matched all five references on both SQLite and PostgreSQL after fixes; each backend's write-request guard passed. These are validation cases, not holdout.
- PostgreSQL development suite: 5 of 5 correctness cases matched trusted reference results; the write-request guard passed. Model time was about 32 seconds.
- Cross-backend validation: all five cases matched on both SQLite and PostgreSQL; the write-request guard passed on both.
- The eight-case validation suite and the cross-backend suite have both been used for error analysis; do not report them as untouched holdouts.
- Detailed SQL, expected and actual rows, timings, and error stages are saved in ignored CSV files in the project.

The eight-case validation suite was used during error analysis and should not be reported as a blind benchmark. The initial PostgreSQL evaluation exposed that the model sometimes used SQLite's `is_product = 1` syntax for a PostgreSQL BOOLEAN column. The prompt now explicitly uses `TRUE`/`FALSE`; the final full PostgreSQL development run passed.

Run the development evaluation while Ollama is available:

    python run_evaluation.py

Run the cross-backend validation evaluation:

    python run_evaluation.py --suite cross_backend

Run the PostgreSQL development evaluation (the password prompt is hidden):

    python run_evaluation.py --backend postgres

## Uploaded-data smoke check

A synthetic CSV with headers Order ID, Region, and Revenue ($) was loaded as a local table. Headers were normalized to order_id, region, and revenue. A grouped SUM query returned East = 20 and West = 15. The table allowlist and read-only query execution worked.

## Large local-file import smoke check

- A temporary 100,005-row CSV imported through multiple 50,000-row batches. SQLite returned 100,005 rows and the expected amount sum of 150,007.50.
- A small Parquet file imported successfully in one-row batches and returned the expected grouped totals.
- Both tests used the existing read-only SQLite query runner.
- The app's Python files compile successfully after adding the large local-file source mode.

## Errors found and fixes

- The model initially ranked individual transaction rows instead of product totals. The prompt now requires aggregation for rankings and ordering by the aggregate expression.
- A date query initially used a date-only inclusive end and could omit transactions later on the last day. The prompt now uses an exclusive next-day or next-month boundary.
- The model initially grouped NULL customer IDs into one apparent customer. Customer questions now exclude NULL IDs.
- The model initially returned only a customer ID. Customer rankings now request both the ID and aggregate amount.
- The model initially answered a delete request with a read query. The app now blocks write-intent requests before contacting the model, and SQLite also opens the database read-only and authorizes reads only from the selected table.
- A later development rerun grouped March sales by product when the question asked for a single total. The prompt now says to add grouping dimensions only when the user asks for a breakdown.
- The first validation run exposed two evaluation/data issues: empty expected and actual results could be counted as a pass, and the prepared demo had no retained return rows. In the source workbook, negative-quantity lines are either cancellations or have non-positive prices, so the preparation rules exclude them. The evaluator now rejects empty reference results; the documentation no longer claims the demo totals include returns.
- The model once invented a `sales.sales` table path. The prompt now tells it to use table names exactly as provided and not add schema prefixes.
- A customer ranking initially treated NULL customer IDs as a single customer group. The prompt now explains the required filter and includes an SQL example; the final development run passed this case.
- PostgreSQL rejected SQLite-style integer comparisons against the BOOLEAN `is_product` column. The prompt now requires TRUE/FALSE for PostgreSQL booleans; the final five-case PostgreSQL development run passed.
- The model sometimes omitted the merchandise filter on counts and quantity questions; one query coincidentally returned the reference result without the filter. The prompt now requires the merchandise filter for product counts, quantities, rankings, and sales, and the evaluator checks required filters in addition to comparing results.

The final results apply only to these prepared-data questions and this local model configuration. They do not guarantee correct answers for arbitrary user questions or other datasets.

