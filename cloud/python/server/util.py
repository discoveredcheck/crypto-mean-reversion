import json
from binance.client import AsyncClient
import aioboto3
import boto3
import base64
import aiopg
import asyncio
import datetime
import logging
import pandas as pd
import math
from io import BytesIO, StringIO
import aiocontext
from psycopg2.errors import UniqueViolation

# create table market (id serial not null, time timestamp not null, symbol varchar(16) not null, close real, unique(time, symbol));
# create index on market (symbol, time);

# create table slippage (id serial not null, time timestamp not null, symbol varchar(16) not null, fill_price real, target_price real, side varchar(8), unique(time, symbol));
# create index on slippage (symbol, time);

# create table symbol_info (id serial not null, symbol varchar(16) not null, filter_key varchar(255), filter_value jsonb);
# create index on symbol_info (symbol);

# create table orderbook (id serial not null, time timestamp not null, symbol varchar(16) not null, bid real, ask real, unique(time, symbol));
# create index on orderbook (symbol, time);

# create table account (id serial not null, time timestamp not null, balance real, account_name varchar(32), symbol varchar(16), unique(time, account_name, symbol));
# create index on account (time, account_name, symbol);

# docker login --username AWS -p $(aws ecr get-login-password --region eu-west-1) 123456789012.dkr.ecr.eu-west-1.amazonaws.com/binance_server

BINANCE_WAIT_TIME = 0.2
BINANCE_S3_BUCKET = 'my-binance-config'
logger = logging.getLogger('binance')
formatter = logging.Formatter('%(levelname)s - %(message)s')
ch = logging.StreamHandler()
ch.setFormatter(formatter)
logger.addHandler(ch)
logger.setLevel(logging.INFO)
logger.info('Info check')

BLACKLIST_UNIVERSE = ['USDCUSDT']
HEDGE_UNIVERSE = ['BTCDOWNUSDT', 'ETHDOWNUSDT', 'ADADOWNUSDT', 'LINKDOWNUSDT',
       'BNBDOWNUSDT', 'XTZDOWNUSDT', 'EOSDOWNUSDT', 'TRXDOWNUSDT',
       'XRPDOWNUSDT', 'DOTDOWNUSDT', 'LTCDOWNUSDT', 'UNIDOWNUSDT',
       'SXPDOWNUSDT', 'FILDOWNUSDT', 'YFIDOWNUSDT', 'BCHDOWNUSDT',
       'AAVEDOWNUSDT', 'SUSHIDOWNUSDT', 'XLMDOWNUSDT', '1INCHDOWNUSDT']
ASYNCIO_CONTEXT = aiocontext.Context()

def info(msg):
    logger.info(f'{ASYNCIO_CONTEXT.get("request_id", "None")} - {msg}')

def debug(msg):
    logger.debug(f'{ASYNCIO_CONTEXT.get("request_id", "None")} - {msg}')

def error(msg):
    logger.error(f'{ASYNCIO_CONTEXT.get("request_id", "None")} - {msg}')

def warn(msg):
    logger.warning(f'{ASYNCIO_CONTEXT.get("request_id", "None")} - {msg}')

async def save_fill_prices(db_secret_name, prices):
    conn_str = get_connection_string_from_secret(db_secret_name)
    async with aiopg.create_pool(conn_str) as pool:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                time = datetime.datetime.now().strftime('%d-%b-%Y %H:%M')
                for key, val in prices.items():
                    try:
                        data = (time, key, val['target_price'], val['fill_price'], val['side'])
                        await cur.execute(f'insert into slippage (time, symbol, target_price, fill_price, side) values (%s, %s, %s, %s, %s) returning id', data)
                        _id = []
                        async for row in cur:
                            _id.append(row)
                        assert len(_id) == 1
                    except Exception as e:
                        error(f'Error while saving fill prices for {key}. {e}')

async def get_binance_server_time(client):
    curr_time = await client.get_server_time()
    curr_time = curr_time['serverTime']
    curr_time = datetime.datetime.utcfromtimestamp(int(curr_time/1000))
    return curr_time

