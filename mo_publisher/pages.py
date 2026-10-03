"""Accessible, script-free publisher pages using MO's canonical default skin."""

import json
import re
from datetime import date
from html import escape
from pathlib import Path


def load_settings(path: Path) -> dict[str, str]:
    if not path.is_absolute():
        raise ValueError("Publisher configuration must be an absolute private path")
    data = json.loads(path.read_text(encoding="utf-8"))
    required = {"publisher_name", "support_email", "effective_date", "hosting", "backups", "support_retention"}
    if set(data) != required or any(not isinstance(v, str) or not v.strip() or len(v) > 2000 for v in data.values()):
        raise ValueError("Complete the publisher identity and operating privacy facts")
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", data["support_email"]):
        raise ValueError("Invalid public support email")
    date.fromisoformat(data["effective_date"])
    return data


def stylesheet() -> str:
    # The same reviewed wire tokens seed an unpaired Android client. Do not add
    # an independent publisher palette or load an operator's private theme.
    skin_path = Path(__file__).resolve().parents[1] / "mo_everywhere/contracts/skin_v1/default-skin.json"
    skin = json.loads(skin_path.read_text(encoding="utf-8"))
    tokens = "".join(f"--{key}:{value};" for key, value in skin.items() if key != "name" and re.fullmatch(r"#[0-9a-fA-F]{6}", value))
    return ":root{" + tokens + "}" + """
*{box-sizing:border-box}html{color-scheme:dark}body{margin:0;background:var(--background);color:var(--text);font:17px/1.7 system-ui,sans-serif}
a{color:var(--brand);text-underline-offset:.25em}a:hover{color:var(--glow)}a:focus-visible{outline:2px solid var(--glow);outline-offset:5px}
.skip{position:absolute;left:1rem;top:-5rem;background:var(--input);padding:.5rem}.skip:focus{top:1rem}
header,main,footer{max-width:1040px;margin:auto;padding:1.3rem 1.5rem}header{display:flex;align-items:center;justify-content:space-between;gap:1rem;border-bottom:1px solid var(--border)}
.identity{font-weight:700;text-decoration:none;letter-spacing:.08em;white-space:nowrap}nav{display:flex;flex-wrap:wrap;gap:1.2rem}nav a[aria-current=page]{color:var(--text)}
main{min-height:65vh;padding-top:3rem;padding-bottom:4rem}h1{font-size:clamp(2rem,5vw,3.5rem);line-height:1.13;font-weight:600;letter-spacing:-.035em;margin:.2rem 0 1.5rem;max-width:18ch}
h2{font-size:1.3rem;line-height:1.4;margin-top:2.5rem}p,li{max-width:76ch}p{margin:1rem 0}.eyebrow{color:var(--brand);font-size:.85rem;letter-spacing:.12em;text-transform:uppercase}
.lead{font-size:1.2rem;max-width:58ch}.note{border-left:2px solid var(--brand);padding:.3rem 1.3rem;background:var(--input);margin:2rem 0}
.links{display:flex;flex-wrap:wrap;gap:1rem;margin:2rem 0}.links a{border:1px solid var(--border);padding:.6rem 1rem;border-radius:8px;text-decoration:none}
footer{border-top:1px solid var(--border);color:var(--muted);font-size:.9rem}footer p{margin:.4rem 0}code{overflow-wrap:anywhere;background:var(--input);padding:.1em .3em;border-radius:3px}
@media(max-width:650px){header{align-items:flex-start;flex-direction:column}header,main,footer{padding-left:1.2rem;padding-right:1.2rem}main{padding-top:2rem}nav{gap:.8rem;font-size:.94rem}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
"""


def pages(settings: dict[str, str]) -> dict[str, str]:
    values = {key: escape(value, quote=True) for key, value in settings.items()}
    email = values["support_email"]
    contact = f'<a href="mailto:{email}">{email}</a>'

    def layout(path, title, body):
        links = (("/", "Home"), ("/support", "Support"), ("/privacy", "Privacy"), ("/delete-data", "Delete data"))
        nav = "".join(f'<a href="{url}"' + (' aria-current="page"' if path == url else "") + f'>{label}</a>' for url, label in links)
        return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="MO Everywhere for Android. Setup, support, privacy and data deletion.">
