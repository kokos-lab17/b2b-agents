'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { AllCommunityModule, ModuleRegistry } from 'ag-grid-community';
import { AgGridReact } from 'ag-grid-react';

ModuleRegistry.registerModules([AllCommunityModule]);

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL || 'https://b2b-agents.onrender.com';

type CommercialTerms = {
  unit_price: number;
  total_price: number;
  quantity: number;
  delivery_days: number;
  warranty_months: number;
  shipping_cost: number;
  payment_terms: string;
  special_concessions?: string[];
};

type ScoreBreakdown = {
  total_score: number;
  price_score: number;
  delivery_score: number;
  warranty_score: number;
  shipping_score: number;
  terms_score: number;
  reliability_score: number;
  explanation: string;
};

type Deal = {
  selected_vendor: string;
  commercial_score: number;
  score_breakdown: ScoreBreakdown;
  terms: CommercialTerms;
};

type TokenUsage = {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  total_calls?: number;
  is_demo?: boolean;
};

type EventRow = {
  step: number | null;
  agent: string;
  message: string;
  strategy?: string | null;
  offer: number | null;
  status: string;
  details?: Record<string, any> | null;
};

type Vendor = {
  name: string;
  archetype?: string;
  style?: string;
  specialty?: string;
  completed_deals?: number;
  attempted_deals?: number;
  reliability_score?: number;
};

type StatusResponse = {
  events?: EventRow[] | null;
  order_id?: string | null;
  approve_url?: string | null;
  status?: string | null;
  vendors?: Vendor[] | null;
  deal?: Deal | null;
  token_usage?: TokenUsage | null;
};

// Frontend phases; backend terminal states map onto these.
type Phase = 'idle' | 'running' | 'awaiting_approval' | 'completed' | 'failed' | 'no_agreement';

const TERMINAL_EVENTS: Record<string, Exclude<Phase, 'idle' | 'running' | 'awaiting_approval'>> = {
  captured: 'completed',
  completed: 'completed',
  succeeded: 'completed',
  error: 'failed',
  failed: 'failed',
  no_agreement: 'no_agreement',
};

function mapBackendStatus(status: string | null | undefined): Phase | null {
  if (!status) return null;
  const s = status.toLowerCase();
  if (s === 'awaiting_approval') return 'awaiting_approval';
  return TERMINAL_EVENTS[s] || null;
}

type ColDef = import('ag-grid-community').ColDef;

const STATUS_STYLES: Record<string, string> = {
  negotiating: 'text-orange-400 bg-orange-400/10 border border-orange-400/30',
  awaiting_approval: 'text-yellow-300 bg-yellow-400/10 border border-yellow-400/30',
  deal_selected: 'text-cyan-300 bg-cyan-400/10 border border-cyan-400/30',
  captured: 'text-green-400 bg-green-400/10 border border-green-400/30',
  completed: 'text-green-400 bg-green-400/10 border border-green-400/30',
  success: 'text-blue-300 bg-blue-400/10 border border-blue-400/30',
  error: 'text-red-400 bg-red-400/10 border border-red-400/30',
  failed: 'text-red-400 bg-red-400/10 border border-red-400/30',
};

