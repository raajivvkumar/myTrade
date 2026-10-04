# myTrade Local Setup

myTrade is designed to run locally on Windows. The GitHub repository stores code only; market data, credentials, tokens and trained models stay on the local machine.

## 1. Clone and create the Python environment

Use Git Bash:

```bash
git clone https://github.com/raajivvkumar/myTrade.git
cd myTrade

python -m venv .venv
source .venv/Scripts/activate

python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 2. Create local environment configuration

```bash
cp .env.example .env
```

Edit `.env` locally and enter your own values:

```env
ANGEL_API_KEY=
ANGEL_CLIENT_CODE=
ANGEL_PIN=
ANGEL_TOTP_SECRET=

OPENAI_API_KEY=
OPENAI_MODEL=gpt-5

MYTRADE_DATA_DIR=data
```

Never commit `.env`.

## 3. Check project status

```bash
python main.py status
```

This reports whether local configuration exists without printing credential values.

## 4. Search Angel One instruments

Refresh Angel One's public instrument master and search for NIFTY:

```bash
python main.py find-instrument "NIFTY" --exchange NSE --refresh
```

Search for BANKNIFTY:

```bash
python main.py find-instrument "BANKNIFTY" --exchange NSE
```

Use the returned `token` for historical-data requests rather than permanently hard-coding instrument tokens.

## 5. Verify Angel One market-data login

After filling the Angel One fields in `.env`:

```bash
python main.py angel-login
```

The program only reports success/failure. Session and feed-token values are not printed.

## 6. Download historical candles

First search the instrument and copy the required token. Then run:

```bash
python main.py download \
  --exchange NSE \
  --token <TOKEN> \
  --interval FIVE_MINUTE \
  --from "2026-10-01 09:15" \
  --to "2026-10-01 15:30"
```

The default output is stored locally under:

```text
data/raw/<EXCHANGE>/<TOKEN>/<INTERVAL>.parquet
```

The `data/` directory is excluded from Git.

Supported initial intervals are:

```text
ONE_MINUTE
THREE_MINUTE
FIVE_MINUTE
TEN_MINUTE
FIFTEEN_MINUTE
THIRTY_MINUTE
ONE_HOUR
ONE_DAY
```

## 7. Optional OpenAI connectivity test

OpenAI is not required for Angel One data collection. It is an optional explanation layer.

After setting `OPENAI_API_KEY` locally:

```bash
python main.py openai-test
```

The OpenAI layer will be used to explain structured signals and model output. It will not be used to place orders or bypass deterministic risk controls.

## 8. Run tests

```bash
pytest -q
```

## Current implementation

Implemented:

- Python project foundation
- secure `.env` template and Git exclusions
- Angel One instrument-master download/cache/search
- Angel One local market-data authentication
- historical candle retrieval
- Parquet candle storage
- initial CLI
- optional OpenAI Responses API explanation wrapper
- initial automated test coverage

Next:

- Angel WebSocket V2 live feed
- live tick normalization
- 1-minute / 5-minute candle builder
- retry/reconnect handling
- feature-engine foundation
