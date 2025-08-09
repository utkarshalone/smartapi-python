import pandas as pd
import time
import os

def write_excel_rows_with_delay():
    """
    Read Excel file and write data rows in batches of 5 with 2-second intervals,
    skipping rows that already exist in the output file.
    """
    INPUT_FILE = "/Users/utkarshalone/angel_one_test/smartapi-python/oi_data_20250807.xlsx"
    OUTPUT_FILE = "/Users/utkarshalone/angel_one_test/oi_data_20250809.xlsx"
    DELAY_SECONDS = 1
    BATCH_SIZE = 18

    print(f"Starting Excel row writer...")
    print(f"Input file: {INPUT_FILE}")
    print(f"Output file: {OUTPUT_FILE}")
    print(f"Delay: {DELAY_SECONDS} seconds between batches of {BATCH_SIZE} rows")
    print("-" * 50)

    if not os.path.exists(INPUT_FILE):
        print(f"❌ Error: Input file '{INPUT_FILE}' not found!")
        return

    try:
        # Read all sheets from input file
        print("📖 Reading input file...")
        all_sheets = pd.read_excel(INPUT_FILE, sheet_name=None)
        print(f"✅ Found {len(all_sheets)} sheets: {list(all_sheets.keys())}")

        max_rows = max(len(df) for df in all_sheets.values())
        print(f"📊 Maximum rows in any sheet: {max_rows}")

        # Initialize output file with headers only if not exists or empty
        if not os.path.exists(OUTPUT_FILE) or os.path.getsize(OUTPUT_FILE) == 0:
            print("📝 Initializing output file with headers only...")
            with pd.ExcelWriter(OUTPUT_FILE, engine='openpyxl') as writer:
                for sheet_name, df in all_sheets.items():
                    empty_df = pd.DataFrame(columns=df.columns)
                    empty_df.to_excel(writer, sheet_name=sheet_name, index=False)
            print("✅ Output file initialized")

        print(f"\n🚀 Starting batch processing...")

        for start_row in range(0, max_rows, BATCH_SIZE):
            end_row = min(start_row + BATCH_SIZE, max_rows)
            print(f"\n⏰ Processing rows {start_row + 1} to {end_row} / {max_rows}")

            for sheet_name, source_df in all_sheets.items():
                # Read existing output for the sheet
                try:
                    existing_df = pd.read_excel(OUTPUT_FILE, sheet_name=sheet_name)
                except:
                    existing_df = pd.DataFrame(columns=source_df.columns)

                # Slice batch rows, skip if index out of range
                batch_rows = source_df.iloc[start_row:end_row]

                # Skip rows already present - assume rows are unique by their full content
                # Here, we use merge with indicator to find new rows
                if not existing_df.empty:
                    # Merge on all columns to find rows not in existing_df
                    merged = batch_rows.merge(existing_df, how='left', indicator=True)
                    new_rows = merged[merged['_merge'] == 'left_only'].drop(columns=['_merge'])
                else:
                    new_rows = batch_rows

                if not new_rows.empty:
                    # Append new rows
                    updated_df = pd.concat([existing_df, new_rows], ignore_index=True)

                    with pd.ExcelWriter(OUTPUT_FILE, engine='openpyxl', mode='a', if_sheet_exists='replace') as writer:
                        updated_df.to_excel(writer, sheet_name=sheet_name, index=False)

                    print(f"  ✓ Written {len(new_rows)} new rows to sheet '{sheet_name}'")
                else:
                    print(f"  ⚪ No new rows to write for sheet '{sheet_name}' in this batch")

            if end_row < max_rows:
                print(f"  ⏳ Waiting {DELAY_SECONDS} seconds before next batch...")
                time.sleep(DELAY_SECONDS)

        print(f"\n🎉 Processing completed!")
        print(f"📁 Output saved to: {OUTPUT_FILE}")
        print(f"📊 Total rows processed (max): {max_rows}")

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    write_excel_rows_with_delay()
