// cdimport's page: what the flow in app.py shows, and its open question.
// Everything comes from /api/state (cdimport/web.py); answers go back to
// /api/answer. The flow itself is the terminal's, step for step.
import { html, render, useState, useEffect, useRef } from '/static/vendor/preact-htm.js';

async function api(path, body) {
  const options = {};
  if (body !== undefined) {
    options.method = 'POST';
    options.headers = { 'Content-Type': 'application/json' };
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch('/api' + path, options);
  } catch (err) {
    throw new Error('cdimport is not responding. Is it still running?');
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
  return data;
}

const STEPS = [[0, 'Tools'], [1, 'Disc'], [2, 'Rip'], [3, 'Tags'], [4, 'Lyrics'], [5, 'Finish']];
// The same links ui.py underlines: never ending in sentence punctuation.
const URL_PATTERN = /(?:https?):\/\/[^\s)\]]*[^\s)\].,;:!?'"]/g;

// Text with its web links clickable; never markup from the text itself.
function Linked({ text }) {
  const parts = [];
  let last = 0;
  for (const match of String(text).matchAll(URL_PATTERN)) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    parts.push(html`<a href=${match[0]} target="_blank" rel="noopener noreferrer">${match[0]}</a>`);
    last = match.index + match[0].length;
  }
  parts.push(String(text).slice(last));
  return parts;
}

const MARKS = { success: '✓', warn: '!', error: '✗' };
const CHECK_MARKS = { true: ['✓', 'k-success'], false: ['!', 'k-warn'], null: ['–', 'k-dim'] };

function Item({ event }) {
  switch (event.kind) {
    case 'info':
      return html`<p class="line"><${Linked} text=${event.text} /></p>`;
    case 'success': case 'warn': case 'error':
      return html`<p class=${'line k-' + event.kind}><span class="mark">${MARKS[event.kind]}</span><span><${Linked} text=${event.text} /></span></p>`;
    case 'dim':
      return html`<p class="line k-dim"><${Linked} text=${event.text} /></p>`;
    case 'field':
      return html`<div class="field"><span class="name">${event.label}</span><span><${Linked} text=${event.text} /></span></div>`;
    case 'check': {
      const [mark, cls] = CHECK_MARKS[String(event.ok)];
      return html`<div class="check"><span class=${'mark ' + cls}>${mark}</span><span class="name">${event.label}</span>
        <span><${Linked} text=${event.text} /></span>${event.note && html`<span class="note"><${Linked} text=${event.note} /></span>`}</div>`;
    }
    case 'tool_start':
      return html`<${Tool} event=${event} />`;
    case 'answer':
      return html`<p class="answer"><span class="q">${event.prompt}</span>${event.text}</p>`;
    default:
      return null;
  }
}

function Tool({ event }) {
  const body = useRef(null);
  const [stopping, setStopping] = useState(false);
  useEffect(() => {
    // Follow the output while it runs, as a terminal does.
    if (event.running && body.current) body.current.scrollTop = body.current.scrollHeight;
  });
  const stop = async () => {
    if (!confirm(`Stop ${event.name}? It finishes its log first, as on Ctrl+C; cdimport then asks what to do.`)) return;
    setStopping(true);
    await api('/stop-tool', {}).catch(() => {});
  };
  const status = event.running
    ? html`<span class="row"><span class="spinner" aria-hidden="true"></span> running</span>`
    : event.code === 0 ? html`<span class="status-ok">done</span>`
      : html`<span class="status-bad">${event.code === null ? 'failed' : `exit code ${event.code}`}</span>`;
  return html`<div class="tool">
    <div class="tool-head">
      <b>${event.name}</b><span class="cmd mono">${event.text}</span>${status}
      ${event.running && html`<button class="btn btn-small" disabled=${stopping} onClick=${stop}>${stopping ? 'Stopping…' : `Stop ${event.name}`}</button>`}
    </div>
    ${(event.lines.length > 0 || event.partial) && html`<div class="tool-body mono" ref=${body}>${event.lines.join('\n')}${event.partial
      ? html`${event.lines.length ? '\n' : ''}<span class="partial">${event.partial}</span>` : ''}</div>`}
  </div>`;
}

