(() => {
  const seenIds = new Set();

  function onComment(node) {
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    if (!node.matches?.('yt-live-chat-text-message-renderer, yt-live-chat-paid-message-renderer')) return;

    const sender = node.querySelector('#author-name')?.innerText?.trim();
    const text = node.querySelector('#message')?.innerText?.trim();
    if (!sender || !text) return;

    const id = node.id || `${sender}:${text}:${Date.now()}`;
    if (seenIds.has(id)) return;
    seenIds.add(id);

    const authorType = node.getAttribute('author-type') || 'viewer';
    console.log(`[YouTube Live] @${sender} [${authorType}]: ${text}`);

    chrome.runtime?.sendMessage({
      type: 'NEW_COMMENT',
      payload: { id, sender, text, authorType, timestamp: Date.now() }
    });
  }

  function startObserving(container) {
    console.log('%c[YouTube Live] Đã kết nối vào khung chat, đang thu thập bình luận realtime!', 'color: #10b981; font-weight: bold;');
    const observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        m.addedNodes.forEach(onComment);
      }
    });
    observer.observe(container, { childList: true });
  }

  function findContainer() {
    const container = document.querySelector('yt-live-chat-item-list-renderer #items, #item-scroller #items, #items');
    if (container) {
      startObserving(container);
      return true;
    }
    return false;
  }

  // Khởi động: tìm ngay hoặc chờ container xuất hiện trong DOM
  if (!findContainer()) {
    const docObserver = new MutationObserver(() => {
      if (findContainer()) {
        docObserver.disconnect();
      }
    });
    docObserver.observe(document.documentElement, { childList: true, subtree: true });
  }

  // --- Custom Button ---

  const CUSTOM_BUTTON_ID = 'livestream-ai-custom-button';

  function addPlayerButton() {
    const controls = document.querySelector('.ytp-right-controls');

    if (!controls || document.getElementById(CUSTOM_BUTTON_ID)) {
      return;
    }

    const settingsBtn = controls.querySelector('.ytp-settings-button');
    if (!settingsBtn) {
      return;
    }

    const button = document.createElement('button');
    button.id = CUSTOM_BUTTON_ID;
    button.className = 'ytp-button';
    button.type = 'button';
    button.title = 'Livestream AI Assistant';
    button.setAttribute('aria-label', 'Livestream AI Assistant');

    button.style.position = 'relative';
    button.style.overflow = 'visible';

    const iconScale = 1.0;

    const nativeSvg = settingsBtn.querySelector('svg');
    if (nativeSvg) {
      const clonedSvg = nativeSvg.cloneNode(true);

      clonedSvg.style.transform = `scale(${iconScale})`;
      clonedSvg.style.transformOrigin = 'center center';
      clonedSvg.style.display = 'block';
      clonedSvg.style.margin = 'auto';
      clonedSvg.style.overflow = 'visible';

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
        <svg height="100%" version="1.1" viewBox="0 0 36 36" width="100%" style="transform: scale(${iconScale}); transform-origin: center center; display: block; margin: auto; overflow: visible;">
          <use class="ytp-svg-shadow" xlink:href="#ytp-livestream-ai-gear"></use>
          <path class="ytp-svg-fill" id="ytp-livestream-ai-gear" d="m 23.94,18.78 c .03,-0.25 .05,-0.51 .05,-0.78 0,-0.27 -0.02,-0.52 -0.05,-0.78 l 1.68,-1.32 c .15,-0.12 .19,-0.33 .09,-0.51 l -1.6,-2.76 c -0.1,-0.18 -0.31,-0.24 -0.49,-0.17 l -1.99,0.8 c -0.41,-0.32 -0.86,-0.58 -1.35,-0.78 l -0.3,-2.12 c -0.03,-0.19 -0.19,-0.33 -0.39,-0.33 l -3.2,0 c -0.2,0 -0.36,0.14 -0.39,0.33 l -0.3,2.12 c -0.49,0.2 -0.94,0.47 -1.35,0.78 l -1.99,-0.8 c -0.18,-0.07 -0.39,0.01 -0.49,0.17 l -1.6,2.76 c -0.1,0.18 -0.06,0.39 .09,0.51 l 1.68,1.32 c -0.03,0.25 -0.05,0.52 -0.05,0.78 0,0.26 0.02,0.53 0.05,0.78 l -1.68,1.32 c -0.15,0.12 -0.19,0.33 -0.09,0.51 l 1.6,2.76 c .1,0.18 .31,0.24 .49,0.17 l 1.99,-0.8 c .41,0.32 .86,0.58 1.35,0.78 l .3,2.12 c .03,0.19 .19,0.33 .39,0.33 l 3.2,0 c .2,0 .36,-0.14 .39,-0.33 l .3,-2.12 c .49,-0.2 .94,-0.47 1.35,-0.78 l 1.99,0.8 c .18,.07 .39,-0.01 .49,-0.17 l 1.6,-2.76 c .1,-0.18 .06,-0.39 -0.09,-0.51 l -1.68,-1.32 z m -5.94,2.01 c -1.54,0 -2.79,-1.25 -2.79,-2.79 0,-1.54 1.25,-2.79 2.79,-2.79 1.54,0 2.79,1.25 2.79,2.79 0,1.54 -1.25,2.79 -2.79,2.79 z"></path>
        </svg>
      `.trim();
    }

    const badge = document.createElement('span');
    badge.textContent = 'AI';
    badge.style.cssText = [
      'position: absolute',
      'top: 9px',
      'right: 8px',
      'background-color: #cc0000',
      'color: #ffffff',
      'font-family: "Press Start 2P", "Courier New", monospace',
      'font-size: 7px',
      'font-weight: bold',
      'line-height: 1',
      'padding: 1.5px 2px',
      'border-radius: 2px',
      'letter-spacing: 0px',
      'pointer-events: none',
      'box-shadow: 0 1px 2px rgba(0,0,0,0.4)',
      'image-rendering: pixelated'
    ].join(';');
    button.appendChild(badge);

    button.addEventListener('click', () => {
      console.log('[YouTube Live] Custom gear button clicked.');
      // TODO: Connect your chosen feature here.
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
