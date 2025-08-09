import pandas as pd
import plotly.graph_objs as go
from plotly.subplots import make_subplots
import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import os
import datetime

def fmt_lacs(val):
    try:
        return f"{val/1e5:.2f}L"
    except Exception:
        return val

def get_oi_data(excel_filename):
    if not os.path.exists(excel_filename):
        return {}
    xls = pd.ExcelFile(excel_filename)
    sheet_names = xls.sheet_names
    data = {}
    for sheet in sorted(sheet_names):
        df = pd.read_excel(xls, sheet_name=sheet)
        if 'opnInterest' not in df.columns or 'fetch_time' not in df.columns or df.empty:
            continue
        df = df.sort_values('fetch_time')
        df['fetch_time'] = pd.to_datetime(df['fetch_time'])
        # Limit to last 90 minutes
        latest_time = df['fetch_time'].iloc[-1]
        min_time = latest_time - pd.Timedelta(minutes=90)
        df = df[df['fetch_time'] >= min_time]
        data[sheet] = df
    return data

# Dash app
app = dash.Dash(__name__)
app.title = "OI Realtime Dashboard"

excel_filename = f"oi_data_{datetime.datetime.now().strftime('%Y%m%d')}.xlsx"

app.layout = html.Div([
    html.H1("Change in Open Interest for All Strikes", style={"textAlign": "center"}),

    html.Div([
        dcc.Checklist(
            id='show-trend-toggle',
            options=[{'label': ' Show Trend Indicators (arrows & %)', 'value': 'show'}],
            value=['show'],
            inputStyle={'margin-right': '5px'}
        )
    ], style={'textAlign': 'center', 'marginBottom': '10px'}),

    dcc.Interval(id='interval', interval=30 * 1000, n_intervals=0),
    html.Div(id='graphs-container')
])

import re

def extract_strike(name):
    # Extract numeric part from string, e.g., "nifty24500ce" → 24500
    match = re.search(r'(\d+)', name)
    return int(match.group(1)) if match else 0

