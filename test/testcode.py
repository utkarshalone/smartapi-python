from logzero import logger
from SmartApi.smartConnect import SmartConnect
import pyotp

api_key = 'rMJaGlTh'
username = 'U21226'
pwd = '4444'
smartApi = SmartConnect(api_key)

try:
    token = "4WD4HHYWSCEL5GPK7WXKUCPNVM"
    totp = pyotp.TOTP(token).now()
except Exception as e:
    logger.error("Invalid Token: The provided token is not valid.")
    raise e

correlation_id = "abcde"
data = smartApi.generateSession(username, pwd, totp)
if data['status'] == False:
    logger.error(data)
else:
    # logger.info(f"data: {data}")
    authToken = data['data']['jwtToken']
    refreshToken = data['data']['refreshToken']
    feedToken = smartApi.getfeedToken()
    # logger.info(f"Feed-Token :{feedToken}")
    res = smartApi.getProfile(refreshToken)
    # logger.info(f"Get Profile: {res}")
    smartApi.generateToken(refreshToken)
    res=res['data']['exchanges']

print(res)



# import json
# holdings = smartApi.holding()
# logger.info("Holdings :\n%s", json.dumps(holdings, indent=2))

# Fetch OI data for NSE
oi_params = {
    "name":"NSE", 
    "expirydate":"7AUG2025"
}



# print(dir(smartApi))
# oi_data = smartApi.getOIData(oi_params)

# logger.info("OI Data (NSE):\n%s", json.dumps(oi_data, indent=2))







import requests
import pandas as pd
import math
import json
import time
from datetime import datetime


API_KEY = "rMJaGlTh"
AUTH_TOKEN = "eyJhbGciOiJIUzUxMiJ9.eyJ1c2VybmFtZSI6IlUyMTIyNiIsInJvbGVzIjowLCJ1c2VydHlwZSI6IlVTRVIiLCJ0b2tlbiI6ImV5SmhiR2NpT2lKU1V6STFOaUlzSW5SNWNDSTZJa3BYVkNKOS5leUoxYzJWeVgzUjVjR1VpT2lKamJHbGxiblFpTENKMGIydGxibDkwZVhCbElqb2lkSEpoWkdWZllXTmpaWE56WDNSdmEyVnVJaXdpWjIxZmFXUWlPalFzSW5OdmRYSmpaU0k2SWpNaUxDSmtaWFpwWTJWZmFXUWlPaUkxTVdWak1qY3hNaTB4WlRWbExUTTJOalF0WVRSbU1DMDJaV1EyTnpRMU1HTTFPR0VpTENKcmFXUWlPaUowY21Ga1pWOXJaWGxmZGpJaUxDSnZiVzVsYldGdVlXZGxjbWxrSWpvMExDSndjbTlrZFdOMGN5STZleUprWlcxaGRDSTZleUp6ZEdGMGRYTWlPaUpoWTNScGRtVWlmU3dpYldZaU9uc2ljM1JoZEhWeklqb2lZV04wYVhabEluMHNJbTVpZFV4bGJtUnBibWNpT25zaWMzUmhkSFZ6SWpvaVlXTjBhWFpsSW4xOUxDSnBjM01pT2lKMGNtRmtaVjlzYjJkcGJsOXpaWEoyYVdObElpd2ljM1ZpSWpvaVZUSXhNakkySWl3aVpYaHdJam94TnpVME5UZ3hOelExTENKdVltWWlPakUzTlRRME9UVXhOalVzSW1saGRDSTZNVGMxTkRRNU5URTJOU3dpYW5ScElqb2lOVEZoTmpreFpUUXRNMkV6WWkwME56WmxMVGd3WTJRdE1qSmhORFZqWmpJMU9EVTFJaXdpVkc5clpXNGlPaUlpZlEuZ0NoQW43N3hZR1o4MXctQXlneEJ3NDgxMzlWWDgzSHRDdk1YZ0RGcHNNa2lPSmdCblYzY3A5RGZZNHZzRGxHTlFZUlIxTlNtMXozZHV2NkhmNGVlTnM4YWdHd3l5WWdkMzZaVTA4MEs2UTZQbWRGTTgtaDJFdHZURzJ2OGJLMTVZWUZFRDBMQkgyRkhyZWJRcjFndEdJRnJOQ2dtRnNtOTVGdDdRaDJTNGVNIiwiQVBJLUtFWSI6InJNSmFHbFRoIiwiaWF0IjoxNzU0NDk1MzQ1LCJleHAiOjE3NTQ1ODE3NDV9.CsYNxHEDc2718JOeX1792uEsqBOGYO87_jfmWppZdd1eTo4qOCHXfBa8d1j2nWmIjnUwLCgnu_10SwqdWs7A1g"  # from AngelOne login
BATCH_SIZE = 50

