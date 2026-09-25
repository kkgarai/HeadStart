# OrgCS activity fetch (not the planner)

You only fetch this engineer's **open owned** cases, then those cases' thread bodies. Python clips them. Then you stop. Do not plan. Do not Peek. Do not Read SKILL.md. Do not Write `/tmp/plan-ai.json`.

Never invent ids. Never put JavaScript in SOQL. Never `HtmlBody`. Never print `CommentBody` / `TextBody` / `Body`.
Never fetch a **closed** case (`IsClosed = true`) or a case this engineer does not own.

Copy every `500…` ParentId **verbatim** from the **open Case** query you just ran. Ignore any other 500-ids in the user message — those may be closed or transferred leftovers.

1. Resolve this engineer's User Id with `mcp__orgcs__soqlQuery`. The user message has the email. `SELECT Id FROM User WHERE Email = '<email>' OR Username = '<email>' LIMIT 5`. Use the one Id that comes back. If `mcp__orgcs__getUserInfo` errors, ignore it and keep going. That error is not a reason to stop.
2. `mcp__orgcs__soqlQuery`: `SELECT Id, CaseNumber, Subject, Status, Severity_Level__c, LastModifiedDate, IsClosed, SE_Initial_Response_Status__c, SE_Target_Response__c, First_Response_Date_Time__c, GUS_Investigation_Number__c, Display_Bug__c FROM Case WHERE OwnerId = '<that Id>' AND IsClosed = false ORDER BY LastModifiedDate DESC LIMIT 80` (never `Description`).
3. Write `{"records":[...]}` to `/tmp/owned-cases.json` (those records only).
4. **One query per case**, not one shared LIMIT across the queue. Page until a page comes back short. **EmailMessage is mandatory — never skip it for CaseComment.** Do not drop a comment or email to save size.
   - `SELECT ParentId, CommentBody, CreatedDate, CreatedBy.Name, IsPublished FROM CaseComment WHERE ParentId = '<one id>' ORDER BY CreatedDate DESC LIMIT 200` then `CreatedDate < <oldest>` until a short page
   - `SELECT ParentId, Incoming, MessageDate, TextBody, FromName, FromAddress, Subject FROM EmailMessage WHERE ParentId = '<one id>' ORDER BY MessageDate DESC LIMIT 200` then `MessageDate < <oldest>` until a short page
   - `SELECT ParentId, Body, CreatedDate, CreatedBy.Name, Type FROM CaseFeed WHERE ParentId = '<one id>' ORDER BY CreatedDate DESC LIMIT 200` — skip Type TrackedChange, ChangeStatusPost, CaseCommentPost, EmailMessageEvent. Keep a feed row only when Body is real text.

Overflow = success. Do not Read the file. Do not slim. Do not `--cards`. Reply: `fetched`
