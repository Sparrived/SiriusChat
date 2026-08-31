import { clearToken, getToken } from './api.js';

let ws = null;
let reconnectTimer = null;
let shouldReconnect = true;

export function wsConnect() {
  if (ws && ws.readyState <= 1) return;
  const token = getToken();
  if (!token) return;
  // A fresh login may follow an explicit wsDisconnect() from the prior session.
  shouldReconnect = true;
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  // Browser WebSocket cannot send Authorization.  Offer the constant protocol
  // then the JWT; the server authenticates the upgrade and only selects the
  // constant protocol, so the token is never part of the URL or response.
  try {
    ws = new WebSocket(`${proto}//${location.host}/ws/events`, ['sirius-auth', token]);
  } catch { return; }
  
  ws.onopen = () => {
    // A missing selected protocol means the peer did not honor the authenticated
    // WebSocket contract; close rather than accepting an ambiguous connection.
    if (ws?.protocol !== 'sirius-auth') {
      ws?.close();
      return;
    }
    window.dispatchEvent(new CustomEvent('ws:connected'));
    clearTimeout(reconnectTimer);
  };
  
  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type !== 'connected') {
        window.dispatchEvent(new CustomEvent('sirius:event', { detail: msg }));
      }
    } catch {}
  };
  
  ws.onclose = (event) => {
    window.dispatchEvent(new CustomEvent('ws:disconnected'));
    // Policy/authorization closure cannot be repaired by endlessly reconnecting
    // with the same credential.  Let the normal REST request/auth flow send
    // the user to login once the token has expired or been invalidated.
    if (event.code === 1008 || event.code === 4001 || event.code === 4003) {
      shouldReconnect = false;
      clearToken();
      window.dispatchEvent(new CustomEvent('auth:expired'));
      return;
    }
    if (shouldReconnect) reconnectTimer = setTimeout(wsConnect, 5000);
  };
  
  ws.onerror = () => ws?.close();
}

export function wsDisconnect() {
  shouldReconnect = false;
  clearTimeout(reconnectTimer);
  ws?.close();
  ws = null;
}
