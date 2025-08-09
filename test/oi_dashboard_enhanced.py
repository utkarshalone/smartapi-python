import os
import re
import datetime
from functools import lru_cache

import pandas as pd
import numpy as np
import plotly.graph_objs as go
import dash
from dash import dcc, html
from dash.dependencies import Input, Output

# -------------------------
# CONFIG
# -------------------------
EXCEL_FILENAME = f"oi_data_{datetime.datetime.now().strftime('%Y%m%d')}.xlsx"
INTERVAL_MS = .5 * 1000
PLOT_MINUTES = 390
HISTORY_MINUTES = 390
VOL_WINDOW_MINUTES = 15
VOL_LTP_THRESHOLD_PCT = 0.02
VOL_OI_THRESHOLD_PCT = 0.05
USECOLS = None

# -------------------------
# GLOBAL CACHE
# -------------------------
_data_cache = {}
_last_mtime = None
_analysis_cache = {}

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
    return s.endswith('CE') or ('CE' in s and 'PE' not in s)

@lru_cache(maxsize=512)
def is_put(name: str):
    """Return True if PE (put). Case-insensitive.""" 
    if not isinstance(name, str):
        return False
    s = name.upper()
    return s.endswith('PE') or ('PE' in s and 'CE' not in s)

def fmt_lacs(val):
    """Format value in lakhs."""
    try:
        return f"{val/1e5:.2f}L"
    except Exception:
        return str(val)

# -------------------------
# OPTIMIZED DATA LOADING
# -------------------------
def load_data_if_changed(filename=EXCEL_FILENAME, usecols=USECOLS):
    """Load Excel workbook only when file changed."""
    global _data_cache, _last_mtime, _analysis_cache

    if not os.path.exists(filename):
        _data_cache = {}
        _last_mtime = None
        _analysis_cache = {}
        return {}

    mtime = os.path.getmtime(filename)
    if _last_mtime is not None and mtime == _last_mtime and _data_cache:
        return _data_cache

    # File changed - reload
    try:
        xls = pd.ExcelFile(filename)
    except Exception as e:
        print(f"Error opening Excel: {e}")
        return {}

    cutoff_time = pd.Timestamp.now() - pd.Timedelta(minutes=HISTORY_MINUTES)
    new_cache = {}

    for sheet in xls.sheet_names:
        try:
            df = pd.read_excel(xls, sheet_name=sheet, usecols=usecols)
        except Exception as e:
            print(f"Error reading sheet {sheet}: {e}")
            continue

        if df.empty or 'fetch_time' not in df.columns or 'opnInterest' not in df.columns:
            continue

        # Optimize data processing
        df['fetch_time'] = pd.to_datetime(df['fetch_time'], errors='coerce')
        df = df.dropna(subset=['fetch_time']).sort_values('fetch_time')
        
        # Remove duplicates and keep recent data
        df = df.drop_duplicates(subset=['fetch_time', 'opnInterest'], keep='last')
        df = df[df['fetch_time'] >= (df['fetch_time'].iloc[-1] - pd.Timedelta(minutes=HISTORY_MINUTES))]
        
        if not df.empty:
            new_cache[sheet] = df.reset_index(drop=True)

    _data_cache = new_cache
    _last_mtime = mtime
    _analysis_cache = {}  # Clear analysis cache when data changes
    # print(f"Loaded {len(new_cache)} sheets from {filename}")
    return _data_cache

# -------------------------
# OPTIMIZED ANALYTICS
# -------------------------
def compute_trend(df, minutes_back=3):
    """Compute trend against value minutes_back ago."""
    result = {
        'oi': np.nan, 'ltp': np.nan, 'oi_delta': None, 'ltp_delta': None,
        'oi_pct': None, 'ltp_pct': None, 'oi_arrow': '➖', 'ltp_arrow': '➖'
    }
    
    if df is None or df.empty:
        return result
        
    last_row = df.iloc[-1]
    result['oi'] = last_row.get('opnInterest', np.nan)
    result['ltp'] = last_row.get('ltp', np.nan)
    
    # Find past value
    past_time = last_row['fetch_time'] - pd.Timedelta(minutes=minutes_back)
    past_df = df[df['fetch_time'] <= past_time]
    
    if not past_df.empty:
        prev_row = past_df.iloc[-1]
        prev_oi = prev_row.get('opnInterest', np.nan)
        prev_ltp = prev_row.get('ltp', np.nan)
        
        # Calculate OI changes
        if pd.notna(result['oi']) and pd.notna(prev_oi) and prev_oi != 0:
            result['oi_delta'] = result['oi'] - prev_oi
            result['oi_pct'] = (result['oi_delta'] / prev_oi) * 100
            result['oi_arrow'] = '📈' if result['oi_delta'] > 0 else ('📉' if result['oi_delta'] < 0 else '➖')
        
        # Calculate LTP changes
        if pd.notna(result['ltp']) and pd.notna(prev_ltp) and prev_ltp != 0:
            result['ltp_delta'] = result['ltp'] - prev_ltp
            result['ltp_pct'] = (result['ltp_delta'] / prev_ltp) * 100
            result['ltp_arrow'] = '📈' if result['ltp_delta'] > 0 else ('📉' if result['ltp_delta'] < 0 else '➖')
    
    return result

