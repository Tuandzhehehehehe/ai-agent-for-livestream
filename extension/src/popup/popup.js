const BACKEND_BASE = 'http://127.0.0.1:8000';

function createNode(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  Object.entries(props || {}).forEach(([k, v]) => {
    if (v == null || v === false) return;
    if (k === 'className') el.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k.startsWith('on') && typeof v === 'function') {
      el.addEventListener(k.slice(2).toLowerCase(), v);
    } else {
      el.setAttribute(k, String(v));
    }
  });
  children.flat().forEach((child) => {
    if (child == null || child === false) return;
    el.appendChild(
      child instanceof Node ? child : document.createTextNode(String(child))
    );
  });
  return el;
}

function sendExtensionMessage(msg) {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(msg, (res) => resolve(res || {}));
    } catch (_) {
      resolve({});
    }
  });
}

function mountPopupApp(rootEl) {
  let state = {
    health: null,
    summary: null,
    orders: [],
    comments: [],
    collectorEnabled: false,
    autoReplyEnabled: false,
  };

  const toggleCollector = async () => {
    const next = !state.collectorEnabled;
    state = { ...state, collectorEnabled: next };
    render();
    await sendExtensionMessage({ type: 'SET_ENABLED', enabled: next });
  };

  const toggleAutoReply = async () => {
    const next = !state.autoReplyEnabled;
    state = { ...state, autoReplyEnabled: next };
    render();
    await sendExtensionMessage({ type: 'SET_AUTO_REPLY', autoReply: next });
  };

  const render = () => {
    const {
      health,
      summary,
      orders,
      comments,
      collectorEnabled,
      autoReplyEnabled,
    } = state;
    const view = createNode(
      'div',
      { className: 'popup-root' },
      createNode(
        'div',
        { className: 'header' },
        createNode(
          'div',
          {},
          createNode('h1', { className: 'title' }, 'Livestream AI Assistant'),
          createNode(
            'div',
            { className: 'subtitle' },
            'React 18 • LangGraph • Vector RAG'
          )
        ),
        createNode(
          'span',
          { className: `status-pill ${health ? 'online' : 'offline'}` },
          health ? 'Backend Online' : 'Offline'
        )
      ),
      createNode(
        'div',
        {
          className: 'card',
          style: { display: 'flex', flexDirection: 'column', gap: '8px' },
        },
        createNode(
          'div',
          { className: 'stat-label' },
          'YouTube Live Controls (Watch / Popout / Studio)'
        ),
        createNode(
          'div',
          { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' } },
          createNode(
            'button',
            {
              type: 'button',
              className: 'btn',
              style: {
                background: collectorEnabled ? '#10b981' : '#334155',
                padding: '8px 10px',
                fontSize: '12px',
              },
              onClick: toggleCollector,
            },
            collectorEnabled ? 'Collector: ON' : 'Collector: OFF'
          ),
          createNode(
            'button',
            {
              type: 'button',
              className: 'btn',
              style: {
                background: autoReplyEnabled ? '#7c3aed' : '#334155',
                padding: '8px 10px',
                fontSize: '12px',
              },
              onClick: toggleAutoReply,
            },
            autoReplyEnabled ? 'Auto-Reply: ON' : 'Auto-Reply: OFF'
          )
        )
      ),
      createNode(
        'div',
        { className: 'stats-grid' },
        createNode(
          'div',
          { className: 'card' },
          createNode('div', { className: 'stat-label' }, 'Live Comments'),
          createNode('div', { className: 'stat-value' }, summary?.comment_count ?? 0)
        ),
        createNode(
          'div',
          { className: 'card' },
          createNode('div', { className: 'stat-label' }, 'Hot Buyer Leads'),
          createNode(
            'div',
            { className: 'stat-value', style: { color: '#34d399' } },
            summary?.hot_lead_count ?? 0
          )
        ),
        createNode(
          'div',
          { className: 'card' },
          createNode('div', { className: 'stat-label' }, 'Total Orders'),
          createNode(
            'div',
            { className: 'stat-value', style: { color: '#a78bfa' } },
            orders.length
          )
        ),
        createNode(
          'div',
          { className: 'card' },
          createNode('div', { className: 'stat-label' }, 'Needs Review'),
          createNode(
            'div',
            { className: 'stat-value', style: { color: '#fbbf24' } },
            summary?.needs_human_review_count ?? 0
          )
        )
      ),
      createNode(
        'div',
        { className: 'card' },
        createNode(
          'div',
          { className: 'stat-label', style: { marginBottom: '4px' } },
          'Latest Live Comments & Auto-Orders'
        ),
        comments.length === 0
          ? createNode(
              'div',
              { style: { fontSize: '12px', color: '#64748b' } },
              'Waiting for YouTube Live chat messages...'
            )
          : comments.map((c) =>
              createNode(
                'div',
                { className: 'comment-item' },
                createNode(
                  'div',
                  { style: { display: 'flex', justifyContent: 'space-between' } },
                  createNode('strong', {}, c.sender),
                  createNode(
                    'span',
                    {},
                    createNode('span', { className: 'badge' }, c.intent),
                    c.auto_order
                      ? createNode(
                          'span',
                          { className: 'badge order' },
                          `#${c.auto_order.order_id}`
                        )
                      : null
                  )
                ),
                createNode(
                  'div',
                  { style: { marginTop: '4px', color: '#cbd5e1' } },
                  c.text
                )
              )
            )
      ),
      createNode(
        'button',
        {
          type: 'button',
          className: 'btn',
          onClick: () => chrome.tabs.create({ url: `${BACKEND_BASE}/dashboard` }),
        },
        'Open React Operator Dashboard ↗'
      )
    );
    rootEl.replaceChildren(view);
  };

  const refreshData = async () => {
    try {
      const toggleRes = await sendExtensionMessage({ type: 'GET_ENABLED' });
      const [h, s, o, c] = await Promise.all([
        fetch(`${BACKEND_BASE}/health`).then((r) => r.json()),
        fetch(`${BACKEND_BASE}/api/plugin/summary`).then((r) => r.json()),
        fetch(`${BACKEND_BASE}/api/store/orders`).then((r) => r.json()),
        fetch(`${BACKEND_BASE}/api/plugin/comments?limit=3`).then((r) => r.json()),
      ]);
      state = {
        health: h,
        summary: s,
        orders: o.items || [],
        comments: Array.isArray(c) ? c : [],
        collectorEnabled:
          typeof toggleRes.enabled === 'boolean'
            ? toggleRes.enabled
            : state.collectorEnabled,
        autoReplyEnabled:
          typeof toggleRes.autoReply === 'boolean'
            ? toggleRes.autoReply
            : state.autoReplyEnabled,
      };
    } catch (_) {
      state = { ...state, health: null };
    }
    render();
  };

  render();
  refreshData();
  setInterval(refreshData, 3000);
}

const root = document.getElementById('popup-root');
if (root) {
  mountPopupApp(root);
}
