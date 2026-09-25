# Privacy — HeadStart

This extension runs a **local** planner page. It does not send your cases, mail, Slack, or calendar to the Chrome Web Store publisher.

## What stays on this machine

- **Express LLM Gateway token, model, and runner** — `chrome.storage.local` in this Chrome profile. Sent only to the local bridge (`http://127.0.0.1:8765`–`8799`) when you Run Planner or Desk Chat.
- **Done ledger** — case / mail / Slack identity keys in this Chrome profile.
- **Published page** — `skill/out/current.html` on disk, served by the local Python bridge.
- **Native messaging** — Chrome starts `com.kgarai.engineerdayplanner.bridge` so the toolbar can launch that local bridge. The host JSON lives under this OS user’s Chrome NativeMessagingHosts folder after the one-time Python helper.

## What the gather uses

Run Planner calls tools **already logged in on this machine** (OrgCS, GUS, Slack, Gmail, Calendar). Those hosts keep their own OAuth. This extension does not collect a Salesforce or Google password.

## Cookies

The `cookies` permission is for the local planner page / OrgCS session checks on this profile. It is not used to sell or sync cookies off-device.

## Network

- Localhost to the planner bridge.
- Your Express gateway, only with the token you paste.
- OrgCS Lightning URLs listed in the manifest, for case links in the page.

No analytics SDK. No remote crash reporter in this extension.
