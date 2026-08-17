# Sinria in Chrome

A local-first Manifest V3 side-panel extension that lets Sinria read and act on the current Chrome tab without giving it permanent access to browser history or every site.

## Install

Install the bundled extension and native host from any Sinria checkout or packaged installation:

```bash
sinria chrome install
sinria chrome
```

`sinria chrome` opens a dedicated local Chrome profile with the extension loaded. Start the Sinria API server on `http://127.0.0.1:8642`; if the gateway is managed by a daemon, enable its existing `api_server` adapter instead of starting a second gateway. In the side-panel **Settings**, save and test the local API URL.

The extension has a stable origin, `chrome-extension://pebcacnleolamclolgncigkgojkdghgc`. Add that origin to the API server's `api_server.cors_origins` configuration when direct local API access is enabled. The optional bearer token is held only for the current browser session.

Use `sinria chrome status` to verify the extension, native host, and browser manifests. Use `sinria chrome uninstall` to remove only Sinria's installed Chrome runtime.

### Developer-mode fallback

Open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**, and select `apps/sinria-in-chrome`. Then pin **Sinria in Chrome**, open an HTTP(S) page, and click the extension icon to grant temporary `activeTab` access and open the side panel.

## Workflow

- Ask a question: the extension captures a bounded snapshot of visible page text and interactive elements, then sends it to the local Sinria `/v1/runs` API.
- Request an action: Sinria may propose `click`, `type`, or HTTP(S) `navigate` operations.
- Review: every browser action is displayed separately and remains inert until **Approve once** is clicked.
- Readback: after execution the extension captures the resulting page title and URL. Cross-origin navigation can revoke Chrome's temporary `activeTab` grant; click the extension again to re-grant it.

## Safety boundaries

- Permissions: `activeTab`, `scripting`, `storage`, and `sidePanel`; no browsing-history or permanent all-sites permission.
- Network: extension host access is limited to local `localhost` / `127.0.0.1` Sinria endpoints.
- Secrets: password and sensitive-looking form values are replaced with `[REDACTED]`; typing into such fields is blocked again at execution time.
- Navigation: only `http:` and `https:` destinations are accepted. `javascript:`, `data:`, `file:`, and browser-internal URLs are rejected.
- Page content is untrusted data. It is delimited in the Sinria instruction and cannot authorize an action.
- The optional API token is stored only in `chrome.storage.session`; it is cleared when the browser session ends and is never written to persistent or synced extension storage.

## Test

```bash
cd apps/sinria-in-chrome
npm test
```
