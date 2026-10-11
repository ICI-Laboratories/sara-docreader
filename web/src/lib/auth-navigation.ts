export function readerLocation(href: string) {
  const url = new URL(href);
  const id = url.searchParams.get("document");
  if (!id || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) return null;
  const page = Number(url.searchParams.get("page") || 1);
  return {
    id,
    page: Number.isSafeInteger(page) && page > 0 ? page : 1,
    panel: url.searchParams.get("panel") === "chat" ? "chat" as const : "info" as const,
  };
}

export function readerDestination(href: string, id: string | null, page: number, panel: "info" | "chat") {
  const url = new URL(href);
  for (const key of ["document", "page", "panel"]) url.searchParams.delete(key);
  if (id) {
    url.searchParams.set("document", id);
    url.searchParams.set("page", String(page));
    url.searchParams.set("panel", panel);
  }
  return url.pathname + url.search + url.hash;
}
