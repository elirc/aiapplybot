"""ApplyBot command-line interface.

Usage:
  python -m applybot init
  python -m applybot apply <job-url> [--profile profile.yaml] [--headless]
  python -m applybot history
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path


from . import ats as ats_mod
from . import tracker
from .application_store import ApplicationStore, StoreError, application_input

BANNER = "ApplyBot — AI job application filler"

# Anchor all default paths to the project root so the tool behaves the same
# no matter which directory it's launched from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

_USE_COLOR = False


def _init_terminal() -> None:
    """UTF-8-safe output (model answers contain emoji/arrows that crash a
    cp1252 pipe) and ANSI colors where the terminal supports them."""
    global _USE_COLOR
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    _USE_COLOR = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    if _USE_COLOR and os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                # ENABLE_VIRTUAL_TERMINAL_PROCESSING
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
            else:
                _USE_COLOR = False
        except Exception:
            _USE_COLOR = False


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def green(text: str) -> str:
    return _c("32", text)


def red(text: str) -> str:
    return _c("31", text)


def yellow(text: str) -> str:
    return _c("33", text)


def cmd_init(_: argparse.Namespace) -> None:
    src = PROJECT_ROOT / "profile.example.yaml"
    dst = PROJECT_ROOT / "profile.yaml"
    if dst.exists():
        print(f"{dst} already exists — not overwriting.")
        return
    shutil.copyfile(src, dst)
    print(
        f"Created {dst} — open it and fill in your details, "
        "especially documents.resume (path to your resume PDF)."
    )


def cmd_history(args: argparse.Namespace) -> None:
    tracker.print_history(args.limit)


def _scan_and_fill(
    browser: Browser, backend, profile_yaml: str, documents: dict, detected: str | None
) -> tuple[str | None, str | None] | None:
    fields = browser.scan_fields()
    if not fields:
        print(
            "No form fields found on this page. If the form uses custom "
            "widgets the scanner can't reach, fill it by hand and press [d] "
            "once you've submitted."
        )
        return None

    print(f"Scanned {len(fields)} field(s). Asking {backend.name} for a fill plan...")
    detected = ats_mod.detect_ats(browser.page.url, "") or detected
    plan = backend.plan_form(
        profile_yaml,
        fields,
        browser.page_text(),
        browser.page.url,
        ats_mod.ats_hint(detected),
    )
    if plan.company or plan.job_title:
        print(f"Position: {plan.job_title or '?'} @ {plan.company or '?'}")

    fields_by_id = {f["id"]: f for f in fields}
    filled = skipped = failed = 0
    failures: list[tuple[str, str]] = []
    planned_ids: set[str] = set()

    for act in plan.actions:
        field = fields_by_id.get(act.field_id)
        if field is None:
            continue
        planned_ids.add(act.field_id)
        label = (field.get("label") or field.get("name") or act.field_id)[:46]

        if act.action == "skip":
            skipped += 1
            print(
                f"  {yellow('-')} {label:<48} skipped ({(act.reason or 'no data')[:60]})"
            )
            continue

        try:
            status = browser.apply_action(field, act.action, act.value, documents)
        except Exception as e:
            msg = str(e).splitlines()[0][:120] if str(e) else ""
            status = (
                f"FAILED {act.action} {act.value[:40]!r} — {type(e).__name__}: {msg}"
            )

        if status.startswith(("FAILED", "NO MATCHING", "NO FILE", "unknown action")):
            failed += 1
            failures.append((label, status))
            print(f"  {red('x')} {label:<48} {status[:110]}")
        else:
            filled += 1
            print(f"  {green('+')} {label:<48} {status[:90]}")

    unplanned = [f for f in fields if f["id"] not in planned_ids]
    for f in unplanned:
        label = (f.get("label") or f.get("name") or f["id"])[:46]
        print(f"  {yellow('?')} {label:<48} no action from the model — handle manually")

    summary = f"\nDone: {filled} filled, {skipped} skipped, {failed} failed"
    if unplanned:
        summary += f", {len(unplanned)} unplanned"
    print(summary + ".")

    if failures:
        print(red(f"Needs your attention ({len(failures)}):"))
        for label, status in failures:
            print(red(f"  - {label.strip()}: {status[:130]}"))
    if plan.notes:
        print(f"NOTE from the model: {plan.notes}")
    print(
        "Review everything in the browser before submitting — "
        "you are responsible for what gets sent."
    )
    return plan.company, plan.job_title


def cmd_apply(args: argparse.Namespace) -> None:
    from . import llm
    from .browser import Browser
    from .profile import load_profile, profile_as_yaml

    application_input(
        {
            "url": args.url,
            "company": "Unknown company",
            "title": "Untitled role",
            "status": "queued",
        }
    )
    profile = load_profile(args.profile)
    profile_yaml = profile_as_yaml(profile)
    documents = profile.get("documents", {}) or {}
    backend = llm.build_backend(args.llm, args.model)

    print(BANNER)
    print(f"Model: {backend.name} / {backend.model}")

    prior = tracker.prior_applications(args.url)
    if prior:
        when = prior[-1].get("timestamp", "")[:10]
        print(yellow(f"You already applied to this URL on {when}."))
        if input("Apply again anyway? [y/N] ").strip().lower() != "y":
            return

    print(f"Opening: {args.url}")
    browser = Browser(headless=args.headless, profile_dir=args.browser_profile)
    company: str | None = None
    job_title: str | None = None
    logged = False
    recording_attempted = False

    try:
        browser.goto(args.url)
        detected = ats_mod.detect_ats(args.url, browser.page_html_head())
        print(f"Platform: {detected or 'unknown / custom form'}")
        if not args.headless:
            input(
                "\nIn the browser: dismiss cookie banners and click through to the "
                "application form if needed.\nPress Enter when the form is visible... "
            )

        refill = True
        while True:
            if refill:  # only scan + call the LLM when there's new filling to do
                refill = False
                try:
                    result = _scan_and_fill(
                        browser, backend, profile_yaml, documents, detected
                    )
                    if result:
                        company = result[0] or company
                        job_title = result[1] or job_title
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    print(
                        red(
                            f"\nError while scanning/planning/filling: "
                            f"{type(e).__name__}: {str(e)[:300]}"
                        )
                    )
                    print(
                        "The browser is still open — fix anything there, "
                        "then press [r] to retry."
                    )

            cmd = (
                input(
                    "\n[r] rescan & fill (next step / after fixes)   "
                    "[s] click submit for me   \n"
                    "[d] I submitted it — log & finish              "
                    "[q] quit (logged as abandoned)\n> "
                )
                .strip()
                .lower()
            )

            if cmd == "r" or cmd == "":
                refill = True
                continue
            if cmd == "s":
                missing = browser.unfilled_required()
                if missing:
                    shown = "; ".join(missing[:8]) + ("..." if len(missing) > 8 else "")
                    print(yellow(f"Required fields still empty: {shown}"))
                confirm = (
                    input(
                        "Click the submit button now? This sends the "
                        "application. [y/N] "
                    )
                    .strip()
                    .lower()
                )
                if confirm != "y":
                    continue
                clicked = browser.try_submit()
                if clicked:
                    print(
                        f"Clicked {clicked!r}. Check the browser for confirmation or "
                        "validation errors, then [d] to log or [r] to fix and refill."
                    )
                else:
                    print("Couldn't find an obvious submit button — click it manually.")
                continue
            if cmd == "d":
                recording_attempted = True
                tracker.log_application(args.url, company, job_title, "applied")
                logged = True
                print("Logged. Good luck!")
                break
            if cmd == "q":
                recording_attempted = True
                tracker.log_application(args.url, company, job_title, "abandoned")
                logged = True
                break

    except (KeyboardInterrupt, EOFError):
        print("\nInput ended. The browser will close.")
    finally:
        try:
            if not logged and not recording_attempted and (company or job_title):
                tracker.log_application(args.url, company, job_title, "incomplete")
        finally:
            browser.close()


def cmd_dashboard(args: argparse.Namespace) -> None:
    from .dashboard import run_dashboard

    if not 0 <= args.port <= 65535:
        raise StoreError("Port must be between 0 and 65535.")
    run_dashboard(args.data_dir, args.port)


def cmd_import_history(args: argparse.Namespace) -> None:
    from .legacy_history import parse_legacy_history

    records = parse_legacy_history(args.file)
    if args.dry_run:
        print(
            f"Validated {len(records)} legacy records. No database was opened or changed; duplicate receipts are checked during import."
        )
        return
    result = ApplicationStore(
        Path(args.data_dir) / "applications.sqlite3"
    ).import_history(records)
    print(
        f"Imported {result['created']} records; skipped {result['skipped']} previously imported occurrences. Original JSONL was not changed."
    )


def cmd_backup(args: argparse.Namespace) -> None:
    path = Path(args.data_dir) / "applications.sqlite3"
    if not path.exists():
        raise StoreError("No tracker database exists at this data directory.")
    store = ApplicationStore(path)
    store.backup(args.output)
    print("Consistent tracker backup created in the requested new file.")


def main(argv: list[str] | None = None) -> None:
    _init_terminal()
    try:
        from dotenv import load_dotenv
    except ImportError:
        pass  # The tracker/dashboard only requires Python's standard library.
    else:
        load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(prog="applybot", description=BANNER)
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create profile.yaml from the template")
    p_init.set_defaults(func=cmd_init)

    p_apply = sub.add_parser("apply", help="Fill an application at the given URL")
    p_apply.add_argument("url", help="Job posting / application URL")
    p_apply.add_argument(
        "--profile",
        default=str(PROJECT_ROOT / "profile.yaml"),
        help="Path to profile YAML",
    )
    p_apply.add_argument(
        "--llm",
        default=None,
        choices=[
            "cli",
            "local",
            "claude",
            "anthropic",
            "deepseek",
            "qwen",
            "kimi",
            "glm",
            "minimax",
            "custom",
        ],
        help="Planner: cli = Claude Code subscription (default, no API key), "
        "local = offline rules (no API key), others need API keys. "
        "APPLYBOT_LLM env var overrides.",
    )
    p_apply.add_argument(
        "--model", default=None, help="Override the provider's default model name"
    )
    p_apply.add_argument(
        "--headless",
        action="store_true",
        help="Run without a visible browser (not recommended)",
    )
    p_apply.add_argument(
        "--browser-profile",
        default=str(PROJECT_ROOT / ".browser_profile"),
        help="Directory for the persistent browser profile (keeps logins)",
    )
    p_apply.set_defaults(func=cmd_apply)

    p_hist = sub.add_parser("history", help="Show logged applications")
    p_hist.add_argument(
        "--limit", type=int, default=200, help="Show up to 10000 recent records"
    )
    p_hist.set_defaults(func=cmd_history)

    p_dashboard = sub.add_parser(
        "dashboard", help="Open the local application-tracking web app"
    )
    p_dashboard.add_argument("--data-dir", default=str(PROJECT_ROOT / "data"))
    p_dashboard.add_argument("--port", type=int, default=8421)
    p_dashboard.set_defaults(func=cmd_dashboard)

    p_import = sub.add_parser(
        "import-history", help="Validate and explicitly import legacy JSONL history"
    )
    p_import.add_argument(
        "--file", default=str(PROJECT_ROOT / "data" / "applications.jsonl")
    )
    p_import.add_argument("--data-dir", default=str(PROJECT_ROOT / "data"))
    p_import.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate without opening or writing a database",
    )
    p_import.set_defaults(func=cmd_import_history)

    p_backup = sub.add_parser(
        "backup", help="Create a consistent SQLite backup in a new file"
    )
    p_backup.add_argument("--data-dir", default=str(PROJECT_ROOT / "data"))
    p_backup.add_argument("--output", required=True)
    p_backup.set_defaults(func=cmd_backup)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except sqlite3.Error:
        print(
            "Error: the tracker database is unavailable. Review the local data directory and keep any pending record for retry.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except (StoreError, ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
