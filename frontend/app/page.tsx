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
  session_id?: string | null;
  events?: EventRow[] | null;
  order_id?: string | null;
  approve_url?: string | null;
  status?: string | null;
  vendors?: Vendor[] | null;
  deal?: Deal | null;
  negotiation_mode?: string | null;
  payment_mode?: string | null;
};

type LearnedRecommendation = {
  id: string;
  buyer_id: string;
  recommendation_type: string;
  title: string;
  description: string;
  evidence: string;
  suggested_priority: string;
  status: string;
};

type AnalyticsData = {
  overall: {
    total_sandbox_spend: number;
    sandbox_orders_count: number;
    total_simulated_spend: number;
    simulated_orders_count: number;
    total_sessions: number;
    completed_sessions: number;
    total_savings: number;
  };
  suppliers: Record<string, {
    attempted_deals: number;
    accepted_deals: number;
    captured_payments: number;
    simulated_deals: number;
    avg_deal_amount: number;
    reliability_score: number;
  }>;
  strategies: Array<{
    strategy_name: string;
    total_uses: number;
    concessions_won: number;
    avg_price_cut: number;
    deals_closed: number;
  }>;
};

type AuditEntry = {
  id: number;
  session_id: string;
  timestamp: string;
  action: string;
  actor: string;
  reason: string;
  details?: Record<string, any> | null;
};

