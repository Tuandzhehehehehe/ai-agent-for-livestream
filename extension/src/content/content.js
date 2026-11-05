(() => {
  const seenIds = new Set();
  let isEnabled = false;
  let chatObserver = null;
  let docObserver = null;
  let observedContainer = null;

  function onComment(node) {
    if (!isEnabled) return;
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    if (!node.matches?.('yt-live-chat-text-message-renderer, yt-live-chat-paid-message-renderer')) return;

    const sender = node.querySelector('#author-name')?.innerText?.trim();
    const text = node.querySelector('#message')?.innerText?.trim();
    if (!sender || !text) return;

    const id = node.id || `${sender}:${text}:${Date.now()}`;
    if (seenIds.has(id)) return;
    seenIds.add(id);

    const authorType = node.getAttribute('author-type') || 'viewer';

    chrome.runtime?.sendMessage({
      type: 'NEW_COMMENT',
      payload: { id, sender, text, authorType, timestamp: Date.now() }
    });
  }

  function startObserving(container) {
    if (chatObserver && observedContainer === container) return;
    if (chatObserver) {
      chatObserver.disconnect();
    }

    observedContainer = container;
    chatObserver = new MutationObserver((mutations) => {
      if (!isEnabled) return;
      for (const m of mutations) {
        m.addedNodes.forEach(onComment);
      }
    });
    chatObserver.observe(container, { childList: true });
  }

  function findContainer() {
    const container = document.querySelector('yt-live-chat-item-list-renderer #items, #item-scroller #items');
    if (container) {
      startObserving(container);
      return true;
    }
    return false;
  }

  function startChatCollection() {
    findContainer();

    if (!docObserver) {
      docObserver = new MutationObserver(() => {
        if (!isEnabled) return;
        if (!observedContainer || !observedContainer.isConnected) {
          findContainer();
        }
      });
      docObserver.observe(document.documentElement, { childList: true, subtree: true });
    }
  }

  function stopChatCollection() {
    if (chatObserver) {
      chatObserver.disconnect();
      chatObserver = null;
    }
    if (docObserver) {
      docObserver.disconnect();
      docObserver = null;
    }
    observedContainer = null;
  }

  // --- Custom Button & Settings Panel ---

  const CUSTOM_BUTTON_ID = 'livestream-ai-custom-button';
  const CUSTOM_PANEL_ID = 'livestream-ai-settings-panel';

  function setAssistantEnabled(nextState, { broadcast = false } = {}) {
    const normalized = Boolean(nextState);
    const changed = isEnabled !== normalized;
    isEnabled = normalized;

    const menuItem = document.querySelector(`#${CUSTOM_PANEL_ID} .livestream-ai-menuitem`);
    if (menuItem) {
      menuItem.setAttribute('aria-checked', String(isEnabled));
    }

    if (isEnabled) {
      startChatCollection();
    } else if (changed) {
      stopChatCollection();
    }

    if (broadcast && changed) {
      chrome.runtime?.sendMessage({
        type: 'SET_ENABLED',
        enabled: isEnabled
      });
      const chatFrame = document.querySelector('iframe#chatframe, ytd-live-chat-frame iframe');
      chatFrame?.contentWindow?.postMessage(
        { source: 'livestream-ai-extension', type: 'AI_STATE_CHANGED', enabled: isEnabled },
        '*'
      );
    }
  }

  chrome.runtime?.onMessage?.addListener((request) => {
    if (request?.type === 'AI_STATE_CHANGED') {
      setAssistantEnabled(request.enabled, { broadcast: false });
    }
  });

  window.addEventListener('message', (event) => {
    if (event.data?.source === 'livestream-ai-extension' && event.data?.type === 'AI_STATE_CHANGED') {
      setAssistantEnabled(event.data.enabled, { broadcast: false });
    }
  });

  if (window === window.top) {
    chrome.runtime?.sendMessage({ type: 'SET_ENABLED', enabled: false });
  } else {
    chrome.runtime?.sendMessage({ type: 'GET_ENABLED' }, (response) => {
      if (chrome.runtime.lastError) return;
      if (response?.enabled) {
        setAssistantEnabled(true, { broadcast: false });
      }
    });
  }

  function createSettingsPanel(playerContainer, button) {
    let panel = document.getElementById(CUSTOM_PANEL_ID);
    if (panel) {
      return panel;
    }

    panel = document.createElement('div');
    panel.id = CUSTOM_PANEL_ID;
    panel.setAttribute('role', 'menu');
    panel.setAttribute('aria-label', 'Livestream AI settings');

    const menuItem = document.createElement('div');
    menuItem.className = 'livestream-ai-menuitem';
    menuItem.setAttribute('role', 'menuitemcheckbox');
    menuItem.setAttribute('aria-checked', String(isEnabled));
    menuItem.tabIndex = 0;

    const label = document.createElement('div');
    label.className = 'livestream-ai-menuitem-label';
    label.textContent = 'Enable';

    const toggleTrack = document.createElement('div');
    toggleTrack.className = 'livestream-ai-toggle-track';
    const toggleThumb = document.createElement('div');
    toggleThumb.className = 'livestream-ai-toggle-thumb';
    toggleTrack.appendChild(toggleThumb);

    menuItem.append(label, toggleTrack);

    const toggleEnable = (event) => {
      event.stopPropagation();
      setAssistantEnabled(!isEnabled, { broadcast: true });
    };

    menuItem.addEventListener('click', toggleEnable);
    menuItem.addEventListener('keydown', (event) => {
      if (event.key === ' ' || event.key === 'Enter') {
        event.preventDefault();
        toggleEnable(event);
      }
    });

    panel.addEventListener('click', (event) => {
      event.stopPropagation();
    });

    panel.appendChild(menuItem);
    playerContainer.appendChild(panel);

    document.addEventListener('click', (event) => {
      if (!panel.classList.contains('is-open')) return;
      if (panel.contains(event.target) || button.contains(event.target)) return;
      panel.classList.remove('is-open');
      button.setAttribute('aria-expanded', 'false');
    });

    return panel;
  }

  function addPlayerButton() {
    const controls = document.querySelector('.ytp-right-controls');

    if (!controls || document.getElementById(CUSTOM_BUTTON_ID)) {
      return;
    }

    const settingsBtn = controls.querySelector('.ytp-settings-button');
    if (!settingsBtn) {
      return;
    }

    const playerContainer = controls.closest('.html5-video-player') || controls.parentElement;
    if (!playerContainer) {
      return;
    }

    const button = document.createElement('button');
    button.id = CUSTOM_BUTTON_ID;
    button.className = 'ytp-button';
    button.type = 'button';
    button.title = 'Livestream AI Assistant';
    button.setAttribute('aria-label', 'Livestream AI Assistant');
    button.setAttribute('aria-haspopup', 'true');
    button.setAttribute('aria-expanded', 'false');

    const nativeSvg = settingsBtn.querySelector('svg');
    if (nativeSvg) {
      const clonedSvg = nativeSvg.cloneNode(true);
      const pathWithId = clonedSvg.querySelector('[id]');
      if (pathWithId) {
        const uniqueId = `${pathWithId.id}-custom-ai`;
        pathWithId.id = uniqueId;
        const useElem = clonedSvg.querySelector('use');
        if (useElem) {
          useElem.setAttribute('xlink:href', `#${uniqueId}`);
          useElem.setAttribute('href', `#${uniqueId}`);
        }
      }
      button.appendChild(clonedSvg);
    } else {
      button.innerHTML = `
        <svg height="100%" version="1.1" viewBox="0 0 36 36" width="100%">
          <use class="ytp-svg-shadow" xlink:href="#ytp-livestream-ai-gear"></use>
          <path class="ytp-svg-fill" id="ytp-livestream-ai-gear" d="m 23.94,18.78 c .03,-0.25 .05,-0.51 .05,-0.78 0,-0.27 -0.02,-0.52 -0.05,-0.78 l 1.68,-1.32 c .15,-0.12 .19,-0.33 .09,-0.51 l -1.6,-2.76 c -0.1,-0.18 -0.31,-0.24 -0.49,-0.17 l -1.99,0.8 c -0.41,-0.32 -0.86,-0.58 -1.35,-0.78 l -0.3,-2.12 c -0.03,-0.19 -0.19,-0.33 -0.39,-0.33 l -3.2,0 c -0.2,0 -0.36,0.14 -0.39,0.33 l -0.3,2.12 c -0.49,0.2 -0.94,0.47 -1.35,0.78 l -1.99,-0.8 c -0.18,-0.07 -0.39,0.01 -0.49,0.17 l -1.6,2.76 c -0.1,0.18 -0.06,0.39 .09,0.51 l 1.68,1.32 c -0.03,0.25 -0.05,0.52 -0.05,0.78 0,0.26 0.02,0.53 0.05,0.78 l -1.68,1.32 c -0.15,0.12 -0.19,0.33 -0.09,0.51 l 1.6,2.76 c .1,0.18 .31,0.24 .49,0.17 l 1.99,-0.8 c .41,0.32 .86,0.58 1.35,0.78 l .3,2.12 c .03,0.19 .19,0.33 .39,0.33 l 3.2,0 c .2,0 .36,-0.14 .39,-0.33 l .3,-2.12 c .49,-0.2 .94,-0.47 1.35,-0.78 l 1.99,0.8 c .18,.07 .39,-0.01 .49,-0.17 l 1.6,-2.76 c .1,-0.18 .06,-0.39 -0.09,-0.51 l -1.68,-1.32 z m -5.94,2.01 c -1.54,0 -2.79,-1.25 -2.79,-2.79 0,-1.54 1.25,-2.79 2.79,-2.79 1.54,0 2.79,1.25 2.79,2.79 0,1.54 -1.25,2.79 -2.79,2.79 z"></path>
        </svg>
      `.trim();
    }

    const badge = document.createElement('span');
    badge.className = 'livestream-ai-badge';
    badge.textContent = 'AI';
    button.appendChild(badge);

    const panel = createSettingsPanel(playerContainer, button);

    button.addEventListener('click', (event) => {
      event.stopPropagation();

      const willOpen = !panel.classList.contains('is-open');
      if (willOpen && settingsBtn.getAttribute('aria-expanded') === 'true') {
        settingsBtn.click();
      }

      panel.classList.toggle('is-open', willOpen);
      button.setAttribute('aria-expanded', String(willOpen));
    });

    settingsBtn.addEventListener('click', () => {
      if (panel.classList.contains('is-open')) {
        panel.classList.remove('is-open');
        button.setAttribute('aria-expanded', 'false');
      }
    });

    settingsBtn.insertAdjacentElement('beforebegin', button);
  }

  const playerObserver = new MutationObserver(addPlayerButton);

  playerObserver.observe(document.documentElement, {
    childList: true,
    subtree: true
  });

  addPlayerButton();
})();