<title>{escape(title)} · MO Everywhere</title><link rel="stylesheet" href="/style.css"></head>
<body><a class="skip" href="#main">Skip to content</a><header><a class="identity" href="/">MO EVERYWHERE</a><nav aria-label="Main">{nav}</nav></header>
<main id="main">{body}</main><footer><p>MO Everywhere · {values['publisher_name']}</p><p>Support and privacy: {contact}</p></footer></body></html>'''

    home = '''<p class="eyebrow">The Android companion to MO Agent</p><h1>Your agent works.<br>You keep moving.</h1>
<p class="lead">Stay connected to MO Agent on your own computer or server. Continue a conversation, follow work and reach your projects from your phone, wherever your Hub is reachable.</p>
<div class="note"><p><strong>Closed testing.</strong> Version 0.1.62 is available to selected Google Play testers. Public production access is pending. Hub, Remote and file features require your own configured systems and reviewed device access.</p></div>
<div class="links"><a href="https://play.google.com/apps/testing/app.moagent.mobile">Closed-test access on Google Play</a><a href="/support">Set up your connection</a><a href="/privacy">Your data and choices</a></div>
<h2>The working agent stays on your machine</h2><p>MO Agent is a local-first AI coding and working agent. Through your paired Hub, ask it to work on a project, research a question or organize files using the tools and providers you configured. The host carries out the work under its existing access and approval rules.</p>
<h2>Leave the desk. Stay involved.</h2><p>Continue a conversation you choose to share across your connected devices. Check Dashboard, start background tasks from your phone and follow their progress, or schedule a timed follow-up in Control &rarr; Work. Keep your Hub running and your phone connected to receive completion and problem notifications.</p>
<h2>Start on your phone</h2><p>Use your own HTTPS OpenAI-compatible provider, model and API key for separate phone chat. Enable image input only when your selected provider and model support it. Phone chat does not run the full MO Agent tool runtime.</p>
<h2>Reach the tools around your work</h2><p>Browse authorized file sources and exchange documents. Open compatible Desktop hosts and terminal workspaces with touch, cursor and keyboard controls. You can also select one phone folder for read-only access by authorized Hub devices while sharing is enabled and the phone is unlocked.</p>
<h2>Pair deliberately. Stay in control.</h2><p>Review the Hub and device permissions before pairing. Access is scoped and revocable. Share conversations and selected files explicitly. Hub provider keys stay on the Hub; your private profile is not automatically copied to Android. The optional floating Cube keeps MO close. Microphone and camera access are optional, and you can report a problem with an AI response from the app.</p>
<p>Google Play is the public app installation and update source. The Play app does not provide privileged Android phone automation.</p>
<p>The app does not include a hosted Hub, an AI subscription or unlimited AI usage. Your provider may charge separately. Hub features require MO Agent on a computer or server you are authorized to use. Follow the <a href="https://github.com/IQMO/MO#quickstart">MO Agent installation guide</a>, then configure your <a href="https://github.com/IQMO/MO/blob/main/mo_everywhere/README.md">MO Hub</a>.</p>'''

    support = f'''<p class="eyebrow">Setup and support</p><h1>Get started with MO.</h1>
