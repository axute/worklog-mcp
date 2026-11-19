"""Made-up example entries, to try the web view or take screenshots without real data."""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta

from .db import Database

PROJECTS = {
    "website-relaunch": ("New company website with Astro", "Acme GmbH", "#2a78d6", [
        "Set up the Astro project with Tailwind and the content collections",
        "Built the landing page hero and the feature grid",
        "Moved the blog posts from WordPress, redirects for the old URLs",
        "Fixed the mobile navigation not closing after a click",
        "Added the contact form with spam protection and mail delivery",
        "Optimized images: AVIF with fallbacks, Lighthouse performance 98",
        "Deployed the preview to Cloudflare Pages with branch previews",
        "Accessibility pass: contrast, focus styles and landmarks",
    ]),
    "invoice-tool": ("Small Laravel app for invoices", "Acme GmbH", "#1baf7a", [
        "Created the invoice PDF template with line items and VAT",
        "Added recurring invoices with a nightly scheduler job",
        "Fixed rounding of VAT totals for mixed tax rates",
        "Import of customers from the old CSV export",
        "Dunning e-mails after 14 and 28 days",
        "Upgraded to the current Laravel version, tests green",
    ]),
    "home-lab": ("Docker host at home", None, "#e8a33a", [
        "Moved the reverse proxy to Traefik with automatic certificates",
        "Set up nightly backups of the app data to the NAS",
        "Single sign-on for the dashboards with Authelia",
        "Replaced the old Pi-hole with AdGuard Home",
        "Monitoring with Uptime Kuma and push notifications",
    ]),
    "mobile-app": ("Companion app (Flutter)", "Acme GmbH", "#9b59d0", [
        "Login screen with token refresh",
        "Offline cache for the order list",
        "Push notifications for new orders",
        "Fixed a crash on Android 12 when the camera permission is denied",
    ]),
    "docs": ("Internal handbook", None, None, [
        "Wrote the onboarding guide for new developers",
        "Documented the release process with a checklist",
        "Updated the architecture diagram after the API split",
    ]),
}
AGENTS = ["claude-code-laptop", "claude-code-laptop", "claude-desktop", "junie", "hook"]
TAGS = ["feature", "fix", "refactor", "deploy", "docs", "test", "infra"]


def create_demo_data(db: Database, owner: str, days: int = 120, seed: int = 7) -> int:
    """Entries on working days of the last `days` days, a few on weekends. Returns the number created."""
    rng = random.Random(seed)
    tz_now = datetime.now().astimezone()
    today = tz_now.date()
    created = 0
    for back in range(days, -1, -1):
        day = today - timedelta(days=back)
        weekend = day.weekday() >= 5
        if weekend and rng.random() > 0.2:
            continue
        for _ in range(rng.choice([1, 2, 2, 3, 4]) if not weekend else 1):
            project = rng.choices(list(PROJECTS), weights=[5, 4, 2, 2, 1])[0]
            summary = rng.choice(PROJECTS[project][3])
            start = datetime.combine(day, time(rng.randint(8, 18), rng.choice([0, 15, 30, 45])), tz_now.tzinfo)
            if start > tz_now:
                continue
            db.add_entry(
                owner=owner, agent=rng.choice(AGENTS), source=rng.choice(["tool"] * 6 + ["hook", "web"]),
                project=project, summary=summary, ts=start,
                details=rng.choice([None, "Changed files are in the pull request; tests added.",
                                    "Decided against a separate service: one container is enough for now."]),
                tags=rng.sample(TAGS, rng.choice([0, 1, 2])), duration_min=rng.choice([None, 15, 20, 30, 45, 60, 90, 120]),
            )
            created += 1
    for name, (description, customer, color, _) in PROJECTS.items():
        db.set_project_details(owner=owner, name=name, description=description, customer=customer, color=color,
                               archived=(name == "docs"))
    return created