def compute_volatility(df, window_minutes=VOL_WINDOW_MINUTES):
    """Compute volatility flags."""
    result = {'std_ltp': None, 'std_oi': None, 'vol_flag': False}
    
    if df is None or df.empty:
        return result
        
    # Get window data
    last_ts = df['fetch_time'].iloc[-1]
    window_data = df[df['fetch_time'] >= (last_ts - pd.Timedelta(minutes=window_minutes))]
    
    if window_data.empty:
        return result
    
    # Calculate volatility
    std_oi = window_data['opnInterest'].std()
    mean_oi = window_data['opnInterest'].mean()
    
    vol_flag = False
    if pd.notna(std_oi) and pd.notna(mean_oi) and mean_oi != 0:
        if std_oi > abs(mean_oi) * VOL_OI_THRESHOLD_PCT:
            vol_flag = True
    
    if 'ltp' in window_data.columns:
        std_ltp = window_data['ltp'].std()
        mean_ltp = window_data['ltp'].mean()
        if pd.notna(std_ltp) and pd.notna(mean_ltp) and mean_ltp != 0:
            if std_ltp > abs(mean_ltp) * VOL_LTP_THRESHOLD_PCT:
                vol_flag = True
        result['std_ltp'] = std_ltp
    
    result.update({'std_oi': std_oi, 'vol_flag': vol_flag})
    return result

def compute_market_analysis(data_dict):
    global _analysis_cache

    cache_key = tuple(sorted(data_dict.keys())) + tuple(df.shape[0] for df in data_dict.values())
    if cache_key in _analysis_cache:
        return _analysis_cache[cache_key]

    calls, puts = {}, {}
    ce_ltp_map, pe_ltp_map = {}, {}
    strikes_set = set()

    for sheet, df in data_dict.items():
        if df is None or df.empty:
            continue
        last = df.iloc[-1]
        ts = str(last.get('tradingSymbol', sheet)).upper()
        strike = extract_strike(ts)
        strikes_set.add(strike)

        oi = last.get('opnInterest', 0) or 0
        ltp = last.get('ltp', None)

        if is_call(ts) or sheet.upper().endswith('CE'):
            calls[strike] = calls.get(strike, 0) + oi
            if pd.notna(ltp):
                ce_ltp_map[strike] = float(ltp)
        elif is_put(ts) or sheet.upper().endswith('PE'):
            puts[strike] = puts.get(strike, 0) + oi
            if pd.notna(ltp):
                pe_ltp_map[strike] = float(ltp)

    total_ce = sum(calls.values())
    total_pe = sum(puts.values())
    pcr = (total_pe / total_ce) if total_ce != 0 else (float('inf') if total_pe > 0 else None)

    # Max Pain
    max_pain = None
    if strikes_set:
        pain_scores = {}
        for s in strikes_set:
            pain = sum(max(0, cs - s) * coi for cs, coi in calls.items())
            pain += sum(max(0, s - ps) * poi for ps, poi in puts.items())
            pain_scores[s] = pain
        max_pain = min(pain_scores, key=pain_scores.get)

    # ATM
    atm_strike, atm_ce_ltp, atm_pe_ltp = None, None, None
    common_strikes = sorted(set(ce_ltp_map.keys()) & set(pe_ltp_map.keys()))
    if common_strikes:
        atm_strike = min(common_strikes, key=lambda s: abs(ce_ltp_map[s] - pe_ltp_map[s]))
        atm_ce_ltp = ce_ltp_map.get(atm_strike)
        atm_pe_ltp = pe_ltp_map.get(atm_strike)

    # Enhanced totals (ATM + ITM only)
    enhanced_total_ce = enhanced_total_pe = 0
    ce_itm_strikes, pe_itm_strikes = [], []
    if atm_strike is not None:
        ce_itm_strikes = [s for s in calls.keys() if s <= atm_strike]
        pe_itm_strikes = [s for s in puts.keys() if s >= atm_strike]
        enhanced_total_ce = sum(calls[s] for s in ce_itm_strikes)
        enhanced_total_pe = sum(puts[s] for s in pe_itm_strikes)

    enhanced_pcr = (enhanced_total_pe / enhanced_total_ce) if enhanced_total_ce != 0 else (float('inf') if enhanced_total_pe > 0 else None)

    # Old bias (Market Summary)
    if pcr and pcr > 1:
        bias = "Sell CE"
    elif pcr and pcr < 1:
        bias = "Sell PE"
    else:
        bias = "Neutral"

    # New bias (Enhanced Market Summary)
    if enhanced_pcr is not None and atm_ce_ltp is not None and atm_pe_ltp is not None:
        if enhanced_pcr > 1 and atm_pe_ltp > atm_ce_ltp:
            enhanced_bias = "Sell CE"
        elif enhanced_pcr < 1 and atm_ce_ltp > atm_pe_ltp:
            enhanced_bias = "Sell PE"
        else:
            enhanced_bias = "Neutral"
    else:
        enhanced_bias = "Neutral"

    analysis = {
        'pcr': pcr,
        'total_ce': total_ce,
        'total_pe': total_pe,
        'max_pain': max_pain,
        'bias': bias,  # old bias
        'enhanced_bias': enhanced_bias,  # new bias
        'calls': calls,
        'puts': puts,
        'atm_strike': atm_strike,
        'atm_ce_ltp': atm_ce_ltp,
        'atm_pe_ltp': atm_pe_ltp,
        'enhanced_total_ce': enhanced_total_ce,
        'enhanced_total_pe': enhanced_total_pe,
        'enhanced_pcr': enhanced_pcr,
        'enhanced_totals': {'ce_itm_oi': enhanced_total_ce, 'pe_itm_oi': enhanced_total_pe},
        'ce_itm_strikes': ce_itm_strikes,
        'pe_itm_strikes': pe_itm_strikes
    }

    _analysis_cache[cache_key] = analysis
    return analysis

