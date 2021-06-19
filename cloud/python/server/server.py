import os
from aiohttp import web
import traceback
import asyncio
import argparse
from util import download_prices, logger, create_orders, get_oou_symbols,\
    get_current_universe, set_current_config, get_current_config, get_target_positions,\
    save_fill_prices, download_order_book, get_binance_server_time, get_secret

async def hello(request):
    return web.Response(text="Pyserver is running!\n")


@web.middleware
async def logging(request, handler):
    logger.info(f'Received {request}')
    item = await request.text()
    logger.info(item)
    return await handler(request)


@web.middleware
async def gateway(request, handler):
    try:
        return await handler(request)
    except Exception as e:
        err = traceback.format_exc()
        logger.error(e)
        logger.error(err)
        return web.json_response(status=500, data=err)


async def get_positions_handler(request):
    item = await request.json()
    config = await get_current_config()
    targets, _ = await get_target_positions(
                        item['binance_secret_name'],
                        item['db_secret_name'],
                        item['table_name'],
                        config['window_size'],
                        config['window_size_short'],
                        config['universe'],
                        config['fund_size'],
                        config['max_num_assets'])
    return web.json_response(targets)

async def download_prices_handler(request):
    item = await request.json()
    count = await download_prices(item['db_secret_name'], item['binance_secret_name'], item['table_name'], item.get('freq', '1H'))
    assert count > 0, f'No prices downloaded'
    return web.json_response(count)

async def trade_handler(request):
    item = await request.json()
    config = await get_current_config()
    if 'fund_size' in item:
        config['fund_size'] = item['fund_size']
    targets, target_prices = await get_target_positions(
                        item['binance_secret_name'],
                        item['db_secret_name'],
                        item['table_name'],
                        config['window_size'],
                        config['window_size_short'],
                        config['universe'],
                        config['fund_size'],
                        config['max_num_assets'])
    result = await create_orders(item['binance_secret_name'], item['db_secret_name'], targets, item.get('save', False))
    if item.get('save_fill_prices', False):
        await save_fill_prices(item['db_secret_name'], {k: {'fill_price': v['fill_price'], 'target_price': target_prices[k], 'side': v['side']} for k, v in result.items() if 'fill_price' in v})
    return web.json_response(result)

async def get_oou_symbols_handler(request):
    item = await request.json()
    curr_universe = await get_current_universe()
    result = await get_oou_symbols(item['binance_secret_name'], curr_universe)
    return web.json_response(result)

async def get_current_universe_handler(request):
    result = await get_current_universe()
    return web.json_response(result)

async def set_config_handler(request):
    item = await request.json()
    res = await set_current_config(item)
    return web.json_response(res)

async def download_order_book_handler(request):
    item = await request.json()
    res = await download_order_book(item['binance_secret_name'], item['db_secret_name'])
    return web.json_response(res)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Binance server')
    parser.add_argument('--port', default=8080, type=int)
    args, _ = parser.parse_known_args()
    app = web.Application()
    app.middlewares.append(gateway)
    app.middlewares.append(logging)
    routes = [
        web.get('/', hello),
        web.post('/download_prices', download_prices_handler),
        web.post('/get_positions', get_positions_handler),
        web.post('/trade', trade_handler),
        web.post('/get_oou_symbols', get_oou_symbols_handler),
        web.get('/get_current_universe', get_current_universe_handler),
        web.post('/download_orderbook', download_order_book_handler),
        web.post('/set_config', set_config_handler),
    ]
    app.add_routes(routes)
    web.run_app(app, port=args.port)
