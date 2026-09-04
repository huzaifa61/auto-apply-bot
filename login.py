from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        "/Users/saadshaikh/.linkedin-apply-bot-profile",
        channel="chrome",
        headless=False,
    )
    page = ctx.pages[0]
    page.goto("https://www.linkedin.com/login")
    input("Log in, then press Enter here...")
    ctx.close()
