# Agentic B2B Commerce

Dynamic multi-vendor procurement: source three vendors, negotiate, select the best offer, then settle through PayPal sandbox after human approval.

Requires Python 3.11 or newer.

## Flow

1. You send a product name, quantity, and per-unit budget.
2. A sourcing agent returns three vendor profiles (name, floor price, negotiation style, specialty).
3. A seller agent is created for each vendor.
4. The buyer negotiates with each vendor, then compares the three results and selects the best value.
5. An order agent creates a PayPal order with intent `CAPTURE` for the selected terms.
6. You approve the order in the PayPal sandbox.
7. `GET /api/confirm-payment` captures the payment. Capture does not happen before that call.

## Setup

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in the keys:

```bash
copy .env.example .env
```

## Run

```bash
uvicorn main:app --reload --port 8000
```

Start a run:

```bash
curl -X POST http://127.0.0.1:8000/api/start -H "Content-Type: application/json" -d "{\"product\":\"Industrial Widget X\",\"qty\":500,\"budget\":90}"
```

`budget` is the maximum USD price per unit.

Poll `GET /api/status` for `events`, `vendors`, `order_id`, `approve_url`, and `status`. When `status` is `awaiting_approval`, open `approve_url` in the PayPal sandbox and approve the order. Then settle with `GET /api/confirm-payment?token=<order_id>`.

## PayPal sandbox credentials

1. Open [developer.paypal.com](https://developer.paypal.com) and log in.
2. Go to **Apps & Credentials** and switch to **Sandbox**.
3. Create an app, or open the default sandbox app.
4. Copy the **Client ID** into `PAYPAL_CLIENT_ID` and the **Secret** into `PAYPAL_CLIENT_SECRET`.

Orders are created in the sandbox. Payment is captured only after the human approval call.

## Gemini API key

1. Open [aistudio.google.com](https://aistudio.google.com).
2. Create an API key.
3. Put it in `GEMINI_API_KEY`.