async def query_live_prices(db_secret_name, table_name, from_time: datetime.datetime, universe=None):
    conn_str = get_connection_string_from_secret(db_secret_name)
    time_str = datetime.datetime.strftime(from_time, '%d-%b-%Y %H:00:00')
    async with aiopg.create_pool(conn_str) as pool:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                try:
                    qry = f"select time, symbol, close from {table_name} where 1=1 and time >= '{time_str}'"
                    if universe is not None:
                        fmt = ','.join([f"'{x}'" for x in universe])
                        qry += f" and symbol in ({fmt})"
                    debug(qry)
                    await cur.execute(qry)
                    data = await cur.fetchall()
                    data = pd.DataFrame(data, columns=['time', 'symbol', 'close']).set_index('time').sort_index()
                    data = data.pivot(columns='symbol').droplevel(0, axis=1)
                    debug(f'Query output length = {len(data)}')
                except Exception as e:
                    await conn.close()
                    raise e
    return data


def get_connection_string_from_secret(secret_name):
    parsed = get_secret(secret_name)
    host = parsed['host']
    connection_string = f"""user={parsed["username"]}
        host={host} password={parsed["password"]} port={parsed["port"]}"""
    return connection_string

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


async def get_current_order_book(binance_client):
    curr_time = await get_binance_server_time(binance_client)
    curr_time = curr_time.strftime('%d-%b-%Y %H:%M:%S')
    tickers = await binance_client.get_orderbook_tickers()
    tickers = [x for x in tickers if
                x['symbol'].endswith('USDT')
                and 'BULL' not in x['symbol']
                and 'BEAR' not in x['symbol']]
    return curr_time, tickers

async def read_account(binance_client, ptype):
    acc = await binance_client.get_account()
    curr_time = await get_binance_server_time(binance_client)
    tkrs = await binance_client.get_orderbook_tickers()
    if ptype == 'bid':
        tkrs = [{'symbol': x['symbol'], 'price': float(x['bidPrice'])} for x in tkrs]
    elif ptype == 'ask':
        tkrs = [{'symbol': x['symbol'], 'price': float(x['askPrice'])} for x in tkrs]
    elif ptype == 'mean':
        tkrs = [{'symbol': x['symbol'], 'price': 0.5*(float(x['bidPrice'])+float(x['askPrice']))} for x in tkrs]
    else:
        raise ValueError(f'Unrecognised price type {ptype}')
    balances = {x['asset']: float(x['free'])+float(x['locked']) for x in acc['balances']}
    tkrs = {x['symbol']: float(x['price']) for x in tkrs}
    values = {a+'USDT': tkrs.get(a+'USDT', 0)*v for a, v in balances.items()}
    values['USDT'] = balances['USDT']
    return values, curr_time

def get_binance_client(secret):
    binance_api = get_secret(secret)
    is_testnet = binance_api.get('testnet', '1') == '1'
    return AsyncClient(binance_api['api_key'], binance_api['api_secret'])

async def download_account_balance(binance_secret_name, acc_name, db_secret_name):
    binance_client = get_binance_client(binance_secret_name)
    try:
        total, stime = await read_account(binance_client, 'mean')
        stime = stime.strftime('%d-%b-%Y %H:00:00')
        info(f'Balance = {sum(total.values())}')
        conn_str = get_connection_string_from_secret(db_secret_name)
        async with aiopg.create_pool(conn_str) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    for symbol, value in total.items():
                        try:
                            await cur.execute("insert into account (time, symbol, balance, account_name) values(%s, %s, %s, %s)", (stime, symbol, value, acc_name))
                        except UniqueViolation as ex:
                            error(f'Row already exists. {ex}')
    except Exception as ex2:
        error(f'Exception while downloading account balance {ex2}')
    finally:
         await binance_client.close_connection()
    return total

async def download_order_book(binance_secret_name, db_secret_name):
    conn_str = get_connection_string_from_secret(db_secret_name)
    binance_client = get_binance_client(binance_secret_name)
    all_count = 0
    async with aiopg.create_pool(conn_str) as pool:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                try:
                    curr_time, book = await get_current_order_book(binance_client)
                    data = [(curr_time, x['symbol'], float(x['askPrice']), float(x['bidPrice'])) for x in book]
                    count = 0
                    for row in data:
                        try:
                            await cur.execute("insert into orderbook (time, symbol, ask, bid) values(%s, %s, %s, %s) returning id", row)
                            _id = []
                            async for row in cur:
                                _id.append(row)
                            assert len(_id) == 1
                            count += 1
                        except UniqueViolation as e:
                            pass
                    all_count += count
                except Exception as ex:
                    error(f'Exception while downloading order book {ex}')
                finally:
                    await binance_client.close_connection()
    info(f'Inserted {all_count} rows into orderbook')
    return all_count