<div class="note"><p>Google Play currently offers version 0.1.62 to selected closed-test users. Install <a href="https://github.com/IQMO/MO#quickstart">MO Agent</a> and configure a <a href="https://github.com/IQMO/MO/blob/main/mo_everywhere/README.md">MO Hub</a> on your own computer or server, or obtain access from an authorized operator. Buying the app does not supply a hosted Hub or grant access to the publisher's private systems.</p></div>
<h2>Bring your existing work with you</h2><p>Run MO Agent and its Hub on your own computer or server. Hub chat reaches that agent, its configured tools and provider catalog. Use Dashboard for available work/project actions; Control &rarr; Work starts phone-owned background tasks and schedules timed MO turns. Tasks continue on the host while it remains running. The phone does not gain ownership of every host process or another device's schedules.</p>
<h2>Chat on this phone</h2><ol><li>Open MO Everywhere and choose <strong>Use a provider on this phone</strong>.</li>
<li>In Provider settings, enter your chosen OpenAI-compatible HTTPS endpoint, model and API key. Enable image input only when your provider and model support it.</li>
<li>Save the configuration, then send a short typed message.</li></ol>
<p>Phone chat sends requests directly to its configured provider. MO's full tool and automation runtime runs on a separate computer or server. Your provider's rules and pricing apply.</p>
<h2>Pair with an existing Hub</h2><ol><li>Ask the operator of a Hub you are authorized to use to start its Android pairing flow: <code>/everywhere pair android</code>.</li>
<li>Choose the existing-user setup path in the app. Scan the QR code or use manual entry.</li>
<li>Review the Hub address and requested permissions, then confirm. Pairing codes expire and can be used once; request a fresh one if needed.</li></ol>
<p>Configure your own MO Hub or get access from its operator. Buying the app does not grant access to the publisher's private Hub.</p>
<h2>Remote access and folder sharing</h2><p>Control opens authorized Desktop and terminal sessions. Selected-folder sharing uses Android's folder picker and your user-enabled resident Cube. Other authorized devices can browse and read that folder through your Hub; remove the folder or stop the Cube to stop sharing. Android lock blocks file operations. File sharing does not enable phone automation or broad storage access.</p>
<h2>Research, images and files</h2><p>The Search Google bar in Chat opens your query in a browser. For research performed by the agent, ask in Hub chat to use the host's configured web, coding and file tools; those tools depend on your setup. Attach only images and files you intend to share, and choose an image-capable provider when needed. These host tools are separate from privileged automation of the Android phone.</p>
<h2>Conversations and profile protection</h2><p>Share a named conversation explicitly to continue it across authorized surfaces. Device pairing does not copy Hub provider keys or the entire private profile to Android. Curated profile replication between trusted computers uses a separate private SSH/Git setup, not automatic phone synchronization. Revoke a device at the Hub when its access is no longer needed.</p>
<h2>Voice, camera and the Cube</h2><p>Typed chat and manual pairing work without microphone or camera permission. Dictation produces an editable draft; <strong>Send</strong> submits it. Android's speech service may process audio remotely. The optional resident Cube has a persistent notification with a Stop action.</p>
<h2>If something is not working</h2><p>Check the selected chat mode first: phone chat and a paired Hub have separate settings. For a Hub, check that it is online and your access has not been revoked. For phone chat, check the selected provider, model, API key and usage balance. Do not send credentials to support.</p>
<h2>Report an AI response</h2><p>Use the report action on the assistant message, choose a reason and add an explanation. Including the response and preceding prompt is optional. Keep the receipt shown after submission if you may request deletion.</p>
<h2>Contact the publisher</h2><p>Email {contact}. Include the version from About and a short description. Do not include passwords, API keys, pairing codes or sensitive conversations.</p>'''

    privacy = f'''<p class="eyebrow">Privacy policy · effective {values['effective_date']}</p><h1>Your data and choices.</h1>
