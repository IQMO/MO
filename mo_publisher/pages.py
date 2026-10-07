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
header,main,footer{max-width:1120px;margin:auto;padding:1.3rem 1.5rem}header{display:flex;flex-wrap:wrap;align-items:center;gap:.8rem 1.6rem;border-bottom:1px solid var(--border)}
.identity{display:inline-flex;align-items:center;gap:.6rem;font-weight:700;text-decoration:none;color:var(--text);white-space:nowrap}nav{display:flex;flex-wrap:wrap;gap:1.2rem}nav a[aria-current=page]{color:var(--text)}
.mark{display:inline-grid;grid-template-columns:repeat(2,8px);gap:4px}.mark i{width:8px;height:8px;border-radius:2px;background:var(--brand)}
.gh{margin-left:auto;display:inline-flex;align-items:center;gap:.5rem;border:1px solid var(--border);border-radius:8px;padding:.45rem .85rem;color:var(--text);text-decoration:none;background:var(--surface)}.gh:hover{border-color:var(--brand);color:var(--text)}.gh svg{width:17px;height:17px}
main{min-height:65vh;padding-top:3rem;padding-bottom:4rem}h1{font-size:clamp(2rem,5vw,3.5rem);line-height:1.13;font-weight:600;letter-spacing:-.035em;margin:.2rem 0 1.5rem;max-width:18ch}
h2{font-size:1.3rem;line-height:1.4;margin-top:2.5rem}p,li{max-width:76ch}p{margin:1rem 0}.eyebrow{color:var(--brand);font-size:.85rem;letter-spacing:.12em;text-transform:uppercase}
.lead{font-size:1.2rem;max-width:58ch}.note{border-left:2px solid var(--brand);padding:.3rem 1.3rem;background:var(--input);margin:2rem 0}
.links{display:flex;flex-wrap:wrap;gap:1rem;margin:2rem 0}.links a{border:1px solid var(--border);padding:.6rem 1rem;border-radius:8px;text-decoration:none}
footer{border-top:1px solid var(--border);color:var(--muted);font-size:.9rem}footer p{margin:.4rem 0}code{overflow-wrap:anywhere;background:var(--input);padding:.1em .3em;border-radius:3px}
.landing{display:flex;flex-direction:column;gap:5.5rem}.landing h2{font-size:clamp(1.6rem,3.2vw,2.2rem);line-height:1.2;margin:.4rem 0 1.6rem}
.pick{position:absolute;width:1px;height:1px;opacity:0;overflow:hidden;clip-path:inset(50%)}
.hero{display:grid;grid-template-columns:minmax(0,.9fr) minmax(0,1.1fr);gap:2.8rem;align-items:center}.hero h1{max-width:none;font-size:clamp(2rem,4.4vw,3.2rem);margin:.6rem 0 1.2rem}
.kicker{display:flex;align-items:center;gap:.9rem;margin:0}.kicker .eyebrow{margin:0}
.cubes{--e:18px;position:relative;width:calc(var(--e)*2.4545);height:calc(var(--e)*2.4545);flex:none}
.cubes i{position:absolute;width:var(--e);height:var(--e);border-radius:calc(var(--e)*.22);background:var(--brand);animation:beat 2.4s ease-in-out infinite;--dx:-1;--dy:-1}
.cubes i:nth-child(1){left:0;top:0}.cubes i:nth-child(2){right:0;top:0;--dx:1}.cubes i:nth-child(3){left:0;bottom:0;--dy:1}.cubes i:nth-child(4){right:0;bottom:0;--dx:1;--dy:1}
@keyframes beat{0%,46%,100%{transform:none}8%{transform:translate(calc(var(--dx)*var(--e)*.07),calc(var(--dy)*var(--e)*.07))}16%{transform:none}24%{transform:translate(calc(var(--dx)*var(--e)*.045),calc(var(--dy)*var(--e)*.045))}34%{transform:none}}
.actions{display:flex;flex-wrap:wrap;gap:.8rem;margin-top:1.8rem}.btn{display:inline-block;border:1px solid var(--border);border-radius:8px;padding:.65rem 1.1rem;color:var(--text);text-decoration:none;font-weight:600;background:var(--surface)}
.btn:hover{border-color:var(--brand);color:var(--text)}.btn.primary{background:var(--brand);border-color:var(--brand);color:var(--background)}.btn.primary:hover{color:var(--background)}
.switch-label{margin:0 0 .6rem;color:var(--muted);font:.85rem ui-monospace,"Cascadia Mono",Consolas,monospace}
.providers{display:flex;flex-wrap:wrap;gap:.4rem;margin-bottom:.9rem}.providers label{cursor:pointer;font:.85rem ui-monospace,"Cascadia Mono",Consolas,monospace;color:var(--muted);background:var(--input);border:1px solid var(--border);border-radius:6px;padding:.3rem .65rem}
#m-codex:checked~.hero label[for=m-codex],#m-deepseek:checked~.hero label[for=m-deepseek],#m-zai:checked~.hero label[for=m-zai],#m-ollama:checked~.hero label[for=m-ollama]{color:var(--brand);border-color:var(--brand)}
#m-codex:focus-visible~.hero label[for=m-codex],#m-deepseek:focus-visible~.hero label[for=m-deepseek],#m-zai:focus-visible~.hero label[for=m-zai],#m-ollama:focus-visible~.hero label[for=m-ollama]{outline:2px solid var(--glow);outline-offset:3px}
.win{background:var(--background);border:1px solid var(--border);border-radius:10px;overflow:hidden;min-width:0}
.titlebar{display:flex;align-items:stretch;min-height:2.3rem;background:var(--surface);border-bottom:1px solid var(--border);font:.78rem ui-monospace,"Cascadia Mono",Consolas,monospace}
.tab{display:flex;align-items:center;gap:.5rem;padding:0 .9rem;color:var(--text);background:var(--background);border-right:1px solid var(--border);white-space:nowrap;cursor:default}.tab .mark{grid-template-columns:repeat(2,5px);gap:2px}.tab .mark i{width:5px;height:5px;border-radius:1px}
.ctl{margin-left:auto;display:flex;color:var(--muted)}.ctl span{width:2.4rem;display:grid;place-items:center}
.screen{padding:.9rem 1rem 0;font:13.5px/1.2 ui-monospace,"Cascadia Mono",Consolas,monospace;color:var(--text);overflow-x:auto}.row{white-space:pre;min-height:1.2em;padding:1px 0}
.screen .dim{color:var(--muted)}.screen .cmd{color:var(--brand)}.screen .head{color:var(--brand);font-weight:700}.screen .chip{color:var(--glow);font-weight:700}.screen .ok{color:var(--ok)}.screen .mk{color:var(--brand);font-weight:700}
.screen .you{display:inline-block;padding-right:1ch;background:color-mix(in srgb,var(--brand) 14%,var(--background))}
.startup{display:grid;grid-template-columns:13ch minmax(0,1fr);align-items:start}.startup .gap{visibility:hidden}
.logo{--r:calc(1.2em + 2px);position:relative;display:inline-block;width:9ch;height:calc(5*var(--r));margin-left:2ch}.logo b{position:absolute;width:4ch;height:calc(2*var(--r));background:var(--brand)}
.logo b:nth-child(1){left:0;top:0}.logo b:nth-child(2){left:5ch;top:0}.logo b:nth-child(3){left:0;top:calc(2.5*var(--r))}.logo b:nth-child(4){left:5ch;top:calc(2.5*var(--r))}
.inputline{margin-top:.4rem;border-top:1px solid var(--border);border-bottom:1px solid var(--border)}.inputline .ph{color:var(--muted);font-style:italic}
.statusline{padding:.4rem 1rem .6rem;font:12.5px ui-monospace,"Cascadia Mono",Consolas,monospace;color:var(--muted);white-space:nowrap;overflow-x:auto}
.hero .when-deepseek,.hero .when-zai,.hero .when-ollama{display:none}
#m-deepseek:checked~.hero .when-codex,#m-zai:checked~.hero .when-codex,#m-ollama:checked~.hero .when-codex{display:none}
#m-deepseek:checked~.hero span.when-deepseek,#m-zai:checked~.hero span.when-zai,#m-ollama:checked~.hero span.when-ollama{display:inline}
#m-deepseek:checked~.hero div.when-deepseek,#m-zai:checked~.hero div.when-zai,#m-ollama:checked~.hero div.when-ollama{display:block;animation:typein .25s ease-out}
#m-deepseek:checked~.hero p.when-deepseek,#m-zai:checked~.hero p.when-zai,#m-ollama:checked~.hero p.when-ollama{display:flex}
@keyframes typein{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
.kept{display:flex;flex-wrap:wrap;align-items:center;gap:.4rem .5rem;margin:.9rem 0 0;font:.8rem ui-monospace,"Cascadia Mono",Consolas,monospace;color:var(--muted)}.kept .k{border:1px solid var(--border);border-radius:999px;padding:.1rem .6rem}
.kept.flash .k{animation:kept 1.6s ease-out both}.kept.flash .k:nth-of-type(2){animation-delay:.11s}.kept.flash .k:nth-of-type(3){animation-delay:.22s}.kept.flash .k:nth-of-type(4){animation-delay:.33s}.kept.flash .k:nth-of-type(5){animation-delay:.44s}
@keyframes kept{0%,100%{color:var(--muted);border-color:var(--border)}20%,70%{color:var(--ok);border-color:var(--ok)}}
.thesis{border-left:3px solid var(--brand);padding:.3rem 0 .3rem 1.6rem}.thesis h2{margin:0}.thesis p{color:var(--muted);font-size:1.12rem}
.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;background:var(--border);border:1px solid var(--border);border-radius:12px;overflow:hidden}
.grid2>div{background:var(--surface);padding:1.2rem 1.35rem;min-width:0}.grid2 h3,.card h3{font-size:1.05rem;margin:0 0 .35rem}.grid2 p,.card p{margin:0;color:var(--muted);font-size:.95rem}
.wont{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.9rem 1.8rem}.wont li{max-width:none;padding-left:1.6rem;position:relative}
.wont li::before{content:"\\2715";position:absolute;left:0;color:var(--muted);font-size:.8rem;top:.2rem}.wont b{display:block}.wont span{color:var(--muted);font-size:.95rem}
.pillars{list-style:none;padding:0;margin:0;border-top:1px solid var(--border)}.pillars li{max-width:none;display:grid;grid-template-columns:3.2rem minmax(0,1fr) minmax(0,1.4fr);gap:1.1rem;padding:.9rem 0;border-bottom:1px solid var(--border);align-items:baseline}
.pillars .id{color:var(--brand);font:.82rem ui-monospace,"Cascadia Mono",Consolas,monospace}.pillars h3{font-size:1.05rem;margin:0}.pillars p{margin:0;color:var(--muted);font-size:.95rem}
.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.9rem}.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:1.1rem 1.25rem;min-width:0}
.tag{display:inline-block;margin-left:.4rem;font:.68rem ui-monospace,"Cascadia Mono",Consolas,monospace;letter-spacing:.06em;text-transform:uppercase;padding:.1rem .5rem;border-radius:999px;border:1px solid var(--border);color:var(--muted);vertical-align:.15em}
.tag.main{color:var(--brand);border-color:var(--brand)}.tag.test{color:var(--warn);border-color:var(--warn)}
.android h2{margin-top:0}.android h3{font-size:1.15rem;margin:2rem 0 .4rem}
.film .screen{display:grid;grid-template-columns:auto minmax(0,1fr);gap:1.4rem;align-items:center;padding:1.6rem 1.4rem;white-space:normal;font:inherit}.film h2{margin:0 0 .3rem;font-size:1.35rem}.film p{margin:0;color:var(--muted)}
.install .titlebar label{cursor:pointer;background:none;color:var(--muted)}.pane-nix{display:none}
#os-win:checked~.install label[for=os-win],#os-nix:checked~.install label[for=os-nix]{background:var(--background);color:var(--text)}
#os-win:focus-visible~.install label[for=os-win],#os-nix:focus-visible~.install label[for=os-nix]{outline:2px solid var(--glow);outline-offset:-3px}
#os-nix:checked~.install .pane-nix{display:block}#os-nix:checked~.install .pane-win{display:none}
.install .screen{padding:1rem 1rem .9rem}.install .ps{color:var(--muted);user-select:none}.after{padding:.9rem 1rem;border-top:1px solid var(--border);color:var(--muted)}
.footlinks{display:flex;flex-wrap:wrap;gap:.4rem 1.1rem}
@media(max-width:900px){.hero,.grid2,.wont{grid-template-columns:minmax(0,1fr)}.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.pillars li{grid-template-columns:2.6rem minmax(0,1fr)}.pillars li p{grid-column:2}}
@media(max-width:560px){.cards{grid-template-columns:minmax(0,1fr)}.film .screen{grid-template-columns:minmax(0,1fr)}.screen{font-size:12px}.landing{gap:4rem}}
@media(prefers-reduced-motion:reduce){.cubes i,.kept.flash .k{animation:none}#m-deepseek:checked~.hero div.when-deepseek,#m-zai:checked~.hero div.when-zai,#m-ollama:checked~.hero div.when-ollama{animation:none}}
@media(max-width:650px){header{align-items:flex-start}header,main,footer{padding-left:1.2rem;padding-right:1.2rem}main{padding-top:2rem}nav{gap:.8rem;font-size:.94rem}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
"""


GITHUB_LINK = '<a class="gh" href="https://github.com/IQMO/MO" aria-label="MO on GitHub"><svg viewBox="0 0 16 16" aria-hidden="true" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>GitHub</a>'

# The MO Agent home page (his option B): the hero is an example MO Terminal session in which switching the model
# changes only the route. Script-free, so the switch and the install tabs are CSS radio inputs.
LANDING = r'''<div class="landing">
<input class="pick" type="radio" name="model" id="m-codex" checked aria-label="OpenAI Codex">
<input class="pick" type="radio" name="model" id="m-deepseek" aria-label="DeepSeek">
<input class="pick" type="radio" name="model" id="m-zai" aria-label="Z.ai">
<input class="pick" type="radio" name="model" id="m-ollama" aria-label="Ollama, offline">
<section class="hero" aria-labelledby="hero-h">
<div>
<p class="kicker"><span class="cubes" aria-hidden="true"><i></i><i></i><i></i><i></i></span><span class="eyebrow">MO Agent · open source · MIT</span></p>
<h1 id="hero-h">Your model can change. Your work stays.</h1>
<p class="lead">One local agent runtime. Honest progress. Reachable everywhere. MO keeps the tools, the safety, your memory and the proof, whichever model is thinking.</p>
<div class="actions"><a class="btn primary" href="#install">Install MO</a><a class="btn" href="#different">Why MO</a></div>
</div>
<div>
<p class="switch-label">Switch the model. Nothing else changes.</p>
<div class="providers"><label for="m-codex">OpenAI · Codex</label><label for="m-deepseek">DeepSeek</label><label for="m-zai">Z.ai</label><label for="m-ollama">Ollama · offline</label></div>
<div class="win" role="img" aria-label="An example MO Terminal session: a bug fixed with test proof, then the model switched while the session, tools and proof stay">
<div class="titlebar"><span class="tab"><span class="mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span>MO · example session</span><span class="ctl" aria-hidden="true"><span>─</span><span>☐</span><span>✕</span></span></div>
<div class="screen">
<div class="startup"><span class="logo" aria-hidden="true"><b></b><b></b><b></b><b></b></span><div>
<div class="row"><span class="head">MO v1.0</span></div>
<div class="row dim">No work running</div>
<div class="row gap">-</div>
<div class="row"><span class="cmd">/help</span><span class="dim">  ·  </span><span class="cmd">/status</span><span class="dim">  ·  </span><span class="cmd">/dashboard</span><span class="dim">  ·  </span><span class="cmd">Ctrl+B</span><span class="dim"> workspace</span></div>
</div></div>
<div class="row"><span class="dim">  Folders   </span>shop, website<span class="dim">  /projects</span></div>
<div class="row"><span class="dim">  MO host   </span>Online · synced just now<span class="dim">  /everywhere status</span></div>
<div class="row"> </div>
<div class="row"><span class="you">❯ the checkout total is a cent off, fix it</span></div>
<div class="row"><span class="dim">│ ▸ </span><span class="chip">[read_file]</span> checkout.py</div>
<div class="row"><span class="dim">│ ▸ </span><span class="chip">[edit_file]</span> checkout.py<span class="dim">  +3 -1</span></div>
<div class="row"><span class="dim">│ ▸ </span><span class="chip">[test_runner]</span> <span class="ok">48 passed</span></div>
<div class="row dim">     3 tasks complete · 38s · +3 -1</div>
<div class="row"><span class="mk">› </span>Fixed: totals now round once, at the end. 48 tests pass.</div>
<div class="when-deepseek"><div class="row"><span class="you">❯ /model deepseek</span></div><div class="row dim">Switched to model: deepseek / deepseek-chat · thinking high</div></div>
<div class="when-zai"><div class="row"><span class="you">❯ /model z.ai</span></div><div class="row dim">Switched to model: z.ai / glm-4.6 · thinking high</div></div>
<div class="when-ollama"><div class="row"><span class="you">❯ /model ollama</span></div><div class="row dim">Switched to model: ollama / qwen3 · thinking default</div><div class="row"><span class="mk">› </span>Running locally now. Same session, same tools, same proof.</div></div>
<div class="row inputline"> <span class="cmd">❯</span> <span class="ph">Type a message</span></div>
</div>
<div class="statusline">~/projects/shop · <span class="when-codex">openai-codex / gpt-6.1-sol · xhigh</span><span class="when-deepseek">deepseek / deepseek-chat · high</span><span class="when-zai">z.ai / glm-4.6 · high</span><span class="when-ollama">ollama / qwen3 · offline</span> · ↑12.6k ↓136</div>
</div>
<p class="kept when-codex"><span>Kept on every switch:</span><span class="k">every tool</span><span class="k">the safety gate</span><span class="k">your memory</span><span class="k">this session</span><span class="k">the proof</span></p>
<p class="kept flash when-deepseek"><span>Kept:</span><span class="k">every tool</span><span class="k">the safety gate</span><span class="k">your memory</span><span class="k">this session</span><span class="k">the proof</span></p>
<p class="kept flash when-zai"><span>Kept:</span><span class="k">every tool</span><span class="k">the safety gate</span><span class="k">your memory</span><span class="k">this session</span><span class="k">the proof</span></p>
<p class="kept flash when-ollama"><span>Kept:</span><span class="k">every tool</span><span class="k">the safety gate</span><span class="k">your memory</span><span class="k">this session</span><span class="k">the proof</span></p>
</div>
</section>

<section id="thesis" aria-labelledby="thesis-h"><div class="thesis"><h2 id="thesis-h">The model proposes. MO proves.</h2>
<p>The runtime, not the model, decides what counts as done. A task closes only on tool evidence, and the final answer is checked for claims nobody verified. Switch providers, close the terminal, pick it up on another device: the work and its proof come with you.</p></div></section>

<section id="different" aria-labelledby="different-h"><p class="eyebrow">Why MO</p><h2 id="different-h">What makes MO different</h2>
<div class="grid2">
<div><h3>A runtime, not a model</h3><p>Use any provider, or a local model fully offline; switching providers keeps the same tools, safety, memory and proof.</p></div>
<div><h3>Done means proven</h3><p>Tasks close only on tool evidence, and final-answer gates flag unverified claims.</p></div>
<div><h3>Your work carries on</h3><p>Conversations are saved and resumable, interrupted work is kept honestly, several terminals coexist, and each surface knows where the work stands.</p></div>
<div><h3>One runtime, every surface</h3><p>Terminal, Desktop, Android, Telegram and a headless service reach the same private runtime.</p></div>
<div><h3>Your phone reaches your machines</h3><p>Chat, start and follow work, open a terminal on your Hub, drive a running terminal or Desktop, move files. The computers you reach connect out to your Hub.</p></div>
<div><h3>Private, and it learns you openly</h3><p>Everything lives in your private MO home; credentials never enter the model's context; learning is reviewable.</p></div>
<div><h3>Deep project work</h3><p>A structural code graph, project mapping and rules, goals with an auditor, background workers and a review team that hands confirmed findings back for fixing.</p></div>
<div><h3>It uses your computer with you</h3><p>Actions on real windows, tabs and controls, each returning its own proof; risky actions ask once.</p></div>
</div></section>

<section id="wont" aria-labelledby="wont-h"><p class="eyebrow">By design</p><h2 id="wont-h">What MO won't do</h2>
<ul class="wont">
<li><b>Call work done without evidence</b><span>Unverified results are said plainly.</span></li>
<li><b>Put your credentials in the model's context</b><span>Secrets stay in your private MO home; answers are scanned and redacted.</span></li>
<li><b>Tie you to one model</b><span>Change provider and keep everything else.</span></li>
<li><b>Pass a cloud route off as local</b><span>Offline means offline.</span></li>
<li><b>Send your Hub's keys to your phone</b><span>The phone talks to your Hub and runs no second agent.</span></li>
<li><b>Ask the computers you reach to open a port</b><span>They connect out to your Hub.</span></li>
</ul></section>

<section id="pillars" aria-labelledby="pillars-h"><p class="eyebrow">Everything it does</p><h2 id="pillars-h">Ten pillars, one runtime</h2>
<ol class="pillars">
<li><span class="id">P1</span><h3>A runtime, not a model</h3><p>Any provider or a local model; the runtime owns tools, context, discovery and attachments.</p></li>
<li><span class="id">P2</span><h3>Done means proven</h3><p>Task truth, verification, final gates, goals and reviews run on evidence.</p></li>
<li><span class="id">P3</span><h3>Your work carries on</h3><p>Saved, resumable, honest sessions; portable conversations; continuity across surfaces and terminals.</p></li>
<li><span class="id">P4</span><h3>Reachable everywhere</h3><p>Your own Hub, the Android app, remote terminals, Live Control, files and transfers, Telegram and a headless service.</p></li>
<li><span class="id">P5</span><h3>Private, and it learns you</h3><p>Profile, terms, memory, reviewable learning, skills and moving in from other agents.</p></li>
<li><span class="id">P6</span><h3>Safe by design</h3><p>One sandbox for every tool call, exact confirmations, secrets kept out, untrusted content fenced.</p></li>
<li><span class="id">P7</span><h3>The engineering workbench</h3><p>The Terminal: workspace and panes, code graph and maps, roles and workers.</p></li>
<li><span class="id">P8</span><h3>It uses your computer with you</h3><p>Computer use, MO Desktop and its apps, voice, MO Shell and PC care.</p></li>
<li><span class="id">P9</span><h3>Life and making things</h3><p>Mail, Life records and money, schedules, MO Design, visuals, images and explainer videos.</p></li>
<li><span class="id">P10</span><h3>Setup and operations</h3><p>Install, health, credentials, updates, settings and optional public pages.</p></li>
</ol></section>

<section id="surfaces" aria-labelledby="surfaces-h"><p class="eyebrow">Where it runs</p><h2 id="surfaces-h">One runtime, every surface</h2>
<div class="cards">
<div class="card"><h3>MO Terminal <span class="tag main">main workbench</span></h3><p>Coding, research, files, goals, reviews and automation in your real projects.</p></div>
<div class="card"><h3>MO Desktop <span class="tag">optional · Windows</span></h3><p>A resident assistant: Dashboard, design, screen help, voice and files.</p></div>
<div class="card"><h3>MO Shell <span class="tag">optional · Windows</span></h3><p>Keeps the terminal beside the app you are working in.</p></div>
<div class="card"><h3>MO Everywhere <span class="tag test">closed test</span></h3><p>The Android app reaches your own Hub. <a href="#android">More about the app</a>.</p></div>
<div class="card"><h3>Telegram</h3><p>Conversation and approvals through your own bot.</p></div>
<div class="card"><h3>Headless service</h3><p>Keeps the Hub, scheduler and integrations running on your computer or server.</p></div>
</div></section>

'''
LANDING_END = r'''

<section id="film" aria-label="See MO work"><div class="win film">
<div class="titlebar"><span class="tab"><span class="mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span>MO · film</span><span class="ctl" aria-hidden="true"><span>─</span><span>☐</span><span>✕</span></span></div>
<div class="screen"><span class="cubes" aria-hidden="true"><i></i><i></i><i></i><i></i></span><div><h2>See it work</h2><p>The film is being made from real MO runs, so it shows only what works today. It goes here when it is ready. Until then, the <a href="https://github.com/IQMO/MO#everything-mo-does">README lists every feature</a>.</p></div></div>
</div></section>

<section id="install" aria-labelledby="install-h"><p class="eyebrow">Install</p><h2 id="install-h">Install MO</h2>
<p class="lead">You need Git, Python 3.10 or newer, and one AI provider or a local model server. The base install has five direct dependencies.</p>
<input class="pick" type="radio" name="os" id="os-win" checked aria-label="Windows PowerShell">
<input class="pick" type="radio" name="os" id="os-nix" aria-label="Linux or macOS">
<div class="win install">
<div class="titlebar"><label class="tab" for="os-win">PowerShell</label><label class="tab" for="os-nix">bash · Linux or macOS</label><span class="ctl" aria-hidden="true"><span>─</span><span>☐</span><span>✕</span></span></div>
<div class="screen pane-win"><div class="row"><span class="ps">PS&gt; </span>git clone https://github.com/IQMO/MO.git</div><div class="row"><span class="ps">PS&gt; </span>cd MO</div><div class="row"><span class="ps">PS&gt; </span>python -m venv .venv</div><div class="row"><span class="ps">PS&gt; </span>.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools</div><div class="row"><span class="ps">PS&gt; </span>.\.venv\Scripts\python.exe -m pip install -r requirements.txt</div><div class="row"><span class="ps">PS&gt; </span>.\.venv\Scripts\python.exe mo.py --init</div></div>
<div class="screen pane-nix"><div class="row"><span class="ps">$ </span>git clone https://github.com/IQMO/MO.git</div><div class="row"><span class="ps">$ </span>cd MO</div><div class="row"><span class="ps">$ </span>python3 -m venv .venv</div><div class="row"><span class="ps">$ </span>.venv/bin/python -m pip install --upgrade pip setuptools</div><div class="row"><span class="ps">$ </span>.venv/bin/python -m pip install -r requirements.txt</div><div class="row"><span class="ps">$ </span>.venv/bin/python mo.py --init</div></div>
<div class="after">Then add your provider and run <code>mo</code> in your project. The <a href="https://github.com/IQMO/MO#quickstart">Quickstart</a> walks through both, and the <a href="https://github.com/IQMO/MO/blob/main/FAQ.md">FAQ</a> explains what runs where.</div>
</div></section>
</div>'''


def pages(settings: dict[str, str]) -> dict[str, str]:
    values = {key: escape(value, quote=True) for key, value in settings.items()}
    email = values["support_email"]
    contact = f'<a href="mailto:{email}">{email}</a>'

    def layout(path, title, body):
        links = (("/", "Home"), ("/support", "Support"), ("/privacy", "Privacy"), ("/delete-data", "Delete data"))
        nav = "".join(f'<a href="{url}"' + (' aria-current="page"' if path == url else "") + f'>{label}</a>' for url, label in links)
        # The home page introduces MO Agent; the other three serve the Android app (Google Play links them).
        product, description = (
            ("MO Agent", "MO Agent: one local agent runtime around the AI model you choose. Honest progress. Reachable everywhere.")
            if path == "/" else ("MO Everywhere", "MO Everywhere for Android. Setup, support, privacy and data deletion."))
        return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="{description}">
<title>{escape(title)} · {product}</title><link rel="stylesheet" href="/style.css"></head>
<body><a class="skip" href="#main">Skip to content</a><header><a class="identity" href="/"><span class="mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span>MO Agent</a><nav aria-label="Main">{nav}</nav>{GITHUB_LINK}</header>
<main id="main">{body}</main><footer><p>MO Agent · MO Everywhere · {values['publisher_name']}</p><p>Support and privacy: {contact}</p><p class="footlinks"><a href="https://github.com/IQMO/MO">GitHub</a><a href="https://github.com/IQMO/MO/blob/main/FAQ.md">FAQ</a><a href="https://github.com/IQMO/MO/blob/main/CAPABILITIES.md">Capabilities</a><a href="https://github.com/IQMO/MO/blob/main/LICENSE">MIT license</a></p></footer></body></html>'''

    android = '''<p class="eyebrow">The Android companion to MO Agent</p><h2 id="android-h">Your agent works.<br>You keep moving.</h3>
<p class="lead">Stay connected to MO Agent on your own computer or server. Continue a conversation, follow work and reach your projects from your phone, wherever your Hub is reachable.</p>
<div class="note"><p><strong>Closed testing.</strong> Version 0.1.62 is available to selected Google Play testers. Public production access is pending. Hub, Remote and file features require your own configured systems and reviewed device access.</p></div>
<div class="links"><a href="https://play.google.com/apps/testing/app.moagent.mobile">Closed-test access on Google Play</a><a href="/support">Set up your connection</a><a href="/privacy">Your data and choices</a></div>
<h3>The working agent stays on your machine</h3><p>MO Agent is a local-first AI coding and working agent. Through your paired Hub, ask it to work on a project, research a question or organize files using the tools and providers you configured. The host carries out the work under its existing access and approval rules.</p>
<h3>Leave the desk. Stay involved.</h3><p>Continue a conversation you choose to share across your connected devices. Check Dashboard, start background tasks from your phone and follow their progress, or schedule a timed follow-up in Control &rarr; Work. Keep your Hub running and your phone connected to receive completion and problem notifications.</p>
<h3>Start on your phone</h3><p>Use your own HTTPS OpenAI-compatible provider, model and API key for separate phone chat. Enable image input only when your selected provider and model support it. Phone chat does not run the full MO Agent tool runtime.</p>
<h3>Reach the tools around your work</h3><p>Browse authorized file sources and exchange documents. Open compatible Desktop hosts and terminal workspaces with touch, cursor and keyboard controls. You can also select one phone folder for read-only access by authorized Hub devices while sharing is enabled and the phone is unlocked.</p>
<h3>Pair deliberately. Stay in control.</h3><p>Review the Hub and device permissions before pairing. Access is scoped and revocable. Share conversations and selected files explicitly. Hub provider keys stay on the Hub; your private profile is not automatically copied to Android. The optional floating Cube keeps MO close. Microphone and camera access are optional, and you can report a problem with an AI response from the app.</p>
<p>Google Play is the public app installation and update source. The Play app does not provide privileged Android phone automation.</p>
<p>The app does not include a hosted Hub, an AI subscription or unlimited AI usage. Your provider may charge separately. Hub features require MO Agent on a computer or server you are authorized to use. Follow the <a href="https://github.com/IQMO/MO#quickstart">MO Agent installation guide</a>, then configure your <a href="https://github.com/IQMO/MO/blob/main/mo_everywhere/README.md">MO Hub</a>.</p>'''

    home = LANDING + '<section class="android" id="android" aria-labelledby="android-h">' + android + '</section>' + LANDING_END

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
        ("/", "One local agent runtime", home), ("/support", "Setup and support", support),
        ("/privacy", "Privacy policy", privacy), ("/delete-data", "Data deletion", deletion),
    )}
