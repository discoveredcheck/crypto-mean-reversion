# crypto-mean-reversion

This is a personal Python project that I wrote in June and July 2021. It runs two rule-based trading strategies on the Binance crypto exchange through the Binance API. I use the project to manage my own portfolio on the Binance platform through automated trading.

The code sends real orders with real money. It has no dry-run mode and no tests.

## What it does

The hourly spot strategy works on a list of 203 USDT spot symbols. The list is the `universe` key in `cloud/python/server/config.json`. Each hour it stores the latest ask price of each symbol in the `market` table. For each symbol it computes an exponentially weighted mean of the hourly returns over a short window. The signal is the negative of this mean. A symbol that fell in the last hours gets a positive signal.

The strategy keeps the `max_num_assets` symbols with the highest positive signal. Each kept symbol gets the same weight. A fixed share of the fund, set by `hedge_weight`, is split equally over 20 Binance DOWN tokens, for example `BTCDOWNUSDT`. This split is the hedge. The weight times the fund size gives the target position of each symbol in USDT.

The strategy sends market orders to move each account to its target positions. A large order is split into batches of 100 USDT. The strategy stores the fill price and the target price of each order in the `slippage` table. One run can trade more than one Binance account. Each account has its own fund size and hedge weight.

The pairs strategy trades pairs of symbols on Binance futures. It is in `cloud/python/server/pairs_v2.py`. The file `pairs.py` is the first version. It downloads 5 days of hourly futures prices. It tests each pair of symbols for cointegration with the `coint` function of statsmodels. It runs the test 5 times on random 95 percent samples and takes the median p-value. A pair is a candidate if the median p-value is below 0.001.

The strategy ranks the candidates by the Sharpe ratio of a simulated trade on the same 5 days. It holds 3 pairs at a time. For each pair it computes the z-score of the price ratio. If the z-score is above 0, it sells the first symbol and buys the second. If the z-score is below 0, it does the opposite. It closes the pair when the z-score changes sign.

The size of each pair position is the fund size times the value of `leverage`, divided by 3. The open pairs and the list of candidates are stored in AWS Systems Manager Parameter Store.

The file `run_hourly.py` runs these tasks.

- `download_prices` stores the latest prices in the `market` table.
- `download_orderbook` stores the best bid and ask of each symbol in the `orderbook` table.
- `trade` computes the target positions and sends the orders.
- `take_profit` sells a position when its gain is above `--take-profit-pct` or its loss is above `--stop-loss-pct`.
- `download_account_balance` stores the balances of each account in the `account` table.
- `buy_bnb` buys BNB when the BNB balance is worth less than `--buy-bnb-threshold` USDT.
- `liquidate` sells all positions.

The default for `--buy-bnb-threshold` is 10 USDT. BNB pays the trading fees.

`server.py` is an aiohttp server. The GET routes are `/` and `/get_current_universe`. The POST routes are `/download_prices`, `/get_positions`, `/trade`, `/get_oou_symbols`, `/download_orderbook` and `/set_config`.

The repository has three notebooks. `futures.ipynb` holds the research for the pairs strategy. `monitor.ipynb` checks prices, slippage and balances for the spot strategy. `account_live.ipynb` shows the profit and loss of the futures account. The notebooks are stored without outputs.

## Built with

The code is written in Python 3. The Dockerfile installs Python 3.7 on Amazon Linux. The Python packages are `python-binance`, `pandas`, `numpy`, `statsmodels`, `aiohttp`, `aiopg`, `psycopg2`, `boto3`, `aioboto3` and `aiocontext`.

Postgres stores the prices, the order book, the slippage and the balances. AWS Secrets Manager stores the Binance API keys and the database login. AWS Systems Manager Parameter Store stores the accounts, the trading parameters and the pairs state. AWS S3 stores the live `config.json`. The hourly task uses Docker, AWS ECR and AWS ECS Fargate. The file `buildspec.yml` builds the image in AWS CodeBuild. The code fixes the AWS region to `eu-west-1`.

## How to run

You need a Binance account with an API key, an AWS account and a Postgres database.

1. Create the Postgres tables `market`, `slippage`, `symbol_info`, `orderbook` and `account`. The SQL statements are in the comments at the top of `cloud/python/server/util.py`.
2. In AWS Secrets Manager, create one secret for each Binance account. Each secret has the JSON fields `api_key` and `api_secret`. Create one secret for the database. It has the JSON fields `username`, `password`, `host` and `port`.
3. In Parameter Store, create `/binance/accounts` and `/binance/trading`. `/binance/accounts` is a JSON list. Each item is a pair of the account secret name and an object with the fields `fund_size`, `hedge_weight` and `name`. `/binance/trading` is a JSON object with the fields `db_secret_name`, `window_size_short` and `max_num_assets`.
4. Install the packages. The file `requirements.txt` lists only `python-binance`, `pandas` and `boto3`. Install the other packages from the list above by hand.
5. Build the image with these commands.

   ```
   cd cloud/python/server
   docker build --rm -t binance_server .
   ```

6. Run one or more tasks with this command. The container needs AWS credentials.

   ```
   docker run binance_server --tasks download_prices trade
   ```

7. Use the shell scripts `authenticate_ecr.sh`, `build_task.sh`, `push_task.sh` and `run_task.sh` to push the image to ECR and start the task on ECS. They contain the placeholder AWS account id `123456789012`. The file `task_input.json` contains placeholder subnet and security group ids. Replace them with your own values.

The server runs with this command.

```
python3 server.py --port 8080
```

The hourly tasks run with this command. Replace `TASK` with one or more task names from the list above.

```
python3 server/run_hourly.py --tasks TASK [TASK ...]
```

The pairs strategy runs with this command.

```
python3 pairs_v2.py
```

Add the flag `--clear` to close all pair positions first.

## Keys

The repository contains no keys. The code reads the Binance API keys and the database login from AWS Secrets Manager at run time.

The placeholders `YOUR_BINANCE_API_KEY`, `YOUR_BINANCE_API_SECRET` and `YOUR_DB_PASSWORD` are in the notebooks and in `cloud/python/server/pairs.py`.

## Plans

The planned work is listed in `ROADMAP.md`.

## License

This project is licensed under the MIT license. See the `LICENSE` file.
