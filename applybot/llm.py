"""LLM integration: turn a scanned form + user profile into a fill plan.

Backends, in default order (no API key required for the first two):
  - "cli" (default when Claude Code is installed): shells out to `claude -p`,
    which uses your Claude Code subscription login — no API key.
  - "local": fully offline rule-based planner driven by profile.yaml. Fills
    everything derivable from the profile and flags open-ended questions.
  - "claude": Anthropic API with native structured outputs (needs
    ANTHROPIC_API_KEY).
  - OpenAI-compatible endpoints: DeepSeek, Qwen (DashScope), Kimi (Moonshot),
    GLM (Zhipu), MiniMax, or any custom base URL — these use JSON-mode
    prompting and the same Pydantic validation.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import List, Literal, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError

from .browser import _match_option

CLAUDE_MODEL = "claude-opus-4-8"


class FieldAction(BaseModel):
    field_id: str = Field(description="The data-applybot-id of the field, e.g. 'ab3'")
    action: Literal["fill", "select", "check", "upload", "skip"]
    value: str = Field(
        description=(
            "fill: the text to type. select: an option string copied EXACTLY from the "
            "field's options list. check: 'true' or 'false'. upload: 'resume' or "
            "'cover_letter'. skip: empty string."
        )
    )
    reason: Optional[str] = Field(
        default=None, description="Only for skip: short reason the field was skipped."
    )


class FormPlan(BaseModel):
    company: Optional[str] = Field(
        default=None, description="Company name, if identifiable from the page."
    )
    job_title: Optional[str] = Field(
        default=None, description="Job title, if identifiable from the page."
    )
    actions: List[FieldAction]
    notes: Optional[str] = Field(
        default=None,
        description="Anything the user should double-check or handle manually before submitting.",
    )


SYSTEM_PROMPT = """You are ApplyBot, an assistant that fills out job application forms \
on behalf of one specific job seeker. You are given the seeker's profile, the visible \
text of a job application page, and a JSON list of scanned form fields.

Rules:
- Be truthful. Use only facts from the profile. Never invent employers, degrees, \
dates, visa status, or anything else. If the profile lacks the information a field \
needs, use action "skip" with a reason.
- For every scanned field, output exactly one action.
- "select" values must be copied character-for-character from that field's "options" \
list. If no option fits, pick the closest truthful one; if none is truthful, skip.
- For radio-type fields, treat them like selects: value must match one option exactly.
- For checkboxes: "true" only for consents/acknowledgements a reasonable applicant \
accepts (e.g. "I certify my answers are accurate") and opt-ins the profile supports. \
Skip marketing opt-ins.
- For file inputs: value "resume" for resume/CV fields, "cover_letter" only if the \
profile lists a cover letter file. Otherwise skip.
- For open-ended questions (why this company, tell us about yourself, cover letter \
text boxes): write a specific, first-person answer grounded in the profile and the \
job page text. Follow the profile's writing_style. Mention the company/role when the \
page identifies them.
- For salary questions, use the profile's desired_salary. For start date, use \
earliest_start_date.
- EEO / voluntary demographic questions: answer from the profile's eeo section, \
matching to the closest offered option.
- If a field's current_value already looks correct, skip it with reason "already filled".
- Answer standard yes/no screening questions from canned_answers and \
work_authorization when applicable.

The seeker's profile follows.

"""

# Appended to the user message on OpenAI-compatible backends, which lack
# Anthropic-style native structured outputs.
JSON_INSTRUCTIONS = """
Respond with ONLY a single JSON object (no markdown fences, no commentary) in
exactly this shape:

{
  "company": "company name or null",
  "job_title": "job title or null",
  "actions": [
    {"field_id": "ab0", "action": "fill", "value": "text to type", "reason": null},
    {"field_id": "ab1", "action": "select", "value": "exact option text", "reason": null},
    {"field_id": "ab2", "action": "check", "value": "true", "reason": null},
    {"field_id": "ab3", "action": "upload", "value": "resume", "reason": null},
    {"field_id": "ab4", "action": "skip", "value": "", "reason": "why skipped"}
  ],
  "notes": "anything the user should double-check, or null"
}

