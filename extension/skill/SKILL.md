---
name: engineer-day-planner
description: engineer day planner — a local clickable planner page only (never paste the briefing into chat). Start of Day (full day plan), Mid-Day (course-correct the remainder), End of Day (close out + tee up tomorrow). Auto-detects daypart from this engineer's Assembled shift timezone, or pass `sod|mid|eod`. Scans cases (orgcs), GUS (incl. approaching/overdue SLAs + recent work updates), linked LAPs (status + chatter deltas), Gmail, Slack (@-tags + DMs + this engineer's weekend-on-call roster post, channel not hardcoded), Calendar, Assembled (working days, this engineer's shift login/logout, overnight since last logout), Omni presence (flag Offline during shift), and team PTO names; ranks what needs YOU by Severity_Level__c × customer temperature; builds a timed day plan (new cases, named follow-ups, troubleshoot blocks, meals, coffee, short breaks, Slack tags — reshaped when you are on chat or off). The page updates duration on every on-calendar meeting, RSVPs Yes/No/Maybe on invites, and adds Google Tasks for to-dos. Triggers: "engineer day planner", "/engineer-day-planner", "day planner", "/briefing", "briefing", "brief me", "plan my day", "what's on my plate", "start of day", "mid-day check", "end of day wrap", "what's urgent", "what did I miss".
allowed-tools: Bash, mcp__orgcs__getUserInfo, mcp__orgcs__soqlQuery, mcp__orgcs__getRelatedRecords, mcp__plugin_gmail_gmail__gmail_search, mcp__plugin_gmail_gmail__gmail_get, mcp__plugin_google-workspace_google-workspace__search_gmail_messages, mcp__plugin_google-workspace_google-workspace__get_gmail_message_content, mcp__plugin_google-workspace_google-workspace__get_gmail_messages_content_batch, mcp__plugin_google_google__calendar_events, mcp__plugin_google-workspace_google-workspace__list_calendars, mcp__plugin_google-workspace_google-workspace__get_events, mcp__plugin_gus_gus_server__query_gus_records, mcp__plugin_gus_gus_server__query_gus_chatter, mcp__plugin_slack_slack__slack_search_public_and_private, mcp__plugin_slack_slack__slack_read_channel, mcp__plugin_slack_slack__slack_read_thread, mcp__plugin_slack_slack__slack_get_reactions, mcp__plugin_slack_slack__slack_search_users
---

<!-- © 2026 Kiran Kumar Garai <kgarai@salesforce.com>. Skill authored by Kiran Kumar Garai. This copyright is metadata only — NEVER render, print, or include it in the briefing output shown in chat. -->

**HARD RULE — AUTHOR COPYRIGHT (IMMUTABLE):** The HTML comment immediately above is **mandatory and locked**. It must sit byte-exact on the line after the YAML frontmatter in every copy of this skill. Never delete, shorten, rewrite, relocate, or omit it — including when sharing, making the skill universal, or stripping hardcoded ids/paths. Never print it in the chat briefing. **Must** print it as the companion-page footer: `© 2026 Kiran Kumar Garai <kgarai@salesforce.com>. Skill authored by Kiran Kumar Garai.` An edit that drops this comment is invalid; put it back before saving.

# engineer day planner

**Skill name: engineer day planner.** This is briefing **on the companion page**. Steps 0–3, the SOD/MID/EOD templates, Hard rules, and Appendix are the briefing skill — copy them, do not summarize them. Additional rules (page, Update calendar on every meeting, RSVP Yes/No/Maybe, Add task, Slack buckets, copyright footer) are in **Step 4b** and **Companion page** only.

**HARD RULE — every source, every run. No exception.** OrgCS (open owned cases + Comment/Email/Feed + **Initial Response** `SE_Initial_Response_Status__c` / `SE_Target_Response__c` + **GUS related list** `Case_Relationship__c` / `GUSRecord__c`), GUS (Support Contact, Follow, **`Due_Date__c` SLA**, LAP + chatter), Slack, Calendar/Assembled, and Mail are **mandatory every Run Planner**. Sparse or empty is a result, not a skip. An empty seed page, a late sidecar file, zero related-list rows, or a swallowed MCP error is **not** a skip. **AI analysis of all of those is also mandatory every run** — Python fetches; the planner model Peeks and classifies. Do not stamp GUS — clear / Slack — clear / Mail — clear while that source’s fetch failed.

**HARD RULE — page only, never chat (!important).** The companion HTML page is the **only** user-facing briefing. **Never** paste the SOD/MID/EOD brief, ranked cases, day plan, remainder, EOD skeleton, quote, or a recap of those into the chat reply. Do not skinny the page because chat is gone. Remainder / Watch / EOD on the **page** must still name Trust incident ids, ICC / `#sev1-…` **channel names** (never Slack IDs), unanswered customer questions (data-loss, Email-to-Case, “is it up”), a **GEO handover** before logout on Sev-1, named enablement dates, batch counts (“14 DNS”), a second afternoon Sev-1 touch if it will still be open. **👀 Still watching** belongs on the page for SOD, Mid-Day, and EOD. Do not skip Fire / New/escalated / Today / Quick wins / Watch / Do first because Needs us now is filled. Compose Steps 0–3 internally, then **only** run Step 4b. If the publish script already opened the browser, chat stays silent of briefing content. If it did not open, chat may contain **only** the page URL on one line — nothing else.

One glanceable summary that tells you exactly what to do next **and when** — on demand, tuned to where you are in the workday. Rank Fire/Today so the punchline is fast; **do not** trade away Watch, Sev-1 remainder facts, named DNS batches, or EOD close-out to hit a word count. **It is shown on the local page — never in this chat, never posted to Slack or mail.**

**Who this engineer is** comes from the OrgCS User every run — never a hardcoded name, title, manager, cloud, or skill group. **Stamp Name and Manager on the page header** (SOD, Mid-Day, End of Day). Do **not** print a heading called `Identity` (not on the page, not in chat). Cloud / Skill Group only if the User record says so. Full rule: **Appendix — Identity**.

**Shift timezone** comes from OrgCS `Engineer_Shift__c`. If that field is empty, Assembled. If hours are still empty, 8:00 AM–5:00 PM in that zone. Stamp the resolved **12-hour** shift line **on the page header** on **every** daypart — never glued to Follow-up due, never as Assembled UTC math (`15:00Z`). Full rule: **Appendix — Shift clock**.

**HARD RULE — MCP arguments (do not rediscover).** Call these tools with **these names only**. Do not ToolSearch schemas. Do not pass `query` for SOQL — OrgCS treats a missing `q` as an empty statement (`MALFORMED_QUERY`).

- `soqlQuery`: `{ "q": "<SOQL>" }` only.
- `query_gus_records`: `{ "soql": "<SOQL>" }` only.
- `query_gus_chatter`: `{ "record_id": "<Id or W-xxxx>" }`.
- If a tool says the result was saved to a file, run `python3 "$DAY_PLANNER_SKILL/scripts/slim-tool-result.py" "<path>"` (stdout `ok …` only). **Do not Read the overflow file.** Do not Bash `find` / `wc` / `ls` under `~/.claude/projects`.
- Do not `date` in `Asia/Kolkata` or the machine zone. Resolve Assembled / `Engineer_Shift__c` first, then `TZ=<that IANA zone> date`.
- Slack: `slack_search_users` on this OrgCS Name, then paginate `is:dm`, `<@USERID>`, and `is:thread <@USERID>` since last logout. Do not search `to:@Full Name`. `is:unread` is not enough.

**Output:** publish the companion HTML page (Step 4b) and open it. **Do not** render the finished brief as chat markdown. No canvas, no mermaid, no `.canvas.tsx`. Gather of Salesforce / Slack / mail stays read-only. Calendar writes happen **only** when this engineer clicks **Update calendar** or **Yes / No / Maybe** on the page. Never post to Slack or send mail from this skill. Full customer detail (names, case #s, org IDs) is fine on the local page.

**Do not add a preamble** in chat that restates the daypart or the title (`This run is End of Day — …`). Do **not** narrate omitted sources (`No WOC line — no roster`, `WOC not found`, `no roster`, Omni skip, etc.). Absence is silence.

**Do not replace or drop previous sections.** This skill already had Fire, Today, Quick wins, Unblocked overnight, **👀 Watch / Still watching**, Day plan, Do first, Mid-Day remainder / Next 2h, and the EOD close-out (Wins, Before you log off, Handoff, Tomorrow first thing, Loose threads, next-working-day skeleton / WOC remainder, Walk away). **Follow-up due** is an **added** last ranked case block. Keep every previous section. **EOD stays EOD** — do **not** replace the next-working-day skeleton with a full Day plan. A MID remainder that only names `#Case — one-liner` while skipping Trust / ICC channel / unanswered customer question / GEO handoff / the DNS split (batch-nudge vs already-nudged Watch) is incomplete.

## Timezone — read this first

The machine clock is not the shift clock. OrgCS `Engineer_Shift__c` is the shift. Assembled is next, only when that field is empty. 8:00 AM–5:00 PM in the shift zone is the last resort when hours are still empty. Hours differ per engineer. Never assume a night window, AMER-PST, or AMER-EST.

After the zone is resolved:

```bash
TZ="<resolved-iana>" date '+%A %Y-%m-%d %-I:%M %p %Z'
```

Never `TZ="America/Los_Angeles"` unless the resolved zone is actually Pacific. Never trust the bare machine clock. Day-plan lines are in the **resolved shift timezone** (12-hour AM/PM). Add a body-clock conversion in parentheses only when it helps a meal/break decision. Mapping, disagreement, and “both missing”: **Appendix — Shift clock**.

## Step 0 — Pick the daypart

If an explicit arg was passed (`sod`/`mid`/`eod` or a clear synonym), use it. Otherwise auto-detect from current time in the **resolved shift timezone**, relative to today's Assembled shift (login / logout from Assembled; if Assembled has no hours today but the zone is already known, 8:00 AM / 5:00 PM in that zone):

| Shift-zone window | Daypart | The question it answers |
|---|---|---|
| before login + ~3h (typical 11:00 AM) | **SOD** — Start of Day | *What's the plan? What caught fire since last logout? How do I spend the day?* |
| until logout − ~2.5h (typical 2:30 PM) | **MID** — Mid-Day | *Am I on track? What changed? Rebuild the remainder.* |
| last ~2.5h of shift (typical 2:30 PM onward) | **EOD** — End of Day | *What got done, what's owed before logout, what's the next working day's first move?* |

If the shift timezone is unknown, do not auto-detect from Pacific. Warn and use an explicit `sod`/`mid`/`eod` if one was passed; otherwise deliver a short brief without inventing daypart from a default zone.

Every daypart pulls the same sources but **frames, filters, ranks, and plans** them differently (Steps 2–3).

## Step 1 — Gather (all in parallel, read-only)

Fire these together to minimize wait:

1. **Identity + clock** — `mcp__orgcs__getUserInfo` (user id, Name, Title, Email, Manager). Then SOQL that User (`AboutMe`, `Engineer_Cloud__c`, `Engineer_Shift__c`, Manager.Name). Confirm `OwnerId` = this `userId`. Note current time in the **resolved** shift zone after Assembled is read — not OrgCS User timezone and not PST by default. Identity / Cloud / Skill Group / shift-picklist rules: **Appendix — Identity** and **Appendix — Shift clock**.
2. **Open cases** — `mcp__orgcs__soqlQuery`:
   ```sql
   SELECT Id, CaseNumber, Subject, Description, Status, Severity_Level__c, CreatedDate, LastModifiedDate, Contact.Name, Contact.Email, SE_Initial_Response_Status__c, SE_Target_Response__c, First_Response_Date_Time__c, GUS_Investigation_Number__c, Display_Bug__c
   FROM Case
   WHERE OwnerId = '<user-id>' AND IsClosed = false
   ORDER BY LastModifiedDate DESC
   ```
   **Always read `Description`** on every open case — not only brand-new ones. It is the customer's literal problem statement and often holds the question they still want answered; ranking and "what's owed" without it miss the point. Rank Severity in application logic (Level 1 → Level 2 → …) — do **not** trust `ORDER BY Severity_Level__c` string sort.

   **Initial Response (case SLA, every run):** `SE_Initial_Response_Status__c` (label **Initial Response**) plus `SE_Target_Response__c` (Response Target). **Pending** = status is not `Met` / `Completed After Violation`, or status is blank **and** a Response Target is set **and** `First_Response_Date_Time__c` is empty. Overdue/due-today pending IR is heat. Met is done — still print `- ir: Met` on the digest so Peek can see it.

   **OrgCS GUS related list (every run, never skip):** a case is tied to GUS by a `Case_Relationship__c` row (`Case__c` required). Two shapes. **GUS Work** (record type GUS Work): `GUS_Work__c` lookup to Bug / Investigation / User Story. `Type__c` and `GUSText__c` are blank. The W-number is `GUS_Work__r.Name`. Record type, status, and subject come from `Work_Record_Type__c`, `Work_Status__c`, `Work_Subject__c`. **LAP / PRB / RCA / Change / GitHub / CPR:** `Type__c` plus `GUSText__c` (and `GUS_PRB_LAP_Link__c`). `GUSRecord__c` is a second list (`AssociatedCase__c` + `GUSNumber__c`) and may be empty when the relationship is a GUS Work row. Query both on **all open owned Case Ids**. Empty rows are a result, not “no GUS.”
   ```sql
   SELECT Case__c, Name, RecordType.Name, Type__c, GUSText__c, GUS_PRB_LAP_Link__c, Work_Status__c, Work_Subject__c, GUS_Work__c, GUS_Work__r.Name, Work_Record_Type__c
   FROM Case_Relationship__c
   WHERE Case__c IN ('<open-case-ids>')
   ORDER BY LastModifiedDate DESC
   ```
   ```sql
   SELECT AssociatedCase__c, Name, GUSNumber__c, Type__c, Link__c
   FROM GUSRecord__c
   WHERE AssociatedCase__c IN ('<open-case-ids>')
   ORDER BY LastModifiedDate DESC
   ```
   Map rows back to OrgCS `#CaseNumber`. An investigation is `Work_Record_Type__c` + `GUS_Work__r.Name` (W-), not a blank `Type__c`. Feed that W- / LAP / PRB number into the GUS work/chatter gather. A missing-column / `INVALID_FIELD` error is **not** fatal: drop the bad field, still query the other object, still publish.

   **OrgCS unreachable.** If `getUserInfo` or case SOQL errors, or the OrgCS MCP is unloaded: do **not** invent this engineer's live queue. Do **not** add sample cases. If `out/current.html` already exists, **keep every previous row** and stamp `offline` (`source: OrgCS`) so the page pops a notice. If there is no last page, publish an empty board. Never copy a published page into the Chrome extension.

   **Run Planner CLI:** skip the body SOQL below. Python already clipped CaseComment **and** EmailMessage `TextBody` (and feed) for **this engineer's open owned cases only** (`OwnerId` + `IsClosed = false`) into `/tmp/planner-digest.txt`. CaseComment alone is never enough. Closed, transferred, or **reassigned** leftovers from the previous page are dropped — never Peek them. Gather JSON has no activity arrays. Never SELECT `CommentBody` / `TextBody` / `HtmlBody` in that CLI — that compact-kills the run. Analyze from the digest file (Read once).

   For each open case (interactive briefing only, not Run Planner CLI), read **all three** activity surfaces — CaseComment alone is never enough (customers often reply only by email; internal asks often live only on feed/chatter):

   **(a) CaseComment**
   ```sql
   SELECT CommentBody, CreatedDate, CreatedBy.Name, CreatedBy.Id, IsPublished
   FROM CaseComment WHERE ParentId = '<case-id>' ORDER BY CreatedDate DESC LIMIT 8
   ```
   If the oldest of those 8 is still inside the age window you are judging (Follow-up due, overnight, last customer-facing), pull more. Internal (`IsPublished = false`) comments are first-class for **holds and scheduled touches**.

   **(b) EmailMessage — mandatory** (sacred for "customer replied / waiting on you")
   ```sql
   SELECT Incoming, MessageDate, TextBody, FromAddress, FromName, Subject, CcAddress
   FROM EmailMessage WHERE ParentId = '<case-id>' ORDER BY MessageDate DESC LIMIT 6
   ```
   The latest **Incoming = true** row is often the true last customer word. Never decide "awaiting customer" or "needs you" from CaseComment alone.

   **(c) CaseFeed / chatter — all relevant posts** (TextPost, LinkPost, ContentPost; include nested feed comments when present)
   ```sql
   SELECT Id, Body, CreatedDate, CreatedBy.Name, Type
   FROM CaseFeed
   WHERE ParentId = '<case-id>'
     AND Type IN ('TextPost','LinkPost','ContentPost','CaseCommentPost','EmailMessageEvent')
   ORDER BY CreatedDate DESC LIMIT 15
   ```
   For any TextPost/LinkPost with a Body (colleague handoff, LAP link, "customer responded", reject/refile ask), also pull nested comments:
   ```sql
   SELECT CommentBody, CreatedDate, CreatedBy.Name
   FROM FeedComment WHERE FeedItemId = '<feed-item-id>' ORDER BY CreatedDate ASC
   ```
   Treat feed/chatter the same altitude as comments/emails for ranking — an internal "LAP rejected, please refile" or "customer responded, take it forward" is a **FIRE / TODAY** signal even when the last public CaseComment is yours.

   **Promised closes — every open owned OrgCS case, every run.** Read this engineer's **last public** CaseComment (`IsPublished = true`), last **outbound** email (`Incoming = false`), and last **internal** note (`IsPublished = false` CaseComment or CaseFeed TextPost). If that message already promised a close, record the promised day — do **not** invent a close. Route it in Step 2. Stamp `promisedClose: true` and `promisedCloseOn: YYYY-MM-DD` (shift TZ).
   - **EOD / same-day** (promised day = the Assembled working day of that comment/email/note, not every later EOD): "EOD", "by EOD", "end of the business day today", "close today", "tonight", "before I log off", wait-until-EOD-then-close, or "I will go ahead and close this case" with no later date.
   - **Named or relative day:** "Friday", "Monday", "tomorrow", "18 Sep", "by {date}", "in 2 days", "in two business days". Resolve in the shift timezone from the **comment date** ("tomorrow" / "in 2 days" start from that note, not from later runs).
   - **Not a promise:** offer-and-await only ("If you are all set, please let us know so that we can go ahead and close this case" + "Looking forward to your response") with no EOD/date. A later customer keep-open or new question **cancels** the old promise.
   - **Clock:** that day's Today's plan gets a 🔒 Case closures block (10-min reminder). Overdue promised closes still sit on **today's** clock + Before you log off.

   **Customer-suggested meetings — incoming email or public customer CaseComment only, every open owned case.** Internal notes and this engineer's outbound do not count. Use it when the customer asks to meet.
   - They named a day or a time ("I'm available Thursday 10:00 AM PT", "can we meet at 2pm", "I'm free 3–4pm tomorrow"). Resolve the slot in the **shift timezone** unless they named another zone. "Tomorrow" / a weekday is from that comment/email's date. Start-only → **30 minutes**. Write `meetingStartStamp` / `meetingEndStamp`. The page groups this under **Named a time**. **Schedule meeting** opens calendar compose.
   - They asked to meet and named no day and no time ("can we connect", "let's get on a call"). Leave both stamps off. Do not invent a slot. The page groups this under **No time yet**. **Ask for a time** opens the case. There is no calendar button on that row.
   - **Guests:** always include the customer's email — `Contact.Email`, or that incoming mail's `FromAddress` if Contact.Email is empty. If they also asked to add / invite / include named people or emails on the meeting, add those extras too. If they did **not** name anyone else, guests = **just the customer's email**. Never include this engineer's address. Ordinary CCs on a case email are **not** invitees unless the body asks to add those people to the meeting.
   - **Ask for a time** needs the case link, not an email. **Schedule Meeting** stays off when there is no customer email.
   - **Page:** one **Customer asked for a meeting** section **immediately under Needs us now** (`tone: now`), with **No time yet** above **Named a time**. Do not park the unbooked row in Rest of day. `caseNumber` (digits, no `#`), `caseSubject` (`Case.Subject` verbatim), `customerEmail` when known. Stamps and `meetingGuests` only when they named a day or a time. Do **not** overwrite the row's work-block `startStamp` / `endStamp` with the meeting slot.
   - **Schedule Meeting** opens one Google Calendar compose: subject **and** invite body `Case Discussion: {caseNumber} ({Subject})`, all guests. Never copy planner `detail` into the invite. Engineer hits Save there — not a silent calendar write. Clicking the button does **not** mark Action taken. After they save, **Refresh calendar** confirms the event: this block turns **green** (`Action taken — scheduled.`) and the meeting lands on **Today's plan**.

3. **Case dependencies + LAP updates — always run.** This engineer is **not** the GUS LAP requestor in the common case. Join LAPs from **owned OrgCS case numbers** (and Follow — Step 1.4). Do **not** invent `Related_Case__c` or `Relationship_Type__c`.

   **Primary (GUS Case, every run if GUS Connected):** after the open-case list, take those `CaseNumber` values (digits only). Resolve the GUS User in Step 1.4. Then:
   ```sql
   SELECT Id, CaseNumber, Subject, Status, LAP_type__c, LAP_Global_Case_Number__c, LastModifiedDate, CreatedDate
   FROM Case
   WHERE RecordType.Name = 'LAP'
     AND LAP_Global_Case_Number__c IN ('<owned CaseNumber>', …)
   ```
   Chunk the `IN` list at **100** numbers. Map each row back to OrgCS `#LAP_Global_Case_Number__c`. Empty `IN` → skip (do not send `IN ()`).

   **Also** query any Followed GUS Case Ids from Step 1.4 whose `RecordType.Name = 'LAP'` and `IsClosed = false` (even when the OrgCS number is not in this queue).

   OrgCS `Case_Relationship__c` is **mandatory every run** (Step 1.2) — empty is a result, not a skip. A GUS Work row links `Case__c` to `GUS_Work__c` (W-number on `GUS_Work__r.Name`; `Type__c` and `GUSText__c` blank; record type on `Work_Record_Type__c`). A LAP/PRB/RCA/Change/GitHub/CPR row uses `Type__c` and `GUSText__c`. `GUSRecord__c` is the other list and may be empty. A missing-column / `INVALID_FIELD` error is **not** fatal: drop the bad field, keep the GUS Case query, still publish.
   ```sql
   SELECT Id, Name, RecordType.Name, CreatedDate, CreatedBy.Name, GUSText__c, Type__c, GUS_PRB_LAP_Link__c, GUS_Work__c, GUS_Work__r.Name, Work_Record_Type__c, Work_Status__c, Work_Subject__c
   FROM Case_Relationship__c
   WHERE Case__c IN ('<open-case-ids>')
   ORDER BY CreatedDate ASC
   ```

   For **every** LAP Case found (GUS query, Follow, relationship, or a LAP URL on CaseFeed), in the same gather wave:
   - Pull **recent chatter** on that LAP Case (`query_gus_chatter`) since the daypart window start — approvals, rejects, Black Tab / bot comments, reviewer asks.
   - **Surface LAP deltas** whenever status changed or new chatter landed in-window: **Approved** / **Rejected** / **Pending Approval** / reviewer question / applied-limit note. Map back to the OrgCS `#CaseNumber`.
   - A blocker that *just cleared* (**Approved** / limit applied) → FIRE (tell the customer) **and** Unblocked overnight on SOD when it cleared since last logout. **Rejected** → FIRE/TODAY (refile or get justification). **Pending** with no new ask → WATCH, but still print a one-line LAP status so it isn’t invisible.
4. **GUS work + Follow + SLA — always run, never skip** (`mcp__plugin_gus_gus_server__query_gus_records`). This engineer is **not** `Assignee__c`. Open work is **Support Contact** (`CS_Contact__c`) and/or **Follow** (`EntitySubscription`). Never filter `ADM_Work__c` by `Assignee__r.Email` / `Assignee__c`. Do **not** hardcode a GUS User Id.

   Resolve the GUS User from Step 1 Email (same Salesforce login email as OrgCS `getUserInfo`):
   ```sql
   SELECT Id, Email, Name FROM User WHERE Email = '<this-user-email>' LIMIT 1
   ```
   That `Id` is `gusUserId`. Zero rows → stamp GUS offline (email did not match a GUS User). Do not fall back to Assignee.

   **Support Contact (open work):** `Closed__c` is a 0/1 numeric formula — open is `Closed__c = 0`.
   ```sql
   SELECT Id, Name, Subject__c, Status__c, Priority__c, RecordType.Name, Due_Date__c, Sprint__r.Name,
          CS_Contact__r.Name, Assignee__r.Name, LastModifiedDate
   FROM ADM_Work__c
   WHERE CS_Contact__c = '<gusUserId>'
     AND Closed__c = 0
   ORDER BY LastModifiedDate DESC
   ```

   **Follow:** GUS “Follow this work / case” is `EntitySubscription` (`SubscriberId` = this user, `ParentId` = work `a07…` or Case `500…`). `ADM_Work_Subscriber__c` is a mail-subscription object — do **not** use it as Follow. Salesforce requires `SubscriberId` on this query:
   ```sql
   SELECT ParentId FROM EntitySubscription WHERE SubscriberId = '<gusUserId>' LIMIT 200
   ```
   Split `ParentId`s: work vs Case. Then:
   ```sql
   SELECT Id, Name, Subject__c, Status__c, Priority__c, RecordType.Name, Due_Date__c,
          CS_Contact__r.Name, Assignee__r.Name, LastModifiedDate
   FROM ADM_Work__c
   WHERE Id IN ('<followed work ids>')
     AND Closed__c = 0
   ```
   ```sql
   SELECT Id, CaseNumber, Subject, Status, RecordType.Name, LAP_type__c, LAP_Global_Case_Number__c, LastModifiedDate
   FROM Case
   WHERE Id IN ('<followed Case ids>')
     AND RecordType.Name = 'LAP'
     AND IsClosed = false
   ```
   Union Follow work with Support Contact work (dedupe on `Id`). Skip empty `IN ()`. Print assignee **name** on the row (who owns the bug) — still not a filter. `Priority__c` here is GUS `ADM_Work__c.Priority__c`, not OrgCS `Case.Priority`.

   Surface anything with activity in the window, P0/P1, or a Support Contact / Follow item awaiting this engineer.

   **SLA (mandatory every run).** The model judges. Python copies fields and does not mark overdue or approaching.
   - Investigation fields, as stored: `Out_of_SLA__c`, `SLA__c`, `SLA_Warning_Notification_Sent__c`, `Number_of_SLA_Violations__c`, `Due_Date__c`.
   - GUS Bot DM (Work Notifier) is the Slack feed for that work: status, assignee, severity, Chatter text, closing summary, and "update SLA has been violated".
   - Mail from `slamonitor@salesforce.com` is the same signal: warning due in under 2 hours, or Out of SLA.
   - A LAP has no SLA. Use requested start `SM_Target_Execution_Date_Time__c` and requested end `SM_Target_Execution_End_Date_Time__c`.
   - Zero keepers → **"🛠️ GUS — clear"**. Never silently drop GUS.

   If the GUS MCP tool isn't loaded, fall back to `sf data query --target-org gus -q "<same query>"`. `query_gus_records` / `query_gus_chatter` **is** GUS on this machine. A result written to a file because it was large is **success**, not unreachable. Do not stamp GUS offline because a host-specific tool name was missing, or because you did not also run `sf`.
5. **Gmail — always run, never skip, never snippet-only, never first-page-only.** Search **and then open** leftover human mail. A search snippet is **not** a read. Same bar as Slack. On Cursor / Claude google-workspace the tools are `search_gmail_messages` and `get_gmail_message_content` / `get_gmail_messages_content_batch` — that **is** Gmail. Not finding `mcp__plugin_gmail_gmail__gmail_search` is not a Gmail outage.

   Search **since this engineer's previous working-day logout** (see Assembled), plus **+1 calendar day** for missed mail. Size `newer_than` to cover that timestamp. Run Planner leftover fetch uses **4 days on Monday** and **2 days** the rest of the week, in the shift timezone. **`is:unread` / `is:important` is not enough** — do **not** pre-filter the search to urgent words, `is:important`, customer/manager/escalation, or deadline-today (that is why mail was getting dropped). Do not stamp Mail empty after `is:unread OR is:important`.

   **Paginate three searches** (`search_gmail_messages`, `page_size: 50` — default is 10). Follow `page_token` until none or **5 pages (250 hits)** each. Overflow file → slim stdout; do not drop a page because it overflowed.
   1. Unread — `is:unread in:inbox` (every unread, including unread replies inside threads)
   2. To me — `to:me in:inbox newer_than:Nd` (mail addressed to this engineer)
   3. Inbox window — `in:inbox newer_than:Nd` (the rest of the leftover inbox, including threads)

   Dedupe **one row per threadId**. Do **not** keep one mail and drop the rest.

   **Drop from snippets** (do **not** open these bodies):
   - bulk / newsletter / marketing / `list-unsubscribe`
   - `noreply@` / `no-reply@` / `notifications@` automation with no human ask
   - calendar invite / accepted / declined ICS mail (the meeting is already a calendar row)
   - OrgCS case-assignment / **case-comment / case-thread** Gmail (`ref:!`, Outbound Contact, `customersupport@salesforce.com`, Email-to-Case, “New Case Comment”) — already tracked on the case row.

   **Open leftovers.** `get_gmail_messages_content_batch` (`format: full`, groups of ≤25) on leftover human / Chatter / GUS / Black Tab hits. Cap **40**. Do **not** skip a thread because the snippet looked like FYI, a digest subject, or “not time-sensitive.”

   **Keep** Chatter, GUS, Black Tab, and other human mail: customers, coworkers, manager, partners, vendors, replies, and person-to-person FYI. Do not require customer / manager / escalation / deadline to keep a message. Person-to-person FYI stamps Mail (Not opened / Needs a reply) — not discarded. Do not drop a GUS / Chatter / Black Tab message because it mentions a case number.

   Zero leftover humans **after pagination + drop + open** → "✉️ Gmail — clear" / `Mail — clear`. Do not stamp clear while a `page_token` was still available or unread/leftover-human hits were skipped. Never silently omit the source. Case-thread-only Gmail may stamp Mail — clear.

   **Page:** stamp kept mail on a **Mail** section (`kind: "mail"`, `mailUrl` = the Gmail web link). Open email is the control. Do not dump newsletters onto the page.
   6. **Slack — mandatory every run, never skip, never search-only, never first-page-only.** Search **and then open** the messages. Keyword hits with empty `Text` (Calendar / STORM / Career Connect bots) are **not** a read. A brief that stamps `💬 Slack — clear` without opening human DMs and mention threads is a miss. Scope: **since previous working-day logout** (not a hardcoded Friday 5 PM), plus **+1 calendar day** so a missed DM/tag is not dropped. Run Planner leftover fetch uses **4 days on Monday** and **2 days** the rest of the week for Slack, unread mail, and investigation Chatter. Slack search `is:unread` is **not** sufficient here (it often returns zero DMs while leftover humans exist) — do not use it as the only filter.
   - **Paginate three searches** (`slack_search_public_and_private`, `include_context: false`, `response_format: detailed` so Channel ID + Permalink exist, `limit: 20`, `sort: timestamp` / `desc`, `after:YYYY-MM-DD` or `after` unix = last logout). Follow `cursor` until “No more pages” or **5 pages (100 hits)** each. `to:<@USER>` is DMs-to-you, **not** channel mentions.
     1. DMs — `query: is:dm`, `channel_types: im,mpim`
     2. Mentions — `query: <@this-user-id>`, `only_my_channels: true`
     3. Threads — `query: is:thread <@this-user-id>`, `only_my_channels: true`
     Dedupe **one row per channel**. Overflow file → slim stdout; do not drop the mention page because it overflowed.
   - **DMs — open them.** After the paginated DM search, **`slack_read_channel`** on each leftover **human** DM / MPIM (`response_format: detailed` so reactions are in the history, `limit: 40`; pass the DM `channel_id`, or the other person’s Slack `user_id` as `channel_id`). Python also **`slack_get_reactions`** on the leftover `channel_id` + `message_ts` (read-only — never `slack_add_reaction`). Stamp `- reactions:` on the inbox clip. Do **not** skip a person because the search snippet was blank. Do **not** keep one DM and drop the rest. Bots can be skimmed; people cannot.
   - **All @-mentions / tags — open the thread.** After the paginated mention + thread searches, **`slack_read_thread`** (channel + parent `message_ts`, **detailed**, `limit: 80`) on each leftover hit that is not already a DM row. Same reaction fetch. Include channel asks, Sev-1 covers (`#sev1-…`), handoffs, and “@…” pings even when the DM inbox looks empty. Resolve Slack identity from the OrgCS User **Name**. Cap **20** DM opens + **20** thread opens.
   - **Only this engineer’s tags** — suppress mentions of other people who share a first name.
   - **Judge before you plan a reply** — do **not** auto-FIRE every DM/tag as “reply owed.” Python does **not** keep/drop. Read the opened thread **and** `- reactions:`:
     - **Already handled** — you replied in text, **or** a closing reaction (`ack`, ✅, 👀 as acknowledgment, etc.) → treat as done. Do **not** invent a “day-plan reply” / follow-up text just because there is no prose reply. Closing reactions can drop even when `lastHumanIsMe` is false.
     - **FYI / no ask** — heads-up, share, joke, “fyi”, or content that doesn’t need your words → print under WATCH/notes as **“{Person} sent you …”**, not as a plan action.
     - **Real ask still open** — tagged + clear question/request + **no** reply and **no** closing reaction → FIRE (or next ≤15m gap), same altitude as a customer reply.
   Rank ruthlessly in Step 2 (drop noise and already-handled threads from the *printed* brief), but **do not skip opening DMs/mentions** because a search looks quiet. Zero *actionable* Slack items **after opening** → "💬 Slack — clear" (FYI notes may still appear under WATCH); never silently drop Slack from the gather.
   - **Sev-1 / incident channels — mandatory when you own a Level 1.** For every owned Level 1, search Slack for the ICC / incident / `#sev1-{CaseNumber}-…` channel (by case number and account name). **Open it** (`slack_read_channel` or `slack_read_thread`). Print the **channel name** (`#icc-…`, `#sev1-…`) in Needs us now, the remainder, and any GEO handoff — never a Slack ID. Trust incident URLs from case comments go in the same lines.
   - **Weekend on-call (WOC) schedule — always run, not overnight-scoped.** Teams post the roster in **their own** channel; **never hardcode a channel name or id.** Search **this engineer’s** Slack (`only_my_channels` / channels they belong to) for the latest **roster post** covering the **upcoming or current weekend** (look back ~14 days — these often land mid-week). Match phrasing like `Week end on call Schedule`, `Weekend on call Schedule`, `Weekend Roster`, `WOC roster`, `weekend coverage schedule` — a dated Sat/Sun (or holiday-weekend) **name list**, not random WOC chatter. `slack_read_thread` on that parent for **swaps** (`X will be doing WOC … in place of Y`). Parse whether **this engineer** is listed (Slack mention or name) under which day(s), plus role if present (`Dev case`, `Chat`, …). Do **not** print the rest of the roster. If this engineer is scheduled, the **WOC remainder** belongs on **EOD of the last Assembled working day before that WOC day** (usually Friday; if that day is off, the working day before it) — not Monday, not Saturday SOD. Also search (same, no hardcoded channel) for `Task Remainders` / `TASK REMINDER` / `Task remainders` that name this engineer; those cases are the remainder. None posted yet → still tee up owned hot/Sev1 + GHO, and note remainders have not landed **only if this engineer is on the roster**. No roster post found → **omit every WOC line** (header stamp, remainder, preamble, closer — nothing). Do **not** print `⚠️ WOC schedule not found in Slack`, `No WOC line`, `no roster`, `not on the roster`, or any other absence stamp. Sparse chatter is not a roster. Assembled weekend events still count as a working day.
7. **Calendar — always run, never skip** (`list_calendars` + `get_events` / Claude `calendar_events`). Those google-workspace names **are** Calendar. Not finding `calendar_events` is not a Calendar outage. Do not stamp Calendar offline and tell them to click Refresh Calendar if `list_calendars` / `get_events` returned. **`list_calendars` first every run** — resolve Assembled and team PTO from summaries. Assembled is **not** its own MCP; it is a calendar on that list. Never hardcode a calendar id, calendar email, or a team calendar’s display name. Pull **all three** every run.
   - **Primary** (this engineer’s Google / Salesforce email from `getUserInfo`) — today's Assembled shift window (if Assembled has no hours, 8:00 AM–5:00 PM in the **resolved** zone): meetings, Dinner/Breakfast, Log In, customer calls, and any **your** PTO on this calendar.
   - **Assembled** — discover via `list_calendars`: the calendar whose **summary** contains `Assembled` (often `Assembled Calendar`). **Never pin a calendar id** in this skill. **This is the only working-day source and this engineer's shift clock.** Shift titles: `Casework`, `Chat` / live-queue, `Lunch`, `Break`. `PTO` / `Sick Leave` / `Comp Off` / `VTO` = off. Never override Assembled with a holiday calendar (this engineer may still work a national or site holiday). No shift events that civil day → not scheduled.

     **How much Assembled to pull (do not scan ~10 days):** `Engineer_Shift__c` is the zone + 8:00 AM–5:00 PM fallback when a day has no events.
     - **Today** — always (login/logout, Lunch/Break, Chat vs Casework, PTO/off).
     - **Previous weekday** (Friday when today is Monday) — last logout / overnight. If that day is empty too, use 5:00 PM in the resolved zone from `Engineer_Shift__c`. Do not walk back a week of Assembled.
     - **Holiday / leave today** (no shift events, or PTO/Sick/Comp Off/VTO): do not build an 8:00 AM–5:00 PM plan. Still pull **previous weekday** for last logout. Zone from `Engineer_Shift__c`.
     - **EOD only:** also pull **next weekday** once so the skeleton is not “tomorrow” when that day is off.
     - **Weekend hours:** only if Slack already lists this engineer on WOC that day — then pull that Sat/Sun once. Otherwise omit Assembled weekend hours (WOC stamp is Slack role only).

     **Derive this engineer's timing (resolved shift timezone):**
     - A **working day** = a civil day with Assembled shift events (Casework/Chat/Lunch/Break).
     - **Login** = start of the first shift event that day.
     - **Logout** = end of the last shift event that day.
     - **Previous working day** = the **previous weekday** in the resolved zone (Friday when today is Monday). If Assembled has shift events that day, overnight starts at that logout. If that day is empty, overnight = 5:00 PM that weekday in the resolved zone (`Engineer_Shift__c`). Do not scan a week of Assembled to find a Saturday WOC as “previous.” **Overnight / "since last run" for SOD = since that logout**, not since 5 PM in a default zone.
     - **Today's plan** = login → logout. Hard stop = today's logout.
     - If Assembled is missing for today: see **Appendix — Shift clock** (User `Engineer_Shift__c` 8:00 AM–5:00 PM in that zone when the zone is known; unknown if both sources are empty). Overnight = previous weekday logout in that same zone when known.
   - **Team PTO** — discover via `list_calendars`: a **non-primary** calendar whose summary looks like team time-off (contains `PTO` plus `Team`, or `Team PTO`, or similar). **Never pin a calendar name or id.** If several match, pick the team/PTO calendar that is not Assembled and not this engineer’s primary. If none → omit the Out line (sparse = clear). Today + next 2 calendar days including the weekend. Use **named people only** (`{Name} - PTO`). **Ignore** location-holiday, site-holiday, and national-holiday titles — they are not a working-day or shift-timing parameter. Awareness only — **do not rebuild the day plan** from this calendar.

   Classify **primary** events (for work-block reshape). **Today's plan calendar rows** = Assembled **login − 2 hours through logout + 2 hours**. Outside that pad, include a meeting only when **you judge it actually important** (customer / case call, required POD or Team Connect, this engineer’s own Important / focus hold, Sev-1 war room). Optional keynotes, Salesforce+, office hours, blood drives, and enablement webinars are not important just because they are on the calendar — drop them when they sit outside the pad. Stamp `important: true` on the rows you kept outside the pad. **Never** decide importance with a title regex.
   - **hard meeting** — POD, training, team required
   - **customer call** — external / case discussion
   - **personal meal** — Dinner, Breakfast, lunch (honor if already booked)
   - **personal block** — Log In, Important, focus holds
   - **your PTO** — title contains `PTO`, `Sick Leave`, `Comp Off`, `VTO` (you)
   - **soft enablement** — optional webinars / Decoded / Tableau On Tap (droppable)
   - **free** — gaps ≥15m between the above

   Classify **Assembled** titles (case-insensitive):
   - **chat coverage** — title is/contains `Chat` **or** `Phone` / `Queue` / `Inbound`
   - **casework** — `Casework`
   - **assembled meal / break** — `Lunch` (often 8:00–9:00 AM in the shift zone; merge with primary Dinner if they overlap), `Break`
   - **your PTO / off** — `PTO`, `Sick Leave`, `Comp Off`, `VTO`
   - anything else that looks like live channel time → **chat coverage**

   If `list_calendars` worked, Assembled is reachable even when today has no shift events. Use `Engineer_Shift__c` for the clock when today is empty — that is a **clock**, not an offline popup. Stamp Assembled `offline` only when calendar list/get **errored or the MCP is unloaded**. Empty team PTO → omit the out-line (sparse = clear).
8. **Omni presence — always run, never skip** (`mcp__orgcs__soqlQuery` on OrgCS). An engineer **must not be out of adherence on Omni** while Assembled has a block covering now (Casework, Chat / live-queue, Messaging, Voice, Lunch, Break). Query this engineer's current presence every run:
   ```sql
   SELECT Id, UserId, IsCurrentState, IsAway, StatusStartDate,
          ServicePresenceStatus.MasterLabel, ServicePresenceStatus.DeveloperName
   FROM UserServicePresence
   WHERE UserId = '<user-id>' AND IsCurrentState = true
   LIMIT 1
   ```
   OrgCS has **no status named Offline** — logging out of the Omni widget **clears** the current row. Match the **current Assembled block** (the event covering now — Casework, Chat / live-queue, Messaging, Voice, Lunch, Break). Classify:
   - **Out of adherence** — on a **work** block (Casework / Chat / Messaging / Voice): zero current rows; **Busy**; `End of Work` / contains `Offline`; or Available that does **not** include this block’s channel (Chat-only during Casework, Case-only during Chat, Case-only during Messaging). **Not** during Assembled Lunch / Break / Dinner — staying Offline there is in adherence.
   - **In adherence** — **Screen Sharing** on any block (do **not** use `IsAway`), **or** the Omni status for the Assembled block covering now. Casework → Available that includes **Case**. Chat / live-queue → includes **Chat**. Messaging → includes **Messaging**. Voice → includes **Voice**. Lunch / Break / Dinner → Lunch/Break, **or Offline / no Omni row / End of Work**. Combos that still include the required channel count.
   - Do **not** print on-Omni labels on the page. Do **not** flag combos that still include the required channel.
   Only flag when **now is inside today's Assembled block** **and** they are out of adherence. One quiet line — **do not rebuild the plan**, do not FIRE it:
   `⚠️ Omni: out of adherence — should be available for this Assembled block`
   **Page:** set `omniOffline: true` and stamp `offline` (`source: Omni`, `text: Omni Is Out Of Adherence. You should be available for this Assembled block.`). The page opens a **popup** (not a body card) that shows the **current Omni status** and the **Assembled block covering now**. × / OK dismisses it for this load; it comes back on reload. **Do not** put Omni in a page-body section. Skip the check (and omit the line) when they are off (PTO / no shift events), before login, or after logout. Query error → `⚠️ Omni unreachable` (same popup). This is **this engineer only** — do not roster the rest of OrgCS.

   **Live recheck (Chrome, not Run Planner).** Every 15 minutes the extension calls `GET /omni/check`. **Strict — Assembled calendar only.** Discover it via `list_calendars` (summary contains `Assembled`). Never primary, never team PTO, never holidays, never `Engineer_Shift__c`, never briefing `shiftStart` / `shiftEnd`, never pin a calendar id. Skip when that calendar is missing, today has no Assembled events around now, or Assembled titles are PTO / Sick Leave / Comp Off / VTO. Else match Omni to the **Assembled block covering now**: **Screen Sharing is never out of adherence**; otherwise the status for that block (Available that contains that channel, including combos). **Busy** never on a work block. Offline / no row during Assembled Lunch, Break, or Dinner is in adherence. Query failure is skip, not an alert. Out of adherence → reminder-style popup showing **current Omni status** and the **Assembled block covering now**, with a **different sound** once every **15 seconds** until **Got It**. **Got It suppresses until the next 15-minute poll.** Still out on that poll → start the loop again. Do **not** print on-Omni labels on the page body. Do not rebuild the plan. Do not FIRE. Gather still does not have to re-query Omni (PLAN_SYSTEM skips it).

**Scope the window by daypart (all relative to Assembled):**
- **SOD** — overnight = **since previous weekday logout** (Assembled that weekday, or `Engineer_Shift__c` 5:00 PM if that day had no events). Monday after a Friday shift = Friday logout. Do not pull Saturday Assembled just to start overnight there.
- **MID** — since today's Assembled **login** (not a hardcoded 8 AM).
- **EOD** — the full shift, **plus** anything that will sit unattended until the **next** working-day login if you don't act before **today's logout**. If this engineer is on the Slack WOC roster for the next weekend day, this EOD **is** the WOC remainder (not a Monday skeleton).

## Step 2 — Rank (the intelligence)

Sort everything into these buckets, highest first. Be ruthless on **Fire / Today / Needs us now** — a brief that lists everything there ranks nothing. **Do not** apply that ruthlessness to Watch, Sev-1 remainder lines, named follow-up batches, or the EOD close-out. Those are the briefing, not clutter.

### Work-type tag (every printed case/Slack item)
Assign one primary work type — this drives the day plan:
- **new-case** — needs first response / ownership today
- **reply** — customer (or Slack tag) waiting; short answer
- **follow-up** — you are nudging; ball was in their court or soft deadline
- **troubleshoot** — LAP refile, logs, repro, novel bug, deep diagnosis (≥40m)
- **meeting / prep** — call on calendar; prep in the 15–30m before
- **watch** — waiting with a clear wait indicator; no action yet

### Customer temperature (cases)
- **hot** — waiting on you, escalation language, same-day call, Sev1, repeated pings, outage/limit pain
- **warm** — engaged recently, asked for call/info, aged without closure after a promised date
- **cool** — proactive DNS / Solution Provided with no push; waiting on them quietly

**Sort follow-ups and plan slots by:** Severity (Level 1 → 2 → 3…) × temperature (hot → warm → cool) × age. **Name the case numbers** in follow-up and troubleshoot slots — never a generic “do follow-ups” line.

**🚨 Needs us now — owned cases only, signal only**
A short punchline **above Fire**. Job: the few **owned OrgCS cases** that will escalate or stall badly if not moved today. **Not** a second ranking of the whole brief.

**This section is cases that trip the signal — nothing else.** Slack, Gmail, GUS work/SLA, LAP chatter, calendar, Omni, WOC, and FYI already live in Fire / Today / Watch / GUS / LAPs. Do **not** reprint them here. Do **not** reprint Today follow-ups, Watch wait-on-customer, Quick wins, new-case intake with no heat, **Follow-up due**, or the rest of the open queue. Do **not** print a “left off” / “also open” / “none” list. Never use `Case.Priority`; case urgency is `Severity_Level__c` only.

Print a case here only on judgment (typically **1–5**, never the full open list). Need a real **wait-on-us** (or a wait-indicator that has expired) **plus** heat — not Level 2 alone, not “it is open.”

**Trip the signal** (one strong hit, or several weaker ones together):
- Level 1, or hot Level 2, with the ball on us
- Last customer / TAM / AE word is inbound (CaseComment **or** Incoming email **or** feed) and still unanswered, and the ask is **on us** (act, deactivate, unblock, status)
- Escalation, outage, production / business-stopping, users blocked
- Repeated pings, management CC, TAM/AE chasing the same unanswered ask
- Same-day call they asked for and we have not closed the loop — **not** if we already sent a booking link and are waiting on them to schedule (that is Watch / Today)
- Aged with no progress after a promised update, wait-indicator expired

**Keep out** (already handled elsewhere):
- Solution Provided / NMI / wait-indicator with the ball in the customer’s court
- Quiet DNS/cert with no inbound (customer back with an urgent ask is **not** keep-out)
- Quiet past hold, no heat → **Follow-up due** (last ranked case block)
- New case needing first response with no heat → Today / 📥
- Thanks / resolved → Quick win
- Slack tags, GUS SLA, LAP Approved/Rejected/Pending — stay in Fire / GUS / LAPs even when they mention a case number

Zero hits → **omit the entire heading**. If a case is in this section, **do not also print that same case as a Fire bullet** and **do not also print it under Follow-up due**. It still counts in `_N need you_` and in named day-plan slots. Fire keeps Slack / GUS / meetings / LAP / other Fire items that did **not** trip this signal.

**🔥 FIRE — do now**
- P0 / Sev1 / Critical with pending activity; new Sev1 handoff / cover request (Slack asks count).
- Customer replied overnight / since last run and is waiting on you.
- **Slack: you are @-tagged with a real unanswered ask** (no text reply **and** no closing reaction like `ack`/✅). Do **not** FIRE FYI pings or threads you already acked.
- A meeting today you must prep for, or a customer call requested.
- **A blocker just cleared** — LAP **Approved** / limit applied, GUS bug → Fixed, swarm answered → the customer is already waiting; close the loop. On SOD, also list it under **Unblocked overnight** (that section stays — do not fold it into Fire and delete the heading).
- **LAP Rejected** (or reviewer asked for more justification) on an open owned case → refile / answer today.
- **GUS SLA** — GUS Bot or slamonitor says the update SLA was violated or is due in under 2 hours, or `Out_of_SLA__c` is true, and the Chatter update is still owed.
- Escalation signals — management CC'd, "I've been waiting X weeks".
- **Promised close already past** — last public outbound named EOD/a day that has elapsed, case still open, no keep-open / new customer question.

**📋 TODAY — during business hours**
- Customer replied during hours; a timeline you promised has passed; **new case needing first response**.
- Named follow-ups ranked by severity × temperature that are **not** Follow-up due (quiet past-hold cases have their own last section).
- **GUS Bot update** — status, assignee, or closing summary on an investigation you support or follow. A LAP window uses its requested start and end.
- **LAP Pending Approval** with new chatter/reviewer ping in-window (no full reject yet).
- **Promised close** — last public outbound, outbound email, or internal note said the case would close at EOD, on a named day, or in N days. **The model reads that beat and decides.** The home is **Before you log off** in almost every case (today, a specific day, or in N days). Not Follow-up due, not Needs us now, not Still watching, unless a newer beat cancelled the promise or asked for something else. Offer-and-await is not a close date. Python does not detect or move these.

**⚡ QUICK WIN — < 5 min each (batch these first)**
- Customer confirmed resolved ("thanks / that worked / resolved") → candidate for a closure comment (do not invent EOD/auto-close wording here).
- A soft deadline you set has passed → note a follow-up or closure comment is owed.
- Contact swap, duplicate consolidation, proactive notification with no reply needed.
- Short Slack tag replies.

**🔓 Unblocked overnight (SOD)**
- Blocker cleared since previous working-day logout (LAP Approved / limit applied / GUS Fixed / swarm answered). Keep this heading on SOD. Omit if none.

**👀 WATCH — no action yet, just track**
- Escalations to Product/Eng (did they reply?), active investigations, meetings not yet happened, **LAPs still Pending with no new chatter**, swarms in flight, ball in customer’s court with wait indicator **that has not expired**.
- **Promised close on a future day or in N days** — still the model's call, and still **Before you log off** unless that read says the close is not owed before logout today.
- **Did not trip Follow-up due** — wait/peek owned cases that failed the Follow-up due test (ball with the customer, hold not expired, Solution Provided awaiting confirm, already-nudged DNS). Print under **👀 Still watching**. Not Fire, not Needs us now, not a dump of the open queue.
- **Slack FYI** — “{Person} sent you …” (share / ping / acked already / no ask). Awareness only — **not** a plan “reply” line.
- **Roster notes (one line, not a plan rewrite)** — weekend/WOC is **one** `📅` stamp, never two. Slack roster = who/role; Assembled = hours. If this engineer is on the Slack WOC roster: `📅 WOC {Sat/Sun} — {role}` and, only when Assembled has hours that day, append `· {Casework|Chat} {h:MM AM/PM–h:MM AM/PM {zone}}` on **that same line**. If they are not on the roster but Assembled has weekend events: `📅 Weekend shift — {Sat/Sun} {Casework|Chat} {h:MM AM/PM–h:MM AM/PM {zone}}`. Do **not** also print a “Weekend WOC / Weekend shift” working-day line that restates the same fact. Do **not** print the word `fallback` (or “Assembled empty”) on the stamp — login/logout already live on the Shift line. `Out: {Name} (PTO)` for named people on the team PTO calendar; **`⚠️ Omni: out of adherence`** if this engineer is out of adherence on Omni **during** today's Assembled block. Skip the line when there’s nothing to say. Do **not** add location/national holiday lines. Do **not** print Omni when they are in adherence. Do **not** paste the whole WOC roster.
- Always still **print a LAP status line** for every linked LAP on a hot/open case (Pending / Approved / Rejected + last update age) even when WATCH — never leave LAP state implied.

**🫱 Follow-up due — owned cases only, last ranked case block**

Quiet owned cases past a sensible hold — pending **us** or pending the **customer**. This is **untouched**, not unresponded. Place it **last among ranked case blocks**, immediately **before the plan** (SOD Day plan · MID Rest-of-day · EOD skeleton). Do not put this in the middle of Fire / Today. Full test, holds, and weekend math: **Appendix — Follow-up due**. If it trips, print it last. **Keep the heading when the list is empty** (count 0, no “none” placeholder). Cases already in Needs us now stay out of this list. Named day-plan `FOLLOW-UPS` still include these cases.

**Age by severity — Sev-1 first.** **Level 1 / Sev-1** trips at **1+ business day** untouched and is listed **first** in this block. **Every other severity** (Level 2 / 3 / 4, including quiet DNS / Solution Provided) trips at **2+ business days**. Do not wait two days on a quiet Sev-1.

**Every printed case needs a one-liner.** Case numbers alone are not enough — twenty `#`s will not be remembered. Each bullet is `#CaseNumber — {short what-it-is}` (domain, customer, or the actual problem — one clause, not the full Subject). Same-wave DNS can share a last-touch header, but **each case is still its own bullet with that one-liner.** Never a comma-separated number dump.

**Suppress — don't print, just count:** SAFE cases (you followed up inside the hold window, or you're waiting with a stated timeline that has not expired), bot pings, digests, newsletters, already-handled threads, @mentions of other people who share a first name.

**Guards against false urgency:**
- *Wait-indicator check* — if your last reply said "allow us some time", "with the product team", "I'll keep you updated", or "checking internally" → **NOT** overdue for a customer nudge. Product / investigation with no expired date is a **Follow-up due hold**. Working it ourselves with no public update and no dated hold **is** Follow-up due (pending us). Honor internal follow-up-by date/time and customer N-days / until-date as holds until they elapse.
- *Business-day math* — Sat/Sun in the **resolved shift timezone** do not count. Last reply **Thursday**, today **Monday** = 1 business day (not overdue at 2). Last customer-facing **Friday**, today **Monday** = 1 business day — **do not** flag that as two days untouched. Calendar weekend days are not neglect.
- *Resolution check* — "resolved / fixed / working now / that did it" = quick-win close; "still seeing it / that didn't work / same error" = NOT resolved → FIRE or TODAY.

**Effort tags** (calibrated on 200+ cases): auto-close `2–3m` · Slack tag `2–3m` · KA-backed reply `5–10m` · limit-increase / redirect `10–15m` · multi-question `20–30m` · novel bug / troubleshoot `45–90m` · escalation draft `30–45m`. Use these to estimate total load and to size plan blocks.

## Step 2b — Build the day plan

After ranking, **compose a timed plan** from calendar classifications + ranked work. This is a core deliverable, not an afterthought.

### Daypart scope
| Daypart | Plan scope |
|---|---|
| **SOD** | **Full shift** Assembled login → logout (8:00 AM–5:00 PM in the resolved zone only when Assembled has no hours but the zone is known) — named follow-ups, new cases, meals, coffee, short breaks, meetings |
| **MID** | **Remainder only** from now → logout (do not rebuild the morning) — same named detail as SOD for the leftover hours |
| **EOD** | **Next working-day skeleton** from Assembled (not blindly "tomorrow" if tomorrow is off); meetings TBD until SOD re-pulls calendar. **Keep this skeleton — do not replace it with a full Day plan.** **If this engineer is on the Slack WOC roster** for the next weekend day: **WOC remainder** on this EOD when today is the last working day before that WOC — not Monday, not wait for Saturday SOD. |

### Placement rules
1. **New cases at start of shift** — protect **≥40–60m after Assembled login** (after Lunch/Dinner if that is the first block) for **📥 NEW CASES / first responses**. Do not let soft enablement eat that slot.
2. **Honor existing personal blocks** — if Dinner / Breakfast / Log In / Important are already on the calendar, keep them; **reshape** only on conflicts (e.g. Breakfast overlapping Training → shrink or move breakfast).
3. **Honor meals; spaced short breaks.** Dinner / Breakfast / Lunch already on Google Calendar stay. Do **not** invent a second lunch or a noon meal. If a snack or meal label is ever needed, name it from the **resolved shift timezone** wall clock (breakfast / lunch / dinner / snacks) — not a hardcoded PT noon. **Short breaks are 10–15m**, parked in leftover schedule gaps after ~60m+ of focus. **At most 4 a day** (2 is enough on a normal shift; 3–4 only when the shift is long). At least **45 minutes of other work** between short breaks. A meal is already a break: never place a short break immediately before or after Dinner, Breakfast, or Lunch. Prefer a leftover 10–15m hole over carving extra pauses into a long Open.
4. **Follow-up slots — name cases with a one-liner** — every FOLLOW-UPS block lists `#CaseNumber — {short what-it-is}` in severity × temperature order (1…n), with work type. Include **Follow-up due** cases here. Same-wave DNS may say `batch-nudge the {N} DNS cases` in the remainder **and** still list Watch for the ones that did not trip Follow-up due. Never “do some follow-ups.” Never a bare number list. Named enablement dates (MFA / LAP / 9/18 testing) go in the remainder or Tomorrow first thing — not only in a LAP status line.
5. **Troubleshoot gets real time** — if any item is tagged **troubleshoot**, reserve a **≥40m** (often 45–90m) free block; never sandwich that into a 15m gap before a customer call. After a troubleshoot block, prefer a **short break** before the next deep slot.
6. **Sev-1 remainder is specific.** For every owned Level 1 still open at plan time, the remainder/EOD must name: (a) Trust incident if one exists, (b) ICC / `#sev1-…` channel **name**, (c) each unanswered customer question still on the thread (data-loss, Email-to-Case, “is the instance up”), (d) a **GEO handover** before Assembled logout so 24/7 coverage continues (this GEO → next GEO, with ICC / Trust / unanswered Qs), (e) a **second touch** later in the shift if the first update is before Break. Do not reduce a Sev-1 to `#Case — one-liner`. Do **not** name another skill (`/case-gho` or similar) — recipients of this folder may not have it.
7. **Slack tags early** — only **real unanswered asks** go in **FIRE** or the **first ≤15m gap**; don’t defer those to EOD. Acked / FYI Slack stays out of the plan.
8. **Customer-call buffer** — no new-case intake and no deep troubleshoot in the **15–30m before** a customer call; that window is **prep** (a 10–15m short break before prep only if that leftover hole already exists).
9. **Afternoon** — prefer follow-ups + troubleshoot; **new-case intake only if** morning new-case queue is clear / fires are done.
10. **Soft enablement** — mark optional; drop from the plan unless you have spare capacity.
11. **Conflicts** — print a one-line `⚠️ Conflict:` when two blocks overlap; propose the fix in the plan.
12. **Hard stop = Assembled logout** (5:00 PM in the resolved zone only if Assembled is missing and the zone is known).
13. **Don’t over-break** — **at most 4 short breaks** a day besides meals already on the calendar, with at least 45 minutes between them, never immediately before or after a meal; never a pile; never insert a short break that would delay a FIRE reply or cut a customer call.
14. **Assembled is the working-day source; chat still reshapes hours.** Overlay primary meetings on Assembled, not the other way around.
    - **Working day** — Assembled has Casework/Chat/Lunch/Break today, **or** the Slack WOC roster lists this engineer today (even if Assembled is empty that civil day). Plan **login → logout** from Assembled events when present; if WOC and Assembled is empty, use 8:00 AM–5:00 PM in the **resolved** zone for the plan clock and put those times **only** on the Shift line. True on weekdays, weekends, and holidays whenever either source says they work.
    - **Not a working day** — Assembled has no shift events **and** this engineer is not on the Slack WOC roster for that day. Do **not** build an 8:00 AM–5:00 PM plan. FIRE only if something is actually on fire; otherwise a short brief. Holiday calendars never create this state.
    - **Overnight adjustment** — rank FIRE/TODAY using activity **since previous weekday logout**. A customer reply at 6 PM Friday is overnight if Friday logout was 5 PM; it is **not** overnight if they were still on shift. A Monday SOD uses Friday logout (or Engineer_Shift 5:00 PM that Friday if Assembled was empty) — not a Saturday WOC scan.
    - **Chat coverage** — those windows are **hard**. No `📥` / `🔧` ≥40m inside them. FIRE / short Slack still happen. Punchline: `🎧 On chat {h:MM AM/PM–h:MM AM/PM {zone}}`.
    - **Casework** — 📥 / FOLLOW-UPS / 🔧 belong here (still yield to meetings and call buffers).
    - **Assembled Lunch / Break** — honor; merge with primary Dinner/Breakfast if they overlap.
    - **⚠️ Conflict:** required meeting overlapping Chat — take the meeting; note the coverage gap.
15. **Small roster notes — don’t rebuild the plan for other people.**
    - **You off** (Assembled PTO/Sick/Comp Off, or no shift events): `🏖️ You’re off today` (or the Assembled title). No case-work plan.
    - **Weekend scheduled — one 📅 line, never two.** Slack roster = **who/role**; Assembled = **hours**. Never print both a working-day weekend line and a WOC line. Never print `fallback`.
      - On the Slack WOC roster: `📅 WOC {Sat/Sun} — {role from post}` plus `· {Casework|Chat} {h:MM AM/PM–h:MM AM/PM {zone}}` **only if** Assembled has hours that day.
      - Not on the roster, Assembled has weekend events: `📅 Weekend shift — {Sat/Sun} {Casework|Chat} {h:MM AM/PM–h:MM AM/PM {zone}}`.
      - On the roster, Assembled empty: `📅 WOC {Sat/Sun} — {role}` only. Hours stay on `📅 Shift {login}–{logout} {zone}`.
    - **WOC remainder (this engineer only).** If the Slack roster (after thread swaps) lists this engineer on a weekend day, put that day's remainder on **EOD of the last Assembled working day before it**. Include Slack Task Remainders named to them. Do **not** dump the team roster. Do **not** wait for Saturday SOD. SOD/MID of that last working day: one line that WOC is coming; the remainder itself is EOD.
    - **Someone on PTO** (team PTO calendar, named person, today or next 2 days): one quiet line, e.g. `🏖️ Out: {Name} (PTO)`. Don’t @ them. Don’t drop your own 📥/🔧 because they are out. **Never** treat location / site / national holiday titles as PTO or as “you’re off.”
    - **Omni out of adherence during this Assembled block:** `⚠️ Omni: out of adherence — should be available for this Assembled block`. Point it out only. Do not insert a plan block to “go online,” do not FIRE it, do not change 📥/🔧. Omit when in adherence, off-shift, or PTO.
    - Omit any of these lines when there’s nothing to report.

### Work icons in the plan
`📥` new cases · `FOLLOW-UPS` named list · `🔧 TROUBLESHOOT` · `🗓️` meeting/call · `🎧` chat coverage · `📅` working day / weekend shift · `🏖️` PTO note · `⚠️ Omni` out of adherence · `💬` Slack · `🍽️`/`🍳` meals · `☕` coffee · `⏸️` short break (10–15m, at most 4/day, not next to a meal) · `⚠️` conflict

## Step 3 — Compose for the daypart

Compose internally from **the template for this daypart**, then map that content onto the **page JSON** (Step 4b). **Never** paste that markdown into the chat reply. **Never** mermaid, **never** a canvas or `.canvas.tsx`. Keep items one line each on the page — `**[icon] #Case / subject** — why → action _(effort · temp)_`. Use plain unicode emoji: 🗂️ case · ✉️ email · 💬 Slack · 🛠️ GUS · 🗓️ meeting. Lead with the punchline; cut filler. Drop empty Fire/Today with the one-line empty form (`🔥 No fires 🎉`). **Quick wins, Customer asked for a meeting, and Before you log off omit the heading when empty.** **Do not drop** New/escalated, Still open, At risk, **👀 Still watching**, **Follow-up due**, Day plan / Rest-of-day, Do first / Next 2h, Unblocked overnight, or Tomorrow first thing from the **page**. Rank Fire/Today; do **not** starve Watch, Sev-1 remainder, or named DNS batch counts.

Use **the template for this daypart**, in that order. Do **not** invent a single spine that deletes SOD/MID detail or turns EOD into a full Day plan. **Follow-up due** is inserted last among ranked case blocks, immediately before the plan. Name + manager + shift sit at the **start of the page** on SOD, Mid-Day, **and** End of Day.

**Section purpose line.** After every printed section header, one italic sentence that says what the section is for (same sense as the page blurb). Blank line before and after that line so it does not glue to the heading or the first bullet. Omit the italic when the whole heading is omitted.

Do **not** print `Identity`, Assembled UTC (`15:00Z`), or a second line that repeats the briefing title.

**Line breaks are mandatory (standing instruction, Kiran 2026-09-15; reinforced 2026-09-16 after a run-on SOD brief).** Chat markdown **collapses a single newline into one paragraph** — two lines separated by only a bare newline glue together (this is exactly how the two `📅` header lines ran into one another). The **only** reliable separators are: **(a)** make each line its own `-` bullet, or **(b)** put a **blank line** (two newlines) between the lines. Use one of these for **every** standalone line — a bare newline is never a line break. Kiran should never have to ask for line breaks after the fact.

**When to break — apply mechanically:**
- **Blank line before every section header** (`🚨 Needs us now`, `🔥 Fire`, `📋 Today`, `⚡ Quick wins`, `🔓 Unblocked overnight`, `👀 Still watching`, `🫱 Follow-up due`, `🗓️ Day plan`, `▶️ Do first`, `▶️ Next 2h`, `🔜 Tomorrow, first thing`, EOD blocks like Wins / Before you log off / Handoff / Loose threads / Walk away). **👀 Still watching** is a printed heading on SOD, MID, and EOD whenever Watch has wait/peek owned cases that did **not** trip Follow-up due. Omit only when the bucket is empty.
- **Header block:** Name, Manager, briefing title, and the case-count line are each separated by a **blank line** (they are not bullets). The **`📅` shift line, `📅` working-day line, and any `🎧` / `⚠️ Omni` / `🏖️` line are each their own `-` bullet** — never bare newlines (that is the exact glue Kiran flagged). Blank line after the header block before the first section.
- Each **ranked item** on its own `-` line; do not chain multiple items onto one line with `·`.
- In the **Day plan** (SOD and MID), **each time-block is its own `-` bullet**, formatted ``- `{h:MM–h:MM AM/PM}` — {block}`` (a contiguous bullet list renders each block on its own line). The `⚠️ Conflict:` line and `_Hard stop_` line break **out** of the list, each standing alone with a blank line above and below.
- The trailing status lines are **each their own `-` bullet** (never packed onto one): `🛠️ GUS …` / `📋 LAPs …` / `💬 Slack …` / `✉️ Gmail …`.
- **`▶️ Do first:`** / **`▶️ Next 2h:`** / **`🔜 Tomorrow, first thing`** — a **numbered list**, each step on its own line (blank line between the `▶️` label and the list).
- **Any other dense block** (EOD Wins, Handoff, Loose threads, Watch notes): a blank line between facts that would otherwise glue into one paragraph.

**Self-check before publishing the page:** the SOD/MID/EOD facts below are the content model for the page, not a chat paste. Confirm every required section is on the page. Then **do not** send that markdown in chat.

`{zone}` below is the short label for the resolved shift timezone (`PT`, `ET`, `IST`, …).

### SOD — Start of Day (full plan)

Blank lines and `-` bullets below are **part of the template** — copy them. Never emit the header/shift/status lines as bare consecutive newlines.

```
**{Name}** — {Title}{, Cloud}{, Skill Group — only if on the User record}

Manager: {Manager.Name}

☀️ **Briefing · Start of Day** — {Day, Mon D} · {shift-zone time}

_{N} open cases · {N} need you · ~{X}h case work_

- 📅 Shift {login}–{logout} {zone} · {GEO label} · overnight since {prev-day} {prev-logout} {zone}
- 📅 {Working day · Casework  |  one weekend/WOC stamp (see merge rule)  |  Not a working day (Assembled clear, not on WOC roster)}
- {🎧 On chat 9:00 AM–1:00 PM {zone} — omit if not}
- {⚠️ Omni: out of adherence — should be available for this Assembled block — omit if in adherence / off-shift}
- {🏖️ Out: {Name} (PTO) — omit if none}

{🚨 **Needs us now ({N})** — omit the whole block if zero hits; owned cases that trip the escalate-if-unmoved-today signal only; never Slack/GUS/LAP/queue dump}

_Move these today or they escalate / stall. Not the rest of the queue._

- {🗂️ #Case / subject — Level · why it will escalate → what we owe _(effort · temp)_}

🔥 **Fire ({N})**

_Do now: customer waiting, SLA, unanswered Slack ask, or a blocker that just cleared._

- {item — why → action _(effort · temp)_}

📋 **Today ({N})**

_During hours — follow-ups and first responses that are not Fire._

- {item → action _(effort · temp · work-type)_}

⚡ **Quick wins ({N}, ~{X}m)**

_Under 5 minutes each. Batch these first._

- {item}

🔓 **Unblocked overnight**

_Blocker cleared since last logout. Close the loop with the customer._

- {#case — what cleared → tell customer}

👀 **Still watching**

_Peek only. These did not trip Follow-up due. Do not re-nudge._

- {#Case — {one-liner} · {Level} · ball with customer / nudged {day} / Solution Provided awaiting confirm · peek for a reply}

{🫱 **Follow-up due ({N})** — keep the heading at 0; last ranked case block; Level 1 / Sev-1 first (1+ BD); others (2+ BD); quiet past hold, pending us or pending them; cases that do not trip this test go in Still watching}

_Quiet past hold. You have not touched these. A public update is owed._

- {🗂️ #Case — {one-liner: domain / customer / what it is} — last customer-facing {day} · {pending us | pending customer | follow-up due} → touch _(effort)_}

🗓️ **Day plan**

_Meetings and timed blocks for this shift._

- `{8:00–9:00 AM}` — {block — 📥 new cases / 💬 Slack / 🗓️ meeting / FOLLOW-UPS → #Case — {one-liner} including ICC `#icc-…` / Trust {id} / unanswered Qs on Sev-1 / meal / coffee / ⏸️ short break}
- `{9:00–10:00 AM}` — {block}
- {…one bullet per time-block…}

⚠️ **Conflict:** {if any — and the reshape}

_Hard stop {logout} {zone}_

- 🛠️ **GUS** — {clear | top item} · **SLA** — {none approaching | W-… due {date} · overdue: …}
- 📋 **LAPs** — {none linked | #OrgCS → LAP {CaseNumber} {Status} ({age/last chatter})}
- 💬 {N} Slack tag(s) owed · ✉️ {gap or clear}

▶️ **Do first:**

_The first three moves after you finish this read._

1. {…}
2. {…}
3. {…}
```

### MID — Mid-Day (rebuild the remainder)

Blank lines and `-` bullets below are **part of the template** — copy them. Never emit the header/shift/status lines as bare consecutive newlines.

```
**{Name}** — {Title}{, Cloud}{, Skill Group — only if on the User record}

Manager: {Manager.Name}

🌤️ **Briefing · Mid-Day** — {Day, Mon D} · {shift-zone time}

_Since this morning: {N} new · {N} still open from this morning's plan_

- 📅 Shift {login}–{logout} {zone} · {GEO label}
- 📅 {Working day · Casework  |  one weekend/WOC stamp  |  Not a working day}
- {🎧 On chat {h:MM AM/PM–h:MM AM/PM {zone}} — omit if not}
- {⚠️ Omni: out of adherence — should be available for this Assembled block — omit if in adherence}
- {🏖️ Out: {Name} (PTO) — omit if none}

{🚨 **Needs us now ({N})** — omit if zero; same signal as SOD; cases only}

_Move these today or they escalate / stall. Not the rest of the queue._

- {🗂️ #Case / subject — Level · Trust/ICC `#channel-name` · unanswered customer question → action _(effort · temp)_}

🔥 **New / escalated ({N})**

_Do now: new or escalated since last logout._

- {item → action _(effort · temp)_}

⏳ **Still open from this morning**

_From this morning's plan and still unpaid._

- {#… — what’s unpaid}

⚠️ **At risk before logout ({h:MM AM/PM {zone}})**

_Will sit until next login if you skip it before logout._

- {#case / meeting / GUS SLA due / LAP pending / Sev-1 GEO handoff / promised close at logout}

🛠️ **GUS SLA** — {none approaching | …} · **LAP updates** — {none | #… → Approved/Rejected/Pending + note}

👀 **Still watching**

_Peek only. These did not trip Follow-up due. Do not re-nudge._

- {#Case — {one-liner} · {Level} · ball with customer / nudged {day} · peek for a reply}

{🫱 **Follow-up due ({N})** — keep the heading at 0; last ranked case block; Level 1 / Sev-1 first (1+ BD); others (2+ BD); cases that do not trip this test go in Still watching}

_Quiet past hold. You have not touched these. A public update is owed._

- {🗂️ #Case — {one-liner: domain / customer / what it is} — last customer-facing {day} · {pending us | pending customer | follow-up due} → touch _(effort)_}

🗓️ **Rest-of-day plan** (from now → logout {h:MM AM/PM {zone}})

_Meetings and timed blocks from now to logout._

- `{h:MM–h:MM AM/PM}` — {named action: e.g. Linde recheck ICC `#icc-…` / Trust {id}; update Bettina; settle data-loss; Team Connect; batch-nudge {N} DNS; second Sev-1 touch; GEO handoff}
- {…one bullet per time-block…}

_No new-case intake unless fires clear_  {or: 📥 if capacity}

▶️ **Next 2h:**

_What to do in the next two hours._

1. {…}
2. {…}
3. {…}
```

### EOD — End of Day (close out + tee up tomorrow)

Blank lines and `-` bullets below are **part of the template** — copy them. Never emit the header/shift/status lines as bare consecutive newlines.

```
**{Name}** — {Title}{, Cloud}{, Skill Group — only if on the User record}

Manager: {Manager.Name}

🌙 **Briefing · End of Day** — {Day, Mon D} · {shift-zone time}

_Today: {N} closed/replied · {N} still open_

- 📅 Shift {login}–{logout} {zone} · {GEO label}
- 📅 {Working day · Casework  |  one weekend/WOC stamp  |  Not a working day}
- {🎧 On chat {h:MM AM/PM–h:MM AM/PM {zone}} — omit if not}
- {⚠️ Omni: out of adherence — should be available for this Assembled block — omit if in adherence or already past logout}
- {🏖️ Out: {Name} (PTO) — omit if none}

🏆 **Wins today**

_What actually moved today._

- {what actually moved — pickup, status to the customer, case you unstuck, meeting attended. Not a one-word shrug.}

{🚨 **Needs us now ({N})** — omit if zero; only still-unpaid owned cases that still trip the signal; not a queue dump}

_Move these today or they escalate / stall. Not the rest of the queue._

- {🗂️ #Case — still unpaid → finish before logout _(effort · temp)_}

👀 **Still watching**

_Peek only. These did not trip Follow-up due. Do not re-nudge._

- {#Case — {one-liner} · {Level} · ball with customer / nudged {day} / awaiting confirm · peek for a reply}

{🫱 **Follow-up due ({N})** — keep the heading at 0; last ranked case block; Level 1 / Sev-1 first (1+ BD); others (2+ BD); still quiet past hold; cases that do not trip this test go in Still watching}

_Quiet past hold. You have not touched these. A public update is owed._

- {🗂️ #Case — {one-liner: domain / customer / what it is} — last customer-facing {day} → touch next working day _(effort)_}

🚪 **Before you log off ({N})**

_Finish before logout — including closes you already promised the customer._

- {🗂️ #Case — promised close (EOD / {named day that is today}) → close if still quiet _(2–3m)_}
- {Sev-1: fresh status to {contact}, confirm instance state from ICC `#icc-…` / Trust {id}, answer each unanswered customer question (data-loss, Email-to-Case, “is it up”)}
- {GEO handover (this GEO → next GEO) so 24/7 coverage continues past logout}
- {batch-nudge {N} DNS / other owed follow-ups — or mark carryover}
- {any GUS SLA due tomorrow / LAP Pending — leave a clear next step}

🤝 **Handoff**

_Sev-1 / hot for the next GEO so coverage continues past logout._

- {#Case {account} (Sev-1) → next GEO — complete a GEO handover; link ICC `#icc-…` and Trust {id}; note instance recovery state (confirmed recovered / not yet confirmed)}
- {skip this heading only when there is no Sev-1/hot to hand}

🔜 **Tomorrow, first thing** ({next working day, e.g. Thu Sep 17})

_Next working day's first moves, including promised closes on that day._

1. {Sev-1 status if still open}
2. {named enablement: e.g. MFA/permission date is the very next day — verify it is in place}
3. {#case — promised close on that next working day}
4. {any replies on the DNS nudges / CTI / Message Center / other Watch}

🧵 **Loose threads**

_Named leftovers. Not a forced action this minute._

- {{N} {wave} DNS cases (awaiting customer DNS updates)}
- {#Case — Solution Provided, awaiting confirm}
- {#Case — Need More Information, {N} business days, nudge due}

🛠️ **GUS SLA overnight** — {none | W-… due …} · **LAPs** — {#… Status}

🗓️ **Next working-day skeleton** (from Assembled, not calendar-tomorrow if Assembled is empty)

- 📅 {8:00 AM–5:00 PM {zone}} ({Lunch … · Casework … · Break … · Casework …})
- 🗓️ Daily POD {h:MM AM/PM–h:MM AM/PM} {if on calendar}
- First move: {Sev-1 + named enablement}

_(re-pull calendar at SOD)_

**If this engineer is on the Slack WOC roster for the next weekend day and today is the last working day before it, replace the skeleton above with:**

🗓️ **WOC remainder** ({Sat/Sun} — {role from post})

- {Task Remainders from Slack that name this engineer — or: remainders not posted yet}
- {hot / Sev1 owned cases to carry · GEO handover if needed}
- {Assembled hours for that WOC day if known}

_(do not tee up Monday; this is the weekend remainder)_

👋 **Walk away**

Once {Sev-1} has a fresh status and a clean GEO handoff, log off — overnight coverage is the next GEO. Nothing else is on fire unless listed above.

_Hard stop logout ({h:MM AM/PM} {zone}). Oldest open: {🗂️ #case — {N}d}._
```

## Step 4 — Do not display in chat

**The webpage is the briefing.** Do **not** show the composed brief in the chat reply. Do not post to Slack, do not send email, do not build a canvas, do not emit mermaid, do not print the copyright. Run **Step 4b**. If the publish script opened the page, send **no** briefing text in chat. If it did not open, the chat reply may be **only** the page URL (one line). No preamble. No recap. No closer that restates Fire / Today / the day plan.

## Step 4b — Companion page (required)

**HARD RULE — the model does the work.** Fetching cases, Slack search hits, and unread mail is plumbing. Python does **not** write Peek summaries or chronology, keep/drop Slack or Mail, or invent Today's plan when you wrote `todayPlan` (it only fits those blocks into calendar gaps). Python fetches one new quote every publish; the model must not curl. **Python already clipped CaseComment and EmailMessage (and feed) for this engineer's open owned cases only into `/tmp/planner-digest.txt`, and leftover Slack into gather `openedClip` plus `- reactions:`, before this CLI starts.** Never Peek a closed, transferred, or reassigned case. Gather JSON is thin (no activity, no digest blob). The model Reads gather **once** and the digest **once**, never Slack MCP, never Gmail batch, never `soqlQuery` CaseComment / EmailMessage / CaseFeed, never SELECT `CommentBody` / `TextBody` / `HtmlBody`. **You still decide** Peek, keep / drop / `Not opened` / `Needs a reply`, ranks, and `todayPlan` from those clips. **Classify Slack and Mail every run from `/tmp/planner-inbox.txt`.** Keep or drop from `clip:` and `- reactions:` — closing ack/✅/👀 can drop even when `lastHumanIsMe` is false. Python does not keep/drop. Empty inbox.txt is **not** Slack/Mail — clear unless gather `slackFetchOk` / `mailFetchOk` is true — then omit `inboxReviewed`. If leftover fetch failed, Python restores last classified Slack/Mail; do not copy last page when this run fetched leftovers. Public help-channel shouts (“escalate to the concerned team”) are **drop** unless that thread @-mentioned you and you still owe a reply. Drop gather Slack/Mail with `done: true`. Cases this engineer already marked Done stay Done by case number. Write **`/tmp/plan-ai.json` ONCE** (Peek + ranks + rebuilt `todayPlan` + slack + mail, `aiAnalyzed` true; `inboxReviewed` true only after classifying clips). Never `/tmp/plan.json`, never `out/current.html`, never self-check, never publish-page. **Needs us now is burning only** (high-urgency `Severity_Level__c`, business-stopping/impacting, urgent, time-sensitive) — empty is correct; do not pad it. DNS/cert with a burning inbound ask belongs here; quiet close-outs do not. Empty `quickWins` / `customerAskedMeeting` / `beforeYouLogOff` omit those headings. Copying search hits onto the page, or shipping `X last wrote:` stubs, is a miss.

After ranking and composing internally, write one JSON file matching **Companion page → JSON** and run `scripts/publish-page.sh` **from this skill folder** (the directory that contains this `SKILL.md`):

```bash
"<skill-dir>/scripts/publish-page.sh" /path/to/data.json
```

Do not hardcode a machine path. Resolve `<skill-dir>` as the folder this file lives in. The script writes **`out/current.html` and `out/briefing.json` inside that same folder**, starts `scripts/calendar-bridge.py` on `127.0.0.1` (prefers **8765**; if that port is taken by something else, the next free port through **8799**), prints the URL it actually bound, and opens the page (`DAY_PLANNER_NO_OPEN=1` to skip). Never kill a foreign process on 8765. `DAY_PLANNER_PORT=NNNN` starts the search at a different port. Host-agnostic: Cursor, Claude Code, OpenCode, or any other AI CLI. No Cursor canvas. No browser MCP. Never publish the page outside this skill folder.

**Stamp `runner` every publish** — the **app** running this session (`claude`, `cursor`, `opencode`, or any other). Not a model name. `export DAY_PLANNER_RUNNER=<name>` before `publish-page.sh` if you need to set it by hand. Never paste the Express LLM Gateway token into chat or this skill (DevBar already holds it for the app). Never hardcode a model. When publishing with `DAY_PLANNER_NO_BRIDGE=1`, overwrite `out/current.html` and `out/briefing.json` only — do not start a second server and do not open a browser.

**Event reminders.** Ten minutes before any timed Today's plan row (meetings, named work, and breaks on the clock — not Assembled), the page plays an **alert sound** and opens a **popup**. **Snooze 5 Min** or **Turn Off** for that event. If this engineer allows notifications, also fire a desktop notification. Page-local — do **not** write Google Calendar reminders. Audio and notification permission unlock on first click.

**Omni out of adherence.** During an **Assembled** shift only, Chrome rechecks Omni every **15 minutes** against the **block covering now**. **In adherence** = **Screen Sharing** (any block) **or** the status for that block (Available that contains that channel; Lunch/Break on those blocks). **Busy** never on a work block. **Offline during Assembled Lunch, Break, or Dinner is not out of adherence.** Query failure / unparsed presence is **skip** — never a false popup. Out of adherence → reminder-style popup showing **current Omni status** and the **Assembled block covering now**, with a **different sound** once every **15 seconds** until **Got It**. **Got It suppresses the alert until the next 15-minute poll.** Still out on that poll → the loop starts again. Skip when there is no Assembled calendar, nothing is scheduled on Assembled, or Assembled is PTO. Never use primary Google Calendar or the User shift picklist. Never stamp Omni out-of-adherence onto the published page — live `/omni/check` is the only source.

**Today's plan window.** Calendar rows on the clock are **shift login − 2 hours through logout + 2 hours**. Timed meetings inside that pad stay (including un-RSVP’d invites). A meeting **outside** that pad is on the clock only when gather judged it an **important meeting** — stamp `important: true`. That is a judgment call (customer/case, required team meeting, this engineer’s Important hold, Sev-1). It is **not** a keyword regex. Optional Dreamforce / Salesforce+ / office hours / blood drive / webinars outside the pad stay off the clock.

**Every timed event in that plan window** — POD, Team Connect, Breakfast, Important, Log In, customer calls, and in-window Dreamforce / Salesforce+ / office hours whether RSVP’d or not — gets `eventId` + `htmlLink` + duration stepper + **Update calendar**. **Update calendar stays disabled until this engineer changes the duration** (−15/−5/+5/+15). After a successful save it disables again. Not Breakfast-only. **Do not put Assembled events on the page** (Casework, Chat, Lunch, Break, or `assembled: true`). **Shift** is Assembled login–logout. **Assembled schedule** is today’s Assembled calendar blocks (`Lunch 8:00–9:00 AM · Casework 9:00 AM–1:00 PM · Break 1:00–2:00 PM · Casework 2:00–5:00 PM PT`) — not a collapsed 8–5. Fold PDT/PST → PT on the page (US DST fold, not a city pair). Assembled is not a page row.

**Page buttons are Title Case.** Visible controls use `Join Meeting`, `Open Event`, `Update Calendar`, `Schedule Meeting`, `Add Task`, `Refresh Calendar`, `Open Email`, `Open Channel`, `Open DM`, `Open Thread`, `Open Message`, `Snooze 5 Min`, `Turn Off`, `Got It`. Do not sentence-case them.

**Join Meeting vs Open Event.** If the event has a conference URL, the page shows **Join Meeting** (that URL). If it does not, **Open Event** (`htmlLink`) is fine. Pull `joinUrl` from `get_events` `detailed: true` **Meeting Link** (Google Meet hangout / conference entry). If that field is empty, use `location` when it is an `https://` Meet / Zoom / Teams URL, else the first such URL in the description. Never invent a join URL. Meals, Important, Log In, and other blocks with no conference stay on Open Event.

**Meeting invites** (`invite: true`, this engineer is an attendee on someone else’s event) also get **Yes / No / Maybe** on their **own row** under Join meeting / Update calendar. Highlight the current RSVP (`accepted` / `declined` / `tentative`; `needsAction` = none selected). Yes=`accepted`, No=`declined`, Maybe=`tentative`. RSVP uses `manage_event` `rsvp` with `send_updates: all`. Self-created blocks with no attendees (Breakfast, Important, Log In) get Update calendar only — no RSVP.

**Add task** opens the Task composer on Calendar week view (`/calendar/u/0/r/week/{y}/{m}/{d}?taskId=new`). Stamp the day-plan **start and end** the same way meetings do: `dates={startStamp}/{endStamp}` plus `ctz` (resolved shift zone). Also set `ddl=` to the full start stamp (`YYYYMMDDTHHMMSS`), not the date-only slice — otherwise Google opens an all-day task. The task **name** is an ASCII subject from the row label: drop `#`, turn `/` and `·` into `-`. Google’s task composer does **not** decode `%23` / `%2F` / `%C2%B7`. Example: `000000001 - Example Corp - sample instance down`. Never `/r/eventedit` (event editor + Task overlay). Never `taskBundleId=@default` (Calendar says task list not found). Untimed rows may omit `dates` and keep `ddl=` as the date only.

**Schedule Meeting** is only for a slot they named. **Ask for a time** is the other row: they asked to meet and named no day and no time. It opens the case. It does not open calendar compose. Both sit in **Customer asked for a meeting**, under **No time yet** or **Named a time**. After a named slot is saved, **Refresh calendar** matches the event, that block turns **green** (`Action taken — scheduled.`), and the meeting shows on **Today's plan**.

**Slack on the page** is its own section with two buckets: **Not opened** and **Needs a reply**. Never put DMs in New / escalated. DM labels are the **other person** (`{Name} (DM)`), never this engineer’s name — even when they sent the last search hit. Every Slack row’s control is the Slack permalink: **Open Channel** for channels, **Open DM** for DMs, **Open Thread** for channel threads, or **Open Message** for a channel message. Prompt / huddle / join-now / asap rows use the **same red row treatment as Needs us now**. Acked / no-ask stay off the list. Empty bucket omitted. **Never omit the Slack heading** — if both buckets are empty, keep the section with `Slack — clear`.

**Mail on the page** is its own section, same two buckets, immediately **under Slack**. Stamp human mail after opening the thread. Drop bulk/newsletter/noreply automation, calendar-invite ICS, and OrgCS case-comment / case-thread / case-assignment mail that is already on a case. Keep Chatter, GUS, and Black Tab. Do **not** require customer/manager/escalation/deadline to keep a message. Every Mail row’s control is the Gmail web link: **Open email**. Empty bucket omitted. **Never omit the Mail heading** — if both buckets are empty, keep `Mail — clear`.

**Page is the briefing, not a dump.** Rank and sort. One- or two-line labels; put Trust / ICC **channel names** / unanswered customer questions / GEO handover / named enablement / DNS batch counts in `detail` and Peek `summary` so they are not lost (there is no chat brief). **Follow-up due** sits **above** **No action needed** (Still watching). Status GUS/LAP lines that are overdue, rejected, or ⚠️ bubble into a red **Needs a look** card; quiet status stays off the page. **Today's plan** is the clock: **the same named timed blocks as the composed day plan** (meetings **and** case/work slots) plus **short breaks** (10–15m, at most 4 a day, at least 45m apart, never immediately before or after a meal) in leftover gaps after ~60m+ focus — keep calendar Dinner/Breakfast/Lunch; do not invent meals; do not emit Assembled Casework/Chat/Lunch/Break as rows. **Close-out on the page is two cards only:** **Before you log off** (GEO handover lives here — do not also paint a Handoff column) and **Tomorrow, first thing**. Fold EOD Wins / Loose threads / Walk away into Before you log off, No action needed, and Tomorrow — they have no chat home. **Refresh calendar** stays. **Collapse all** closes every card, including Today's plan. **Expand all** opens every card.

**Responsive.** From two columns up, **Today's plan stays on the right** (sticky clock, full meeting detail). Everything else stacks on the left. Wide screens: act | follow-up due | plan, with close-out under the left pair. **Very wide screens (1600px+):** the clock **grows** (about 520–720px) so Open event / Join / Update / RSVP / duration sit on **one line** instead of stacking in a 440px rail. Narrow screens stack: Act → Plan → Follow-up due → No action needed → close-out. Do not pin the page at 880px. Do not put the clock in the middle of a three-column hole.

Page look: Salesforce Lightning / Primer — **neutral surfaces**, navy/off-white text, **cloud blue on controls**. Light: `#eef3f8` canvas, white cards, `#032d60` text, `#0b5cab` buttons with white labels. Dark: `#070b12` canvas, lifted slate cards (`#1e2a3d`), `#f4f7fb` text, `#c5d0e0` secondary, visible `#6b7d96` borders, `#3b82f6` buttons with **white** labels and `#93c5fd` accent text (not navy-on-navy). **Identity header** is one band: name / role / manager, then **Shift** · **Assembled schedule** in the middle. **Header is a fixed set:** name / role / manager, Shift, Assembled schedule, run label, Refreshed, Now, Open, Need you. Never Omni, Out, WOC, Note, OrgCS, or other extras — those are the popup or they stay off the header. No Today punchline, no WOC tile, no **Note** tiles, no case-count line in facts (Open / Need you live on the right), then **Refreshed** / live **Now** / open / need-you on the right. Named grid areas restack the band: wide = name | facts | clocks; mid = name+clocks then facts full width; narrow = stacked. Clock **time** and **date** sit on two lines with a zone abbreviation (`IST`, `PT`) — never `GMT+5:30`. **Header color follows daypart** (`daypart`: `sod` / `mid` / `eod`) — a **solid** fill on the header (warm gold start of day, sage mid-day, **slate dusk** end of day — never the red family; Need you / Needs us now own red), plus a matching left stripe. No gradient, no sun, no disc, no extra icon. Header text stays on the normal colors. Body, cards, and the quote bar stay on the normal canvas. **Quote bar** sits immediately under the header, **same wrap width as the header** — a slim italic line, **centered**. Quote and author stay **one line when the bar is wide enough**; they wrap only when the width requires it. Never a forced second line for the author. Stamp a **new** quote every publish (see JSON). **📅 WOC** belongs on **Tomorrow, first thing**: a **WOC Sat/Sun** chip in that card’s heading, plus a stamp line `WOC {Saturday, Sep 19} — {role}` above the same compact later rows. It is **not** a header fact tile and it is **not** printed in chat. Do not rename the card **WOC remainder**. JSON: `woc: { day, role, hours? }` on that section. **Today's plan** is a calendar: a red **Now** hairline moves through timed rows. When the browser’s local zone differs from the resolved shift zone, a control on that card switches the displayed times; **its labels are those two zones as `Intl` reports them** (never hardcoded `PT` / `IST`, never the words “Shift time” / “Local time”). JSON stamps stay shift-zone wall clock. **Needs us now** uses the **red family** on those neutrals (not cyan-on-navy): SLDS `#ba0517` / rose `#e11d48` header bar, white title + count, rose `--now-bg` body, red border. Do not fill the whole navy canvas in magenta. Error red is `--err`. No beige/zinc on the body. **Light / Dark** is one round icon: sun while light is on, moon while dark is on. Click to switch. Footer **must** show `© 2026 Kiran Kumar Garai <kgarai@salesforce.com>. Skill authored by Kiran Kumar Garai.`

**Quote — fetch every run, never a list.** The Python publisher fetches one new quote every publish (`fetch_fresh_quote`: prefer `https://zenquotes.io/api/random`; if that fails, `https://dummyjson.com/quotes/random`). Stamp top-level `quote: { "text": "…", "author": "…" }`. Never bake quotes into this skill or `template.html`. Never reuse the previous run’s quote. Fetch fails → omit `quote` (the bar hides). The model must not curl. **Never** print the quote in chat.

**Peek is OrgCS cases and GUS only.** A row gets Peek only when it is a case (`kind: "case"`, `caseUrl`, `caseNumber`) or GUS (`kind: "gus"`, `gusUrl`, `workId`). Calendar invites, meetings, mail, Slack, FYI, and any row with `eventId` / `htmlLink` / `invite: true` **never** get Peek — even if you attached `summary` / `chronology`. Invites that fall **inside this shift’s plan window** are meeting rows on **Today's plan** (Open event / Join / RSVP). An FYI invite **outside** that window (next week, awareness only) is a quiet **No action needed** row **without Peek** — never a chat Watch line.

**Today's plan JSON is the model's `todayPlan`, rebuilt every run.** Title `Day plan` or `Today's plan` (`open: true`). Do not copy the previous page's plan. Python keeps Google meetings (including Dinner / Breakfast / Lunch) and fits those windows into free gaps from **now through logout**. The page draws **Login** and **Logout** lines on that clock from `shiftStart` / `shiftEnd` (Assembled hours when they exist). Those are markers, not Assembled rows. **Every leftover hole counts** (not only 40+ minutes). **When Assembled hours exist**, a hole may be Take new cases, Work on cases, a short break, or left free — not a dump of every watch row. Empty leftover paints as compact **Free · duration**, not a bare Open slab. Nothing Scheduled → do not invent a casework day. Do not invent a second meal. Work rows are `kind: "plan"`, `"task"`, or `"case"` — never `slack`, `assembled`, or `mail` on the clock. Each Needs us now window (`plan-case-<number>`) is 20–30 minutes. Each follow-up window (`plan-follow-<number>`) is 5–10 minutes, one case per block. Short pauses are `kind: "break"` (10–15m, at most 4 a day, at least 45m apart, never immediately before or after Dinner, Breakfast, or Lunch). Do not rank the clock by severity (time order only). Empty `todayPlan` is allowed when leftover time is free.

**Header Open / Need you are live.** Stamp `openCases` / `needYou` from gather, but the page **recomputes** them on every render. **Open** = remaining **action items** still owed a click or reply: Needs us now, Customer asked for a meeting (unscheduled), Slack leftovers, Mail leftovers, Follow-up due, Quick wins, Before you log off. Deduped by case number / Slack / Mail id. Not Done. **Not** Still watching, Tomorrow, Today's plan clock rows, calendar meetings, or leftover aggregators (`plan-slack` / `plan-follow`). **Need you** is the urgent subset of Open: remaining Needs us now, unscheduled customer-named meetings, and Slack/Mail marked urgent (`urgent: true` or huddle / asap / join now / outage). **0** = filled `--ok` green; **any non-zero** = light-mode red `#ba0517` in both themes. Both update when this engineer marks Done / undo (and when a customer meeting is scheduled). They are not a frozen snapshot next to the live Now clock.


## Hard rules
- **Author copyright is immutable.** The HTML comment after the YAML frontmatter (`© 2026 Kiran Kumar Garai <kgarai@salesforce.com>…`) is **mandatory**. Keep it byte-exact. Never delete, rewrite, or omit it when editing or sharing this skill. Never print it in the briefing. If it is missing, restore it before any other change.
- **Gather is read-only.** Never reply to a customer / case / email / Slack on your behalf, never post to any channel or DM, never send mail, never edit a case / GUS item / doc. This skill plans the work; it does not draft the customer comment or post the GEO handover. **Calendar writes only on explicit page clicks** (Update calendar, Yes/No/Maybe) via the local bridge — never silently, never during gather. **Add task** and **Schedule meeting** open Google compose; this engineer saves there.
- **Portable — no other skills.** This folder is enough. Never print `/case-gho`, `/case-comment`, `/briefing`, or any other skill slash-name in the chat brief or on the page. Say **GEO handover** (this GEO → next GEO) and **draft a customer comment** in plain English. Another engineer who receives this folder will not have those skills.
- **Page only — never chat.** Publish **`out/current.html` and `out/briefing.json` in this skill folder**. Do not paste the briefing into the conversation. No Slack post, no email send.
- **Text only — never a canvas, never mermaid.** Do not create, edit, or link `.canvas.tsx`. Do not emit a mermaid diagram (gantt or otherwise). Day plan is 12-hour timed rows on the page.
- **No lab notes in the brief.** Never print `Identity`, never print Assembled UTC conversion (`15:00Z = 8:00 AM PT`). Never print that WOC was omitted (`No WOC line — no roster`, `WOC not found`, `no roster`). Name and manager are facts at the top. Shift is one 12-hour line at the top.
- **No duplicate title.** Do not print “This run is SOD/MID/EOD …” in addition to the briefing header.
- **Keep previous sections on the page.** Add Follow-up due; do not delete Fire / New/escalated, Today, **👀 Still watching**, Unblocked overnight, Day plan, Do first, Rest-of-day, Next 2h, Tomorrow first thing. Empty Fire/Today get the one-line empty form (`🔥 No fires 🎉`). **Quick wins, Customer asked for a meeting, and Before you log off paint only when the list has rows** — omit the heading when empty (no “none” placeholder). **Follow-up due keeps the heading when empty** (count 0, no “none” placeholder), same as Needs us now. Slack / Mail / Still watching keep their empty forms (`Slack — clear`). Empty Watch is omitted; a non-empty Watch that you skip so the remainder looks short is a miss.
- **Page density = briefing.** Remainder / Watch / EOD name Trust ids, ICC / `#sev1-…` **channel names**, unanswered customer questions, GEO handover, named enablement dates, batch counts, and a second Sev-1 touch when the first update is before Break. Do not skinny the page because chat is gone.
- **EOD is not a full Day plan.** Next working-day skeleton (or WOC remainder) stays a skeleton. SOD and MID keep full named follow-ups, meals, and meetings.
- **Line breaks whenever required.** Blank line before every section header; blank line between day-plan blocks; each bullet, numbered step, status line, and header fact on its own line. Prefer blank lines wherever chat markdown would otherwise glue content into a run-on paragraph. Self-check the composed brief before sending. Kiran should never have to ask for line breaks after the fact (see Step 3).
- **12-hour clock in the brief.** Every printed time is 12-hour **in the resolved shift timezone** with AM/PM (`8:00–9:00 AM`, `1:00–2:00 PM`). Same period once at the end of the range; if the range crosses noon or midnight, put AM/PM on both ends (`11:00 AM–12:00 PM`). Header clock too (`8:51 AM PDT`). Never print 24-hour (`13:00`, `17:00`, `08:00`). Internal math may use 24-hour; the reply does not.
- **Shift timezone from OrgCS `Engineer_Shift__c`, then Assembled, then 8:00 AM–5:00 PM in that zone.** Never default all math to `America/Los_Angeles`. Never invent PT or ET when the zone is missing. Never use the laptop zone as the shift zone. Full rule at the end of this skill.
- **Identity from the OrgCS User.** Never hardcode this engineer’s name, title, manager, cloud, or skill group. Print Name and Manager at the top of SOD, Mid-Day, and End of Day. Full rule at the end of this skill.
- **Case.Description always.** Every open case in the gather wave includes `Description` — never Subject-only.
- **Case activity = Comment + Email + Feed/chatter.** Never rank a case from CaseComment alone; EmailMessage and CaseFeed (with nested FeedComment) are mandatory peers. Internal comments count for holds and scheduled touches.
- **Day plan every run.** SOD = full Assembled shift; MID = remainder to logout; EOD = next working-day skeleton **or WOC remainder** when this engineer is on the Slack WOC roster and today is the last working day before that WOC. Overnight / Slack / Gmail / “customer replied since…” = **since previous working-day Assembled logout** (WOC roster search is **not** limited to that window). Hard stop = today's Assembled logout. Use 8:00 AM–5:00 PM in the **resolved** zone on the Shift line when Assembled is unread and the zone is known, or when this engineer is on Slack WOC and Assembled has no hours that day — never print `fallback`.
- **New cases at start of shift.** Protect an early 📥 block after login **unless Assembled has you on chat then**.
- **Named follow-ups + troubleshoot time.** Follow-up slots list `#CaseNumber — {short what-it-is}` by severity × temperature; troubleshoot items get ≥40m reserved. Never a bare number dump.
- **Slack messages are mandatory every run.** Paginate DMs, @-mentions, **and threads** since last logout (`is:dm` / `<@USER>` / `is:thread <@USER>` — `is:unread` is not enough), then **open** leftovers: `slack_read_channel` / `slack_read_thread` **detailed** on each leftover, plus **`slack_get_reactions`**. Search snippets (especially empty bot `Text`) are not a read. First page of 20 is not the full inbox. Do not stamp `💬 Slack — clear` without that open. Put on FIRE/plan only when a real ask is still open. Ack/reactions can close a thread; FYI → “X sent you …” under WATCH, never a forced reply prompt. Python fetches; you classify.
- **Gmail is search-then-open, every leftover human.** Paginate **three** searches since last logout (`is:unread in:inbox` / `to:me in:inbox newer_than` / `in:inbox newer_than` — `is:unread` and `is:important` are not enough; default page size is 10 so set `page_size` **50** and follow `page_token` to 5 pages). Dedupe one row per thread. Then **open** leftover humans (`get_gmail_messages_content_batch`, ≤25, cap 40) except OrgCS case-comment / case-thread copies (`ref:!`, Outbound Contact, `customersupport@salesforce.com`) — drop those from snippets; they are already on the case. Keep Chatter, GUS, Black Tab. Snippets are not a read. First page is not the full inbox. Drop bulk/newsletter/noreply automation and calendar invites. Do not stamp `✉️ Gmail — clear` / `Mail — clear` while a `page_token` remained or leftover-human hits were skipped. Case-thread-only Gmail may stamp Mail — clear.
- **GUS SLA every run.** Copy `Out_of_SLA__c`, `SLA__c`, `SLA_Warning_Notification_Sent__c`, `Number_of_SLA_Violations__c`, and `Due_Date__c` as stored. GUS Bot Work Notifier and slamonitor mail are the alerts. The model judges. Python does not mark overdue or approaching. LAP uses requested start and end, not an SLA. Open work = Support Contact (`CS_Contact__c`) and Follow (`EntitySubscription`) for this User’s GUS Id (from Email). Never Assignee.
- **OrgCS Initial Response + GUS related list every run.** `SE_Initial_Response_Status__c` / `SE_Target_Response__c` on every open owned case. Query `Case_Relationship__c` for those Case Ids: GUS Work lookup (`GUS_Work__r.Name`, `Work_Record_Type__c`) or typed LAP/PRB (`Type__c`, `GUSText__c`). Also query `GUSRecord__c`. Empty is a result, not a skip.
- **LAP updates every run.** For every LAP linked to an open owned case, read status + recent chatter; print a **📋 LAPs** line (Approved / Rejected / Pending + last update). Approved/Rejected/reviewer-ask → FIRE/TODAY; silent Pending → WATCH but still listed.
- **Rank Fire/Today; do not starve Watch or remainder.** Fewer, sharper Fire/Today items beat a queue dump. Watch cases, Sev-1 remainder facts, named DNS batches, and EOD close-out are completeness, not dump. If truly nothing needs you: show one line — "✅ Nothing needs you right now — oldest open #X ({N}d)." — still show a minimal day plan from calendar if meetings remain.
- **Needs us now is cases-only and burning-only.** Print **owned OrgCS cases** that are high-urgency (`Severity_Level__c` Level 1, or hot Level 2), **business-stopping or business-impacting for the customer**, need **urgent effort**, and are **very time-sensitive** (typically 0–3). Production/outage/freeze with users blocked, customer operations stopped, escalation, unanswered inbound on us with that heat. **Keep the heading when the list is empty** (no “none” placeholder, no filler rows). **Keep out** Solution Provided waiting on the customer, Need More Information / NMI waiting on the customer, promised EOD close, quiet Sev-2, Status=Working with no heat. The model keeps Solution Provided and Need More Information out of Needs us now. Python does not move them. **DNS/cert is not a keep-out** — if the customer is back with a burning or urgent ask, it belongs here; quiet close-outs stay Follow-up due / Still watching. Those wait statuses belong in Follow-up due, Still watching, Quick wins, or Before you log off. Do **not** put Slack, Gmail, GUS, LAP, calendar, Omni, or the rest of the queue in that section. Never use `Case.Priority`. A case in this section is not also Follow-up due.
- **Follow-up due is cases-only, signal-only, and last among ranked case blocks.** Quiet past hold, pending us or pending them. Not unresponded inbound. **Level 1 / Sev-1: 1+ business day, listed first. All other severities: 2+ business days.** Honor internal follow-up dates, customer wait windows, and weekends. Wait/peek owned cases that **do not** trip this test → **👀 Still watching** (not calendar week; not Fire; not Needs us now; not a dump of the open queue). **Keep the heading when the list is empty** (count 0, no “none” placeholder). **Each case is its own bullet with `#CaseNumber — {short what-it-is}`** — never a comma-separated number list. Full rule at the end of this skill.
- **Every source, every run.** OrgCS cases (Description + Comment + Email + Feed + **Initial Response** + **GUS related list**), Case_Relationship/**LAP status+chatter**, GUS (**incl. SLA/due dates**), Gmail, **Slack (mandatory: paginate DMs + @-mentions + threads since last logout, then open leftovers with `slack_read_channel` / `slack_read_thread`** — plus this engineer’s **WOC roster post**, channel not hardcoded), **primary Calendar**, **Assembled** (shift login/logout, previous-logout overnight window, chat vs casework / lunch / break / your PTO / weekend), **Omni presence** (this engineer, during shift), and **team PTO** (named people only) are all mandatory gathers — never skip one because another looks empty or “thin.” Filter hard in ranking; gather complete. **AI analysis of OrgCS, GUS, Slack, Calendar, and Mail is mandatory every run.**
- **Assembled chat = reshape, don’t ignore.** A Chat (or live-queue) block on Assembled is not a footnote — the day plan must change. No 📥 / 🔧 inside chat hours.
- **OrgCS owns the shift clock. Assembled owns the block list and Omni.** Discover Assembled and team PTO with `list_calendars` every run — **never store a calendar id or calendar name in this skill.** Login and logout come from OrgCS Working Hours when written, else Assembled when `Engineer_Shift__c` is empty, else 8:00 AM–5:00 PM in the shift zone. Previous working day = previous weekday. Do not pull ~10 days of Assembled. Never use location/national holidays to decide if or when you work. Casework on a holiday = working day. Empty Assembled = not scheduled **unless** the Slack WOC roster lists this engineer that day — then it is a WOC working day; use 8:00 AM–5:00 PM in the resolved zone on the Shift line only. Never print the word `fallback` in the brief.
- **One weekend/WOC 📅 stamp.** Never two lines that both mean “you’re on WOC / weekend shift.” Merge Slack role + Assembled hours onto one line; if Assembled has no hours, print only `📅 WOC {day} — {role}`. **No roster and no Assembled weekend hours → omit the stamp and stay silent about it.** Never print that a WOC schedule was not found. Never print `No WOC line`, `no roster`, or `not on the roster`.
- **Omni during shift.** Match Omni to the Assembled block covering now. **In adherence** = **Screen Sharing** (never OOA) **or** the status for that block. **Busy**, Offline / `End of Work` / no current row on a **work** block, or Available missing that block’s channel → **point it out** (`⚠️ Omni: out of adherence`) and leave the plan alone. Offline during Assembled Lunch, Break, or Dinner is in adherence. Omit when in adherence, off-shift, or PTO. Do not roster other people’s Omni.
- **WOC from Slack, channel not hardcoded.** Discover the latest weekend-on-call **roster post** in **this engineer’s** Slack. Do not pin a channel, poster, or spreadsheet. If they are on that roster (after thread swaps), their remainder goes on **EOD of the last working day before that WOC**. Task Remainders named to them come from Slack the same way. Do not dump the team list. Print WOC as **one** header line (role from Slack; hours from Assembled only when present). **Print that line only when a roster (or Assembled weekend hours) is found.** No roster → omit, including in any preamble before the brief. Never print `⚠️ WOC schedule not found in Slack`, `No WOC line — no roster`, `no roster`, or `not on the roster`.
- **PTO every run.** Your PTO/Sick/Comp Off from **Assembled** (and primary if present). Named teammates on the team PTO calendar → one `Out: {Name} (PTO)` note, no plan rewrite. Ignore location/national holiday events on that calendar. Empty = omit the line.
- **Degrade only on hard failure.** Stamp `offline[]` only when that source’s tool **returned an error, was unloaded, or auth failed**. The Tools-offline popup is that list. Do **not** stamp because this host uses google-workspace names (`search_gmail_messages`, `list_calendars`, `get_events`) instead of `gmail_search` / `calendar_events`, because a payload was large and saved to a file, because you used `Engineer_Shift__c` for the shift clock, because Assembled has no hours today, or because you want them to click Refresh Calendar. If the tool returned rows, that source is not offline. Never a Sample OrgCS card. Deliver the rest. Sparse = "clear," not "skip."
- **Promised closes on that day; do not invent them.** If this engineer's last public CaseComment / outbound email told the customer the case would close at EOD / end of the business day today, that still-open case is on **today's EOD Before you log off** (SOD/MID: Today + late-day slot). If they named a day, it belongs on **that day's** brief. Offer-and-await ("if you are all set, let us know") is not a close date. A later keep-open or new customer question cancels the old promise. Note that a closure comment is owed; never invent EOD/close language from this skill.
- **Quote bar every publish.** Python fetches one new quote from a public API. Stamp `quote` on the page JSON. Never a hardcoded list. Never reuse the previous page quote. Never print it in chat. The model must not curl.
- **Peek is cases and GUS only.** Calendar invites never get Peek and never appear as No action needed on the page.


## Companion page

This skill is **one folder**. The Chrome extension already contains it as `skill/`. Recipients **Load unpacked** or install from the **Chrome Web Store** — they do not copy this folder into Cursor, Claude Code, OpenCode, or any other host skills directory. If you invoke the skill from an AI CLI without the extension, copy the same folder into that host’s skills directory. Every file the page needs lives here:

- `SKILL.md` — this file
- `README.md` — install for humans
- `page/template.html` — the page
- `scripts/publish-page.sh` — write `out/current.html` and `out/briefing.json` and open the page
- `scripts/calendar-bridge.py` — local Calendar update / RSVP (uses the recipient’s Google Workspace MCP; **do not** ship tokens)
- `scripts/install-native-host.py` — one-time native-host registration, run once by absolute path (Load unpacked or Chrome Web Store). Not again when Chrome quits.
- `out/` — generated `current.html` + `briefing.json` each run (do not ship a published page)

Update calendar / RSVP / **Refresh calendar** need the recipient’s Google Workspace MCP. Refresh calendar calls `get_events` for **today’s civil day** in the resolved shift timezone on the **primary** calendar — max **50** rows. Keep timed events that overlap **login − 2h through logout + 2h**. Also keep event ids the page sends as `importantEventIds` (gather-judged important meetings outside that pad). Include `needsAction`. Skip Assembled titles and all-day / multi-day blocks. Replace meeting rows; do not append. Do not re-gather cases, Slack, Gmail, or GUS.

### JSON rows (every run, live gather — not a stale snapshot)

- `id`, `label`, `detail`, `kind`. **Never** put the template verb `touch` (or `→ touch _(effort)_`) in a page row. That word is chat-brief shorthand; on the page the section title is enough. Plan block names and Add task titles say **Follow-up due**, never **Needs a touch**, never a percent-encoded label. `detail` is the fact (last customer-facing day, pending us / pending customer).
- `openCases` / `needYou` — gather snapshot only. The header tiles recompute: Open = remaining action items (cases you still owe + Slack + Mail + unscheduled customer meetings + quick wins + log-off); Need you = urgent subset (Needs us now + customer-named meetings + urgent Slack/Mail). 0 Need you is green; non-zero is red. Do **not** also stamp those counts as a header **Note** fact.
- `shiftStart` / `shiftEnd` — `HH:MM` Assembled login / logout in the shift zone (default `08:00` / `17:00`). Today's plan pad is these times ± 2 hours.
- `important: true` — only on calendar rows **outside** that pad that gather judged as real important meetings. Never a title regex.
- `startStamp` / `endStamp` = `YYYYMMDDTHHMMSS` in the **resolved shift timezone** (no `Z`). The page header shows **Refreshed** (`stamp`) and a live **Now** clock. Today's plan draws a red **now-line** through timed rows. When local zone ≠ shift zone, the plan card toggles between them; labels are populated from `data.timezone` and the browser zone via `Intl` — do **not** hardcode PT, IST, or “Shift time” / “Local time” in the skill or template.
- `quote` — `{ "text": "…", "author": "…" }` fetched this publish by Python. Omit when the fetch fails. Never reuse the previous page quote. The page paints it in the slim bar under the header. Never a hardcoded string.
- `summary` is the **Peek brief**, not the page punchline. **You write it** from CaseComment (public and internal) / email / feed / related LAP / investigation / related channels / GUS chatter — 4–8 sentences: what is broken, last customer-facing, last outbound, last internal hold, LAP/investigation status if linked, who has the ball. Do not paste a Python stub (`X last wrote:`, `Last outbound (Name):`, status one-liners). `detail` on the page stays one or two lines. `chronology[]` (`when`, `who`, `kind`, `text`) is a **timeline overview** of what happened, oldest → newest in JSON — the page paints newest on top. Never paste CommentBody / email / digest `activity[].text`. **Peek is Summary + Chronology only** — no fact chips, no “Also on this case.” Do not restamp Before you log off / Handoff / Tomorrow as chronology. **Only OrgCS cases and GUS get Peek** — never calendar invites, meetings, mail, or Slack. `gusUrl` / `workId` on GUS. Slack and Mail rows are classified the same way: search hits are candidates; you keep unanswered asks and drop FYI / bots / your own posts / digests.
- Tomorrow rows do **not** get Done. Done is for Needs us now, Customer asked for a meeting, Slack, Mail, Follow-up due, Before you log off, and composed Today's plan work blocks. Clicking **Done** / **Completed** stamps `done: true` (undo clears it) and POSTs `/page/sync` so Desk Chat answers on the new status — not localStorage alone. Completing a task or Needs us now row **strikes it on Today’s plan and silences its reminder** — it does **not** remove the clock row and does **not** delete Google Calendar. Completing **every** leftover Slack thread strikes 💬 Slack; every leftover email strikes ✉️ Mail; every Follow-up due row strikes Follow-ups; every promised-close row strikes 🔒 Case closures. The section count pill is **remaining** items and updates on Done / undo. When every item in a section is Done, the section **stays** on the page with the light-green completed treatment — it does not disappear. **The next Run Planner respects Done:** Slack/Mail already Done are omitted from leftovers (`slackch:` / `mailid:` / permalink without `/p…`); cases stay Done by `case:` number even when the row id changes (`need-` → `follow-`). Undo still clears the ledger key. If this engineer is on WOC, set `woc: { "day": "Saturday, Sep 19", "role": "Dev case", "hours": "8:00 AM–5:00 PM PT" }` on **Tomorrow, first thing** (hours optional). The page paints a **WOC Sat** chip and a stamp line. Do not emit a WOC header fact. Do not retitle the card WOC remainder.
- `eventId` + `htmlLink` on **every** primary-calendar event in the plan (meetings, meals, Important, Log In). **Never** emit Assembled rows (`assembled: true`, `kind: "assembled"`, Casework / Chat / Lunch / Break from the Assembled calendar). The page hides them if they slip in.
- `joinUrl` when a Meet / Zoom / Teams (or other conference) link exists — page shows **Join Meeting**. Omit or leave empty when there is none — page shows **Open Event**.
- `invite: true` + `rsvp` (`accepted` / `declined` / `tentative` / `needsAction`) when this engineer is an invitee. `rsvp` is **this engineer’s** attendee status from `get_events` `detailed: true`.
- `slackUrl` (or Slack message / channel / thread permalink) on every Slack row — page shows **Open Channel**, **Open DM**, **Open Thread**, or **Open Message**. Never a Calendar URL. Slack rows do not get Add Task / Add to calendar / Open Event.
- `mailUrl` (Gmail web link from `search_gmail_messages`) on every Mail row — page shows **Open Email**. Never a Calendar URL. Mail rows do not get Add Task / Add to calendar / Open Event / Peek.
- Customer-suggested meeting: `caseNumber`, `caseSubject`, `customerEmail` when known. If they named a day or time, also `meetingStartStamp`, `meetingEndStamp`, and `meetingGuests`. If they asked to meet and named neither a day nor a time, leave the stamps off. Both go in **Customer asked for a meeting** (under Needs us now): **No time yet** / **Ask for a time**, or **Named a time** / **Schedule Meeting**. Invite body is the Case Discussion subject, not `detail`. **Schedule Meeting** opens compose only. **Ask for a time** opens the case. **Refresh calendar** is what turns a booked slot green (`Action taken — scheduled.`) and places the event on Today's plan.
- `asTask: true` on Follow-up due, Before you log off, and tomorrow to-dos — not Slack (Slack opens the permalink), not Mail (Mail opens Gmail). **No action needed** is quiet **cases** (Still watching) — they may Peek. Do **not** put calendar invites there. **Every section heading on the page shows a one-line blurb**. Page `detail` is one or two lines — not the chat remainder. **Do not put Loose threads, a second Handoff column, Wins, Walk away, or quiet Status on the page.** Fold GEO handover into **Before you log off**. Chat brief still prints the full EOD skeleton.
- Slack `groups[]`: `{ title: "Not opened" | "Needs a reply", items }`. Set `urgent: true` when the ask is prompt (huddle / join now / asap); the page also infers from those words. Mail uses the same `groups[]`. (`Unread` / `Opened — still on you` still hoist into these.)
- `kind: "break"` optional (`plan-break-1`); 10–15m. The page may add short breaks up to **4 a day**, with at least 45 minutes between them, in leftover gaps after ~60m+ focus. Never immediately before or after Dinner, Breakfast, or Lunch. Never a pile.
- `bridgeUrl` is the URL the publish script actually bound (`http://127.0.0.1:8765` when that port is free). The page uses `location.origin` when opened from that server, so a fallback port still works.
- `facts` — **header only, fixed set:** Shift (login–logout) and Assembled schedule (today’s Assembled calendar blocks; `Working day` maps to Assembled schedule). Never Omni, Out, WOC, Note, OrgCS, or tool-offline lines. Omni / MCP gaps go in `offline[]` (popup).
- `offline` — array of `{ source, text }` **only** when that tool actually errored, was unloaded, or auth failed, or Omni is out of adherence during this Assembled block. Same popup for all of them (not a body card). Do not emit it after a successful gather that used google-workspace / GUS / OrgCS under this host’s tool names. Omni text is **Omni Is Out Of Adherence. You should be available for this Assembled block.** MCP text is `{Tool} MCP is unreachable.` × / OK dismisses for this load only. Never emit a Sample OrgCS section. `omniOffline: true` is the Omni shortcut. Flags `orgcsOffline` / `slackOffline` / `gmailOffline` / `calendarOffline` / `gusOffline` / `assembledOffline` also work. Do not FIRE Omni. Do not rebuild the plan.
- **Live Omni poll (extension, not Run Planner).** Every **15 minutes** Chrome calls `GET /omni/check`. **Assembled calendar only** (never primary / team PTO / User shift). Skip when that calendar is missing, empty around now, or PTO, **or when presence cannot be read**. Else: **Screen Sharing is never out of adherence**; otherwise the status for the block covering now (Available that contains that channel, including combos). **Busy** never on a work block. Offline during Assembled Lunch, Break, or Dinner is in adherence. Out of adherence → reminder-style popup showing **current Omni status** and the **Assembled block covering now**, with a **different sound** once every **15 seconds** until **Got It**. **Got It suppresses until the next 15-minute poll.** Still out then → start again. Do **not** print on-Omni labels on the page body. Do not rebuild the plan. Do not stamp a false Omni popup onto `offline[]` / `omniOffline`.
- `runner` — the **app** that published this page (`claude`, `cursor`, `opencode`, or any other). Not a model name. Stamp every publish. Do not store Express LLM Gateway tokens here.

### Calendar bridge

`calendar-bridge.py` listens on `127.0.0.1`, preferring **8765**. If 8765 (or the next candidate) is already in use by another program, it binds the next free port through **8799** and the publish script prints that URL — it does **not** kill the other listener. Closing the last planner tab stops our listener (launchd KeepAlive is off) so the port is free; clicking the toolbar icon starts it again. Omni and snapshot alarms never start the bridge when the tab is gone. Run Planner gather attaches CaseComment + email + feed into the draft; the model gets 20 minutes after that. The page POSTs `/calendar/update`, `/calendar/rsvp`, `/calendar/refresh`, `/calendar/delete`, `/page/sync`, and `/ask` to the origin it was opened from. Chrome also GETs `/omni/check` every 15 minutes for Assembled skip + Omni Offline. **Desk Chat** answers in natural language from this run’s page JSON plus a computed Clock for now/next (Express LLM Gateway on the local bridge — never store the token in the page or this skill). Clock omits `done: true` rows. Planner Buddy chip buttons POST `/ask`. Clock / now / this-hour answers follow the page clock switcher (`clock`, `viewZone`, and `viewShort` on the body) — do not assume a pair of zone names. When Run Planner finishes, the open page repaints from `/briefing.json` (and the extension iframe gets `page-refresh`). Never `location.reload` after a run — that trips the close-tab guard. **Done** writes `done` onto the published JSON via `/page/sync` so Desk Chat (in-page and extension) sees complete/incomplete. Completing a task or Today's plan work block strikes the row and silences its reminder — it does not remove the clock row and does not delete Google Calendar. Completing every leftover Slack thread / leftover email / Follow-up due row / promised-close row also strikes 💬 Slack / ✉️ Mail / Follow-ups / 🔒 Case closures. **Refresh calendar** queries today’s civil day, then **keeps shift ±2 hours** plus `importantEventIds` from the page (gather-judged important meetings). Skip Assembled titles and all-day blocks. It **replaces** meeting rows (it does not append) and writes those rows into the published page JSON so Ask is current. Never paste the Express LLM Gateway token into chat or this skill. Never hardcode a model. Run Planner and Desk Chat both use the **model this engineer saved** from the Express gateway list. Run Planner uses the **runner they saved** on the Token screen (Claude Code, Cursor Agent, OpenCode, Codex, Gemini CLI, or Aider — whichever CLIs are installed). The saved Express model is what inference uses; do not remap grok / GPT / other gateway ids to a Claude default. Claude Code is one runner, not a requirement. While it runs, the bridge records CLI steps on `GET /plan/status` so the extension can show a Compact / Verbose / Off log. That log is not the briefing. The Token screen lists live ping status for the planner MCPs it needs: OrgCS, GUS, Slack, Gmail, Calendar (`GET /mcp/status`). It also lists installed runners (`GET /runners`). It reads this machine’s MCP config (not one laptop’s plugin path). Omni is not an MCP. The bridge calls Google Workspace `manage_event` for duration/RSVP/delete. Duration update uses `send_updates: none` unless the event has guests you must notify. RSVP uses `send_updates: all`. Do not shrink a group meeting until this engineer clicks **Update calendar**.

## Appendix — this engineer (end of skill; do not inline into Step 2)

Short pointers live in Timezone and Step 2. The detail stays here so the middle of the skill stays a briefing.

### Identity

Do **not** hardcode this engineer’s name, title, manager, cloud, skill group, GEO, hub, or shift. A different Support user can run this skill.

1. `getUserInfo` — `userId`, Name, Title, Email, Manager (name / title / id). That `userId` is `OwnerId` for open cases. GUS Support Contact / Follow / LAP use this **Email** to resolve the GUS User Id — never Assignee.
2. SOQL that User:
   ```sql
   SELECT Id, Name, Title, Email, AboutMe, Manager.Name, Manager.Title,
          Engineer_Cloud__c, Engineer_Shift__c
   FROM User
   WHERE Id = '<userId>'
   LIMIT 1
   ```

**Print at the top of every brief** (SOD, Mid-Day, End of Day) — two lines, no `Identity` label:

```
**{Name}** — {Title}{, Cloud}{, Skill Group}
Manager: {Manager.Name}
```

Omit the manager line if Manager is blank. **Cloud / skill group — only if the record says so.** OrgCS has no `User.Description`; the free-text bio is **`AboutMe`**. If `AboutMe` names a **Cloud** or **Skill Group**, use that wording. If `AboutMe` has no Cloud line, `Engineer_Cloud__c` may fill Cloud when it is populated. If neither Cloud nor Skill Group is on the record, **do not mention a cloud or skill group** — do not invent “Service Cloud,” “Developer Support,” or a GEO team name.

Do **not** print Hub, Mobile, Working Days, or Working Hours from `AboutMe` as identity color. Working Hours on `AboutMe` are the shift hours when they are written there.

### Shift clock

Hours differ per engineer. Do not assume a night window. Do not assume AMER-PST or AMER-EST. Do not use the laptop zone as the shift zone.

1. OrgCS `Engineer_Shift__c` is the shift. The picklist sets the zone. Working Hours on the User record set login and logout when they are written there.
2. If `Engineer_Shift__c` is empty, Assembled login and logout are the shift, including the IANA zone on those events when it is present.
3. If a zone is known and hours are still empty, the shift is **8:00 AM–5:00 PM** in that zone.
4. If the zone is still unknown, leave it empty. Do not invent Pacific. `Asia/Kolkata`, `Asia/Calcutta`, and `Asia/Colombo` are the label IST only. The IANA name stays.

`Engineer_Shift__c` picklist, when the code is one of these. An unknown code stays unresolved.

| Picklist | Label | IANA |
|---|---|---|
| `PST` / `AMER-PST` | AMER-PST | `America/Los_Angeles` |
| `EST` / `AMER-EST` | AMER-EST | `America/New_York` |
| `IST` / `APAC-INDIA` | IST | `Asia/Kolkata` |
| `JAPAN` | JP | `Asia/Tokyo` |
| `APAC-ANZ` | APAC-ANZ | `Australia/Sydney` |
| `EMEA` | EMEA | `Europe/London` |
| `AMER-LATAM` | AMER-LATAM | `America/Sao_Paulo` |

Omni adherence stays on the Assembled block covering now. It does not use this clock.

Print the label and a **12-hour** clock **at the top of every brief** — in the header, never as the start of Follow-up due. Example: `📅 Shift 8:00 AM–5:00 PM PT · AMER-PST`. An IST-shift engineer sees IST hours and `IST`. A night shift stays overnight. Never print the word `fallback`.

If Assembled is unread for **today** but this engineer is on Slack WOC, use **8:00 AM–5:00 PM in the already-derived shift timezone** (from other Assembled days this cycle, or `Engineer_Shift__c`) on the Shift line only.

Untouched / business-day age uses **this same zone** (Saturday/Sunday in that zone do not count).

### Follow-up due

Job: owned cases that have gone **quiet** past a sensible hold — whether the next action sits with **us** or with the **customer**. This is **untouched**, not unresponded.

Unanswered inbound (customer / TAM / AE wrote, we have not replied) is **not** this section. That remains Needs us now / Fire.

**Did not trip this test → Still watching.** After the Follow-up due test, remaining **wait/peek owned cases** print under **👀 Still watching** (ball with the customer, hold not expired, Solution Provided awaiting confirm, already-nudged DNS, promised close on a future day). Do **not** use calendar week as the split. Do **not** dump Fire, Needs us now, Slack, or the rest of the open queue here. A wait/peek case is in Follow-up due **or** Still watching, not both.

Do **not** assume “latest public CaseComment or Incoming email is ours ⇒ pending the customer.” We may have been working a case for days with no customer-facing update. That silence still trips this section.

**This section is cases that trip the signal — nothing else.** Do not reprint Slack, Gmail, GUS, LAP, calendar, Omni, Needs us now, Fire, Today new-cases, Watch, or the rest of the queue. Zero hits → **omit the entire heading.** If a case is in **Needs us now**, do **not** also print it here.

Read **public CaseComment, Incoming email, internal CaseComment, and CaseFeed**. Internal notes are first-class for **holds and scheduled touches**. An internal comment is **not** a customer update. Private work for days with no public comment still counts as untouched.

**Holds — do not remind yet**
- Waiting on product, engineering, or a GUS investigation, with no expired “update by” date.
- An **internal** note that this case should be followed up on a **named date or time** — remind when that instant arrives in the resolved shift timezone, not before. If that date falls on a weekend, remind on the next shift weekday.
- The **customer** asked for N days, or until a date, or to wait until an event before the next update — remind only after that window ends.
- A dated commitment we already made to the customer (for example “we will process this on 9/18 and will update you then”) — on that date it is a touch we owe; before that date it is a hold.

**Remind** when a hold is absent or has expired, and any of these is true (business days in the resolved shift timezone):
- **Level 1 / Sev-1** — last **customer-facing** activity is **1+ business day** old. Higher priority: list these **first** in the block. Do not wait for day two on a quiet Sev-1.
- **All other severities** (Level 2 / 3 / 4, including quiet proactive DNS / Solution Provided) — last **customer-facing** activity is **2+ business days** old — whether we are waiting on them or still working it ourselves.
- An internal “follow up on {date/time}” is **due or overdue**.
- The customer’s requested wait has **ended** and we still have not touched the case.

**Weekends — Friday is not two days on Monday.** Age is business days, not calendar days. Saturday and Sunday in the shift timezone do not count. Last customer-facing **Friday**, this **Monday** = **1 business day** (Monday only). Sat+Sun are not two days of neglect. **Level 1 / Sev-1** with a Friday last-touch **does** trip on Monday (1 BD). **Level 2+** does **not** trip on Monday; earliest 2-day default after a Friday last-touch is **Tuesday** (Monday + Tuesday). Quiet proactive DNS (not Sev-1) still needs 2 business days. Last reply **Thursday**, today **Monday** = **1** business day (Sev-1 trips; Level 2+ does not).

Print under this heading (not Fire, not Needs us now unless heat also trips). **Sort Level 1 / Sev-1 first**, then the rest. List **every** case that trips. **Each case is its own `-` bullet:** `#CaseNumber — {short what-it-is}` (the domain, the customer, or the actual problem — one clause). A number without that one-liner is a miss — twenty cases will not be remembered by `#` alone. Same-wave DNS may share a last-touch day in the heading or a one-line group note, but do **not** collapse them into a comma-separated number dump. Those cases still belong in named day-plan `FOLLOW-UPS` slots (SOD/MID) and in EOD Tomorrow / skeleton carryovers — with the same one-liner there too.
