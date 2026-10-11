import { test, expect, type Page } from "@playwright/test";

const id = "aabbccdd-1234-4321-9123-123456789abc";
const doc = { id, name: "Libro de prueba", size: 1200, created_at: "2026-10-09T10:00:00Z", status: "ready", page_count: 3, error: null, embedding_error: null, summary: null, category: null, summary_error: null, summary_requested: false };
const account = { mode: "account", login_available: true, account_url: "https://auth.ici-labs.com/account", user: { id: "user-a", name: "Persona de prueba", email: "test@example.invalid" } };
function blankPdf() {
  const objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R] /Count 3 >>", ...Array(3).fill("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Resources << >> >>")];
  let body = "%PDF-1.4\n";
  const offsets = [0];
  for (const [index, object] of objects.entries()) { offsets.push(Buffer.byteLength(body)); body += `${index + 1} 0 obj\n${object}\nendobj\n`; }
  const xref = Buffer.byteLength(body);
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n` + offsets.slice(1).map(n => `${String(n).padStart(10, "0")} 00000 n \n`).join("");
  return Buffer.from(body + `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`);
}
async function mockLibrary(page: Page, initial: typeof account | { mode: string; login_available: boolean }) {
  const state = { session: initial, status: 200, reads: 0, documents: [doc] };
  await page.route("**/session/me", route => route.fulfill({ status: state.status, json: state.status === 200 ? state.session : { error: "Acceso temporalmente no disponible" } }));
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    state.reads++;
    if (path.endsWith("/file")) return route.fulfill({ contentType: "application/pdf", body: blankPdf() });
    if (path === "/api/analytics/preferences") return route.fulfill({ json: { enabled: false, available: false, retention_days: 90 } });
    if (path === "/api/documents") return route.fulfill({ json: { items: state.documents, next: null, stats: { total: state.documents.length, pending: 0, bytes: 1200 } } });
    if (path === `/api/documents/${id}` && state.documents.length) return route.fulfill({ json: doc });
    return route.fulfill({ status: 404, json: { error: "No existe" } });
  });
  return state;
}

test("login keeps the requested document without reading it anonymously", async ({ page }) => {
  const state = await mockLibrary(page, { mode: "choose", login_available: true });
  const destination = `/?document=${id}&page=3&panel=chat#main`;
  await page.goto(destination);
  const link = page.getByRole("link", { name: "Iniciar sesión →" });
  await expect(link).toBeVisible();
  const login = new URL((await link.getAttribute("href"))!, "http://localhost");
  expect(login.searchParams.get("return_to")).toBe(destination);
  expect(state.reads).toBe(0);
});

test("the same central profile and account controls fit on mobile", async ({ page }) => {
  await mockLibrary(page, account);
  await page.goto("/");
  await expect(page.getByText("◉ Persona de prueba")).toBeVisible();
  await expect(page.getByRole("link", { name: "Mi cuenta SARA ↗" })).toHaveAttribute("href", account.account_url);
  await expect(page.getByRole("button", { name: "Salir de SmartDoc" })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/auth-account-mobile.png", fullPage: true });
});

test("returning from login restores document, page and panel", async ({ page }) => {
  await mockLibrary(page, account);
  await page.goto(`/?document=${id}&page=3&panel=chat`);
  await expect(page.getByRole("dialog", { name: `Lector de ${doc.name}` })).toBeVisible();
  await expect(page.getByText("Página 3 de 3")).toBeVisible();
  await expect(page.getByLabel("Tu pregunta")).toBeVisible();
  await page.getByRole("button", { name: "← Biblioteca" }).click();
  await expect(page).toHaveURL("http://127.0.0.1:4174/");
});

test("global revocation hides private content and keeps the return destination", async ({ page }) => {
  const state = await mockLibrary(page, account);
  await page.goto(`/?document=${id}&page=3&panel=chat`);
  await expect(page.getByText("Página 3 de 3")).toBeVisible();
  state.status = 401;
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByText(doc.name, { exact: true })).toHaveCount(0);
  const login = page.getByRole("link", { name: "Iniciar sesión →" });
  await expect(login).toBeVisible();
  expect(new URL((await login.getAttribute("href"))!, "http://localhost").searchParams.get("return_to")).toContain(`document=${id}`);
});

test("temporary identity failure preserves the open document", async ({ page }) => {
  const state = await mockLibrary(page, account);
  await page.goto(`/?document=${id}&page=3&panel=chat`);
  await expect(page.getByText("Página 3 de 3")).toBeVisible();
  state.status = 503;
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText("Acceso temporalmente no disponible");
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByText("Página 3 de 3")).toBeVisible();
});

test("a pending comparison cannot appear after changing account", async ({ page }) => {
  const state = await mockLibrary(page, account);
  const other = { ...doc, id: "bbccddee-1234-4321-9123-123456789abc", name: "Otro libro" };
  state.documents = [doc, other];
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let started = false;
  await page.route("**/api/similarity", async route => {
    started = true;
    await gate;
    await route.fulfill({ json: { documents: [doc, other], pairs: [{ id, other_id: other.id, similarity: 0.83 }] } }).catch(() => {});
  });
  await page.goto("/");
  await page.getByLabel(`Seleccionar ${doc.name}`, { exact: true }).check();
  await page.getByLabel(`Seleccionar ${other.name}`, { exact: true }).check();
  await page.getByRole("button", { name: "Comparar 2 documentos" }).click();
  await expect.poll(() => started).toBe(true);
  state.documents = [];
  state.session = { ...account, user: { ...account.user, id: "user-b", name: "Cuenta B" } };
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect(page.getByText("◉ Cuenta B")).toBeVisible();
  release();
  await expect(page.getByText(doc.name, { exact: true })).toHaveCount(0);
  await expect(page.getByText(other.name, { exact: true })).toHaveCount(0);
});

test("session revocation cancels transfers and stops the remaining upload queue", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const state = await mockLibrary(page, account);
  let uploads = 0;
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/documents", async route => {
    if (route.request().method() !== "POST") return route.fallback();
    uploads++;
    await gate;
    await route.fulfill({ json: { document: doc, duplicate: false } }).catch(() => {});
  });
  await page.goto("/");
  await expect(page.getByText("◉ Persona de prueba")).toBeVisible();
  await page.getByLabel("Seleccionar documentos PDF").setInputFiles(["uno", "dos", "tres"].map(name => ({ name: `${name}.pdf`, mimeType: "application/pdf", buffer: blankPdf() })));
  await expect.poll(() => uploads).toBe(2);
  state.status = 401;
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect(page.getByRole("link", { name: "Iniciar sesión →" })).toBeVisible();
  release();
  await page.waitForTimeout(250);
  expect(uploads).toBe(2);
  expect(errors).toEqual([]);
});