<p>MO Everywhere is independently published under the public operating name <strong>{values['publisher_name']}</strong>. The publisher controls this website, support correspondence and AI reports. Contact: {contact}.</p>
<h2>Chats and connected systems</h2><p>In Hub mode, messages, selected files, device status and supported continuity data go to the Hub you pair with. That Hub may send content to its configured AI provider. Its operator controls access, storage and deletion.</p>
<p>In This phone mode, prompts and optionally selected images go directly to the configured HTTPS provider. Image input must be enabled for your chosen compatible model. Your provider API key and visible transcript are stored locally using Android Keystore-backed encryption and excluded from Android backup. Phone-provider credentials are separate from Hub credentials.</p>
<p>The MO publisher does not receive those chats merely because you use the app. Your selected Hub, AI provider and speech service have their own privacy terms and retention practices.</p>
<h2>Access and synchronization</h2><p>Pairing establishes a scoped, revocable device grant protected on Android with Keystore-backed storage. Hub provider credentials remain on the Hub. Explicit shared conversations and bounded continuity data are distinct from the full private profile; that profile is not automatically synchronized to the phone. Private curated profile replication between trusted computers is a separate SSH/Git facility. HTTPS protects transport to the configured Hub or provider; those recipients can process the content you send.</p>
<h2>Optional permissions</h2><p>The camera decodes pairing QR codes locally while the scanner is open. Android's selected speech service may process microphone audio off-device. Recognized words remain an editable draft until you press Send. Files are sent only through the selected sharing or Hub file action. The resident Cube is optional.</p>
<h2>Remote sessions and shared folders</h2><p>Desktop frames, terminal output and your manual input pass through your Hub during an authorized remote session. Optional folder sharing lets authorized devices request the selected folder's names and supported text content through that Hub while the resident Cube is active and the phone is unlocked. Remove the folder or stop the Cube to end sharing. The publisher does not receive this traffic.</p>
<h2>Reports and human review</h2><p>If you submit an AI report, the publisher receives a report identifier, category, explanation, hash of the response, app version/build/distribution and language. The assistant response and preceding prompt are included only if you select the content checkbox. Reports contain no Hub authentication header. Do not put passwords or other people's sensitive information in a report.</p>
<p>Reports receive human review to investigate harmful output, fix defects and decide on corrective action. This service does not automatically send reports to another AI provider or use them to train a model. Report records, including any conversation content and review outcome, expire after 30 days and are erased by an hourly cleanup. You can request earlier deletion using your receipt.</p>
<h2>Website, hosting and email</h2><p>This website has no advertising, analytics, cookies, customer login or tracking scripts. The server necessarily receives network information to answer requests and limit abuse. Public-page/report access logging is disabled; per-address rate-limit counters remain in server memory and are not added to reports.</p>
<p>{values['hosting']}</p><p>{values['backups']}</p><p>{values['support_retention']}</p>
<p>Support email is handled by the publisher's email provider. Google Play processes purchases and updates under Google's terms. The Play in-app update library uses device, app-version and installed-module information to check for updates. MO includes no advertising or publisher analytics SDK.</p>
<h2>Purposes and your rights</h2><p>Publisher support and safety review serve the legitimate interests of answering requests, maintaining the service and preventing abuse. Submitting reports and optional conversation content is voluntary. We use the minimum information needed for those purposes and do not sell it.</p>
<p>You may request access, correction, deletion, restriction or object to publisher processing by emailing {contact}. Include your report receipt or enough information to identify your support request, without sending credentials. We respond without undue delay, normally within one month. Applicable rights depend on the processing involved. You can complain to your competent data-protection authority.</p>
<p>For records held by an independently operated Hub, AI provider or speech service, contact that operator. See <a href="/delete-data">data deletion instructions</a>. The app does not currently create a publisher customer account.</p>'''

    deletion = f'''<p class="eyebrow">Data deletion</p><h1>Choose what to remove.</h1>
<h2>Data on your phone</h2><p>In Provider settings, use <strong>Clear transcript</strong> to remove the phone-mode conversation or <strong>Remove provider</strong> to remove its saved provider configuration. Privacy controls also offer local Hub-data deletion and unpairing. Read the scope shown by each control.</p>
<p>Unpairing a Hub does not remove the separate phone-provider configuration or records already held by a Hub or provider. Uninstalling the app does not delete remote records.</p>
<h2>Publisher AI reports</h2><p>Email {contact} with the subject <strong>MO report deletion</strong>. Include the receipt and approximate submission date. You do not need to resend the conversation. After checking that the receipt identifies the correct record, the publisher erases the report and its review outcome. Reports otherwise expire after 30 days, with hourly cleanup.</p>
<h2>Support correspondence</h2><p>Email {contact} from the address used for your original request and identify the conversation you want deleted. We will verify the request and explain any record that must be retained for a legal obligation. We respond without undue delay, normally within one month.</p>
<h2>Hub and AI-provider records</h2><p>Ask the operator of the Hub or provider you used to delete its records. The MO publisher cannot access or erase a private Hub it does not operate.</p>
<h2>Accounts and purchases</h2><p>MO Everywhere does not currently create a publisher customer account. Google manages Play purchase records; removing app data is not a Google-account deletion or refund request. A future customer-account service will have its own deletion controls and updated privacy information.</p>
<p>See the <a href="/privacy">privacy policy</a> for hosting, backups and retention.</p>'''
    return {path: layout(path, title, body) for path, title, body in (
        ("/", "Home", home), ("/support", "Setup and support", support),
        ("/privacy", "Privacy policy", privacy), ("/delete-data", "Data deletion", deletion),
    )}
