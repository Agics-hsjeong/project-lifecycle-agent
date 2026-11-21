/** Hermetic production-UI regressions. No customer API or model jobs. */
import fs from "node:fs";
import path from "node:path";
import assert from "node:assert/strict";
import { fileURLToPath, pathToFileURL } from "node:url";
const website = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const assets = path.resolve(
  process.env.IEUM_TEST_ASSETS || path.join(website, "dist"),
);
const moduleName = process.env.PLAYWRIGHT_MODULE;
const { chromium } = await import(
  moduleName && path.isAbsolute(moduleName)
    ? pathToFileURL(moduleName).href
    : moduleName || "playwright"
);
const report = {
  checks: [],
  errors: [],
  posts: [],
  fixture_only: true,
  customer_reads: 0,
  customer_writes: 0,
};
const check = (value, label) => {
  assert(value, label);
  report.checks.push(label);
};
const snapshot = () => ({
  schema_version: 1,
  mode: "local",
  revision: "rev-old",
  captured_at: new Date().toISOString(),
  project: { id: "observer-regression", name: "관제 회귀 검증" },
  phases: [],
  tasks: [
    {
      id: "TASK-R",
      title: "실제 결과 표시 검증",
      status: "running",
      role: "backend",
      stage: "implementation",
      phase: "03",
      requirement_ids: [],
      depends_on: [],
      artefact_ids: ["ART-R"],
    },
  ],
  artefacts: [{ id: "ART-R", path: "src/example.py", sha256: "old-hash" }],
  agents: [],
  gates: [],
  activity: [],
  commands: [],
  runners: [],
  relations: [],
  logs: [],
  usage: { tokens: null, cost: null },
  capabilities: {},
});
const newer = (raw) => {
  const s = structuredClone(raw);
  s.revision = "rev-new";
  s.captured_at = new Date().toISOString();
  s.tasks[0].status = "done";
  s.artefacts[0].sha256 = "new-hash";
  return s;
};
const deferred = () => {
  let resolve;
  const promise = new Promise((r) => (resolve = r));
  return { promise, resolve };
};
const browser = await chromium.launch({
  headless: true,
  ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH
    ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH }
    : {}),
});
const pages = [];
async function pageFor(
  onSnapshot,
  onDiff,
  demo = false,
  width = 1440,
  initialWait = true,
  onArtefact,
) {
  const p = await browser.newPage({
    viewport: { width, height: 1000 },
    reducedMotion: "reduce",
    timezoneId: "Asia/Seoul",
  });
  pages.push(p);
  p.on("pageerror", (e) => report.errors.push(e.message));
  p.on("request", (r) => {
    if (r.method() === "POST") report.posts.push(new URL(r.url()).pathname);
  });
  await p.route("**/*", async (route) => {
    const u = new URL(route.request().url());
    if (u.pathname === "/api/snapshot") return onSnapshot(route);
    if (u.pathname === "/api/artefact")
      return onArtefact
        ? onArtefact(route)
        : route.fulfill({
            status: 404,
            json: { error: "Synthetic file not connected" },
          });
    if (u.pathname === "/api/diff")
      return onDiff
        ? onDiff(route)
        : route.fulfill({
            json: {
              path: "src/example.py",
              sha256: "old-hash",
              binary: false,
              baseline_available: false,
              content: "OLD_FILE_CONTENT",
            },
          });
    if (u.origin !== "http://127.0.0.1:65501") return route.abort();
    let relative = u.pathname.replace(/^\/project-lifecycle-agent\//, "");
    if (!relative || relative === "workspace/")
      relative = "workspace/index.html";
    const f = path.resolve(assets, relative);
    if (
      !f.startsWith(assets + path.sep) ||
      !fs.existsSync(f) ||
      !fs.statSync(f).isFile()
    )
      return route.fulfill({ status: 404, body: "Missing regression asset" });
    const mime =
      {
        ".html": "text/html",
        ".js": "text/javascript",
        ".css": "text/css",
        ".woff2": "font/woff2",
        ".png": "image/png",
        ".svg": "image/svg+xml",
      }[path.extname(f)] || "application/octet-stream";
    return route.fulfill({ body: fs.readFileSync(f), contentType: mime });
  });
  await p.goto(
    "http://127.0.0.1:65501/project-lifecycle-agent/workspace/" +
      (demo ? "" : "?mode=local#token=regression-fixture"),
    { waitUntil: "domcontentloaded" },
  );
  if (initialWait) await p.locator(".b-task-table").waitFor();
  return p;
}
async function waitFor(p, predicate, label) {
  await p.waitForFunction(predicate);
  check(true, label);
}
const refresh = (p) =>
  p.getByRole("button", { name: "프로젝트 새로고침", exact: true }).click();
async function artifact(p) {
  await p
    .locator(".workspace-tabs button")
    .filter({ hasText: "산출물" })
    .click();
  await p
    .getByRole("button")
    .filter({ hasText: "src/example.py" })
    .first()
    .click();
}
try {
  // A forced refresh must ignore both older successes and failures.
  for (const oldStatus of [200, 503]) {
    const raw = snapshot(),
      updated = newer(raw),
      held = deferred(),
      entered = deferred();
    let calls = 0;
    const p = await pageFor(async (route) => {
      const n = ++calls;
      if (n === 2) {
        entered.resolve();
        await held.promise;
        return route
          .fulfill(
            oldStatus === 200
              ? { json: raw }
              : { status: 503, json: { error: "STALE_FAILURE" } },
          )
          .catch(() => {});
      }
      return route.fulfill({ json: n >= 3 ? updated : raw });
    });
    await refresh(p);
    await entered.promise;
    await refresh(p);
    await waitFor(
      p,
      () =>
        document
          .querySelector(".workspace-footer")
          ?.textContent.includes("rev-new"),
      `newest snapshot applied before old ${oldStatus}`,
    );
    held.resolve();
    await p.waitForTimeout(150);
    check(
      (await p.locator(".overall-progress").innerText()).includes("100%"),
      `old ${oldStatus} cannot regress completion`,
    );
    check(
      !(await p.locator("body").innerText()).includes("STALE_FAILURE"),
      `old ${oldStatus} cannot replace connection state`,
    );
    await p.close();
  }
  // Slow automatic polls remain in flight rather than being cancelled every three seconds.
  {
    const raw = snapshot(),
      updated = newer(raw),
      held = deferred(),
      entered = deferred();
    let calls = 0;
    const p = await pageFor(async (route) => {
      const n = ++calls;
      if (n === 2) {
        entered.resolve();
        await held.promise;
        return route.fulfill({ json: updated });
      }
      return route.fulfill({ json: raw });
    });
    await p.clock.install();
    await refresh(p);
    await entered.promise;
    await p.clock.runFor(9000);
    check(
      calls === 2,
      "automatic polling does not overlap or starve a slow request",
    );
    held.resolve();
    await waitFor(
      p,
      () =>
        document
          .querySelector(".workspace-footer")
          ?.textContent.includes("rev-new"),
      "slow latest response remains usable",
    );
    await p.close();
  }
  // Existing file contents follow actual artefact hash changes without a human click.
  {
    let current = snapshot(),
      diffCalls = 0;
    const p = await pageFor(
      (route) => route.fulfill({ json: current }),
      (route) => {
        diffCalls++;
        return route.fulfill({
          json: {
            path: "src/example.py",
            sha256: current.artefacts[0].sha256,
            binary: false,
            baseline_available: false,
            content:
              current.revision === "rev-new"
                ? "NEW_FILE_CONTENT"
                : "OLD_FILE_CONTENT",
          },
        });
      },
    );
    await artifact(p);
    await p.getByText("OLD_FILE_CONTENT", { exact: false }).first().waitFor();
    current = newer(current);
    await refresh(p);
    await waitFor(
      p,
      () =>
        document
          .querySelector(".diff-content")
          ?.textContent.includes("NEW_FILE_CONTENT"),
      "open Diff refreshes when the selected file SHA changes",
    );
    check(
      diffCalls === 2,
      "file hash change issues exactly one new Diff request",
    );
    await p.close();
  }
  // A delayed old file response must never replace the newer file.
  {
    let current = snapshot(),
      diffCalls = 0;
    const held = deferred(),
      entered = deferred();
    const p = await pageFor(
      (route) => route.fulfill({ json: current }),
      async (route) => {
        if (++diffCalls === 1) {
          entered.resolve();
          await held.promise;
          return route
            .fulfill({
              json: {
                path: "src/example.py",
                sha256: "old-hash",
                content: "OLD_FILE_CONTENT",
                binary: false,
              },
            })
            .catch(() => {});
        }
        return route.fulfill({
          json: {
            path: "src/example.py",
            sha256: "new-hash",
            content: "NEW_FILE_CONTENT",
            binary: false,
          },
        });
      },
    );
    await artifact(p);
    await entered.promise;
    current = newer(current);
    await refresh(p);
    await waitFor(
      p,
      () =>
        document
          .querySelector(".diff-content")
          ?.textContent.includes("NEW_FILE_CONTENT"),
      "new file displayed while old Diff is delayed",
    );
    held.resolve();
    await p.waitForTimeout(150);
    check(
      !(await p.locator(".diff-content").innerText()).includes(
        "OLD_FILE_CONTENT",
      ),
      "late Diff does not overwrite current content",
    );
    await p.close();
  }
  // A busy project ledger is a reachable API, not a failed worker or lost transport.
  {
    const raw = snapshot();
    let calls = 0;
    const p = await pageFor((route) => {
      const n = ++calls;
      return n === 2
        ? route.fulfill({
            status: 409,
            json: { error: "합성 원장 기록 중", code: "conflict" },
          })
        : route.fulfill({ json: n >= 3 ? newer(raw) : raw });
    });
    await p.clock.install();
    const receipt = await p.locator(".last-synced").innerText();
    await refresh(p);
    await p.locator('[data-records-busy="true"]').waitFor();
    check(
      (await p.locator(".connection-state").innerText()).includes(
        "데이터 연결됨",
      ),
      "known busy ledger preserves the existing transport state",
    );
    check(
      (await p.locator(".last-synced").innerText()) === receipt,
      "busy response never invents a newer received timestamp",
    );
    check(
      (await p.getByRole("alert").count()) === 0,
      "busy ledger is a status notice rather than a worker failure",
    );
    await p.clock.runFor(3100);
    await waitFor(
      p,
      () =>
        document
          .querySelector(".workspace-footer")
          ?.textContent.includes("rev-new"),
      "normal next poll recovers after a ledger conflict",
    );
    check(
      (await p.locator('[data-records-busy="true"]').count()) === 0,
      "successful poll clears the temporary ledger notice",
    );
    check(
      calls === 3,
      "ledger busy recovery has no unbounded or extra retry loop",
    );
    await p.close();
  }
  {
    const raw = snapshot();
    let calls = 0;
    const p = await pageFor(
      (route) =>
        ++calls === 1
          ? route.fulfill({
              status: 409,
              json: { error: "합성 최초 원장 경합", code: "conflict" },
            })
          : route.fulfill({ json: raw }),
      undefined,
      false,
      1440,
      false,
    );
    await p
      .getByRole("alert")
      .filter({ hasText: "합성 최초 원장 경합" })
      .waitFor();
    check(
      !(await p.locator(".connection-state").innerText()).includes(
        "데이터 연결됨",
      ),
      "startup ledger conflict does not fabricate a received connection",
    );
    check(
      (await p.locator('[data-records-busy="true"]').count()) === 0,
      "startup without a valid snapshot remains an unknown state",
    );
    await p.clock.install();
    await p.clock.runFor(3100);
    await p.locator(".b-task-table").waitFor();
    check(
      (await p.locator(".connection-state").innerText()).includes(
        "데이터 연결됨",
      ),
      "initial ledger conflict recovers through the normal poll",
    );
    await p.close();
  }
  // A response with the wrong file version is never presented as the current artefact.
  {
    const current = newer(snapshot());
    let correct = false;
    const p = await pageFor(
      (route) => route.fulfill({ json: current }),
      (route) =>
        route.fulfill({
          json: {
            path: "src/example.py",
            sha256: correct ? "new-hash" : "old-hash",
            content: correct ? "NEW_FILE_CONTENT" : "WRONG_VERSION_CONTENT",
            binary: false,
          },
        }),
    );
    await artifact(p);
    await p
      .getByRole("alert")
      .filter({ hasText: "파일이 변경되었습니다" })
      .waitFor();
    check(
      (await p.locator(".diff-content").count()) === 0,
      "mismatched Diff hash is rejected before displaying contents",
    );
    correct = true;
    await p
      .getByRole("button", { name: "산출물 다시 불러오기", exact: true })
      .click();
    await waitFor(
      p,
      () =>
        document
          .querySelector(".diff-content")
          ?.textContent.includes("NEW_FILE_CONTENT"),
      "Diff retry recovers to the verified file version",
    );
    await p.close();
  }
  // Untrusted imports and malformed live data preserve the last valid workspace.
  {
    const p = await pageFor(
      (route) => route.fulfill({ json: snapshot() }),
      undefined,
      true,
    );
    const before = await p.locator(".sidebar-project").innerText();
    const bad = snapshot();
    bad.project.name = { invalid: true };
    await p.locator("input[type=file]").setInputFiles({
      name: "bad-snapshot.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify(bad)),
    });
    await waitFor(
      p,
      () => document.body.textContent.includes("문자열이어야"),
      "malformed import is rejected with an explanation",
    );
    check(
      (await p.locator(".sidebar-project").innerText()) === before,
      "invalid import retains the last valid project",
    );
    check(
      (await p.locator(".b-task-table").count()) === 1,
      "invalid import never blanks the workspace",
    );
    await p.close();
  }
  {
    let current = snapshot(),
      fail = false;
    const p = await pageFor((route) =>
      fail
        ? route.fulfill({ status: 503, json: { error: "합성 연결 끊김" } })
        : route.fulfill({ json: current }),
    );
    fail = true;
    await refresh(p);
    await p.getByRole("alert").filter({ hasText: "합성 연결 끊김" }).waitFor();
    check(
      (await p.locator(".b-task-table").count()) === 1,
      "disconnection keeps last known tasks",
    );
    fail = false;
    current = newer(current);
    await refresh(p);
    await waitFor(
      p,
      () =>
        document
          .querySelector(".workspace-footer")
          ?.textContent.includes("rev-new"),
      "connection recovery updates actual completion",
    );
    check(
      (await p.locator(".overall-progress").innerText()).includes("100%"),
      "recovery shows actual completed count",
    );
    await p.close();
  }
  {
    let current = snapshot();
    const p = await pageFor((route) => route.fulfill({ json: current }));
    current = structuredClone(current);
    current.tasks[0].title = { invalid: true };
    await refresh(p);
    await p.getByRole("alert").filter({ hasText: "문자열이어야" }).waitFor();
    check(
      (await p.locator(".b-task-table").innerText()).includes(
        "실제 결과 표시 검증",
      ),
      "malformed live snapshot preserves the last valid task display",
    );
    check(
      (await p.locator(".connection-state").innerText()).includes(
        "연결 확인 필요",
      ),
      "invalid live data does not claim a healthy transport",
    );
    current = newer(snapshot());
    current.activity = [
      {
        id: "event-real",
        seq: 1,
        at: new Date().toISOString(),
        task_id: "TASK-R",
        role: "backend",
        kind: "completed",
        provider: "codex",
        summary: "NEW_NATIVE_ACTIVITY",
      },
    ];
    await refresh(p);
    await waitFor(
      p,
      () => document.body.textContent.includes("NEW_NATIVE_ACTIVITY"),
      "newly received native activity is actually visible",
    );
    await p.close();
  }
  {
    let calls = 0;
    const held = deferred(),
      entered = deferred();
    const raw = snapshot();
    const p = await pageFor(async (route) => {
      if (++calls === 2) {
        entered.resolve();
        await held.promise;
        return route.fulfill({ json: raw }).catch(() => {});
      }
      return route.fulfill({ json: calls > 2 ? newer(raw) : raw });
    });
    await p.clock.install();
    await refresh(p);
    await entered.promise;
    await p.clock.runFor(15100);
    await p
      .getByRole("alert")
      .filter({ hasText: "응답 시간이 초과" })
      .waitFor();
    check(
      (await p.locator(".b-task-table").count()) === 1,
      "poll deadline reports an error and preserves known tasks",
    );
    await refresh(p);
    await waitFor(
      p,
      () =>
        document
          .querySelector(".workspace-footer")
          ?.textContent.includes("rev-new"),
      "manual refresh recovers after a timed out request",
    );
    held.resolve();
    await p.close();
  }
  {
    let current = snapshot();
    current.host = {
      connected: false,
      runner_connected: false,
      supervisor: {
        configured: true,
        provider: "codex",
        status: "retrying",
        attempts: 1,
        next_retry_seconds: 2,
        last_error: "합성 소유권 확인",
      },
    };
    const p = await pageFor((route) => route.fulfill({ json: current }));
    await p.locator('[data-supervisor-state="retrying"]').waitFor();
    check(
      (await p.locator(".connection-state").innerText()).includes(
        "데이터 연결됨",
      ),
      "API transport is labelled separately from automatic execution",
    );
    current.host.supervisor.status = "failed";
    await refresh(p);
    await p.locator('[data-supervisor-state="failed"]').waitFor();
    check(
      (await p.locator("body").innerText()).includes("시작되지 않았습니다"),
      "supervisor startup failure remains visible despite HTTP success",
    );
    current.host.runner_connected = true;
    current.host.connected = true;
    current.runners = [
      {
        id: "runner-existing",
        provider: "codex",
        status: "waiting",
        connected: true,
      },
    ];
    await refresh(p);
    await waitFor(
      p,
      () =>
        document.body.textContent.includes(
          "기존 자동 실행기 연결은 유지됩니다",
        ),
      "failed additional supervisor does not mark an existing worker disconnected",
    );
    current.host.supervisor = { configured: false, status: "disabled" };
    await refresh(p);
    await p.waitForTimeout(100);
    check(
      (await p.locator("[data-supervisor-state]").count()) === 0,
      "observer-only mode needs no execution warning",
    );
    await p.close();
  }
  {
    const p = await pageFor(
      (route) => route.fulfill({ json: snapshot() }),
      undefined,
      false,
      390,
    );
    await p
      .locator(".b-task-table [data-task-id]")
      .first()
      .getByRole("button")
      .first()
      .click();
    await p.locator(".inspector").waitFor();
    await p.keyboard.press("Escape");
    check(
      (await p.locator(".inspector").count()) === 0,
      "mobile detail remains dismissible after state fixes",
    );
    check(
      await p.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      "mobile workspace has no page overflow",
    );
    await p.close();
  }
  // Scope, execution source and verification semantics must agree in the real rendered UI.
  for (const width of [1440, 390, 320]) {
    const raw = snapshot();
    raw.host = {
      connected: true,
      execution_mode: "native",
      runner_connected: false,
    };
    raw.tasks = [
      {
        ...raw.tasks[0],
        id: "DONE",
        title: "검증된 완료",
        status: "done",
        verification: { status: "passed", checks: 1, source: "supervisor" },
      },
      {
        ...raw.tasks[0],
        id: "OMIT",
        title: "계약 범위 제외",
        status: "skipped",
      },
      {
        ...raw.tasks[0],
        id: "FAIL",
        title: "실패 확인",
        status: "failed",
        verification: { status: "failed", checks: 1 },
      },
    ];
    raw.usage = {
      tokens: null,
      cost: null,
      known_tokens: 100,
      known_cost_usd: 0.1,
      coverage: "partial",
      total_attempts: 2,
      observed_attempts: 1,
      unreported_attempts: 1,
    };
    const p = await pageFor(
      (route) => route.fulfill({ json: raw }),
      null,
      false,
      width,
    );
    check(
      (await p.locator(".overall-progress").innerText()).includes("1 / 2 완료"),
      `${width}: excluded tasks are not counted as complete`,
    );
    check(
      (await p.locator(".overall-progress").innerText()).includes("50%"),
      `${width}: applicable completion is fifty percent`,
    );
    check(
      (await p.locator(".overall-progress").innerText()).includes("0 대기"),
      `${width}: failed task is not ordinary waiting`,
    );
    check(
      (await p.locator(".execution-hud").innerText()).includes(
        "현재 호스트의 활동 보고",
      ),
      `${width}: native observation is not missing CLI failure`,
    );
    check(
      (await p.locator(".execution-hud").innerText()).includes("미집계 1회"),
      `${width}: observed subtotal is visibly partial`,
    );
    if (process.env.IEUM_TEST_SCREENSHOTS) {
      fs.mkdirSync(process.env.IEUM_TEST_SCREENSHOTS, { recursive: true });
      await p.screenshot({
        path: path.join(
          process.env.IEUM_TEST_SCREENSHOTS,
          `observer-${width}.png`,
        ),
        fullPage: true,
      });
    }
    await p
      .locator('.b-task-row[data-task-id="DONE"]')
      .getByRole("button")
      .first()
      .click();
    if (width >= 800)
      await p
        .getByRole("button", { name: "작업 상세 보기", exact: true })
        .click();
    const first = p.getByRole("tab", { name: "실행 활동", exact: true });
    await first.focus();
    await p.keyboard.press("ArrowRight");
    check(
      (await p
        .getByRole("tab", { name: "파일·Diff", exact: true })
        .getAttribute("aria-selected")) === "true",
      `${width}: inspector arrow activates adjacent tab`,
    );
    check(
      (await p.locator('[role="tab"][tabindex="0"]').count()) === 1,
      `${width}: inspector uses roving tab stop`,
    );
    check(
      (await p.getByRole("tabpanel").getAttribute("aria-labelledby")) ===
        "inspector-tab-diff",
      `${width}: panel is named by selected tab`,
    );
    await p.keyboard.press("End");
    await p
      .locator('#inspector-tab-guidelines[aria-selected="true"]')
      .waitFor();
    check(
      (await p
        .getByRole("tab", { name: "관찰 안내", exact: true })
        .getAttribute("aria-selected")) === "true",
      `${width}: inspector End works`,
    );
    await p.keyboard.press("Home");
    await p.locator('#inspector-tab-activity[aria-selected="true"]').waitFor();
    check(
      (await first.getAttribute("aria-selected")) === "true",
      `${width}: inspector Home works`,
    );
    await p.keyboard.press("Escape");
    await p.locator(".inspector").waitFor({ state: "detached" });
    check(
      (await p.locator(".inspector").count()) === 0,
      `${width}: inspector Escape still closes`,
    );
    check(
      await p.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      `${width}: quality HUD and filters do not overflow`,
    );
    await p.close();
  }
  {
    const raw = snapshot();
    raw.tasks[0].status = "skipped";
    const p = await pageFor((route) => route.fulfill({ json: raw }));
    check(
      (await p.locator(".overall-progress").innerText()).includes(
        "적용 대상 없음",
      ),
      "all-excluded scope has no fake completion percentage",
    );
    check(
      (await p.locator('.overall-progress [role="progressbar"]').count()) === 0,
      "no applicable tasks are not an indeterminate progress bar",
    );
    await p.close();
  }
  // Local delivery uses the exact registered version with a header-only credential.
  {
    const raw = snapshot();
    raw.tasks[0].status = "done";
    raw.artefacts[0] = {
      id: "ART-R",
      path: "docs/result.png",
      sha256: "a".repeat(64),
      exists: true,
    };
    raw.tasks[0].artefact_ids = ["ART-R"];
    const png = fs.readFileSync(path.join(website, "public/brand/symbol.png"));
    let requests = 0,
      stale = false,
      unsafe = false;
    const p = await pageFor(
      (route) => route.fulfill({ json: raw }),
      (route) =>
        route.fulfill({
          json: {
            path: "docs/result.png",
            sha256: "a".repeat(64),
            binary: true,
          },
        }),
      false,
      1440,
      true,
      (route) => {
        requests++;
        const u = new URL(route.request().url());
        check(
          u.searchParams.get("id") === "ART-R" &&
            u.searchParams.get("sha256") === "a".repeat(64),
          "delivery is bound to registered ID and SHA",
        );
        check(
          route.request().headers().authorization ===
            "Bearer regression-fixture" &&
            !u.href.includes("regression-fixture"),
          "delivery token stays in authorization header",
        );
        return stale
          ? route.fulfill({
              status: 409,
              json: { error: "Synthetic file version changed" },
            })
          : unsafe
            ? route.fulfill({
                contentType: "image/svg+xml",
                body: '<svg xmlns="http://www.w3.org/2000/svg"><text>Unsafe inline format</text></svg>',
              })
            : route.fulfill({ contentType: "image/png", body: png });
      },
    );
    await p
      .locator(".workspace-tabs button")
      .filter({ hasText: "산출물" })
      .click();
    await p
      .getByRole("button")
      .filter({ hasText: "result.png" })
      .first()
      .click();
    await p.getByRole("tab", { name: "파일·Diff", exact: true }).click();
    await p.getByRole("button", { name: "미리보기", exact: true }).click();
    await p.locator(".artefact-preview img").waitFor();
    await p.waitForFunction(
      () => document.querySelector(".artefact-preview img")?.naturalWidth > 0,
    );
    check(
      await p
        .locator(".artefact-preview img")
        .getAttribute("src")
        .then((x) => x.startsWith("blob:")),
      "static image preview is created from authenticated bytes",
    );
    const download = p.waitForEvent("download");
    await p.getByRole("button", { name: "다운로드", exact: true }).click();
    const received = await download;
    check(
      received.suggestedFilename() === "result.png",
      "file delivery uses the current registered filename",
    );
    unsafe = true;
    await p.getByRole("button", { name: "미리보기", exact: true }).click();
    await p.getByRole("alert").filter({ hasText: "HTML·SVG" }).waitFor();
    check(
      (await p.locator(".artefact-preview").count()) === 0,
      "active or mislabeled SVG never becomes inline preview",
    );
    unsafe = false;
    stale = true;
    await p.getByRole("button", { name: "미리보기", exact: true }).click();
    await p
      .getByRole("alert")
      .filter({ hasText: "Synthetic file version changed" })
      .waitFor();
    check(
      (await p.locator(".artefact-preview").count()) === 0,
      "stale version cannot retain an old preview",
    );
    check(requests === 4, "read-only file actions issue only requested GETs");
    await p.close();
  }
  check(
    report.errors.length === 0,
    "all regressions have zero browser exceptions",
  );
  check(
    report.posts.length === 0,
    "all regressions are read-only with zero POST requests",
  );
  console.log(
    JSON.stringify({
      passed: true,
      checks: report.checks.length,
      errors: report.errors,
      posts: report.posts,
    }),
  );
} catch (error) {
  console.error(error);
  process.exitCode = 1;
} finally {
  for (const p of pages) if (!p.isClosed()) await p.close();
  await browser.close();
  if (process.env.IEUM_TEST_REPORT) {
    const target = path.resolve(process.env.IEUM_TEST_REPORT);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, JSON.stringify(report, null, 2));
  }
}