"action" must be one of: fill, select, check, upload, skip.
Include exactly one action per scanned field. Output valid JSON only.
"""

# OpenAI-compatible provider presets. Every default model can be overridden
# with --model or the APPLYBOT_MODEL env var.
PRESETS: dict[str, dict] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "key_env": "DASHSCOPE_API_KEY",
    },
    "kimi": {
        "base_url": "https://api.moonshot.ai/v1",
        "model": "kimi-k2-turbo-preview",
        "key_env": "MOONSHOT_API_KEY",
    },
    "glm": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4.6",
        "key_env": "ZHIPU_API_KEY",
    },
    "minimax": {
        "base_url": "https://api.minimax.io/v1",
        "model": "MiniMax-M2",
        "key_env": "MINIMAX_API_KEY",
    },
    "custom": {
        # Fully env-driven: APPLYBOT_BASE_URL / APPLYBOT_API_KEY / APPLYBOT_MODEL
        "base_url": None,
        "model": None,
        "key_env": "APPLYBOT_API_KEY",
    },
}


def _user_message(fields: list[dict], page_text: str, url: str, ats_note: str) -> str:
    return (
        f"URL: {url}\n"
        f"Platform note: {ats_note}\n\n"
        f"Visible page text (truncated):\n---\n{page_text[:6000]}\n---\n\n"
        f"Scanned form fields (JSON):\n{json.dumps(fields, ensure_ascii=False, indent=1)}\n\n"
        "Produce the fill plan."
    )


def extract_json(text: str) -> dict:
    """Pull a JSON object out of a model response that may include fences or prose."""
    text = text.strip()
    # strip ```json ... ``` fences
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


class ClaudeCodeBackend:
    """Plans via the installed Claude Code CLI (`claude -p`), which uses your
    Claude subscription login — no API key involved."""

    name = "claude-code"

    def __init__(self, model: str | None = None):
        self.exe = shutil.which("claude")
        if not self.exe:
            raise SystemExit(
                "Claude Code CLI not found on PATH. Install it, or use "
                "--llm local for the offline planner."
            )
        self.model = model or "subscription default"
        self._model_arg = model

    def _ask(self, prompt: str) -> str:
        cmd = [self.exe, "-p", "--output-format", "text"]
        if self._model_arg:
            cmd += ["--model", self._model_arg]
        try:
            proc = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("claude CLI timed out after 10 minutes.")
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[:300]
            raise RuntimeError(f"claude CLI failed (exit {proc.returncode}): {err}")
        if not proc.stdout.strip():
            raise RuntimeError("claude CLI returned no output.")
        return proc.stdout

    def plan_form(
        self,
        profile_yaml: str,
        fields: list[dict],
        page_text: str,
        url: str,
        ats_note: str,
    ) -> FormPlan:
        prompt = (
            SYSTEM_PROMPT
            + profile_yaml
            + "\n\n"
            + _user_message(fields, page_text, url, ats_note)
            + JSON_INSTRUCTIONS
        )
        text = self._ask(prompt)
        try:
            return FormPlan.model_validate(extract_json(text))
        except (json.JSONDecodeError, ValidationError):
            repair = (
                "The following was supposed to be a single valid JSON object in this shape:\n"
                + JSON_INSTRUCTIONS
                + "\nbut it is malformed or mis-shaped:\n---\n"
                + text[:8000]
                + "\n---\n"
                "Output ONLY the corrected JSON object, nothing else."
            )
            return FormPlan.model_validate(extract_json(self._ask(repair)))


class LocalBackend:
    """Fully offline planner: maps fields to profile.yaml values with keyword
    rules. Fills the standard ~90% of an application; open-ended questions are
    flagged for you to write (or use --llm cli for AI-written answers)."""

    name = "local"
    model = "rule-based (offline)"

    def __init__(self, model: str | None = None):
        pass

    def plan_form(
        self,
        profile_yaml: str,
        fields: list[dict],
        page_text: str,
        url: str,
        ats_note: str,
    ) -> FormPlan:
        p = yaml.safe_load(profile_yaml) or {}
        actions = [self._plan_field(f, p) for f in fields]
        return FormPlan(
            company=None,
            job_title=None,
            actions=actions,
            notes="Offline plan from profile.yaml — open-ended questions were "
            "left for you to write.",
        )

    def _plan_field(self, f: dict, p: dict) -> FieldAction:
        def act(action: str, value: str = "", reason: str | None = None) -> FieldAction:
            return FieldAction(
                field_id=f["id"], action=action, value=value, reason=reason
            )

        label = f"{f.get('label') or ''} {f.get('name') or ''}".lower()
        ftype = f.get("type", "")
        options = f.get("options", [])
        has = lambda *words: any(w in label for w in words)

        personal = p.get("personal", {}) or {}
        links = p.get("links", {}) or {}
        docs = p.get("documents", {}) or {}
        auth = p.get("work_authorization", {}) or {}
        prefs = p.get("preferences", {}) or {}
        eeo = p.get("eeo", {}) or {}
        canned = p.get("canned_answers", {}) or {}

        # Leave already-filled text-like fields alone.
        if ftype in ("text", "email", "tel", "url", "textarea") and f.get(
            "current_value"
        ):
            return act("skip", reason="already filled")

        if ftype == "file":
            if has("resume", "cv"):
                return (
                    act("upload", "resume")
                    if docs.get("resume")
                    else act("skip", reason="no resume path in profile.yaml")
                )
            if has("cover"):
                return (
                    act("upload", "cover_letter")
                    if docs.get("cover_letter")
                    else act("skip", reason="no cover letter file")
                )
            return act("skip", reason="unknown file field")

        if ftype == "checkbox":
            if has(
                "marketing", "newsletter", "promotional", "job alerts", "updates from"
            ):
                return act("skip", reason="marketing opt-in")
            if has(
                "certify",
                "accurate",
                "true and complete",
                "acknowledge",
                "agree",
                "terms",
                "consent",
                "privacy",
            ):
                return act("skip", reason="certification or consent — review manually")
            return act("skip", reason="checkbox — review manually")

        # Canned answers win over everything below.
        value: str | None = None
        for q, a in canned.items():
            if q.lower() in label and a is not None and str(a).strip():
                value = str(a)
                break

        if value is None:
            value = self._standard_value(label, has, personal, links, auth, prefs, eeo)

        if value is None or not str(value).strip():
            if ftype == "textarea" or has(
                "why ",
                "describe",
                "tell us",
                "cover letter",
                "about yourself",
                "anything else",
                "additional",
            ):
                return act(
                    "skip", reason="open-ended — write manually or use --llm cli"
                )
            return act("skip", reason="no matching rule")

        if options or ftype == "radio":
            idx = _match_option(value, options)
            if idx is None:
                return act(
                    "skip", reason=f"profile value {value[:30]!r} not in options"
                )
            return act("select", options[idx])
        return act("fill", value)

    @staticmethod
    def _standard_value(label, has, personal, links, auth, prefs, eeo) -> str | None:
        def yn(flag: object) -> str | None:
            if flag is True:
                return "Yes"
            if flag is False:
                return "No"
            return None

        def word(*ws: str) -> bool:
            # Whole-word match so "city" can't hit "ethnicity" or "state" hit
            # "United States".
            return any(re.search(rf"\b{re.escape(w)}\b", label) for w in ws)

        # Screening / EEO first — their labels often contain address-ish
        # substrings ("United States", "ethnicity").
        if has("sponsor"):
            return yn(auth.get("require_sponsorship"))
        if has(
            "authorized", "authorised", "legally", "work authorization", "right to work"
        ):
            return yn(auth.get("authorized_to_work_us"))
        if has("race", "ethnic"):
            return eeo.get("race_ethnicity")
        if has("veteran"):
            return eeo.get("veteran_status")
        if has("disability", "disabled"):
            return eeo.get("disability_status")
        if has("gender") and "identity" not in label:
            return eeo.get("gender")
        if has("pronoun"):
            return None

        if has("linkedin"):
            return links.get("linkedin")
        if has("github"):
            return links.get("github")
        if has("portfolio", "personal website", "website"):
            return links.get("portfolio") or None

        if has("salary", "compensation", "pay expectation"):
            return prefs.get("desired_salary")
        if has("start date", "available to start", "availability"):
            return prefs.get("earliest_start_date")
        if has("relocat"):
            return yn(prefs.get("willing_to_relocate"))
        if has("remote", "work arrangement", "hybrid"):
            return prefs.get("remote_preference")
        if has("hear about", "hear of", "referral source", "how did you find"):
            return prefs.get("how_did_you_hear")

        if has("first name", "given name"):
            return personal.get("first_name")
        if has("last name", "family name", "surname"):
            return personal.get("last_name")
        if has("full name", "your name", "legal name"):
            return personal.get("full_name")
        if has("email"):
            return personal.get("email")
        if has("phone", "mobile"):
            return personal.get("phone")
        if has("street", "address line") or word("address"):
            return personal.get("address")
        if word("city"):
            return personal.get("city")
        if word("state", "province"):
            return personal.get("state")
        if has("zip", "postal"):
            return personal.get("zip")
        if word("country"):
            return personal.get("country")
        return None


class ClaudeBackend:
    name = "claude"

    def __init__(self, model: str | None = None):
        import anthropic

        self.model = model or CLAUDE_MODEL
        self.client = anthropic.Anthropic()

    def plan_form(
        self,
        profile_yaml: str,
        fields: list[dict],
        page_text: str,
        url: str,
        ats_note: str,
    ) -> FormPlan:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT + profile_yaml,
                    # Profile + instructions are stable across a multi-step form,
                    # so cache them for cheaper follow-up calls.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": _user_message(fields, page_text, url, ats_note),
                }
            ],
            output_format=FormPlan,
        )
        plan = response.parsed_output
        if plan is None:
            raise RuntimeError("Claude returned an unparseable plan; try rescanning.")
        return plan


class OpenAICompatBackend:
    """DeepSeek / Qwen / Kimi / GLM / MiniMax / any OpenAI-compatible endpoint."""

    def __init__(self, name: str, model: str | None = None):
        from openai import OpenAI

        preset = PRESETS[name]
        self.name = name
        base_url = os.environ.get("APPLYBOT_BASE_URL") or preset["base_url"]
        if not base_url:
            raise SystemExit(
                "For --llm custom, set APPLYBOT_BASE_URL (and APPLYBOT_API_KEY, APPLYBOT_MODEL)."
            )
        api_key = os.environ.get(preset["key_env"]) or os.environ.get(
            "APPLYBOT_API_KEY"
        )
        if not api_key:
            raise SystemExit(
                f"No API key found for provider '{name}'. "
                f"Set {preset['key_env']} (or APPLYBOT_API_KEY) in your environment or .env file."
            )
        self.model = model or os.environ.get("APPLYBOT_MODEL") or preset["model"]
        if not self.model:
            raise SystemExit("No model set. Pass --model or set APPLYBOT_MODEL.")
        self.client = OpenAI(base_url=base_url, api_key=api_key)

    def plan_form(
        self,
        profile_yaml: str,
        fields: list[dict],
        page_text: str,
        url: str,
        ats_note: str,
    ) -> FormPlan:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + profile_yaml},
            {
                "role": "user",
                "content": _user_message(fields, page_text, url, ats_note)
                + JSON_INSTRUCTIONS,
            },
        ]
        text = self._chat(messages)
        try:
            return FormPlan.model_validate(extract_json(text))
        except (json.JSONDecodeError, ValidationError):
            # One repair round: show the model its own broken output.
            repair = messages + [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": "That was not valid JSON matching the required shape. "
                    "Respond again with ONLY the corrected JSON object.",
                },
            ]
            return FormPlan.model_validate(extract_json(self._chat(repair)))

    def _chat(self, messages: list[dict]) -> str:
        kwargs = dict(
            model=self.model, messages=messages, temperature=0.3, max_tokens=8192
        )
        try:
            resp = self.client.chat.completions.create(
                response_format={"type": "json_object"}, **kwargs
            )
        except Exception:
            # Some endpoints/models reject response_format — retry without it.
            resp = self.client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or ""
        if not content.strip():
            raise RuntimeError(f"Empty response from {self.name} model {self.model}.")
        return content


def build_backend(provider: str | None = None, model: str | None = None):
    provider = (
        provider or os.environ.get("APPLYBOT_LLM") or _default_provider()
    ).lower()
    if provider in ("cli", "claude-code", "code"):
        return ClaudeCodeBackend(model)
    if provider in ("local", "offline"):
        return LocalBackend(model)
    if provider in ("claude", "anthropic"):
        return ClaudeBackend(model)
    if provider in PRESETS:
        return OpenAICompatBackend(provider, model)
    raise SystemExit(
        f"Unknown LLM provider '{provider}'. "
        f"Choose from: cli, local, claude, {', '.join(PRESETS)}."
    )


def _default_provider() -> str:
    """No API key needed by default: Claude Code subscription if installed,
    otherwise the offline rule-based planner."""
    return "cli" if shutil.which("claude") else "local"
