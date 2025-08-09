import os
import re
import datetime
from functools import lru_cache

import pandas as pd
import numpy as np
import plotly.graph_objs as go
import dash
from dash import dcc, html
from dash.dependencies import Input, Output, State

# -------------------------
# CONFIG
# -------------------------
EXCEL_FILENAME = f"oi_data_{datetime.datetime.now().strftime('%Y%m%d')}.xlsx"
INTERVAL_MS = 30 * 1000            # refresh interval (ms)
PLOT_MINUTES = 390                 # show last 90 minutes in plot
HISTORY_MINUTES = 390              # keep last 120 minutes for calculations
VOL_WINDOW_MINUTES = 15            # window to compute volatility
VOL_LTP_THRESHOLD_PCT = 0.02       # 2% of mean LTP
VOL_OI_THRESHOLD_PCT = 0.05        # 5% of mean OI
USECOLS = None                     # None -> read all; if you want fast, set to ['tradingSymbol','ltp','opnInterest','fetch_time']

# -------------------------
# GLOBAL CACHE (in-memory)
# -------------------------
_data_cache = {}            # sheet_name -> DataFrame (already filtered to last HISTORY_MINUTES)
_last_mtime = None          # last file mtime used to load cache

# -------------------------
# UTILITIES
# -------------------------
@lru_cache(maxsize=512)
def extract_strike(name: str):
    """Extract numeric strike from tradingSymbol or sheet name."""
    if not isinstance(name, str):
        return 0
    m = re.search(r'(\d{3,})', name)
    return int(m.group(1)) if m else 0

@lru_cache(maxsize=512)
def is_call(name: str):
    """Return True if CE (call). Case-insensitive."""
    if not isinstance(name, str):
        return False
    s = name.upper()
    return s.endswith('CE') or 'CE' in s and 'PE' not in s

def fmt_lacs(val):
    try:
        return f"{val/1e5:.2f}L"
    except Exception:
        return val

# -------------------------
# FAST & SAFE READ + CACHE
# -------------------------
def load_data_if_changed(filename=EXCEL_FILENAME, usecols=USECOLS):
    """
    Load Excel workbook only when file changed. Keeps last HISTORY_MINUTES in each DF.
    Returns dict: sheet -> DataFrame.
    """
    global _data_cache, _last_mtime

    if not os.path.exists(filename):
        # file missing -> clear cache and return empty
        _data_cache = {}
        _last_mtime = None
        return {}

    mtime = os.path.getmtime(filename)
    if _last_mtime is not None and mtime == _last_mtime and _data_cache:
        # no change -> return cached
        return _data_cache

    # file changed or first load -> read workbook once
    try:
        xls = pd.ExcelFile(filename)
    except Exception as e:
        print(f"[load_data_if_changed] Error opening Excel: {e}")
        return {}

    cutoff_time = pd.Timestamp(datetime.datetime.now() - datetime.timedelta(minutes=HISTORY_MINUTES))
    new_cache = {}

    for sheet in sorted(xls.sheet_names):
        try:
            # read sheet; optionally restrict columns for speed
            if usecols:
                df = pd.read_excel(xls, sheet_name=sheet, usecols=usecols)
            else:
                df = pd.read_excel(xls, sheet_name=sheet)
        except Exception as e:
            print(f"[load_data_if_changed] Error reading sheet {sheet}: {e}")
            continue

        if df is None or df.empty:
            continue
        if 'fetch_time' not in df.columns or 'opnInterest' not in df.columns:
            continue

        # convert and sort
        df['fetch_time'] = pd.to_datetime(df['fetch_time'], errors='coerce')
        df = df.dropna(subset=['fetch_time']).sort_values('fetch_time').reset_index(drop=True)

        # deduplicate by fetch_time + opnInterest (avoid exact duplicates)
        if 'opnInterest' in df.columns:
            df = df.drop_duplicates(subset=['fetch_time', 'opnInterest'], keep='last').reset_index(drop=True)

        # keep last HISTORY_MINUTES
        latest_ts = df['fetch_time'].iloc[-1]
        cutoff = latest_ts - pd.Timedelta(minutes=HISTORY_MINUTES)
        df = df[df['fetch_time'] >= cutoff].reset_index(drop=True)

        if df.empty:
            continue

        new_cache[sheet] = df

    # update global cache and mtime
    _data_cache = new_cache
    _last_mtime = mtime
    print(f"[load_data_if_changed] Loaded {len(new_cache)} sheets from {filename} (mtime updated).")
    return _data_cache

