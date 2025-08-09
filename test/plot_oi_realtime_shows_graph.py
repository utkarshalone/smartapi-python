import pandas as pd
import matplotlib.pyplot as plt
import os
import time

excel_filename = "oi_data_{}.xlsx".format(pd.Timestamp.today().strftime('%Y%m%d'))

if not os.path.exists(excel_filename):
    print(f"File {excel_filename} not found in current directory.")
    exit(1)

xls = pd.ExcelFile(excel_filename)
sheet_names = xls.sheet_names

plt.ion()  # Turn on interactive mode

# Layout for 18 subplots (e.g., 6 rows x 3 cols)
num_sheets = len(sheet_names)
rows = int(num_sheets ** 0.5)
cols = (num_sheets + rows - 1) // rows
fig, axes = plt.subplots(rows, cols, figsize=(cols * 5, rows * 3))
axes = axes.flatten() if num_sheets > 1 else [axes]

while True:
    xls = pd.ExcelFile(excel_filename)
    for idx, sheet in enumerate(sheet_names):
        df = pd.read_excel(xls, sheet_name=sheet)
        if 'opnInterest' not in df.columns or 'fetch_time' not in df.columns:
            axes[idx].set_visible(False)
            continue
        df = df.sort_values('fetch_time')
        df['fetch_time'] = pd.to_datetime(df['fetch_time'])
        # Limit to last 90 minutes for clarity
        if not df.empty:
            latest_time = df['fetch_time'].iloc[-1]
            min_time = latest_time - pd.Timedelta(minutes=90)
            df = df[df['fetch_time'] >= min_time]
        x = df['fetch_time']
        y = df['opnInterest']
        axes[idx].clear()
        axes[idx].plot(x, y, marker='o')
        # Get latest LTP and OI
        ltp = df['ltp'].iloc[-1] if 'ltp' in df.columns and not df['ltp'].isnull().all() else 'N/A'
        oi = df['opnInterest'].iloc[-1] if not df['opnInterest'].isnull().all() else 'N/A'
        # Format OI in lacs
        def fmt_lacs(val):
            try:
                return f"{val/1e5:.2f}L"
            except Exception:
                return val
        oi_fmt = fmt_lacs(oi) if oi != 'N/A' else 'N/A'
        # Calculate change in OI over last 5, 10, and 15 minutes, also format in lacs
        changes = []
        now = df['fetch_time'].iloc[-1] if not df.empty else pd.Timestamp.now()
        for mins in [5, 10, 15]:
            past_time = now - pd.Timedelta(minutes=mins)
            past_df = df[df['fetch_time'] <= past_time]
            if not past_df.empty:
                past_oi = past_df['opnInterest'].iloc[-1]
                change = oi - past_oi if oi != 'N/A' and not pd.isnull(past_oi) else 'N/A'
            else:
                change = 'N/A'
            changes.append(fmt_lacs(change) if change != 'N/A' else 'N/A')
        axes[idx].set_title(
            f"{sheet}\nLTP: {ltp} | OI: {oi_fmt}\n"
            f"ΔOI 5m: {changes[0]} | 10m: {changes[1]} | 15m: {changes[2]}",
            fontsize=8
        )
        axes[idx].set_xlabel("Time")
        axes[idx].set_ylabel("Open Interest")
        axes[idx].grid(True)
        # Rotate x-tick labels for clarity
        for label in axes[idx].get_xticklabels():
            label.set_rotation(30)
            label.set_ha('right')
    # Hide unused axes if any
    for j in range(len(sheet_names), len(axes)):
        axes[j].set_visible(False)
        fig.suptitle("Change in Open Interest for All Strikes", fontsize=16)
        fig.canvas.draw()
        fig.canvas.flush_events()
        time.sleep(30)  # Update every 30 seconds
    
    # Call tight_layout once after creating the figure and axes
    fig.tight_layout(rect=[0, 0.03, 1, 0.95])
