# myTrade

myTrade is a personal, local-first Indian market research and trading application focused on historical analysis, live market data, price-action research, backtesting, machine-learning-assisted prediction, and paper trading.

The project is intended for private/local use. The core application, datasets, models, strategies, and trading logic will run on the user's own computer. Angel One SmartAPI will be used as the initial broker and market-data integration.

> **Important:** myTrade is a research and decision-support application. Predictions are probabilistic and must not be treated as guaranteed market outcomes.

## Objectives

myTrade will be designed to:

- connect securely to Angel One SmartAPI
- download and cache historical market data locally
- consume live market data through WebSocket feeds
- build 1-minute, 5-minute and other configurable candles
- analyse trend, momentum, volatility and price action
- calculate technical indicators and market-structure features
- backtest strategies against historical data
- train and evaluate machine-learning models
- estimate short-term UP / DOWN / SIDEWAYS probabilities
- generate explainable trading signals
- simulate trades using a paper-trading engine
- track performance, drawdown, win rate, expectancy and risk
- optionally support real order execution only after sufficient validation

## Architecture

```text
                    Angel One SmartAPI
                           |
            +--------------+--------------+
            |                             |
      Historical REST                WebSocket V2
            |                             |
            +--------------+--------------+
                           |
                    Local Data Layer
               Parquet + SQLite metadata
                           |
                           v
                     Feature Engine
        Indicators + Price Action + Market Structure
                           |
                           v
                     Backtest Engine
                           |
                           v
                  ML Prediction Engine
                 UP / DOWN / SIDEWAYS
                           |
                           v
                    Strategy Engine
                           |
                           v
                      Risk Manager
                           |
              +------------+------------+
              |                         |
        Paper Trading              Live Execution
                                  (future phase)
```

## Primary Technology Stack

### Core

- **Python 3.12+**
- Angel One SmartAPI Python SDK
- WebSocket client
- python-dotenv

### Data and analytics

- Pandas
- NumPy
- PyArrow / Parquet
- SQLite

### Machine learning

Initial models:

- scikit-learn
- Logistic Regression
- Random Forest
- Gradient Boosting

Possible later additions:

- XGBoost
- LightGBM
- time-series models where justified by backtesting evidence

### Local API / dashboard

When required:

- FastAPI
- Uvicorn
- Plotly or another lightweight charting layer

The trading engine will remain Python-first even if a separate frontend is introduced later.

## Planned Project Structure

```text
myTrade/
|
+-- app/
|   +-- broker/
|   |   +-- angel_auth.py
|   |   +-- angel_historical.py
|   |   +-- angel_live.py
|   |   +-- instruments.py
|   |
|   +-- data/
|   |   +-- downloader.py
|   |   +-- candle_builder.py
|   |   +-- storage.py
|   |
|   +-- features/
|   |   +-- indicators.py
|   |   +-- price_action.py
|   |   +-- market_structure.py
|   |
|   +-- models/
|   |   +-- train.py
|   |   +-- predict.py
|   |   +-- evaluate.py
|   |
|   +-- backtest/
|   |   +-- engine.py
|   |
|   +-- strategy/
|   |   +-- signal_engine.py
|   |
|   +-- risk/
|   |   +-- risk_manager.py
|   |
|   +-- execution/
|       +-- paper_broker.py
|       +-- live_broker.py
|
+-- data/
|   +-- raw/
|   +-- processed/
|   +-- live/
|
+-- models/
+-- scripts/
+-- tests/
+-- .env.example
+-- .gitignore
+-- requirements.txt
+-- main.py
+-- README.md
```

## Angel One Integration

Angel One SmartAPI will initially provide:

- authentication
- instrument lookup/token resolution
- historical candle data
- live WebSocket market data

The first supported market instruments will include:

- NIFTY 50
- BANK NIFTY
- INDIA VIX
- selected NSE equities
- current futures/options where useful for analysis

Broker-specific code will remain isolated under `app/broker/` so that another market-data provider or broker can be added later without rewriting the strategy and ML layers.

## Local-First Data Design

Historical data should be downloaded once and cached locally rather than repeatedly requested from the broker API.

Example:

```text
data/
+-- raw/
|   +-- nifty/
|   +-- banknifty/
|
+-- processed/
|   +-- features/
|
+-- live/
```

Parquet will be preferred for larger OHLC datasets because it is compact and fast for analytics. SQLite will be used for metadata such as trades, strategy runs, model experiments and application state.

## Feature Engineering

Planned features include:

### Price data

- Open / High / Low / Close
- percentage returns
- log returns
- candle body size
- upper/lower wick percentage
- candle range

### Technical indicators