function Question({ question, onAnswer }) {
  const [text, setText] = useState(question.kind === 'ask_path' ? question.prefill || '' : '');
  const [sending, setSending] = useState(false);
  const answer = async (value) => {
    if (sending) return;
    setSending(true);
    try { await onAnswer(question.id, value); } finally { setSending(false); }
  };

  // The terminal's keys: an option's key, y/n, Enter for the default.
  const keys = useRef(null);
  keys.current = (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const typing = ['INPUT', 'TEXTAREA'].includes(e.target.tagName);
    if (question.kind === 'choose' && !typing) {
      const hit = question.options.find(([key]) => key === e.key.toLowerCase());
      if (hit) { e.preventDefault(); answer(hit[0]); return; }
      if (e.key === 'Enter' && question.default) { e.preventDefault(); answer(question.default); }
    } else if (question.kind === 'confirm' && !typing) {
      if (e.key === 'y') answer(true);
      else if (e.key === 'n') answer(false);
      else if (e.key === 'Enter') { e.preventDefault(); answer(question.default); }
    } else if (question.kind === 'wait' && e.key === 'Enter' && !typing) {
      e.preventDefault();
      answer(null);
    }
  };
  useEffect(() => {
    const handler = (e) => keys.current(e);
    addEventListener('keydown', handler);
    return () => removeEventListener('keydown', handler);
  }, []);

  let body;
  if (question.kind === 'choose') {
    body = html`<div class="options">${question.options.map(([key, description]) => {
      const [first, ...more] = description.split('\n');
      return html`<button class=${'btn option' + (key === question.default ? ' default' : '')} disabled=${sending}
        onClick=${() => answer(key)}>
        <span class="key">${key}</span>
        <span class="text"><span><${Linked} text=${first} /></span>${more.map((m) => html`<span class="more"><${Linked} text=${m.trim()} /></span>`)}</span>
      </button>`;
    })}</div>`;
  } else if (question.kind === 'confirm') {
    body = html`<div class="row">
      <button class=${'btn' + (question.default ? ' btn-primary' : '')} disabled=${sending} onClick=${() => answer(true)}>Yes</button>
      <button class=${'btn' + (!question.default ? ' btn-primary' : '')} disabled=${sending} onClick=${() => answer(false)}>No</button>
    </div>`;
  } else if (question.kind === 'wait') {
    body = html`<div class="row"><button class="btn btn-primary" disabled=${sending} onClick=${() => answer(null)}>Done</button></div>`;
  } else {
    body = html`<form class="row" onSubmit=${(e) => { e.preventDefault(); answer(text); }}>
      <label class="sr" for="answer" style=${{ position: 'absolute', left: '-9999px' }}>${question.prompt}</label>
      <input id="answer" type="text" class=${question.kind === 'ask_path' ? 'mono' : ''} value=${text} autofocus
        placeholder=${question.default || ''} onInput=${(e) => setText(e.target.value)} />
      <button class="btn btn-primary" type="submit" disabled=${sending}>OK</button>
    </form>
    ${question.kind === 'ask' && question.default && html`<span class="muted small">Empty: ${question.default}</span>`}`;
  }
  return html`<section class="question" aria-live="polite">
    <h2><${Linked} text=${question.prompt} /></h2>
    ${body}
  </section>`;
}