# -------------------------
# ANALYTICS: trend, volatility, PCR, max pain
# -------------------------
def compute_trend(df, minutes_back=3):
    """
    Compute trend against value minutes_back ago. Returns dict of deltas, pct and arrows for oi & ltp.
    """
    out = {'oi': np.nan, 'ltp': np.nan, 'oi_delta': None, 'ltp_delta': None,
           'oi_pct': None, 'ltp_pct': None, 'oi_arrow': '➖', 'ltp_arrow': '➖'}
    if df is None or df.empty:
        return out
    last = df.iloc[-1]
    out['oi'] = last.get('opnInterest', np.nan)
    out['ltp'] = last.get('ltp', np.nan)
    now = last['fetch_time']
    past_time = now - pd.Timedelta(minutes=minutes_back)
    past_df = df[df['fetch_time'] <= past_time]
    if not past_df.empty:
        prev = past_df.iloc[-1]
        prev_oi = prev.get('opnInterest', np.nan)
        prev_ltp = prev.get('ltp', np.nan)
        if pd.notna(out['oi']) and pd.notna(prev_oi):
            out['oi_delta'] = out['oi'] - prev_oi
            out['oi_pct'] = (out['oi_delta'] / prev_oi * 100) if prev_oi != 0 else None
            out['oi_arrow'] = '📈' if out['oi_delta'] > 0 else ('📉' if out['oi_delta'] < 0 else '➖')
        if pd.notna(out['ltp']) and pd.notna(prev_ltp):
            out['ltp_delta'] = out['ltp'] - prev_ltp
            out['ltp_pct'] = (out['ltp_delta'] / prev_ltp * 100) if prev_ltp != 0 else None
            out['ltp_arrow'] = '📈' if out['ltp_delta'] > 0 else ('📉' if out['ltp_delta'] < 0 else '➖')
    return out

def compute_volatility(df, window_minutes=VOL_WINDOW_MINUTES):
    """Compute std dev over last window_minutes for ltp & oi and return flag if high volatility."""
    out = {'std_ltp': None, 'std_oi': None, 'vol_flag': False}
    if df is None or df.empty:
        return out
    last_ts = df['fetch_time'].iloc[-1]
    win_cut = last_ts - pd.Timedelta(minutes=window_minutes)
    window = df[df['fetch_time'] >= win_cut]
    if window.empty:
        return out
    std_ltp = window['ltp'].std(ddof=0) if 'ltp' in window.columns else None
    std_oi = window['opnInterest'].std(ddof=0)
    mean_ltp = window['ltp'].mean() if 'ltp' in window.columns else None
    mean_oi = window['opnInterest'].mean()
    vol_flag = False
    if std_ltp is not None and mean_ltp is not None and mean_ltp != 0:
        if std_ltp > abs(mean_ltp) * VOL_LTP_THRESHOLD_PCT:
            vol_flag = True
    if std_oi is not None and mean_oi is not None and mean_oi != 0:
        if std_oi > abs(mean_oi) * VOL_OI_THRESHOLD_PCT:
            vol_flag = True
    out.update({'std_ltp': std_ltp, 'std_oi': std_oi, 'vol_flag': vol_flag})
    return out

