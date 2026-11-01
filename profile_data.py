from pathlib import Path
import pandas as pd

file_path = Path("online_retail_II.xlsx")

if not file_path.exists():
    raise FileNotFoundError(f"Can't find {file_path}")

# Combine both years into one table.
sheets = pd.read_excel(file_path, sheet_name=None)
df = pd.concat(sheets.values(), ignore_index=True)

# Standardize column names and prepare date values.
df.columns = (
    df.columns.astype(str)
    .str.strip()
    .str.lower()
    .str.replace(" ", "_")
)
df["invoicedate"] = pd.to_datetime(df["invoicedate"], errors="coerce")

# Report the data quality before changing or removing any rows.
print(f"Rows: {len(df):,}")
print(f"Columns: {list(df.columns)}")
print(f"Date range: {df['invoicedate'].min()} to {df['invoicedate'].max()}")
print("\nMissing values:")
print(df.isna().sum().to_string())

invoice_text = df["invoice"].astype(str).str.strip()
print(f"\nCancellation rows (invoice starts with C): {invoice_text.str.upper().str.startswith('C').sum():,}")
print(f"Rows with negative quantity: {(df['quantity'] < 0).sum():,}")
print(f"Rows with zero or negative price: {(df['price'] <= 0).sum():,}")
print(f"Exact duplicate rows: {df.duplicated().sum():,}")

print("\nSample rows:")
print(df.head(5).to_string(index=False))