# def compute_market_analysis(data_dict):
#     """Unified function to compute PCR, max pain, and market summary."""
#     global _analysis_cache
    
#     # Use cache key based on data state
#     cache_key = tuple(sorted(data_dict.keys())) + tuple(df.shape[0] for df in data_dict.values())
#     if cache_key in _analysis_cache:
#         return _analysis_cache[cache_key]
    
#     calls, puts, strikes = {}, {}, set()
    
#     # Categorize contracts and sum OI by strike
#     for sheet, df in data_dict.items():
#         if df.empty:
#             continue
            
#         last_row = df.iloc[-1]
#         strike = extract_strike(sheet)
#         strikes.add(strike)
#         oi = last_row.get('opnInterest', 0) or 0
        
#         if is_call(sheet):
#             calls[strike] = calls.get(strike, 0) + oi
#         elif is_put(sheet):
#             puts[strike] = puts.get(strike, 0) + oi
    
#     # Calculate totals and PCR
#     total_ce = sum(calls.values())
#     total_pe = sum(puts.values())
#     pcr = (total_pe / total_ce) if total_ce != 0 else (float('inf') if total_pe > 0 else None)
    
#     # Calculate Max Pain
#     max_pain = None
#     if strikes:
#         pain_scores = {}
#         for strike in strikes:
#             pain = 0
#             for call_strike, call_oi in calls.items():
#                 pain += max(0, call_strike - strike) * call_oi
#             for put_strike, put_oi in puts.items():
#                 pain += max(0, strike - put_strike) * put_oi
#             pain_scores[strike] = pain
#         max_pain = min(pain_scores, key=pain_scores.get)
    
#     # Determine bias
#     bias = "Sell CE" if pcr and pcr > 1 else ("Sell PE" if pcr and pcr < 1 else "Neutral")
    
#     analysis = {
#         'pcr': pcr,
#         'total_ce': total_ce,
#         'total_pe': total_pe,
#         'max_pain': max_pain,
#         'bias': bias,
#         'calls': calls,
#         'puts': puts
#     }
    
#     _analysis_cache[cache_key] = analysis
#     return analysis

# -------------------------
# UI COMPONENTS
# -------------------------
def build_summary_card(analysis):
    """Build the unified market summary card."""
    pcr = analysis.get('pcr')
    total_ce = analysis.get('total_ce', 0)
    total_pe = analysis.get('total_pe', 0)
    max_pain = analysis.get('max_pain')
    bias = analysis.get('bias', 'Neutral')
    
    pcr_str = f"{pcr:.2f}" if (pcr is not None and pcr != float('inf')) else ("∞" if pcr == float('inf') else "N/A")
    max_pain_str = str(max_pain) if max_pain is not None else "N/A"

    return html.Div([
        html.H4("Market Summary", style={'margin': '0 0 10px 0', 'color': '#2c3e50'}),
        html.Div([
            html.Div(f"Total CE OI: {fmt_lacs(total_ce)}", style={'marginBottom': '4px'}),
            html.Div(f"Total PE OI: {fmt_lacs(total_pe)}", style={'marginBottom': '4px'}),
            html.Div(f"PCR (PE/CE): {pcr_str}", style={'marginBottom': '4px'}),
            html.Div(f"Max Pain Strike: {max_pain_str}", style={'fontWeight': 'bold', 'marginBottom': '4px'}),
            html.Div(f"Market Bias: {bias}", style={'fontWeight': 'bold', 'color': '#e74c3c' if bias != 'Neutral' else '#27ae60'})
        ])
    ], style={
        'border': '1px solid #bdc3c7',
        'borderRadius': '8px',
        'backgroundColor': '#ecf0f1',
        'padding': '12px',
        'marginBottom': '10px',
        'minWidth': '250px'
    })

# def build_enhanced_summary_card(analysis):
#     """
#     Build the Enhanced Market Summary block from computed analysis.
#     """
#     # Totals (normal)
#     total_ce = analysis.get('total_ce', 0)
#     total_pe = analysis.get('total_pe', 0)
#     pcr = analysis.get('pcr')

#     # Max Pain & Bias
#     max_pain = analysis.get('max_pain')
#     bias = analysis.get('bias', 'Neutral')

#     # ATM details
#     atm = analysis.get('atm_strike')
#     atm_ce = analysis.get('atm_ce_ltp')
#     atm_pe = analysis.get('atm_pe_ltp')

#     # Enhanced PCR
#     enhanced_pcr = analysis.get('enhanced_pcr')
#     ce_itm_oi = analysis.get('enhanced_totals', {}).get('ce_itm_oi', 0)
#     pe_itm_oi = analysis.get('enhanced_totals', {}).get('pe_itm_oi', 0)

