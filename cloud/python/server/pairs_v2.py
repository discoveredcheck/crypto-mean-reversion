from binance.client import Client
import json
import pandas as pd
import numpy as np
import datetime
import boto3
import time
import argparse
import logging
import os
from statsmodels.tsa.stattools import coint

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

def calc_pairs_pnl(v1, v2):
    ratio = v1/v2
    assert len(v1) >= window+1, f'{len(v1)}, {window+1}'
    prev = ratio.iloc[-window-1:-1]
    zratio = (ratio.iloc[-1]-prev.mean())/prev.std()
    if prev.std() == 0.:
        logger.error('Nan std')
        logger.error(prev)
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
        if not DRYRUN:
            res = client.futures_create_order(symbol=symbol, side=side, type='MARKET', quantity=round(abs(curr_position), _prec))
            logger.info(f'Order {res}')
    elif position*curr_position <= 0:
        pr = float(client.futures_ticker(symbol=symbol)['lastPrice'])
        qty = abs(position)/pr + abs(curr_position)
        qty = round(qty, _prec)
        side = 'SELL' if position<0 else 'BUY'
        logger.info(f'{symbol} {side} {qty}')
        try:
            if not DRYRUN:
                res = client.futures_create_order(symbol=symbol, side=side, type='MARKET', quantity=qty)
                logger.info(f'Order {res}')
        except Exception as ex:
            logger.error(f'Exception: {ex}')

def initialise(binance_secret):
    client = get_binance_client_from_secret(binance_secret)
    info = [x for x in client.futures_exchange_info()['symbols'] if x['contractType'] == 'PERPETUAL' and x['quoteAsset'] == 'USDT']
    prec = {x['symbol']: x['quantityPrecision'] for x in info}
    pprec = {x['symbol']: x['pricePrecision'] for x in info}
    return prec, pprec

def initial_data(binance_secret, all_pairs):
    logger.info('First data')
    client = get_binance_client_from_secret(binance_secret)
    while True:
        try:
            prices = []
            found = set()
            for chosen in all_pairs:
                for ticker in chosen:
                    if ticker in found:
                        continue
                    st = datetime.datetime.now() - datetime.timedelta(hours=window*2)
                    prices.append(get_klines_futures(client, ticker, st.strftime('%d-%b-%Y %H:%M')))
                    found.add(ticker)
            prices = pd.concat(prices, axis=1)
            return prices.iloc[-window*2:, :]
        except Exception as ex:
            logger.error(f'Exception while downloading data. {ex}')
            time.sleep(0.1)
            client = get_binance_client_from_secret(binance_secret)

def read_parameter(pname):
    ssm = boto3.client('ssm', region_name='eu-west-1')
    res = ssm.get_parameter(Name=pname)
    return json.loads(res['Parameter']['Value'])

def set_parameter(pname, pval):
    ssm = boto3.client('ssm', region_name='eu-west-1')
    res = ssm.put_parameter(Name=pname, Value=json.dumps(pval), Overwrite=True)

def update_account(acc, config, desired):
    acc_client = get_binance_client_from_secret(acc)
    position_size = config['fund_size']*config['leverage']/NUM_PAIRS
    for k, v in desired.items():
        set_position(acc_client, k, v*position_size)

def calc_signal(state, prices):
    new_state = []
    des = {}
    for pstate in state:
        m1, m2 = pstate['pair']
        holding = pstate['state']
        logger.info(f'Processing {(m1, m2)}')
        pos, zr = calc_pairs_pnl(prices[m1], prices[m2])
        logger.info(f'Current state {(m1, m2)} {holding}')
        logger.info(f'pos/zr {pos} {zr}')
        if holding == 'LONG':
            if pos<=0:
                pstate['final_price'] = [prices[m1].iloc[-1], prices[m2].iloc[-1]]
                pstate['date_closed'] = datetime.datetime.now().strftime('%d-%b-%Y %H:%M')
                logger.info(f'Closing trade {json.dumps(pstate)}')
                des[m1] = 0
                des[m2] = 0
            else:
                new_state.append(dict(pstate))
        elif holding == 'SHORT':
            if pos>=0:
                pstate['final_price'] = [prices[m1].iloc[-1], prices[m2].iloc[-1]]
                pstate['date_closed'] = datetime.datetime.now().strftime('%d-%b-%Y %H:%M')
                logger.info(f'Closing trade {json.dumps(pstate)}')
                des[m1] = 0
                des[m2] = 0
            else:
                new_state.append(dict(pstate))
        elif holding is None:
            des[m1] = pos
            des[m2] = -pos
            to_add = dict(pstate)
            to_add['state'] = 'LONG' if pos>0 else 'SHORT'
            to_add['initial_ratio'] = zr
            to_add['initial_price'] = [prices[m1].iloc[-1], prices[m2].iloc[-1]]
            new_state.append(to_add)
        else:
            raise ValueError(f'Undefined holding state {holding}')
    return new_state, des

