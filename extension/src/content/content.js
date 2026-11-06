(() => {
  const BUTTON_ID = 'livestream-ai-custom-button';
  const PANEL_ID = 'livestream-ai-settings-panel';
  const ROW_ID = 'livestream-ai-message-row';
  const POPOVER_ID = 'livestream-ai-detail-popover';
  const MAX_CHIPS = 50;

  const seenIds = new Set();
  const aiMessages = new Map();

  let isEnabled = false;
  let chatObserver = null;
  let structureObserver = null;
  let observedContainer = null;
  let observedRoot = null;
  let observedTicker = null;
  let aiRow = null;
  let popover = null;
  let selectedId = null;
  let chatRaf = null;
  let playerRaf = null;
  let playerBtn = null;

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  };

  const getInitials = (name) => {
    const parts = String(name || '').replace(/^@+/, '').trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return '?';
    return (parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : parts[0].slice(0, 2)).toUpperCase();
  };

  const createAvatar = (sender, avatarUrl) => {
    const wrap = el('span', 'livestream-ai-chip-avatar');
    wrap.appendChild(el('span', 'livestream-ai-chip-initials', getInitials(sender)));
    if (avatarUrl) {
      const img = el('img', 'livestream-ai-chip-avatar-img');
      img.src = avatarUrl;
      img.alt = '';
      img.loading = 'lazy';
      img.addEventListener('error', () => img.remove(), { once: true });
      wrap.appendChild(img);
    }
    return wrap;
  };

  const isSelectedComment = (d) =>
    Boolean(
      d &&
        d.moderation_status !== 'blocked' &&
        d.intent !== 'spam_or_irrelevant' &&
        d.intent !== 'prompt_injection' &&
        (d.answer_draft || (d.intent && d.intent !== 'unknown'))
    );

  const isOwnedNode = (node) =>
    node?.nodeType === 1 &&
    Boolean(node.closest?.(`#${ROW_ID}, #${POPOVER_ID}, #${BUTTON_ID}, #${PANEL_ID}`));

  function ensureAiRow() {
    if (aiRow?.id === ROW_ID) return aiRow;
    aiRow = document.getElementById(ROW_ID) || el('div');
    aiRow.id = ROW_ID;
    aiRow.setAttribute('role', 'region');
    aiRow.setAttribute('aria-label', 'AI selected live chat messages');
    aiRow.addEventListener(
      'wheel',
      (e) => {
        if (aiRow.scrollWidth > aiRow.clientWidth && Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
          aiRow.scrollLeft += e.deltaY;
          e.preventDefault();
          e.stopPropagation();
        }
      },
      { passive: false }
    );
    return aiRow;
  }

  function mountAiRow() {
    const root = document.querySelector('yt-live-chat-renderer');
    const ticker = root?.querySelector('yt-live-chat-ticker-renderer, #ticker') ||
      document.querySelector('yt-live-chat-ticker-renderer');
    if (ticker) {
      observedTicker = ticker;
      const row = ensureAiRow();
      if (ticker.nextElementSibling !== row) ticker.insertAdjacentElement('afterend', row);
      return;
    }
    const list = root?.querySelector('yt-live-chat-item-list-renderer, #chat') ||
      document.querySelector('yt-live-chat-item-list-renderer');
    if (list) {
      const row = ensureAiRow();
      if (list.previousElementSibling !== row) list.insertAdjacentElement('beforebegin', row);
    }
  }

  function closePopover() {
    selectedId = null;
    aiRow?.querySelectorAll('.livestream-ai-chip.is-active').forEach((c) => {
      c.classList.remove('is-active');
      c.setAttribute('aria-expanded', 'false');
    });
    if (popover) popover.hidden = true;
  }

  function openPopover(record) {
    if (!popover || !popover.isConnected) {
      popover = document.getElementById(POPOVER_ID) || el('div');
      popover.id = POPOVER_ID;
      popover.setAttribute('role', 'dialog');
      popover.addEventListener('click', (e) => e.stopPropagation());
      (document.querySelector('yt-live-chat-renderer') || document.body)?.appendChild(popover);
    }

    const header = el('div', 'livestream-ai-popover-header');
    const meta = el('div', 'livestream-ai-popover-meta');
    const badges = el('div', 'livestream-ai-popover-badges');
    badges.appendChild(el('span', 'livestream-ai-popover-badge', String(record.intent || 'selected').replace(/_/g, ' ')));

    if (record.needs_human_review) {
      badges.appendChild(el('span', 'livestream-ai-popover-badge is-review', 'Needs Review'));
    } else if (record.answer_draft?.status === 'answered') {
      badges.appendChild(el('span', 'livestream-ai-popover-badge is-answered', 'Draft Ready'));
    }

    meta.append(el('div', 'livestream-ai-popover-author', record.sender), badges);
    const closeBtn = el('button', 'livestream-ai-popover-close', '×');
    closeBtn.type = 'button';
    closeBtn.addEventListener('click', closePopover);
    header.append(createAvatar(record.sender, record.avatarUrl), meta, closeBtn);

    popover.replaceChildren(header, el('div', 'livestream-ai-popover-message', record.text));

    if (record.answer_draft?.answer) {
      const draftBox = el('div', 'livestream-ai-popover-section');
      draftBox.append(
        el('div', 'livestream-ai-popover-label', 'AI Suggested Draft'),
        el('div', 'livestream-ai-popover-draft', record.answer_draft.answer)
      );
      if (record.answer_draft.sources?.length) {
        const sources = el('div', 'livestream-ai-popover-sources');
        record.answer_draft.sources.forEach((s) =>
          sources.appendChild(el('span', 'livestream-ai-popover-source', `${s.title} (${s.source_id})`))
        );
        draftBox.appendChild(sources);
      }
      popover.appendChild(draftBox);
    }

    const reason = record.reason || record.answer_draft?.reason;
    if (reason) popover.appendChild(el('div', 'livestream-ai-popover-reason', reason));

    if (aiRow?.isConnected) {
      const rect = aiRow.getBoundingClientRect();
      popover.style.top = `${Math.max(4, Math.round(rect.bottom + 4))}px`;
      popover.style.left = `${Math.max(8, Math.round(rect.left + 8))}px`;
      popover.style.right = '8px';
    }
    popover.hidden = false;
  }

  function upsertChip(record) {
    mountAiRow();
    const row = ensureAiRow();
    const selector = `.livestream-ai-chip[data-comment-id="${CSS.escape(record.comment_id)}"]`;
    let chip = row.querySelector(selector);

    if (!chip) {
      chip = el('button', 'livestream-ai-chip');
      chip.type = 'button';
      chip.dataset.commentId = record.comment_id;
      chip.setAttribute('aria-expanded', 'false');
      chip.addEventListener('click', (e) => {
        e.stopPropagation();
        if (selectedId === record.comment_id) return closePopover();
        selectedId = record.comment_id;
        row.querySelectorAll('.livestream-ai-chip').forEach((c) => {
          const active = c.dataset.commentId === record.comment_id;
          c.classList.toggle('is-active', active);
          c.setAttribute('aria-expanded', String(active));
        });
        openPopover(aiMessages.get(record.comment_id));
      });
      row.appendChild(chip);
    }

    chip.title = `${record.sender}: ${record.text}`;
    chip.replaceChildren(
      createAvatar(record.sender, record.avatarUrl),
      el('span', 'livestream-ai-chip-author', record.sender),
      el('span', 'livestream-ai-chip-text', record.text)
    );

    while (aiMessages.size > MAX_CHIPS) {
      const oldestId = aiMessages.keys().next().value;
      aiMessages.delete(oldestId);
      row.querySelector(`.livestream-ai-chip[data-comment-id="${CSS.escape(oldestId)}"]`)?.remove();
      if (selectedId === oldestId) closePopover();
    }
  }

  function onComment(node) {
    if (!isEnabled || node.nodeType !== 1) return;
    if (!node.matches?.('yt-live-chat-text-message-renderer, yt-live-chat-paid-message-renderer')) return;

    const sender = node.querySelector('#author-name')?.innerText?.trim();
    const text = node.querySelector('#message')?.innerText?.trim();
    if (!sender || !text) return;

    const id = node.id || `${sender}:${text}:${Date.now()}`;
    if (seenIds.has(id)) return;
    seenIds.add(id);

    const authorType = node.getAttribute('author-type') || 'viewer';
    const avatarUrl = node.querySelector('#author-photo img, #img')?.getAttribute('src') || '';

    chrome.runtime?.sendMessage(
      { type: 'NEW_COMMENT', payload: { id, sender, text, authorType, timestamp: Date.now() } },
      (res) => {
        if (chrome.runtime?.lastError || !isEnabled || !res?.success || !isSelectedComment(res.data)) return;
        const cid = String(res.data.comment_id || '');
        if (!cid) return;
        const record = { ...aiMessages.get(cid), ...res.data, avatarUrl: avatarUrl || aiMessages.get(cid)?.avatarUrl || '' };
        aiMessages.set(cid, record);
        upsertChip(record);
        if (selectedId === cid && popover && !popover.hidden) openPopover(record);
      }
    );
  }

  function syncChatDom() {
    chatRaf = null;
    if (!isEnabled) return;

    const container = document.querySelector('yt-live-chat-item-list-renderer #items, #item-scroller #items');
    if (container && observedContainer !== container) {
      chatObserver?.disconnect();
      observedContainer = container;
      chatObserver = new MutationObserver((mutations) => {
        if (!isEnabled) return;
        for (const m of mutations) m.addedNodes.forEach(onComment);
      });
      chatObserver.observe(container, { childList: true });
    }

    mountAiRow();

    const root = document.querySelector('yt-live-chat-renderer, yt-live-chat-app');
    if (root && structureObserver && observedRoot !== root) {
      structureObserver.disconnect();
      observedRoot = root;
      structureObserver.observe(root, { childList: true, subtree: true });
    }
  }

  function startChatCollection() {
    if (window === window.top && !window.location.pathname.startsWith('/live_chat') && !document.querySelector('yt-live-chat-app, yt-live-chat-renderer')) {
      return;
    }
    syncChatDom();
    if (structureObserver) return;

    structureObserver = new MutationObserver((mutations) => {
      if (!isEnabled || !mutations.some((m) => !isOwnedNode(m.target))) return;
      if (observedContainer?.isConnected && observedTicker?.isConnected && observedTicker.nextElementSibling === aiRow) {
        return;
      }
      if (chatRaf === null) chatRaf = requestAnimationFrame(syncChatDom);
    });

    observedRoot = document.querySelector('yt-live-chat-renderer, yt-live-chat-app') || document.body || document.documentElement;
    if (observedRoot) structureObserver.observe(observedRoot, { childList: true, subtree: true });
  }

  function stopChatCollection() {
    if (chatRaf !== null) cancelAnimationFrame(chatRaf);
    chatRaf = null;
    chatObserver?.disconnect();
    structureObserver?.disconnect();
    chatObserver = null;
    structureObserver = null;
    observedContainer = null;
    observedRoot = null;
    observedTicker = null;
    closePopover();
  }

  function setAssistantEnabled(nextState, { broadcast = false } = {}) {
    const normalized = Boolean(nextState);
    const changed = isEnabled !== normalized;
    isEnabled = normalized;

    document.querySelector(`#${PANEL_ID} .livestream-ai-menuitem`)?.setAttribute('aria-checked', String(isEnabled));
    if (isEnabled) startChatCollection();
    else if (changed) stopChatCollection();

    if (broadcast && changed) {
      chrome.runtime?.sendMessage({ type: 'SET_ENABLED', enabled: isEnabled });
      document.querySelector('iframe#chatframe, ytd-live-chat-frame iframe')?.contentWindow?.postMessage(
        { source: 'livestream-ai-extension', type: 'AI_STATE_CHANGED', enabled: isEnabled },
        '*'
      );
    }
  }

  chrome.runtime?.onMessage?.addListener((req) => {
    if (req?.type === 'AI_STATE_CHANGED') setAssistantEnabled(req.enabled);
  });

  window.addEventListener('message', (e) => {
    if (e.data?.source === 'livestream-ai-extension' && e.data?.type === 'AI_STATE_CHANGED') {
      setAssistantEnabled(e.data.enabled);
    }
  });

  document.addEventListener('click', (e) => {
    if (selectedId && popover && !popover.hidden && !popover.contains(e.target) && !aiRow?.contains(e.target)) {
      closePopover();
    }
    const panel = document.getElementById(PANEL_ID);
    if (panel?.classList.contains('is-open') && !panel.contains(e.target) && !playerBtn?.contains(e.target)) {
      panel.classList.remove('is-open');
      playerBtn?.setAttribute('aria-expanded', 'false');
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && selectedId) closePopover();
  });

  if (window === window.top && !window.location.pathname.startsWith('/live_chat')) {
    chrome.runtime?.sendMessage({ type: 'SET_ENABLED', enabled: false });
  } else {
    chrome.runtime?.sendMessage({ type: 'GET_ENABLED' }, (res) => {
      if (!chrome.runtime.lastError && res?.enabled) setAssistantEnabled(true);
    });
  }

  function addPlayerButton() {
    playerRaf = null;
    if (playerBtn?.isConnected) return;
    playerBtn = document.getElementById(BUTTON_ID);
    if (playerBtn) return;

    const controls = document.querySelector('.ytp-right-controls');
    const settingsBtn = controls?.querySelector('.ytp-settings-button');
    const playerContainer = controls?.closest('.html5-video-player') || controls?.parentElement;
    if (!settingsBtn || !playerContainer) return;

    const button = el('button', 'ytp-button');
    button.id = BUTTON_ID;
    button.type = 'button';
    button.title = 'Livestream AI Assistant';
    button.setAttribute('aria-label', 'Livestream AI Assistant');
    button.setAttribute('aria-haspopup', 'true');
    button.setAttribute('aria-expanded', 'false');

    const nativeSvg = settingsBtn.querySelector('svg');
    if (nativeSvg) {
      const cloned = nativeSvg.cloneNode(true);
      const path = cloned.querySelector('[id]');
      if (path) {
        path.id = `${path.id}-custom-ai`;
        const use = cloned.querySelector('use');
        use?.setAttribute('xlink:href', `#${path.id}`);
        use?.setAttribute('href', `#${path.id}`);
      }
      button.appendChild(cloned);
    }
    button.appendChild(el('span', 'livestream-ai-badge', 'AI'));

    let panel = document.getElementById(PANEL_ID);
    if (!panel) {
      panel = el('div');
      panel.id = PANEL_ID;
      panel.setAttribute('role', 'menu');

      const item = el('div', 'livestream-ai-menuitem');
      item.setAttribute('role', 'menuitemcheckbox');
      item.setAttribute('aria-checked', String(isEnabled));
      item.tabIndex = 0;

      const track = el('div', 'livestream-ai-toggle-track');
      track.appendChild(el('div', 'livestream-ai-toggle-thumb'));
      item.append(el('div', 'livestream-ai-menuitem-label', 'Enable'), track);

      const toggle = (e) => {
        e.stopPropagation();
        setAssistantEnabled(!isEnabled, { broadcast: true });
      };
      item.addEventListener('click', toggle);
      item.addEventListener('keydown', (e) => {
        if (e.key === ' ' || e.key === 'Enter') {
          e.preventDefault();
          toggle(e);
        }
      });
      panel.addEventListener('click', (e) => e.stopPropagation());
      panel.appendChild(item);
      playerContainer.appendChild(panel);
    }

    button.addEventListener('click', (e) => {
      e.stopPropagation();
      const willOpen = !panel.classList.contains('is-open');
      if (willOpen && settingsBtn.getAttribute('aria-expanded') === 'true') settingsBtn.click();
      panel.classList.toggle('is-open', willOpen);
      button.setAttribute('aria-expanded', String(willOpen));
    });

    settingsBtn.addEventListener('click', () => {
      panel.classList.remove('is-open');
      button.setAttribute('aria-expanded', 'false');
    });

    settingsBtn.insertAdjacentElement('beforebegin', button);
    playerBtn = button;
  }

  if (window === window.top && !window.location.pathname.startsWith('/live_chat')) {
    const schedule = () => {
      if (!playerBtn?.isConnected && playerRaf === null) playerRaf = requestAnimationFrame(addPlayerButton);
    };
    new MutationObserver(schedule).observe(document.documentElement, { childList: true, subtree: true });
    window.addEventListener('yt-navigate-finish', schedule);
    addPlayerButton();
  }
})();
