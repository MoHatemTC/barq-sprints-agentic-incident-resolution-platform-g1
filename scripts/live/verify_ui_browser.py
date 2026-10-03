"""Real-browser check of the users' BARQ AI page and the engineers' card, in Chrome.

An admin session (setup only) impersonates a fresh ordinary employee, who opens a ticket
on /esc?id=barq_ai exactly as a person would: the form, the "working" banner, the answer
arriving without pressing Refresh, readable text, confirming, the chat, and a phone-sized
screen. An engineer then opens the same incident. Screenshots go to --shots. Needs
Playwright:

    uv run --with playwright python scripts/live/verify_ui_browser.py --out ui.json \
        --shots /tmp/barq-ui
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_agentic_core import LABEL, ServiceNow  # noqa: E402
from verify_conversation import OUTLOOK, AdminSession  # noqa: E402
from verify_triage import fresh_callers  # noqa: E402

ENGINEER = "beth.anglin"
RAW_MARKUP = ("::chunk::", "**", "###")


def main() -> int:  # noqa: PLR0915 - one linear walk through the page
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--shots", required=True)
    args = parser.parse_args()
    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)

    sn, admin = ServiceNow(), AdminSession()
    caller = fresh_callers(sn, 1)[0]
    users = {
        name: sn.query("sys_user", f"user_name={name}", "sys_id")[0]["sys_id"]
        for name in (caller, ENGINEER)
    }
    host = admin.url.split("//", 1)[1]
    jar = next(h.cookiejar for h in admin.opener.handlers if hasattr(h, "cookiejar"))
    cookies = [
        {"name": c.name, "value": c.value, "domain": host, "path": c.path or "/"} for c in jar
    ]
    checks: dict[str, bool] = {}
    timings: dict[str, float] = {}
    evidence: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat(), "caller": caller}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome")
        context = browser.new_context(viewport={"width": 1440, "height": 950})
        context.add_cookies(cookies)
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        def shot(name: str) -> None:
            page.screenshot(path=str(shots / f"{name}.png"), full_page=True)

        def impersonate(user_sys_id: str) -> None:
            page.goto(f"{admin.url}/now/nav/ui/home", wait_until="domcontentloaded")
            token = page.evaluate("() => window.g_ck || ''") or admin.ck
            page.request.post(
                f"{admin.url}/api/now/ui/impersonate/{user_sys_id}",
                headers={"X-UserToken": token, "Content-Type": "application/json"},
                data="{}",
            )

        # 1. The page opens on an empty "New ticket" form; no Virtual Agent bubble.
        impersonate(users[caller])
        page.goto(f"{admin.url}/esc?id=barq_ai", wait_until="networkidle")
        page.wait_for_selector(".barq-newform", timeout=30000)
        shot("01_page_opens_on_new_ticket")
        checks["opens_on_new_ticket_form"] = page.locator(".barq-newform").is_visible()
        checks["new_user_has_no_tickets"] = page.locator(".barq-empty").is_visible()
        checks["virtual_agent_bubble_hidden"] = not page.locator(".sp-ac-root").is_visible()
        checks["header_has_barq_ai"] = page.get_by_text("BARQ AI", exact=True).count() > 0

        # 2. Fill and send the form like a person.
        page.fill(".barq-newform input[type=text]", f"{LABEL} Outlook is disconnected")
        page.fill(".barq-newform textarea", OUTLOOK)
        page.select_option(".barq-newform select", "software")
        page.get_by_role("button", name="Open the ticket").click()
        sent = time.monotonic()
        page.wait_for_selector(".barq-head .barq-pill", timeout=30000)
        number = page.locator(".barq-head .barq-sub").inner_text().split(" ")[0]
        evidence["incident"] = number
        try:
            page.wait_for_selector(".barq-working", timeout=15000)
            checks["working_banner_shown"] = True
        except Exception:  # noqa: BLE001 - the agent may already have finished
            checks["working_banner_shown"] = False
        shot("02_working_banner")

        # 3. The answer arrives by itself (nobody presses Refresh).
        page.wait_for_function(
            "() => /Solved|engineer|answer/.test("
            "document.querySelector('.barq-head .barq-pill')?.innerText || '')",
            timeout=150000,
        )
        timings["answer_on_screen_s"] = round(time.monotonic() - sent, 1)
        page.wait_for_timeout(1500)
        shot("03_answer_arrived")
        status = page.locator(".barq-head .barq-pill").inner_text()
        evidence["status_after_answer"] = status
        bubbles = " ".join(page.locator(".barq-bubble").all_inner_texts())
        checks["answer_arrived_without_refresh"] = "Solved" in status
        checks["answer_within_90s"] = timings["answer_on_screen_s"] <= 90
        checks["answer_is_readable"] = not any(mark in bubbles for mark in RAW_MARKUP)
        checks["answer_has_steps"] = "1." in bubbles
        checks["banner_gone_after_answer"] = not page.locator(".barq-working").is_visible()

        # 4. Confirm: the ticket closes and moves under "closed tickets".
        page.get_by_role("button", name="It works, close it").click()
        page.wait_for_function(
            "() => /Closed/.test(document.querySelector('.barq-head .barq-pill')?.innerText||'')",
            timeout=30000,
        )
        shot("04_confirmed_closed")
        checks["closed_after_confirm"] = True
        checks["closed_ticket_folded"] = page.locator(".barq-closed-toggle").is_visible()

        # 5. The chat answers a question readably.
        page.get_by_text("Chat with BARQ AI", exact=True).click()
        page.fill(".barq-input textarea", "How do I fix Wi-Fi that keeps dropping on 5 GHz?")
        page.get_by_role("button", name="Send").click()
        asked = time.monotonic()
        page.wait_for_selector(".barq-msg.from-ai .barq-bubble", timeout=150000)
        timings["chat_answer_s"] = round(time.monotonic() - asked, 1)
        page.wait_for_timeout(1000)
        shot("05_chat_answer")
        chat = " ".join(page.locator(".barq-msg.from-ai .barq-bubble").all_inner_texts())
        checks["chat_answered"] = len(chat) > 40
        checks["chat_is_readable"] = not any(mark in chat for mark in RAW_MARKUP)

        # 6. Phone-sized screen: nothing wider than the screen.
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{admin.url}/esc?id=barq_ai", wait_until="networkidle")
        page.wait_for_selector(".barq-newform", timeout=30000)
        shot("06_phone")
        overflow = page.evaluate(
            "() => document.querySelector('.barq').scrollWidth - "
            "document.querySelector('.barq').clientWidth"
        )
        checks["fits_a_phone"] = overflow <= 2
        checks["no_script_errors"] = not [e for e in errors if "barq" in e.lower()]
        evidence["script_errors"] = errors[:10]

        # 7. The engineer opens the same incident: the BARQ AI card shows its state.
        page.set_viewport_size({"width": 1440, "height": 950})
        impersonate(users[ENGINEER])
        sys_id = sn.query("incident", f"number={number}", "sys_id")[0]["sys_id"]
        page.goto(f"{admin.url}/incident.do?sys_id={sys_id}", wait_until="networkidle")
        page.wait_for_timeout(2500)
        shot("07_engineer_card")
        html = page.content()
        checks["engineer_sees_barq_card"] = "barq-chip" in html
        checks["closed_ticket_offers_no_buttons"] = "barq_take_over_from_ai" not in html
        browser.close()

    evidence.update(
        {
            "checks": checks,
            "timings": timings,
            "passed": all(checks.values()),
            "shots": sorted(str(p) for p in shots.glob("*.png")),
        }
    )
    Path(args.out).write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(
        json.dumps({"checks": checks, "timings": timings, "passed": evidence["passed"]}, indent=1)
    )
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
