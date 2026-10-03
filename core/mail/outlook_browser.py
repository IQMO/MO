"""Personal Outlook mail through the operator's existing MO Connected Tab.

Page data stays inside this adapter and the direct mail operator result. The
normal browser snapshot/read tools must not be used for private mail content.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from urllib.parse import urlparse

from tools.browser import _browser_key_events, _manager
from ..browser_bridge import BrowserBridgeError, BrowserBridgeUnavailable


_LOCK = threading.RLock()
_DRAFTS: dict[str, tuple[str, str]] = {}  # session -> (tab ref, content digest)
_DELETIONS: dict[str, tuple[str, str, str]] = {}  # session -> (tab ref, path, message digest)
_ADDRESS = re.compile(r"^[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+$")


def _script(body: str) -> str:
    return "(() => { if(location.hostname!=='outlook.live.com' || !location.pathname.startsWith('/mail')) return JSON.stringify({error:'Outlook mail tab changed'}); " + body + " })()"


_INBOX = _script("""
 const box=[...document.querySelectorAll('[role=listbox]')].find(e=>
   (e.getAttribute('aria-label')||'').startsWith('Message list') && e.getClientRects().length);
 if(!box) return JSON.stringify({error:'Outlook inbox is not visible'});
 const rows=[...box.querySelectorAll('[role=option]')].slice(0,20);
 return JSON.stringify({messages:rows.map((e,i)=>({id:String(i+1),
   preview:String(e.innerText||e.getAttribute('aria-label')||'').trim().slice(0,500)}))});
""")

_SEARCH = _script("""
 const input=document.querySelector('input[aria-label="Search for email, meetings, files and more."]');
 const heading=[...document.querySelectorAll('[role=heading]')].some(e=>
   e.getClientRects().length && String(e.innerText||'').trim()==='Results');
 if(!heading || !input) return JSON.stringify({error:'Outlook search results are not visible'});
 const box=[...document.querySelectorAll('[role=listbox]')].find(e=>
   (e.getAttribute('aria-label')||'').startsWith('Message list') && e.getClientRects().length);
 const rows=[...(box?.querySelectorAll('[role=option]')||[])].slice(0,20);
 const noResults=/No results found|No results|didn.t find anything|We couldn.t find/i
   .test(document.body.innerText||'');
 return JSON.stringify({query:String(input.value||''), noResults, loaded:!!box,
   messages:rows.map((e,i)=>({id:String(i+1),
     preview:String(e.innerText||e.getAttribute('aria-label')||'').trim().slice(0,500)}))});
""")

_MESSAGE = _script("""
 const pane=document.querySelector('[role=main][aria-label="Reading Pane"]');
 const body=pane?.querySelector('[role=document][aria-label="Message body"]');
 const heading=pane?.querySelector('[role=heading]');
 if(!pane || !body || !heading) return JSON.stringify({error:'Outlook message is not open'});
 const sender=[...pane.querySelectorAll('[aria-label]')].find(e=>(e.getAttribute('aria-label')||'').startsWith('From:'));
 const recipient=[...pane.querySelectorAll('[aria-label]')].find(e=>(e.getAttribute('aria-label')||'').startsWith('To:'));
 return JSON.stringify({subject:String(heading.innerText||'').trim().slice(0,500),
   from:String(sender?.getAttribute('aria-label')||'').replace(/^From:\\s*/, '').slice(0,500),
   to:String(recipient?.getAttribute('aria-label')||'').replace(/^To:\\s*/, '').slice(0,500),
   body:String(body.innerText||'').slice(0,30000),
   truncated:String(body.innerText||'').length>30000});