- EMA
- SMA
- RSI
- MACD
- ATR
- VWAP where applicable

### Market structure

- Higher High
- Higher Low
- Lower High
- Lower Low
- breakout detection
- failed breakout detection
- support/resistance zones
- trend regime
- volatility regime

### Context

- time of day
- day of week
- previous-day high/low
- opening range
- NIFTY / BANKNIFTY relationship
- INDIA VIX context
- derivative volume/open interest where appropriate and available

## Prediction Philosophy

The model should not attempt to claim that an exact future market price is certain.

A preferred prediction format is probabilistic:

```text
NIFTY 50
Current Price: 25,620

Next 5-minute model output
--------------------------
UP        67%
DOWN      23%
SIDEWAYS  10%

Trend      Bullish
Volatility Medium
Confidence 71%
```

Prediction targets, thresholds and confidence calibration must be validated using out-of-sample testing.

## Backtesting

The backtesting engine should account for more than win rate.

Metrics will include:

- net P&L
- win rate
- loss rate
- profit factor
- expectancy
- maximum drawdown
- average winner
- average loser
- risk/reward ratio
- consecutive wins/losses
- trade frequency
- slippage
- transaction costs

Walk-forward and out-of-sample validation will be preferred over judging a model on the same data used for training.

## Development Roadmap

### Phase 1 - Foundation

- Python project structure
- environment configuration
- Angel One authentication
- instrument lookup
- historical candle downloader
- local Parquet storage

### Phase 2 - Live Market Data

- Angel One WebSocket V2
- live tick ingestion
- candle builder
- reconnect/retry handling
- live local storage

### Phase 3 - Feature Engine

- indicators
- price action
- market structure
- volatility features

### Phase 4 - Backtesting

- strategy interface
- historical replay
- transaction costs
- slippage
- detailed performance statistics

### Phase 5 - Machine Learning

- dataset preparation
- feature selection
- model training
- time-series-safe train/test split
- probability calibration
- model evaluation

### Phase 6 - Live Prediction

- real-time feature generation
- UP / DOWN / SIDEWAYS prediction
- confidence score
- explainable signal components

### Phase 7 - Paper Trading

- simulated orders
- position management
- stop-loss/target logic
- risk limits
- performance journal

### Phase 8 - Optional Live Trading

Only after extensive backtesting and paper-trading validation:

- static public IPv4 where required by broker/exchange rules
- Angel order API
- hard risk controls
- emergency kill switch
- daily loss limit

Live execution will be kept separate from signal generation so it can be disabled completely.

## Security

Secrets must never be committed to GitHub.

Local credentials will be stored in `.env`:

```env
ANGEL_API_KEY=
ANGEL_CLIENT_CODE=
ANGEL_PIN=
ANGEL_TOTP_SECRET=
```

The real `.env` file must be ignored by Git.

Only `.env.example` should be committed.

The application must never log or expose:

- API keys
- PINs
- TOTP secrets
- JWT tokens
- refresh tokens
- feed tokens

## Cloudflare Access Tunnel

The dashboard remains a local Python application. Optional access at `https://mytrade.halovialabs.com` uses a Cloudflare Tunnel to the local Streamlit port and should be protected by a Cloudflare Access policy limited to your own verified email. Follow [cloudflare/README.md](cloudflare/README.md). Do not publish Angel One credentials or move trading logic into a public Worker.

## Safety Principles

- no automatic live trading during initial development
- backtest before paper trading
- paper trade before real-money execution
- no strategy should bypass the risk manager
- always enforce position-size and loss limits
- keep broker execution separate from prediction logic
- treat model confidence as probabilistic, not guaranteed

## Current Status

Python foundation, Angel One authentication and historical-data ingestion, deterministic backtesting, and a local live-signal/dashboard workflow are in place. Candle history is stored locally, and live BUY/SELL signals are audited in a durable prediction journal.


## Local Dashboard, Backtesting, and Live Signals

A local dashboard is available for interactive backtesting and live chart research. It uses deterministic technical rules only; the application has no OpenAI API dependency.

### Install and launch

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and enter Angel One credentials locally. Then start the dashboard bound to this computer:

```bash
streamlit run app/dashboard.py --server.address 127.0.0.1
```

The Live Chart tab loads recent Angel One candles, subscribes to the selected instrument through SmartAPI WebSocket V2, and displays a live candle chart. EMA crossover markers and the latest closed-candle signal are labeled **BUY**, **SELL**, or **HOLD**. The signal engine excludes the active, unfinished candle to avoid repainting. It provides research signals only and never submits orders.

The Backtesting tab accepts historical OHLC CSV files and displays the equity curve, trade list, net P&L, return, drawdown, win rate, and profit factor. The CLI accepts either CSV or Parquet input:

