"""Playwright layer: launch the browser, scan form fields, apply the fill plan."""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Locator, sync_playwright

# JS that tags every visible form control with a stable data-applybot-id and
# returns a JSON description of each. Radio buttons sharing a name are grouped
# into one logical field whose options map to individual radio ids.
#
# Takes a starting counter so ids stay unique when the same scan runs across
# multiple frames. Any ids left over from a previous scan are cleared first —
# on multi-step wizards and accordions, elements hidden since the last scan
# would otherwise keep stale ids that collide with freshly assigned ones and
# make every selector ambiguous.
SCAN_JS = r"""
(start) => {
  document.querySelectorAll("[data-applybot-id]").forEach((el) => {
    el.removeAttribute("data-applybot-id");
  });

  const results = [];
  const radioGroups = new Map();
  let counter = start;

  const clean = (t) => (t || "").replace(/\s+/g, " ").trim().slice(0, 300);

  const getLabel = (el) => {
    let t = "";
    if (el.labels && el.labels.length) t = el.labels[0].innerText;
    if (!t) t = el.getAttribute("aria-label") || "";
    if (!t) {
      const lb = el.getAttribute("aria-labelledby");
      if (lb) {
        t = lb.split(/\s+/).map((id) => {
          const n = document.getElementById(id);
          return n ? n.innerText : "";
        }).join(" ");
      }
    }
    if (!t) {
      const wrap = el.closest("label");
      if (wrap) t = wrap.innerText;
    }
    if (!t) t = el.placeholder || "";
    if (!t) t = el.name || el.id || "";
    return clean(t);
  };

  const isVisible = (el) => {
    const s = window.getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden") return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 || r.height > 0;
  };

  document.querySelectorAll("input, select, textarea").forEach((el) => {
    const type = (el.type || "").toLowerCase();
    if (["hidden", "submit", "button", "image", "reset"].includes(type)) return;
    // File inputs are routinely styled invisible behind a custom button, so
    // keep them regardless of visibility.
    if (type !== "file" && !isVisible(el)) return;
    if (el.disabled || el.readOnly) return;

    const abId = "ab" + counter++;
    el.setAttribute("data-applybot-id", abId);

    if (type === "radio") {
      const key = el.name || getLabel(el);
      if (!radioGroups.has(key)) {
        let groupLabel = "";
        const fs = el.closest("fieldset");
        if (fs) {
          const lg = fs.querySelector("legend");
          if (lg) groupLabel = clean(lg.innerText);
        }
        if (!groupLabel) {
          const grp = el.closest('[role="radiogroup"]');
          if (grp) groupLabel = clean(grp.getAttribute("aria-label") || "");
        }
        const field = {
          id: abId, tag: "input", type: "radio", name: el.name || "",
          label: groupLabel || key, required: !!el.required,
          current_value: "", options: [], option_ids: [],
        };
        radioGroups.set(key, field);
        results.push(field);
      }
      const field = radioGroups.get(key);
      const optLabel = getLabel(el) || el.value;
      field.options.push(optLabel);
      field.option_ids.push(abId);
      if (el.checked) field.current_value = optLabel;
      return;
    }

    const field = {
      id: abId,
      tag: el.tagName.toLowerCase(),
      type: type || (el.tagName === "SELECT" ? "select" : "text"),
      name: el.name || "",
      label: getLabel(el),
      required: !!el.required,
      current_value: type === "checkbox"
        ? String(el.checked)
        : clean(String(el.value || "")).slice(0, 100),
      options: [],
    };
    if (el.tagName === "SELECT") {
      field.options = Array.from(el.options)
        .map((o) => (o.label || o.text || "").trim())
        .filter((t) => t);
    }
    results.push(field);
  });

  return { fields: results, next: counter };
}
"""

# Lists required fields that are still empty, for the pre-submit warning.
REQUIRED_JS = r"""
() => {
  const out = [];
  const clean = (t) => (t || "").replace(/\s+/g, " ").trim().slice(0, 60);
  document.querySelectorAll("input[required], select[required], textarea[required]")
    .forEach((el) => {
      const type = (el.type || "").toLowerCase();
      if (["hidden", "submit", "button", "image", "reset"].includes(type)) return;
      let empty;
      if (type === "checkbox" || type === "radio") {
        empty = el.name
          ? !document.querySelector(`[name="${CSS.escape(el.name)}"]:checked`)
          : !el.checked;
      } else if (type === "file") {
        empty = el.files.length === 0;
      } else {
        empty = !el.value;
      }
      if (!empty) return;
      const label = el.labels && el.labels.length
        ? el.labels[0].innerText
        : el.name || el.id || type;
      out.push(clean(label));
    });
  return [...new Set(out)];
}
"""