""")

_COMPOSE = _script("""
 const visible=(selector)=>[...document.querySelectorAll(selector)].filter(e=>e.getClientRects().length);
 const tos=visible('[contenteditable=true][aria-label="To"]');
 const subjects=visible('input[aria-label="Subject"]');
 const bodies=visible('[contenteditable=true][aria-label="Message body"]');
 const to=tos[0], subject=subjects[0], body=bodies[0];
 if(tos.length!==1||subjects.length!==1||bodies.length!==1)
   return JSON.stringify({error:'One visible Outlook compose form is required'});
 if(!to||!subject||!body) return JSON.stringify({error:'Outlook compose form is not open'});
 const addresses=[...to.querySelectorAll('[contenteditable=false][aria-label]')]
   .map(e=>e.getAttribute('aria-label')||'').filter(x=>/^[^\\s@,;<>]+@[^\\s@,;<>]+\\.[^\\s@,;<>]+$/.test(x));
 const pendingRecipient=[...to.childNodes].filter(e=>e.nodeType===Node.TEXT_NODE)
   .some(e=>String(e.textContent||'').replace(/\\u200b/g,'').trim());
 const attachments=[...document.querySelectorAll('[aria-label]')].filter(e=>e.getClientRects().length)
   .some(e=>/^(Remove attachment|Remove file|Attachment:)/i.test(e.getAttribute('aria-label')||''));
 const extraRecipients=[...document.querySelectorAll(
   '[contenteditable=true][aria-label="Cc"],[contenteditable=true][aria-label="Bcc"],input[aria-label="Cc"],input[aria-label="Bcc"]')]
   .filter(e=>e.getClientRects().length)
   .some(e=>/@/.test(String(e.parentElement?.innerText||'')+' '+String(e.value||'')));
 return JSON.stringify({to:[...new Set(addresses)].join(', '), subject:String(subject.value||'').slice(0,255),
   body:String(body.innerText||'').slice(0,30000), attachments, extraRecipients, pendingRecipient});
""")


def _decode(manager, expression: str) -> dict:
    value = json.loads(manager._eval(expression))
    if not isinstance(value, dict):
        raise ValueError("Outlook returned an invalid page result")
    if value.get("error"):
        raise ValueError(str(value["error"]))
    return value


def _mail_tabs(manager) -> list[dict]:
    return [item for item in manager.client.catalog(cancel_event=manager.cancel_event)
            if urlparse(str(item.get("url") or "")).hostname == "outlook.live.com"
            and urlparse(str(item.get("url") or "")).path.startswith("/mail")]


def _tab(manager) -> str:
    matches = _mail_tabs(manager)
    if len(matches) != 1:
        raise ValueError("Open exactly one signed-in Outlook mail tab in Chrome with MO Connected Tab available")
    ref = str(matches[0].get("ref") or "")
    if not ref:
        raise ValueError("Outlook tab has no Connected Tab reference")
    error = manager._select_target(ref)
    if error:
        raise ValueError(error.removeprefix("Error: "))
    return ref


def _open_mail_tab_if_missing(manager) -> None:
    """Use MO's existing desktop-open action for a missing Outlook tab."""
    try:
        if _mail_tabs(manager):
            return  # The exact-one check remains in _tab; never open a duplicate.
    except BrowserBridgeUnavailable:
        pass  # Chrome may not have opened its native channel yet.
    from tools.computer import execute_computer_act

    result = execute_computer_act({"kind": "desktop", "action": "open",
                                   "url": "https://outlook.live.com/mail/"})
    if not result.startswith("Opened "):
        raise ValueError("Could not open Outlook Mail in the default browser")
    deadline = time.monotonic() + 4.0
    last_error = None
    while time.monotonic() < deadline:
        try:
            if _mail_tabs(manager):
                return
        except BrowserBridgeError as exc:
            last_error = exc  # Chrome can reconnect its native host after opening a tab.
        time.sleep(0.15)
    if last_error is not None:
        raise last_error
    raise ValueError("Outlook Mail opened, but MO Connected Tab did not attach in Chrome")


def _observe(manager, action: str):
    # Evidence contains the host and action only; never message or draft text.
    observation = manager._record_observation("mail", {"url": "https://outlook.live.com/mail/", "action": action})
    _, error = manager._validate_action()
    if error:
        raise ValueError(error.removeprefix("Error: "))
    return observation


def _act(manager, action: str, expression: str) -> str:
    observation = _observe(manager, action)
    result = str(manager._eval(_script(expression), access="act") or "")
    if result != "ok":
        raise ValueError(result or "Outlook control was unavailable")
    manager._record_action("mail", observation=observation, state_changed=True)
    return result


