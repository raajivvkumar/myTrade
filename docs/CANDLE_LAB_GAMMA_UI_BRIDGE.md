# Original MyTrade Candle Lab: Gamma Investigation bridge (no UI replacement)

## Why this exists

The original **MyTrade Candle Lab** is a custom-designed, independently
hosted interactive website with experiment setup, research workspace,
predicted-vs-actual candlestick charts, candle inspector and backtesting.
It is **NOT** the same app as Python/Streamlit \`app/dashboard.py\`.

The user explicitly confirmed the original custom UI should be retained.
Do NOT instruct them to open Python Streamlit and call that "the restored
MyTrade site." Do NOT change the original pages/navigation/chart behaviors.

This PR adds \`app/research/candle_lab_bridge.py\` to format existing
Python Gamma research outputs into a safe, plain JSON schema for eventual
integration as one additional card/tab **inside the existing Candle Lab**.

No production site UI is edited or deployed by this PR. Its source is not
in this GitHub repository; a separate Sites project owns that frontend.
Integration requires access to the actual Sites source and its hosting
configuration, and must preserve the original design and UX.

## Read-only payload design

\`build_live_gamma_panel(raw_chain, retrieved_at=..., previous_chain=...)\`
takes an ALREADY AUTHORIZED Upstox Option Chain response in a TRUSTED
server process. It validates true NIFTY fixed instrument identities and
prepares:
- Exact expiry/strike/CE-PE/instrument_key
- Gamma, Delta, IV, Theta, underlying, option premium, OI, volume, bid/ask
- Missing-quote and missing-Greek reasons; never substitute zero for missing
- Within-expiry gamma relative screen as an INVESTIGATION CANDIDATE only
- Changes between two snapshots for the **same exact contract**
- Source retrieved time explicitly NOT labelled quote exchange timestamp
- \`trade_signal: null\`, \`multiplier_probability: null\`,
  \`order_allowed: false\` always

\`build_event_study_panel(report, source_provenance=...)\` returns ONLY
metadata/status while historical source is unverified, even if old CSV
contains hypothetical 2x/3x/5x/10x event labels. Even independently verified
minute data would only allow retrospective descriptive event counts,
never predictive probabilities or BUY/SELL.

**Never call the Upstox API directly from JavaScript/browser/Sites and
never put a broker API token in a public VITE_* environment variable.**
An eventual endpoint must sit behind the user's private trusted backend,
with server-side secrets, Cloudflare Access or equivalent authorization,
explicit origin permissions, anti-cache headers and GET-only market-data
routes. The Site UI should consume only allow-listed JSON, no credentials.
No new endpoint or domain has been exposed by this change.

## UX integration intent

Keep existing original Candle Lab layout untouched. When the actual
Sites source is accessible, add an OPTIONAL side-panel/tab called
"Gamma Investigation" to the same Research Workspace. Default stays
existing NIFTY candle chart and backtest. If Gamma input is missing,
show **Insufficient contract evidence**; if only old NSE daily bhavcopy
is present, show **Daily corroboration only — minute Gamma unknown**.
Render contract candidates as INVESTIGATE, not trades. Show source,
timestamp confidence and IV/quote missingness. No JEV, Dhan paid API,
auto-trading or automatic local data archive.

## Offline verification

    python -m pytest -q tests/test_gamma_site_bridge.py

No Upstox calls, website deployments, order placement or disk writes.
All tests use deterministic synthetic option-chain responses. The
Site's original browser UI is not affected until a separate authorized
frontend integration is performed.