def compute_pcr_maxpain(data_dict):
    """
    Compute PCR (PE OI / CE OI) using latest OI per symbol; compute max pain across strikes.
    Returns dict with pcr, totals, max_pain strike.
    """
    calls = {}
    puts = {}
    strikes = set()

    for sheet, df in data_dict.items():
        if df is None or df.empty:
            continue
        last = df.iloc[-1]
        ts = last.get('tradingSymbol') if 'tradingSymbol' in last else sheet
        strike = extract_strike(str(ts))
        strikes.add(strike)
        oi = last.get('opnInterest', 0) or 0
        name = str(ts).upper()
        if 'PE' in name and 'CE' not in name:
            puts[strike] = puts.get(strike, 0) + oi
        elif 'CE' in name and 'PE' not in name:
            calls[strike] = calls.get(strike, 0) + oi
        else:
            # fallback to sheet suffix
            if sheet.upper().endswith('CE'):
                calls[strike] = calls.get(strike, 0) + oi
            elif sheet.upper().endswith('PE'):
                puts[strike] = puts.get(strike, 0) + oi
            else:
                # unknown, attempt is_call heuristics
                if is_call(name):
                    calls[strike] = calls.get(strike, 0) + oi
                else:
                    puts[strike] = puts.get(strike, 0) + oi

    total_ce = sum(calls.values())
    total_pe = sum(puts.values())
    pcr = (total_pe / total_ce) if total_ce != 0 else (float('inf') if total_pe > 0 else None)

    # Max Pain: minimize summed payouts (distance * OI)
    if not strikes:
        return {'pcr': pcr, 'total_ce': total_ce, 'total_pe': total_pe, 'max_pain': None}

    strikes_list = sorted(list(strikes))
    pain = {}
    for s in strikes_list:
        total_pain = 0.0
        for cs, coi in calls.items():
            total_pain += max(0, cs - s) * coi
        for ps, poi in puts.items():
            total_pain += max(0, s - ps) * poi
        pain[s] = total_pain

    max_pain_strike = min(pain, key=lambda k: pain[k])
    return {'pcr': pcr, 'total_ce': total_ce, 'total_pe': total_pe, 'max_pain': max_pain_strike, 'pain': pain}

# -------------------------
# PLOTTING / UI HELPERS
# -------------------------
def build_summary_card(analysis):
    """Return a compact card (PCR, totals, max pain, top vol)."""
    pcr = analysis.get('pcr')
    total_ce = analysis.get('total_ce', 0)
    total_pe = analysis.get('total_pe', 0)
    max_pain = analysis.get('max_pain')
    pcr_str = f"{pcr:.2f}" if (pcr is not None and pcr != float('inf')) else ("∞" if pcr == float('inf') else "N/A")
    max_pain_str = str(max_pain) if max_pain is not None else "N/A"

    return html.Div([
        html.Div([
            html.H4("Market Summary", style={'margin': '0 0 6px 0'}),
            html.Div(f"Total CE OI: {fmt_lacs(total_ce)}"),
            html.Div(f"Total PE OI: {fmt_lacs(total_pe)}"),
            html.Div(f"PCR (PE/CE): {pcr_str}"),
            html.Div(f"Max Pain Strike: {max_pain_str}", style={'fontWeight': 700})
        ], style={'padding': '8px'})
    ], style={'border': '1px solid #ddd', 'borderRadius': '6px', 'backgroundColor': '#fafafa', 'marginBottom': '8px'})

def compute_market_summary(data_dict):
    total_ce = sum(df['opnInterest'].iloc[-1] for k, df in data_dict.items() if k.lower().endswith('ce'))
    total_pe = sum(df['opnInterest'].iloc[-1] for k, df in data_dict.items() if k.lower().endswith('pe'))
    pcr = total_pe / total_ce if total_ce != 0 else None
    
    # Max Pain: strike with lowest total OI pain
    strike_oi = {}
    for k, df in data_dict.items():
        strike = extract_strike(k)
        strike_oi.setdefault(strike, 0)
        strike_oi[strike] += df['opnInterest'].iloc[-1]
    max_pain_strike = min(strike_oi, key=strike_oi.get) if strike_oi else None
    
    # Bias rule: if PCR > 1 → Sell CE bias, if PCR < 1 → Sell PE bias
    bias = "Sell CE" if pcr and pcr > 1 else "Sell PE" if pcr else "Neutral"
    
    return {
        "total_ce": fmt_lacs(total_ce),
        "total_pe": fmt_lacs(total_pe),
        "pcr": f"{pcr:.2f}" if pcr else "N/A",
        "max_pain": max_pain_strike,
        "bias": bias
    }



