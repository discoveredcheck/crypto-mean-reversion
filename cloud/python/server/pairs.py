from binance.client import Client
import json
import pandas as pd
import numpy as np
import datetime
import boto3
import time
import argparse
import logging

logger = logging.getLogger('pairs')
formatter = logging.Formatter('%(levelname)s - %(message)s')
ch = logging.StreamHandler()
ch.setFormatter(formatter)
logger.addHandler(ch)
logger.setLevel(logging.INFO)
logger.info('Info check')

def get_klines_futures(client, symbol, start_str):
    data = client.futures_historical_klines(symbol=symbol, interval=client.KLINE_INTERVAL_1HOUR, start_str=start_str)
    ser = pd.Series([vv[4] for vv in data], dtype=float, index=pd.to_datetime([vv[6] for vv in data], unit='ms'))
    ser.name = symbol
    return ser.iloc[:-1]

def get_secret(secret_name):
    region_name = "eu-west-1"
    # Create a Secrets Manager client
    client = boto3.client('secretsmanager', region_name='eu-west-1')
    get_secret_value_response = client.get_secret_value(SecretId=secret_name)
    if 'SecretString' in get_secret_value_response:
        secret = get_secret_value_response['SecretString']
    else:
        secret = base64.b64decode(get_secret_value_response['SecretBinary'])
    return json.loads(secret)

def get_binance_client_from_secret(secret):
    binance_api = get_secret(secret)
    return Client(binance_api['api_key'], binance_api['api_secret'])
"""
def get_binance_client():
    return Client('YOUR_BINANCE_API_KEY', 'YOUR_BINANCE_API_SECRET')
"""
def calc_pairs_pnl(v1, v2):
    ratio = v1/v2
    assert len(v1) >= window+1, f'{len(v1)}, {window+1}'
    prev = ratio.iloc[-window-1:-1]
    zratio = (ratio.iloc[-1]-prev.mean())/prev.std()
    thr = 0.
    pos1 = 0
    if zratio > thr:
        pos1 = -0.5
    elif zratio < -thr:
        pos1 = 0.5
    return pos1, zratio

def set_position(client, symbol, position):
    _prec = prec[symbol]
    curr_position = float(client.futures_position_information(symbol=symbol)[0]['positionAmt'])
    res = {}
    if position == 0 and curr_position == 0:
        logger.info('Desired and current positions both zero. Nothing to be done')
        return
    if position == 0. and curr_position != 0:
        logger.info(f'Closing {symbol}')
        side = 'BUY' if curr_position <0 else 'SELL'
        res = client.futures_create_order(symbol=symbol, side=side, type='MARKET', quantity=round(abs(curr_position), _prec))
        logger.info(f'Status {res["status"]}')
    elif position*curr_position <= 0:
        pr = float(client.futures_ticker(symbol=symbol)['lastPrice'])
        qty = abs(position)/pr + abs(curr_position)
        qty = round(qty, _prec)
        side = 'SELL' if position<0 else 'BUY'
        logger.info(f'{symbol} {side} {qty}')
        try:
            res = client.futures_create_order(symbol=symbol, side=side, type='MARKET', quantity=qty)
            logger.info(f'Status {res["status"]}')
        except Exception as ex:
            logger.error(f'Exception: {ex}')

def initialise(binance_secret):
    client = get_binance_client_from_secret(binance_secret)
    info = [x for x in client.futures_exchange_info()['symbols'] if x['contractType'] == 'PERPETUAL' and x['quoteAsset'] == 'USDT']
    prec = {x['symbol']: x['quantityPrecision'] for x in info}
    pprec = {x['symbol']: x['pricePrecision'] for x in info}
    return prec, pprec

def initial_data(binance_secret, clear_positions, all_pairs):
    logger.info('First data')
    client = get_binance_client_from_secret(binance_secret)
    prices = []
    found = set()
    for chosen in all_pairs:
        for ticker in chosen:
            if ticker in found:
                continue
            st = datetime.datetime.now() - datetime.timedelta(hours=window*2)
            prices.append(get_klines_futures(client, ticker, st.strftime('%d-%b-%Y %H:%M')))
            found.add(ticker)
            if clear_positions:
                set_position(client, ticker, 0)
    prices = pd.concat(prices, axis=1)
    return prices.iloc[-window*2:, :]

def read_parameter(pname):
    ssm = boto3.client('ssm', region_name='eu-west-1')
    res = ssm.get_parameter(Name=pname)
    return json.loads(res['Parameter']['Value'])

def set_parameter(pname, pval):
    ssm = boto3.client('ssm', region_name='eu-west-1')
    res = ssm.put_parameter(Name=pname, Value=pval, Overwrite=True)

def update_account(acc, config, pairs, prices):
    acc_client = get_binance_client_from_secret(acc)
    position_size = config['fund_size']*config['leverage']/len(pairs)
    for m1, m2 in pairs:        
        logger.info(f'Processing {(m1, m2)}')
        pos, zr = calc_pairs_pnl(prices[m1], prices[m2])
        logger.info(f'pos/zr {pos} {zr}')
        set_position(acc_client, m1, pos*position_size)
        set_position(acc_client, m2, -pos*position_size)

window = 30
binance_pairs_universe_param = '/binance/pairs/universe'
binance_pairs_accounts_param = '/binance/pairs/accounts'
MAIN_ACCOUNT = '/binance/binance/futures'
prec, pprec = initialise(MAIN_ACCOUNT)
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--clear', action='store_true')
    args = parser.parse_args()
    logger.info('Trading')
    chosen_all = read_parameter(binance_pairs_universe_param)
    logger.info(chosen_all)
    prices = initial_data(MAIN_ACCOUNT, args.clear, chosen_all)
    logger.info(prices.index[-1])
    accs = read_parameter(binance_pairs_accounts_param)
    for acc, config in accs.items():
        try:
            logger.info(f'Account {acc}, {config}')
            update_account(acc, config, chosen_all, prices)
        except Exception as ex:
            logger.error(f'Exception updating account. {ex}')
    logger.info('Done')