class Browser:
    def __init__(self, headless: bool = False, profile_dir: str = ".browser_profile"):
        self._pw = sync_playwright().start()
        # Persistent context so logins (e.g. Workday accounts) survive between runs.
        self._ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(Path(profile_dir).absolute()),
            headless=headless,
            viewport={"width": 1400, "height": 950},
            args=["--disable-blink-features=AutomationControlled"],
        )
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()

    def goto(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)

    def scan_fields(self) -> list[dict]:
        """Scan every frame (embedded Greenhouse/Lever forms live in iframes)."""
        self.page.wait_for_timeout(500)
        fields: list[dict] = []
        counter = 0
        for idx, frame in enumerate(self.page.frames):
            try:
                scanned = frame.evaluate(SCAN_JS, counter)
            except Exception:
                continue  # frame detached or navigation in flight
            for f in scanned["fields"]:
                f["frame"] = idx
                fields.append(f)
            counter = scanned["next"]
        return fields

    def page_text(self) -> str:
        try:
            return self.page.inner_text("body", timeout=5000)
        except Exception:
            return ""

    def page_html_head(self) -> str:
        try:
            return self.page.content()[:20000]
        except Exception:
            return ""

    # ---- applying the plan -------------------------------------------------

    def _locator(self, field_id: str, frame_idx: int) -> Locator:
        """Locate a tagged element in its frame; fall back to searching all frames
        (frame order can shift between scan and action)."""
        sel = f'[data-applybot-id="{field_id}"]'
        frames = self.page.frames
        candidates = [frames[frame_idx]] if frame_idx < len(frames) else []
        candidates += [f for i, f in enumerate(frames) if i != frame_idx]
        for frame in candidates:
            try:
                loc = frame.locator(sel)
                if loc.count() > 0:
                    return loc
            except Exception:
                continue
        return self.page.locator(sel)

    def apply_action(self, field: dict, action: str, value: str, documents: dict) -> str:
        """Execute one planned action. Returns a short status string."""
        frame_idx = field.get("frame", 0)
        loc = self._locator(field["id"], frame_idx)

        if action == "skip":
            return "skipped"

        if action == "fill":
            loc.fill(value, timeout=8000)
            return f"filled: {value[:60]}"

        if action == "select":
            options = field.get("options", [])
            if field.get("type") == "radio":
                idx = _match_option(value, options)
                if idx is None:
                    return _no_match_status(value, options)
                self._locator(field["option_ids"][idx], frame_idx).check(timeout=8000)
                return f"selected radio: {options[idx]}"
            idx = _match_option(value, options)
            if idx is not None:
                loc.select_option(label=options[idx], timeout=8000)
                return f"selected: {options[idx][:60]}"
            # The model may have returned the option's value attribute rather
            # than its label; short timeout so an unmatchable value can't hang
            # the whole run.
            try:
                loc.select_option(value=value, timeout=2000)
                return f"selected by value: {value[:60]}"
            except Exception:
                return _no_match_status(value, options)

        if action == "check":
            loc.set_checked(value.strip().lower() == "true", timeout=8000)
            return f"checked={value}"

        if action == "upload":
            path = documents.get(value, "")
            if not path or not Path(path).exists():
                return f"NO FILE for {value!r} (set documents.{value} in profile.yaml)"
            loc.set_input_files(path, timeout=15000)
            return f"uploaded {value}: {Path(path).name}"

        return f"unknown action {action!r}"

    def unfilled_required(self) -> list[str]:
        """Labels of required fields that are still empty, across all frames."""
        missing: list[str] = []
        for frame in self.page.frames:
            try:
                missing.extend(frame.evaluate(REQUIRED_JS))
            except Exception:
                continue
        seen: set[str] = set()
        return [m for m in missing if not (m in seen or seen.add(m))]

    def try_submit(self) -> str | None:
        """Best-effort click of the primary submit button in any frame.
        Returns the button's text when clicked, None when nothing was found."""
        candidates = [
            'button[type="submit"]:visible',
            'input[type="submit"]:visible',
            'button:visible:has-text("Submit application")',
            'button:visible:has-text("Submit Application")',
            'button:visible:has-text("Submit")',
            'button:visible:has-text("Apply")',
        ]
        for c in candidates:
            for frame in self.page.frames:
                try:
                    loc = frame.locator(c)
                    if loc.count() == 0:
                        continue
                    first = loc.first
                    try:
                        text = first.inner_text(timeout=1000).strip()
                    except Exception:
                        text = ""
                    first.click(timeout=8000)
                    return text or "the submit button"
                except Exception:
                    continue
        return None

    def close(self) -> None:
        # The user may have closed the window themselves — don't crash on exit.
        try:
            self._ctx.close()
        except Exception:
            pass
        finally:
            try:
                self._pw.stop()
            except Exception:
                pass


def _match_option(value: str, options: list[str]) -> int | None:
    """Exact, then case-insensitive, then substring match of value in options."""
    if not options:
        return None
    for i, o in enumerate(options):
        if o == value:
            return i
    v = value.strip().lower()
    for i, o in enumerate(options):
        if o.strip().lower() == v:
            return i
    for i, o in enumerate(options):
        ol = o.strip().lower()
        if v and (v in ol or ol in v):
            return i
    return None


def _no_match_status(value: str, options: list[str]) -> str:
    shown = ", ".join(o[:30] for o in options[:6])
    more = "..." if len(options) > 6 else ""
    return f"NO MATCHING OPTION for {value!r} (options: {shown}{more})"
