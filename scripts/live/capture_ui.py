"""Screenshots of the engineers' incident page and the users' BARQ AI page, as the real users.

An admin session (setup only) impersonates each demo user, exactly as an administrator
would to check a page; nothing is changed on the instance. Needs Playwright:

    uv run --with playwright python scripts/live/capture_ui.py --out /tmp/barq-ui \
        --incident INC0010341 [--engineer beth.anglin] [--caller <user_name>]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_agentic_core import ServiceNow  # noqa: E402
from verify_conversation import AdminSession  # noqa: E402


def main() -> int:
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--incident", required=True, help="incident number")
    parser.add_argument("--engineer", default="beth.anglin")
    parser.add_argument("--caller", default="", help="defaults to the incident's caller")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    sn, admin = ServiceNow(), AdminSession()
    row = sn.query("incident", f"number={args.incident}", "sys_id,caller_id.user_name")[0]
    caller = args.caller or row["caller_id.user_name"]
    users = {
        name: sn.query("sys_user", f"user_name={name}", "sys_id")[0]["sys_id"]
        for name in (args.engineer, caller)
    }
    host = admin.url.split("//", 1)[1]
    jar = next(h.cookiejar for h in admin.opener.handlers if hasattr(h, "cookiejar"))
    cookies = [
        {"name": c.name, "value": c.value, "domain": host, "path": c.path or "/"} for c in jar
    ]

    shots: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome")
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        context.add_cookies(cookies)
        page = context.new_page()

        def impersonate(user_sys_id: str) -> None:
            page.goto(f"{admin.url}/now/nav/ui/home", wait_until="domcontentloaded")
            token = page.evaluate("() => window.g_ck || ''") or admin.ck
            page.request.post(
                f"{admin.url}/api/now/ui/impersonate/{user_sys_id}",
                headers={"X-UserToken": token, "Content-Type": "application/json"},
                data="{}",
            )

        impersonate(users[args.engineer])
        page.goto(f"{admin.url}/incident.do?sys_id={row['sys_id']}", wait_until="networkidle")
        page.wait_for_timeout(3000)
        path = out / f"engineer_{args.incident}.png"
        page.screenshot(path=str(path), full_page=True)
        shots.append(str(path))

        impersonate(users[caller])
        page.goto(f"{admin.url}/esc?id=barq_ai", wait_until="networkidle")
        page.wait_for_timeout(5000)
        path = out / "user_page_home.png"
        page.screenshot(path=str(path), full_page=True)
        shots.append(str(path))
        tickets = page.locator(".barq-ticket")
        if tickets.count():
            tickets.first.click()
            page.wait_for_timeout(4000)
            path = out / "user_page_ticket.png"
            page.screenshot(path=str(path), full_page=True)
            shots.append(str(path))
        browser.close()
    print("\n".join(shots))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
