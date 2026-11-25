
(() => {
  const BUTTON_ID = 'livestream-ai-custom-button';
  const PANEL_ID = 'livestream-ai-settings-panel';
  const CHAT_TOOLBAR_ID = 'livestream-ai-chat-toolbar';
  const ROW_ID = 'livestream-ai-message-row';
  const POPOVER_ID = 'livestream-ai-detail-popover';
  const MAX_CHIPS = 50;

  const seenIds = new Set();
  const aiMessages = new Map();
  const sentAiTexts = new Set();
  const sentAiSignatures = new Set();
  const autoRepliedIds = new Set();
  const chatSendQueue = [];
  const CHAT_SEND_COOLDOWN_MS = 1800;

  let isEnabled = false;
  let isAutoReplyEnabled = false;
  let isProcessingChatQueue = false;
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

  function truncateAtWordBoundary(str, maxLen = 195) {
    if (str.length <= maxLen) return str;
    const limit = maxLen - 1;
    const sliced = str.slice(0, limit);
    const lastSpace = sliced.lastIndexOf(' ');
    if (lastSpace >= Math.floor(maxLen * 0.6)) {
      return `${sliced.slice(0, lastSpace).replace(/[,;:\s]+$/, '')}…`;
    }
    return `${sliced}…`;
  }

  function normalizeReplySignature(raw) {
    return String(raw || '')
      .replace(/🤖/g, '')
      .replace(/^\[AI\]\s*/i, '')
      .replace(/^@[A-Za-z0-9_.-]+\s+/i, '')
      .replace(/…|\.\.\./g, '')
      .replace(/\s+/g, ' ')
      .trim()
      .toLowerCase();
  }

  function rememberSentAiText(text) {
    const trimmed = String(text || '').trim();
    if (!trimmed) return;
    sentAiTexts.add(trimmed);
    const withoutEmoji = trimmed.replace(/^🤖\s*/, '').trim();
    if (withoutEmoji) {
      sentAiTexts.add(withoutEmoji);
    }
    const sig = normalizeReplySignature(trimmed);
    if (sig) {
      sentAiSignatures.add(sig);
      if (sig.length > 48) {
        sentAiSignatures.add(sig.slice(0, 48));
      }
    }
  }

  function extractCommentText(messageEl) {
    if (!messageEl) return '';
    let out = '';
    const walk = (node) => {
      if (!node) return;
      if (node.nodeType === 3) {
        out += node.nodeValue || '';
      } else if (node.nodeType === 1) {
        if (node.tagName === 'IMG') {
          out += node.getAttribute('alt') || node.getAttribute('data-emoji-id') || '';
        } else {
          node.childNodes.forEach(walk);
        }
      }
    };
    walk(messageEl);
    return out.replace(/\s+/g, ' ').trim();
  }

  function isBotOrEchoComment(node, text, authorType) {
    const normAuthor = String(authorType || '').toLowerCase();
    if (normAuthor === 'owner' || normAuthor === 'moderator') {
      return true;
    }
    if (
      node.hasAttribute?.('author-is-owner') ||
      node.querySelector?.(
        'yt-live-chat-author-badge-renderer[type="owner"], yt-live-chat-author-badge-renderer[type="moderator"], #author-name.owner, #author-name.moderator'
      )
    ) {
      return true;
    }
    if (node.querySelector?.('#message img[alt*="🤖"]')) {
      return true;
    }
    if (
      text.startsWith('🤖') ||
      text.startsWith('[AI]') ||
      sentAiTexts.has(text)
    ) {
      return true;
    }
    const sig = normalizeReplySignature(text);
    if (sig && (sentAiSignatures.has(sig) || (sig.length > 48 && sentAiSignatures.has(sig.slice(0, 48))))) {
      return true;
    }
    if (
      /(?:Order\s+#ORD-(?:LIVE|DEMO)-\d+\s+created!|Đã\s+tạo\s+đơn\s+#ORD-(?:LIVE|DEMO)-\d+|Đã\s+cập\s+nhật\s+đơn\s+#ORD-(?:LIVE|DEMO)-\d+|Draft\s*\(synthetic\s+demo\s+data\)|Bản\s+nháp\s*\(dữ\s+liệu\s+mô\s+phỏng\)|the\s+catalog\s+lists\s+\d+\s+simulated\s+units|có\s+giá\s+niêm\s+yết\s+là\s+\$\d)/i.test(
        text
      )
    ) {
      return true;
    }
    return false;
  }

  function formatPublicChatReply(sender, rawAnswer) {
    const handle = String(sender || '').replace(/^@+/, '').trim();
    const cleaned = String(rawAnswer || '')
      .replace(/^(?:Draft|Bản nháp)\s*(?:\([^)]*\))?\s*:\s*/i, '')
      .replace(/\s*This is a fictional demo price\.?/gi, '')
      .replace(/\s*This is not live inventory\.?/gi, '')
      .replace(/\s+/g, ' ')
      .trim();
    if (!cleaned) return '';
    const prefix = handle ? `🤖 @${handle} ` : '🤖 ';
    const full = `${prefix}${cleaned}`;
    return truncateAtWordBoundary(full, 195);
  }

  function updateCommentSendStatus(commentId, status) {
    if (!commentId || !aiMessages.has(commentId)) return;
    const rec = aiMessages.get(commentId);
    rec.chat_send_status = status;
    aiMessages.set(commentId, rec);
    upsertChip(rec);
    if (selectedId === commentId && popover && !popover.hidden) {
      openPopover(rec);
    }
  }

  function resolveChatDocument() {
    if (document.querySelector('yt-live-chat-message-input-renderer, yt-live-chat-renderer')) {
      return document;
    }
    try {
      const frame = document.querySelector('iframe#chatframe, ytd-live-chat-frame iframe');
      if (frame?.contentDocument) {
        return frame.contentDocument;
      }
    } catch (_) {}
    return document;
  }

  function setEditableTextModern(chatDoc, inputEl, text) {
    inputEl.focus();
    inputEl.dispatchEvent(
      new InputEvent('beforeinput', {
        bubbles: true,
        cancelable: true,
        composed: true,
        data: text,
        inputType: 'insertText'
      })
    );
    inputEl.textContent = text;
    try {
      const sel = chatDoc.getSelection?.();
      if (sel) {
        const range = chatDoc.createRange();
        range.selectNodeContents(inputEl);
        range.collapse(false);
        sel.removeAllRanges();
        sel.addRange(range);
      }
    } catch (_) {}
    inputEl.dispatchEvent(
      new InputEvent('input', {
        bubbles: true,
        cancelable: true,
        composed: true,
        data: text,
        inputType: 'insertText'
      })
    );
    inputEl.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
    inputEl.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
    inputEl.dispatchEvent(
      new KeyboardEvent('keyup', {
        key: ' ',
        code: 'Space',
        bubbles: true,
        composed: true
      })
    );
  }

  function processNextQueuedChatReply() {
    if (isProcessingChatQueue || !chatSendQueue.length) return;
    if (!isEnabled || !isAutoReplyEnabled) {
      chatSendQueue.length = 0;
      return;
    }

    const chatDoc = resolveChatDocument();
    const restricted = chatDoc.querySelector(
      'yt-live-chat-restricted-participation-renderer'
    );
    const inputRenderer = chatDoc.querySelector('yt-live-chat-message-input-renderer');
    const inputEl =
      inputRenderer?.querySelector(
        'yt-live-chat-text-input-field-renderer #input[contenteditable], #input[contenteditable]'
      ) ||
      chatDoc.querySelector('yt-live-chat-text-input-field-renderer #input[contenteditable]');

    if (restricted || !inputEl || inputEl.getAttribute('contenteditable') === 'false') {
      const failedItem = chatSendQueue.shift();
      if (failedItem?.commentId) {
        updateCommentSendStatus(failedItem.commentId, 'blocked_or_restricted');
      }
      if (chatSendQueue.length) {
        setTimeout(processNextQueuedChatReply, CHAT_SEND_COOLDOWN_MS);
      }
      return;
    }

    const existingDraft = (inputEl.textContent || '').trim();
    if (
      chatDoc.activeElement === inputEl &&
      existingDraft.length > 0 &&
      !sentAiTexts.has(existingDraft)
    ) {
      setTimeout(processNextQueuedChatReply, 1500);
      return;
    }

    isProcessingChatQueue = true;
    const { commentId, text } = chatSendQueue.shift();
    rememberSentAiText(text);
    setEditableTextModern(chatDoc, inputEl, text);

    setTimeout(() => {
      const sendButton =
        inputRenderer?.querySelector(
          '#send-button button, yt-button-renderer#send-button button, yt-icon-button#button button'
        ) ||
        chatDoc.querySelector('yt-live-chat-message-input-renderer #send-button button');

      const canClickSend = Boolean(
        sendButton &&
        !sendButton.disabled &&
        sendButton.getAttribute('aria-disabled') !== 'true'
      );

      if (canClickSend) {
        sendButton.click();
      } else {
        inputEl.dispatchEvent(
          new KeyboardEvent('keydown', {
            key: 'Enter',
            code: 'Enter',
            keyCode: 13,
            which: 13,
            bubbles: true,
            cancelable: true,
            composed: true
          })
        );
      }

      let attempts = 0;
      const maxAttempts = 6;
      const verifySent = () => {
        attempts += 1;
        const remaining = (inputEl.textContent || '').trim();
        const errorToast = chatDoc.querySelector('yt-live-chat-toast-renderer[is-showing]');
        if (remaining === '' && !errorToast) {
          updateCommentSendStatus(commentId, 'sent');
          isProcessingChatQueue = false;
          if (chatSendQueue.length) {
            setTimeout(processNextQueuedChatReply, CHAT_SEND_COOLDOWN_MS);
          }
          return;
        }
        if (attempts < maxAttempts && !errorToast) {
          if (attempts === 2) {
            const retryBtn =
              inputRenderer?.querySelector(
                '#send-button button, yt-button-renderer#send-button button, yt-icon-button#button button'
              ) ||
              chatDoc.querySelector('yt-live-chat-message-input-renderer #send-button button');
            if (retryBtn && !retryBtn.disabled && retryBtn.getAttribute('aria-disabled') !== 'true') {
              retryBtn.click();
            }
          }
          setTimeout(verifySent, 220);
          return;
        }
        if (remaining === text) {
          inputEl.textContent = '';
          inputEl.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true }));
        }
        updateCommentSendStatus(commentId, errorToast ? 'blocked_or_restricted' : 'failed');
        isProcessingChatQueue = false;
        if (chatSendQueue.length) {
          setTimeout(processNextQueuedChatReply, CHAT_SEND_COOLDOWN_MS);
        }
      };
      setTimeout(verifySent, 250);
    }, 150);
  }

  function enqueueYouTubeLiveChatReply(commentId, messageText) {
    const text = String(messageText || '').trim();
    if (!text || !isEnabled || !isAutoReplyEnabled) return false;
    if (chatSendQueue.some((item) => item.commentId === commentId || item.text === text)) {
      return false;
    }
    rememberSentAiText(text);
    updateCommentSendStatus(commentId, 'queued');
    chatSendQueue.push({ commentId, text });
    processNextQueuedChatReply();
    return true;
  }

  // Safely communicate with the extension runtime.
  function sendRuntimeMessage(message, callback) {
    try {
      if (!chrome.runtime?.id) return;

      chrome.runtime.sendMessage(message, (response) => {
        try {
          const error = chrome.runtime.lastError;
          if (callback) callback(response, error || null);
        } catch (_) {
          // Extension context may have been invalidated.
        }
      });
    } catch (_) {
      // Extension context is no longer available.
    }
  }

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  };

  const getInitials = (name) => {
    const parts = String(name || '')
      .replace(/^@+/, '')
      .trim()
      .split(/\s+/)
      .filter(Boolean);

    if (!parts.length) return '?';

    return (
      parts.length > 1
        ? parts[0][0] + parts[parts.length - 1][0]
        : parts[0].slice(0, 2)
    ).toUpperCase();
  };

  const createAvatar = (sender, avatarUrl) => {
    const wrap = el('span', 'livestream-ai-chip-avatar');

    wrap.appendChild(
      el('span', 'livestream-ai-chip-initials', getInitials(sender))
    );

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
    Boolean(
      node.closest?.(
        `#${ROW_ID}, #${POPOVER_ID}, #${BUTTON_ID}, #${PANEL_ID}, #${CHAT_TOOLBAR_ID}`
      )
    );

  function ensureChatToolbar() {
    const root = document.querySelector('yt-live-chat-renderer');
    if (!root) return null;

    let toolbar = document.getElementById(CHAT_TOOLBAR_ID);
    if (!toolbar) {
      toolbar = el('div');
      toolbar.id = CHAT_TOOLBAR_ID;
      toolbar.setAttribute('role', 'toolbar');
      toolbar.setAttribute('aria-label', 'Livestream AI Assistant Controls');

      const title = el('span', 'livestream-ai-toolbar-brand', 'AI Live');

      const collectorBtn = el('button', 'livestream-ai-toolbar-toggle');
      collectorBtn.type = 'button';
      collectorBtn.dataset.toggle = 'collector';
      collectorBtn.setAttribute('aria-pressed', String(isEnabled));
      collectorBtn.textContent = isEnabled ? 'Collector: ON' : 'Collector: OFF';
      collectorBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        setAssistantEnabled(!isEnabled, { broadcast: true });
      });

      const autoReplyBtn = el('button', 'livestream-ai-toolbar-toggle');
      autoReplyBtn.type = 'button';
      autoReplyBtn.dataset.toggle = 'auto-reply';
      autoReplyBtn.setAttribute('aria-pressed', String(isAutoReplyEnabled));
      autoReplyBtn.textContent = isAutoReplyEnabled ? 'Auto-Reply: ON' : 'Auto-Reply: OFF';
      autoReplyBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        setAutoReplyEnabled(!isAutoReplyEnabled, { broadcast: true });
      });

      const dashBtn = el('button', 'livestream-ai-toolbar-dash', 'Dashboard ↗');
      dashBtn.type = 'button';
      dashBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        sendRuntimeMessage({ type: 'OPEN_DASHBOARD' }, (res, error) => {
          if (error || !res?.success) {
            window.open('http://127.0.0.1:8000/dashboard', '_blank', 'noopener,noreferrer');
          }
        });
      });

      toolbar.append(title, collectorBtn, autoReplyBtn, dashBtn);
    }

    const header = root.querySelector('yt-live-chat-header-renderer');
    if (header && header.nextElementSibling !== toolbar) {
      header.insertAdjacentElement('afterend', toolbar);
    } else if (!header && root.firstElementChild !== toolbar) {
      root.insertAdjacentElement('afterbegin', toolbar);
    }
    return toolbar;
  }

  function ensureAiRow() {
    if (aiRow?.id === ROW_ID) return aiRow;

    aiRow = document.getElementById(ROW_ID) || el('div');
    aiRow.id = ROW_ID;
    aiRow.setAttribute('role', 'region');
    aiRow.setAttribute('aria-label', 'AI selected live chat messages');

    aiRow.addEventListener(
      'wheel',
      (e) => {
        if (
          aiRow.scrollWidth > aiRow.clientWidth &&
          Math.abs(e.deltaY) > Math.abs(e.deltaX)
        ) {
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

    const ticker =
      root?.querySelector('yt-live-chat-ticker-renderer, #ticker') ||
      document.querySelector('yt-live-chat-ticker-renderer');

    if (ticker) {
      observedTicker = ticker;
      const row = ensureAiRow();

      if (ticker.nextElementSibling !== row) {
        ticker.insertAdjacentElement('afterend', row);
      }

      return;
    }

    const list =
      root?.querySelector('yt-live-chat-item-list-renderer, #chat') ||
      document.querySelector('yt-live-chat-item-list-renderer');

    if (list) {
      const row = ensureAiRow();

      if (list.previousElementSibling !== row) {
        list.insertAdjacentElement('beforebegin', row);
      }
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

  const createThreadMessage = (sender, text, isReply = false) => {
    const box = el(
      'div',
      `livestream-ai-popover-message${isReply ? ' is-reply' : ''}`
    );
    if (sender) {
      box.appendChild(el('div', 'livestream-ai-popover-label', sender));
    }
    box.appendChild(el('div', '', text));
    return box;
  };

  function openPopover(record) {
    if (!record) return;

    if (!popover || !popover.isConnected) {
      popover = document.getElementById(POPOVER_ID) || el('div');
      popover.id = POPOVER_ID;
      popover.setAttribute('role', 'dialog');
      ['click', 'keydown', 'keyup', 'keypress'].forEach((evt) => {
        popover.addEventListener(evt, (e) => {
          if (evt === 'keydown' && e.key === 'Escape') closePopover();
          e.stopPropagation();
        });
      });

      (
        document.querySelector('yt-live-chat-renderer') ||
        document.body
      )?.appendChild(popover);
    }

    const header = el('div', 'livestream-ai-popover-header');
    const meta = el('div', 'livestream-ai-popover-meta');
    const badges = el('div', 'livestream-ai-popover-badges');

    badges.appendChild(
      el(
        'span',
        'livestream-ai-popover-badge',
        String(record.intent || 'selected').replace(/_/g, ' ')
      )
    );

    if (record.auto_order?.order_id) {
      badges.appendChild(
        el(
          'span',
          'livestream-ai-popover-badge is-answered',
          `Order #${record.auto_order.order_id}`
        )
      );
    }

    if (record.needs_human_review) {
      badges.appendChild(
        el('span', 'livestream-ai-popover-badge is-review', 'Needs Review')
      );
    } else if (record.answer_draft?.status === 'answered') {
      badges.appendChild(
        el('span', 'livestream-ai-popover-badge is-answered', 'Draft Ready')
      );
    }

    if (record.chat_send_status) {
      const statusLabels = {
        queued: ['Chat: Queued ⏳', ''],
        sent: ['Chat: Sent ✓', ' is-answered'],
        failed: ['Chat: Send Failed ⚠', ' is-error'],
        blocked_or_restricted: ['Chat: Restricted ⛔', ' is-review']
      };
      const [labelTxt, extraCls] = statusLabels[record.chat_send_status] || [
        `Chat: ${record.chat_send_status}`,
        ''
      ];
      badges.appendChild(
        el('span', `livestream-ai-popover-badge${extraCls}`, labelTxt)
      );
    }

    meta.append(
      el('div', 'livestream-ai-popover-author', record.sender),
      badges
    );

    const closeBtn = el('button', 'livestream-ai-popover-close', '×');
    closeBtn.type = 'button';
    closeBtn.addEventListener('click', closePopover);

    header.append(
      createAvatar(record.sender, record.avatarUrl),
      meta,
      closeBtn
    );

    const threadList = el('div', 'livestream-ai-thread-list');
    threadList.appendChild(createThreadMessage(null, record.text));

    if (record.answer_draft?.sources?.length) {
      const sources = el('div', 'livestream-ai-popover-sources');
      record.answer_draft.sources.forEach((s) => {
        sources.appendChild(
          el('span', 'livestream-ai-popover-source', `${s.title} (${s.source_id})`)
        );
      });
      threadList.appendChild(sources);
    }

    const reason = record.reason || record.answer_draft?.reason;
    if (reason) {
      threadList.appendChild(el('div', 'livestream-ai-popover-reason', reason));
    }

    const draftText = record.answer_draft?.answer || '';
    const hasReadyDraft =
      record.answer_draft?.status === 'answered' &&
      !record.needs_human_review &&
      !record.answer_draft?.needs_human_review &&
      Boolean(draftText);

    const replies = Array.isArray(record.thread_messages) && record.thread_messages.length
      ? record.thread_messages
      : hasReadyDraft
        ? [
            {
              sender: 'AI Draft',
              authorRole: 'ai',
              text: draftText,
              timestamp: Date.now()
            }
          ]
        : [];
    replies.forEach((m) => {
      threadList.appendChild(createThreadMessage(m.sender, m.text, true));
    });

    const replyInput = el('input', 'livestream-ai-thread-input');
    replyInput.type = 'text';
    replyInput.placeholder = `Internal draft / operator note for ${record.sender}...`;
    replyInput.value = '';

    const sendBtn = el('button', 'livestream-ai-thread-send', 'Save Note');
    sendBtn.type = 'submit';

    const composer = el('form', 'livestream-ai-thread-composer');
    composer.append(replyInput, sendBtn);
    composer.addEventListener('submit', (e) => {
      e.preventDefault();
      const text = replyInput.value.trim();
      if (!text) return;

      const isAi = Boolean(draftText && text === draftText.trim());
      const payload = {
        sender: isAi ? 'AI Draft' : 'Moderator',
        authorRole: isAi ? 'ai' : 'moderator',
        text,
        timestamp: Date.now()
      };

      replyInput.value = '';
      replyInput.disabled = true;
      sendBtn.disabled = true;

      sendRuntimeMessage(
        {
          type: 'SEND_THREAD_MESSAGE',
          commentId: record.comment_id,
          payload
        },
        (res, error) => {
          replyInput.disabled = false;
          sendBtn.disabled = false;

          const current = aiMessages.get(record.comment_id);
          if (!current) return;

          if (error || !res?.success) {
            replyInput.value = text;
            return;
          }

          current.thread_messages = Array.isArray(res.data)
            ? res.data
            : [...(current.thread_messages || []), payload];

          aiMessages.set(record.comment_id, current);
          if (selectedId === record.comment_id && popover && !popover.hidden) {
            const liveThreadList = popover.querySelector(
              '.livestream-ai-thread-list'
            );
            if (liveThreadList) {
              liveThreadList.appendChild(
                createThreadMessage(payload.sender, payload.text, true)
              );
              liveThreadList.scrollTop = liveThreadList.scrollHeight;
            }
          }
        }
      );
    });

    popover.replaceChildren(header, threadList, composer);

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

    const selector =
      `.livestream-ai-chip[data-comment-id="${CSS.escape(record.comment_id)}"]`;

    let chip = row.querySelector(selector);

    if (!chip) {
      chip = el('button', 'livestream-ai-chip');
      chip.type = 'button';
      chip.dataset.commentId = record.comment_id;
      chip.setAttribute('aria-expanded', 'false');

      chip.addEventListener('click', (e) => {
        e.stopPropagation();

        if (selectedId === record.comment_id) {
          return closePopover();
        }

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

    const statusSuffix =
      record.chat_send_status === 'sent'
        ? ' [Chat: Sent ✓]'
        : record.chat_send_status === 'failed'
          ? ' [Chat: Send Failed ⚠]'
          : record.chat_send_status === 'blocked_or_restricted'
            ? ' [Chat: Restricted ⛔]'
            : record.chat_send_status === 'queued'
              ? ' [Chat: Queued ⏳]'
              : '';
    chip.title = `${record.sender}: ${record.text}${statusSuffix}`;

    const chipChildren = [
      createAvatar(record.sender, record.avatarUrl),
      el('span', 'livestream-ai-chip-author', record.sender),
      el('span', 'livestream-ai-chip-text', record.text)
    ];
    if (record.chat_send_status) {
      const iconMap = {
        sent: '✓',
        failed: '⚠',
        blocked_or_restricted: '⛔',
        queued: '⏳'
      };
      const statusBadge = el(
        'span',
        `livestream-ai-chip-status is-${record.chat_send_status}`,
        iconMap[record.chat_send_status] || '•'
      );
      chipChildren.push(statusBadge);
    }
    chip.replaceChildren(...chipChildren);

    while (aiMessages.size > MAX_CHIPS) {
      const oldestId = aiMessages.keys().next().value;

      aiMessages.delete(oldestId);

      row
        .querySelector(
          `.livestream-ai-chip[data-comment-id="${CSS.escape(oldestId)}"]`
        )
        ?.remove();

      if (selectedId === oldestId) closePopover();
    }
  }

  function onComment(node) {
    if (!isEnabled || node.nodeType !== 1) return;

    if (
      !node.matches?.(
        'yt-live-chat-text-message-renderer, yt-live-chat-paid-message-renderer'
      )
    ) {
      return;
    }

    const sender = node.querySelector('#author-name')?.innerText?.trim();
    const text = extractCommentText(node.querySelector('#message'));
    const authorType = node.getAttribute('author-type') || 'viewer';

    if (!sender || !text) return;
    if (isBotOrEchoComment(node, text, authorType)) return;

    const rawId = node.id || `${sender}-${text}-${Date.now()}`;
    const id = rawId.replace(/[^A-Za-z0-9._@-]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 128) || `c_${Date.now()}`;

    if (seenIds.has(id)) return;

    seenIds.add(id);

    const avatarUrl =
      node.querySelector('#author-photo img, #img')?.getAttribute('src') || '';

    sendRuntimeMessage(
      {
        type: 'NEW_COMMENT',
        payload: {
          id,
          sender,
          text,
          authorType,
          timestamp: Date.now()
        }
      },
      (res, error) => {
        if (
          error ||
          !isEnabled ||
          !res?.success ||
          !isSelectedComment(res.data)
        ) {
          return;
        }

        const cid = String(res.data.comment_id || '');

        if (!cid) return;

        const prev = aiMessages.get(cid);
        const record = {
          ...prev,
          ...res.data,
          thread_messages:
            res.data.thread_messages?.length
              ? res.data.thread_messages
              : prev?.thread_messages || [],
          avatarUrl:
            avatarUrl || prev?.avatarUrl || ''
        };

        aiMessages.set(cid, record);
        upsertChip(record);

        if (
          isAutoReplyEnabled &&
          !autoRepliedIds.has(cid) &&
          record.answer_draft?.status === 'answered' &&
          !record.needs_human_review &&
          !record.answer_draft?.needs_human_review &&
          record.answer_draft?.answer
        ) {
          autoRepliedIds.add(cid);
          const chatReply = formatPublicChatReply(sender, record.answer_draft.answer);
          enqueueYouTubeLiveChatReply(cid, chatReply);
        }

        if (selectedId === cid && popover && !popover.hidden) {
          openPopover(record);
        }
      }
    );
  }

  function syncChatDom() {
    chatRaf = null;

    if (!isEnabled) return;

    const container = document.querySelector(
      'yt-live-chat-item-list-renderer #items, #item-scroller #items'
    );

    if (container && observedContainer !== container) {
      chatObserver?.disconnect();
      observedContainer = container;

      chatObserver = new MutationObserver((mutations) => {
        if (!isEnabled) return;

        for (const mutation of mutations) {
          mutation.addedNodes.forEach(onComment);
        }
      });

      chatObserver.observe(container, { childList: true });
    }

    mountAiRow();

    const root = document.querySelector(
      'yt-live-chat-renderer, yt-live-chat-app'
    );

    if (root && structureObserver && observedRoot !== root) {
      structureObserver.disconnect();
      observedRoot = root;

      structureObserver.observe(root, {
        childList: true,
        subtree: true
      });
    }
  }

  function startChatCollection() {
    if (
      window === window.top &&
      !window.location.pathname.startsWith('/live_chat') &&
      !document.querySelector('yt-live-chat-app, yt-live-chat-renderer')
    ) {
      return;
    }

    syncChatDom();

    if (structureObserver) return;

    structureObserver = new MutationObserver((mutations) => {
      if (
        !isEnabled ||
        !mutations.some((m) => !isOwnedNode(m.target))
      ) {
        return;
      }

      if (
        observedContainer?.isConnected &&
        observedTicker?.isConnected &&
        observedTicker.nextElementSibling === aiRow
      ) {
        return;
      }

      if (chatRaf === null) {
        chatRaf = requestAnimationFrame(syncChatDom);
      }
    });

    observedRoot =
      document.querySelector('yt-live-chat-renderer, yt-live-chat-app') ||
      document.body ||
      document.documentElement;

    if (observedRoot) {
      structureObserver.observe(observedRoot, {
        childList: true,
        subtree: true
      });
    }
  }

  function stopChatCollection() {
    if (chatRaf !== null) {
      cancelAnimationFrame(chatRaf);
    }

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

  function broadcastToChatFrame(payload) {
    const chatFrame = document.querySelector(
      'iframe#chatframe, ytd-live-chat-frame iframe'
    );

    if (chatFrame?.contentWindow) {
      let targetOrigin = window.location.origin;
      try {
        if (chatFrame.src) {
          targetOrigin = new URL(chatFrame.src, window.location.href).origin;
        }
      } catch (_) {}

      chatFrame.contentWindow.postMessage(
        {
          source: 'livestream-ai-extension',
          ...payload
        },
        targetOrigin
      );
    }
  }

  function setAssistantEnabled(nextState, { broadcast = false } = {}) {
    const normalized = Boolean(nextState);
    const changed = isEnabled !== normalized;

    isEnabled = normalized;

    document
      .querySelector(`#${PANEL_ID} .livestream-ai-menuitem[data-toggle="collector"]`)
      ?.setAttribute('aria-checked', String(isEnabled));

    const chatCollectorBtn = document.querySelector(
      `#${CHAT_TOOLBAR_ID} .livestream-ai-toolbar-toggle[data-toggle="collector"]`
    );
    if (chatCollectorBtn) {
      chatCollectorBtn.setAttribute('aria-pressed', String(isEnabled));
      chatCollectorBtn.textContent = isEnabled ? 'Collector: ON' : 'Collector: OFF';
    }

    ensureChatToolbar();

    if (isEnabled) {
      startChatCollection();
    } else if (changed) {
      chatSendQueue.length = 0;
      stopChatCollection();
    }

    if (broadcast && changed) {
      sendRuntimeMessage({
        type: 'SET_ENABLED',
        enabled: isEnabled
      });
      broadcastToChatFrame({
        type: 'AI_STATE_CHANGED',
        enabled: isEnabled
      });
    }
  }

  function setAutoReplyEnabled(nextState, { broadcast = false } = {}) {
    const normalized = Boolean(nextState);
    const changed = isAutoReplyEnabled !== normalized;

    isAutoReplyEnabled = normalized;

    document
      .querySelector(`#${PANEL_ID} .livestream-ai-menuitem[data-toggle="auto-reply"]`)
      ?.setAttribute('aria-checked', String(isAutoReplyEnabled));

    const chatAutoReplyBtn = document.querySelector(
      `#${CHAT_TOOLBAR_ID} .livestream-ai-toolbar-toggle[data-toggle="auto-reply"]`
    );
    if (chatAutoReplyBtn) {
      chatAutoReplyBtn.setAttribute('aria-pressed', String(isAutoReplyEnabled));
      chatAutoReplyBtn.textContent = isAutoReplyEnabled ? 'Auto-Reply: ON' : 'Auto-Reply: OFF';
    }

    ensureChatToolbar();

    if (!isAutoReplyEnabled) {
      chatSendQueue.length = 0;
    }

    if (broadcast && changed) {
      sendRuntimeMessage({
        type: 'SET_AUTO_REPLY',
        autoReply: isAutoReplyEnabled
      });
      broadcastToChatFrame({
        type: 'AI_AUTO_REPLY_CHANGED',
        autoReply: isAutoReplyEnabled
      });
    }
  }

  const isTrustedYouTubeOrigin = (origin) =>
    /^https:\/\/([a-z0-9-]+\.)?youtube\.com$/i.test(String(origin || ''));

  try {
    chrome.runtime.onMessage.addListener((req) => {
      if (req?.type === 'AI_STATE_CHANGED') {
        setAssistantEnabled(req.enabled);
      } else if (req?.type === 'AI_AUTO_REPLY_CHANGED') {
        setAutoReplyEnabled(req.autoReply);
      }
    });
  } catch (_) {
    // Context unavailable.
  }

  window.addEventListener('message', (e) => {
    const chatFrameWindow = document.querySelector(
      'iframe#chatframe, ytd-live-chat-frame iframe'
    )?.contentWindow;

    if (
      isTrustedYouTubeOrigin(e.origin) &&
      (e.source === window.top || e.source === chatFrameWindow) &&
      e.data?.source === 'livestream-ai-extension'
    ) {
      if (e.data?.type === 'AI_STATE_CHANGED') {
        setAssistantEnabled(e.data.enabled);
      } else if (e.data?.type === 'AI_AUTO_REPLY_CHANGED') {
        setAutoReplyEnabled(e.data.autoReply);
      }
    }
  });

  document.addEventListener('click', (e) => {
    if (
      selectedId &&
      popover &&
      !popover.hidden &&
      !popover.contains(e.target) &&
      !aiRow?.contains(e.target)
    ) {
      closePopover();
    }

    const panel = document.getElementById(PANEL_ID);

    if (
      panel?.classList.contains('is-open') &&
      !panel.contains(e.target) &&
      !playerBtn?.contains(e.target)
    ) {
      panel.classList.remove('is-open');
      playerBtn?.setAttribute('aria-expanded', 'false');
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && selectedId) {
      closePopover();
    }
  });

  function syncSavedEnabledState() {
    sendRuntimeMessage(
      { type: 'GET_ENABLED' },
      (res, error) => {
        if (!error) {
          if (typeof res?.enabled === 'boolean') {
            setAssistantEnabled(res.enabled);
          }
          if (typeof res?.autoReply === 'boolean') {
            setAutoReplyEnabled(res.autoReply);
          }
        }
      }
    );
  }

  syncSavedEnabledState();
  ensureChatToolbar();
  new MutationObserver(() => {
    if (!document.getElementById(CHAT_TOOLBAR_ID) && document.querySelector('yt-live-chat-renderer')) {
      ensureChatToolbar();
    }
  }).observe(document.documentElement, { childList: true, subtree: true });

  function addPlayerButton() {
    playerRaf = null;

    if (playerBtn?.isConnected) return;

    playerBtn = document.getElementById(BUTTON_ID);

    if (playerBtn) return;

    const controls = document.querySelector('.ytp-right-controls');

    const settingsBtn = controls?.querySelector('.ytp-settings-button');

    const playerContainer =
      controls?.closest('.html5-video-player') ||
      controls?.parentElement;

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
      item.dataset.toggle = 'collector';
      item.setAttribute('aria-checked', String(isEnabled));
      item.tabIndex = 0;

      const track = el('div', 'livestream-ai-toggle-track');

      track.appendChild(
        el('div', 'livestream-ai-toggle-thumb')
      );

      item.append(
        el('div', 'livestream-ai-menuitem-label', 'Enable Collector'),
        track
      );

      const toggle = (e) => {
        e.stopPropagation();

        setAssistantEnabled(!isEnabled, {
          broadcast: true
        });
      };

      item.addEventListener('click', toggle);

      item.addEventListener('keydown', (e) => {
        if (e.key === ' ' || e.key === 'Enter') {
          e.preventDefault();
          toggle(e);
        }
      });

      const autoReplyItem = el('div', 'livestream-ai-menuitem');
      autoReplyItem.setAttribute('role', 'menuitemcheckbox');
      autoReplyItem.dataset.toggle = 'auto-reply';
      autoReplyItem.setAttribute('aria-checked', String(isAutoReplyEnabled));
      autoReplyItem.tabIndex = 0;

      const autoReplyTrack = el('div', 'livestream-ai-toggle-track');
      autoReplyTrack.appendChild(el('div', 'livestream-ai-toggle-thumb'));
      autoReplyItem.append(
        el('div', 'livestream-ai-menuitem-label', 'Auto-Reply in Chat'),
        autoReplyTrack
      );

      const toggleAutoReply = (e) => {
        e.stopPropagation();
        setAutoReplyEnabled(!isAutoReplyEnabled, {
          broadcast: true
        });
      };

      autoReplyItem.addEventListener('click', toggleAutoReply);
      autoReplyItem.addEventListener('keydown', (e) => {
        if (e.key === ' ' || e.key === 'Enter') {
          e.preventDefault();
          toggleAutoReply(e);
        }
      });

      panel.addEventListener('click', (e) => {
        e.stopPropagation();
      });

      const dashboardItem = el(
        'div',
        'livestream-ai-menuitem livestream-ai-dashboard-item'
      );
      dashboardItem.setAttribute('role', 'menuitem');
      dashboardItem.tabIndex = 0;
      dashboardItem.append(
        el('div', 'livestream-ai-menuitem-label', 'Open AI Dashboard'),
        el('span', 'livestream-ai-dashboard-icon', '↗')
      );

      const openDashboard = (e) => {
        e.stopPropagation();
        panel.classList.remove('is-open');
        button.setAttribute('aria-expanded', 'false');

        sendRuntimeMessage({ type: 'OPEN_DASHBOARD' }, (res, error) => {
          if (error || !res?.success) {
            window.open(
              'http://127.0.0.1:8000/dashboard',
              '_blank',
              'noopener,noreferrer'
            );
          }
        });
      };

      dashboardItem.addEventListener('click', openDashboard);
      dashboardItem.addEventListener('keydown', (e) => {
        if (e.key === ' ' || e.key === 'Enter') {
          e.preventDefault();
          openDashboard(e);
        }
      });

      panel.append(item, autoReplyItem, dashboardItem);
      playerContainer.appendChild(panel);
    } else {
      panel
        .querySelector('.livestream-ai-menuitem[data-toggle="collector"]')
        ?.setAttribute('aria-checked', String(isEnabled));
      panel
        .querySelector('.livestream-ai-menuitem[data-toggle="auto-reply"]')
        ?.setAttribute('aria-checked', String(isAutoReplyEnabled));
    }

    button.addEventListener('click', (e) => {
      e.stopPropagation();

      const willOpen = !panel.classList.contains('is-open');

      if (
        willOpen &&
        settingsBtn.getAttribute('aria-expanded') === 'true'
      ) {
        settingsBtn.click();
      }

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

  if (
    window === window.top &&
    !window.location.pathname.startsWith('/live_chat')
  ) {
    const schedule = () => {
      if (!playerBtn?.isConnected && playerRaf === null) {
        playerRaf = requestAnimationFrame(addPlayerButton);
      }
    };

    new MutationObserver(schedule).observe(
      document.documentElement,
      {
        childList: true,
        subtree: true
      }
    );

    window.addEventListener('yt-navigate-finish', () => {
      syncSavedEnabledState();
      ensureChatToolbar();
      schedule();
    });

    addPlayerButton();
  }
})();
