# MyTrade Gamma Investigation — no local history storage

This branch deliberately does **not** download, persist or back up market-history data. The previous local Parquet/SQLite archive proposals live in a separate Draft PR and remain untouched. This Gamma-only branch starts from main and focuses on two testable investigative workflows.

## Launch in Windows Git Bash

    cd /d/RaajivvProject/myTrade
    git fetch origin
    git switch --track origin/feature/gamma-investigation-no-archive
    python -m pip install -r requirements.txt
    python -m streamlit run app/dashboard.py

Open **Gamma Investigation** in the Streamlit sidebar. If that page doesn't appear, navigate from Streamlit's page menu. No new data folders are created by this feature.

### Live Gamma chain (Upstox Basic account)

1. Revoke a token previously pasted into a chat or other unsafe place and generate a NEW free, read-only Analytics Token at https://account.upstox.com/developer/apps (Analytics tab).
2. Keep it ONLY in the project's local .env as UPSTOX_ANALYTICS_TOKEN=...
3. Choose Upstox relative expiry current_week/next_week/current_month etc., then click **Fetch current option chain**.
4. Review exact NIFTY CE/PE strike+expiry+instrument key with real data: Gamma, Delta, IV, Theta, OI, volume, LTP, bid/ask, spread percent and scaled Gamma sensitivity.
5. Click Fetch again later in the SAME browser session to compare the two snapshots for identical instrument identities; there is no automatic refreshing and no disk storage. Close session or use Clear to discard snapshots.

Upstox official API: https://upstox.com/developer/api-documentation/get-pc-option-chain/
Analytics Token access: https://upstox.com/developer/api-documentation/analytics-token/

Important limitations: The option-chain endpoint is a live snapshot, not continuous 1-minute historical Greeks. Gamma × spot × 0.01 estimates the LOCAL change in delta from a 1% underlying price move, not a 5× premium prediction. Relative Gamma 75th percentile and coarse quote/volume/OI/Delta filters are investigation aids, not empirically optimized signals. If Upstox returns missing Greeks or invalid quotes, the tool marks them missing / unsuitable rather than inventing data. OI increase alone is not proof of directional intent.

### Retrospective fingerprint pilot (optional upload only)

Upload a CSV with exactly ONE actual NIFTY option contract identified by instrument_key, strike_price, option_type CE/PE and expiry, 1-minute timestamp+open+high+low+close+volume+oi. Optional gamma/delta/iv/theta must be historical values actually seen at those minutes. No fake metadata or changing ATM strikes allowed.

The in-memory engine computes at each candidate minute t:
- Completed 60-minute no-gap history (for eligibility)
- Mean minute volume over last five versus preceding 30, plus OI delta over 5 min
- Current Gamma and 5-minute Gamma difference only if actual historical Greek series exists
- Previous five-minute premium return (not future)
- Hypothetical next-minute OPEN entry, then max next-horizon minute CLOSE over complete forward window within same session
- Retrospective ≥3×/5×/10× close-multiple labels, and simple rates for volume acceleration ≥2× versus other eligible windows

These labels are descriptive opportunity upper bounds. The future best close is NOT known at time t, does not prove actual fill, is not fair value, cannot distinguish gamma from IV/delta/theta, and has severe overlapping-window dependency. Need many independent expiries and holdout testing to establish any useful early warning. No writes happen; a manual download CSV button is provided.

### Why expired option data still matters

Upstox Plus-only expired-instruments API can retrieve actual past fixed-strike contracts and OHLC, but Basic does not guarantee access. Do not infer actual historical Gamma or option 10× frequency from NIFTY index OHLC or a current option-chain snapshot. Alternative legitimately sourced contract CSVs can be investigated in memory without changing account plan.

Official: https://upstox.com/developer/api-documentation/get-expired-historical-candle-data/

This UI never places orders, schedules trading, saves a broker data archive, creates Google Drive files or uploads token/market data to ChatGPT.
