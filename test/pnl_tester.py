import dash
from dash import dcc, html, Input, Output, State
import pandas as pd
import datetime

# Load your Excel file data (strike, LTP etc)
df_oi = pd.read_excel('/Users/utkarshalone/angel_one_test/oi_data_20250809.xlsx')

app = dash.Dash(__name__)

# In-memory store of trades
trades = []

def calculate_profit(trades):
    """Calculate total profit per strike and overall."""
    df = pd.DataFrame(trades)
    if df.empty:
        return {}, 0
    df['pl'] = df.apply(lambda row: (row['ltp'] - row['entry_ltp']) * row['lots'] * (1 if row['action']=='sell' else -1), axis=1)
    pl_per_strike = df.groupby('strike')['pl'].sum().to_dict()
    total_pl = df['pl'].sum()
    return pl_per_strike, total_pl

app.layout = html.Div([
    html.H3("Option Trade Tracker"),
    
    html.Div([
        dcc.Dropdown(
            id='strike-dropdown',
            options=[{'label': str(s), 'value': s} for s in sorted(df_oi['tradingSymbol'].unique())],
            placeholder="Select Strike Price"
        ),
        dcc.Dropdown(
            id='action-dropdown',
            options=[{'label': 'Buy', 'value': 'buy'}, {'label': 'Sell', 'value': 'sell'}],
            placeholder="Buy or Sell"
        ),
        dcc.Input(id='input-lots', type='number', placeholder='Lots', min=1),
        dcc.Input(id='input-ltp', type='number', placeholder='LTP'),
        dcc.DatePickerSingle(
            id='trade-date',
            date=datetime.datetime.now().date()
        ),
        html.Button("Add Trade", id='add-trade-btn'),
    ], style={'display': 'flex', 'gap': '10px'}),
    
    html.H4("Trades"),
    html.Div(id='trade-table'),
    
    html.H4("Total Profit/Loss:"),
    html.Div(id='total-pl'),
])

@app.callback(
    Output('trade-table', 'children'),
    Output('total-pl', 'children'),
    Input('add-trade-btn', 'n_clicks'),
    State('strike-dropdown', 'value'),
    State('action-dropdown', 'value'),
    State('input-lots', 'value'),
    State('input-ltp', 'value'),
    State('trade-date', 'date'),
    prevent_initial_call=True
)
def add_trade(n_clicks, strike, action, lots, ltp, trade_date):
    if not all([strike, action, lots, ltp, trade_date]):
        return dash.no_update, dash.no_update
    
    trade = {
        'strike': strike,
        'action': action,
        'lots': lots,
        'entry_ltp': ltp,
        'timestamp': trade_date,
        'ltp': df_oi.loc[df_oi['tradingSymbol'] == strike, 'ltp'].values[0] if not df_oi.loc[df_oi['tradingSymbol'] == strike].empty else ltp
    }
    trades.append(trade)
    
    # Build trade table
    rows = []
    for t in trades:
        rows.append(html.Tr([
            html.Td(t['strike']),
            html.Td(t['action']),
            html.Td(t['lots']),
            html.Td(t['entry_ltp']),
            html.Td(t['timestamp']),
            html.Td(round((t['ltp'] - t['entry_ltp']) * t['lots'] * (1 if t['action']=='sell' else -1), 2)),
        ]))
    
    table = html.Table([
        html.Thead(html.Tr(['Strike', 'Action', 'Lots', 'Entry LTP', 'Date', 'P/L'])),
        html.Tbody(rows)
    ])
    
    # Calculate total P/L
    pl_per_strike, total_pl = calculate_profit(trades)
    
    return table, f"{total_pl:.2f}"

if __name__ == '__main__':
    app.run(debug=True, port=8051)
