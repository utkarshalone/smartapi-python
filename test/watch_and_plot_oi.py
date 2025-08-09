import os
import time
from plot_oi_realtime import excel_filename
import plot_oi_realtime

def get_mtime(path):
    try:
        return os.path.getmtime(path)
    except Exception:
        return None

def main():
    last_mtime = get_mtime(excel_filename)
    print(f"Watching {excel_filename} for changes...")
    while True:
        time.sleep(5)  # Check every 5 seconds
        mtime = get_mtime(excel_filename)
        if mtime and mtime != last_mtime:
            print(f"Detected change in {excel_filename}, re-plotting...")
            plot_oi_realtime.main()
            last_mtime = mtime

if __name__ == "__main__":
    main()
