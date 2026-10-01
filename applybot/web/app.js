"use strict";
const element = (id) => document.getElementById(id);
const form = element("editor"),
  fields = element("fields");
let token = "",
  editor = null,
  dirty = false,
  busy = false,
  page = 1,
  listing = { items: [], total: 0, page: 1, pageSize: 20 },
  listController = null,
  listSequence = 0,
  searchTimer = null,
  auditSequence = 0;
const editable = ["url", "company", "title", "status", "notes"];
function message(id, text = "") {
  element(id).textContent = text;
  element(id).hidden = !text;
}
function values() {
  return Object.fromEntries(
    editable.map((name) => [name, form.elements.namedItem(name).value]),
  );
}
function mayDiscard() {
  return (
    !dirty ||
    confirm(
      "Discard unsaved editor changes? Export the draft first if you want to keep it.",
    )
  );
}
function setDirty(value) {
  dirty = value;
  element("draft-state").textContent = value
    ? "Unsaved changes"
    : "Showing saved values";
}
function setBusy(value) {
  busy = value;
  fields.disabled = value;
  for (const id of [
    "new",
    "logout",
    "save",
    "load-current",
    "discard",
    "delete",
  ])
    element(id).disabled = value;
  for (const button of element("records").querySelectorAll("button"))
    button.disabled = value;
  element("save").textContent = value ? "Saving…" : "Save application";
}
async function request(path, options = {}) {
  const controller = new AbortController(),
    external = options.signal;
  const cancel = () => controller.abort();
  external?.addEventListener("abort", cancel, { once: true });
  if (external?.aborted) controller.abort();
  const timer = setTimeout(
    () =>
      controller.abort(
        new Error(
          "The request timed out and may already have completed. Refresh current records before retrying.",
        ),
      ),
    15000,
  );
  try {
    const response = await fetch(path, {
      ...options,
      signal: controller.signal,
      cache: "no-store",
      headers: {
        Authorization: "Bearer " + token,
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
    });
    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error(
        "The tracker returned an unreadable response. Keep your draft and refresh the library before retrying.",
      );
    }
    if (!response.ok)
      throw new Error(data.error || "The request could not be completed.");
    return data;
  } finally {
    clearTimeout(timer);
    external?.removeEventListener("abort", cancel);
  }
}
async function run(work) {
  if (busy) return;
  setBusy(true);
  message("error");
  message("notice");
  try {
    await work();
  } catch (error) {
    message(
      "error",
      error.message +
        " Your editor remains available. For an uncertain new-record save, refresh the library before retrying.",
    );
  } finally {
    setBusy(false);
  }
}
function node(tag, text, className) {
  const value = document.createElement(tag);
  if (text !== undefined) value.textContent = text;
  if (className) value.className = className;
  return value;
}
function renderList() {
  const parent = element("records");
  parent.replaceChildren();
  if (!listing.items.length)
    parent.append(
      node("p", "No applications match these filters.", "empty-list"),
    );
  for (const record of listing.items) {
    const card = node("article", undefined, "record");
    card.append(
      node("span", record.status, "status-tag"),
      node("h3", record.title),
      node("p", record.company, "company"),
      node(
        "p",
        record.notes.slice(0, 130) + (record.notes.length > 130 ? "…" : ""),
        "preview",
      ),
    );
    const open = node("button", "Review " + record.title);
    open.disabled = busy;
    open.addEventListener("click", () => {
      if (mayDiscard())
        void run(async () =>
          adopt(await request("/api/applications/" + record.id)),
        );
    });
    card.append(open);
    parent.append(card);
  }
  element("page-label").textContent =
    `Page ${listing.page} of ${Math.max(1, Math.ceil(listing.total / 20))}`;
  element("previous").disabled = page <= 1;
  element("next").disabled = page * 20 >= listing.total;
  message(
    "list-status",
    `${listing.total} ${listing.total === 1 ? "application" : "applications"}`,
  );
}
async function loadList() {
  if (!token) return;
  listController?.abort();
  const controller = new AbortController();
  listController = controller;
  const sequence = ++listSequence;
  message("list-error");
  message("list-status", "Loading applications…");
  element("previous").disabled = true;
  element("next").disabled = true;
  const query = new URLSearchParams({
    q: element("search").value,
    status: element("status-filter").value,
    page: String(page),
  });
  try {
    const result = await request("/api/applications?" + query, {
      signal: controller.signal,
    });
    if (sequence !== listSequence) return;
    const lastPage = Math.max(1, Math.ceil(result.total / 20));
    if (page > lastPage) {
      page = lastPage;
      return loadList();
    }
    listing = result;
    renderList();
  } catch (error) {
    if (error.name !== "AbortError" && sequence === listSequence) {
      message("list-error", error.message);
      message("list-status", "The last loaded list is still displayed.");
    }
  }
}
async function loadAudit(identity) {
  const sequence = ++auditSequence;
  element("audit").replaceChildren();
  message("audit-error");
  try {
    const data = await request("/api/applications/" + identity + "/audit");
    if (editor?.id !== identity || sequence !== auditSequence) return;
    for (const entry of data.items)
      element("audit").append(
        node(
          "li",
          `${entry.action.toLowerCase()} · version ${entry.version} · ${new Date(entry.recorded_at).toLocaleString()}`,
        ),
      );
  } catch (error) {
    if (editor?.id === identity && sequence === auditSequence)
      message("audit-error", error.message);
  }
}
function adopt(record) {
  editor = record;
  for (const name of editable)
    form.elements.namedItem(name).value = record[name] ?? "";
  element("empty-editor").hidden = true;
  form.hidden = false;
  element("editor-heading").textContent = record.id
    ? record.title
    : "New application";
  element("version-label").textContent = record.id
    ? "Version " + record.version
    : "DRAFT";
  element("load-current").hidden = !record.id;
  element("record-actions").hidden = !record.id;
  element("audit-panel").hidden = !record.id;
  if (record.id) {
    element("job-link").href = record.url;
    void loadAudit(record.id);
  }
  setDirty(!record.id);
}
function closeEditor() {
  editor = null;
  auditSequence++;
  element("audit").replaceChildren();
  element("editor-heading").textContent = "New application";
  element("version-label").textContent = "";
  element("job-link").removeAttribute("href");
  message("audit-error");
  form.reset();
  form.hidden = true;
  element("empty-editor").hidden = false;
  setDirty(false);
}
async function unlock(candidate) {
  const previous = token;
  token = candidate;
  try {
    const result = await request("/api/applications");
    listing = result;
    page = 1;
    element("login").hidden = true;
    element("workspace").hidden = false;
    element("token").value = "";
    message("error");
    renderList();
    try {
      const meta = await request("/api/meta");
      element("legacy").hidden = !meta.legacyHistoryAvailable;
    } catch (error) {
      message("list-error", error.message);
    }
  } catch (error) {
    token = previous;
    throw error;
  }
}
element("login-form").addEventListener("submit", (event) => {
  event.preventDefault();
  void run(() => unlock(element("token").value));
});
element("new").addEventListener("click", () => {
  if (!busy && mayDiscard()) {
    adopt({ url: "", company: "", title: "", status: "queued", notes: "" });
    message("error");
    message("notice");
    form.elements.namedItem("company").focus();
  }
});
element("logout").addEventListener("click", () => {
  if (busy || !mayDiscard()) return;
  token = "";
  listController?.abort();
  listSequence++;
  clearTimeout(searchTimer);
  closeEditor();
  listing = { items: [], total: 0, page: 1, pageSize: 20 };
  element("records").replaceChildren();
  element("search").value = "";
  element("status-filter").value = "";
  element("workspace").hidden = true;
  element("login").hidden = false;
  message("error");
  message("notice");
});
form.addEventListener("input", () => setDirty(true));
form.addEventListener("change", () => setDirty(true));
form.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!editor) return;
  const base = editor,
    application = values();
  void run(async () => {
    const result = await request(
      "/api/applications" + (base.id ? "/" + base.id : ""),
      {
        method: base.id ? "PUT" : "POST",
        body: JSON.stringify(
          base.id ? { version: base.version, application } : application,
        ),
      },
    );
    adopt(result);
    message("notice", "Application saved at version " + result.version + ".");
    await loadList();
  });
});
element("load-current").addEventListener("click", () => {
  if (
    editor?.id &&
    confirm(
      "Replace the editor with the currently saved record? Export unsaved work first.",
    )
  )
    void run(async () =>
      adopt(await request("/api/applications/" + editor.id)),
    );
});
element("discard").addEventListener("click", () => {
  if (mayDiscard()) closeEditor();
});
element("delete").addEventListener("click", () => {
  if (
    editor?.id &&
    confirm(
      "Delete this application record permanently? Its minimal change history will remain.",
    )
  ) {
    const base = editor;
    void run(async () => {
      await request("/api/applications/" + base.id, {
        method: "DELETE",
        body: JSON.stringify({ version: base.version }),
      });
      closeEditor();
      message("notice", "Application deleted.");
      await loadList();
    });
  }
});
element("export-draft").addEventListener("click", () => {
  if (!editor) return;
  const url = URL.createObjectURL(
    new Blob(
      [
        JSON.stringify(
          {
            id: editor.id ?? null,
            version: editor.version ?? null,
            application: values(),
          },
          null,
          2,
        ),
      ],
      { type: "application/json" },
    ),
  );
  const link = node("a");
  link.href = url;
  link.download = "applybot-editor-draft.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
});
element("search").addEventListener("input", () => {
  page = 1;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => void loadList(), 180);
});
element("status-filter").addEventListener("change", () => {
  page = 1;
  void loadList();
});
element("refresh").addEventListener("click", () => void loadList());
element("previous").addEventListener("click", () => {
  page = Math.max(1, page - 1);
  void loadList();
});
element("next").addEventListener("click", () => {
  page++;
  void loadList();
});
window.addEventListener("beforeunload", (event) => {
  if (dirty) {
    event.preventDefault();
    event.returnValue = "";
  }
});
const initialToken = new URLSearchParams(location.hash.slice(1)).get("token");
if (initialToken) {
  history.replaceState(null, "", location.pathname);
  void run(() => unlock(initialToken));
}
