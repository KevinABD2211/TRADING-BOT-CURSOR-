const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

export type Health = { status: string; api: string };

export type Stats = {
  raw_messages_total: number;
  signals_total: number;
  signals_actionable: number;
  executions_total: number;
  execution_mode: string;
  environment: string;
};

export type SignalItem = {
  id: string;
  symbol: string;
  asset_type: string;
  direction: string;
  entry_price: number | null;
  stop_loss: number | null;
  take_profit_1: number | null;
  take_profit_2: number | null;
  take_profit_3: number | null;
  leverage: number | null;
  timeframe: string | null;
  signal_timestamp: string | null;
  parsed_at: string | null;
  parse_method: string;
  source: string;
  signal_completeness_pct: number | null;
  llm_confidence: number | null;
  confidence_wording: string | null;
  risk_reward_ratio: number | null;
  raw_text_preview: string;
  discord_author_name: string | null;
  discord_message_link: string | null;
};

export type SignalsResponse = { signals: SignalItem[]; total: number };

export type MarketSignalItem = {
  id: string;
  symbol: string;
  direction: string;
  entry_price: number | null;
  price_change_pct: number | null;
  confidence_pct: number | null;
  raw_text_preview: string;
  signal_timestamp: string | null;
  parse_method: string;
  source: string;
};

export type MarketSignalsResponse = {
  signals: MarketSignalItem[];
  total: number;
  source: string;
};

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${path}: ${res.status} ${text}`);
  }
  return res.json() as Promise<T>;
}

export async function getHealth(): Promise<Health> {
  return fetchApi<Health>("/health");
}

export async function getStats(): Promise<Stats> {
  return fetchApi<Stats>("/api/stats");
}

export async function getSignals(params: {
  limit?: number;
  offset?: number;
  symbol?: string;
  direction?: string;
  source?: string;
  min_confidence?: number;
}): Promise<SignalsResponse> {
  const sp = new URLSearchParams();
  if (params.limit != null) sp.set("limit", String(params.limit));
  if (params.offset != null) sp.set("offset", String(params.offset));
  if (params.symbol) sp.set("symbol", params.symbol);
  if (params.direction) sp.set("direction", params.direction);
  if (params.source) sp.set("source", params.source);
  if (params.min_confidence != null) sp.set("min_confidence", String(params.min_confidence));
  const q = sp.toString();
  return fetchApi<SignalsResponse>(`/api/signals${q ? `?${q}` : ""}`);
}

export async function getSignalsUnified(params?: { limit?: number }): Promise<SignalsResponse> {
  const limit = params?.limit ?? 50;
  return fetchApi<SignalsResponse>(`/api/signals/unified?limit=${limit}`);
}

export async function seedDemoSignals(): Promise<{ seeded: number; message: string }> {
  return fetchApi<{ seeded: number; message: string }>("/api/seed-demo", { method: "POST" });
}

export type ResearchConfidence = { confidence_pct: number; rationale: string; sources: string[] };

export async function getResearchConfidence(params: {
  symbol: string;
  direction?: string;
  signal_summary?: string;
}): Promise<ResearchConfidence> {
  const sp = new URLSearchParams();
  sp.set("symbol", params.symbol);
  if (params.direction) sp.set("direction", params.direction);
  if (params.signal_summary) sp.set("signal_summary", params.signal_summary);
  return fetchApi<ResearchConfidence>(`/api/research/confidence?${sp.toString()}`);
}

export type SourceCount = { source: string; count: number };
export type SourcesResponse = { sources: SourceCount[] };

export async function getSources(): Promise<SourcesResponse> {
  return fetchApi<SourcesResponse>("/api/sources");
}

export async function getMarketSignals(params?: { limit?: number }): Promise<MarketSignalsResponse> {
  const limit = params?.limit ?? 50;
  return fetchApi<MarketSignalsResponse>(`/api/market-signals?limit=${limit}`);
}

export type ExecutionItem = {
  id: string;
  symbol: string;
  direction: string;
  side: string;
  quantity: number | null;
  price: number | null;
  notional_usd: number | null;
  status: string;
  broker: string;
  executed_at: string | null;
  created_at: string | null;
  notes: string | null;
};

export type ExecutionsResponse = { executions: ExecutionItem[]; total: number };

export async function getExecutions(params?: {
  limit?: number;
  offset?: number;
  symbol?: string;
  status?: string;
}): Promise<ExecutionsResponse> {
  const sp = new URLSearchParams();
  if (params?.limit != null) sp.set("limit", String(params.limit));
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.symbol) sp.set("symbol", params.symbol);
  if (params?.status) sp.set("status", params.status);
  const q = sp.toString();
  return fetchApi<ExecutionsResponse>(`/api/executions${q ? `?${q}` : ""}`);
}

export async function createExecution(body: {
  symbol: string;
  direction: string;
  quantity?: number;
  notional_usd?: number;
  price?: number;
  notes?: string;
}): Promise<ExecutionItem> {
  return fetchApi<ExecutionItem>("/api/executions", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// Alpaca: suggestions & execute
export type AlpacaAccount = {
  configured: boolean;
  environment?: string;
  buying_power?: string;
  cash?: string;
  portfolio_value?: string;
};

export type AlpacaSuggestion = {
  signal_id: string;
  symbol: string;
  direction: string;
  entry_price: number | null;
  current_price: number | null;
  suggested_notional_usd: number;
  suggested_qty: number | null;
  source: string;
  confidence_pct: number | null;
  stop_loss: number | null;
  take_profit_1: number | null;
};

export type AlpacaSuggestionsResponse = {
  suggestions: AlpacaSuggestion[];
  total: number;
  alpaca_configured: boolean;
};

export async function getAlpacaAccount(): Promise<AlpacaAccount> {
  return fetchApi<AlpacaAccount>("/api/alpaca/account");
}

export async function getAlpacaSuggestions(params?: { limit?: number }): Promise<AlpacaSuggestionsResponse> {
  const limit = params?.limit ?? 30;
  return fetchApi<AlpacaSuggestionsResponse>(`/api/alpaca/suggestions?limit=${limit}`);
}

export async function alpacaExecute(body: {
  symbol: string;
  direction: string;
  quantity?: number;
  notional_usd?: number;
  signal_id?: string;
}): Promise<ExecutionItem> {
  return fetchApi<ExecutionItem>("/api/alpaca/execute", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