def _inbox(manager) -> list[dict]:
    # Preserve the operator's current Focused/Other inbox view when it is open.
    if not _decode(manager, _script("return JSON.stringify({inbox:location.pathname.startsWith('/mail/inbox')});"))["inbox"]:
        _act(manager, "outlook_inbox", """
          const folder=[...document.querySelectorAll('[role=treeitem]')].find(e=>
            (e.getAttribute('aria-label')||'').trim()==='Inbox' ||
            String(e.innerText||'').split('\\n').some(line=>line.trim()==='Inbox'));
          if(!folder) return 'Inbox folder is unavailable'; folder.click(); return 'ok';
        """)
    inbox_visible = False
    for _ in range(20):
        try:
            rows = list(_decode(manager, _INBOX)["messages"])
            inbox_visible = True
            if rows:
                return rows
        except (ValueError, KeyError):
            pass
        time.sleep(0.1)
    if not inbox_visible:
        raise ValueError("Outlook inbox did not load")
    raise ValueError("Outlook showed no inbox rows; refresh the signed-in tab and try again")


def _search(manager, query: str) -> list[dict]:
    query = str(query or "").strip()
    if not query or len(query) > 300:
        raise ValueError("Outlook search needs a query of 1–300 characters")
    # Leave an earlier result view before submitting a new query, so its rows
    # cannot be mistaken for the new result while Outlook is still loading.
    prior = _decode(manager, _script("""
      return JSON.stringify({results:[...document.querySelectorAll('[role=heading]')].some(e=>
        e.getClientRects().length && String(e.innerText||'').trim()==='Results')});
    """))
    if prior["results"]:
        _act(manager, "outlook_close_prior_search", """
          const buttons=[...document.querySelectorAll('button')].filter(e=>
            e.getClientRects().length && e.getAttribute('aria-label')==='Close search');
          if(buttons.length!==1) return 'Outlook could not close the previous search';
          buttons[0].click();return 'ok';
        """)
        for _ in range(20):
            state = _decode(manager, _script("""
              return JSON.stringify({results:[...document.querySelectorAll('[role=heading]')].some(e=>
                e.getClientRects().length && String(e.innerText||'').trim()==='Results')});
            """))
            if not state["results"]:
                break
            time.sleep(0.1)
        else:
            raise ValueError("Outlook previous search did not close")
    observation = _observe(manager, "outlook_search")
    focus = _script("""
      const fields=[...document.querySelectorAll('input[aria-label="Search for email, meetings, files and more."]')]
        .filter(e=>e.getClientRects().length);
      if(fields.length!==1) return 'One visible Outlook search field is required';
      fields[0].focus();return document.activeElement===fields[0]?'ok':'Outlook search field could not be focused';
    """)
    result = manager._eval(focus, access="act")
    if result != "ok":
        raise ValueError(str(result))
    for event in _browser_key_events("ctrl+a"):
        manager._cmd("Input.dispatchKeyEvent", event, access="act")
    manager._cmd("Input.insertText", {"text": query}, access="act")
    for event in _browser_key_events("enter"):
        manager._cmd("Input.dispatchKeyEvent", event, access="act")
    manager._record_action("mail", observation=observation, state_changed=True)
    for _ in range(30):
        try:
            result = _decode(manager, _SEARCH)
            if result.get("query") == query and (result.get("messages") or result.get("noResults")):
                return list(result.get("messages") or [])
        except ValueError:
            pass
        time.sleep(0.1)
    raise ValueError("Outlook search results did not finish loading")


def _current_rows(manager) -> list[dict]:
    """Read the visible result list if searching; otherwise use the inbox."""
    mode = _decode(manager, _script("""
      const input=document.querySelector('input[aria-label="Search for email, meetings, files and more."]');
      const results=[...document.querySelectorAll('[role=heading]')].some(e=>
        e.getClientRects().length && String(e.innerText||'').trim()==='Results');
      return JSON.stringify({search:results && !!String(input?.value||'').trim()});
    """))
    if mode["search"]:
        result = _decode(manager, _SEARCH)
        if not result.get("messages") and not result.get("noResults"):
            raise ValueError("Outlook search results did not finish loading")
        return list(result.get("messages") or [])
    return _inbox(manager)


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _session(agent) -> str:
    return str(getattr(getattr(agent, "session", None), "session_id", "") or "")