@app.callback(
    Output('graphs-container', 'children'),
    [Input('interval', 'n_intervals'),
     Input('show-trend-toggle', 'value')]
)
def update_graphs(n, show_trend_toggle):

    print("Callback triggered. show_trend_toggle:", show_trend_toggle)
    show_trend = 'show' in show_trend_toggle if show_trend_toggle else False

    data = get_oi_data(excel_filename)
    print("Loaded sheets:", list(data.keys()))

    if not data:
        return [html.Div("No data available.", style={"textAlign": "center", "color": "red"})]

    for symbol, df in data.items():
        print(f"Symbol: {symbol} | Shape: {df.shape} | Columns: {df.columns.tolist()}")
        # Let's also check if essential columns exist:
        for col in ['fetch_time', 'opnInterest', 'ltp']:
            if col not in df.columns:
                print(f"Warning: '{col}' column missing in {symbol}")

    ce_data = []
    pe_data = []

    for symbol, df in data.items():
        if symbol.lower().endswith("ce"):
            ce_data.append(df)
        elif symbol.lower().endswith("pe"):
            pe_data.append(df)

    graph_divs = []
    def build_aggregate_graph(label, dfs):
        if not dfs:
            return None
        df_all = pd.concat(dfs)
        df_all['fetch_time'] = pd.to_datetime(df_all['fetch_time'])
        df_all = df_all.sort_values("fetch_time")

        grouped = df_all.groupby("fetch_time").agg({
            "opnInterest": "sum",
            "ltp": "mean"
        }).reset_index()

        x = grouped["fetch_time"]
        y = grouped["opnInterest"]
        ltp_series = grouped["ltp"]
        now = x.iloc[-1]

        # Current values
        curr_oi = y.iloc[-1]
        curr_ltp = ltp_series.iloc[-1]
        oi_fmt = fmt_lacs(curr_oi)

        oi_changes = []
        ltp_changes = []

        arrow = ""
        percent = ""

        for mins in [3, 5, 10, 15]:
            past_time = now - pd.Timedelta(minutes=mins)
            past_df = grouped[grouped["fetch_time"] <= past_time]

            if not past_df.empty:
                past_oi = past_df["opnInterest"].iloc[-1]
                past_ltp = past_df["ltp"].iloc[-1]

                delta_oi = curr_oi - past_oi
                delta_ltp = curr_ltp - past_ltp

                oi_changes.append(fmt_lacs(delta_oi))
                ltp_changes.append(f"{delta_ltp:.2f}")

                if mins == 3:
                    if show_trend:
                        arrow = "▲" if delta_oi > 0 else "▼" if delta_oi < 0 else "➖"
                        percent = f"({(delta_oi / past_oi * 100):.1f}%)" if past_oi != 0 else "(∞%)"
            else:
                oi_changes.append("N/A")
                ltp_changes.append("N/A")

        title = (
            f"ALL {label.upper()} | OI: {oi_fmt} {arrow} {percent}<br>"
            f"ΔOI  3m: {oi_changes[0]}, 5m: {oi_changes[1]}, 10m: {oi_changes[2]}, 15m: {oi_changes[3]} | <br>"
            f"ΔLTP 3m: {ltp_changes[0]}, 5m: {ltp_changes[1]}, 10m: {ltp_changes[2]}, 15m: {ltp_changes[3]}"
        )

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=x,
            y=y,
            mode="lines+markers",
            name="OI",
            customdata=ltp_series.round(2),
            hovertemplate="Time: %{x|%H:%M}<br>OI: %{y}<br>LTP: %{customdata:.2f}<extra></extra>"
        ))

        fig.update_layout(
            title=dict(text=title, font=dict(size=12)),
            xaxis=dict(title="Time", tickformat="%H:%M", tickfont=dict(size=9)),
            yaxis=dict(title="Open Interest", tickfont=dict(size=9)),
            font=dict(size=9),
            height=300,
            margin=dict(l=30, r=30, t=60, b=30),
        )

        return html.Div(dcc.Graph(figure=fig), style={'padding': '10px', 'minWidth': '48%'})


    # Add aggregate graphs for CE and PE on top
    agg_ce = build_aggregate_graph('CE', ce_data)
    agg_pe = build_aggregate_graph('PE', pe_data)
    if agg_ce and agg_pe:
        graph_divs.append(html.Div([agg_ce, agg_pe],
                                   style={'display': 'flex', 'flexWrap': 'wrap', 'justifyContent': 'space-between'}))

    # Sort CE ascending, PE descending keys
    ce_keys = sorted([k for k in data if k.lower().endswith('ce')])
    pe_keys = sorted([k for k in data if k.lower().endswith('pe')], reverse=True)

    # Zip to pair CE and PE for side-by-side display
    combined_keys = list(zip(ce_keys, pe_keys))

    for ce_key, pe_key in combined_keys:
        row = []
        for symbol in [ce_key, pe_key]:
            if symbol not in data:
                continue
            df = data[symbol]
            x = df['fetch_time']
            y = df['opnInterest']
            ltp = df['ltp'].iloc[-1] if 'ltp' in df.columns else None
            oi = y.iloc[-1]
            oi_fmt = fmt_lacs(oi)

            # Calculate trend indicators
            oi_arrow = ltp_arrow = ''
            oi_percent = ltp_percent = ''
            delta_oi = delta_ltp = None
            if show_trend and len(df) > 1:
                now = x.iloc[-1]
                past_time = now - pd.Timedelta(minutes=3)
                past_df = df[df['fetch_time'] <= past_time]
                if not past_df.empty:
                    past_oi = past_df['opnInterest'].iloc[-1]
                    past_ltp = past_df['ltp'].iloc[-1] if 'ltp' in df.columns else None
                    delta_oi = oi - past_oi
                    delta_ltp = (ltp - past_ltp) if ltp is not None and past_ltp is not None else None

                    oi_arrow = "▲" if delta_oi > 0 else "▼" if delta_oi < 0 else "➖"
                    oi_percent = f"{(delta_oi / past_oi * 100):.1f}%" if past_oi != 0 else "∞%"

                    if delta_ltp is not None:
                        ltp_arrow = "▲" if delta_ltp > 0 else "▼" if delta_ltp < 0 else "➖"
                        ltp_percent = f"{(delta_ltp / past_ltp * 100):.1f}%" if past_ltp != 0 else "∞%"

            title = f"{symbol} | LTP: {ltp:.2f} {ltp_arrow} ({ltp_percent}) | OI: {oi_fmt} {oi_arrow} ({oi_percent})"

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=x, y=y, mode='lines+markers', name='OI',
                customdata=df['ltp'].round(2),
                hovertemplate='Time: %{x|%H:%M}<br>OI: %{y}<br>LTP: %{customdata:.2f}<extra></extra>'
            ))
            fig.update_layout(
                title=dict(text=title, font=dict(size=12)),
                xaxis=dict(title='Time', tickformat='%H:%M', tickfont=dict(size=9)),
                yaxis=dict(title='Open Interest', tickfont=dict(size=9)),
                font=dict(size=9),
                height=330,
                margin=dict(l=30, r=30, t=60, b=30),
            )
            graph_component = html.Div(
                dcc.Graph(figure=fig, id=f'graph-{symbol}'),
                style={'flex': '1', 'padding': '10px', 'minWidth': '48%'}
            )
            row.append(graph_component)

        graph_divs.append(html.Div(row, style={'display': 'flex', 'flexWrap': 'wrap', 'justifyContent': 'space-between'}))

    return graph_divs
# Run the app
if __name__ == "__main__":
    app.run(debug=True, port=8050)
