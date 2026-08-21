---
title: "Sinria in Chrome"
description: "Install Sinria's local-first Chrome extension on each computer and Chrome profile"
sidebar_label: "Chrome Extension"
sidebar_position: 3
---

# Sinria in Chrome

Sinria in Chrome is a local-first Manifest V3 side-panel extension. It connects a Chrome tab to the Sinria API running on the same computer.

## Installation scope

Chrome does not sync unpacked extensions through a Google account. Installation is therefore required:

- on every computer that will run the extension; and
- in every Chrome profile that Sinria should control.

Installing the runtime and activating it in a Chrome profile are separate steps. The extension can control only profiles where activation has been completed.

## Install on a computer

1. Install or update Sinria Agent on that computer.
2. Run:

   ```bash
   sinria chrome install
   ```

3. Start the existing Sinria API server on `http://127.0.0.1:8642`. If a daemon already manages the gateway, enable its existing `api_server` adapter rather than starting a second gateway.
4. In every Chrome profile that should use Sinria:
   - Open `chrome://extensions`.
   - Enable **Developer mode**.
   - Select **Load unpacked**.
   - Choose the exact absolute extension path printed by `sinria chrome install`. Do not enter a literal `~` in Chrome's file picker.
   - Pin **Sinria in Chrome** and open its side panel.
5. Before starting or restarting the Sinria API, allow the extension's stable origin:

   ```bash
   export API_SERVER_CORS_ORIGINS="chrome-extension://pebcacnleolamclolgncigkgojkdghgc"
   ```

   For a daemon-managed gateway, set the same environment variable in that service's environment rather than starting a second gateway.

6. In the side-panel **Settings**, set the API URL to `http://127.0.0.1:8642`, add the optional bearer token, and select **Save & test**.
7. Confirm the side panel reports **Connected**.

Repeat these steps on another computer even when Chrome uses the same Google account.

## Check installation

Run:

```bash
sinria chrome status
```

This verifies Sinria's local runtime files, native host, and managed browser. Chrome controls profile activation separately, so also verify that:

- **Sinria in Chrome** appears and is enabled in `chrome://extensions` for the intended profile; and
- **Save & test** reports **Connected** in that profile.

## Security boundary

- The extension uses temporary `activeTab` access, not permanent access to browsing history or every website.
- Browser actions remain inert until the user selects **Approve once**.
- Local API access is restricted to `localhost` and `127.0.0.1`.
- The optional bearer token is stored in `chrome.storage.session` and is cleared when the browser session ends.
- Chrome extension control does not bypass Chrome profiles, operating-system accounts, or Sinria's review gates.

## Troubleshooting

### The runtime is installed, but the extension is absent

Runtime installation does not activate an unpacked extension in every Chrome profile. Repeat **Load unpacked** in the missing profile using the absolute path printed by `sinria chrome install`.

### Save & test cannot connect

Confirm that:

1. the Sinria API is running on the same computer at `127.0.0.1:8642`;
2. `API_SERVER_CORS_ORIGINS` contains the extension origin;
3. the bearer token matches the API configuration, if authentication is enabled; and
4. an existing daemon-managed gateway is being reused rather than starting a conflicting second gateway.