def build_individual_graph(symbol, df, show_trend=True):
    """
    Build the individual OI graph with enhanced title/hover.
    """
    if df is None or df.empty:
        return html.Div(f"No data for {symbol}", style={'padding': '10px'})

    # plot frame: last PLOT_MINUTES
    latest = df['fetch_time'].iloc[-1]
    plot_cut = latest - pd.Timedelta(minutes=PLOT_MINUTES)
    plot_df = df[df['fetch_time'] >= plot_cut].copy()
    if plot_df.empty:
        plot_df = df.copy()

    # compute metrics
    trend_3 = compute_trend(df, minutes_back=3)
    vol = compute_volatility(df, window_minutes=VOL_WINDOW_MINUTES)

    ltp = trend_3.get('ltp')
    oi = trend_3.get('oi')
    oi_fmt = fmt_lacs(oi) if pd.notna(oi) else "N/A"

    # build title with arrows and colored span
    oi_arrow = trend_3.get('oi_arrow', '➖')
    ltp_arrow = trend_3.get('ltp_arrow', '➖')
    oi_pct = f"{trend_3.get('oi_pct'):.2f}%" if trend_3.get('oi_pct') is not None else "N/A"
    ltp_pct = f"{trend_3.get('ltp_pct'):.2f}%" if trend_3.get('ltp_pct') is not None else "N/A"

    vol_flag = " ⚡" if vol.get('vol_flag') else ""
    # clickable title anchor (can be enhanced to link to a modal)
    title_html = f"{symbol} | LTP: {ltp if pd.notna(ltp) else 'N/A'} {ltp_arrow} ({ltp_pct}) | OI: {oi_fmt} {oi_arrow} ({oi_pct}){vol_flag}"

    # build plotly figure
    fig = go.Figure()
    has_ltp = 'ltp' in plot_df.columns and not plot_df['ltp'].isnull().all()
    customdata = plot_df['ltp'].round(2) if has_ltp else None

    fig.add_trace(go.Scatter(
        x=plot_df['fetch_time'],
        y=plot_df['opnInterest'],
        mode='lines+markers',
        name='OI',
        customdata=customdata,
        hovertemplate=('Contract: ' + str(symbol) + '<br>' +
                       'Time: %{x|%Y-%m-%d %H:%M:%S}<br>' +
                       'OI: %{y}<br>' +
                       (('LTP: %{customdata:.2f}<br>') if has_ltp else '') +
                       '<extra></extra>')
    ))

    fig.update_layout(
        title=dict(text=title_html, font=dict(size=11)),
        xaxis=dict(title='Time', tickformat='%H:%M', tickfont=dict(size=9)),
        yaxis=dict(title='Open Interest', tickfont=dict(size=9)),
        font=dict(size=9),
        height=330,
        margin=dict(l=30, r=30, t=70, b=30)
    )

    # return graph container with anchor id for clickable title
    container = html.Div(
        [
            html.A("", id=f"anchor-{symbol}"),  # anchor for clickable navigation
            dcc.Graph(figure=fig, id=f'graph-{symbol}')
        ],
        style={'flex': '1', 'padding': '8px', 'minWidth': '48%'}
    )
    return container


# -------------------------
# DASH APP & LAYOUT
# -------------------------
app = dash.Dash(__name__)
app.title = "OI Realtime Dashboard (Enhanced)"

app.layout = html.Div([
    html.H1("Change in Open Interest & Insights V1.3", style={"textAlign": "center"}),

    # top controls
    html.Div([
        html.Div([
            dcc.Checklist(
                id='show-trend-toggle',
                options=[{'label': ' Show Trend Indicators (arrows & %)', 'value': 'show'}],
                value=['show'],
                inputStyle={'margin-right': '5px'}
            )
        ], style={'display': 'inline-block', 'marginRight': '16px'}),

        html.Div([
            html.Label("Filter Symbols:", style={'marginRight': '8px'}),
            dcc.Dropdown(id='symbol-filter', options=[], multi=True, placeholder="Choose symbols to show...")
        ], style={'display': 'inline-block', 'width': '45%'}),

        html.Div([
            dcc.RadioItems(
                id='type-filter',
                options=[
                    {'label': 'All', 'value': 'all'},
                    {'label': 'CE only', 'value': 'ce'},
                    {'label': 'PE only', 'value': 'pe'}
                ],
                value='all',
                labelStyle={'display': 'inline-block', 'margin-right': '8px'}
            )
        ], style={'display': 'inline-block', 'marginLeft': '12px'})
    ], style={'padding': '6px 12px', 'display': 'flex', 'alignItems': 'center', 'justifyContent': 'space-between'}),

    html.Div(id='status-indicator', style={'textAlign': 'center', 'color': 'green', 'marginBottom': '6px'}),

    dcc.Interval(id='interval', interval=INTERVAL_MS, n_intervals=0),

    # summary card
    html.Div(id='summary-row'),

    # graphs container
    html.Div(id='graphs-container')
], style={'padding': '8px'})

