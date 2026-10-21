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

// Whether a scroll position is at (or within a few pixels of) the end.
const atEnd = (bottom, height) => bottom >= height - 40;
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
    case 'handoff':
      return html`<p class="line k-dim">The album opened on ${event.name}'s page:
        <a href=${event.url}>go there</a> if this tab did not switch.</p>`;
    default:
      return null;
  }
}

function Tool({ event }) {
  const body = useRef(null);
  // Like a terminal: follow new output only while scrolled to its end.
  const stick = useRef(true);
  const [stopping, setStopping] = useState(false);
  useEffect(() => {
    if (stick.current && body.current) body.current.scrollTop = body.current.scrollHeight;
  });
  const scrolled = (e) => { stick.current = atEnd(e.target.scrollTop + e.target.clientHeight, e.target.scrollHeight); };
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
    ${(event.lines.length > 0 || event.partial) && html`<div class="tool-body mono" ref=${body} onScroll=${scrolled}>${event.lines.join('\n')}${event.partial
      ? html`${event.lines.length ? '\n' : ''}<span class="partial">${event.partial}</span>` : ''}</div>`}
  </div>`;
}

// A cover, or a note where there is none (the Cover Art Archive answers 404).
function Picture({ src }) {
  const [failed, setFailed] = useState(false);
  return failed ? html`<span class="no-picture">no cover</span>`
    : html`<img src=${src} alt="" loading="lazy" onError=${() => setFailed(true)} />`;
}

// --- the tag editor (only on the page; the terminal opens Ear Tag and co.) ------
// The file's other tags as they are, one value per line; emptied, a tag goes.
function OtherTags({ id, title, note, tags, onChange }) {
  const names = Object.keys(tags || {});
  if (!names.length) return null;
  return html`<details class="other-tags">
    <summary class="small"><b>${title}</b> <span class="muted">(${names.length}${note ? ', ' + note : ''})</span></summary>
    <div class="other-grid">
      ${names.map((name) => html`<label class="mono small" for=${id + '-' + name}>${name}</label>
        <textarea id=${id + '-' + name} class="mono" rows=${Math.max(1, tags[name].length)} spellcheck="false"
          value=${tags[name].join('\n')}
          onInput=${(e) => onChange({ ...tags, [name]: e.target.value.split('\n').map((v) => v.trim()).filter(Boolean) })}></textarea>`)}
    </div>
  </details>`;
}

const FEAT = /\s*[(\[](?:feat\.?|ft\.?|featuring)\s+([^)\]]+)[)\]]/i;
const JOINS = /\s+(?:feat\.?|ft\.?|featuring|&|and|x|×|with)\s+|\s*,\s*/i;

// "A", "A & B", "A, B & C": the credit for names without join phrases.
function creditFrom(names) {
  const list = names.filter(Boolean);
  if (list.length <= 1) return list[0] || '';
  return list.slice(0, -1).join(', ') + ' & ' + list[list.length - 1];
}

function Chips({ names, onChange, label, id }) {
  const add = (e) => {
    const value = e.target.value.trim();
    if (e.key === 'Enter' && value) {
      e.preventDefault();
      if (!names.includes(value)) onChange([...names, value]);
      e.target.value = '';
    }
  };
  return html`<div class="chips">
    ${names.map((name, i) => html`<span class="chip" key=${name}>${name}
      <button type="button" aria-label=${'Remove ' + name} onClick=${() => onChange(names.filter((_, j) => j !== i))}>×</button></span>`)}
    <label class="sr-only" for=${id}>${label}</label>
    <input id=${id} type="text" class="chip-input" placeholder="Add a name, then Enter" onKeyDown=${add} />
  </div>`;
}

function TagEditor({ question, onAnswer }) {
  const [loaded, setLoaded] = useState(null);
  const [edit, setEdit] = useState(null);
  const [plan, setPlan] = useState(null);
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [find, setFind] = useState('');
  const [replace, setReplace] = useState('');

  const load = async () => {
    try {
      const data = await api('/tags');
      setLoaded(data);
      setEdit(JSON.parse(JSON.stringify(data)));
      setError('');
    } catch (err) {
      setError(err.message);
    }
  };
  useEffect(() => { load(); }, []);
  // What a save would rename, worked out by the server while typing.
  useEffect(() => {
    if (!edit) return undefined;
    const timer = setTimeout(() => api('/tags/plan', edit).then(setPlan).catch(() => {}), 250);
    return () => clearTimeout(timer);
  }, [edit]);

  if (error && !edit) return html`<p class="line k-error"><span class="mark">✗</span>${error}</p>`;
  if (!edit) return html`<p class="muted">Reading the tags…</p>`;

  const dirty = JSON.stringify(edit) !== JSON.stringify(loaded);
  const set = (change) => setEdit({ ...edit, ...change });
  const setTrack = (i, change) => setEdit({ ...edit, tracks: edit.tracks.map((t, j) => (j === i ? { ...t, ...change } : t)) });
  const planFor = (file) => (plan && plan.files ? plan.files.find((f) => f.file === file) : null);
  const featCount = edit.tracks.filter((t) => FEAT.test(t.title)).length;
  const findCount = find ? edit.tracks.filter((t) => t.title.includes(find)).length : 0;

  const moveFeat = () => setEdit({
    ...edit,
    tracks: edit.tracks.map((t) => {
      const m = t.title.match(FEAT);
      if (!m) return t;
      const guests = m[1].split(/\s*(?:,|&| and )\s*/).map((g) => g.trim()).filter(Boolean);
      const names = (t.artists.length ? t.artists : [t.artist]).filter(Boolean);
      for (const g of guests) if (!names.includes(g)) names.push(g);
      return { ...t, title: t.title.replace(m[0], '').trim(), artist: `${t.artist} feat. ${creditFrom(guests)}`, artists: names };
    }),
  });
  const replaceAll = () => {
    if (!find) return;
    setEdit({ ...edit, tracks: edit.tracks.map((t) => ({ ...t, title: t.title.split(find).join(replace) })) });
  };
  const split = (credit) => credit.split(JOINS).map((n) => n.trim()).filter(Boolean);

  const save = async () => {
    setBusy(true);
    setError('');
    try {
      const result = await api('/tags/save', edit);
      const parts = [`Saved ${result.changed} track${result.changed === 1 ? '' : 's'}`];
      if (result.renamed.length) parts.push(`renamed ${result.renamed.length} file${result.renamed.length === 1 ? '' : 's'}`);
      if (result.folderRenamed) parts.push('renamed the folder');
      setNote(parts.join(', ') + '.' + (result.kept.length ? ` Kept the names of ${result.kept.length} file(s) cyanrip did not name.` : ''));
      await load();
      return true;
    } catch (err) {
      setError(err.message);
      return false;
    } finally {
      setBusy(false);
    }
  };
  const done = async () => {
    if (dirty && !(await save())) return;
    onAnswer(question.id, 'done');
  };
  // Back to the cover step, then into the editor again.
  const changeCover = async () => {
    if (dirty && !(await save())) return;
    onAnswer(question.id, 'cover');
  };
  const coverOf = (file, id) => `/api/tags/cover?file=${encodeURIComponent(file)}&v=${id}`;
  const coverCount = loaded.tracks.filter((t) => t.cover).length;

  return html`<div class="editor stack">
    <div class="row cover-row">
      ${loaded.cover
        ? html`<img class="album-cover" src=${coverOf(loaded.tracks[0].file, loaded.cover)} alt="The album's cover" />`
        : html`<span class="album-cover no-picture">${coverCount ? 'different covers per track' : 'no cover embedded'}</span>`}
      <div class="stack-tight">
        <span class="muted small">${loaded.cover ? 'The cover embedded in every track.'
          : coverCount ? `${coverCount} of ${loaded.tracks.length} tracks have a cover; each shows below.` : 'No track has a cover.'}</span>
        <div><button class="btn btn-small" type="button" disabled=${busy} onClick=${changeCover}>Change the cover…</button></div>
        <span class="muted small">Goes back to the cover step${dirty ? ', after saving these changes' : ''}, then returns here.</span>
      </div>
    </div>
    <div class="grid">
      <label>Album<input type="text" value=${edit.album} onInput=${(e) => set({ album: e.target.value })} /></label>
      <label>Album artist (credit)<input type="text" value=${edit.albumArtist} onInput=${(e) => set({ albumArtist: e.target.value })} /></label>
      <label>Date<input type="text" value=${edit.date} onInput=${(e) => set({ date: e.target.value })} /></label>
      <label>Genre<input type="text" value=${edit.genre} onInput=${(e) => set({ genre: e.target.value })} /></label>
    </div>
    <div class="stack-tight">
      <span class="small"><b>Album artists</b> <span class="muted">(single names, ALBUMARTISTS; optional)</span></span>
      <div class="row">
        <${Chips} id="album-artists" label="Add an album artist" names=${edit.albumArtists} onChange=${(names) => set({ albumArtists: names })} />
        ${!edit.albumArtists.length && edit.albumArtist && html`<button class="btn btn-small" type="button"
          onClick=${() => set({ albumArtists: split(edit.albumArtist) })}>Split from the credit</button>`}
      </div>
    </div>
    <${OtherTags} id="album" title="More tags of the album" note="the same in every track"
      tags=${edit.other} onChange=${(other) => set({ other })} />
    ${edit.differing.length > 0 && html`<p class="line k-warn"><span class="mark">!</span>The tracks differ in ${edit.differing.join(', ')}; saving gives every track the values above.</p>`}
    ${plan && plan.folderName && html`<p class="line k-dim">The folder will be renamed to <b class="mono">${plan.folderName}</b>.</p>`}

    <div class="row">
      <span class="label">All tracks</span>
      <button class="btn btn-small" type="button" disabled=${!featCount} onClick=${moveFeat}>Move “feat.” from titles to artists${featCount ? ` (${featCount})` : ''}</button>
      <label class="sr-only" for="find">Find in titles</label>
      <input id="find" type="text" class="short" placeholder="Find in titles" value=${find} onInput=${(e) => setFind(e.target.value)} />
      <label class="sr-only" for="repl">Replace with</label>
      <input id="repl" type="text" class="short" placeholder="Replace with" value=${replace} onInput=${(e) => setReplace(e.target.value)} />
      <button class="btn btn-small" type="button" disabled=${!findCount} onClick=${replaceAll}>Replace${findCount ? ` in ${findCount}` : ''}</button>
    </div>

    <div class="tracks">
      ${edit.tracks.map((t, i) => {
        const p = planFor(t.file);
        const old = loaded.tracks[i];
        return html`<div class="track-row" key=${t.file}>
          <div class="stack-tight">
            <span class="mono muted">${t.track || i + 1}</span>
            ${!loaded.cover && t.cover && html`<img class="track-cover" src=${coverOf(t.file, t.cover)} alt="" />`}
          </div>
          <div class="stack-tight">
            <div class="row numbers">
              <label>Disc <input type="text" class="number" value=${t.disc} onInput=${(e) => setTrack(i, { disc: e.target.value })} /></label>
              <label>Track <input type="text" class="number" value=${t.track} onInput=${(e) => setTrack(i, { track: e.target.value })} /></label>
              ${old.hasLyrics && html`<span class="muted small">has lyrics (lyrembed edits them)</span>`}
            </div>
            <label class="sr-only" for=${'title' + i}>Title of track ${i + 1}</label>
            <input id=${'title' + i} type="text" class=${t.title.trim() ? '' : 'missing'} value=${t.title} onInput=${(e) => setTrack(i, { title: e.target.value })} />
            <label class="sr-only" for=${'artist' + i}>Artist of track ${i + 1}</label>
            <input id=${'artist' + i} type="text" class=${t.artist.trim() ? '' : 'missing'} value=${t.artist} placeholder="Artist (credit)"
              onInput=${(e) => setTrack(i, { artist: e.target.value })} />
            <div class="row">
              <${Chips} id=${'artists' + i} label=${'Add an artist to track ' + (i + 1)} names=${t.artists} onChange=${(names) => setTrack(i, { artists: names })} />
              ${!t.artists.length && split(t.artist).length > 1 && html`<button class="btn btn-small" type="button"
                onClick=${() => setTrack(i, { artists: split(t.artist) })}>Split: ${split(t.artist).join(' · ')}</button>`}
            </div>
            <span class="mono small ${p && p.newFile ? '' : 'muted'}">${p && p.newFile
              ? html`<s class="muted">${old.file}</s> → ${p.newFile}` : t.file}${p && p.kept ? html` <span class="muted">(kept: ${p.kept})</span>` : ''}</span>
            <${OtherTags} id=${'track' + i} title="More tags of this track" note="" tags=${t.other}
              onChange=${(other) => setTrack(i, { other })} />
          </div>
        </div>`;
      })}
    </div>
    ${loaded.others > 0 && html`<p class="line k-dim">${loaded.others} file(s) in other formats are not edited here.</p>`}
    ${plan && plan.problem && html`<p class="line k-warn"><span class="mark">!</span>${plan.problem}</p>`}
    ${error && html`<p class="line k-error"><span class="mark">✗</span>${error}</p>`}
    ${note && !dirty && html`<p class="line k-success"><span class="mark">✓</span>${note}</p>`}
    <div class="row">
      <button class="btn" type="button" disabled=${!dirty || busy} onClick=${() => setEdit(JSON.parse(JSON.stringify(loaded)))}>Undo changes</button>
      <button class="btn" type="button" disabled=${!dirty || busy || (plan && plan.problem)} onClick=${save}>Save</button>
      <button class="btn btn-primary" type="button" disabled=${busy || (dirty && plan && plan.problem)} onClick=${done}>
        ${dirty ? 'Save and continue' : 'Continue'}</button>
    </div>
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
    if (e.ctrlKey || e.metaKey || e.altKey || question.kind === 'tag_editor') return;
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
  const images = question.images || {};
  if (question.kind === 'tag_editor') {
    body = html`<${TagEditor} question=${question} onAnswer=${onAnswer} />`;
  } else if (question.kind === 'choose' && Object.keys(images).length) {
    // Options with a picture as cards (releases, covers), the rest as usual.
    const pictured = question.options.filter(([key]) => images[key]);
    const plain = question.options.filter(([key]) => !images[key]);
    body = html`<div class="cards">${pictured.map(([key, description]) => {
      const [first, ...more] = description.split('\n');
      return html`<button class=${'btn card-option' + (key === question.default ? ' default' : '')} disabled=${sending}
        onClick=${() => answer(key)}>
        <span class="picture"><${Picture} src=${images[key]} /></span>
        <span class="text"><span><span class="key">${key}</span> <${Linked} text=${first} /></span>
          ${more.map((m) => html`<span class="more"><${Linked} text=${m.trim()} /></span>`)}</span>
      </button>`;
    })}</div>
    <div class="options">${plain.map(([key, description]) => html`<button class=${'btn option' + (key === question.default ? ' default' : '')}
      disabled=${sending} onClick=${() => answer(key)}><span class="key">${key}</span><span class="text"><${Linked} text=${description} /></span></button>`)}</div>`;
  } else if (question.kind === 'choose') {
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
  return html`<section class=${'question' + (question.kind === 'tag_editor' ? ' wide' : '')} aria-live="polite">
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
  const [back, setBack] = useState(null);
  const end = useRef(null);
  const stick = useRef(true);
  const lastHeight = useRef(0);
  const ownScroll = useRef(false);

  useEffect(() => {
    let alive = true;
    let rev = 0;
    (async () => {
      while (alive) {
        try {
          const state = await api(`/state?since=${rev}`);
          rev = state.rev;
          // Another program's page takes the tab (lyrembed): go there once;
          // coming back, the same event is not followed again.
          for (const e of state.events) {
            if (e.kind !== 'handoff' || !state.toolRunning) continue;
            const key = `cdimport-handoff-${e.id}-${e.url}`;
            let seen = false;
            try { seen = sessionStorage.getItem(key) === '1'; sessionStorage.setItem(key, '1'); } catch (err) { seen = false; }
            if (!seen) { location.href = e.url; return; }
          }
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
          if (state.returnUrl) setBack({ url: state.returnUrl, name: state.returnName });
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

  // Like a terminal: new content scrolls the page only for a reader who was
  // at its end; one who scrolled up to read stays where they are.
  // Only the reader's own scrolling decides: by the time the page's own
  // scroll arrives, a loading picture may have made the page taller again.
  const follow = () => {
    const height = document.documentElement.scrollHeight;
    if (height !== lastHeight.current && stick.current) {
      ownScroll.current = true;
      scrollTo(0, height);
    }
    lastHeight.current = height;
  };
  useEffect(() => {
    const onScroll = () => {
      if (ownScroll.current) { ownScroll.current = false; return; }
      stick.current = atEnd(scrollY + innerHeight, document.documentElement.scrollHeight);
    };
    addEventListener('scroll', onScroll, { passive: true });
    // Pictures (covers in a question) make the page taller after it was
    // drawn, without a new render: follow the height itself.
    const grows = new ResizeObserver(follow);
    grows.observe(document.body);
    return () => { removeEventListener('scroll', onScroll); grows.disconnect(); };
  }, []);
  useEffect(follow);

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
    // Started from another program's page (lyrembed's "Import a CD"): back there.
    if (back) location.href = back.url;
    else setClosed(true);
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
        <div class="row"><button class="btn btn-primary" onClick=${close}>${back ? `Done – back to ${back.name}` : 'Close'}</button></div>
      </section>`}
      <div ref=${end}></div>
    </main>
    ${error && html`<div class="toast" role="alert">${error}</div>`}
    ${closed && html`<div class="overlay"><div class="section"><h2>cdimport has ended</h2>
      <p class="muted">You can close this tab.</p></div></div>`}
  `;
}

render(html`<${App} />`, document.getElementById('app'));
