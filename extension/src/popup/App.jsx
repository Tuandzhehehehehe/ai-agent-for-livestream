import React, { useEffect, useState } from 'react';

const BACKEND_BASE = 'http://127.0.0.1:8000';

export default function PopupApp() {
  const [health, setHealth] = useState(null);
  const [summary, setSummary] = useState(null);
  const [orders, setOrders] = useState([]);
  const [comments, setComments] = useState([]);

  const refreshData = async () => {
    try {
      const [h, s, o, c] = await Promise.all([
        fetch(`${BACKEND_BASE}/health`).then((r) => r.json()),
        fetch(`${BACKEND_BASE}/api/plugin/summary`).then((r) => r.json()),
        fetch(`${BACKEND_BASE}/api/store/orders`).then((r) => r.json()),
        fetch(`${BACKEND_BASE}/api/plugin/comments?limit=3`).then((r) => r.json()),
      ]);
      setHealth(h);
      setSummary(s);
      setOrders(o.items || []);
      setComments(c || []);
    } catch (_) {
      setHealth(null);
    }
  };

  useEffect(() => {
    refreshData();
    const timer = setInterval(refreshData, 3000);
    return () => clearInterval(timer);
  }, []);

  const openDashboard = () => {
    chrome.tabs.create({ url: `${BACKEND_BASE}/dashboard` });
  };

  return (
    <div className="popup-root">
      <div className="header">
        <div>
          <h1 className="title">Livestream AI Assistant</h1>
          <div className="subtitle">React 18 • LangGraph • Vector RAG</div>
        </div>
        <span className={`status-pill ${health ? 'online' : 'offline'}`}>
          {health ? 'Backend Online' : 'Offline'}
        </span>
      </div>

      <div className="stats-grid">
        <div className="card">
          <div className="stat-label">Live Comments</div>
          <div className="stat-value">{summary?.comment_count ?? 0}</div>
        </div>
        <div className="card">
          <div className="stat-label">Hot Buyer Leads</div>
          <div className="stat-value" style={{ color: '#34d399' }}>
            {summary?.hot_lead_count ?? 0}
          </div>
        </div>
        <div className="card">
          <div className="stat-label">Total Orders</div>
          <div className="stat-value" style={{ color: '#a78bfa' }}>
            {orders.length}
          </div>
        </div>
        <div className="card">
          <div className="stat-label">Needs Review</div>
          <div className="stat-value" style={{ color: '#fbbf24' }}>
            {summary?.needs_human_review_count ?? 0}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="stat-label" style={{ marginBottom: 4 }}>
          Latest Live Comments & Auto-Orders
        </div>
        {comments.length === 0 && (
          <div style={{ fontSize: 12, color: '#64748b' }}>
            Waiting for YouTube Live chat messages...
          </div>
        )}
        {comments.map((c) => (
          <div key={c.comment_id} className="comment-item">
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <strong>{c.sender}</strong>
              <span>
                <span className="badge">{c.intent}</span>
                {c.auto_order && (
                  <span className="badge order">#{c.auto_order.order_id}</span>
                )}
              </span>
            </div>
            <div style={{ marginTop: 4, color: '#cbd5e1' }}>{c.text}</div>
          </div>
        ))}
      </div>

      <button type="button" className="btn" onClick={openDashboard}>
        Open React Operator Dashboard ↗
      </button>
    </div>
  );
}
