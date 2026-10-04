# Cloudflare Tunnel for the local myTrade dashboard

The Python dashboard and Angel One session remain on the user's PC. This tunnel gives the local Streamlit UI an HTTPS hostname without moving broker credentials or strategy logic into a Worker.

## Protect the hostname first

Before creating the public DNS route, open Cloudflare Zero Trust and create a self-hosted Access application for:

\`mytrade.halovialabs.com\`

Add an **Allow** policy for only your own verified email address, using a one-time PIN or your configured identity provider. Do not use an Everyone/Bypass policy. Keep Access enabled before and after the DNS route is created.

Cloudflare Tunnel publishes a route from the Cloudflare edge to the local service; Access is the login gate in front of it. Keep the dashboard bound to \`127.0.0.1\`.

## Create the named tunnel

Install the official \`cloudflared\` client, then sign in to the Cloudflare account that owns \`halovialabs.com\`. Run:

\`\`\`bash
cloudflared tunnel login
cloudflared tunnel create mytrade-local
cloudflared tunnel route dns mytrade-local mytrade.halovialabs.com
\`\`\`

Copy the returned tunnel UUID and local credentials JSON path into a local copy of \`cloudflare/config.yml.example\`, saved as \`cloudflare/config.yml\`. Do not commit the local config or credentials file.

## Run myTrade locally

In one Bash terminal from the repository root:

\`\`\`bash
python -m pip install -r requirements.txt
streamlit run app/dashboard.py --server.address 127.0.0.1 --server.port 8501
\`\`\`

In a second Bash terminal:

\`\`\`bash
cloudflared tunnel --config cloudflare/config.yml run
\`\`\`

Open \`https://mytrade.halovialabs.com\` and sign in through Cloudflare Access. The PC and both processes must stay running while you use the hostname.

This is a private access tunnel to the local dashboard, not a public trading API. The dashboard does not place broker orders. Never put Angel One credentials in Cloudflare Worker variables or the repository.