def _open_row(manager, rows: list[dict], ident: str) -> dict:
    index = int(str(ident or "0")) - 1
    if not 0 <= index < len(rows):
        raise ValueError("Choose a number from the current Outlook list (1–20)")
    _act(manager, "outlook_open_message", """
      const box=[...document.querySelectorAll('[role=listbox]')].find(e=>
        (e.getAttribute('aria-label')||'').startsWith('Message list') && e.getClientRects().length);
      const row=box?.querySelectorAll('[role=option]')[__INDEX__];
      if(!row) return 'Outlook message list changed'; row.click(); return 'ok';
    """.replace("__INDEX__", str(index)))
    for _ in range(20):
        try:
            message = _decode(manager, _MESSAGE)
            selection = _decode(manager, _script("""
              const box=[...document.querySelectorAll('[role=listbox]')].find(e=>
                (e.getAttribute('aria-label')||'').startsWith('Message list') && e.getClientRects().length);
              const row=box?.querySelectorAll('[role=option]')[__INDEX__];
              return JSON.stringify({selected:row?.getAttribute('aria-selected')==='true'});
            """.replace("__INDEX__", str(index))))
            expected = re.sub(r"\s+", " ", str(rows[index].get("preview") or "")).lower()
            subject = re.sub(r"\s+", " ", str(message.get("subject") or "")).lower()
            if selection["selected"] and subject and subject in expected and message.get("from") and message.get("body"):
                message["id"] = str(index + 1)
                return message
        except ValueError:
            pass
        time.sleep(0.1)
    raise ValueError("Outlook message content did not finish loading; no partial read was returned")


def _message_identity(manager, message: dict) -> tuple[str, str]:
    path = _decode(manager, _script("return JSON.stringify({path:location.pathname});"))["path"]
    if not str(path).startswith("/mail/"):
        raise ValueError("Outlook message path changed")
    return str(path), _digest({"subject": message["subject"], "from": message["from"], "body": message["body"]})


def _toolbar_click(manager, label: str, action: str) -> None:
    _act(manager, action, """
      const buttons=[...document.querySelectorAll('[role=toolbar] button')].filter(e=>
        e.getClientRects().length && e.getAttribute('aria-label')===__LABEL__);
      if(buttons.length!==1) return 'One visible Outlook __NAME__ control is required';
      buttons[0].click(); return 'ok';
    """.replace("__LABEL__", json.dumps(label)).replace("__NAME__", label))


