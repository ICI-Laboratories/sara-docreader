export type Document = {
  id: string;
  name: string;
  size: number;
  created_at: string;
  status: "queued" | "processing" | "ready" | "failed";
  page_count: number | null;
  error: string | null;
  embedding_error: string | null;
  summary: string | null;
  category: string | null;
  summary_error: string | null;
  summary_requested: boolean;
};
export type Hit = {
  id: string;
  name: string;
  page: number;
  content: string;
  score: number;
};
export type Source = { id: string; name: string; page: number; text: string };
export type Catalog = {
  items: Document[];
  next: { before: string; before_id: string } | null;
  stats: { total: number; pending: number; bytes: number };
};
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export function csrf() {
  return decodeURIComponent(
    document.cookie
      .split("; ")
      .find((c) => c.startsWith("smartdoc_csrf="))
      ?.split("=")
      .slice(1)
      .join("=") ?? "",
  );
}
export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.method && options.method !== "GET")
    headers.set("X-CSRF-Token", csrf());
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new ApiError(
      response.status,
      typeof payload.detail === "string"
        ? payload.detail
        : payload.error || "No se pudo completar la solicitud.",
    );
  }
  return response.json();
}
export function uploadFile(
  file: File,
  progress: (n: number) => void,
  signal?: AbortSignal,
): Promise<{ document: Document; duplicate: boolean }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let settled = false;
    const cleanup = () => {
      settled = true;
      signal?.removeEventListener("abort", abort);
      xhr.upload.onprogress = null;
      xhr.onload = xhr.onerror = xhr.onabort = null;
    };
    const fail = (error: unknown) => {
      if (settled) return;
      cleanup();
      reject(error);
    };
    const abort = () => {
      fail(new DOMException("La subida fue cancelada.", "AbortError"));
      xhr.abort();
    };
    if (signal?.aborted) {
      abort();
      return;
    }
    signal?.addEventListener("abort", abort, { once: true });
    xhr.upload.onprogress = (e) => {
      if (!settled && e.lengthComputable) progress(Math.round((100 * e.loaded) / e.total));
    };
    xhr.onerror = () =>
      fail(
        new Error(
          "Se interrumpió la conexión. Puedes volver a subir el archivo.",
        ),
      );
    xhr.onabort = () => fail(new DOMException("La subida fue cancelada.", "AbortError"));
    xhr.onload = () => {
      if (settled) return;
      if (xhr.status < 200 || xhr.status >= 300) {
        let result: { detail?: string; error?: string } = {};
        try { result = JSON.parse(xhr.responseText); } catch { /* Keep the HTTP status even for a proxy error page. */ }
        fail(new ApiError(xhr.status, result.detail || result.error || "No se pudo subir el archivo."));
        return;
      }
      try {
        const result = JSON.parse(xhr.responseText);
        cleanup();
        resolve(result);
      } catch {
        fail(new Error("El servidor no devolvió una respuesta válida."));
      }
    };
    try {
      xhr.open("POST", "/api/documents");
      xhr.setRequestHeader("X-CSRF-Token", csrf());
      const data = new FormData();
      data.append("file", file);
      xhr.send(data);
    } catch (error) { fail(error); }
  });
}
export const states = {
  queued: "En cola",
  processing: "Procesando",
  ready: "Listo",
  failed: "Necesita revisión",
};
export function bytes(value: number) {
  return value < 1048576
    ? `${Math.round(value / 1024)} KB`
    : `${(value / 1048576).toFixed(1)} MB`;
}