#     # Formatting
#     pcr_str = f"{pcr:.2f}" if (pcr is not None and pcr != float('inf')) else ("∞" if pcr == float('inf') else "N/A")
#     enhanced_pcr_str = f"{enhanced_pcr:.2f}" if (enhanced_pcr is not None and enhanced_pcr != float('inf')) else ("∞" if enhanced_pcr == float('inf') else "N/A")
#     max_pain_str = str(max_pain) if max_pain is not None else "N/A"
#     atm_str = str(atm) if atm is not None else "N/A"
#     atm_ce_str = f"{atm_ce:.2f}" if atm_ce is not None else "N/A"
#     atm_pe_str = f"{atm_pe:.2f}" if atm_pe is not None else "N/A"

#     return html.Div([
#         html.H4("Enhanced Market Summary", style={'margin': '0 0 10px 0', 'color': '#8e44ad'}),
#         html.Div(f"Enhanced Total CE OI: {fmt_lacs(total_ce)}", style={'marginBottom': '4px'}),
#         html.Div(f"Enhanced Total PE OI: {fmt_lacs(total_pe)}", style={'marginBottom': '4px'}),
#         html.Div(f"Enhanced PCR (PE/CE): {pcr_str}", style={'marginBottom': '4px'}),
#         html.Div(f"Max Pain Strike: {max_pain_str}", style={'marginBottom': '4px'}),
#         html.Div(f"ATM Option : {atm_str} (CE LTP: {atm_ce_str}, PE LTP: {atm_pe_str})", style={'marginBottom': '4px'}),
#         html.Div(f"Enhanced PCR (ITM PE / ITM CE): {enhanced_pcr_str}", style={'marginBottom': '4px'}),
#         html.Div(f"Enhanced Totals — CE_ITM: {fmt_lacs(ce_itm_oi)}, PE_ITM: {fmt_lacs(pe_itm_oi)}", style={'marginBottom': '4px'}),
#         html.Div(f"Market Bias: {bias}", style={'fontWeight': 'bold', 'color': '#e74c3c' if bias != 'Neutral' else '#27ae60'}),
#     ], style={
#         'border': '1px solid #8e44ad',
#         'borderRadius': '8px',
#         'backgroundColor': '#f5eaf7',
#         'padding': '12px',
#         'marginBottom': '10px',
#         'minWidth': '300px'
#     })

def build_enhanced_summary_card(analysis):
    """
    Display the Enhanced Market Summary block (ATM + ITM based metrics).
    """
    enhanced_total_ce = analysis.get('enhanced_total_ce', 0)
    enhanced_total_pe = analysis.get('enhanced_total_pe', 0)
    enhanced_pcr = analysis.get('enhanced_pcr')
    atm = analysis.get('atm_strike')
    atm_ce = analysis.get('atm_ce_ltp')
    atm_pe = analysis.get('atm_pe_ltp')
    max_pain = analysis.get('max_pain')
    bias = analysis.get('enhanced_bias', 'Neutral')

    ce_itm_oi = analysis.get('enhanced_totals', {}).get('ce_itm_oi', 0)
    pe_itm_oi = analysis.get('enhanced_totals', {}).get('pe_itm_oi', 0)

    # Pre-format values
    pcr_val = analysis.get('pcr')
    if pcr_val is None:
        pcr_str = "N/A"
    elif pcr_val == float('inf'):
        pcr_str = "∞"
    else:
        pcr_str = f"{pcr_val:.2f}"

    if enhanced_pcr is None:
        enhanced_pcr_str = "N/A"
    elif enhanced_pcr == float('inf'):
        enhanced_pcr_str = "∞"
    else:
        enhanced_pcr_str = f"{enhanced_pcr:.2f}"

    atm_str = str(atm) if atm is not None else "N/A"
    atm_ce_str = f"{atm_ce:.2f}" if atm_ce is not None else "N/A"
    atm_pe_str = f"{atm_pe:.2f}" if atm_pe is not None else "N/A"
    max_pain_str = str(max_pain) if max_pain is not None else "N/A"

    return html.Div([
        html.H4("Enhanced Market Summary", style={'margin': '0 0 10px 0', 'color': '#8e44ad'}),
        html.Div(f"Enhanced Total CE OI: {fmt_lacs(enhanced_total_ce)}", style={'marginBottom': '4px'}),
        html.Div(f"Enhanced Total PE OI: {fmt_lacs(enhanced_total_pe)}", style={'marginBottom': '4px'}),
        html.Div(f"Enhanced PCR (PE/CE): {pcr_str}", style={'marginBottom': '4px'}),
        html.Div(f"Max Pain Strike: {max_pain_str}", style={'marginBottom': '4px'}),
        html.Div(f"ATM Option : {atm_str} (CE LTP: {atm_ce_str}, PE LTP: {atm_pe_str})", style={'marginBottom': '4px'}),
        html.Div(f"Enhanced PCR (ITM PE / ITM CE): {enhanced_pcr_str}", style={'marginBottom': '4px'}),
        html.Div(f"Enhanced Totals — CE_ITM: {fmt_lacs(ce_itm_oi)}, PE_ITM: {fmt_lacs(pe_itm_oi)}", style={'marginBottom': '4px'}),
        html.Div(f"Enhanced Market Bias: {bias}", style={
            'fontWeight': 'bold',
            'color': '#e74c3c' if bias != 'Neutral' else '#27ae60'
        }),
    ], style={
        'border': '1px solid #8e44ad',
        'borderRadius': '8px',
        'backgroundColor': '#f5eaf7',
        'padding': '12px',
        'marginBottom': '10px',
        'minWidth': '300px'
    })


