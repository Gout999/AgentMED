import { expect, test } from "@playwright/test";

test("renders a real complaint event through Vite, FastAPI, and PostgreSQL", async ({
  page,
  request,
}, testInfo) => {
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  const externalId = `console-real-stack-${Date.now()}`;
  const title = "Console real-stack integration case";
  const created = await request.post("/api/v1/complaints", {
    data: {
      source: "webhook",
      text: "Playwright should render this authoritative complaint event.",
      external_id: externalId,
      channel: "feishu-mock:contract-replay:console-e2e",
      complainant_ref: "console-e2e-user",
      title,
    },
  });
  expect(created.ok()).toBe(true);
  const createdBody = (await created.json()) as { case_id: string };
  expect(createdBody.case_id).toMatch(/^case_[0-9A-Za-z]{8,64}$/);

  const caseId = createdBody.case_id;
  const secondTitle = "Console request-key route switch case";
  const secondCreated = await request.post("/api/v1/complaints", {
    data: {
      source: "webhook",
      text: "Route changes must abort stale reads and load this second case.",
      external_id: `${externalId}-second`,
      channel: "feishu-mock:contract-replay:console-e2e",
      complainant_ref: "console-e2e-user-2",
      title: secondTitle,
    },
  });
  expect(secondCreated.ok()).toBe(true);
  const secondBody = (await secondCreated.json()) as { case_id: string };

  await page.goto(`/cases/${encodeURIComponent(caseId)}`, { waitUntil: "networkidle" });

  await expect(page.getByText(caseId, { exact: true }).first()).toBeVisible();
  await expect(page.getByText(title, { exact: true })).toBeVisible();
  const timeline = page.getByRole("list", { name: "Case 事件时间线" });
  const receivedEvent = timeline.getByRole("listitem").filter({
    has: page.getByText("complaint.received", { exact: true }),
  });
  await expect(receivedEvent).toHaveCount(1);
  await expect(receivedEvent.getByText("seq 1", { exact: true })).toBeVisible();
  await expect(receivedEvent.getByText("complaint.received", { exact: true })).toBeVisible();
  await expect(receivedEvent.getByText("actor controller:case", { exact: true })).toBeVisible();
  await expect(receivedEvent.getByText(`inline:${caseId}`, { exact: false })).toBeVisible();
  await expect(page.getByText("数据待接入", { exact: false })).toHaveCount(0);
  const caseDetailScreenshot = await page.screenshot({ fullPage: true });

  await page.evaluate((nextCaseId) => {
    window.history.pushState({}, "", `/cases/${encodeURIComponent(nextCaseId)}`);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, secondBody.case_id);
  await expect(page.getByText(secondBody.case_id, { exact: true }).first()).toBeVisible();
  await expect(page.getByText(secondTitle, { exact: true })).toBeVisible();
  await expect(page.getByText(caseId, { exact: true })).toHaveCount(0);

  await page.goto("/", { waitUntil: "networkidle" });
  await expect(page.getByText(title, { exact: true })).toBeVisible();
  await expect(page.getByText(secondTitle, { exact: true })).toBeVisible();

  const eventsResponse = await request.get(`/api/v1/cases/${encodeURIComponent(caseId)}/events`);
  expect(eventsResponse.ok()).toBe(true);
  const events = (await eventsResponse.json()) as {
    items: Array<{
      seq: number;
      event_type: string;
      actor: string;
      payload: { text_ref?: string };
    }>;
  };
  expect(events.items[0]).toMatchObject({
    seq: 1,
    event_type: "complaint.received",
    actor: "controller:case",
    payload: { text_ref: `inline:${caseId}` },
  });
  expect(browserErrors).toEqual([]);

  await testInfo.attach("real-case.json", {
    body: Buffer.from(JSON.stringify({
      case_id: caseId,
      route_switch_case_id: secondBody.case_id,
      events: events.items,
    }, null, 2)),
    contentType: "application/json",
  });
  await testInfo.attach("case-detail.png", {
    body: caseDetailScreenshot,
    contentType: "image/png",
  });
});
