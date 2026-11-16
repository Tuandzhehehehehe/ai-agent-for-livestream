'use client';

import React, { useEffect, useState } from 'react';

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://127.0.0.1:8000';

export default function LivestreamDashboardPage() {
  const [health, setHealth] = useState(null);
  const [architecture, setArchitecture] = useState(null);
  const [summary, setSummary] = useState(null);
  const [comments, setComments] = useState([]);
  const [agentInput, setAgentInput] = useState('What is the price and stock of the Aurora Ring Light?');
  const [agentResult, setAgentResult] = useState(null);
  const [ragQuery, setRagQuery] = useState('shipping policy delivery days');
  const [ragHits, setRagHits] = useState([]);
  const [loading, setLoading] = useState(false);

  const fetchOverview = async () => {
    try {
      const [hRes, aRes, sRes, cRes] = await Promise.all([
        fetch(`${API_BASE}/health`),
        fetch(`${API_BASE}/api/agent/architecture`),
        fetch(`${API_BASE}/api/plugin/summary`),
        fetch(`${API_BASE}/api/plugin/comments?limit=25`),
      ]);
      if (hRes.ok) setHealth(await hRes.json());
      if (aRes.ok) setArchitecture(await aRes.json());
      if (sRes.ok) setSummary(await sRes.json());
      if (cRes.ok) setComments(await cRes.json());
    } catch (_) {
      // Backend offline or unreachable
    }
  };

  useEffect(() => {
    fetchOverview();
    const timer = setInterval(fetchOverview, 4000);
    return () => clearInterval(timer);
  }, []);

  const runMultiAgent = async (e) => {
    e.preventDefault();
    if (!agentInput.trim()) return;
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/agent/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: agentInput, sender: 'DemoViewer', author_type: 'viewer' }),
      });
      if (res.ok) {
        setAgentResult(await res.json());
      }
    } finally {
      setLoading(false);
    }
  };

  const runVectorSearch = async (e) => {
    e.preventDefault();
    if (!ragQuery.trim()) return;
    const res = await fetch(`${API_BASE}/api/agent/rag/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: ragQuery, top_k: 4 }),
    });
    if (res.ok) {
      const data = await res.json();
      setRagHits(data.hits || []);
    }
  };

  return (
    <div style={{ maxWidth: 1280, margin: '0 auto', padding: '24px' }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24, color: '#c4b5fd' }}>
            Livestream Sales Multi-Agent & RAG Console
          </h1>
          <p style={{ margin: '4px 0 0', color: '#94a3b8', fontSize: 14 }}>
            LangGraph StateGraph • Vector RAG ({health?.vector_rag_backend || 'local_vector'}) • Ollama Function Calling
          </p>
        </div>
        <div style={{ display: 'flex', gap: 12, fontSize: 13 }}>
          <span style={{ padding: '6px 12px', borderRadius: 999, background: '#1e293b', border: '1px solid #475569' }}>
            AI: {health?.ai_status || 'connecting...'}
          </span>
          <span style={{ padding: '6px 12px', borderRadius: 999, background: '#1e293b', border: '1px solid #475569' }}>
            Mongo: {health?.mongo_status || 'n/a'} | Redis: {health?.redis_status || 'n/a'}
          </span>
        </div>
      </header>

      <section style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 24 }}>
        <div style={{ background: '#1e293b', padding: 16, borderRadius: 12, border: '1px solid #334155' }}>
          <div style={{ fontSize: 12, color: '#94a3b8' }}>Total Live Comments</div>
          <div style={{ fontSize: 28, fontWeight: 700 }}>{summary?.comment_count ?? 0}</div>
        </div>
        <div style={{ background: '#1e293b', padding: 16, borderRadius: 12, border: '1px solid #334155' }}>
          <div style={{ fontSize: 12, color: '#94a3b8' }}>Hot Leads (High Purchase Intent)</div>
          <div style={{ fontSize: 28, fontWeight: 700, color: '#34d399' }}>{summary?.hot_lead_count ?? 0}</div>
        </div>
        <div style={{ background: '#1e293b', padding: 16, borderRadius: 12, border: '1px solid #334155' }}>
          <div style={{ fontSize: 12, color: '#94a3b8' }}>Needs Human Review</div>
          <div style={{ fontSize: 28, fontWeight: 700, color: '#fbbf24' }}>{summary?.needs_human_review_count ?? 0}</div>
        </div>
        <div style={{ background: '#1e293b', padding: 16, borderRadius: 12, border: '1px solid #334155' }}>
          <div style={{ fontSize: 12, color: '#94a3b8' }}>Blocked by Moderation</div>
          <div style={{ fontSize: 28, fontWeight: 700, color: '#f87171' }}>{summary?.moderated_comment_count ?? 0}</div>
        </div>
      </section>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, marginBottom: 24 }}>
        <div style={{ background: '#1e293b', padding: 20, borderRadius: 12, border: '1px solid #334155' }}>
          <h2 style={{ marginTop: 0, fontSize: 18, color: '#ddd6fe' }}>Multi-Agent + Tool Calling Playground</h2>
          <form onSubmit={runMultiAgent} style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
            <input
              value={agentInput}
              onChange={(e) => setAgentInput(e.target.value)}
              placeholder="Ask a livestream customer question..."
              style={{ flex: 1, padding: '8px 12px', borderRadius: 8, border: '1px solid #475569', background: '#0f172a', color: '#fff' }}
            />
            <button
              type="submit"
              disabled={loading}
              style={{ padding: '8px 16px', borderRadius: 8, border: 0, background: '#7c3aed', color: '#fff', fontWeight: 600, cursor: 'pointer' }}
            >
              {loading ? 'Running...' : 'Run Agents'}
            </button>
          </form>

          {agentResult?.state && (
            <div style={{ fontSize: 13, display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ padding: 12, borderRadius: 8, background: '#0f172a', border: '1px solid #4c1d95' }}>
                <strong>Suggested Draft:</strong>
                <div style={{ marginTop: 4 }}>{agentResult.state.answer_draft?.answer}</div>
                <div style={{ marginTop: 6, color: '#a78bfa', fontSize: 12 }}>
                  Intent: {agentResult.state.intent} | Lead: {agentResult.state.lead_label} ({agentResult.state.lead_score}/100) | Tool: {agentResult.state.selected_tool || 'none'}
                </div>
              </div>
              <div>
                <strong>Agent Execution Trace:</strong>
                {(agentResult.state.agent_trace || []).map((step, i) => (
                  <div key={i} style={{ marginTop: 6, padding: '6px 10px', borderRadius: 6, background: '#0f172a' }}>
                    <span style={{ color: '#c4b5fd', fontWeight: 600 }}>{step.agent}</span> ({step.action}): {step.detail}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div style={{ background: '#1e293b', padding: 20, borderRadius: 12, border: '1px solid #334155' }}>
          <h2 style={{ marginTop: 0, fontSize: 18, color: '#ddd6fe' }}>Vector RAG Semantic Search (FAISS / Chroma / Cosine)</h2>
          <form onSubmit={runVectorSearch} style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
            <input
              value={ragQuery}
              onChange={(e) => setRagQuery(e.target.value)}
              placeholder="Search catalog, FAQs, policies by meaning..."
              style={{ flex: 1, padding: '8px 12px', borderRadius: 8, border: '1px solid #475569', background: '#0f172a', color: '#fff' }}
            />
            <button
              type="submit"
              style={{ padding: '8px 16px', borderRadius: 8, border: 0, background: '#2563eb', color: '#fff', fontWeight: 600, cursor: 'pointer' }}
            >
              Search RAG
            </button>
          </form>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 13 }}>
            {ragHits.map((hit, i) => (
              <div key={i} style={{ padding: 10, borderRadius: 8, background: '#0f172a', border: '1px solid #334155' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', color: '#93c5fd', fontWeight: 600 }}>
                  <span>[{hit.source_type}] {hit.title} ({hit.source_id})</span>
                  <span>score: {hit.score}</span>
                </div>
                <div style={{ marginTop: 4, color: '#cbd5e1' }}>{hit.excerpt}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <section style={{ background: '#1e293b', padding: 20, borderRadius: 12, border: '1px solid #334155', marginBottom: 24 }}>
        <h2 style={{ marginTop: 0, fontSize: 18, color: '#ddd6fe' }}>Multi-Agent Architecture Topology</h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12 }}>
          {(architecture?.agents || []).map((ag) => (
            <div key={ag.id} style={{ padding: 12, borderRadius: 8, background: '#0f172a', border: '1px solid #475569' }}>
              <div style={{ fontWeight: 600, color: '#c4b5fd', fontSize: 13 }}>{ag.name}</div>
              <div style={{ marginTop: 6, fontSize: 12, color: '#94a3b8' }}>{ag.role}</div>
            </div>
          ))}
        </div>
      </section>

      <section style={{ background: '#1e293b', padding: 20, borderRadius: 12, border: '1px solid #334155' }}>
        <h2 style={{ marginTop: 0, fontSize: 18, color: '#ddd6fe' }}>Real-Time Livestream Comments Feed</h2>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {comments.length === 0 && <div style={{ color: '#94a3b8' }}>No livestream comments received yet.</div>}
          {comments.map((c) => (
            <div key={c.comment_id} style={{ padding: 12, borderRadius: 8, background: '#0f172a', border: '1px solid #334155' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
                <strong>{c.sender}</strong>
                <span>
                  Intent: <code>{c.intent}</code> | Lead: <strong>{c.lead_label}</strong> ({c.lead_score})
                </span>
              </div>
              <div style={{ marginTop: 6 }}>{c.text}</div>
              {c.answer_draft?.answer && (
                <div style={{ marginTop: 8, padding: 8, borderRadius: 6, background: '#1e1b4b', color: '#ddd6fe', fontSize: 13 }}>
                  <strong>AI Draft:</strong> {c.answer_draft.answer}
                </div>
              )}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