def execute(action: str, arguments: dict, *, agent=None) -> dict:
    manager = _manager({"_cancel_event": getattr(agent, "_cancel_event", None)})
    try:
        if action in {"status", "list", "search", "read_latest", "folders"}:
            _open_mail_tab_if_missing(manager)
        ref = _tab(manager)
        if action == "folders":
            found = _decode(manager, _script("""
              return JSON.stringify({folders:[...document.querySelectorAll('[role=treeitem][data-folder-name]')]
                .filter(e=>e.getClientRects().length && Number(e.getAttribute('aria-level'))>=2)
                .map(e=>String(e.getAttribute('data-folder-name')||'').trim())});
            """))
            names = [name for name in found.get("folders", [])
                     if isinstance(name, str) and name and len(name) <= 120]
            return {"folders": sorted({name for name in names if names.count(name) == 1}, key=str.casefold)}
        if action == "status":
            if not _decode(manager, _script("""
              return JSON.stringify({ready:!!document.querySelector(
                '[role=listbox],input[aria-label="Search for email, meetings, files and more."],[contenteditable=true][aria-label=To]')});
            """))["ready"]:
                raise ValueError("Outlook Mail has not finished loading or is signed out")
            return {"state": "connected", "method": "connected_tab"}
        if action in {"list", "search", "read_latest", "read", "archive", "mark_read", "move", "trash"}:
            query = str(arguments.get("query") or "").strip()
            if action == "search" and not query:
                raise ValueError("Outlook search needs a query")
            rows = (_search(manager, query) if query and action in {"search", "read_latest"}
                    else _current_rows(manager) if action in {"read", "archive", "mark_read", "move", "trash"}
                    else _inbox(manager))
            if action in {"list", "search"}:
                limit = max(1, min(20, int(arguments.get("limit") or 10)))
                return {"messages": rows[:limit], "view": "search" if action == "search" else "inbox"}
            if not rows:
                return {"state": "not_found"}
            ident = "1" if action == "read_latest" else str(arguments.get("id") or "")
            expected = str(arguments.get("expected_preview_hash") or "")
            if expected:
                index = int(ident) - 1
                if (not re.fullmatch(r"[0-9a-f]{64}", expected)
                        or not 0 <= index < len(rows)
                        or hashlib.sha256(str(rows[index].get("preview") or "").encode()).hexdigest() != expected):
                    raise ValueError("Outlook message list changed; refresh it before acting")
            if action == "mark_read":
                index = int(ident) - 1
                if not 0 <= index < len(rows):
                    raise ValueError("Choose a number from the current Outlook list (1–20)")
                unread = _decode(manager, _script("""
                  const box=[...document.querySelectorAll('[role=listbox]')].find(e=>
                    (e.getAttribute('aria-label')||'').startsWith('Message list') && e.getClientRects().length);
                  const row=box?.querySelectorAll('[role=option]')[__INDEX__];
                  return JSON.stringify({unread:!!row?.querySelector('button[title="Mark as read"]')});
                """.replace("__INDEX__", str(index))))["unread"]
                if unread:
                    _act(manager, "outlook_mark_read", """
                      const box=[...document.querySelectorAll('[role=listbox]')].find(e=>
                        (e.getAttribute('aria-label')||'').startsWith('Message list') && e.getClientRects().length);
                      const button=box?.querySelectorAll('[role=option]')[__INDEX__]?.querySelector('button[title="Mark as read"]');
                      if(!button) return 'Outlook read control changed'; button.click(); return 'ok';
                    """.replace("__INDEX__", str(index)))
                return {"state": "mark_read", "id": ident, "already_read": not unread}
            message = _open_row(manager, rows, ident)
            if action in {"read", "read_latest"}:
                return message
            if action == "archive":
                _toolbar_click(manager, "Archive", "outlook_archive")
                return {"state": "archive", "id": ident, "subject": message["subject"]}
            if action == "trash":
                session = _session(agent)
                if not session:
                    raise ValueError("Outlook trash needs a live MO conversation")
                path, fingerprint = _message_identity(manager, message)
                with _LOCK:
                    _DELETIONS[session] = (ref, path, fingerprint)
                return {"state": "ready_to_approve", "fingerprint": fingerprint, "subject": message["subject"]}
            if action == "move":
                folder = str(arguments.get("folder") or "").strip()
                if not folder or len(folder) > 120 or any(c in folder for c in "\r\n"):
                    raise ValueError("Move needs one existing Outlook folder name")
                _toolbar_click(manager, "Move to", "outlook_move_menu")
                _act(manager, "outlook_folder_picker", """
                  const menu=document.querySelector('[role=menu][aria-label="Move to"]');
                  const item=[...(menu?.querySelectorAll('[role=menuitem]')||[])].find(e=>
                    e.getClientRects().length && e.getAttribute('aria-label')==='Select a different folder');
                  if(!item) return 'Outlook folder picker is unavailable'; item.click(); return 'ok';
                """)
                picker_focus = _script("""
                  const inputs=[...document.querySelectorAll('[role=dialog] input[placeholder="Type folder or group name"]')]
                    .filter(e=>e.getClientRects().length);
                  if(inputs.length!==1) return 'waiting';
                  inputs[0].focus();return document.activeElement===inputs[0]?'ok':'waiting';
                """)
                for _ in range(20):
                    if manager._eval(picker_focus, access="act") == "ok":
                        break
                    time.sleep(0.1)
                else:
                    raise ValueError("Outlook folder search field is unavailable")
                observation = _observe(manager, "outlook_folder_search")
                for event in _browser_key_events("ctrl+a"):
                    manager._cmd("Input.dispatchKeyEvent", event, access="act")
                manager._cmd("Input.insertText", {"text": folder}, access="act")
                manager._record_action("mail", observation=observation, state_changed=True)
                folder_json = json.dumps(folder)
                for _ in range(20):
                    matches = _decode(manager, _script("""
                      const dialog=[...document.querySelectorAll('[role=dialog]')].find(e=>e.getClientRects().length);
                      const names=[...(dialog?.querySelectorAll('[role=treeitem]')||[])].filter(e=>e.getClientRects().length)
                        .filter(e=>String(e.querySelector(':scope > div')?.innerText||'').split('\\n')
                          .some(line=>line.trim()===__FOLDER__));
                      return JSON.stringify({matches:names.length});
                    """.replace("__FOLDER__", folder_json)))["matches"]
                    if matches == 1:
                        break
                    time.sleep(0.1)
                if matches != 1:
                    raise ValueError("Choose one exact visible Outlook folder; no message was moved")
                _act(manager, "outlook_select_folder", """
                  const dialog=[...document.querySelectorAll('[role=dialog]')].find(e=>e.getClientRects().length);
                  const names=[...(dialog?.querySelectorAll('[role=treeitem]')||[])].filter(e=>e.getClientRects().length)
                    .filter(e=>String(e.querySelector(':scope > div')?.innerText||'').split('\\n')
                      .some(line=>line.trim()===__FOLDER__));
                  if(names.length!==1) return 'Outlook folder changed'; names[0].click(); return 'ok';
                """.replace("__FOLDER__", folder_json))
                _act(manager, "outlook_move", """
                  const dialog=[...document.querySelectorAll('[role=dialog]')].find(e=>e.getClientRects().length);
                  const buttons=[...(dialog?.querySelectorAll('button')||[])].filter(e=>
                    e.getClientRects().length && String(e.innerText||'').trim()==='Move');
                  if(buttons.length!==1 || buttons[0].disabled) return 'Outlook Move control is unavailable';
                  buttons[0].click(); return 'ok';
                """)
                return {"state": "move_clicked", "folder": folder, "subject": message["subject"]}
        if action == "draft":
            to = str(arguments.get("to") or "").strip()
            subject = str(arguments.get("subject") or "").strip()
            body = str(arguments.get("body") or "")
            if not _ADDRESS.fullmatch(to) or not subject or not body or len(subject) > 255 or len(body) > 30000:
                raise ValueError("Draft needs one valid To address, a subject, and a body (up to 30,000 characters)")
            new_mail = _script("return [...document.querySelectorAll('button')].filter(e=>e.getClientRects().length&&"
                               "(e.getAttribute('aria-label')||e.innerText||'').trim()==='New mail').length;")
            for _ in range(20):
                if manager._eval(new_mail) == 1:
                    break
                time.sleep(0.1)
            else:
                raise ValueError("Outlook New mail control did not load")
            _act(manager, "outlook_new_draft", """
              const buttons=[...document.querySelectorAll('button')].filter(e=>e.getClientRects().length&&
                (e.getAttribute('aria-label')||e.innerText||'').trim()==='New mail');
              if(buttons.length!==1) return 'One New mail control is required'; buttons[0].click(); return 'ok';
            """)
            # Insert text through the existing browser's real input event path.
            for label, value in (("To", to), ("Subject", subject), ("Message body", body)):
                observation = _observe(manager, "outlook_draft_" + label.lower().replace(" ", "_"))
                selector = (f'[contenteditable=true][aria-label="{label}"]'
                            if label != "Subject" else 'input[aria-label="Subject"]')
                focus_script = _script(
                    f"const fields=[...document.querySelectorAll({json.dumps(selector)})].filter(e=>e.getClientRects().length); "
                    "if(fields.length!==1) return 'one visible field required'; "
                    "fields[0].focus(); return document.activeElement===fields[0]?'ok':'not focused';"
                )
                for _ in range(20):
                    if manager._eval(focus_script, access="act") == "ok":
                        break
                    time.sleep(0.1)
                else:
                    raise ValueError(f"Outlook {label} field is unavailable")
                manager._cmd("Input.insertText", {"text": value}, access="act")
                manager._record_action("mail", observation=observation, state_changed=True)
                if label == "To":
                    match = json.dumps("Use this address: " + to)
                    suggestion = ("const picker=[...document.querySelectorAll('[role=listbox]')].filter(e=>"
                                  "e.getClientRects().length&&e.getAttribute('aria-label')==='Recipient Picker');"
                                  "const buttons=picker.flatMap(e=>[...e.querySelectorAll('button')]).filter(e=>"
                                  f"String(e.innerText||'').trim()==={match});")
                    for _ in range(20):
                        if manager._eval(_script(suggestion + "return buttons.length===1?'ready':'waiting';")) == "ready":
                            break
                        time.sleep(0.1)
                    else:
                        raise ValueError("Outlook did not offer the exact recipient address")
                    _act(manager, "outlook_commit_recipient", suggestion +
                         "if(buttons.length!==1)return 'Exact recipient suggestion unavailable';"
                         "buttons[0].click();return 'ok';")
            for _ in range(20):
                form = _decode(manager, _COMPOSE)
                if (form["subject"] == subject and form["body"] == body and form["to"] == to
                        and not form.get("attachments") and not form.get("extraRecipients")
                        and not form.get("pendingRecipient")):
                    break
                time.sleep(0.1)
            else:
                raise ValueError("Outlook draft fields could not be verified; review the open compose form")
            session = _session(agent)
            if session:
                with _LOCK:
                    _DRAFTS[session] = (ref, _digest({"to": to, "subject": subject, "body": body}))
            return {"state": "browser_draft", "to": to, "subject": subject}
        if action == "send":
            session = _session(agent)
            with _LOCK:
                saved = _DRAFTS.get(session)
            if not saved or saved[0] != ref:
                raise ValueError("Create a draft in this MO conversation before sending")
            form = _decode(manager, _COMPOSE)
            if form.get("attachments") or form.get("extraRecipients") or form.get("pendingRecipient"):
                raise ValueError("Outlook draft has attachments, Cc/Bcc, or an uncommitted recipient")
            if _digest({"to": form["to"], "subject": form["subject"], "body": form["body"]}) != saved[1]:
                raise ValueError("Outlook draft changed; create a new MO draft before sending")
            return {"state": "ready_to_approve", "fingerprint": saved[1], "to": form["to"], "subject": form["subject"]}
        raise ValueError("unsupported Outlook mail action")
    finally:
        manager._release_control()