async def download_prices(db_secret_name, binance_secret_name, out_table, freq):
    conn_str = get_connection_string_from_secret(db_secret_name)
    binance_client = get_binance_client(binance_secret_name)
    curr_time = await get_binance_server_time(binance_client)
    info(curr_time)
    tickers = await binance_client.get_orderbook_tickers()
    await binance_client.close_connection()
    if freq == '1H':
        curr_time -= datetime.timedelta(hours=1)
        curr_time  = curr_time.strftime('%d-%b-%Y %H:00:00')
    elif freq == '5M':
        minute = (curr_time.minute//5)*5
        curr_time -= datetime.timedelta(minutes=5)
        curr_time  = curr_time.strftime('%d-%b-%Y %H:%M:00')
    elif freq == '15M':
        minute = (curr_time.minute//15)*15
        curr_time -= datetime.timedelta(minutes=15)
        curr_time  = curr_time.strftime('%d-%b-%Y %H:%M:00')
    elif freq == '1D':
        curr_time -= datetime.timedelta(days=1)
        curr_time  = curr_time.strftime('%d-%b-%Y')
    else:
        raise ValueError(f"Unrecognised freq {freq}")
    info(f'Prices aligned to {curr_time}')
    async with aiopg.create_pool(conn_str) as pool:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                data = [(curr_time, tick['symbol'], float(tick['askPrice'])) for tick in tickers if
                        tick['symbol'].endswith('USDT') and
                        'BULL' not in tick['symbol'] and
                        'BEAR' not in tick['symbol']]
                data = [x for x in data if x[2] > 0.]
                count = 0
                for row in data:
                    try:
                        await cur.execute(f"insert into {out_table} (time, symbol, close) values(%s, %s, %s) returning id", row)
                        _id = []
                        async for row in cur:
                            _id.append(row)
                        assert len(_id) == 1
                        count += 1
                    except UniqueViolation as e:
                        pass
                info(f'Inserted {count} rows into {out_table}')
    return count

async def save_target_positions(db_secret, positions):
    conn_str = get_connection_string_from_secret(db_secret)
    async with aiopg.create_pool(conn_str) as pool:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                curr_time = datetime.datetime.now()
                curr_time = curr_time.replace(minute=0, second=0)
                curr_time = curr_time.strftime('%d-%b-%Y %H:00:00')
                data = [(curr_time, k, v) for k, v in positions.items()]
                count = 0
                for row in data:
                    try:
                        await cur.execute("insert into positions (time, symbol, usdt) values(%s, %s, %s) returning id", row)
                        _id = []
                        async for row in cur:
                            _id.append(row)
                        assert len(_id) == 1
                        count += 1
                    except Exception as e:
                        error(f'Exception while inserting position record {row}')
                        error(str(e))
                info(f'Inserted {count} position records')
    return count

async def create_orders_tpsl(binance_secret_name, db_secret_name, universe, max_num_assets, hedge_weight, fund_size, tp_pct, sl_pct):
    def make_balances(univ, base):
        if base <= 0.:
            return {}
        bal = {k: balances[k] for k in univ if k in balances}
        info(bal)
        res = {}
        if tp_pct:
            tp_res = {k: v for k, v in bal.items() if ((v-base)/base*100)>tp_pct}
            info(f'Profit {tp_res}')
            res.update(tp_res)
        if sl_pct:
            sl_res = {k: v for k, v in bal.items() if ((v-base)/base*100)<-sl_pct and v > 10}
            info(f'Loss {sl_res}')
            common = set(sl_res.keys()).intersection(set(tp_res.keys()))
            if common:
                error(f'Conflict between TP/SL {common}')
                return {}
            res.update(sl_res)
        total_profit = sum([x-base for x in res.values()])
        info(f'Total profit {total_profit}')
        return res
    binance_client = get_binance_client(binance_secret_name)
    balances, _ = await read_account(binance_client, 'bid')
    cutoff_main = fund_size*(1.-hedge_weight)/max_num_assets
    info(f'Cutoff main {cutoff_main}')
    trades_main = make_balances(universe, cutoff_main)
    cutoff_hedge = fund_size*hedge_weight/len(HEDGE_UNIVERSE)
    info(f'Cutoff hedge {cutoff_hedge}')
    trades_hedge = make_balances(HEDGE_UNIVERSE, cutoff_hedge)
    trades_all = dict(trades_main)
    trades_all.update(trades_hedge)
    for symbol, trade_val in trades_all.items():
        try:
            resp = await update_position(binance_client, db_secret_name, symbol, -trade_val)
            if resp is not None:
                info(resp)
        except Exception as ex:
            error(f'Exception taking profit {symbol} {trade_val} {ex}')
    await binance_client.close_connection()

async def create_orders(binance_secret_name, db_secret_name, target_positions, save):
    binance_client = get_binance_client(binance_secret_name)
    try:
        balances = (await binance_client.get_account())['balances']
        balances = {x['asset']+'USDT': float(x['free']) for x in balances if x['asset']+'USDT' in target_positions}
        avg_price = await binance_client.get_all_tickers()
        avg_price = {x['symbol']: float(x['price']) for x in avg_price}
        avg_price = {k: v for k, v in avg_price.items() if k in target_positions}
        balances = {k: v*avg_price[k] for k, v in balances.items()}
        trades = {k: v-balances[k] for k, v in target_positions.items() if k in balances}
        # Sell first to accumulate USDT for potentially bigger BUYs later
        result = {k: await update_position(binance_client, db_secret_name, k, v) for k, v in trades.items() if v<0}
        result.update({k: await update_position(binance_client, db_secret_name, k, v) for k, v in trades.items() if v>0})
    except Exception as ex:
        await binance_client.close_connection()
        raise ex
    else:
        await binance_client.close_connection()
    filled = {x: y for x, y in result.items() if y is not None and y['status'] == 'FILLED'}
    info(f'{len(filled)}/{len(target_positions)} positions filled. {filled}')
    return filled

def get_admissible_step(raw, step):
    prec = int(round(-math.log(step, 10), 0))
    return round((raw//step)*step, prec)

async def get_filter(db_secret, symbol, filter_type):
    conn_str = get_connection_string_from_secret(db_secret)
    async with aiopg.create_pool(conn_str) as pool:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                qry = f"select filter_value from symbol_info where symbol='{symbol}' and filter_key='{filter_type}'"
                await cur.execute(qry)
                data = await cur.fetchone()
    return data[0] if data else None

async def sell_all(binance_client, db_secret, symbol):
    info(f'Selling all of {symbol}')
    try:
        qty = await binance_client.get_asset_balance(asset=symbol.replace('USDT', ''))
        qty = float(qty['free'])
        info(f'Free qty {qty}')
        filters_lot = await get_filter(db_secret, symbol, 'LOT_SIZE')
        min_qty = float(filters_lot['stepSize'])
        factor = int(round(1./min_qty))
        qty = math.floor(qty*factor)/factor
        info(f'Sell all quantity {qty}')
        resp = await binance_client.create_order(symbol=symbol, side='SELL', type='MARKET', quantity=qty)
        info(resp)
        fill_price = get_fill_price(resp)
        return {'status': resp['status'], 'fill_price': fill_price, 'side': 'SELL'}
    except Exception  as ex:
        error(f'Exception while selling all {ex}')
        return {'status': str(ex)}

def get_fill_price(resp):
    if 'fills' in resp and resp['status'] == 'FILLED':
        fills = [(float(x['price']), float(x['qty'])) for x in resp['fills']]
        amount = sum([x[0]*x[1] for x in fills])
        total_qty = sum([x[1] for x in fills])
        res = amount/total_qty if total_qty else 0.
        return res

def bround(x):
    return round(x, 6)

async def create_binance_market_order(client, symbol, side, amount_usdt):
    res = await client.create_order(symbol=symbol, side=side, type='MARKET', quoteOrderQty=bround(amount_usdt))
    #res = {'status': 'FILLED', 'symbol': symbol, 'side': side, 'amount': amount_usdt}
    info(f'Order created {res}')
    await asyncio.sleep(BINANCE_WAIT_TIME)
    return res

async def create_composite_order(client, symbol, side, amount_usdt, min_notional):
    qty = round(amount_usdt, 6)
    if side == 'BUY':
        info(f'Composite order {symbol}. BUY {min_notional+qty}, SELL {min_notional}')
        resp = await create_binance_market_order(client, symbol, 'BUY', min_notional+qty)
        resp = await create_binance_market_order(client, symbol, 'SELL', min_notional)
    elif side == 'SELL':
        info(f'Composite order {symbol}. BUY {min_notional}, SELL {min_notional+qty}')
        resp = await create_binance_market_order(client, symbol, 'BUY', min_notional)
        resp = await create_binance_market_order(client, symbol, 'SELL', min_notional+qty)
    else:
        raise ValueError(f'Unknown side {side}')
    return {'status': 'FILLED', 'symbol': symbol, 'side': side, 'amount': amount_usdt}

async def create_slow_order(client, symbol, side, amount_usdt, min_notional, batch_amount=100.):
    if amount_usdt < 0.1:
        return
    remaining = bround(amount_usdt)
    batch = bround(batch_amount)
    info(f'Slow order {symbol}, {side}, {amount_usdt}')
    bnum = 1
    while remaining > 0:
        to_order = min(batch, remaining)
        info(f'Batch {bnum}: {symbol}, {side}, {to_order}. Remaining {remaining}')
        if to_order >= min_notional:
            await create_binance_market_order(client, symbol, side, to_order)
        elif to_order > 0.1:
            await create_composite_order(client, symbol, side, to_order, min_notional)
        else:
            break
        remaining = bround(remaining - to_order)
        bnum+=1
    return {'status': 'FILLED', 'symbol': symbol, 'side': side, 'amount': amount_usdt}

async def update_position(binance_client, db_secret_name, symbol, trade):
    debug(f'Target {symbol}: {trade}')
    try:
        side = 'BUY' if trade > 0 else 'SELL'
        notional_filter = await get_filter(db_secret_name, symbol, 'MIN_NOTIONAL')
        limit = float(notional_filter['minNotional']) if notional_filter else None
        qty = abs(trade)
        resp = await create_slow_order(binance_client, symbol, side, qty, limit)
        return resp
    except Exception as ex:
        if side == 'SELL' and 'Account has insufficient balance for requested' in str(ex):
            res = await sell_all(binance_client, db_secret_name, symbol)
            await asyncio.sleep(BINANCE_WAIT_TIME)
            return res
        else:
            info(f'Exception updating position {ex}')
            return {'status': str(ex)}

def get_ema_weights(com, idx):
    alpha = 1./(1.+com)
    res = [(1.-alpha)**i for i in range(len(idx))][::-1]
    res = pd.Series(res, index=idx)
    assert res.iloc[-1] == 1., f'{res}'
    res = res/res.sum()
    return res

def get_signal_from_window(ts, window_size_short):
    if len(ts) < window_size_short*20:
        warn(f'Insufficient price history for ema. Expected {20*window_size_short}, got {len(ts)}.')
    returns = ts.diff(1)/ts.shift(1)
    weights_short = get_ema_weights(window_size_short, ts.index)
    short_mean = (returns*weights_short).sum()
    weights_long = get_ema_weights(2*window_size_short, ts.index)
    long_mean = (returns*weights_long).sum()
    return (-short_mean)

def signal_to_weights(sig, max_num_assets, hedge_weight):
    assert 0 <= hedge_weight <= 1.0
    tmp = sig.copy()
    tmp.loc[HEDGE_UNIVERSE] = 0.
    tmp.loc[[x for x in BLACKLIST_UNIVERSE if x in tmp.index]] = 0.
    rmidx = tmp.sort_values(ascending=False).iloc[max_num_assets:].index
    tmp.loc[rmidx] = 0.
    tmp.loc[tmp<0.0] = 0.
    tmp = (tmp>0).astype(float)
    wt_sum = tmp.sum()
    weights = tmp/max_num_assets*(1.-hedge_weight)
    weights.loc[HEDGE_UNIVERSE] = hedge_weight/len(HEDGE_UNIVERSE)
    return weights

async def get_target_positions(binance_secret_name, db_secret_name, table_name, window_size, window_size_short, universe, fund_size, max_num_assets, hedge_weight):
    binance_client = get_binance_client(binance_secret_name)
    res = {}
    try:
        curr_time = await get_binance_server_time(binance_client)
        curr_time = curr_time.replace(minute=0, second=0)
        start_time = curr_time - datetime.timedelta(hours=20*window_size_short)
        live_data = await query_live_prices(db_secret_name, table_name, start_time, universe+HEDGE_UNIVERSE)
        last_req_timestamp = curr_time - datetime.timedelta(hours=1)
        if live_data.index[-1] != last_req_timestamp:
            warn(f'Cannot access latest prices. Required {last_req_timestamp}, got {live_data.index[-1]}')
        signal = live_data.apply(lambda x: get_signal_from_window(x, window_size_short), axis=0)
        positions = signal_to_weights(signal, max_num_assets, hedge_weight)*fund_size
        res = {k: v for k, v in positions.iteritems()}
        res2 = {k: live_data[k].iloc[-1] for k in positions.keys()}
        await asyncio.sleep(BINANCE_WAIT_TIME)
    except Exception as e:
        await binance_client.close_connection()
        raise e
    else:
        await binance_client.close_connection()
    ppos = {k: v for k, v in res.items() if v != 0}
    ppos_price = {k: res2[k] for k in res.keys() if k in ppos}
    info(f'{len(ppos)}/{len(res)} non-zero positions found')
    info(f'Target positions {ppos}')
    info(f'Target prices {ppos_price}')
    return res, res2


async def get_current_config():
    f = BytesIO()
    async with aioboto3.client('s3', region_name='eu-west-1') as s3:
        await s3.download_fileobj(BINANCE_S3_BUCKET, 'config.json', f)
    f.seek(0)
    res = json.loads(f.getvalue())
    return res


async def get_current_universe():
    res = await get_current_config()
    return res['universe']

async def set_current_config(candidate_config):
    config = await get_current_config()
    new_config = {k: v for k, v in candidate_config.items() if k in config}
    curr_uni = set(config['universe'])
    res = {}
    if 'universe' in new_config:
        new_uni = set(new_config['universe'])
        res.update({
            'additions': list(new_uni-curr_uni),
            'deletions': list(curr_uni-new_uni),
        })
    async with aioboto3.resource('s3', region_name='eu-west-1') as s3:
        suffix = datetime.datetime.now().strftime(r'%d_%b_%Y_%H_%m_%S')
        obj = await s3.Object(BINANCE_S3_BUCKET, f'config.json.{suffix}')
        await obj.put(Body=json.dumps(config))
        config.update(new_config)
        obj = await s3.Object(BINANCE_S3_BUCKET, f'config.json')
        await obj.put(Body=json.dumps(config))
    return res

async def buy_bnb_maybe(binance_secret_name, threshold_usdt):
    client = get_binance_client(binance_secret_name)
    try:
        balance = float((await client.get_asset_balance('BNB'))['free'])
        curr_price = float((await client.get_orderbook_ticker(symbol='BNBUSDT'))['askPrice'])
        bnb_balance = balance*curr_price
        info(f'BNB balance {bnb_balance} ({threshold_usdt})')
        if bnb_balance < threshold_usdt:
            for _ in range(10):
                info(f'BNB order batch {_+1}')
                await client.create_order(symbol='BNBUSDT', side='BUY', type='MARKET', quoteOrderQty=10.0)
                await asyncio.sleep(1.0)
    except Exception as ex:
        error(f'Exception while buying BNB. {ex}')
    finally:
        await client.close_connection()

async def liquidate(binance_secret_name, db_secret_name):
    binance_client = get_binance_client(binance_secret_name)
    try:
        balances, _ = await read_account(binance_client, 'bid')
        for symbol, bal in balances.items():
            if symbol in ['BNBUSDT', 'USDT']:
                continue
            if bal > 1.:
                await update_position(binance_client, db_secret_name, symbol, -bal)
    except Exception as ex:
        error(f'Exception while liquidating {ex}')
    finally:
        await binance_client.close_connection()