function App() {
  const [events, setEvents] = useState([]);
  const [question, setQuestion] = useState(null);
  const [toolRunning, setToolRunning] = useState(false);
  const [finished, setFinished] = useState(null);
  const [error, setError] = useState('');
  const [closed, setClosed] = useState(false);
  const end = useRef(null);
  const count = useRef(0);

  useEffect(() => {
    let alive = true;
    let rev = 0;
    (async () => {
      while (alive) {
        try {
          const state = await api(`/state?since=${rev}`);
          rev = state.rev;
          if (state.events.length) {
            setEvents((list) => {
              const next = list.slice();
              for (const e of state.events) next[e.id] = e;
              return next;
            });
          }
          setQuestion(state.question);
          setToolRunning(state.toolRunning);
          setFinished(state.finished);
          setError('');
          if (state.finished !== null && !state.question) await new Promise((r) => setTimeout(r, 2000));
        } catch (err) {
          setError(err.message);
          await new Promise((r) => setTimeout(r, 2000));
        }
      }
    })();
    const ping = setInterval(() => api('/ping', {}).catch(() => {}), 15000);
    const closing = () => navigator.sendBeacon('/api/closing', '');
    addEventListener('pagehide', closing);
    return () => { alive = false; clearInterval(ping); removeEventListener('pagehide', closing); };
  }, []);

  // New output or a new question: keep the newest in view.
  useEffect(() => {
    const shown = events.length + (question ? 1 : 0);
    if (shown !== count.current && end.current) end.current.scrollIntoView({ block: 'end' });
    count.current = shown;
  });

  const answer = async (id, value) => {
    try {
      await api('/answer', { id, value });
    } catch (err) {
      setError(err.message);
    }
  };
  const quit = async () => {
    if (!confirm('Stop cdimport here? Finished steps are saved; start it again to continue.')) return;
    try { await api('/quit', {}); } catch (err) { setError(err.message); }
  };
  const close = async () => {
    await api('/close', {}).catch(() => {});
    setClosed(true);
  };

  // Steps as the flow announced them; the log grouped under them.
  const sections = [];
  for (const e of events) {
    if (!e) continue;
    if (e.kind === 'step' || !sections.length) {
      sections.push({ step: e.kind === 'step' ? e : null, items: [] });
      if (e.kind === 'step') continue;
    }
    sections[sections.length - 1].items.push(e);
  }
  const reached = sections.map((s) => s.step).filter(Boolean);
  const current = reached.length ? reached[reached.length - 1].number : null;
  const seen = new Set(reached.map((s) => s.number));

  return html`
    <header class="topbar">
      <span class="brand"><img src="/static/icon.svg" alt="" width="26" height="26" /> cdimport</span>
      <ol class="steps" aria-label="Steps">
        ${STEPS.map(([n, name]) => {
          // The summary has no number: once it shows, every step reached is done.
          const state = n === current && finished === null ? 'now' : seen.has(n) ? 'done' : '';
          return html`<li class=${'step ' + state}><span class="dot">${state === 'done' ? '✓' : n}</span>${name}</li>`;
        })}
      </ol>
      ${finished === null && html`<button class="btn btn-small" disabled=${toolRunning} title=${toolRunning ? 'A tool is running: stop it first' : ''}
        onClick=${quit}>Stop cdimport</button>`}
    </header>
    <main>
      ${sections.map((s, i) => html`<section class=${'section' + (i < sections.length - 1 ? ' old' : '')} key=${i}>
        ${s.step && html`<h2>${s.step.number !== null && s.step.number !== undefined ? `${s.step.number}. ` : ''}${s.step.text}</h2>`}
        ${s.items.map((e) => html`<${Item} key=${e.id} event=${e} />`)}
      </section>`)}
      ${question && html`<${Question} key=${question.id} question=${question} onAnswer=${answer} />`}
      ${finished !== null && !question && html`<section class="section finished">
        <h2>${finished === 0 ? 'cdimport has finished' : finished === 130 ? 'cdimport was stopped' : `cdimport ended with exit code ${finished}`}</h2>
        <p class="muted">${finished === 0 ? 'Everything above is done.' : 'Finished steps are saved; start cdimport again to continue.'}</p>
        <div class="row"><button class="btn btn-primary" onClick=${close}>Close</button></div>
      </section>`}
      <div ref=${end}></div>
    </main>
    ${error && html`<div class="toast" role="alert">${error}</div>`}
    ${closed && html`<div class="overlay"><div class="section"><h2>cdimport has ended</h2>
      <p class="muted">You can close this tab.</p></div></div>`}
  `;
}

render(html`<${App} />`, document.getElementById('app'));