def build_volatility_card(data_dict):
    """Build top volatility card."""
    vol_data = []
    for sheet, df in data_dict.items():
        vol = compute_volatility(df)
        if vol['std_oi'] is not None:
            vol_data.append((sheet, vol['std_oi'], vol['vol_flag']))
    
    # Sort by volatility and take top 5
    vol_data.sort(key=lambda x: x[1], reverse=True)
    top_vol = vol_data[:5]
    
    return html.Div([
        html.H4("Top Volatile (OI)", style={'margin': '0 0 10px 0', 'color': '#2c3e50'}),
        html.Div([
            html.Div(f"{sheet}: {std:.0f} {'⚡' if flag else ''}", 
                     style={'marginBottom': '2px', 'fontSize': '12px'})
            for sheet, std, flag in top_vol
        ])
    ], style={
        'border': '1px solid #bdc3c7',
        'borderRadius': '8px',
        'backgroundColor': '#ecf0f1',
        'padding': '12px',
        'marginBottom': '10px',
        'minWidth': '200px'
    })

def build_aggregate_graph(label, filtered_dfs, show_trend=True):
    """Build aggregate CE or PE graph."""
    if not filtered_dfs:
        return None
        
    # Combine all dataframes
    combined = pd.concat(filtered_dfs, ignore_index=True)
    combined['fetch_time'] = pd.to_datetime(combined['fetch_time'])
    
    # Group by time and sum OI
    grouped = combined.groupby('fetch_time').agg({
        'opnInterest': 'sum',
        'ltp': 'mean'
    }).reset_index().sort_values('fetch_time')
    
    # Calculate trend for title
    current_oi = grouped['opnInterest'].iloc[-1]
    current_ltp = grouped['ltp'].iloc[-1] if 'ltp' in grouped.columns else None
    
    title_parts = [f"ALL {label.upper()} | OI: {fmt_lacs(current_oi)}"]
    
    if show_trend and len(grouped) > 1:
        # Calculate 3-minute trend
        past_time = grouped['fetch_time'].iloc[-1] - pd.Timedelta(minutes=3)
        past_data = grouped[grouped['fetch_time'] <= past_time]
        
        if not past_data.empty:
            past_oi = past_data['opnInterest'].iloc[-1]
            delta_oi = current_oi - past_oi
            pct_change = (delta_oi / past_oi * 100) if past_oi != 0 else 0
            arrow = "📈" if delta_oi > 0 else ("📉" if delta_oi < 0 else "➖")
            title_parts.append(f" {arrow} ({pct_change:.1f}%)")
    
    # Create plot
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=grouped['fetch_time'],
        y=grouped['opnInterest'],
        mode='lines+markers',
        name=f'{label} OI',
        customdata=grouped['ltp'].round(2) if 'ltp' in grouped.columns else None,
        hovertemplate=f'Time: %{{x|%H:%M}}<br>Total {label} OI: %{{y}}<br>' + 
                     ('Avg LTP: %{customdata:.2f}<br>' if 'ltp' in grouped.columns else '') +
                     '<extra></extra>'
    ))
    
    fig.update_layout(
        title=dict(text=''.join(title_parts), font=dict(size=12)),
        xaxis=dict(title='Time', tickformat='%H:%M', tickfont=dict(size=9)),
        yaxis=dict(title='Open Interest', tickfont=dict(size=9)),
        height=300,
        margin=dict(l=40, r=40, t=60, b=40)
    )
    
    return html.Div(
        dcc.Graph(figure=fig),
        style={'flex': '1', 'padding': '8px', 'minWidth': '48%'}
    )

