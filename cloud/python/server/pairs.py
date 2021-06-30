from binance.client import Client
import json
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import datetime
import psycopg2
import boto3
import io
import ipywidgets as widgets
import time
import argparse

def get_klines_futures(client, symbol, start_str):
    data = client.futures_historical_klines(symbol=symbol, interval=client.KLINE_INTERVAL_1HOUR, start_str=start_str)
    ser = pd.Series([vv[4] for vv in data], dtype=float, index=pd.to_datetime([vv[6] for vv in data], unit='ms'))
    ser.name = symbol
    return ser.iloc[:-1]

def get_binance_client():
    return Client('YOUR_BINANCE_API_KEY', 'YOUR_BINANCE_API_SECRET')

def calc_pairs_pnl(v1, v2):
    print(v1.iloc[-1])
    ratio = v1/v2
    zratio = (ratio-ratio.rolling(30).mean())/ratio.rolling(30).std()
    thr = 0.
    ret1 = v1.diff(1)/v1.shift(1)
    ret2 = v2.diff(1)/v2.shift(1)
    pos1 = 0 *zratio
    pos1.loc[(zratio>thr)] = -0.5
    pos1.loc[zratio<-thr] = 0.5
    pos2 = 0 *zratio
    pos2.loc[(zratio>thr)] = 0.5
    pos2.loc[zratio<-thr] = -0.5
    pnl1 = pos1.shift(1)*ret1
    pnl2 = pos2.shift(1)*ret2
    cost1 = pos1.diff(1).abs()*0.00035
    cost2 = pos2.diff(1).abs()*0.00035
    net_pnl = pnl1+pnl2-cost1-cost2
    return (net_pnl, (pos1, pos2), (pnl1, pnl2))

def get_new_data(old):
    client = get_binance_client()
    perpetual2 = []
    start_str = old.index[-1] + datetime.timedelta(seconds=1)
    start_str = start_str.strftime('%d-%b-%Y %H:%M')
    for i, ticker in enumerate(old.columns):
        curr = get_klines_futures(client, ticker, start_str)
        if len(curr) == 0:
            return
        perpetual2.append(curr)
    perpetual2 = pd.concat(perpetual2, axis=1)
    return perpetual2

def set_position(symbol, position):
    client = get_binance_client()
    pr = float(client.futures_ticker(symbol=symbol)['lastPrice'])
    _prec = prec[symbol]
    curr_position = float(client.futures_position_information(symbol=symbol)[0]['positionAmt'])
    print(f'Curr/Des {curr_position}, {position}')
    res = {}
    if position == 0. and curr_position != 0:
        print(f'Closing {symbol}')
        side = 'BUY' if curr_position <0 else 'SELL'
        res = client.futures_create_order(symbol=symbol, side=side, type='MARKET', quantity=round(abs(curr_position), _prec))
        print('Status', res['status'])
    elif position*curr_position <= 0:
        if position == 0 and curr_position == 0:
            return
        qty = position_size/pr + abs(curr_position)
        qty = round(qty, _prec)
        side = 'SELL' if position<0 else 'BUY'
        print(f'{side} {qty}')
        res = client.futures_create_order(symbol=symbol, side=side, type='MARKET', quantity=qty)
        print('Status', res['status'])

def initialise():
    client = get_binance_client()
    prec = [x for x in client.futures_exchange_info()['symbols'] if x['contractType'] == 'PERPETUAL' and x['quoteAsset'] == 'USDT']
    prec = {x['symbol']: x['quantityPrecision'] for x in prec}
    return prec

def initial_data(clear_positions):
    print('First data')
    client = get_binance_client()
    prices = []
    found = set()
    for chosen in chosen_all:
        for ticker in chosen:
            if ticker in found:
                continue
            client.futures_change_leverage(symbol=ticker, leverage=leverage)
            prices.append(get_klines_futures(client, ticker, '1-Jun-2021'))
            found.add(ticker)
            if clear_positions:
                set_position(ticker, 0)
    prices = pd.concat(prices, axis=1)
    return prices.iloc[-50:, :]

parser = argparse.ArgumentParser()
parser.add_argument('--clear', action='store_true')
parser.add_argument('--force-first-trade', action='store_true')
args = parser.parse_args()
position_size = 4000
leverage = 20
chosen_all = [('SKLUSDT', 'CELRUSDT'), ('HOTUSDT', 'NKNUSDT')]
prec = initialise()
prices = initial_data(args.clear)
first = True
while True:    
    total_curr_pnl = 0
    total_pfl_pnl = 0
    for chosen in chosen_all:
        print(f'Processing {chosen}')
        pnl, pos, _ = calc_pairs_pnl(prices.loc[:, chosen[0]], prices.loc[:, chosen[1]])
        pnl *= 100
        pos1 = pos[0].iloc[-1]
        pos2 = pos[1].iloc[-1]
        print('pos', pos1, pos2)
        print('pnl', pnl.iloc[-1], pnl.sum())
        total_curr_pnl += pnl.iloc[-1]
        total_pfl_pnl += pnl.sum()
        set_position(chosen[0], pos1)
        set_position(chosen[1], pos2)
        print('----------')
    print(f'Total pnl {total_curr_pnl} {total_pfl_pnl}')
    print('==========')
    if not first:
        time.sleep(55*60)
    print('Polling for data')
    nd = None
    while nd is None:
        nd = get_new_data(prices)
        time.sleep(5)
    prices = pd.concat([prices, nd], axis=0).iloc[-50:, :]
    print(prices.index[-1])
    first = False
