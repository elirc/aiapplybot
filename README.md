# ApplyBot

AI-assisted job application filler for **company career sites** (Greenhouse, Lever,
Ashby, Workday, and custom forms). You point it at a job URL; it opens a real
browser, scans the application form, asks Claude to map your profile onto every
field — including writing answers to open-ended questions — fills it all in, and
then **waits for you to review and submit**. It never submits on its own unless you
tell it to.

## Setup (one time)

```powershell
cd C:\Users\Owner\Desktop\aiapplybot
pip install -r requirements.txt
playwright install chromium

# Create and edit your profile
python -m applybot init
notepad profile.yaml
```

**No API key needed.** By default the bot plans forms through your installed
Claude Code CLI (`claude -p`), which uses your Claude subscription login. If
Claude Code isn't installed, it falls back to a fully offline rule-based
planner. API-key providers remain available via `--llm` (see below).

Fill in `profile.yaml` carefully — the bot only uses facts from that file and will
skip anything it doesn't know. Set `documents.resume` to the path of your resume PDF.

## Applying to a job

```powershell
python -m applybot apply "https://boards.greenhouse.io/company/jobs/12345"
```

1. A Chrome window opens on the job page. Dismiss cookie banners / click "Apply"
   if needed, then press **Enter** in the terminal.
2. The bot scans the form, Claude plans every field, and the bot fills them.
   You'll see a line-by-line report of what was filled or skipped and why.
3. Review the form in the browser. Fix anything, complete custom widgets the
   scanner couldn't reach, then:
   - **r** — rescan & fill again (use this on multi-step forms after clicking *Next*,
     or after you fixed something)
   - **s** — have the bot click the submit button
   - **d** — you submitted it yourself; log it and finish
   - **q** — quit without applying

```powershell
python -m applybot history      # everything you've applied to
```

## Choosing a planner

The default needs **no API key**:

| `--llm`    | What it is                                        | API key |
|------------|---------------------------------------------------|---------|
| `cli`      | **Default.** Claude via your Claude Code login (`claude -p`) — best form understanding + written answers | none |
| `local`    | Offline rule-based planner from profile.yaml — instant, free, skips essay questions | none |

`--model` with `--llm cli` accepts Claude Code model names (`sonnet`, `haiku`,
`opus`) if you want to trade quality for speed/quota.

API-key providers are still supported (keys go in `.env` or the environment):

| `--llm`    | Provider            | Default model         | API key env var    |
|------------|---------------------|-----------------------|--------------------|
| `claude`   | Anthropic API       | `claude-opus-4-8`     | `ANTHROPIC_API_KEY`|
| `deepseek` | DeepSeek            | `deepseek-chat`       | `DEEPSEEK_API_KEY` |
| `qwen`     | Alibaba DashScope   | `qwen-plus`           | `DASHSCOPE_API_KEY`|
| `kimi`     | Moonshot            | `kimi-k2-turbo-preview` | `MOONSHOT_API_KEY` |
| `glm`      | Zhipu (bigmodel.cn) | `glm-4.6`             | `ZHIPU_API_KEY`    |
| `minimax`  | MiniMax             | `MiniMax-M2`          | `MINIMAX_API_KEY`  |
| `custom`   | any OpenAI-compatible endpoint | from `APPLYBOT_MODEL` | `APPLYBOT_API_KEY` |

```powershell
# Examples
python -m applybot apply "<url>" --llm deepseek
python -m applybot apply "<url>" --llm qwen --model qwen-flash
python -m applybot apply "<url>" --llm kimi --model kimi-k2-0905-preview

# Any other OpenAI-compatible endpoint (local Ollama, OpenRouter, etc.)
# via env vars (put them in .env):
#   APPLYBOT_BASE_URL=https://openrouter.ai/api/v1
#   APPLYBOT_API_KEY=sk-or-...
#   APPLYBOT_MODEL=deepseek/deepseek-chat
python -m applybot apply "<url>" --llm custom
```

Defaults can also be set once via env: `APPLYBOT_LLM=deepseek` makes DeepSeek the
default provider, and `APPLYBOT_MODEL=...` overrides any preset's model name.
Cheap models occasionally return malformed plans — the bot auto-repairs once and
tells you what it skipped, so review the browser a little more carefully than
you would with Claude.

## How it works

- **Playwright** drives a persistent Chrome profile (`.browser_profile/`), so
  logins to Workday-style portals survive between runs.
- A scan script tags every visible input/select/textarea/radio-group and extracts
  its label, options, and current value.
- The LLM receives your profile + the page text + the scanned fields and returns
  a structured fill plan (validated with Pydantic). On Claude this uses native
  structured outputs and prompt caching (multi-step forms only pay for the
  profile once); on OpenAI-compatible providers it uses JSON mode with a
  one-shot auto-repair if the model returns malformed JSON.
- Free-text questions ("Why do you want to work here?") are answered in your
  voice using `background`, `writing_style`, and the job description on the page.

## Limitations & good sense

- Fancy custom widgets (React comboboxes, some Ashby/Workday dropdowns) may not be
  scannable — fill those by hand, then press **r** so the bot does the rest.
- CAPTCHAs and account sign-ins are yours to handle (the persistent profile helps).
- Always review before submitting. You're accountable for every answer sent.
- The bot is built to be truthful: it only asserts facts present in `profile.yaml`
  and skips questions it can't answer from it.

## Layout

```
applybot/
  cli.py       command-line interface + interactive apply loop
  browser.py   Playwright: scan fields, fill/select/check/upload
  llm.py       Claude call -> structured FormPlan
  ats.py       ATS platform detection + per-platform hints
  profile.py   profile.yaml loading
  tracker.py   data/applications.jsonl history
profile.example.yaml   template — copy to profile.yaml
```