```bash
python main.py backtest --input path/to/candles.csv --fast 9 --slow 21 --capital 100000 --quantity 1 --fee-per-order 20 --slippage-bps 5
python -m pytest
```

Backtest decisions generated from a candle close execute at the next candle open. Fees and slippage are configurable. Backtests are simulations and can differ from actual fills and market conditions.

## OpenAI API Removal

myTrade's analysis, chart signals, and backtesting run locally using Python and deterministic indicator logic. No OpenAI SDK, API key, or recurring AI API charge is required.


### Supported market focus and trade review

The instrument catalogue is restricted to **NIFTY 50**, **MIDCPNIFTY**, **BANKNIFTY**, and **MCX commodities**. Instrument tokens, expiries, and lot sizes are read from Angel One's current instrument master. You can choose a date range and download historical candles directly, or reuse chart history that has already been saved locally.

Live chart candles are appended to `data/live/<exchange>/<token>/<interval>.parquet`. New live crossover calls are written to `data/mytrade_journal.sqlite3`, then classified after three completed candles as **PASSED**, **FAILED**, or **FLAT**. The Prediction Review tab shows the outcome and asks **“Why did I fail?”** and **“Why did I pass?”** so the explanation and next-step note remain with that call. The journal is local and persists between launches.

### Simulated deposit and lots

Backtests accept a simulated deposit of ₹10,000, ₹50,000, ₹1,00,000, or a custom amount. Quantity is calculated as:

`unit multiplier × number of lots × units per lot`

