# Roadmap

This is my plan for the next six months of work on the project. It is for one person, with one milestone for each month.

## Where the project is today

I wrote the code in June and July 2021. The first commits added the hourly spot strategy and its AWS deployment, with Docker, ECR, ECS Fargate and CodeBuild. Then I changed trading parameters by commit, for the fund size and the number of assets. After that I moved the parameters to AWS Parameter Store. At the end of June 2021 I added the first pairs strategy on Binance futures, in `pairs.py`. In July 2021 I added the second version, `pairs_v2.py`, and the research notebooks.

Today the code has no tests and no dry-run mode. The `requirements.txt` file is not complete. The backtests exist only in the notebooks. The AWS setup is done by hand with shell scripts, and monitoring is done by hand in a notebook.

## Month 1. Safe to run and safe to change

At the end of this month I can run the code on a new machine without real orders.

- Complete `requirements.txt` with every package the code imports, each at a fixed version.
- Update the Dockerfile from Python 3.7 to a current Python version and update the code for the current python-binance version.
- Move the code in `cloud/python/server` into one Python package. Remove the copies of the same code, because `get_secret` is defined in `util.py`, `pairs.py` and `pairs_v2.py`. Remove `pairs.py` after `pairs_v2.py` covers it.
- Move the fixed settings into one configuration file or into environment variables. The settings are the AWS region, the S3 bucket, the Parameter Store paths and the number of pairs. Add a `.env.example` file.
- Add a dry-run mode that logs the orders and does not send them. Add support for the Binance test network.
- Move the SQL statements from the comments in `util.py` into a schema file. Add a Docker Compose file that starts a local Postgres with this schema.
- Add continuous integration that runs a linter and the tests on each push.

## Month 2. Tests and a backtest

At the end of this month I can measure a change to a strategy before it trades.

- Add unit tests for the signal functions `get_ema_weights`, `get_signal_from_window`, `signal_to_weights`, `calc_pairs_pnl` and `calc_signal`.
- Add a command that downloads historical spot and futures prices into the local database. Today this code is in the notebooks, as `get_klines` and `get_klines_futures`.
- Move the simulation code `simulate` and `top_pairs_strategy` from the notebooks into a backtest module. The backtest uses the same signal code as the live task.
- Add trading costs to the backtest. Today the cost terms in `calc_pairs_pnl_v2` are set to 0. Use the exchange fees and the slippage measured in the `slippage` table.
- Add a backtest report with profit and loss, Sharpe ratio, largest drawdown and turnover for each strategy.

## Month 3. Better order execution

At the end of this month my orders cost less to trade, and no double orders are sent.

- Write a script that reports the slippage for each symbol and each side from the `slippage` table. Today this analysis is in `monitor.ipynb`.
- Choose the batch size of a large order from the order book depth. Today `create_slow_order` uses a fixed batch of 100 USDT.
- Add limit orders as an option. Today all orders are market orders.
- Check each order against the step size and the minimum notional in the `symbol_info` table. Add tests for this check.
- Replace the fixed wait of 0.2 seconds between API calls with retries and handling of the API rate limits. The wait is set by `BINANCE_WAIT_TIME`.
- Store each run and each order in the database. A second run in the same hour then does not send the same orders again.

## Month 4. Risk limits and monitoring

At the end of this month the system stops itself and tells me when something is wrong.

- Add risk limits for the largest position in each symbol, the largest total exposure and the largest loss in one day. When a limit is reached, the task stops trading and can run `liquidate`.
- Add a check that sends an alert when the margin ratio of the futures account is above a limit. Today I compute this ratio by hand in `account_live.ipynb`.
- Send an alert when a task fails or when the latest prices are missing. Today the code only writes a warning to the log.
- Store closed pair trades in a database table. Today `account_live.ipynb` reads them from the log file.
- Replace `monitor.ipynb` with a daily report of balances, profit and loss for each strategy, slippage and open pairs.

## Month 5. Strategy work

At the end of this month I have tested the open questions of both strategies with the backtest from month 2.

- Test the pair selection of the pairs strategy on data that the selection did not use, as a walk-forward test. Today the selection and the ranking use the same 5 days.
- Test an entry threshold above 0 for the z-score. Test a rule that closes a pair when the ratio moves too far against the position. Today the threshold is fixed at 0 and there is no such rule.
- Test more than 3 pairs and a position size that depends on the volatility of each pair.
- Build the symbol list of the spot strategy from the exchange each day. Today the 203 symbols and the 20 hedge tokens are fixed lists in the code and in `config.json`.
- Test a hedge of the spot strategy with a short futures position in place of the DOWN tokens.
- Test the 5 minute and 15 minute price frequencies for the spot strategy. The price download already supports them, and the strategy uses only the hourly one.

## Month 6. Deployment as code, documents and release

At the end of this month another person can deploy and run the project from the repository.

- Describe the AWS setup as code, covering the ECS task, the hourly schedule, the parameters, the secrets and the database. Remove the shell scripts that do this by hand.
- Make the project run without AWS. It reads keys and settings from environment variables and uses the local Postgres from month 1.
- Run both strategies on the Binance test network for two weeks as the acceptance test.
- Write the architecture document, a description of each strategy and a runbook for common failures.
- Tag version 1.0.

## Out of scope

The project has no machine learning models, and both strategies stay rule-based. The project has no accounts or data of other people.
