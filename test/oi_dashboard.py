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
    dcc.Interval(id='interval', interval=30*1000, n_intervals=0),
    html.Div(id='graphs-container')
])

@app.callback(
    Output('graphs-container', 'children'),
    [Input('interval', 'n_intervals')]
)
def update_graphs(n):
    data = get_oi_data(excel_filename)
    graph_divs = []
    row = []
    count = 0

    for sheet, df in sorted(data.items()):
        x = df['fetch_time']
        y = df['opnInterest']
        ltp = df['ltp'].iloc[-1] if 'ltp' in df.columns and not df['ltp'].isnull().all() else 'N/A'
        oi = df['opnInterest'].iloc[-1] if not df['opnInterest'].isnull().all() else 'N/A'
        oi_fmt = fmt_lacs(oi) if oi != 'N/A' else 'N/A'

        oi_changes = []
        ltp_changes = []
        now = df['fetch_time'].iloc[-1]

        for mins in [3, 5, 10, 15]:
            past_time = now - pd.Timedelta(minutes=mins)
            past_df = df[df['fetch_time'] <= past_time]

            if not past_df.empty:
                past_oi = past_df['opnInterest'].iloc[-1]
                past_ltp = past_df['ltp'].iloc[-1] if 'ltp' in past_df.columns else None

                delta_oi = oi - past_oi if oi != 'N/A' and not pd.isnull(past_oi) else 'N/A'
                delta_ltp = ltp - past_ltp if ltp != 'N/A' and not pd.isnull(past_ltp) else 'N/A'
            else:
                delta_oi = 'N/A'
                delta_ltp = 'N/A'

            oi_changes.append(fmt_lacs(delta_oi) if delta_oi != 'N/A' else 'N/A')
            ltp_changes.append(f"{delta_ltp:.2f}" if delta_ltp != 'N/A' else 'N/A')

        title = (
            f"{sheet} | LTP: {ltp:.2f} | OI: {oi_fmt} | <br>"
            f"ΔOI  3m: {oi_changes[0]}, 5m: {oi_changes[1]}, 10m: {oi_changes[2]}, 15m: {oi_changes[3]} | <br>"
            f"ΔLTP 3m: {ltp_changes[0]}, 5m: {ltp_changes[1]}, 10m: {ltp_changes[2]}, 15m: {ltp_changes[3]}"
        )

        fig = go.Figure()

        # Add OI line with custom hover showing LTP
        if 'ltp' in df.columns and not df['ltp'].isnull().all():
            fig.add_trace(go.Scatter(
                x=x,
                y=y,
                mode='lines+markers',
                name='OI',
                line=dict(color='blue'),
                customdata=df['ltp'].round(2),  # Round LTP to 2 decimals
                hovertemplate=
                    'Time: %{x|%H:%M}<br>' +
                    'OI: %{y}<br>' +
                    'LTP: %{customdata:.2f}<extra></extra>'
            ))
        else:
            fig.add_trace(go.Scatter(
                x=x,
                y=y,
                mode='lines+markers',
                name='OI',
                line=dict(color='blue'),
                hovertemplate=
                    'Time: %{x|%H:%M}<br>' +
                    'OI: %{y}<extra></extra>'
            ))

        # Layout with smaller fonts
        fig.update_layout(
            title=dict(text=title, font=dict(size=12)),
            xaxis=dict(
                title=dict(text='Time', font=dict(size=10)),
                tickformat='%H:%M',
                tickfont=dict(size=9)
            ),
            yaxis=dict(
                title=dict(text='Open Interest', font=dict(size=10)),
                tickfont=dict(size=9)
            ),
            font=dict(size=9),
            height=350,
            margin=dict(l=30, r=30, t=60, b=30)
        )

        graph_component = html.Div(
            dcc.Graph(figure=fig, id=f'graph-{sheet}'),
            style={'flex': '1', 'padding': '10px', 'minWidth': '48%'}
        )
        row.append(graph_component)
        count += 1

        # Every 2 graphs or last graph -> wrap into a row
        if count % 2 == 0 or count == len(data):
            graph_divs.append(html.Div(
                row,
                style={'display': 'flex', 'flexWrap': 'wrap', 'justifyContent': 'space-between'}
            ))
            row = []

    return graph_divs

if __name__ == "__main__":
    app.run(debug=True, port=8050)