def build_individual_graph(symbol, df, show_trend=True):
    """Build individual contract graph."""
    if df is None or df.empty:
        return html.Div(f"No data for {symbol}", style={'padding': '10px'})

    # Get plot data (last PLOT_MINUTES)
    latest = df['fetch_time'].iloc[-1]
    plot_data = df[df['fetch_time'] >= (latest - pd.Timedelta(minutes=PLOT_MINUTES))]
    
    if plot_data.empty:
        plot_data = df

    # Compute metrics
    trend = compute_trend(df, minutes_back=3)
    vol = compute_volatility(df)

    # Build title
    ltp = trend['ltp']
    oi = trend['oi']
    oi_fmt = fmt_lacs(oi) if pd.notna(oi) else "N/A"
    
    title_parts = [symbol]
    
    if show_trend:
        ltp_str = f"{ltp:.2f}" if pd.notna(ltp) else "N/A"
        ltp_pct = f"({trend['ltp_pct']:.2f}%)" if trend['ltp_pct'] is not None else "(N/A)"
        oi_pct = f"({trend['oi_pct']:.2f}%)" if trend['oi_pct'] is not None else "(N/A)"
        
        title_parts.extend([
            f" | LTP: {ltp_str} {trend['ltp_arrow']} {ltp_pct}",
            f" | OI: {oi_fmt} {trend['oi_arrow']} {oi_pct}",
            " ⚡" if vol['vol_flag'] else ""
        ])

    # Create figure
    fig = go.Figure()
    
    customdata = plot_data['ltp'].round(2) if 'ltp' in plot_data.columns else None
    
    fig.add_trace(go.Scatter(
        x=plot_data['fetch_time'],
        y=plot_data['opnInterest'],
        mode='lines+markers',
        name='OI',
        customdata=customdata,
        hovertemplate=f'Contract: {symbol}<br>' +
                     'Time: %{x|%H:%M:%S}<br>' +
                     'OI: %{y}<br>' +
                     ('LTP: %{customdata:.2f}<br>' if customdata is not None else '') +
                     '<extra></extra>'
    ))

    fig.update_layout(
        title=dict(text=''.join(title_parts), font=dict(size=11)),
        xaxis=dict(title='Time', tickformat='%H:%M', tickfont=dict(size=9)),
        yaxis=dict(title='Open Interest', tickfont=dict(size=9)),
        font=dict(size=9),
        height=330,
        margin=dict(l=30, r=30, t=70, b=30)
    )

    return html.Div(
        dcc.Graph(figure=fig, id=f'graph-{symbol}'),
        style={'flex': '1', 'padding': '8px', 'minWidth': '48%'}
    )

# -------------------------
# DASH APP
# -------------------------
app = dash.Dash(__name__)
app.title = "OI Realtime Dashboard (Optimized)"

app.layout = html.Div([
    html.H1("Options Open Interest Dashboard V1.4", 
            style={"textAlign": "center", "marginBottom": "20px", "color": "#2c3e50"}),

    # Controls
    html.Div([
        html.Div([
            dcc.Checklist(
                id='show-trend-toggle',
                options=[{'label': ' Show Trend Indicators', 'value': 'show'}],
                value=['show'],
                style={'display': 'inline-block'}
            )
        ], style={'marginRight': '20px'}),

        html.Div([
            html.Label("Filter Symbols:", style={'marginRight': '8px', 'fontWeight': 'bold'}),
            dcc.Dropdown(
                id='symbol-filter',
                options=[],
                multi=True,
                placeholder="Select contracts to display...",
                style={'minWidth': '300px'}
            )
        ], style={'display': 'inline-block', 'marginRight': '20px'}),

        html.Div([
            dcc.RadioItems(
                id='type-filter',
                options=[
                    {'label': 'All', 'value': 'all'},
                    {'label': 'CE only', 'value': 'ce'},
                    {'label': 'PE only', 'value': 'pe'}
                ],
                value='all',
                labelStyle={'display': 'inline-block', 'marginRight': '10px'}
            )
        ], style={'display': 'inline-block'})
    ], style={
        'padding': '10px',
        'display': 'flex',
        'alignItems': 'center',
        'flexWrap': 'wrap',
        'backgroundColor': '#f8f9fa',
        'borderRadius': '8px',
        'marginBottom': '15px'
    }),

    html.Div(id='status-indicator', style={
        'textAlign': 'center',
        'marginBottom': '10px',
        'padding': '5px',
        'backgroundColor': '#d4edda',
        'border': '1px solid #c3e6cb',
        'borderRadius': '4px',
        'color': '#155724'
    }),

    dcc.Interval(id='interval', interval=INTERVAL_MS, n_intervals=0),

    # Main content
    html.Div(id='main-content')
], style={'padding': '15px', 'fontFamily': 'Arial, sans-serif'})


# -------------------------
# CALLBACKS
# -------------------------
@app.callback(
    Output('symbol-filter', 'options'),
    [Input('interval', 'n_intervals')]
)
def update_symbol_options(n):
    """Update dropdown options."""
    data = load_data_if_changed()
    return [{'label': k, 'value': k} for k in sorted(data.keys(), key=extract_strike)]

# @app.callback(
#     [Output('main-content', 'children'),
#      Output('status-indicator', 'children')],
#     [Input('interval', 'n_intervals'),
#      Input('show-trend-toggle', 'value'),
#      Input('symbol-filter', 'value'),
#      Input('type-filter', 'value')]
# )
# def update_dashboard(n, show_trend_toggle, symbol_filter, type_filter):
#     """Main dashboard update callback."""
#     start_time = datetime.datetime.now()
#     show_trend = 'show' in (show_trend_toggle or [])
    
#     # Load data
#     data = load_data_if_changed()
#     if not data:
#         no_data_msg = f"No data found for '{EXCEL_FILENAME}'. Waiting for data..."
#         return [html.Div(no_data_msg, style={'textAlign': 'center', 'color': 'orange', 'padding': '40px'})], no_data_msg

#     # Filter data based on user selections
#     filtered_keys = sorted(data.keys(), key=extract_strike)
    
#     if symbol_filter:
#         filtered_keys = [k for k in filtered_keys if k in symbol_filter]
    