url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
df = pd.DataFrame(requests.get(url).json())
df.columns = df.columns.str.lower()

df = df[(df["name"] == "NIFTY") & (df["instrumenttype"] == "OPTIDX")]
df["expiry"] = pd.to_datetime(df["expiry"], format="%d%b%Y", errors="coerce")
nearest_expiry = df["expiry"].min()
latest_df = df[df["expiry"] == nearest_expiry]

tokens = latest_df["token"].tolist()
print(f"Total tokens: {len(tokens)}")

def fetch_batch(token_list):
    payload = {
        "mode": "FULL",
        "exchangeTokens": {
            "NFO": token_list
        }
    }
    headers = {
        "X-PrivateKey": API_KEY,
        "Accept": "application/json",
        "X-SourceID": "WEB",
        "X-UserType": "USER",
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "Content-Type": "application/json"
    }
    resp = requests.post(
        "https://apiconnect.angelone.in/rest/secure/angelbroking/market/v1/quote/",
        json=payload,
        headers=headers
    )
    return resp.json()


all_data = []


excel_filename = f"oi_data_{datetime.now().strftime('%Y%m%d')}.xlsx"
symbol_data_dict = {}

while True:
    fetch_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"Fetching data at {fetch_time}")
    for i in range(0, len(tokens), BATCH_SIZE):
        batch = tokens[i:i+BATCH_SIZE]
        print(f"  Batch {i//BATCH_SIZE + 1} ({len(batch)} tokens)")
        data = fetch_batch(batch)
        all_data.append(data)

        # Flatten and collect each trading symbol's data
        if "data" in data and "fetched" in data["data"]:
            fetched = data["data"]["fetched"]
            if isinstance(fetched, dict):
                items = fetched.items()
            elif isinstance(fetched, list):
                items = []
                for entry in fetched:
                    # symbol = entry.get("tradingsymbol") or entry.get("symboltoken") or entry.get("symbol") or f"symbol_{fetched.index(entry)}"
                    # items.append((symbol, entry))
                    symbol = entry.get("tradingSymbol") or entry.get("tradingsymbol") or entry.get("symboltoken") or entry.get("symbol") or f"symbol_{i}"
                    items.append((symbol, entry))
            else:
                items = []
            for symbol, symbol_data in items:
                df_symbol = pd.json_normalize(symbol_data)
                df_symbol['fetch_time'] = fetch_time
                if symbol not in symbol_data_dict:
                    symbol_data_dict[symbol] = []
                symbol_data_dict[symbol].append(df_symbol)

    # Write all collected data to Excel, each symbol to its own sheet
    # Read existing data if file exists
    try:
        with pd.ExcelWriter(excel_filename, engine="openpyxl", mode="a", if_sheet_exists="overlay") as excel_writer:
            for symbol, df_list in symbol_data_dict.items():
                df_concat = pd.concat(df_list, ignore_index=True)
                sheet_name = str(symbol)[:31]
                # Try to read existing sheet
                try:
                    existing_df = pd.read_excel(excel_filename, sheet_name=sheet_name)
                    df_concat = pd.concat([existing_df, df_concat], ignore_index=True)
                except Exception:
                    pass  # Sheet doesn't exist, just write new data
                df_concat.to_excel(excel_writer, sheet_name=sheet_name, index=False)
    except FileNotFoundError:
        with pd.ExcelWriter(excel_filename, engine="openpyxl") as excel_writer:
            for symbol, df_list in symbol_data_dict.items():
                df_concat = pd.concat(df_list, ignore_index=True)
                sheet_name = str(symbol)[:31]
                df_concat.to_excel(excel_writer, sheet_name=sheet_name, index=False)
    print(f"Data written to Excel file: {excel_filename}")
    print("Waiting 1 minute before next update...")
    time.sleep(60)