type ActiveTab = 'procurement' | 'intelligence' | 'simulator' | 'quotes' | 'audit';

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
    return <div className="py-2.5 text-gray-400">{status || '—'}</div>;
  }
  return (
    <div className="py-2.5">
      <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${cls}`}>
        {status.replace(/_/g, ' ')}
      </span>
    </div>
  );
}

function StrategyCell(props: { value?: string | null }) {
  const strategy = props.value;
  if (!strategy) {
    return <div className="py-2.5 text-gray-500">—</div>;
  }
  return (
    <div className="py-2.5">
      <span className="inline-block rounded border border-purple-500/40 bg-purple-500/10 px-2 py-0.5 text-[11px] font-medium text-purple-300">
        {strategy.replace(/_/g, ' ')}
      </span>
    </div>
  );
}

function AgentCell(props: { value?: string }) {
  const v = props.value || '';
  const isBuyer = v.includes('Buyer');
  return (
    <div
      title={v}
      className={`py-2.5 text-xs font-semibold leading-snug whitespace-normal break-words ${
        isBuyer ? 'text-blue-400' : 'text-gray-200'
      }`}
    >
      {v}
    </div>
  );
}

function cleanDialogueMessage(raw: string): string {
  if (!raw) return '';
  let text = raw.trim();
  const hasDemo = text.startsWith('[DEMO SIMULATION]');
  if (hasDemo) {
    text = text.replace(/^\[DEMO SIMULATION\]\s*/i, '');
  }
  text = text.replace(
    /^\[(The\s+)?(Hard Bargainer|Value Defender|Strategic Flexible Negotiator|Willing to Bargain|Strategic Negotiator|Competitive Seller|Bargainer|Defender|Negotiator|Autonomous Buyer Agent|Buyer Agent)[^\]]*\]\s*:?\s*/i,
    ''
  );
  text = text.trim();
  return hasDemo ? `[DEMO] ${text}` : text;
}

function MessageCell(props: { value?: string }) {
  const v = cleanDialogueMessage(props.value || '');
  return (
    <div className="py-2.5 text-xs leading-relaxed text-gray-300 whitespace-normal break-words">
      {v}
    </div>
  );
}

function OfferCell(props: { value?: number | null }) {
  if (props.value === null || props.value === undefined) {
    return <div className="py-2.5 text-gray-500">—</div>;
  }
  return (
    <div className="py-2.5 tabular-nums font-semibold text-emerald-400">
      ${Number(props.value).toFixed(2)}
    </div>
  );
}

function StepCell(props: { value?: number | null }) {
  if (props.value === null || props.value === undefined) {
    return <div className="py-2.5 text-gray-500">—</div>;
  }
  return <div className="py-2.5 tabular-nums text-gray-400">{props.value}</div>;
}

function getStoredOrderId(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return sessionStorage.getItem('pending_paypal_order_id');
  } catch {
    return null;
  }
}

function setStoredOrderId(id: string): void {
  if (typeof window === 'undefined') return;
  try {
    sessionStorage.setItem('pending_paypal_order_id', id);
  } catch {}
}

function removeStoredOrderId(): void {
  if (typeof window === 'undefined') return;
  try {
    sessionStorage.removeItem('pending_paypal_order_id');
  } catch {}
}

const BUTTON_LABELS: Record<Phase, string> = {
  idle: 'Start Autonomous Procurement',
  running: 'Negotiating in Progress…',
  awaiting_approval: 'Review & Settle Deal',
  completed: 'Start New Procurement',
  failed: 'Restart Procurement',
  no_agreement: 'Restart Procurement',
};

export default function Home() {
  // Navigation
  const [activeTab, setActiveTab] = useState<ActiveTab>('procurement');

  // Input States
  const [product, setProduct] = useState('');
  const [qty, setQty] = useState('');
  const [budget, setBudget] = useState('');

  // Mode Selection: Separation of Negotiation Mode and Payment Mode
  const [negotiationMode, setNegotiationMode] = useState<'DEMO' | 'LIVE_AI'>('DEMO');
  const [paymentMode, setPaymentMode] = useState<'SIMULATED' | 'PAYPAL_SANDBOX'>('SIMULATED');

  // Buyer Preferences & Mandatory Constraints
  const [priorityType, setPriorityType] = useState<string>('BALANCED_VALUE');
  const [maxDeliveryDays, setMaxDeliveryDays] = useState<string>('');
  const [minWarrantyMonths, setMinWarrantyMonths] = useState<string>('');
  const [maxShippingCost, setMaxShippingCost] = useState<string>('');
  const [showAdvancedConstraints, setShowAdvancedConstraints] = useState(false);

  // Behavioral Learning Recommendations
  const [recommendations, setRecommendations] = useState<LearnedRecommendation[]>([]);

  // Runtime Procurement State
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [error, setError] = useState('');
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [backendStatus, setBackendStatus] = useState('');
  const [rowData, setRowData] = useState<EventRow[]>([]);
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [deal, setDeal] = useState<Deal | null>(null);
  const [orderId, setOrderId] = useState<string | null>(null);
  const [approveUrl, setApproveUrl] = useState<string | null>(null);
  const [captured, setCaptured] = useState(false);
  const [paypalNotice, setPaypalNotice] = useState<string | null>(null);
  const [isCapturing, setIsCapturing] = useState(false);
  const [captureFailedToken, setCaptureFailedToken] = useState<string | null>(null);

  // Intelligence Analytics Tab State
  const [analytics, setAnalytics] = useState<AnalyticsData | null>(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);

  // What-If Simulator Tab State
  const [simProduct, setSimProduct] = useState('Industrial Valves');
  const [simQty, setSimQty] = useState('500');
  const [simBudget, setSimBudget] = useState('85.00');
  const [simPriority, setSimPriority] = useState('BALANCED_VALUE');
  const [simMaxDelivery, setSimMaxDelivery] = useState('');
  const [simMinWarranty, setSimMinWarranty] = useState('');
  const [simResult, setSimResult] = useState<any | null>(null);
  const [simLoading, setSimLoading] = useState(false);

  // Quote Comparison Tab State
  const [quoteItems, setQuoteItems] = useState<any[]>([
    {
      supplier_name: 'Apex Industrial Corp',
      unit_price: 78.50,
      quantity: 500,
      delivery_days: 10,
      warranty_months: 12,
      shipping_cost: 150.0,
      payment_terms: 'Net 30',
      special_concessions: ['Volume Rebate'],
    },
    {
      supplier_name: 'Sterling Quality Works',
      unit_price: 84.00,
      quantity: 500,
      delivery_days: 12,
      warranty_months: 24,
      shipping_cost: 0.0,
      payment_terms: 'Net 30',
      special_concessions: ['Extended 24m Warranty', 'Free Freight'],
    },
    {
      supplier_name: 'Vanguard Logistics & Supply',
      unit_price: 81.00,
      quantity: 500,
      delivery_days: 5,
      warranty_months: 12,
      shipping_cost: 0.0,
      payment_terms: 'Net 60',
      special_concessions: ['Expedited 5-Day Delivery', 'Net 60 Terms'],
    },
  ]);
  const [quoteBudgetCap, setQuoteBudgetCap] = useState('90.00');
  const [quoteComparisonResult, setQuoteComparisonResult] = useState<any | null>(null);
  const [quoteComparing, setQuoteComparing] = useState(false);

  // Decision Audit Trail State
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([]);
  const [auditLoading, setAuditLoading] = useState(false);

  // Refs
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollInFlightRef = useRef(false);
  const confirmingRef = useRef(false);
  const mountedRef = useRef(true);

  const columnDefs: ColDef[] = [
    { field: 'step', headerName: '#', width: 55, cellRenderer: StepCell },
    {
      field: 'agent',
      headerName: 'Agent Persona',
      width: 175,
      wrapText: true,
      autoHeight: true,
      cellRenderer: AgentCell,
    },
    { field: 'strategy', headerName: 'Buyer Strategy', width: 210, cellRenderer: StrategyCell },
    {
      field: 'message',
      headerName: 'Negotiation Dialogue',
      flex: 2,
      minWidth: 440,
      wrapText: true,
      autoHeight: true,
      cellRenderer: MessageCell,
    },
    { field: 'offer', headerName: 'Offer $', width: 95, cellRenderer: OfferCell },
    { field: 'status', headerName: 'Status', width: 135, cellRenderer: StatusCell },
  ];

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const fetchRecommendations = useCallback(async () => {
    try {
      const res = await fetch(`${BACKEND_URL}/api/buyer/recommendations?buyer_id=default_buyer`, { cache: 'no-store' });
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data)) setRecommendations(data);
      }
    } catch {}
  }, []);

  const handleAcceptRecommendation = async (recId: string) => {
    try {
      const res = await fetch(`${BACKEND_URL}/api/buyer/recommendations/${recId}/accept?buyer_id=default_buyer`, {
        method: 'POST',
      });
      if (res.ok) {
        const data = await res.json();
        if (data?.preferences?.priority_type) {
          setPriorityType(data.preferences.priority_type);
        }
        fetchRecommendations();
      }
    } catch {}
  };

  const handleIgnoreRecommendation = async (recId: string) => {
    try {
      await fetch(`${BACKEND_URL}/api/buyer/recommendations/${recId}/ignore?buyer_id=default_buyer`, {
        method: 'POST',
      });
      fetchRecommendations();
    } catch {}
  };

  const fetchAnalytics = useCallback(async () => {
    setAnalyticsLoading(true);
    try {
      const res = await fetch(`${BACKEND_URL}/api/analytics`, { cache: 'no-store' });
      if (res.ok) {
        const data = await res.json();
        setAnalytics(data);
      }
    } catch {} finally {
      setAnalyticsLoading(false);
    }
  }, []);

  const fetchAuditTrail = useCallback(async (sessId: string) => {
    setAuditLoading(true);
    try {
      const res = await fetch(`${BACKEND_URL}/api/audit-trail?session_id=${encodeURIComponent(sessId)}`, { cache: 'no-store' });
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data.audit_trail)) {
          setAuditEntries(data.audit_trail);
        }
      }
    } catch {} finally {
      setAuditLoading(false);
    }
  }, []);

  const runSimulation = async () => {
    setSimLoading(true);
    try {
      const body = {
        product: simProduct,
        qty: Math.floor(Number(simQty) || 100),
        budget: Number(simBudget) || 50.0,
        priority_type: simPriority,
        max_delivery_days: simMaxDelivery ? Number(simMaxDelivery) : null,
        min_warranty_months: simMinWarranty ? Number(simMinWarranty) : null,
      };
      const res = await fetch(`${BACKEND_URL}/api/simulator`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        const data = await res.json();
        setSimResult(data);
      }
    } catch {} finally {
      setSimLoading(false);
    }
  };

  const runQuoteComparison = async () => {
    setQuoteComparing(true);
    try {
      const body = {
        quotes: quoteItems,
        budget_cap: Number(quoteBudgetCap) || 100.0,
        preferences: {
          priority_type: priorityType,
          constraints: {
            max_delivery_days: maxDeliveryDays ? Number(maxDeliveryDays) : null,
            min_warranty_months: minWarrantyMonths ? Number(minWarrantyMonths) : null,
            max_shipping_cost: maxShippingCost ? Number(maxShippingCost) : null,
          },
        },
      };
      const res = await fetch(`${BACKEND_URL}/api/quotes/compare`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        const data = await res.json();
        setQuoteComparisonResult(data);
      }
    } catch {} finally {
      setQuoteComparing(false);
    }
  };

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
      setBackendStatus(data.status || '');
      if (data.order_id) setOrderId(data.order_id);
      setApproveUrl(data.approve_url || null);
      if (data.session_id) setCurrentSessionId(data.session_id);

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
        const url = currentSessionId
          ? `${BACKEND_URL}/api/status?session_id=${encodeURIComponent(currentSessionId)}`
          : `${BACKEND_URL}/api/status`;
        const res = await fetch(url, {
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
      // Keep polling on transient network hiccups
    } finally {
      pollInFlightRef.current = false;
    }
  }, [applyStatusResponse, currentSessionId]);

  const executePaymentCapture = useCallback(
    async (tokenToCapture: string) => {
      if (confirmingRef.current) return;
      confirmingRef.current = true;
      setIsCapturing(true);
      setError('');
      setCaptureFailedToken(null);
      setPaypalNotice('Finalizing payment settlement and recording in persistent ledger...');

      try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 35000);
        try {
          const sessParam = currentSessionId ? `&session_id=${encodeURIComponent(currentSessionId)}` : '';
          const res = await fetch(
            `${BACKEND_URL}/api/confirm-payment?token=${encodeURIComponent(tokenToCapture)}${sessParam}`,
            { signal: controller.signal }
          );
          const data = await res.json();
          if (!res.ok) {
            throw new Error(data?.detail || 'Payment capture was rejected by server.');
          }
          removeStoredOrderId();
          setCaptured(true);
          setPhase('completed');
          setPaypalNotice(null);
          setCaptureFailedToken(null);
          if (typeof window !== 'undefined') {
            window.history.replaceState({}, document.title, window.location.pathname);
          }
          // Fetch status once to update table
          try {
            const statusUrl = currentSessionId
              ? `${BACKEND_URL}/api/status?session_id=${encodeURIComponent(currentSessionId)}`
              : `${BACKEND_URL}/api/status`;
            const statusRes = await fetch(statusUrl, { cache: 'no-store' });
            if (statusRes.ok) {
              const statusData = await statusRes.json();
              if (mountedRef.current && statusData) {
                applyStatusResponse(statusData);
              }
            }
          } catch {}
          // Refresh recommendations and analytics
          fetchRecommendations();
        } finally {
          clearTimeout(timeout);
        }
      } catch (err: any) {
        setError(err?.message || 'Payment settlement failed. Please retry.');
        setCaptureFailedToken(tokenToCapture);
        setPaypalNotice(null);
      } finally {
        setIsCapturing(false);
        confirmingRef.current = false;
      }
    },
    [applyStatusResponse, currentSessionId, fetchRecommendations]
  );

  useEffect(() => {
    mountedRef.current = true;
    fetchRecommendations();

    // Detect PayPal return or cancellation
    if (typeof window !== 'undefined') {
      const urlParams = new URLSearchParams(window.location.search);
      const isCancelled = urlParams.get('paypal_cancel') === 'true';
      const isReturn = urlParams.get('paypal_return') === 'true';
      const tokenParam = urlParams.get('token');

      if (isCancelled) {
        removeStoredOrderId();
        setPaypalNotice(
          'PayPal checkout was cancelled. No money was captured. You can review the agreed terms and approve or restart anytime.'
        );
        window.history.replaceState({}, document.title, window.location.pathname);
      } else if (tokenParam && !isCancelled) {
        setOrderId(tokenParam);
        executePaymentCapture(tokenParam);
      }
    }

    return () => {
      mountedRef.current = false;
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [executePaymentCapture, fetchRecommendations]);

  // When switching tabs, auto-fetch tab data
  useEffect(() => {
    if (activeTab === 'intelligence') {
      fetchAnalytics();
    } else if (activeTab === 'audit' && currentSessionId) {
      fetchAuditTrail(currentSessionId);
    }
  }, [activeTab, currentSessionId, fetchAnalytics, fetchAuditTrail]);

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
    let newSessionId: string | null = null;

    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 30000);
      try {
        const payload: Record<string, any> = {
          product: product.trim(),
          qty: Math.floor(Number(qty)),
          budget: Number(budget),
          negotiation_mode: negotiationMode,
          payment_mode: paymentMode,
          demo_mode: negotiationMode === 'DEMO',
          buyer_id: 'default_buyer',
          preferences: {
            priority_type: priorityType,
            constraints: {
              max_delivery_days: maxDeliveryDays ? Number(maxDeliveryDays) : null,
              min_warranty_months: minWarrantyMonths ? Number(minWarrantyMonths) : null,
              max_shipping_cost: maxShippingCost ? Number(maxShippingCost) : null,
            },
            preferred_payment_terms: 'Net 30',
          },
        };

        const res = await fetch(`${BACKEND_URL}/api/start`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
          signal: controller.signal,
        });
        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          failure = errData?.detail || `The backend rejected the request (status ${res.status}).`;
        } else {
          const resData = await res.json();
          newSessionId = resData?.session_id || null;
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

    setCurrentSessionId(newSessionId);
    setCaptured(false);
    setRowData([]);
    setVendors([]);
    setDeal(null);
    setOrderId(null);
    setApproveUrl(null);
    confirmingRef.current = false;
    setPhase('running');
    startPolling();
  };

  const openApprove = (url: string) => {
    if (orderId) {
      setStoredOrderId(orderId);
    }
    // Single consistent navigation: redirect in the same tab
    window.location.href = url;
  };

  const running = phase === 'running';
  const canSubmit = !running && phase !== 'awaiting_approval';

  return (
    <main className="mx-auto min-h-screen max-w-7xl bg-gray-950 px-4 py-8 text-white sm:px-8">
      {/* Main Header */}
      <header className="mb-6 flex flex-col justify-between gap-4 border-b border-gray-800 pb-5 sm:flex-row sm:items-center">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight text-white">
            Autonomous B2B Procurement Engine
          </h1>
          <p className="mt-1 text-sm text-gray-400">
            Multi-agent autonomous commercial negotiation with deterministic scoring, preference learning, and verified settlement.
          </p>
        </div>

        {/* Global Navigation Tabs */}
        <nav className="flex items-center gap-1 rounded-lg border border-gray-800 bg-gray-900/90 p-1 text-xs">
          <button
            type="button"
            onClick={() => setActiveTab('procurement')}
            className={`rounded px-3 py-1.5 font-medium transition-colors ${
              activeTab === 'procurement'
                ? 'bg-blue-600 text-white shadow-sm'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            Live Procurement
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('intelligence')}
            className={`rounded px-3 py-1.5 font-medium transition-colors ${
              activeTab === 'intelligence'
                ? 'bg-blue-600 text-white shadow-sm'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            Intelligence Analytics
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('simulator')}
            className={`rounded px-3 py-1.5 font-medium transition-colors ${
              activeTab === 'simulator'
                ? 'bg-blue-600 text-white shadow-sm'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            What-If Simulator
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('quotes')}
            className={`rounded px-3 py-1.5 font-medium transition-colors ${
              activeTab === 'quotes'
                ? 'bg-blue-600 text-white shadow-sm'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            Quote Comparison
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('audit')}
            className={`rounded px-3 py-1.5 font-medium transition-colors ${
              activeTab === 'audit'
                ? 'bg-blue-600 text-white shadow-sm'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            Decision Audit Trail
          </button>
        </nav>
      </header>

      {/* Behavioral Learning & Recommendation Consent Banner */}
      {recommendations.length > 0 && activeTab === 'procurement' && (
        <div className="mb-6 space-y-3">
          {recommendations.map((rec) => (
            <div
              key={rec.id}
              className="flex flex-col gap-3 rounded-lg border border-indigo-500/40 bg-indigo-950/30 p-4 text-xs text-indigo-200 shadow-lg sm:flex-row sm:items-center sm:justify-between"
            >
              <div>
                <div className="flex items-center gap-2">
                  <span className="rounded bg-indigo-500/20 px-2 py-0.5 font-bold uppercase tracking-wider text-indigo-300">
                    Learned Behavioral Pattern
                  </span>
                  <span className="font-semibold text-white">{rec.title}</span>
                </div>
                <p className="mt-1 text-gray-300">{rec.description}</p>
                <p className="mt-1 text-[11px] text-indigo-400 italic font-mono">{rec.evidence}</p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => handleAcceptRecommendation(rec.id)}
                  className="rounded-md bg-indigo-600 px-3 py-1.5 font-semibold text-white transition-colors hover:bg-indigo-500"
                >
                  Accept & Save
                </button>
                <button
                  type="button"
                  onClick={() => handleIgnoreRecommendation(rec.id)}
                  className="rounded-md border border-gray-700 bg-gray-900 px-3 py-1.5 font-medium text-gray-400 transition-colors hover:bg-gray-800 hover:text-white"
                >
                  Ignore
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* TAB 1: LIVE PROCUREMENT */}
      {activeTab === 'procurement' && (
        <>
          {/* Configuration & Controls Section */}
          <section className="rounded-xl border border-gray-800 bg-gray-900/60 p-5 shadow-lg">
            {/* Mode Selectors */}
            <div className="mb-5 flex flex-col gap-4 border-b border-gray-800/80 pb-5 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-4">
                <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">
                  Negotiation Engine:
                </span>
                <div className="flex items-center gap-1.5 rounded-lg border border-gray-800 bg-gray-950 p-1 text-xs">
                  <button
                    type="button"
                    onClick={() => setNegotiationMode('DEMO')}
                    className={`rounded px-3 py-1.5 font-medium transition-colors ${
                      negotiationMode === 'DEMO'
                        ? 'bg-purple-600 text-white shadow-sm'
                        : 'text-gray-400 hover:text-gray-200'
                    }`}
                  >
                    Deterministic Demo
                  </button>
                  <button
                    type="button"
                    onClick={() => setNegotiationMode('LIVE_AI')}
                    className={`rounded px-3 py-1.5 font-medium transition-colors ${
                      negotiationMode === 'LIVE_AI'
                        ? 'bg-blue-600 text-white shadow-sm'
                        : 'text-gray-400 hover:text-gray-200'
                    }`}
                  >
                    Live AI (Groq)
                  </button>
                </div>
              </div>

              <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-4">
                <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">
                  Payment Mode:
                </span>
                <div className="flex items-center gap-1.5 rounded-lg border border-gray-800 bg-gray-950 p-1 text-xs">
                  <button
                    type="button"
                    onClick={() => setPaymentMode('SIMULATED')}
                    className={`rounded px-3 py-1.5 font-medium transition-colors ${
                      paymentMode === 'SIMULATED'
                        ? 'bg-teal-600 text-white shadow-sm'
                        : 'text-gray-400 hover:text-gray-200'
                    }`}
                  >
                    Simulated Settlement
                  </button>
                  <button
                    type="button"
                    onClick={() => setPaymentMode('PAYPAL_SANDBOX')}
                    className={`rounded px-3 py-1.5 font-medium transition-colors ${
                      paymentMode === 'PAYPAL_SANDBOX'
                        ? 'bg-green-600 text-white shadow-sm'
                        : 'text-gray-400 hover:text-gray-200'
                    }`}
                  >
                    PayPal Sandbox Checkout
                  </button>
                </div>
              </div>
            </div>

            {/* Inputs: Product, Qty, Budget, Priority */}
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
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
              <div>
                <label
                  htmlFor="priority"
                  className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-gray-400"
                >
                  Buyer Priority Goal
                </label>
                <select
                  id="priority"
                  value={priorityType}
                  onChange={(e) => setPriorityType(e.target.value)}
                  className="w-full rounded-md border border-gray-700 bg-gray-950 px-3 py-2.5 text-sm text-white outline-none transition-colors focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
                >
                  <option value="BALANCED_VALUE">Balanced Commercial Value</option>
                  <option value="LOWEST_PRICE">Lowest Price (Aggressive Discount)</option>
                  <option value="FASTEST_DELIVERY">Fastest Delivery Lead Time</option>
                  <option value="QUALITY_WARRANTY">Quality & Extended Warranty</option>
                </select>
              </div>
            </div>

            {/* Optional Mandatory Hard Constraints */}
            <div className="mt-4 border-t border-gray-800/80 pt-3">
              <button
                type="button"
                onClick={() => setShowAdvancedConstraints(!showAdvancedConstraints)}
                className="text-xs font-medium text-gray-400 hover:text-gray-200"
              >
                {showAdvancedConstraints ? '▲ Hide Mandatory Constraints' : '▼ Add Mandatory Hard Constraints (Disqualification Rules)'}
              </button>

              {showAdvancedConstraints && (
                <div className="mt-3 grid grid-cols-1 gap-4 rounded-lg border border-gray-800 bg-gray-950/60 p-3 sm:grid-cols-3">
                  <div>
                    <label className="mb-1 block text-[11px] uppercase tracking-wider text-gray-400">
                      Max Lead Time (Calendar Days)
                    </label>
                    <input
                      type="number"
                      placeholder="e.g. 10"
                      value={maxDeliveryDays}
                      onChange={(e) => setMaxDeliveryDays(e.target.value)}
                      className="w-full rounded border border-gray-700 bg-gray-900 px-2.5 py-1.5 text-xs text-white outline-none focus:border-blue-500"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-[11px] uppercase tracking-wider text-gray-400">
                      Min Warranty (Months)
                    </label>
                    <input
                      type="number"
                      placeholder="e.g. 18"
                      value={minWarrantyMonths}
                      onChange={(e) => setMinWarrantyMonths(e.target.value)}
                      className="w-full rounded border border-gray-700 bg-gray-900 px-2.5 py-1.5 text-xs text-white outline-none focus:border-blue-500"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-[11px] uppercase tracking-wider text-gray-400">
                      Max Freight Cost ($)
                    </label>
                    <input
                      type="number"
                      placeholder="e.g. 100.00"
                      value={maxShippingCost}
                      onChange={(e) => setMaxShippingCost(e.target.value)}
                      className="w-full rounded border border-gray-700 bg-gray-900 px-2.5 py-1.5 text-xs text-white outline-none focus:border-blue-500"
                    />
                  </div>
                </div>
              )}
            </div>

            {/* Launch Action */}
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

              {currentSessionId && (
                <div className="text-xs text-gray-500 font-mono">
                  Session: <span className="text-gray-300">{currentSessionId}</span>
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

          {/* Settled Notification */}
          {captured && (
            <div className="mt-6 flex items-center gap-2 rounded-lg border border-green-500/40 bg-green-500/10 px-4 py-3 text-sm font-semibold text-green-400 shadow-md">
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
              ✓ Commercial Deal Settled & Payment Transaction Captured Successfully.
            </div>
          )}

          {/* PayPal Notice (e.g. cancellation or auto-capture status) */}
          {paypalNotice && (
            <div className="mt-6 flex items-center justify-between rounded-lg border border-yellow-500/40 bg-yellow-500/10 px-4 py-3 text-sm text-yellow-300 shadow-md">
              <div className="flex items-center gap-2">
                {isCapturing && (
                  <svg className="h-4 w-4 animate-spin text-yellow-300" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
                  </svg>
                )}
                <span>{paypalNotice}</span>
              </div>
              {!isCapturing && (
                <button
                  onClick={() => setPaypalNotice(null)}
                  className="text-gray-400 transition-colors hover:text-white"
                  aria-label="Dismiss notice"
                >
                  ✕
                </button>
              )}
            </div>
          )}

          {captureFailedToken && !isCapturing && (
            <div className="mt-6 flex flex-col gap-3 rounded-lg border border-red-500/40 bg-red-500/10 p-4 text-sm text-red-300 shadow-md sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="font-semibold text-red-400">Payment Verification Failed</p>
                <p className="mt-0.5 text-xs text-red-300">
                  Order {captureFailedToken} settlement encountered an error.
                </p>
              </div>
              <button
                onClick={() => executePaymentCapture(captureFailedToken)}
                className="rounded-md bg-red-600 px-4 py-2 text-xs font-semibold text-white shadow transition-colors hover:bg-red-500"
              >
                Retry Settlement
              </button>
            </div>
          )}

          {/* Deal Winner & Commercial Scoring Breakdown Card */}
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
                      <span className="text-gray-400">Price Score</span>
                      <div className="font-semibold text-white">{deal.score_breakdown.price_score}</div>
                    </div>
                    <div className="rounded bg-gray-900/60 p-2">
                      <span className="text-gray-400">Delivery Score</span>
                      <div className="font-semibold text-white">{deal.score_breakdown.delivery_score}</div>
                    </div>
                    <div className="rounded bg-gray-900/60 p-2">
                      <span className="text-gray-400">Warranty Score</span>
                      <div className="font-semibold text-white">{deal.score_breakdown.warranty_score}</div>
                    </div>
                    <div className="rounded bg-gray-900/60 p-2">
                      <span className="text-gray-400">Shipping Score</span>
                      <div className="font-semibold text-white">{deal.score_breakdown.shipping_score}</div>
                    </div>
                    <div className="rounded bg-gray-900/60 p-2">
                      <span className="text-gray-400">Terms Score</span>
                      <div className="font-semibold text-white">{deal.score_breakdown.terms_score}</div>
                    </div>
                    <div className="rounded bg-gray-900/60 p-2">
                      <span className="text-gray-400">Reliability Score</span>
                      <div className="font-semibold text-white">{deal.score_breakdown.reliability_score}</div>
                    </div>
                  </div>
                  <p className="mt-2 text-xs text-gray-300 italic">{deal.score_breakdown.explanation}</p>
                </div>
              )}
            </section>
          )}

          {/* Smart Approval & Settlement Card (Features 3 & 11) */}
          {phase === 'awaiting_approval' && (
            <section className="mt-6">
              {paymentMode === 'SIMULATED' ? (
                <div className="rounded-xl border border-teal-500/40 bg-teal-950/20 p-5 shadow-lg">
                  <div className="flex items-center gap-2">
                    <span className="h-2.5 w-2.5 rounded-full bg-teal-400 animate-pulse"></span>
                    <h3 className="text-base font-bold text-teal-300">Simulated Deal Settlement Confirmation</h3>
                  </div>
                  <p className="mt-2 text-xs text-gray-300">
                    Negotiation finalized. Deal locked with <strong className="text-white">{deal?.selected_vendor}</strong>.
                    Simulated Order ID: <span className="font-mono text-white">{orderId}</span>
                  </p>
                  <div className="mt-4">
                    <button
                      onClick={() => orderId && executePaymentCapture(orderId)}
                      disabled={isCapturing}
                      className="rounded-md bg-teal-600 px-6 py-2.5 text-sm font-semibold text-white shadow-md transition-colors hover:bg-teal-500 disabled:opacity-50"
                    >
                      {isCapturing ? 'Recording Settlement...' : 'Confirm Simulated Settlement'}
                    </button>
                  </div>
                </div>
              ) : (
                approveUrl && (
                  <div className="rounded-xl border border-yellow-400/40 bg-yellow-400/5 p-5 shadow-lg">
                    <div className="flex items-center gap-2">
                      <span className="h-2.5 w-2.5 rounded-full bg-yellow-400 animate-pulse"></span>
                      <h3 className="text-base font-bold text-yellow-300">PayPal Sandbox Human Authorization Required</h3>
                    </div>
                    {orderId && (
                      <p className="mt-2 text-xs text-gray-300">
                        Pending Sandbox Order ID: <span className="font-mono text-white">{orderId}</span>
                      </p>
                    )}
                    <div className="mt-4 flex items-center">
                      <button
                        onClick={() => openApprove(approveUrl)}
                        disabled={isCapturing}
                        className="rounded-md bg-green-600 px-6 py-2.5 text-sm font-semibold text-white shadow-md transition-colors hover:bg-green-500 disabled:opacity-50"
                      >
                        Authorize on PayPal Sandbox
                      </button>
                    </div>
                    <p className="mt-3 text-xs text-gray-400">
                      You will be redirected to PayPal Sandbox to review terms and approve payment. Once approved, you will return here automatically for verified final capture.
                    </p>
                  </div>
                )
              )}
            </section>
          )}

          {/* Real-time Explainable AG Grid Feed */}
          <section className="mt-6">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-400">
                Explainable Multi-Agent Negotiation Stream
              </h2>
              <span className="text-xs text-gray-500">Autonomous adaptive turn protocol</span>
            </div>
            <div className="overflow-x-auto rounded-xl border border-gray-800 shadow-md">
              <div
                className="ag-theme-alpine-dark"
                style={{ height: 500, width: '100%', minWidth: 920 }}
              >
                <AgGridReact
                  rowData={rowData}
                  columnDefs={columnDefs}
                  defaultColDef={{ sortable: false, filter: false, resizable: true }}
                  getRowId={(params) => String(params.data.step ?? params.data.agent)}
                  theme="legacy"
                  headerHeight={44}
                  suppressCellFocus={true}
                />
              </div>
            </div>
          </section>

          {/* Supplier Personas & Memory */}
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
        </>
      )}

      {/* TAB 2: INTELLIGENCE ANALYTICS */}
      {activeTab === 'intelligence' && (
        <section className="space-y-6">
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-bold text-white">Procurement Intelligence Dashboard</h2>
            <button
              onClick={fetchAnalytics}
              className="rounded bg-gray-800 px-3 py-1.5 text-xs font-medium text-gray-300 hover:bg-gray-700"
            >
              Refresh Analytics
            </button>
          </div>

          {analyticsLoading && <p className="text-sm text-gray-400">Loading procurement metrics...</p>}

          {analytics && (
            <>
              {/* Top KPI Cards: Real Sandbox vs Simulated Spend Separation */}
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
                <div className="rounded-xl border border-gray-800 bg-gray-900/60 p-4 shadow-sm">
                  <span className="text-xs text-gray-400">PayPal Sandbox Spend</span>
                  <p className="mt-1 text-2xl font-black text-green-400">
                    ${analytics.overall.total_sandbox_spend.toFixed(2)}
                  </p>
                  <p className="mt-1 text-xs text-gray-500">
                    {analytics.overall.sandbox_orders_count} Real Sandbox Orders
                  </p>
                </div>
                <div className="rounded-xl border border-gray-800 bg-gray-900/60 p-4 shadow-sm">
                  <span className="text-xs text-gray-400">Simulated Deal Spend</span>
                  <p className="mt-1 text-2xl font-black text-teal-400">
                    ${analytics.overall.total_simulated_spend.toFixed(2)}
                  </p>
                  <p className="mt-1 text-xs text-gray-500">
                    {analytics.overall.simulated_orders_count} Simulated Deals
                  </p>
                </div>
                <div className="rounded-xl border border-gray-800 bg-gray-900/60 p-4 shadow-sm">
                  <span className="text-xs text-gray-400">Negotiated Savings</span>
                  <p className="mt-1 text-2xl font-black text-cyan-400">
                    ${analytics.overall.total_savings.toFixed(2)}
                  </p>
                  <p className="mt-1 text-xs text-gray-500">Saved vs Initial Budget</p>
                </div>
                <div className="rounded-xl border border-gray-800 bg-gray-900/60 p-4 shadow-sm">
                  <span className="text-xs text-gray-400">Completed Procurements</span>
                  <p className="mt-1 text-2xl font-black text-white">
                    {analytics.overall.completed_sessions}
                  </p>
                  <p className="mt-1 text-xs text-gray-500">
                    of {analytics.overall.total_sessions} Total Sessions
                  </p>
                </div>
              </div>

              {/* Supplier Intelligence Comparison */}
              <div className="rounded-xl border border-gray-800 bg-gray-900/60 p-5 shadow-lg">
                <h3 className="mb-3 text-sm font-semibold uppercase tracking-wider text-gray-400">
                  Supplier Intelligence & Historical Reliability
                </h3>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead>
                      <tr className="border-b border-gray-800 text-gray-400">
                        <th className="pb-2">Supplier Name</th>
                        <th className="pb-2">Attempted</th>
                        <th className="pb-2">Agreed Deals</th>
                        <th className="pb-2">PayPal Captures</th>
                        <th className="pb-2">Simulated Deals</th>
                        <th className="pb-2">Avg Deal Amount</th>
                        <th className="pb-2">Reliability Score</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-800/60">
                      {Object.entries(analytics.suppliers).map(([name, m]) => (
                        <tr key={name} className="hover:bg-gray-800/20">
                          <td className="py-2.5 font-semibold text-white">{name}</td>
                          <td className="py-2.5 text-gray-300">{m.attempted_deals}</td>
                          <td className="py-2.5 text-gray-300">{m.accepted_deals}</td>
                          <td className="py-2.5 font-semibold text-green-400">{m.captured_payments}</td>
                          <td className="py-2.5 font-semibold text-teal-400">{m.simulated_deals}</td>
                          <td className="py-2.5 text-gray-300">${m.avg_deal_amount.toFixed(2)}</td>
                          <td className="py-2.5">
                            <span className="rounded bg-blue-500/10 px-2 py-0.5 font-medium text-blue-300">
                              {(m.reliability_score * 100).toFixed(0)}%
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Strategy Effectiveness Metrics */}
              <div className="rounded-xl border border-gray-800 bg-gray-900/60 p-5 shadow-lg">
                <h3 className="mb-3 text-sm font-semibold uppercase tracking-wider text-gray-400">
                  Buyer Strategy Effectiveness & Win Rates
                </h3>
                {analytics.strategies.length === 0 ? (
                  <p className="text-xs text-gray-500 italic">No completed strategy records yet.</p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs">
                      <thead>
                        <tr className="border-b border-gray-800 text-gray-400">
                          <th className="pb-2">Strategy Name</th>
                          <th className="pb-2">Times Used</th>
                          <th className="pb-2">Concessions Won</th>
                          <th className="pb-2">Avg Price Reduction</th>
                          <th className="pb-2">Deals Closed</th>
                          <th className="pb-2">Win Rate</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-800/60">
                        {analytics.strategies.map((st) => (
                          <tr key={st.strategy_name} className="hover:bg-gray-800/20">
                            <td className="py-2.5 font-medium text-purple-300">{st.strategy_name}</td>
                            <td className="py-2.5 text-gray-300">{st.total_uses}</td>
                            <td className="py-2.5 text-gray-300">{st.concessions_won}</td>
                            <td className="py-2.5 text-emerald-400 font-semibold">${st.avg_price_cut.toFixed(2)}</td>
                            <td className="py-2.5 text-gray-300">{st.deals_closed}</td>
                            <td className="py-2.5 font-semibold text-white">
                              {st.total_uses > 0 ? `${((st.deals_closed / st.total_uses) * 100).toFixed(0)}%` : '0%'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </>
          )}
        </section>
      )}

      {/* TAB 3: WHAT-IF SIMULATOR */}
      {activeTab === 'simulator' && (
        <section className="space-y-6">
          <div className="rounded-xl border border-gray-800 bg-gray-900/60 p-5 shadow-lg">
            <h2 className="text-lg font-bold text-white">What-If Procurement Scenario Simulator</h2>
            <p className="mt-1 text-xs text-gray-400">
              Explore trade-offs across supplier archetypes under varied commercial constraints. Uses zero Groq AI tokens and initiates zero PayPal orders.
            </p>

            <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-gray-400">
                  Product
                </label>
                <input
                  type="text"
                  value={simProduct}
                  onChange={(e) => setSimProduct(e.target.value)}
                  className="w-full rounded border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-white outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-gray-400">
                  Quantity
                </label>
                <input
                  type="number"
                  value={simQty}
                  onChange={(e) => setSimQty(e.target.value)}
                  className="w-full rounded border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-white outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-gray-400">
                  Target Budget ($/unit)
                </label>
                <input
                  type="number"
                  step="0.01"
                  value={simBudget}
                  onChange={(e) => setSimBudget(e.target.value)}
                  className="w-full rounded border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-white outline-none focus:border-blue-500"
                />
              </div>
            </div>

            <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-gray-400">
                  Priority Goal
                </label>
                <select
                  value={simPriority}
                  onChange={(e) => setSimPriority(e.target.value)}
                  className="w-full rounded border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-white outline-none focus:border-blue-500"
                >
                  <option value="BALANCED_VALUE">Balanced Commercial Value</option>
                  <option value="LOWEST_PRICE">Lowest Price</option>
                  <option value="FASTEST_DELIVERY">Fastest Delivery</option>
                  <option value="QUALITY_WARRANTY">Quality & Warranty</option>
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-gray-400">
                  Max Delivery Days (Constraint)
                </label>
                <input
                  type="number"
                  placeholder="Optional constraint"
                  value={simMaxDelivery}
                  onChange={(e) => setSimMaxDelivery(e.target.value)}
                  className="w-full rounded border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-white outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-gray-400">
                  Min Warranty Months (Constraint)
                </label>
                <input
                  type="number"
                  placeholder="Optional constraint"
                  value={simMinWarranty}
                  onChange={(e) => setMinWarrantyMonths(e.target.value)}
                  className="w-full rounded border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-white outline-none focus:border-blue-500"
                />
              </div>
            </div>

            <div className="mt-5">
              <button
                type="button"
                onClick={runSimulation}
                disabled={simLoading}
                className="rounded-md bg-purple-600 px-6 py-2.5 text-sm font-semibold text-white shadow transition-colors hover:bg-purple-500 disabled:opacity-50"
              >
                {simLoading ? 'Simulating Outcomes...' : 'Run What-If Simulation'}
              </button>
            </div>
          </div>

          {/* Simulation Output */}
          {simResult && (
            <div className="space-y-4">
              <div className="rounded-xl border border-purple-500/40 bg-purple-950/20 p-4 text-xs">
                <span className="font-semibold text-purple-300">Recommended Winner: </span>
                <strong className="text-white text-sm">{simResult.recommended_winner}</strong>
                <span className="ml-4 text-gray-400">
                  Estimated Total Cost: <strong className="text-emerald-400">${simResult.estimated_total_cost.toFixed(2)}</strong>
                </span>
                <p className="mt-1 text-gray-400 italic">{simResult.disclaimer}</p>
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                {simResult.suppliers.map((s: any) => (
                  <div
                    key={s.supplier_name}
                    className={`rounded-lg border p-4 text-xs shadow-sm ${
                      s.is_disqualified
                        ? 'border-red-800 bg-red-950/20 opacity-75'
                        : s.supplier_name === simResult.recommended_winner
                        ? 'border-cyan-500 bg-cyan-950/20'
                        : 'border-gray-800 bg-gray-900/60'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <p className="font-bold text-white text-sm">{s.supplier_name}</p>
                      <span className="rounded bg-gray-800 px-2 py-0.5 text-[10px] text-gray-300">
                        Score: {s.commercial_score.toFixed(1)}
                      </span>
                    </div>
                    <p className="text-gray-400 text-[11px] mt-0.5">{s.archetype}</p>

                    <div className="mt-3 space-y-1.5 border-t border-gray-800/80 pt-2 text-gray-300">
                      <div className="flex justify-between">
                        <span>Unit Price:</span>
                        <strong className="text-emerald-400">${s.estimated_unit_price.toFixed(2)}</strong>
                      </div>
                      <div className="flex justify-between">
                        <span>Lead Time:</span>
                        <span>{s.estimated_delivery_days} days</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Warranty:</span>
                        <span>{s.estimated_warranty_months} mos</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Freight:</span>
                        <span>{s.estimated_shipping_cost <= 0 ? 'FREE' : `$${s.estimated_shipping_cost.toFixed(2)}`}</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Terms:</span>
                        <span>{s.estimated_payment_terms}</span>
                      </div>
                    </div>

                    {s.is_disqualified && (
                      <div className="mt-2 rounded bg-red-900/30 p-2 text-[11px] text-red-300">
                        Disqualified: {s.violations?.join(', ')}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      )}

      {/* TAB 4: QUOTE COMPARISON */}
      {activeTab === 'quotes' && (
        <section className="space-y-6">
          <div className="rounded-xl border border-gray-800 bg-gray-900/60 p-5 shadow-lg">
            <h2 className="text-lg font-bold text-white">Quote & Contract Comparison Engine</h2>
            <p className="mt-1 text-xs text-gray-400">
              Side-by-side comparison of supplier quotes against your priority goals and mandatory constraints.
            </p>

            <div className="mt-4 flex items-center justify-between">
              <div className="w-64">
                <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-gray-400">
                  Budget Cap ($/unit)
                </label>
                <input
                  type="number"
                  step="0.01"
                  value={quoteBudgetCap}
                  onChange={(e) => setQuoteBudgetCap(e.target.value)}
                  className="w-full rounded border border-gray-700 bg-gray-950 px-3 py-1.5 text-xs text-white outline-none focus:border-blue-500"
                />
              </div>
              <button
                type="button"
                onClick={runQuoteComparison}
                disabled={quoteComparing}
                className="rounded-md bg-blue-600 px-6 py-2 text-xs font-semibold text-white shadow transition-colors hover:bg-blue-500 disabled:opacity-50"
              >
                {quoteComparing ? 'Evaluating Quotes...' : 'Compare Quotes'}
              </button>
            </div>
          </div>

          {/* Quotes Table */}
          <div className="overflow-x-auto rounded-xl border border-gray-800 bg-gray-900/60 p-5 shadow-lg">
            <h3 className="mb-3 text-sm font-semibold uppercase tracking-wider text-gray-400">
              Evaluated Quotations
            </h3>
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-gray-800 text-gray-400">
                  <th className="pb-2">Supplier</th>
                  <th className="pb-2">Unit Price</th>
                  <th className="pb-2">Lead Time</th>
                  <th className="pb-2">Warranty</th>
                  <th className="pb-2">Freight</th>
                  <th className="pb-2">Terms</th>
                  <th className="pb-2">Special Concessions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800/60">
                {quoteItems.map((q, idx) => (
                  <tr key={idx} className="hover:bg-gray-800/20">
                    <td className="py-2.5 font-semibold text-white">{q.supplier_name}</td>
                    <td className="py-2.5 font-bold text-emerald-400">${q.unit_price.toFixed(2)}</td>
                    <td className="py-2.5 text-gray-300">{q.delivery_days} days</td>
                    <td className="py-2.5 text-purple-300">{q.warranty_months} mos</td>
                    <td className="py-2.5 text-gray-300">{q.shipping_cost <= 0 ? 'FREE' : `$${q.shipping_cost.toFixed(2)}`}</td>
                    <td className="py-2.5 text-blue-300">{q.payment_terms}</td>
                    <td className="py-2.5 text-gray-400">{q.special_concessions?.join(', ') || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Quote Comparison Results */}
          {quoteComparisonResult && (
            <div className="rounded-xl border border-blue-500/40 bg-blue-950/20 p-5 shadow-lg">
              <div className="flex items-center justify-between border-b border-blue-800/40 pb-3">
                <div>
                  <span className="rounded bg-blue-500/20 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-blue-300">
                    Recommended Vendor Quote
                  </span>
                  <h3 className="mt-1 text-lg font-bold text-white">{quoteComparisonResult.best_quote}</h3>
                </div>
                <div className="text-right">
                  <span className="text-xs text-gray-400">Evaluation Score</span>
                  <p className="text-xl font-bold text-blue-300">
                    {quoteComparisonResult.best_score?.toFixed(1) || '0.0'} / 100
                  </p>
                </div>
              </div>
              <p className="mt-3 text-xs text-gray-300">{quoteComparisonResult.summary}</p>

              <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
                {quoteComparisonResult.comparison?.map((c: any) => (
                  <div
                    key={c.supplier_name}
                    className={`rounded-lg border p-3 text-xs ${
                      c.is_disqualified
                        ? 'border-red-800 bg-red-950/20'
                        : c.supplier_name === quoteComparisonResult.best_quote
                        ? 'border-blue-500 bg-blue-900/30'
                        : 'border-gray-800 bg-gray-900/60'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <p className="font-semibold text-white">{c.supplier_name}</p>
                      <span className="font-bold text-blue-300">{c.commercial_score.toFixed(1)} pts</span>
                    </div>
                    {c.is_disqualified ? (
                      <p className="mt-2 text-red-300 text-[11px]">
                        Violations: {c.violations?.join(', ')}
                      </p>
                    ) : (
                      <p className="mt-2 text-gray-300 text-[11px] italic">{c.explanation}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      )}

      {/* TAB 5: DECISION AUDIT TRAIL */}
      {activeTab === 'audit' && (
        <section className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-bold text-white">Decision Governance Audit Trail</h2>
              <p className="mt-1 text-xs text-gray-400">
                Explainable record of autonomous agent strategy formulation, adaptation, and commercial winner selection.
              </p>
            </div>
            {currentSessionId && (
              <button
                onClick={() => fetchAuditTrail(currentSessionId)}
                className="rounded bg-gray-800 px-3 py-1.5 text-xs font-medium text-gray-300 hover:bg-gray-700"
              >
                Refresh Log
              </button>
            )}
          </div>

          {auditLoading && <p className="text-sm text-gray-400">Loading audit records...</p>}

          {!currentSessionId && (
            <div className="rounded-xl border border-gray-800 bg-gray-900/60 p-6 text-center text-xs text-gray-400">
              No active session selected. Start a procurement run to generate an explainable audit trail.
            </div>
          )}

          {auditEntries.length > 0 && (
            <div className="space-y-3">
              {auditEntries.map((entry) => (
                <div
                  key={entry.id}
                  className="rounded-lg border border-gray-800 bg-gray-900/60 p-4 text-xs shadow-sm"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="rounded bg-blue-500/10 px-2 py-0.5 font-bold uppercase tracking-wider text-blue-300">
                        {entry.action}
                      </span>
                      <span className="font-semibold text-white">{entry.actor}</span>
                    </div>
                    <span className="font-mono text-gray-500">{entry.timestamp}</span>
                  </div>
                  <p className="mt-2 text-gray-300 leading-relaxed">{entry.reason}</p>
                  {entry.details && (
                    <pre className="mt-2 overflow-x-auto rounded bg-gray-950 p-2 font-mono text-[11px] text-gray-400">
                      {JSON.stringify(entry.details, null, 2)}
                    </pre>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      )}
    </main>
  );
}
