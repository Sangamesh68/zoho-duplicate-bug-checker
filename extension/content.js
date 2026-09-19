/*
 * Duplicate Bug Checker — Zoho Projects content script.
 *
 * Watches the bug-title field on Zoho's own pages and asks the local backend
 * whether that bug already exists, showing the answer in a docked panel.
 *
 * Zoho's DOM is not a public contract and changes without notice, so field
 * detection is heuristic and every path degrades: if the title field can't be
 * found, the panel still takes typed input, and the tool stays usable.
 */
(() => {
  "use strict";

  const API = "http://localhost:8000";
  const DEBOUNCE_MS = 700;
  const MIN_CHARS = 8;

  // Search boxes and filters are text inputs too — don't treat them as titles.
  const TITLE_HINT = /(bug|issue)?\s*(title|summary|subject|name)/i;
  const NOT_TITLE_HINT = /(search|filter|comment|tag|url|link|email)/i;

  let lastQuery = "";
  let debounceTimer = null;
  let watchedField = null;

  // ---- panel ---------------------------------------------------------------

  function buildPanel() {
    const panel = document.createElement("div");
    panel.id = "dbc-panel";
    panel.innerHTML = `
      <div class="dbc-head">
        <span class="dbc-dot" title="backend status"></span>
        <span class="dbc-title">Duplicate check</span>
        <button class="dbc-btn" data-dbc="sync" title="Re-pull bugs from Zoho">Sync</button>
      </div>
      <div class="dbc-body">
        <div class="dbc-status">Looking for the bug title field…</div>
        <div class="dbc-results"></div>
      </div>`;
    document.body.appendChild(panel);

    // Collapse by clicking the header, but not when hitting Sync.
    panel.querySelector(".dbc-head").addEventListener("click", (e) => {
      if (e.target.closest("[data-dbc=sync]")) return;
      panel.classList.toggle("dbc-collapsed");
    });

    panel.querySelector("[data-dbc=sync]").addEventListener("click", runSync);
    return panel;
  }

  const panel = buildPanel();
  const statusEl = panel.querySelector(".dbc-status");
  const resultsEl = panel.querySelector(".dbc-results");
  const dotEl = panel.querySelector(".dbc-dot");

  function setStatus(text, warn = false) {
    statusEl.textContent = text;
    statusEl.classList.toggle("dbc-warn", warn);
  }

  function setHealth(ok) {
    dotEl.classList.toggle("ok", ok);
    dotEl.classList.toggle("bad", !ok);
    dotEl.title = ok ? "backend reachable" : "backend unreachable";
  }

  // ---- finding the title field --------------------------------------------

  function isVisible(el) {
    const rect = el.getBoundingClientRect();
    return rect.width > 60 && rect.height > 10;
  }

  /** Text near a field that hints at its purpose: label, placeholder, name. */
  function fieldHints(el) {
    const labelled = el.labels && el.labels.length ? el.labels[0].textContent : "";
    return [
      el.getAttribute("placeholder"),
      el.getAttribute("aria-label"),
      el.getAttribute("name"),
      el.id,
      labelled,
    ]
      .filter(Boolean)
      .join(" ");
  }

  function findTitleField() {
    const fields = document.querySelectorAll(
      'input[type="text"]:not([disabled]), input:not([type]), textarea'
    );
    for (const el of fields) {
      if (!isVisible(el)) continue;
      const hints = fieldHints(el);
      if (NOT_TITLE_HINT.test(hints)) continue;
      if (TITLE_HINT.test(hints)) return el;
    }
    return null;
  }

  // ---- backend calls -------------------------------------------------------

  async function checkDuplicate(title) {
    const resp = await fetch(`${API}/api/check-duplicate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, description: "" }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  }

  async function runSync() {
    const btn = panel.querySelector("[data-dbc=sync]");
    btn.disabled = true;
    setStatus("Syncing bugs from Zoho…");
    try {
      const resp = await fetch(`${API}/api/sync`, { method: "POST" });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
      setHealth(true);
      setStatus(`Synced ${data.synced} bugs.`);
      lastQuery = ""; // force a re-check against the refreshed set
    } catch (err) {
      setHealth(false);
      setStatus(`Sync failed: ${err.message}`, true);
    } finally {
      btn.disabled = false;
    }
  }

  // ---- rendering -----------------------------------------------------------

  /**
   * Zoho's UI link for a bug. Built from the portal in the current URL, since
   * the API only returns its own REST endpoint. Returns null when the portal
   * can't be read — the key is then shown as plain text rather than a dead link.
   */
  function bugUrl(match) {
    const portal = location.pathname.match(/\/portal\/([^/]+)/);
    if (!portal || !match.zoho_project_id) return null;
    return `${location.origin}/portal/${portal[1]}#buginfo/${match.zoho_project_id}/${match.zoho_issue_id}`;
  }

  function severityClass(verdict) {
    if (verdict === "likely duplicate") return "likely";
    if (verdict === "possible duplicate") return "possible";
    return "different";
  }

  function render(result) {
    resultsEl.textContent = "";

    const banner = document.createElement("div");
    banner.className = `dbc-verdict ${result.is_duplicate ? "dup" : "clear"}`;
    banner.textContent = result.is_duplicate
      ? "Likely already reported"
      : "No duplicate found";
    resultsEl.appendChild(banner);

    if (!result.matches.length) {
      const empty = document.createElement("div");
      empty.className = "dbc-empty";
      empty.textContent = "No bugs synced yet — hit Sync.";
      resultsEl.appendChild(empty);
      return;
    }

    for (const m of result.matches) {
      const cls = severityClass(m.verdict);
      const row = document.createElement("div");
      row.className = "dbc-match";

      const top = document.createElement("div");
      top.className = "dbc-match-top";

      const score = document.createElement("span");
      score.className = `dbc-score ${cls}`;
      score.textContent = `${Math.round(m.final_score * 100)}%`;
      top.appendChild(score);

      if (m.zoho_issue_key) {
        const key = document.createElement("span");
        key.className = "dbc-key";
        key.textContent = m.zoho_issue_key;
        top.appendChild(key);
      }
      row.appendChild(top);

      // textContent throughout: bug titles are untrusted input, never innerHTML.
      const href = bugUrl(m);
      const title = document.createElement(href ? "a" : "div");
      title.className = "dbc-match-title";
      title.textContent = m.title;
      if (href) {
        title.href = href;
        title.target = "_blank";
        title.rel = "noopener";
      }
      row.appendChild(title);

      const meta = document.createElement("div");
      meta.className = "dbc-meta";
      meta.textContent = [m.verdict, m.status, m.severity].filter(Boolean).join(" · ");
      row.appendChild(meta);

      const bar = document.createElement("div");
      bar.className = "dbc-bar";
      const fill = document.createElement("span");
      fill.className = cls;
      fill.style.width = `${Math.min(100, Math.round(m.final_score * 100))}%`;
      bar.appendChild(fill);
      row.appendChild(bar);

      resultsEl.appendChild(row);
    }
  }

  // ---- the check loop ------------------------------------------------------

  async function check(title) {
    const trimmed = title.trim();
    if (trimmed.length < MIN_CHARS || trimmed === lastQuery) return;
    lastQuery = trimmed;

    setStatus("Checking…");
    try {
      const result = await checkDuplicate(trimmed);
      setHealth(true);
      setStatus(`Checked: "${trimmed.slice(0, 40)}${trimmed.length > 40 ? "…" : ""}"`);
      render(result);
    } catch (err) {
      setHealth(false);
      lastQuery = ""; // let the same text retry once the backend is back
      setStatus(
        `Backend unreachable (${err.message}). Is it running on ${API}?`,
        true
      );
    }
  }

  function onInput(event) {
    clearTimeout(debounceTimer);
    const value = event.target.value;
    debounceTimer = setTimeout(() => check(value), DEBOUNCE_MS);
  }

  function attach(field) {
    if (field === watchedField) return;
    if (watchedField) watchedField.removeEventListener("input", onInput);
    watchedField = field;
    field.addEventListener("input", onInput);
    setStatus("Watching the title field — start typing.");
    if (field.value) check(field.value);
  }

  /** Manual fallback: the panel gets its own input when detection fails. */
  function showManualInput() {
    if (panel.querySelector("[data-dbc=manual]")) return;
    const box = document.createElement("input");
    box.type = "text";
    box.dataset.dbc = "manual";
    box.placeholder = "Paste the bug title here";
    box.style.cssText =
      "width:100%;box-sizing:border-box;margin-bottom:8px;padding:6px 8px;" +
      "border:1px solid #d5dbe1;border-radius:5px;font:inherit;";
    box.addEventListener("input", onInput);
    statusEl.after(box);
  }

  // Zoho is a single-page app: the bug form appears and disappears without a
  // page load, so re-scan rather than binding once at startup.
  function scan() {
    const field = findTitleField();
    if (field) {
      attach(field);
    } else if (!watchedField) {
      setStatus("No bug title field detected — type below instead.", true);
      showManualInput();
    } else if (!document.contains(watchedField)) {
      watchedField = null; // form closed; next scan re-attaches or falls back
    }
  }

  scan();
  setInterval(scan, 2000);

  // Confirm the backend is up before the user types anything.
  fetch(`${API}/api/health`)
    .then((r) => setHealth(r.ok))
    .catch(() => {
      setHealth(false);
      setStatus(`Backend not reachable at ${API}. Start it with uvicorn.`, true);
    });
})();
