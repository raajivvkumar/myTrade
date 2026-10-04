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

## Cloudflare Callback

The trading application itself will remain local.

If Angel One requires an HTTPS redirect/callback during application registration, the project may use:

```text
https://mytrade.halovialabs.com/angel/callback
```

A minimal Cloudflare Worker can serve that endpoint. Trading logic, historical data, ML models and credentials will remain on the local machine.

## Safety Principles

- no automatic live trading during initial development
- backtest before paper trading
- paper trade before real-money execution
- no strategy should bypass the risk manager
- always enforce position-size and loss limits
- keep broker execution separate from prediction logic
- treat model confidence as probabilistic, not guaranteed

## Current Status

Python foundation, Angel One authentication and historical-data ingestion, deterministic backtesting, and a local live-signal/dashboard workflow are in place. Use the setup steps above to run the local app.


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