# -------------------------
# CALLBACKS
# -------------------------
@app.callback(
    Output('symbol-filter', 'options'),
    [Input('interval', 'n_intervals')]
)
def update_symbol_options(n):
    """
    Update dropdown options from latest cache. This runs on interval so user sees new symbols.
    """
    data = load_data_if_changed()
    opts = []
    for k in sorted(data.keys(), key=lambda s: extract_strike(s)):
        opts.append({'label': k, 'value': k})
    return opts

@app.callback(
    [Output('graphs-container', 'children'),
     Output('summary-row', 'children'),
     Output('status-indicator', 'children')],
    [Input('interval', 'n_intervals'),
     Input('show-trend-toggle', 'value'),
     Input('symbol-filter', 'value'),
     Input('type-filter', 'value')]
)
def update_graphs(n, show_trend_toggle, symbol_filter, type_filter):
    """
    Main render callback. Uses cached data (load_data_if_changed) so Excel read happens only when file changes.
    """
    start_ts = datetime.datetime.now()
    show_trend = 'show' in (show_trend_toggle or [])
    data = load_data_if_changed()

    if not data:
        msg = f"No data found for file '{EXCEL_FILENAME}'. Waiting for data..."
        return [html.Div(msg, style={'textAlign': 'center', 'color': 'orange', 'padding': '20px'})], None, msg

    # filter keys per user's selections
    keys = sorted(list(data.keys()), key=lambda s: extract_strike(s))
    if symbol_filter:
        # keep only filtered symbols present in keys
        keys = [k for k in keys if k in symbol_filter]

    if type_filter == 'ce':
        keys = [k for k in keys if k.lower().endswith('ce') or is_call(k)]
    elif type_filter == 'pe':
        keys = [k for k in keys if k.lower().endswith('pe') or (not is_call(k) and 'pe' in k.lower())]

    # compute PCR & max pain using all data (not just filtered), because market summary should be global
    analysis = compute_pcr_maxpain(data)
    summary_card = build_summary_card(analysis)

    # compute volatility across symbols to highlight top vol
    vol_list = []
    for k in data:
        vol = compute_volatility(data[k], window_minutes=VOL_WINDOW_MINUTES)
        vol_list.append((k, vol.get('std_oi') or 0, vol.get('vol_flag')))
    # sort by std_oi desc
    vol_list_sorted = sorted(vol_list, key=lambda x: x[1], reverse=True)
    top_vol = vol_list_sorted[:5]

    # build top-volatility mini card
    vol_html = html.Div([
        html.H4("Top Volatile Contracts (OI std)", style={'margin': '0 0 6px 0'}),
        html.Ul([html.Li(f"{k}: std_OI={s:.0f} {'⚡' if f else ''}") for k, s, f in top_vol])
    ], style={'border': '1px solid #eee', 'padding': '8px', 'borderRadius': '6px', 'backgroundColor': '#fff', 'marginBottom': '8px'})

    # build aggregate graphs for CE and PE (if present)
    ce_dfs = [data[k] for k in data if k.lower().endswith('ce')]
    pe_dfs = [data[k] for k in data if k.lower().endswith('pe')]

    def build_aggregate(label, dfs):
        if not dfs:
            return None
        df_all = pd.concat(dfs, ignore_index=True)
        df_all['fetch_time'] = pd.to_datetime(df_all['fetch_time'])
        df_all = df_all.sort_values('fetch_time')
        grouped = df_all.groupby('fetch_time').agg({'opnInterest': 'sum', 'ltp': 'mean'}).reset_index()
        curr_oi = grouped['opnInterest'].iloc[-1]
        curr_ltp = grouped['ltp'].iloc[-1] if 'ltp' in grouped.columns else None
        oi_fmt = fmt_lacs(curr_oi)
        oi_changes = []
        ltp_changes = []
        arrow = ""
        percent = ""
        now = grouped['fetch_time'].iloc[-1]
        for mins in [3,5,10,15]:
            past_time = now - pd.Timedelta(minutes=mins)
            past_df = grouped[grouped['fetch_time'] <= past_time]
            if not past_df.empty:
                past_oi = past_df['opnInterest'].iloc[-1]
                past_ltp = past_df['ltp'].iloc[-1] if 'ltp' in grouped.columns else None
                delta_oi = curr_oi - past_oi
                delta_ltp = curr_ltp - past_ltp if curr_ltp is not None and past_ltp is not None else None
                oi_changes.append(fmt_lacs(delta_oi))
                ltp_changes.append(f"{delta_ltp:.2f}" if delta_ltp is not None else "N/A")
                if mins == 3 and show_trend:
                    arrow = "▲" if delta_oi > 0 else "▼" if delta_oi < 0 else "➖"
                    percent = f"({(delta_oi / past_oi * 100):.1f}%)" if past_oi != 0 else "(∞%)"
            else:
                oi_changes.append("N/A"); ltp_changes.append("N/A")
        title = (
            f"ALL {label.upper()} | OI: {oi_fmt} {arrow} {percent}<br>"
            f"ΔOI  3m: {oi_changes[0]}, 5m: {oi_changes[1]}, 10m: {oi_changes[2]}, 15m: {oi_changes[3]} | <br>"
            f"ΔLTP 3m: {ltp_changes[0]}, 5m: {ltp_changes[1]}, 10m: {ltp_changes[2]}, 15m: {ltp_changes[3]}"
        )
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=grouped['fetch_time'], y=grouped['opnInterest'], mode='lines+markers',
                                 customdata=grouped['ltp'].round(2), hovertemplate="Time: %{x|%H:%M}<br>OI: %{y}<br>LTP: %{customdata:.2f}<extra></extra>"))
        fig.update_layout(title=dict(text=title, font=dict(size=12)), xaxis=dict(tickformat='%H:%M'), height=300,
                          margin=dict(l=30,r=30,t=70,b=30))
        return html.Div(dcc.Graph(figure=fig), style={'padding': '6px', 'minWidth': '48%'})

    agg_ce = build_aggregate('CE', ce_dfs)
    agg_pe = build_aggregate('PE', pe_dfs)

    # now build individual graphs for filtered keys (two-per-row)
    graph_rows = []
    row = []
    count = 0
    for key in keys:
        df = data.get(key)
        comp = build_individual_graph(key, df, show_trend=show_trend)
        row.append(comp)
        count += 1
        if count % 2 == 0:
            graph_rows.append(html.Div(row, style={'display': 'flex', 'flexWrap': 'wrap', 'justifyContent': 'space-between'}))
            row = []
    if row:
        graph_rows.append(html.Div(row, style={'display': 'flex', 'flexWrap': 'wrap', 'justifyContent': 'space-between'}))

    # assemble top area: summary + vol card + aggregates
    top_row = []
    if summary_card:
        top_row.append(summary_card)
    top_row.append(vol_html)
    agg_row = []
    if agg_ce: agg_row.append(agg_ce)
    if agg_pe: agg_row.append(agg_pe)

    # timing & status
    elapsed = (datetime.datetime.now() - start_ts).total_seconds()
    status = f"Updated: {datetime.datetime.now().strftime('%H:%M:%S')} | Sheets: {len(data)} | Render time: {elapsed:.2f}s"

    # final children: summary, aggregates, then graphs
    children = []
    children.append(html.Div(top_row, style={'display': 'flex', 'gap': '12px', 'marginBottom': '8px', 'flexWrap': 'wrap'}))
    if agg_row:
        children.append(html.Div(agg_row, style={'display': 'flex', 'justifyContent': 'space-between', 'marginBottom': '8px'}))
    children.extend(graph_rows)

    return children, summary_card, status

# -------------------------
# RUN APP
# -------------------------
if __name__ == "__main__":
    print("Starting OI Realtime Dashboard (Enhanced).")
    print("Excel filename:", EXCEL_FILENAME)
    app.run(debug=True, port=8050)