def calc_pairs_pnl_v2(v1, v2, thr=0.):
    ratio = v1/v2
    zratio = (ratio-ratio.rolling(window).mean())/ratio.rolling(window).std()
    crossings = int((zratio*zratio.shift(1)<0).sum())
    ret1 = v1.diff(1)/v1.shift(1)
    ret2 = v2.diff(1)/v2.shift(1)
    pos1 = 0 *zratio
    pos1.loc[(zratio>thr)] = -0.5
    pos1.loc[zratio<-thr] = 0.5
    pos2 = -pos1
    pnl1 = pos1.shift(1)*ret1
    pnl2 = pos2.shift(1)*ret2
    cost1 = 0#pos1.diff(1).abs()*trading_cost/100
    cost2 = 0#pos2.diff(1).abs()*trading_cost/100
    net_pnl = pnl1+pnl2-cost1-cost2
    return net_pnl, crossings

def calc_new_universe(historical_live, num_pairs):
    res = find_cointegrated_pairs(historical_live)
    res_pnl = []
    for _, (m1, m2) in res:
        pp = calc_pairs_pnl_v2(historical_live.loc[:, m1], historical_live.loc[:, m2])[0]
        pp[pp.abs()>0.02] = np.nan
        res_pnl.append(sharpe(pp))
    best_res = sorted(zip(res_pnl, res), key=lambda x: x[0], reverse=True)
    _, best_res = zip(*best_res)
    _, best_res = zip(*best_res)
    best_unique = []
    for m1, m2 in best_res:
        best_unique.append([m1, m2])
        if len(best_unique) >= num_pairs:
            break
    return best_unique

def download_data(main_acc, num_prev_days):
    client = get_binance_client_from_secret(main_acc)
    while True:
        try:
            tickers = client.futures_symbol_ticker()
            logger.info(len(tickers))
            perpetual = []
            sd = (datetime.datetime.now()-datetime.timedelta(days=num_prev_days)).strftime('%d-%b-%Y %H:%M')
            for i, ts in enumerate(tickers):
                ticker = ts['symbol']
                if ticker.endswith('USDT'):
                    perpetual.append(get_klines_futures(client, ticker, sd))
            perpetual = pd.concat(perpetual, axis=1)
            return perpetual
        except Exception as ex:
            logger.error(f'Exception while downloading data. {ex}')
            time.sleep(0.1)
            client = get_binance_client_from_secret(main_acc)

def eval_pair(data_i, data_j):
    tmp = pd.concat([data_i, data_j], axis=1)
    idx = tmp.notna().all(axis=1)
    if idx.sum() < 0.9*len(tmp):
        return
    tmp = tmp.loc[idx]
    curr_l = []
    for _ in range(5):
        sampled = tmp.sample(frac=0.95)
        curr_l.append(coint(sampled.iloc[:, 0], sampled.iloc[:, 1])[1])
    curr = np.median(curr_l)
    return curr if curr < 0.001 else None

def find_cointegrated_pairs(data):
    n = data.shape[1]
    inputs = [(data.iloc[:, i], data.iloc[:, j]) for i in range(n) for j in range(i+1, n)]
    pairs = list(map(lambda x: eval_pair(*x), inputs))
    assert len(pairs) == len(inputs)
    pairs = [(x, (i.name, j.name)) for x, (i, j) in zip(pairs, inputs) if x]
    pairs = sorted(pairs, key=lambda x: x[0])
    return pairs

def get_state():
    return read_parameter(state_param)

def set_state(state):
    set_parameter(state_param, state)

def set_candidates(cand):
    with open(CURR_PAIRS_UNIVERSE_PATH, 'w') as f:
        f.write(json.dumps(cand))

