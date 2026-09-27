// Every call to the ReelSmith server (ui/CONTRACT.md section 3) lives here.
// Errors throw an Error whose message is the server's `detail`;
// err.status is the HTTP status and err.body the parsed JSON body (e.g. { conflict: true }).

const enc = encodeURIComponent;
const P = (name) => `/api/projects/${enc(name)}`;

async function request(method, url, body) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) {
    opts.body = body;
  } else if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    let msg = data && data.detail !== undefined ? data.detail : `${res.status} ${res.statusText}`;
    if (typeof msg !== 'string') msg = JSON.stringify(msg);
    const err = new Error(msg);
    err.status = res.status;
    err.body = data;
    throw err;
  }
  return data;
}

const get = (url) => request('GET', url);
const post = (url, body) => request('POST', url, body ?? {});
const put = (url, body) => request('PUT', url, body);
const del = (url) => request('DELETE', url);

function form(fields) {
  if (fields instanceof FormData) return fields;
  const fd = new FormData();
  for (const [k, v] of Object.entries(fields || {})) if (v !== undefined && v !== null) fd.append(k, v);
  return fd;
}

// — Meta —
/** GET /api/meta */
export const getMeta = () => get('/api/meta');

// — Projects —
/** GET /api/projects */
export const listProjects = () => get('/api/projects');
/** POST /api/projects { name, from } */
export const createProject = (name, from = null) => post('/api/projects', { name, from });
/** GET /api/projects/{name} */
export const getProject = (name) => get(P(name));
/** DELETE /api/projects/{name} */
export const deleteProject = (name) => del(P(name));
/** POST /api/projects/{name}/duplicate { new_name } */
export const duplicateProject = (name, newName) => post(`${P(name)}/duplicate`, { new_name: newName });

// — Screenshots —
/** GET /api/projects/{name}/screenshots */
export const listScreenshots = (name) => get(`${P(name)}/screenshots`);
/** POST /api/projects/{name}/screenshots multipart files[] -> { screenshots, rejected } */
export const uploadScreenshots = (name, files) => {
  const fd = new FormData();
  for (const f of Array.from(files)) fd.append('files', f);
  return request('POST', `${P(name)}/screenshots`, fd);
};
/** PUT /api/projects/{name}/screenshots/order { order } */
export const reorderScreenshots = (name, order) => put(`${P(name)}/screenshots/order`, { order });
/** DELETE /api/projects/{name}/screenshots/{file} */
export const deleteScreenshot = (name, file) => del(`${P(name)}/screenshots/${enc(file)}`);

// — Settings —
/** GET /api/projects/{name}/settings -> { settings, defaults } */
export const getSettings = (name) => get(`${P(name)}/settings`);
/** PUT /api/projects/{name}/settings settings -> { settings, defaults } */
export const putSettings = (name, settings) => put(`${P(name)}/settings`, settings);
/** GET /api/projects/{name}/config -> { text, usage } */
export const getConfig = (name) => get(`${P(name)}/config`);
/** PUT /api/projects/{name}/config { text } */
export const putConfig = (name, text) => put(`${P(name)}/config`, { text });
/** GET /api/defaults */
export const getDefaults = () => get('/api/defaults');
/** PUT /api/defaults settings */
export const putDefaults = (settings) => put('/api/defaults', settings);
/** GET /api/presets */
export const listPresets = () => get('/api/presets');
/** POST /api/presets { name, settings } */
export const savePreset = (name, settings) => post('/api/presets', { name, settings });
/** DELETE /api/presets/{name} */
export const deletePreset = (name) => del(`/api/presets/${enc(name)}`);

// — Run —
/** POST /api/projects/{name}/run { mode: "resume"|"start_over", retry_voice?, pause_after_narration? } */
export const startRun = (name, opts = {}) => post(`${P(name)}/run`, { mode: 'resume', ...opts });
/** POST /api/projects/{name}/stop */
export const stopRun = (name) => post(`${P(name)}/stop`);
/** POST /api/projects/{name}/pause-after-narration { enabled } */
export const setPauseAfterNarration = (name, enabled) => post(`${P(name)}/pause-after-narration`, { enabled });
/** GET /api/projects/{name}/status */
export const getStatus = (name) => get(`${P(name)}/status`);
/** GET /api/projects/{name}/log?offset=N -> { text, offset, name } */
export const getLog = (name, offset = 0) => get(`${P(name)}/log?offset=${offset}`);
/** GET /api/projects/{name}/logs */
export const listLogs = (name) => get(`${P(name)}/logs`);

