const { chromium } = require("@playwright/test");

(async () => {
  const browser = await chromium.launch({ headless: false });
  const page = await browser.newPage();
  page.on("console", (m) => console.log("CONSOLE", m.type(), m.text()));
  page.on("pageerror", (e) => console.log("PAGEERROR", e.message));
  page.on("response", (r) => {
    if (r.status() >= 400) console.log("HTTP", r.status(), r.url());
  });
  page.on("requestfailed", (r) =>
    console.log("REQFAIL", r.url(), r.failure() && r.failure().errorText)
  );
  await page.goto("http://localhost:3000", { waitUntil: "networkidle" });
  await page.waitForTimeout(4000);
  console.log("TITLE", await page.title());
  console.log(
    "BODYTEXT",
    (await page.locator("body").innerText()).slice(0, 800)
  );
  await page.screenshot({
    path: "C:/firstproject/.gstack/qa-reports/qa-home-error.png",
  });
  await browser.close();
})();