def get_candidates():
    with open(CURR_PAIRS_UNIVERSE_PATH, 'r') as f:
        return json.loads(f.read())

def sharpe(pp):
    ser = pp.copy()
    ser[ser>ser.quantile(0.98)] = np.nan
    ser[ser<ser.quantile(0.02)] = np.nan
    return ser.mean()/ser.std()

def new_state(pair):
    return {'pair': pair, 'state': None, 'date_added': datetime.datetime.now().strftime('%d-%b-%Y %H:%M')}

def update_all_accounts(state, prices):
    next_state, signal = calc_signal(state, prices)
    logger.info(f'Signal {signal}')
    logger.info(f'Next state {next_state}')
    update_all_accounts_signal(signal)
    return next_state

def update_all_accounts_signal(signal):
    accs = read_parameter(binance_pairs_accounts_param)
    for acc, config in accs.items():
        try:
            logger.info(f'Account {acc}, {config}')
            update_account(acc, config, signal)
        except Exception as ex:
            logger.error(f'Exception updating account. {ex}')

CURR_PAIRS_UNIVERSE_PATH = '/home/ec2-user/binance/candidates'
DRYRUN = False
NUM_PAIRS = 3
NUM_PREV_DAYS = 5
window = 30
trading_cost = 0.04
slippage = 0.02
binance_pairs_accounts_param = '/binance/pairs_v2/accounts'
state_param = '/binance/pairs_v2/state'
MAIN_ACCOUNT = '/binance/binance/futures'
prec, pprec = initialise(MAIN_ACCOUNT)
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--clear', action='store_true')
    args = parser.parse_args()
    curr_state = get_state()
    logger.info(f'State {curr_state}')
    logger.info('Downloading prices for trading')
    prices = initial_data(MAIN_ACCOUNT, [x['pair'] for x in curr_state])
    logger.info(prices.index[-1])
    if args.clear:
        logger.info('Clearing all positions')
        sig_clear = {k: 0 for x in curr_state for k in x['pair']}
        update_all_accounts_signal(sig_clear)
        for cs in curr_state:
            cs['state'] = None
    next_state = update_all_accounts(curr_state, prices)
    set_state(next_state)  # redundant set_state in case the below code fails
    if len(next_state) != NUM_PAIRS:
        logger.info(f'Updaing pairs')
        num_addn_pairs = NUM_PAIRS-len(next_state)
        assert num_addn_pairs > 0, f'{num_addn_pairs}, {next_state}, {curr_state}'
        logger.info(f'{num_addn_pairs} new pairs needed')
        additional_pairs = get_candidates()
        
        candidates = additional_pairs[:5]
        prices = initial_data(MAIN_ACCOUNT, candidates)
        candidates_zratio = [abs(calc_pairs_pnl(prices[tp[0]], prices[tp[1]])[1]) for tp in candidates]
        logger.info(f'Candidates {list(zip(candidates_zratio, candidates))}')
        _, candidates_sorted = zip(*sorted(zip(candidates_zratio, candidates), key=lambda x:x[0], reverse=True))
        logger.info(candidates_sorted)
        existing_pairs = set([_ for x in next_state for _ in x['pair']])
        new_states = []
        for m1, m2 in candidates_sorted:
            logger.info(f'Trying {(m1, m2)}')
            if m1 not in existing_pairs and m2 not in existing_pairs:
                ns = new_state([m1, m2])
                new_states.append(ns)
                logger.info(f'Adding {(m1, m2)}')
                if len(new_states) == num_addn_pairs:
                    logger.info('New pairs found')
                    break
        prices = initial_data(MAIN_ACCOUNT, [x['pair'] for x in new_states])
        new_next_state = update_all_accounts(new_states, prices)
        next_state.extend(new_next_state)
        logger.info(f'New state after updating pairs {next_state}')
        set_state(next_state)
    test_pairs = get_candidates()[:10]
    prices = initial_data(MAIN_ACCOUNT, test_pairs)
    for tp in test_pairs:
        logger.info(f'Test pair {tp} = {calc_pairs_pnl(prices[tp[0]], prices[tp[1]])[1]}')
    logger.info('Updating candidates')
    data = download_data(MAIN_ACCOUNT, NUM_PREV_DAYS)
    candidates = calc_new_universe(data, 1000)
    set_candidates(candidates)
    logger.info('Done')
