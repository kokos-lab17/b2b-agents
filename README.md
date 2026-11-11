# Token-Efficient Autonomous Multi-Agent B2B Procurement System

An enterprise B2B procurement and negotiation platform where an autonomous AI buyer negotiates multi-variable commercial contracts against three independent seller agent personas, evaluating trade-offs and settling transactions through PayPal Sandbox.

---

## 1. System Architecture

The system coordinates four logically independent agents operating under strict token and call envelopes:

```
                          ┌──────────────────────────┐
                          │   Autonomous Buyer AI    │
                          │   (Strategy Evaluation)  │
                          └─────────────┬────────────┘
                                        │ Multi-Variable Proposals
         ┌──────────────────────────────┼──────────────────────────────┐
         ▼                              ▼                              ▼
┌──────────────────┐          ┌───────────────────┐          ┌────────────────────┐
│ Seller A: Apex   │          │ Seller B: Sterling│          │ Seller C: Vanguard │
│ "Hard Bargainer" │          │  "Value Defender" │          │ "Strategic Flex"   │
│ (Volume/Commit)  │          │  (Warranty/Specs) │          │ (Logistics/Terms)  │
└────────┬─────────┘          └─────────┬─────────┘          └─────────┬──────────┘
         │                              │                              │
         └──────────────────────────────┼──────────────────────────────┘
                                        ▼
                      ┌────────────────────────────────────┐
                      │ Deterministic Commercial Evaluator │
                      │  (Price, Lead Time, Warranty, etc) │
                      └─────────────────┬──────────────────┘
                                        ▼
                      ┌────────────────────────────────────┐
                      │    PayPal Sandbox REST Settle      │
                      │    (Human Approval & Capture)      │
                      └────────────────────────────────────┘
```

### The 4 Agent Personas

1. **Buyer Agent (`The Procurement Director`):**
   * Dynamically tracks strategy effectiveness across rounds.
   * Autonomously recognises seller resistance and switches tactics (e.g. pivoting from price discounting to warranty extension, waived freight, or Net 60 payment terms).
   * Balances total commercial value over pure cheapest price.
2. **Seller A — Apex Industrial Corp (`The Hard Bargainer`):**
   * Values large quantities and volume commitments.
   * Firm pushback on opening bids; concedes price only when volume or repeat commitments are demonstrated.
3. **Seller B — Sterling Quality Works (`The Value Defender`):**
   * Defends gross margin and engineering quality.
   * Strongly resists unit price cuts; readily concedes extended warranties (up to 36 months) and QA inspection certifications.
4. **Seller C — Vanguard Logistics & Supply (`The Strategic Flexible Negotiator`):**
   * Focuses on capacity planning, predictable delivery, and inventory turnover.
   * Offers creative trade-offs: expedited 5-day delivery, free freight shipping, and Net 60 payment terms.

---

## 2. Key Features

* **Autonomous Adaptive Strategy Switching:** The buyer dynamically monitors diminishing returns on price negotiations and autonomously shifts focus to non-price variables.
* **Multi-Variable Commercial Contracts:** Structured negotiations encompass unit price, total price, order quantity, lead time, warranty duration, freight cost, and payment terms (Net 30, Net 60, Due on Delivery, Prepaid).
* **Deterministic Commercial Scoring:** 6-factor evaluation model (Price 40%, Delivery 15%, Warranty 15%, Shipping 10%, Payment Terms 10%, Supplier Reliability 10%). A slightly higher priced offer can win if it provides superior commercial terms.
* **Persistent Supplier Memory:** SQLite database tracks attempted negotiations, accepted deals, and captured payments separately. Unpaid negotiations are never marked as paid.
* **Real PayPal Sandbox Integration:** Server-side PayPal REST client creates orders with intent `CAPTURE`, returns real approval URLs, and captures payments upon human confirmation.
* **Deterministic Demo Mode:** Complete simulation of the multi-agent negotiation, strategy switches, and commercial scoring that operates with zero API keys and zero financial risk.
* **Token & Call Efficiency (Feature 9):**
  * Maximum 12 model calls per session.
  * Maximum 300 output tokens per call.
  * 15,000 session token budget.
  * Structured context summaries: agents never resend entire historical chat transcripts.
  * Exponential backoff with jitter on HTTP 429 rate limits.

---

## 3. Local Installation & Setup

### Prerequisites

* Python 3.11+ (Python 3.12 recommended)
* Node.js 18+ and npm

### Backend Setup

1. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # macOS/Linux:
   source .venv/bin/activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure environment variables:
   ```bash
   cp .env.example .env
   ```
   Add your `GROQ_API_KEY`, `PAYPAL_CLIENT_ID`, and `PAYPAL_CLIENT_SECRET`.

4. Start the backend:
   ```bash
   uvicorn main:app --reload --port 8000
   ```

### Frontend Setup

1. Navigate to the frontend directory and install packages:
   ```bash
   cd frontend
   npm install
   ```

2. Start the development server:
   ```bash
   npm run dev
   ```
   Open `http://localhost:3000` in your browser.

---

## 4. Automated Testing

Run the automated test suite covering agent personas, adaptive strategy switching, commercial scoring, SQLite memory, PayPal integration, and token budgeting:

```bash
python -m unittest discover -s tests -v
```

To run frontend production build checks:
```bash
cd frontend
npm run build
```

---

## 5. Render Deployment Instructions

### Backend (Web Service)

* **Environment:** Python
* **Build Command:** `pip install -r requirements.txt`
* **Start Command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
* **Environment Variables:**
  * `GROQ_API_KEY`: Your Groq API key
  * `GROQ_MODEL`: `llama-3.3-70b-versatile`
  * `PAYPAL_CLIENT_ID`: Your PayPal Sandbox Client ID
  * `PAYPAL_CLIENT_SECRET`: Your PayPal Sandbox Secret
  * `PAYPAL_BASE_URL`: `https://api-m.sandbox.paypal.com`
  * `FRONTEND_ALLOWED_ORIGIN`: Your deployed frontend domain (or `*`)
  * `DATABASE_PATH`: `/var/data/procurement_memory.db` (if persistent disk attached) or `procurement_memory.db`
* **Persistent Disk (Optional but Recommended):**
  * Mount a disk at `/var/data` on Render so that the SQLite supplier memory persists across deployments and restarts.
