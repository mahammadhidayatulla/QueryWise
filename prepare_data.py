from pathlib import Path

import pandas as pd


SOURCE = Path("online_retail_II.xlsx")
OUTPUT = Path("online_retail_clean.parquet")

NON_PRODUCT_CODES = {
    "M",
    "DOT",
    "POST",
    "AMAZONFEE",
    "B",
    "ADJUST",
    "BANK CHARGES",
    "D",
    "S",
}


if not SOURCE.exists():
    raise FileNotFoundError(
        f"Could not find {SOURCE}. Download the UCI workbook first."
    )

print(f"Reading workbook: {SOURCE}")
sheets = pd.read_excel(SOURCE, sheet_name=None)
frame = pd.concat(sheets.values(), ignore_index=True)

frame.columns = (
    frame.columns.astype(str)
    .str.strip()
    .str.lower()
    .str.replace(" ", "_")
)

frame["stockcode"] = frame["stockcode"].map(
    lambda value: str(value) if pd.notna(value) else None
)
frame["invoice"] = frame["invoice"].astype(str).str.strip()
frame["invoicedate"] = pd.to_datetime(frame["invoicedate"], errors="coerce")

original_rows = len(frame)
duplicate_rows = int(frame.duplicated().sum())
cancelled = frame["invoice"].str.upper().str.startswith("C")
bad_price = frame["price"].isna() | (frame["price"] <= 0)
invalid_dates = frame["invoicedate"].isna()

clean = frame.drop_duplicates().copy()
clean = clean[
    ~clean["invoice"].str.upper().str.startswith("C")
    & clean["price"].notna()
    & (clean["price"] > 0)
    & clean["invoicedate"].notna()
].copy()

# In this workbook, negative-quantity lines are either cancellations or have
# non-positive prices; the cleaning rules below exclude them from the demo.
clean["line_amount"] = clean["quantity"] * clean["price"]

stockcode_upper = clean["stockcode"].str.upper()
non_product_codes = {code.upper() for code in NON_PRODUCT_CODES}
clean["is_product"] = (
    clean["stockcode"].notna()
    & ~stockcode_upper.isin(non_product_codes)
)
clean["product_sales"] = clean["line_amount"].where(clean["is_product"], 0)

clean.to_parquet(OUTPUT, index=False)

print(f"Original rows: {original_rows:,}")
print(f"Exact duplicate rows: {duplicate_rows:,}")
print(f"Cancellation rows: {int(cancelled.sum()):,}")
print(f"Rows with non-positive or missing price: {int(bad_price.sum()):,}")
print(f"Rows with invalid or missing date: {int(invalid_dates.sum()):,}")
print(f"Prepared rows: {len(clean):,}")
print(f"Negative-quantity rows in source: {int((frame['quantity'] < 0).sum()):,}")
print(f"Negative-quantity rows retained: {int((clean['quantity'] < 0).sum()):,}")
print(f"Prepared rows with missing customer ID: {int(clean['customer_id'].isna().sum()):,}")
print(f"Total line amount: £{clean['line_amount'].sum():,.2f}")
print(f"Merchandise product sales: £{clean['product_sales'].sum():,.2f}")
print(f"Saved prepared data to: {OUTPUT}")