function StatusCell(props: { value?: string }) {
  const status = props.value || '';
  const cls = STATUS_STYLES[status];
  if (!cls) {
    return <span className="text-gray-400">{status || '—'}</span>;
  }
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${cls}`}>
      {status.replace(/_/g, ' ')}
    </span>
  );
}

function StrategyCell(props: { value?: string | null }) {
  const strategy = props.value;
  if (!strategy) {
    return <span className="text-gray-500">—</span>;
  }
  return (
    <span className="inline-block rounded border border-purple-500/40 bg-purple-500/10 px-2 py-0.5 text-xs font-medium text-purple-300">
      {strategy.replace(/_/g, ' ')}
    </span>
  );
}

function AgentCell(props: { value?: string }) {
  const v = props.value || '';
  const isBuyer = v.includes('Buyer');
  return (
    <span
      title={v}
      className={`block truncate font-medium ${isBuyer ? 'text-blue-400' : 'text-gray-200'}`}
      style={{ lineHeight: '34px' }}
    >
      {v}
    </span>
  );
}

function MessageCell(props: { value?: string }) {
  const v = props.value || '';
  return (
    <span title={v} className="block truncate text-gray-300" style={{ lineHeight: '34px' }}>
      {v}
    </span>
  );
}

function OfferCell(props: { value?: number | null }) {
  if (props.value === null || props.value === undefined) {
    return <span className="text-gray-500">—</span>;
  }
  return <span className="tabular-nums font-semibold text-emerald-400">${Number(props.value).toFixed(2)}</span>;
}

function StepCell(props: { value?: number | null }) {
  if (props.value === null || props.value === undefined) {
    return <span className="text-gray-500">—</span>;
  }
  return <span className="tabular-nums text-gray-400">{props.value}</span>;
}

const BUTTON_LABELS: Record<Phase, string> = {
  idle: 'Start Autonomous Procurement',
  running: 'Negotiating in Progress…',
  awaiting_approval: 'Awaiting PayPal Approval…',
  completed: 'Start New Procurement',
  failed: 'Restart Procurement',
  no_agreement: 'Restart Procurement',
};

export default function Home() {
  const [product, setProduct] = useState('');
  const [qty, setQty] = useState('');
  const [budget, setBudget] = useState('');
  const [isDemoMode, setIsDemoMode] = useState(false);
  const [phase, setPhase] = useState<Phase>('idle');
  const [error, setError] = useState('');
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [backendStatus, setBackendStatus] = useState('');
  const [rowData, setRowData] = useState<EventRow[]>([]);
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [deal, setDeal] = useState<Deal | null>(null);
  const [tokenUsage, setTokenUsage] = useState<TokenUsage | null>(null);
  const [orderId, setOrderId] = useState<string | null>(null);
  const [approveUrl, setApproveUrl] = useState<string | null>(null);
  const [captured, setCaptured] = useState(false);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollInFlightRef = useRef(false);
  const confirmingRef = useRef(false);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, []);

  const columnDefs: ColDef[] = [
    { field: 'step', headerName: '#', width: 50, cellRenderer: StepCell },
    { field: 'agent', headerName: 'Agent Persona', width: 180, cellRenderer: AgentCell },
    { field: 'strategy', headerName: 'Buyer Strategy', width: 220, cellRenderer: StrategyCell },
    {
      field: 'message',
      headerName: 'Explainable Negotiation Dialogue',
      flex: 1,
      minWidth: 320,
      cellRenderer: MessageCell,
    },
    { field: 'offer', headerName: 'Offer $', width: 100, cellRenderer: OfferCell },
    { field: 'status', headerName: 'Status', width: 140, cellRenderer: StatusCell },
  ];

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const applyStatusResponse = useCallback(
    (data: StatusResponse) => {
      const events = Array.isArray(data.events) ? data.events : [];
      const mapped: EventRow[] = events.map((e, i) => ({
        step: typeof e?.step === 'number' ? e.step : i + 1,
        agent: e?.agent ?? '',
        message: e?.message ?? '',
        strategy: e?.strategy ?? null,
        offer: e?.offer ?? null,
        status: e?.status ?? '',
        details: e?.details ?? null,
      }));
      setRowData(mapped);
      setVendors(Array.isArray(data.vendors) ? data.vendors : []);
      if (data.deal) setDeal(data.deal);
      if (data.token_usage) setTokenUsage(data.token_usage);
      setBackendStatus(data.status || '');
      if (data.order_id) setOrderId(data.order_id);
      setApproveUrl(data.approve_url || null);

      const backendPhase = mapBackendStatus(data.status);
      if (backendPhase === 'awaiting_approval') {
        if (!confirmingRef.current) {
          setPhase('awaiting_approval');
        }
        return;
      }
      const erroredEvent = mapped.find((e) => e.status === 'error' || e.status === 'failed');
      if (backendPhase === 'failed' && erroredEvent) {
        setError(erroredEvent.message || 'The procurement run failed.');
      }
      if (backendPhase) {
        stopPolling();
        if (backendPhase === 'completed') {
          confirmingRef.current = false;
          setCaptured(true);
        }
        setPhase(backendPhase);
        return;
      }
      if (erroredEvent && !confirmingRef.current) {
        stopPolling();
        setPhase('failed');
        setError(erroredEvent.message || 'The procurement run failed.');
      }
    },
    [stopPolling]
  );

  const pollStatus = useCallback(async () => {
    if (pollInFlightRef.current) return;
    pollInFlightRef.current = true;
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 15000);
      try {
        const res = await fetch(`${BACKEND_URL}/api/status`, {
          cache: 'no-store',
          signal: controller.signal,
        });
        if (!res.ok) return;
        const data = (await res.json()) as StatusResponse;
        if (!data || typeof data !== 'object') return;
        if (mountedRef.current) applyStatusResponse(data);
      } finally {
        clearTimeout(timeout);
      }
    } catch {
      // Keep polling on transient errors
    } finally {
      pollInFlightRef.current = false;
    }
  }, [applyStatusResponse]);

  const startPolling = useCallback(() => {
    stopPolling();
    pollStatus();
    pollRef.current = setInterval(pollStatus, 2000);
  }, [pollStatus, stopPolling]);

  const validateForm = (): string[] => {
    const errs: string[] = [];
    if (!product.trim()) {
      errs.push('Product name is required.');
    }
    const qtyNum = Number(qty);
    if (!qty.trim() || !Number.isInteger(qtyNum) || qtyNum <= 0) {
      errs.push('Quantity must be a positive whole number.');
    }
    const budgetNum = Number(budget);
    if (!budget.trim() || Number.isNaN(budgetNum) || budgetNum <= 0) {
      errs.push('Budget cap must be a positive number.');
    }
    return errs;
  };

  const startProcurement = async () => {
    setError('');
    const errs = validateForm();
    setValidationErrors(errs);
    if (errs.length > 0) return;

    let ok = false;
    let failure = 'Could not start the procurement run. Is the backend online?';
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 30000);
      try {
        const res = await fetch(`${BACKEND_URL}/api/start`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            product: product.trim(),
            qty: Math.floor(Number(qty)),
            budget: Number(budget),
            demo_mode: isDemoMode,
          }),
          signal: controller.signal,
        });
        if (!res.ok) {
          failure = `The backend rejected the request (status ${res.status}).`;
        } else {
          ok = true;
        }
      } finally {
        clearTimeout(timeout);
      }
    } catch {
      // Network failure
    }

    if (!ok) {
      setError(failure);
      setPhase('failed');
      return;
    }

    setCaptured(false);
    setRowData([]);
    setVendors([]);
    setDeal(null);
    setTokenUsage(null);
    setOrderId(null);
    setApproveUrl(null);
    confirmingRef.current = false;
    setPhase('running');
    startPolling();
  };

  const openApprove = (url: string) => {
    window.open(url, '_blank', 'noopener,noreferrer');
  };

  const confirmPayment = async () => {
    if (!orderId) {
      setError('No PayPal order to confirm yet.');
      return;
    }
    confirmingRef.current = true;
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 30000);
      try {
        const res = await fetch(
          `${BACKEND_URL}/api/confirm-payment?token=${encodeURIComponent(orderId)}`,
          { signal: controller.signal }
        );
        if (!res.ok) {
          confirmingRef.current = false;
          setError('Payment confirmation failed. Please try again.');
          return;
        }
        setPhase('running');
        startPolling();
      } finally {
        clearTimeout(timeout);
      }
    } catch {
      confirmingRef.current = false;
      setError('Payment confirmation failed. Please try again.');
    }
  };

  const running = phase === 'running';
  const canSubmit = !running && phase !== 'awaiting_approval';

  return (
    <main className="mx-auto min-h-screen max-w-7xl bg-gray-950 px-4 py-8 text-white sm:px-8">
      {/* Header */}
      <header className="mb-6 flex flex-col justify-between gap-4 border-b border-gray-800 pb-5 sm:flex-row sm:items-center">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight text-white">
            Autonomous B2B Procurement Engine
          </h1>
          <p className="mt-1 text-sm text-gray-400">
            One autonomous AI buyer negotiating adaptively across multi-variables with three independent seller personas.
          </p>
        </div>

        {/* Mode Selector */}
        <div className="flex items-center gap-2 rounded-lg border border-gray-800 bg-gray-900/80 p-1.5 text-xs">
          <button
            type="button"
            onClick={() => setIsDemoMode(false)}
            className={`rounded px-3 py-1.5 font-medium transition-colors ${
              !isDemoMode
                ? 'bg-blue-600 text-white shadow-sm'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            Live AI Mode (Groq)
          </button>
          <button
            type="button"
            onClick={() => setIsDemoMode(true)}
            className={`rounded px-3 py-1.5 font-medium transition-colors ${
              isDemoMode
                ? 'bg-purple-600 text-white shadow-sm'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            Deterministic Demo Mode
          </button>
        </div>
      </header>

      {/* Mode Banner */}
      {isDemoMode && (
        <div className="mb-6 flex items-center justify-between rounded-lg border border-purple-500/40 bg-purple-950/20 px-4 py-2.5 text-xs font-medium text-purple-300">
          <span>
            <strong>DEMO SIMULATION ACTIVE:</strong> Deterministic rule-based autonomous strategy switching without requiring Groq API keys or PayPal funds.
          </span>
          <span className="rounded bg-purple-500/20 px-2 py-0.5 text-[10px] uppercase font-bold tracking-wider text-purple-200">
            Simulation
          </span>
        </div>
      )}

      {/* Input Section */}
      <section className="rounded-xl border border-gray-800 bg-gray-900/60 p-5 shadow-lg">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div>
            <label
              htmlFor="product"
              className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-gray-400"
            >
              Procurement Product
            </label>
            <input
              id="product"
              type="text"
              placeholder="e.g. Industrial Hydraulic Valves"
              value={product}
              onChange={(e) => setProduct(e.target.value)}
              className="w-full rounded-md border border-gray-700 bg-gray-950 px-3 py-2.5 text-sm text-white placeholder-gray-600 outline-none transition-colors focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
            />
          </div>
          <div>
            <label
              htmlFor="qty"
              className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-gray-400"
            >
              Order Quantity
            </label>
            <input
              id="qty"
              type="number"
              min="1"
              placeholder="500"
              value={qty}
              onChange={(e) => setQty(e.target.value)}
              className="w-full rounded-md border border-gray-700 bg-gray-950 px-3 py-2.5 text-sm text-white placeholder-gray-600 outline-none transition-colors focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
            />
          </div>
          <div>
            <label
              htmlFor="budget"
              className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-gray-400"
            >
              Budget Cap ($/unit)
            </label>
            <input
              id="budget"
              type="number"
              min="0"
              step="0.01"
              placeholder="90.00"
              value={budget}
              onChange={(e) => setBudget(e.target.value)}
              className="w-full rounded-md border border-gray-700 bg-gray-950 px-3 py-2.5 text-sm text-white placeholder-gray-600 outline-none transition-colors focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
            />
          </div>
        </div>

        <div className="mt-5 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button
              onClick={startProcurement}
              disabled={!canSubmit}
              className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-6 py-2.5 text-sm font-semibold text-white shadow-md transition-colors hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {(running || phase === 'awaiting_approval') && (
                <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
                </svg>
              )}
              {BUTTON_LABELS[phase]}
            </button>
            {error && <p className="text-sm font-medium text-red-400">{error}</p>}
          </div>

          {/* Token Usage Stats */}
          {tokenUsage && (
            <div className="hidden items-center gap-4 text-xs text-gray-400 sm:flex">
              <div>
                Calls: <span className="font-semibold text-gray-200">{tokenUsage.total_calls ?? 0}</span>
              </div>
              <div>
                Tokens: <span className="font-semibold text-gray-200">{tokenUsage.total_tokens ?? 0}</span>
              </div>
              <span className="rounded bg-gray-800 px-2 py-0.5 text-[10px] text-gray-300">
                Budget Enforced
              </span>
            </div>
          )}
        </div>

        {validationErrors.length > 0 && (
          <ul className="mt-3 space-y-1 text-sm text-red-400">
            {validationErrors.map((e) => (
              <li key={e}>• {e}</li>
            ))}
          </ul>
        )}
      </section>

      {/* Transaction Notifications */}
      {captured && (
        <div className="mt-6 flex items-center gap-2 rounded-lg border border-green-500/40 bg-green-500/10 px-4 py-3 text-sm font-semibold text-green-400 shadow-md">
          <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
          ✓ Commercial Deal Settled & Payment Captured Successfully.
        </div>
      )}

      {phase === 'failed' && !error && (
        <div className="mt-6 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm font-medium text-red-400">
          The procurement run failed. Please verify credentials or retry in Demo mode.
        </div>
      )}

      {/* Selected Deal & Commercial Scoring Breakdown Card (Feature 4 & 10) */}
      {deal && (
        <section className="mt-6 rounded-xl border border-cyan-500/40 bg-cyan-950/20 p-5 shadow-lg">
          <div className="flex flex-col justify-between gap-2 border-b border-cyan-800/40 pb-3 sm:flex-row sm:items-center">
            <div>
              <span className="rounded bg-cyan-500/20 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-cyan-300">
                Selected Commercial Winner
              </span>
              <h2 className="mt-1 text-xl font-bold text-white">{deal.selected_vendor}</h2>
            </div>
            <div className="text-right">
              <span className="text-xs text-gray-400">Commercial Evaluation Score</span>
              <div className="text-2xl font-black text-cyan-300">
                {deal.commercial_score.toFixed(1)} <span className="text-sm font-normal text-gray-400">/ 100</span>
              </div>
            </div>
          </div>

          {/* Negotiated Terms */}
          {deal.terms && (
            <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-6 text-xs">
              <div className="rounded-lg border border-gray-800 bg-gray-900/80 p-2.5">
                <span className="text-gray-400">Unit Price</span>
                <p className="mt-1 text-base font-bold text-emerald-400">${deal.terms.unit_price.toFixed(2)}</p>
              </div>
              <div className="rounded-lg border border-gray-800 bg-gray-900/80 p-2.5">
                <span className="text-gray-400">Total Order</span>
                <p className="mt-1 text-base font-bold text-white">${deal.terms.total_price.toFixed(2)}</p>
              </div>
              <div className="rounded-lg border border-gray-800 bg-gray-900/80 p-2.5">
                <span className="text-gray-400">Lead Time</span>
                <p className="mt-1 text-base font-bold text-white">{deal.terms.delivery_days} Days</p>
              </div>
              <div className="rounded-lg border border-gray-800 bg-gray-900/80 p-2.5">
                <span className="text-gray-400">Warranty</span>
                <p className="mt-1 text-base font-bold text-purple-300">{deal.terms.warranty_months} Mos</p>
              </div>
              <div className="rounded-lg border border-gray-800 bg-gray-900/80 p-2.5">
                <span className="text-gray-400">Freight</span>
                <p className="mt-1 text-base font-bold text-white">
                  {deal.terms.shipping_cost <= 0 ? 'FREE ($0)' : `$${deal.terms.shipping_cost.toFixed(2)}`}
                </p>
              </div>
              <div className="rounded-lg border border-gray-800 bg-gray-900/80 p-2.5">
                <span className="text-gray-400">Payment Terms</span>
                <p className="mt-1 text-base font-bold text-blue-300">{deal.terms.payment_terms}</p>
              </div>
            </div>
          )}

          {/* Score Factor Breakdown */}
          {deal.score_breakdown && (
            <div className="mt-4 border-t border-gray-800/80 pt-3">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-400">
                Deterministic Commercial Scoring Breakdown
              </div>
              <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-6">
                <div className="rounded bg-gray-900/60 p-2">
                  <span className="text-gray-400">Price (40 pts)</span>
                  <div className="font-semibold text-white">{deal.score_breakdown.price_score}</div>
                </div>
                <div className="rounded bg-gray-900/60 p-2">
                  <span className="text-gray-400">Delivery (15 pts)</span>
                  <div className="font-semibold text-white">{deal.score_breakdown.delivery_score}</div>
                </div>
                <div className="rounded bg-gray-900/60 p-2">
                  <span className="text-gray-400">Warranty (15 pts)</span>
                  <div className="font-semibold text-white">{deal.score_breakdown.warranty_score}</div>
                </div>
                <div className="rounded bg-gray-900/60 p-2">
                  <span className="text-gray-400">Shipping (10 pts)</span>
                  <div className="font-semibold text-white">{deal.score_breakdown.shipping_score}</div>
                </div>
                <div className="rounded bg-gray-900/60 p-2">
                  <span className="text-gray-400">Terms (10 pts)</span>
                  <div className="font-semibold text-white">{deal.score_breakdown.terms_score}</div>
                </div>
                <div className="rounded bg-gray-900/60 p-2">
                  <span className="text-gray-400">Reliability (10 pts)</span>
                  <div className="font-semibold text-white">{deal.score_breakdown.reliability_score}</div>
                </div>
              </div>
              <p className="mt-2 text-xs text-gray-300 italic">{deal.score_breakdown.explanation}</p>
            </div>
          )}
        </section>
      )}

      {/* Real-time Explainable Activity Feed (AG Grid) */}
      <section className="mt-6">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-400">
            Explainable Multi-Agent Negotiation Stream
          </h2>
          <span className="text-xs text-gray-500">Autonomous adaptive turn protocol</span>
        </div>
        <div
          className="ag-theme-alpine-dark rounded-xl border border-gray-800 shadow-md"
          style={{ height: 480, width: '100%' }}
        >
          <AgGridReact
            rowData={rowData}
            columnDefs={columnDefs}
            defaultColDef={{ sortable: false, filter: false, resizable: true }}
            getRowId={(params) => String(params.data.step ?? params.data.agent)}
            theme="legacy"
            headerHeight={44}
            rowHeight={44}
            suppressCellFocus={true}
          />
        </div>
      </section>

      {/* Supplier Personas & Persistent Memory (Feature 1 & Feature 5) */}
      {vendors.length > 0 && (
        <section className="mt-6">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-400">
              Independent Seller Personas & Persistent Memory
            </h2>
            <span className="text-xs text-gray-500">SQLite memory verified</span>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {vendors.map((v, i) => (
              <div
                key={v.name || i}
                className="rounded-lg border border-gray-800 bg-gray-900/60 p-4 text-sm shadow-sm"
              >
                <div className="flex items-center justify-between">
                  <p className="font-semibold text-white">{v.name}</p>
                  {typeof v.reliability_score === 'number' && (
                    <span className="rounded bg-blue-500/10 px-2 py-0.5 text-xs font-medium text-blue-300">
                      {(v.reliability_score * 100).toFixed(0)}% Reliability
                    </span>
                  )}
                </div>
                {v.style && (
                  <p className="mt-1 text-xs font-medium text-purple-300">
                    Style: {v.style}
                  </p>
                )}
                {v.specialty && (
                  <p className="mt-1 text-xs text-gray-400">{v.specialty}</p>
                )}
                <div className="mt-3 flex items-center gap-3 border-t border-gray-800 pt-2 text-xs text-gray-400">
                  <span>Deals: {v.completed_deals ?? 0}</span>
                  <span>•</span>
                  <span>Attempts: {v.attempted_deals ?? 0}</span>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* PayPal Sandbox Approval Box (Feature 6) */}
      {phase === 'awaiting_approval' && approveUrl && (
        <section className="mt-6">
          <div className="rounded-lg border border-yellow-400/40 bg-yellow-400/5 p-5 shadow-lg">
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-yellow-400 animate-pulse"></span>
              <h3 className="text-base font-bold text-yellow-300">PayPal Sandbox Human Authorization Required</h3>
            </div>
            {orderId && (
              <p className="mt-2 text-xs text-gray-300">
                Pending Sandbox Order ID: <span className="font-mono text-white">{orderId}</span>
              </p>
            )}
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <button
                onClick={() => openApprove(approveUrl)}
                className="rounded-md bg-green-600 px-5 py-2.5 text-sm font-semibold text-white shadow-md transition-colors hover:bg-green-500"
              >
                Approve on PayPal Sandbox
              </button>
              <button
                onClick={confirmPayment}
                disabled={!orderId}
                className="rounded-md border border-blue-500/50 bg-blue-600/20 px-5 py-2.5 text-sm font-semibold text-blue-300 shadow-md transition-colors hover:bg-blue-600/40 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Confirm Payment & Settle
              </button>
            </div>
            <p className="mt-2 text-xs text-gray-400">
              Credentials remain strictly on the backend server. Payment capture requires your approval step.
            </p>
          </div>
        </section>
      )}
    </main>
  );
}