def confirm_send(agent, fingerprint: str) -> dict:
    manager = _manager({"_cancel_event": getattr(agent, "_cancel_event", None)})
    try:
        ref = _tab(manager)
        session = _session(agent)
        with _LOCK:
            saved = _DRAFTS.get(session)
        if not saved or saved != (ref, fingerprint):
            raise ValueError("Outlook draft approval no longer matches this conversation")
        form = _decode(manager, _COMPOSE)
        if form.get("attachments") or form.get("extraRecipients") or form.get("pendingRecipient"):
            raise ValueError("Outlook draft has attachments, Cc/Bcc, or an uncommitted recipient")
        if _digest({"to": form["to"], "subject": form["subject"], "body": form["body"]}) != fingerprint:
            raise ValueError("Outlook draft changed after approval was requested")
        _act(manager, "outlook_send", """
          const buttons=[...document.querySelectorAll('button')].filter(e=>e.getClientRects().length &&
            (e.getAttribute('aria-label')||e.innerText||'').trim()==='Send');
          if(buttons.length!==1) return 'One visible Send control is required'; buttons[0].click(); return 'ok';
        """)
        with _LOCK:
            _DRAFTS.pop(session, None)
        return {"state": "send_clicked"}
    finally:
        manager._release_control()


def confirm_trash(agent, fingerprint: str) -> dict:
    manager = _manager({"_cancel_event": getattr(agent, "_cancel_event", None)})
    try:
        ref = _tab(manager)
        session = _session(agent)
        with _LOCK:
            saved = _DELETIONS.get(session)
        if not saved or saved[0] != ref or saved[2] != fingerprint:
            raise ValueError("Outlook delete approval no longer matches this conversation")
        current = _decode(manager, _MESSAGE)
        path, digest = _message_identity(manager, current)
        if (ref, path, digest) != saved:
            raise ValueError("Outlook message changed after deletion was requested")
        _toolbar_click(manager, "Delete", "outlook_delete")
        with _LOCK:
            _DELETIONS.pop(session, None)
        return {"state": "delete_clicked"}
    finally:
        manager._release_control()
