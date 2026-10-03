/**
 * Mount Shadow DOM Container
 * Tạo phần tử host độc lập trên body và gắn Shadow Root
 */

export function mountShadowHost() {
  const HOST_ID = 'shopee-live-ai-copilot-host';

  let host = document.getElementById(HOST_ID);
  if (host) {
    return host.shadowRoot;
  }

  host = document.createElement('div');
  host.id = HOST_ID;
  host.style.position = 'fixed';
  host.style.zIndex = '2147483647';
  host.style.top = '0';
  host.style.left = '0';
  host.style.width = '0';
  host.style.height = '0';
  host.style.overflow = 'visible';

  document.body.appendChild(host);

  const shadowRoot = host.attachShadow({ mode: 'open' });
  return shadowRoot;
}