For a selected Angel One contract, units per lot default to the current instrument-master lot size. The editable brokerage assumption defaults to ₹20 per executed order for F&O and commodity trades, consistent with [Angel One's published pricing](https://www.angelone.in/exchange-transaction-charges). You can enter an additional per-lot/per-order cost and slippage. The current simulation does not calculate statutory taxes, exchange transaction fees, or margin requirements automatically; check the broker calculator and adjust the cost inputs before comparing results with a contract note.

## Predicted candle accuracy and gamma highlighting

The **Candle accuracy** tab measures an experimental rolling-drift next-interval
OHLC forecast. It reports close MAE, RMSE, MAPE, direction match, and the percentage
of closes within a saved tolerance (live default: 0.1%). A previous-close
baseline is scored against the same targets. A high direction-match percentage
does not mean predicted candle prices are accurate or trades profitable.

Live forecasts use completed candles only and are saved once per instrument,
interval, origin, and model. Refreshes cannot revise them. A target is scored
only when its exact interval timestamp is present among completed candles.
Missing target bars remain pending; a later bar cannot substitute for them.
Forecast records include capture time: initial connection can capture a forecast
during a forming target candle, so these records are not all pre-open forecasts.
No historical forecasts are backfilled into the live journal.

Historical OHLC replay separately runs causal rolling forecasts. It excludes
missing-bar and session gaps and exposes predicted/actual OHLC plus price errors.
These simulated results are not evidence of prospective live performance.
Forecasts and existing EMA directional calls are separate models.

**High gamma exposure** is option gamma × lot size × position lots. Gold table
rows and gold chart diamonds identify values at or above the adjustable sidebar
threshold. This is unsigned Greek exposure, not a profit multiplier or confidence
score. Compare contracts on the same underlying and using the same gamma units.
The current live feed does not supply gamma automatically.

For an option contract, save a manual current broker gamma snapshot in the live
tab. It expires after five minutes and is cleared when connecting a new contract.
Each saved forecast or crossover retains the gamma snapshot and source that was
available at capture time. Missing/expired gamma stays unknown and is not coloured.
Backtest CSVs may include an `option_gamma` column captured at each signal candle;
trade exposure uses the chosen simulation lot size and lots. Accuracy replay CSVs
can also include `lot_size` and `lots` (default 1). Never apply current gamma to
historical candles. The threshold is a user setting, not a recommended trade.

Run the regression suite:

```bash
python -m pytest -q
streamlit run app/dashboard.py --server.address 127.0.0.1
```

## Permanent option history (including expired contracts)

The Python dashboard's **Contract history** tab stores a separate durable SQLite
archive at `MYTRADE_DATA_DIR/archive/history.sqlite3` (default
`data/archive/history.sqlite3`). Expiry and removal from Angel One's current
instrument master never delete archived candles. Contract identity includes
exchange, symbol, expiry, original strike and instrument type, so recycled broker
tokens cannot mix different expiries. Original metadata, historical lot size,
data source and capture time are retained. There is no automatic expiry cleanup.

- Use **Refresh current broker catalogue** before selecting a new live contract.
  Connecting a live contract and downloading history automatically archive the
  completed candles retrieved. Live tick-derived completed bars are archived
  while the dashboard runs, marked as partial reconstruction with unknown volume.
  They do not replace better broker/import candles.
- Overlapping downloads merge by contract/interval/timestamp. Empty responses do
  not erase history; changed observations retain their earlier values in a revision
  table. Forming candles are excluded from the permanent archive.
- Use **Backtesting → Permanent contract archive** without a broker login or a
  current instrument catalogue. Historical lot size comes from saved metadata.
  **Contract history → Measure archived candle accuracy** runs historical replay.
- Import old OHLC CSV plus original contract JSON in **Contract history**. Optional
  OI, IV and Greeks columns remain as supplied; a candle API does not magically
  recover unavailable Greeks. Naive timestamps are interpreted as India time.
- Export CSV and metadata JSON for reuse, or download a complete SQLite backup.
  Store backups on a separate disk. With the app stopped, restore by copying the
  backup to `MYTRADE_DATA_DIR/archive/history.sqlite3` (keep the current file first).
  The backup covers this history archive, not the separate prediction journal.

Example metadata JSON (replace all values with the original contract's values;
`strike` retains the source's units rather than guessing a conversion):

```json
{"exch_seg":"NFO","token":"ORIGINAL_TOKEN","symbol":"ORIGINAL_OPTION_SYMBOL_PE",
 "name":"NIFTY","expiry":"2026-10-27","strike":"ORIGINAL_STRIKE_VALUE",
 "lotsize":"65","instrumenttype":"OPTIDX"}
```

Offline Bash commands:

```bash
python main.py history list
python main.py history import --input old_option.csv --contract-json contract.json --interval FIVE_MINUTE --source "Original broker CSV"
python main.py history export --contract-id CONTRACT_ID_FROM_LIST --interval FIVE_MINUTE --output exports/expired_option.csv
python main.py backtest --input exports/expired_option.csv --lot-size HISTORICAL_LOT_SIZE
python main.py history backup --output backups/history-2026-10-07.sqlite3
```

Existing token-only Parquet files are preserved, but cannot be assigned safely to
an expired contract from today's potentially recycled token. Import them with
verified original metadata (convert to CSV first). New Parquet paths use the
contract identity instead of only the token.

This collects selected contracts, not every option on the exchange. The app must
be running and connected to capture live data, or candles must be downloaded
before the provider removes them. Data already removed and never saved requires
an available external historical file/source. Local disk loss is still possible,
so keep independent backups. Archived datasets support future research and model
validation; this change does not automatically retrain a model or promise improved
predictions. The separately hosted Candle Lab is not synchronized with this local
Python archive; exported CSVs can be loaded there.


## Gamma Investigation Lab (read-only; no history archiving)

The **Gamma-only** feature branch `feature/gamma-investigation-no-archive` adds a dedicated Streamlit **Gamma Investigation** screen accessible from `python -m streamlit run app/dashboard.py`. It reads Upstox Basic option-chain snapshots on demand via the free read-only Analytics Token to inspect NIFTY CE/PE Gamma, Delta, IV, Theta, liquidity and OI for matching exact strikes and expiries. The browser session optionally compares two in-memory same-contract snapshots. No JEV AI, external decision model or live order placement is used. This feature performs **no market-history archiving or automatic backup**; existing legacy tabs may still offer older history features and are unchanged.

A separate optional CSV can be uploaded in the browser to investigate one actual, fixed-contract NIFTY option and retrospectively label 2×/3×/5×/10× future-minute-close movements. The repeated-case study uses ≥2× as its entry threshold and matches controls with <2× observed movement. Outcomes are **NOT tradable profit or confirmed Gamma causality**. They require many verified contracts, independent non-event baselines and realistic execution assumptions to become credible. Read [Gamma Investigation Setup](docs/GAMMA_INVESTIGATION_NO_ARCHIVE.md).

Set `UPSTOX_ANALYTICS_TOKEN` only in the local private `.env` (never GitHub or chat), and rotate any token previously shared externally. No broker login is needed for offline CSV investigation.


### One-command Upstox Basic Gamma smoke test (no history storage)

From the repository root: `python -m app.broker.upstox_chain_cli` is a network-free dry run. To fetch exactly one current-week NIFTY Option Chain response after setting your private `UPSTOX_ANALYTICS_TOKEN`, run `python -m app.broker.upstox_chain_cli --expiry current_week --execute`. It shows only up to 6 *investigation candidates* with Gamma, Delta, IV, OI, volume and spread. It does **not** claim a future multiplier and does not write candles, CSV, Parquet or any broker data to storage.