#     if type_filter == 'ce':
#         filtered_keys = [k for k in filtered_keys if is_call(k)]
#     elif type_filter == 'pe':
#         filtered_keys = [k for k in filtered_keys if is_put(k)]
#     filtered_keys = sorted(filtered_keys)
#     # Compute market analysis
#     analysis = compute_market_analysis(data)
#     # print("analysis :",analysis)
#     # Build components
#     summary_card = build_summary_card(analysis)
#     enhanced_card = build_enhanced_summary_card(analysis)
#     volatility_card = build_volatility_card(data)
    
#     # Build aggregate graphs
#     ce_dfs = [data[k] for k in data.keys() if is_call(k)]
#     pe_dfs = [data[k] for k in data.keys() if is_put(k)]
#     # print("\n\n\nce_dfs :",ce_dfs,"\n\n\n")
#     agg_ce = build_aggregate_graph('CE', ce_dfs, show_trend)
#     agg_pe = build_aggregate_graph('PE', pe_dfs, show_trend)
#     # print(ce_dfs)
    
#     ce_itm_dfs = [
#         df[df['tradingSymbol'].isin(analysis['ce_itm_strikes'])]
#         for df in ce_dfs
#         if df is not None and not df.empty and df['tradingSymbol'].isin(analysis['ce_itm_strikes']).any()
#     ]

#     pe_itm_dfs = [
#         df[df['tradingSymbol'].isin(analysis['pe_itm_strikes'])]
#         for df in pe_dfs
#         if df is not None and not df.empty and df['tradingSymbol'].isin(analysis['pe_itm_strikes']).any()
#     ]

#     enhanced_agg_ce = build_aggregate_graph('ITM CE', ce_itm_dfs, show_trend)
#     enhanced_agg_pe = build_aggregate_graph('ITM PE', pe_itm_dfs, show_trend)

#     # Build individual graphs (2 per row)
#     individual_graphs = []
#     for i in range(0, len(filtered_keys), 2):
#         # print(filtered_keys)
#         row_graphs = []
#         for j in range(2):
#             if i + j < len(filtered_keys):
#                 key = filtered_keys[i + j]
#                 graph = build_individual_graph(key, data[key], show_trend)
#                 row_graphs.append(graph)
        
#         if row_graphs:
#             individual_graphs.append(
#                 html.Div(row_graphs, style={
#                     'display': 'flex',
#                     'justifyContent': 'space-between',
#                     'marginBottom': '10px'
#                 })
#             )

#     # Assembly layout
#     content = []
    
#     # Top row: summary cards
#     top_cards = [summary_card, volatility_card,enhanced_card]
#     content.append(html.Div(top_cards, style={
#         'display': 'flex',
#         'gap': '15px',
#         'marginBottom': '15px',
#         'flexWrap': 'wrap'
#     }))
    
#     # Aggregate graphs row    
#     if agg_ce or agg_pe:
#         agg_graphs = [g for g in [agg_ce, agg_pe] if g is not None]
#         content.append(html.Div(agg_graphs, style={
#             'display': 'flex',
#             'justifyContent': 'space-between',
#             'marginBottom': '15px'
#         }))
    
#     if enhanced_agg_ce or enhanced_agg_pe:
#         enhanced_agg_graphs = [g for g in [enhanced_agg_ce, enhanced_agg_pe] if g is not None]
#         content.append(html.Div(enhanced_agg_graphs, style={
#             'display': 'flex',
#             'justifyContent': 'space-between',
#             'marginBottom': '15px'
#         }))

#     # print("\n\n\n\n\n **************** content : ", content, "\n\n\n\n\n")
#     # Individual graphs
#     content.extend(individual_graphs)
    
#     # Status message
#     elapsed = (datetime.datetime.now() - start_time).total_seconds()
#     status = f"📊 Updated: {datetime.datetime.now().strftime('%H:%M:%S')} | Contracts: {len(data)} | Displayed: {len(filtered_keys)} | Render: {elapsed:.2f}s"
    
#     return content, status
# Replace the problematic section in your update_dashboard callback with this fixed version:

@app.callback(
    [Output('main-content', 'children'),
     Output('status-indicator', 'children')],
    [Input('interval', 'n_intervals'),
     Input('show-trend-toggle', 'value'),
     Input('symbol-filter', 'value'),
     Input('type-filter', 'value')]
)
def update_dashboard(n, show_trend_toggle, symbol_filter, type_filter):
    """Main dashboard update callback."""
    start_time = datetime.datetime.now()
    show_trend = 'show' in (show_trend_toggle or [])
    
    # Load data
    data = load_data_if_changed()
    if not data:
        no_data_msg = f"No data found for '{EXCEL_FILENAME}'. Waiting for data..."
        return [html.Div(no_data_msg, style={'textAlign': 'center', 'color': 'orange', 'padding': '40px'})], no_data_msg

    # Filter data based on user selections
    filtered_keys = sorted(data.keys(), key=extract_strike)
    
    if symbol_filter:
        filtered_keys = [k for k in filtered_keys if k in symbol_filter]
    
    if type_filter == 'ce':
        filtered_keys = [k for k in filtered_keys if is_call(k)]
    elif type_filter == 'pe':
        filtered_keys = [k for k in filtered_keys if is_put(k)]
    
    # Compute market analysis
    analysis = compute_market_analysis(data)
    
    # Build components
    summary_card = build_summary_card(analysis)
    enhanced_card = build_enhanced_summary_card(analysis)
    volatility_card = build_volatility_card(data)
    
    # Build aggregate graphs for ALL contracts
    ce_dfs = [data[k] for k in data.keys() if is_call(k)]
    pe_dfs = [data[k] for k in data.keys() if is_put(k)]
    
    agg_ce = build_aggregate_graph('CE', ce_dfs, show_trend)
    agg_pe = build_aggregate_graph('PE', pe_dfs, show_trend)
    
    # Build enhanced aggregate graphs for ITM contracts only
    # Get ITM strikes from analysis
    ce_itm_strikes = set(analysis.get('ce_itm_strikes', []))
    pe_itm_strikes = set(analysis.get('pe_itm_strikes', []))
    
    print(f"CE ITM Strikes: {ce_itm_strikes}")
    print(f"PE ITM Strikes: {pe_itm_strikes}")
    
    # Filter dataframes for ITM contracts
    ce_itm_dfs = []
    pe_itm_dfs = []
    
    for sheet_name, df in data.items():
        if df is None or df.empty:
            continue
            
        sheet_strike = extract_strike(sheet_name)
        
        # Check if this contract is ITM CE
        if is_call(sheet_name) and sheet_strike in ce_itm_strikes:
            ce_itm_dfs.append(df)
            # print(f"Added CE ITM: {sheet_name} (Strike: {sheet_strike})")
        
        # Check if this contract is ITM PE  
        elif is_put(sheet_name) and sheet_strike in pe_itm_strikes:
            pe_itm_dfs.append(df)
            # print(f"Added PE ITM: {sheet_name} (Strike: {sheet_strike})")
    
    # print(f"CE ITM DataFrames: {len(ce_itm_dfs)}")
    # print(f"PE ITM DataFrames: {len(pe_itm_dfs)}")
    
    # Build enhanced aggregate graphs
    enhanced_agg_ce = build_aggregate_graph('ITM CE', ce_itm_dfs, show_trend) if ce_itm_dfs else None
    enhanced_agg_pe = build_aggregate_graph('ITM PE', pe_itm_dfs, show_trend) if pe_itm_dfs else None
    
    # print(f"Enhanced CE graph created: {enhanced_agg_ce is not None}")
    # print(f"Enhanced PE graph created: {enhanced_agg_pe is not None}")

    # Build individual graphs (2 per row) - only for filtered contracts
    individual_graphs = []
    for i in range(0, len(filtered_keys), 2):
        row_graphs = []
        for j in range(2):
            if i + j < len(filtered_keys):
                key = filtered_keys[i + j]
                graph = build_individual_graph(key, data[key], show_trend)
                row_graphs.append(graph)
        
        if row_graphs:
            individual_graphs.append(
                html.Div(row_graphs, style={
                    'display': 'flex',
                    'justifyContent': 'space-between',
                    'marginBottom': '10px'
                })
            )

    # Assembly layout
    content = []
    
    # Top row: summary cards
    top_cards = [summary_card, volatility_card, enhanced_card]
    content.append(html.Div(top_cards, style={
        'display': 'flex',
        'gap': '15px',
        'marginBottom': '15px',
        'flexWrap': 'wrap'
    }))
    
    # All contracts aggregate graphs row    
    if agg_ce or agg_pe:
        agg_graphs = [g for g in [agg_ce, agg_pe] if g is not None]
        content.append(html.Div([
            html.H3("All Contracts Summary", style={'textAlign': 'center', 'color': '#2c3e50', 'margin': '10px 0'}),
            html.Div(agg_graphs, style={
                'display': 'flex',
                'justifyContent': 'space-between',
                'marginBottom': '15px'
            })
        ]))
    
    # Enhanced (ITM only) aggregate graphs row
    if enhanced_agg_ce or enhanced_agg_pe:
        enhanced_agg_graphs = [g for g in [enhanced_agg_ce, enhanced_agg_pe] if g is not None]
        content.append(html.Div([
            html.H3("ITM Contracts Summary", style={'textAlign': 'center', 'color': '#8e44ad', 'margin': '10px 0'}),
            html.Div(enhanced_agg_graphs, style={
                'display': 'flex',
                'justifyContent': 'space-between',
                'marginBottom': '15px'
            })
        ]))
    
    # Individual graphs
    if individual_graphs:
        content.append(html.H3("Individual Contract Details", style={'textAlign': 'center', 'color': '#2c3e50', 'margin': '20px 0 10px 0'}))
        content.extend(individual_graphs)
    
    # Status message
    elapsed = (datetime.datetime.now() - start_time).total_seconds()
    status = f"📊 Updated: {datetime.datetime.now().strftime('%H:%M:%S')} | Contracts: {len(data)} | Displayed: {len(filtered_keys)} | ITM CE: {len(ce_itm_dfs)} | ITM PE: {len(pe_itm_dfs)} | Render: {elapsed:.2f}s"
    
    return content, status


# -------------------------
# RUN APP
# -------------------------
if __name__ == "__main__":
    print("Starting Optimized OI Dashboard")
    print(f"Excel filename: {EXCEL_FILENAME}")
    print(f"Refresh interval: {INTERVAL_MS/1000}s")
    app.run(debug=True, port=8050)