// — Narration —
/** GET /api/projects/{name}/narration */
export const getNarration = (name) => get(`${P(name)}/narration`);
/** PUT /api/projects/{name}/narration { script } */
export const putNarration = (name, script) => put(`${P(name)}/narration`, { script });
/** POST /api/projects/{name}/narration/discard */
export const discardNarration = (name) => post(`${P(name)}/narration/discard`);
/** POST /api/projects/{name}/narration/restore { id } */
export const restoreNarration = (name, id) => post(`${P(name)}/narration/restore`, { id });
/** POST /api/projects/{name}/narration/continue */
export const continueNarration = (name) => post(`${P(name)}/narration/continue`);
/** POST /api/projects/{name}/narration/rewrite { instructions } */
export const rewriteNarration = (name, instructions) => post(`${P(name)}/narration/rewrite`, { instructions });

// — Reviews / Outputs —
/** GET /api/projects/{name}/reviews */
export const getReviews = (name) => get(`${P(name)}/reviews`);
/** GET /api/projects/{name}/outputs */
export const getOutputs = (name) => get(`${P(name)}/outputs`);

// — Cover —
/** GET /api/projects/{name}/cover */
export const getCover = (name) => get(`${P(name)}/cover`);
/** POST /api/projects/{name}/cover/run { hook?, shot?, force?, image_provider? } */
export const runCover = (name, opts = {}) => post(`${P(name)}/cover/run`, opts);
/** POST /api/projects/{name}/cover/revise { feedback } */
export const reviseCover = (name, feedback) => post(`${P(name)}/cover/revise`, { feedback });
/** POST /api/projects/{name}/cover/restore { version } */
export const restoreCover = (name, version) => post(`${P(name)}/cover/restore`, { version });

// — Characters —
/** GET /api/characters */
export const listCharacters = () => get('/api/characters');
/** POST /api/characters multipart name, description, main, turnaround?, face? (FormData or plain object) */
export const createCharacter = (formData) => request('POST', '/api/characters', form(formData));
/** PUT /api/characters/{id} multipart (any subset) */
export const updateCharacter = (id, formData) => request('PUT', `/api/characters/${enc(id)}`, form(formData));
/** DELETE /api/characters/{id} -> { ok, affected } */
export const deleteCharacter = (id) => del(`/api/characters/${enc(id)}`);
/** POST /api/characters/{id}/default -> [Character] */
export const setDefaultCharacter = (id) => post(`/api/characters/${enc(id)}/default`);
/** POST /api/characters/{id}/duplicate */
export const duplicateCharacter = (id) => post(`/api/characters/${enc(id)}/duplicate`);
/** URL of GET /api/characters/{id}/image/{slot} (slot main|turnaround|face) */
export const characterImageUrl = (id, slot) => `/api/characters/${enc(id)}/image/${slot}`;

// — Styles —
/** GET /api/styles */
export const listStyles = () => get('/api/styles');
/** POST /api/styles?on_conflict=replace|keep_both multipart json, image?. A 409 error has err.body.conflict === true. */
export const uploadStyle = (formData, onConflict) =>
  request('POST', '/api/styles' + (onConflict ? `?on_conflict=${enc(onConflict)}` : ''), form(formData));
/** PUT /api/styles/{id}/image multipart image */
export const setStyleImage = (id, file) => request('PUT', `/api/styles/${enc(id)}/image`, form({ image: file }));
/** DELETE /api/styles/{id} -> { ok, affected } */
export const deleteStyle = (id) => del(`/api/styles/${enc(id)}`);
/** URL of GET /api/styles/{id}/image */
export const styleImageUrl = (id) => `/api/styles/${enc(id)}/image`;
/** URL of GET /api/styles-reference-sheet */
export const styleReferenceSheetUrl = '/api/styles-reference-sheet';

// — Setup —
/** GET /api/setup */
export const getSetup = () => get('/api/setup');
/** PUT /api/setup { project_id, location, gcs_bucket_uri } */
export const putSetup = (values) => put('/api/setup', values);
/** POST /api/setup/health -> [{ name, ok, detail, hint }] */
export const runHealth = () => post('/api/setup/health');

// — Files —
/** URL of GET /files/{name}/{path} (raw project file) */
export const fileUrl = (name, path) => `/files/${enc(name)}/${path.split('/').map(enc).join('/')}`;
