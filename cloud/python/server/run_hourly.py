import asyncio
from util import download_prices, download_order_book, get_target_positions, create_orders, save_fill_prices, get_current_config,\
    logger, get_secret, get_binance_server_time, download_account_balance, info, error, warn, ASYNCIO_CONTEXT, create_orders_tpsl,\
    buy_bnb_maybe
import argparse
import aiocontext
import os
import traceback
import json

async def wrapper(coro, name='GLOBAL'):
    ASYNCIO_CONTEXT['request_id'] = name
    try:
        return await coro
    except Exception as ex:
        error(f'Exception in task {coro}')
        error(traceback.format_exc())

async def trade_save(acc, db_name, fund_size, window_size_short, universe, max_num_assets, save, hedge_weight):
    targets, target_prices = await get_target_positions(
                            acc,
                            db_name,
                            'market',
                            None,
                            window_size_short,
                            universe,
                            fund_size,
                            max_num_assets,
                            hedge_weight)
    result = await create_orders(acc, db_name, targets, False)
    if save:
        to_save = {k: {'fill_price': v['fill_price'], 'target_price': target_prices[k], 'side': v['side']}
                   for k, v in result.items() if 'fill_price' in v}
        await save_fill_prices(db_name, to_save)

async def main(args):
    query_tasks = args.tasks
    global_config = json.load(open('server/config.json'))
    main_account = global_config['binance_accounts'][0][0]
    db_name = os.environ.get('BINANCE_DB_SECRET_LOCAL', None)
    if not db_name:
        db_name = global_config['db_secret_name']
    tasks = []
    if 'download_orderbook' in query_tasks:
        tasks.append(asyncio.create_task(wrapper(download_order_book(main_account, db_name))))
    if 'download_prices' in query_tasks:
        prices_task = asyncio.create_task(wrapper(download_prices(db_name, main_account, 'market', '1H'), name='rds'))
        await prices_task
        info('Price download finished')
    for i, (acc, config) in enumerate(global_config['binance_accounts']):
        if 'trade' in query_tasks:
            tasks.append(asyncio.create_task(wrapper(trade_save(
                                                acc,
                                                db_name,
                                                config['fund_size'],
                                                global_config['window_size_short'],
                                                global_config['universe'],
                                                global_config['max_num_assets'],
                                                i==0,
                                                config['hedge_weight']), name=config['name'])))
        if 'take_profit' in query_tasks and 'trade' not in query_tasks:
            tasks.append(asyncio.create_task(wrapper(create_orders_tpsl(
                                                acc,
                                                db_name,
                                                global_config['universe'],
                                                global_config['max_num_assets'],
                                                config['hedge_weight'],
                                                config['fund_size'],
                                                args.take_profit_pct,
                                                args.stop_loss_pct), name=config['name'])))
    for task in tasks:
        await task
    if 'download_account_balance' in query_tasks:
        for i, (acc, config) in enumerate(global_config['binance_accounts']):
            await asyncio.create_task(wrapper(download_account_balance(acc, config['name'], db_name), name=config['name']))
    if 'buy_bnb' in query_tasks:
        for i, (acc, config) in enumerate(global_config['binance_accounts']):
            await asyncio.create_task(wrapper(buy_bnb_maybe(acc, args.buy_bnb_threshold), name=config['name']))
    info('All tasks finished')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Binance Hourly')
    parser.add_argument('--tasks', nargs='+', required=True)
    parser.add_argument('--take-profit-pct', type=float, default=None)
    parser.add_argument('--stop-loss-pct', type=float, default=None)
    parser.add_argument('--buy-bnb-threshold', type=float, default=10.)
    args = parser.parse_args()
    loop = asyncio.get_event_loop()
    aiocontext.wrap_task_factory(loop)
    ASYNCIO_CONTEXT.attach(loop)
    loop.run_until_complete(main(args))
    #asyncio.run(main(args.tasks))
