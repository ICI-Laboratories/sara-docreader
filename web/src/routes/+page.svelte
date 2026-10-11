<script lang="ts">
  import { onMount, tick } from "svelte";
  import { excerpt } from "$lib/search";
  import { readerDestination, readerLocation } from "$lib/auth-navigation";
  import PdfViewer from "$lib/PdfViewer.svelte";
  import Answer from "$lib/Answer.svelte";
  import { track, setMeasurement } from "$lib/analytics";
  let measurement = $state(true),
    measurementAvailable = $state(true),
    savingPreference = $state(false);
  let retentionDays = $state(90);
  let feedback = $state<"positive" | "negative" | null>(null);
  import {
    request,
    uploadFile,
    csrf,
    ApiError,
    states,
    bytes,
    type Document,
    type Catalog,
    type Hit,
    type Source,
  } from "$lib/api";
  let accessMode = $state("choose"),
    loginAvailable = $state(false),
    expiresIn = $state(86400);
  let leaving = $state(false);
  let accountUrl = $state(""), returnPath = $state("/");
  let user = $state<{ id: string; name?: string | null; email?: string | null } | null>(null);
  let locationReady = $state(false);
  let verifyingSession = false;
  type Session = { mode: string; login_available: boolean; account_url?: string;
    user?: { id: string; name?: string | null; email?: string | null };
    expires_in?: number; ephemeral_hours?: number };
  const loginHref = $derived(`/auth/login?${new URLSearchParams({ return_to: returnPath })}`);
  let sessionReady = $state(false),
    checking = $state(true),
    error = $state(""),
    notice = $state("");
  let catalog = $state<Catalog>({
    items: [],
    next: null,
    stats: { total: 0, pending: 0, bytes: 0 },
  });
  let selected = $state<Document | null>(null),
    page = $state(1),
    panel = $state<"info" | "chat">("info");
  let picked = $state<string[]>([]),
    extraDocs = $state<string[]>([]),
    comparing = $state(false);
  let comparison = $state<{
    documents: { id: string; name: string }[];
    pairs: { id: string; other_id: string; similarity: number }[];
  } | null>(null);
  let query = $state(""),
    searchMode = $state<"name" | "text" | "exact" | "hybrid">("name"),
    searching = $state(false),
    results = $state<Hit[] | null>(null),
    retrievalMode = $state("");
  let nextOffset = $state<number | null>(null), searchTotal = $state<number | null>(null);
  let chatDocument = $state<Document | null>(null);
  let turns = $state<{ question: string; answer: string; sources: Source[] }[]>([]);
  let lastQuestion = $state(""), completedAnswer = $state(false);
  let filter = $state("all"),
    dark = $state(false),
    dragging = $state(false);
  let uploads = $state<{ name: string; progress: number; state: string }[]>([]);
  let question = $state(""),
    answer = $state(""),
    sources = $state<Source[]>([]),
    asking = $state(false),
    chatError = $state("");
  let fileInput = $state<HTMLInputElement>(undefined!);
  let searchController: AbortController | undefined;
  let chatController: AbortController | undefined;
  let searchTimer: ReturnType<typeof setTimeout>;
  let lastRequest = 0;
  let navigationRequest = 0;
  // Every asynchronous result belongs to the session that started it.
  let sessionEpoch = 0;
  const uploadControllers = new Set<AbortController>();
  let readerElement = $state<HTMLDivElement>();
  let returnFocus: HTMLElement | null = null;
  let visible = $derived(
    catalog.items.filter(
      (d) =>
        filter === "all" ||
        (filter === "pending"
          ? ["queued", "processing"].includes(d.status)
          : d.status === "failed"),
    ),
  );
  function currentSession(epoch: number) {
    return epoch === sessionEpoch && sessionReady && !leaving;
  }
  function handleError(e: unknown, epoch = sessionEpoch) {
    if (epoch !== sessionEpoch || (e as Error).name === "AbortError") return;
    if (e instanceof ApiError && e.status === 401) {
      sessionReady = false;
      user = null;
      clearPrivateState();
      checking = false;
      leaving = false;
    }
    error = (e as Error).message;
  }
  function invalidateRequests() {
    ++sessionEpoch;
    clearTimeout(searchTimer);
    searchController?.abort();
    searchController = undefined;
    chatController?.abort();
    chatController = undefined;
    for (const controller of uploadControllers) controller.abort();
    uploadControllers.clear();
    ++lastRequest;
    ++navigationRequest;
    verifyingSession = false;
    searching = false;
    asking = false;
    comparing = false;
    savingPreference = false;
  }
  function clearPrivateState() {
    invalidateRequests();
    locationReady = false;
    selected = null;
    page = 1;
    panel = "info";
    returnFocus = null;
    chatDocument = null;
    turns = [];
    answer = "";
    question = "";
    sources = [];
    comparison = null;
    results = null;
    query = "";
    picked = [];
    extraDocs = [];
    uploads = [];
    lastQuestion = "";
    completedAnswer = false;
    chatError = "";
    feedback = null;
    nextOffset = null;
    searchTotal = null;
    retrievalMode = "";
    notice = "";
    measurement = false;
    measurementAvailable = false;
    setMeasurement(false);
    catalog = {items: [], next: null, stats: {total: 0, pending: 0, bytes: 0}};
  }
  $effect(() => {
    if (!sessionReady || !locationReady) return;
    const destination = readerDestination(window.location.href, selected?.id ?? null, page, panel);
    window.history.replaceState(window.history.state, "", destination);
    returnPath = destination;
  });
  async function verifySession() {
    if (!sessionReady || checking || verifyingSession || leaving) return;
    const epoch = sessionEpoch;
    verifyingSession = true;
    try {
      const current = await request<Session>("/session/me");
      if (!currentSession(epoch)) return;
      if (current.mode !== accessMode || current.user?.id !== user?.id) {
        await startSession();
      } else {
        user = current.user ?? null;
        accountUrl = current.account_url ?? accountUrl;
      }
    } catch (e) { handleError(e, epoch); }
    finally { if (epoch === sessionEpoch) verifyingSession = false; }
  }
  async function load(more = false, refresh = false) {
    const epoch = sessionEpoch;
    if (!currentSession(epoch)) return;
    const id = ++lastRequest;
    const params = new URLSearchParams({
      q: searchMode === "name" ? query : "",
      status: filter,
    });
    if (more && catalog.next) {
      params.set("before", catalog.next.before);
      params.set("before_id", catalog.next.before_id);
    }
    try {
      const data = await request<Catalog>(`/api/documents?${params}`);
      if (!currentSession(epoch) || id !== lastRequest) return;
      if (refresh) {
        const fresh = new Map(data.items.map((doc) => [doc.id, doc]));
        const pending = catalog.items.filter((doc) => !fresh.has(doc.id) && (doc.status !== "ready" || doc.summary_requested));
        const updated = await Promise.all(pending.map((doc) => request<Document>(`/api/documents/${doc.id}`)));
        if (!currentSession(epoch) || id !== lastRequest) return;
        for (const doc of updated) fresh.set(doc.id, doc);
        const known = new Set(catalog.items.map((doc) => doc.id));
        catalog = { ...data, next: catalog.next, items: [
          ...data.items.filter((doc) => !known.has(doc.id)),
          ...catalog.items.map((doc) => fresh.get(doc.id) ?? doc),
        ] };
      } else catalog = more
        ? { ...data, items: [...catalog.items, ...data.items] }
        : data;
      if (selected) {
        const selectedId = selected.id;
        const detail =
          data.items.find((d) => d.id === selectedId) ??
          (await request<Document>(`/api/documents/${selectedId}`));
        if (!currentSession(epoch) || id !== lastRequest) return;
        if (selected?.id === selectedId) selected = detail;
        if (chatDocument?.id === selectedId) chatDocument = detail;
      }
      if (chatDocument && chatDocument.id !== selected?.id) {
        const contextId = chatDocument.id;
        const detail = data.items.find((d) => d.id === contextId) ??
          (chatDocument.status !== "ready" ? await request<Document>(`/api/documents/${contextId}`) : null);
        if (currentSession(epoch) && id === lastRequest && detail && chatDocument?.id === contextId) chatDocument = detail;
      }
    } catch (e) {
      if (id === lastRequest) handleError(e, epoch);
    }
  }
  async function doSearch(more = false) {
    const epoch = sessionEpoch;
    if (!currentSession(epoch)) return;
    clearTimeout(searchTimer);
    searchController?.abort();
    searching = false;
    if (!more) { nextOffset = null; searchTotal = null; results = null; }
    error = "";
    if (searchMode === "name" || !query.trim()) {
      results = null;
      await load();
      return;
    }
    const controller = new AbortController();
    searchController = controller;
    searching = true;
    try {
      const data = await request<{ items: Hit[]; mode: string; next_offset?: number | null; total?: number }>(
        `/api/search?${new URLSearchParams({ q: query, semantic: String(searchMode === "hybrid"), exact: String(searchMode === "exact"), limit: "20", offset: String(more ? nextOffset ?? 0 : 0) })}`,
        { signal: controller.signal },
      );
      if (currentSession(epoch) && !controller.signal.aborted && searchController === controller) {
        results = more ? [...(results ?? []), ...data.items] : data.items;
        retrievalMode = data.mode;
        nextOffset = data.next_offset ?? null;
        searchTotal = data.total ?? null;
      }
    } catch (e) {
      if (searchController === controller) handleError(e, epoch);
    } finally {
      if (currentSession(epoch) && searchController === controller) searching = false;
    }
  }
  function scheduleSearch() {
    const epoch = sessionEpoch;
    if (!currentSession(epoch)) return;
    clearTimeout(searchTimer);
    searchController?.abort();
    results = null;
    nextOffset = null;
    searchTimer = setTimeout(() => { if (currentSession(epoch)) void doSearch(); }, 300);
  }
  async function openDocument(doc: Document, at = 1) {
    const epoch = sessionEpoch;
    if (!currentSession(epoch)) return;
    const navigation = ++navigationRequest;
    if (!selected) returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    chatController?.abort();
    chatController = undefined;
    selected = doc;
    chatDocument = doc;
    turns = [];
    lastQuestion = "";
    completedAnswer = false;
    page = at;
    panel = "info";
    answer = "";
    feedback = null;
    sources = [];
    chatError = "";
    question = "";
    extraDocs = [];
    asking = false;
    await tick();
    if (currentSession(epoch) && navigation === navigationRequest) readerElement?.focus();
  }
  async function openHit(hit: Hit) {
    const epoch = sessionEpoch;
    if (!currentSession(epoch)) return;
    const navigation = ++navigationRequest;
    try {
      const doc = await request<Document>(`/api/documents/${hit.id}`);
      if (currentSession(epoch) && navigation === navigationRequest) await openDocument(doc, hit.page);
    } catch (e) {
      if (navigation === navigationRequest) handleError(e, epoch);
    }
  }
  async function closeReader() {
    const epoch = sessionEpoch;
    const navigation = ++navigationRequest;
    selected = null;
    chatController?.abort();
    chatController = undefined;
    asking = false;
    await tick();
    if (currentSession(epoch) && navigation === navigationRequest && returnFocus?.isConnected) returnFocus.focus();
  }
  async function addFiles(files: FileList | File[]) {
    const epoch = sessionEpoch;
    if (!currentSession(epoch)) return;
    const controller = new AbortController();
    uploadControllers.add(controller);
    const active = () => currentSession(epoch) && !controller.signal.aborted;
    error = "";
    notice = "";
    // Two concurrent transfers at most, independent of OCR/LLM work.
    const queue = Array.from(files);
    const consume = async () => {
      while (active() && queue.length) {
        const file = queue.shift()!;
        const item = { name: file.name, progress: 0, state: "Subiendo" };
        const index = uploads.length;
        uploads = [...uploads, item];
        if (
          !file.name.toLowerCase().endsWith(".pdf") ||
          file.size > 100 * 1024 * 1024
        ) {
          uploads[index].state = "Selecciona un PDF de hasta 100 MB";
          continue;
        }
        try {
          const data = await uploadFile(file, (n) => {
            if (active()) uploads[index].progress = n;
          }, controller.signal);
          if (!active()) return;
          uploads[index].progress = 100;
          uploads[index].state = data.duplicate
            ? "Ya estaba en tu biblioteca"
            : "Guardado";
          notice =
            "Tus archivos guardados ya se pueden abrir. El texto se procesará en segundo plano.";
          await load();
        } catch (e) {
          if (!active() || (e as Error).name === "AbortError") return;
          if (e instanceof ApiError && e.status === 401) {
            handleError(e, epoch);
            return;
          }
          uploads[index].state = (e as Error).message;
        }
      }
    };
    try {
      await Promise.all([consume(), consume()]);
      if (active() && fileInput) fileInput.value = "";
    } finally {
      uploadControllers.delete(controller);
    }
  }
  async function retry(doc: Document) {
    const epoch = sessionEpoch;
    if (!currentSession(epoch)) return;
    try {
      await request(`/api/documents/${doc.id}/retry`, { method: "POST" });
      if (currentSession(epoch)) await load();
    } catch (e) {
      handleError(e, epoch);
    }
  }
  async function openSource(source: Source) {
    const epoch = sessionEpoch;
    if (!currentSession(epoch)) return;
    track("citation_open");
    const navigation = ++navigationRequest;
    if (selected?.id === source.id) page = source.page;
    else
      try {
        const doc = await request<Document>(`/api/documents/${source.id}`);
        if (!currentSession(epoch) || navigation !== navigationRequest || !selected) return;
        selected = doc;
        page = source.page;
        panel = "chat";
      } catch (e) {
        if (navigation === navigationRequest) handleError(e, epoch);
      }
  }
  function resetChat() {
    ++navigationRequest;
    chatController?.abort();
    chatController = undefined;
    asking = false;
    answer = "";
    sources = [];
    turns = [];
    lastQuestion = "";
    completedAnswer = false;
    chatError = "";
    feedback = null;
  }
  async function generateSummary() {
    const epoch = sessionEpoch;
    if (!currentSession(epoch) || !selected) return;
    try {
      await request(`/api/documents/${selected.id}/summary`, {
        method: "POST",
      });
      if (currentSession(epoch)) await load();
    } catch (e) {
      handleError(e, epoch);
    }
  }
  async function compare() {
    const epoch = sessionEpoch;
    if (!currentSession(epoch) || comparing) return;
    comparing = true;
    error = "";
    try {
      const data = await request<NonNullable<typeof comparison>>("/api/similarity", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ document_ids: picked }),
      });
      if (currentSession(epoch)) comparison = data;
    } catch (e) {
      handleError(e, epoch);
    } finally {
      if (currentSession(epoch)) comparing = false;
    }
  }
  function similarity(a: string, b: string) {
    return (
      comparison?.pairs.find((p) => p.id === a && p.other_id === b)
        ?.similarity ?? 0
    );
  }
  async function startSession(create = false) {
    if (leaving) return;
    // Stop the previous session before resolving a fresh identity. Keep its URL
    // so an expired session can return to the same document after signing in.
    sessionReady = false;
    user = null;
    clearPrivateState();
    const epoch = sessionEpoch;
    if (create) {
      window.history.replaceState(window.history.state, "", readerDestination(window.location.href, null, 1, "info"));
      returnPath = window.location.pathname + window.location.search + window.location.hash;
    }
    checking = true;
    error = "";
    try {
      const session = await request<Session>(
        create ? "/session/anonymous" : "/session/me",
        create ? { method: "POST" } : {},
      );
      if (epoch !== sessionEpoch) return;
      accessMode = session.mode;
      loginAvailable = session.login_available;
      accountUrl = session.account_url ?? "";
      user = session.user ?? null;
      expiresIn = session.expires_in ?? (session.ephemeral_hours ?? 24) * 3600;
      sessionReady = session.mode !== "choose";
      if (!sessionReady) {
        setMeasurement(false);
        return;
      }
      await load();
      if (!currentSession(epoch)) return;
      if (!locationReady) {
        const destination = readerLocation(window.location.href);
        if (destination) {
          const navigation = ++navigationRequest;
          try {
            const doc = await request<Document>(`/api/documents/${destination.id}`);
            if (!currentSession(epoch) || navigation !== navigationRequest) return;
            await openDocument(doc, Math.min(destination.page, doc.page_count || 1));
            if (!currentSession(epoch) || selected?.id !== doc.id) return;
            panel = destination.panel;
          } catch (e) { if (navigation === navigationRequest) handleError(e, epoch); }
        }
        if (!currentSession(epoch)) return;
        locationReady = true;
      }
      if (!currentSession(epoch)) return;
      try {
        const prefs = await request<{
          enabled: boolean;
          available: boolean;
          retention_days: number;
        }>("/api/analytics/preferences");
        if (!currentSession(epoch)) return;
        measurement = prefs.enabled;
        measurementAvailable = prefs.available;
        retentionDays = prefs.retention_days;
        setMeasurement(measurement);
        track("library_view");
      } catch (e) {
        if (!currentSession(epoch)) return;
        setMeasurement(false);
        measurement = false;
        if (e instanceof ApiError && e.status === 401) handleError(e, epoch);
      }
    } catch (e) {
      handleError(e, epoch);
    } finally {
      if (epoch === sessionEpoch) checking = false;
    }
  }
  async function endSession() {
    if (!sessionReady || leaving) return;
    invalidateRequests();
    const epoch = sessionEpoch;
    leaving = true;
    try {
      await request("/session/end", { method: "POST" });
      if (epoch !== sessionEpoch) return;
      sessionReady = false;
      user = null;
      clearPrivateState();
      window.location.reload();
    } catch (e) {
      handleError(e, epoch);
    } finally {
      if (epoch === sessionEpoch) leaving = false;
    }
  }
  async function changeMeasurement() {
    const epoch = sessionEpoch;
    if (!currentSession(epoch) || savingPreference) return;
    savingPreference = true;
    try {
      const pref = await request<{ enabled: boolean }>(
        "/api/analytics/preferences",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: !measurement }),
        },
      );
      if (!currentSession(epoch)) return;
      measurement = pref.enabled;
      setMeasurement(measurement);
    } catch (e) {
      handleError(e, epoch);
    } finally {
      if (currentSession(epoch)) savingPreference = false;
    }
  }
  function rateAnswer(value: "positive" | "negative") {
    if (feedback) return;
    feedback = value;
    track("answer_feedback", value);
  }
  async function ask() {
    const epoch = sessionEpoch;
    const context = chatDocument ?? selected;
    if (!currentSession(epoch) || !context || !question.trim() || asking) return;
    const controller = new AbortController();
    chatController = controller;
    asking = true;
    if (completedAnswer && answer && !chatError) {
      turns = [...turns, { question: lastQuestion, answer, sources: [...sources] }].slice(-6);
    }
    const history = turns.flatMap((turn) => [
      {role: "user", content: turn.question.slice(0, 4000)},
      {role: "assistant", content: turn.answer.slice(0, 4000)},
    ]);
    lastQuestion = question.trim();
    completedAnswer = false;
    answer = "";
    feedback = null;
    sources = [];
    chatError = "";
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() },
        body: JSON.stringify({
          question: lastQuestion,
          document_ids: [context.id, ...extraDocs],
          history,
        }),
        signal: controller.signal,
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new ApiError(response.status, data.detail || "No se pudo iniciar la consulta.");
      }
      if (!currentSession(epoch) || controller.signal.aborted || chatController !== controller) return;
      const reader = response.body!.getReader(),
        decoder = new TextDecoder();
      let pending = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        pending += decoder.decode(value, { stream: true });
        let boundary;
        while ((boundary = pending.indexOf("\n\n")) >= 0) {
          const event = pending.slice(0, boundary);
          pending = pending.slice(boundary + 2);
          if (event.startsWith("data: ")) {
            const data = JSON.parse(event.slice(6));
            if (!currentSession(epoch) || controller.signal.aborted || chatController !== controller) return;
            if (data.token) answer += data.token;
            if (data.sources) sources = data.sources;
            if (data.error) chatError = data.error;
            if (data.done) completedAnswer = true;
          }
        }
      }
      if (!currentSession(epoch) || controller.signal.aborted || chatController !== controller) return;
      if (!completedAnswer && !chatError)
        chatError = "La respuesta se interrumpió. Puedes volver a consultar.";
      if (completedAnswer && !chatError && question.trim() === lastQuestion) question = "";
    } catch (e) {
      if (currentSession(epoch) && chatController === controller && (e as Error).name !== "AbortError") {
        if (e instanceof ApiError && e.status === 401) handleError(e, epoch);
        else chatError = (e as Error).message;
      }
    } finally {
      if (currentSession(epoch) && chatController === controller) asking = false;
    }
  }
  function toggleTheme() {
    dark = !dark;
    document.documentElement.dataset.saraTheme = dark ? "dark" : "light";
    localStorage.setItem("sara-theme", dark ? "dark" : "light");
    track("theme_changed", dark ? "dark" : "light");
  }
  onMount(() => {
    returnPath = window.location.pathname + window.location.search + window.location.hash;
    dark =
      localStorage.getItem("sara-theme") === "dark" ||
      (!localStorage.getItem("sara-theme") &&
        matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.dataset.saraTheme = dark ? "dark" : "light";
    void startSession();
    const onVisible = () => { if (!document.hidden) void verifySession(); };
    document.addEventListener("visibilitychange", onVisible);
    const sessionPoll = setInterval(onVisible, 60000);
    const poll = setInterval(() => {
      if (
        sessionReady &&
        !checking && !leaving &&
        (catalog.stats.pending > 0 || selected?.summary_requested) &&
        !document.hidden
      )
        void load(false, true);
    }, 4000);
    return () => {
      clearInterval(poll);
      clearInterval(sessionPoll);
      document.removeEventListener("visibilitychange", onVisible);
      sessionReady = false;
      invalidateRequests();
    };
  });
</script>

<svelte:head
  ><title>SARA DocReader · Biblioteca documental</title><meta
    name="description"
    content="Tu biblioteca de documentos del laboratorio. Organiza, encuentra y lee en un solo lugar."
  /></svelte:head
>
<a class="skip-link" href="#main">Saltar al contenido</a>
<div class="app-shell" inert={selected !== null}>
  <aside class="sidebar">
    <a href="/" class="brand" aria-label="SARA DocReader, inicio"
      ><img
        src={dark ? "/brand/sara-dark.svg" : "/brand/sara-light.svg"}
        alt="SARA"
      /><span>DocReader</span></a
    >
    <div class="workspace-label">ESPACIO DE TRABAJO</div>
    <div class="workspace">
      <span class="workspace-icon">L</span>
      <div>
        <strong>Laboratorio</strong><small>Biblioteca documental</small>
      </div>
    </div>
    <nav aria-label="Biblioteca">
      <button
        disabled={!sessionReady}
        class:active={sessionReady && filter === "all"}
        onclick={() => {
          filter = "all";
          selected = null;
          query = "";
          results = null;
          void doSearch();
        }}><span>▤</span> Mi biblioteca <b>{catalog.stats.total}</b></button
      >
      <button
        disabled={!sessionReady}
        class:active={sessionReady && filter === "pending"}
        onclick={() => {
          filter = "pending";
          selected = null;
          query = "";
          results = null;
          void doSearch();
        }}
        ><span>◷</span> En procesamiento <b>{catalog.stats.pending}</b></button
      >
      <button
        disabled={!sessionReady}
        class:active={sessionReady && filter === "failed"}
        onclick={() => {
          filter = "failed";
          selected = null;
          query = "";
          results = null;
          void doSearch();
        }}><span>!</span> Por revisar</button
      >
    </nav>
    <div class="sidebar-note">
      <span class="small-rule"></span><strong>El conocimiento, a mano.</strong>
      <p>Tus documentos y sus fuentes, en un mismo lugar.</p>
    </div>
    <footer>
      <button class="quiet" onclick={toggleTheme}
        >{dark ? "☀ Tema claro" : "☾ Tema oscuro"}</button
      ><small>SARA · ICI Laboratories</small>
    </footer>
  </aside>
  <div class="main-shell">
    <header class="topbar">
      <span>Laboratorio <span class="slash">/</span> DocReader</span><span
        class="private-badge"
        >◉ {accessMode === "account"
          ? user?.name || user?.email || "Cuenta SARA"
          : sessionReady
            ? "Sin cuenta"
            : "Elige tu acceso"}</span
      >
      {#if accessMode === "account" && sessionReady && accountUrl}
        <a class="quiet account-link" href={accountUrl} target="_blank" rel="noopener noreferrer">Mi cuenta SARA ↗</a>
      {/if}
      {#if sessionReady}<button
          class="quiet"
          disabled={leaving}
          onclick={endSession}
          >{accessMode === "account"
            ? "Salir de SmartDoc"
            : accessMode === "ephemeral"
              ? "Terminar y borrar espacio"
              : "Salir del espacio"}</button
        >{/if}
    </header>
    <main id="main">
      {#if checking}<div class="empty">
          <span class="eyebrow">SARA DOCREADER</span>
          <h1>Abriendo tu biblioteca…</h1>
          <p role="status">Comprobando tu acceso y preparando tu espacio.</p>
        </div>
      {:else if !sessionReady}
        <section class="welcome">
          <span class="eyebrow">SARA DOCREADER</span>
          <h1>Tu biblioteca, a tu manera.</h1>
          <p>
            Prueba SARA sin cuenta o conserva tus documentos al iniciar sesión.
          </p>
          <div class="access-options">
            <article>
              <h2>Espacio temporal</h2>
              <p>
                Sin registro. Los archivos se borran al terminar el espacio o
                después de {Math.ceil(expiresIn / 3600)} horas. Cerrar la pestaña
                no los elimina de inmediato.
              </p>
              <button class="primary" onclick={() => startSession(true)}
                >Usar temporalmente →</button
              >
            </article>
            <article>
              <h2>Con tu cuenta SARA</h2>
              <p>
                Usa la misma cuenta que en SARA y SaraPad. Conserva tu biblioteca
                y vuelve a ella desde otros dispositivos.
              </p>
              {#if loginAvailable}<a class="primary" href={loginHref}
                  >Iniciar sesión →</a
                >
              {:else}<button class="secondary" disabled
                  >Iniciar sesión · próximamente</button
                >
                <small
                  >El portal central de SARA se habilitará más adelante.</small
                >{/if}
            </article>
          </div>
          {#if error}<p role="alert">{error}</p>{/if}
        </section>
      {:else}
        <div class="page-heading">
          <div>
            <span class="eyebrow">BIBLIOTECA DOCUMENTAL</span>
            <h1>
              {filter === "pending"
                ? "En procesamiento"
                : filter === "failed"
                  ? "Documentos por revisar"
                  : "Mi biblioteca"}
            </h1>
            <p>Abre, busca y consulta tus documentos con fuentes por página.</p>
          </div>
          <button class="primary" onclick={() => fileInput.click()}
            >＋ Subir documentos</button
          >
        </div>
        <input
          class="sr-only"
          tabindex="-1"
          type="file"
          accept="application/pdf,.pdf"
          multiple
          bind:this={fileInput}
          onchange={(e) => {
            if (e.currentTarget.files) void addFiles(e.currentTarget.files);
          }}
          aria-label="Seleccionar documentos PDF"
        />
        <div class="summary-strip">
          <span><strong>{catalog.stats.total}</strong> documentos</span><span
            ><strong>{catalog.stats.pending}</strong> en procesamiento</span
          ><span
            ><strong>{bytes(catalog.stats.bytes)}</strong> en tu biblioteca</span
          ><span class="summary-end">PDF · hasta 100 MB por archivo</span>
        </div>
        <div class="searchbar">
          <span aria-hidden="true">⌕</span><input
            aria-label="Buscar documentos"
            maxlength="300"
            placeholder={searchMode === "name"
              ? "Buscar por nombre del documento…"
              : "¿Qué quieres encontrar en tus documentos?"}
            bind:value={query}
            oninput={scheduleSearch}
          /><select
            aria-label="Tipo de búsqueda"
            bind:value={searchMode}
            onchange={() => {
              track("search_mode_changed", searchMode);
              void doSearch();
            }}
            ><option value="name">Por nombre</option><option value="text"
              >Por contenido</option
            ><option value="exact">Frase exacta</option><option value="hybrid">Por significado</option></select
          >{#if query}<button
              class="icon-button"
              aria-label="Limpiar búsqueda"
              onclick={() => {
                query = "";
                void doSearch();
              }}>×</button
            >{/if}
        </div>
        {#if searchMode === "exact"}<p class="search-help">Encuentra una palabra, frase o referencia literal en todas las páginas indexadas. No distingue mayúsculas.</p>{/if}
        {#if notice}<p class="notice" role="status">✓ {notice}</p>{/if}
        {#if uploads.length}<section
            class="upload-list"
            aria-label="Estado de las cargas"
          >
            {#each uploads as item}<div>
                <span>{item.name}</span><progress
                  value={item.progress}
                  max="100"
                  aria-label={`Carga de ${item.name}`}
                ></progress><small>{item.state}</small>
              </div>{/each}<button
              class="quiet"
              disabled={uploads.some((u) => u.state === "Subiendo")}
              onclick={() => (uploads = [])}>Ocultar cargas</button
            >
          </section>{/if}
        {#if searching}<p role="status">Buscando en los documentos…</p>{/if}
        {#if results !== null}
          <div class="section-heading">
            <h2>Resultados en el contenido</h2>
            <span role="status">{results.length}{searchTotal !== null ? ` de ${searchTotal}` : ""} {retrievalMode === "exact" ? "páginas" : "fragmentos"}</span>
          </div>
          {#if retrievalMode === "text_fallback"}<p class="notice">
              La búsqueda semántica no está disponible. Mostramos coincidencias
              de texto.
            </p>{/if}
          {#if results.length === 0}<div class="empty compact">
              <h2>No encontramos coincidencias</h2>
              <p>
                Prueba con otras palabras o espera a que termine el
                procesamiento.
              </p>
            </div>{/if}
          <div class="search-results">
            {#each results as hit}<button
                class="result"
                onclick={() => openHit(hit)}
                ><span class="eyebrow">{hit.name} · PÁGINA {hit.page}</span>
                <p>
                  {#each excerpt(hit.content, query) as part}{#if part.match}<mark>{part.text}</mark>{:else}{part.text}{/if}{/each}
                </p>
                <span class="text-link">Leer en el documento →</span></button
              >{/each}
          </div>
          {#if nextOffset !== null}<button class="secondary" disabled={searching} onclick={() => doSearch(true)}>Cargar más coincidencias</button>{/if}
        {:else}
          <div class="section-heading">
            <h2>{query ? "Documentos encontrados" : "Tus documentos"}</h2>
            <div class="list-actions">
              {#if picked.length >= 2}<button
                  class="secondary"
                  disabled={comparing || picked.length > 20}
                  onclick={compare}
                  >{comparing
                    ? "Comparando…"
                    : `Comparar ${picked.length} documentos`}</button
                >{/if}<button class="quiet" onclick={() => load()}
                >↻ Actualizar</button
              >
            </div>
          </div>
          {#if comparison}<section class="comparison">
              <div class="section-heading">
                <h2>Similitud conceptual</h2>
                <button class="quiet" onclick={() => (comparison = null)}
                  >Cerrar comparación</button
                >
              </div>
              <p>
                Similitud del coseno entre vectores promedio; no representa un
                porcentaje de certeza.
              </p>
              <div class="table-wrap">
                <table>
                  <thead
                    ><tr
                      ><th>Documento</th
                      >{#each comparison.documents as doc, i}<th
                          title={doc.name}>{i + 1}</th
                        >{/each}</tr
                    ></thead
                  ><tbody
                    >{#each comparison.documents as doc, i}<tr
                        ><th>{i + 1}. {doc.name}</th
                        >{#each comparison.documents as other}<td
                            style:background={`color-mix(in srgb, var(--sara-action-soft) ${Math.round(Math.max(0, similarity(doc.id, other.id)) * 100)}%, var(--sara-surface))`}
                            >{similarity(doc.id, other.id).toFixed(2)}</td
                          >{/each}</tr
                      >{/each}</tbody
                  >
                </table>
              </div>
            </section>{/if}
          <!-- The labeled region supplements the keyboard-accessible upload button above. -->
          <section
            class:dragging
            class="library"
            aria-label="Lista de documentos y zona de carga"
            ondragover={(e) => {
              e.preventDefault();
              dragging = true;
            }}
            ondragleave={() => (dragging = false)}
            ondrop={(e) => {
              e.preventDefault();
              dragging = false;
              if (e.dataTransfer) void addFiles(e.dataTransfer.files);
            }}
          >
            {#if visible.length}
              <div class="table-wrap">
                <table>
                  <thead
                    ><tr
                      ><th><span class="sr-only">Seleccionar</span></th><th
                        >Documento</th
                      ><th>Estado</th><th>Agregado</th><th>Tamaño</th><th
                        ><span class="sr-only">Acciones</span></th
                      ></tr
                    ></thead
                  ><tbody
                    >{#each visible as doc}<tr
                        ><td
                          ><input
                            class="select-document"
                            type="checkbox"
                            aria-label={`Seleccionar ${doc.name}`}
                            value={doc.id}
                            bind:group={picked}
                          /></td
                        ><td
                          ><button
                            class="document-name"
                            onclick={() => openDocument(doc)}
                            ><span class="pdf-icon">PDF</span><span
                              ><strong>{doc.name}</strong><small
                                >{doc.page_count
                                  ? `${doc.page_count} páginas`
                                  : "Original disponible"}</small
                              ></span
                            ></button
                          ></td
                        ><td
                          ><span class={`status ${doc.status}`}
                            >{states[doc.status]}</span
                          ></td
                        ><td class="muted"
                          >{new Date(doc.created_at).toLocaleDateString(
                            "es-MX",
                            { day: "numeric", month: "short", year: "numeric" },
                          )}</td
                        ><td class="muted">{bytes(doc.size)}</td><td
                          ><button
                            class="icon-button"
                            onclick={() => openDocument(doc)}
                            aria-label={`Abrir ${doc.name}`}>↗</button
                          ></td
                        ></tr
                      >{/each}</tbody
                  >
                </table>
              </div>
              <div class="table-footer">
                <span>Arrastra más archivos PDF aquí</span
                >{#if catalog.next}<button
                    class="secondary"
                    onclick={() => load(true)}>Cargar más</button
                  >{/if}
              </div>
            {:else}<div class="empty">
                <span class="empty-icon">▤</span>
                <h2>
                  {query
                    ? "No hay documentos con ese nombre"
                    : filter === "pending"
                      ? "No hay documentos en procesamiento"
                      : filter === "failed"
                        ? "Todo en orden"
                        : "Tu próxima idea empieza aquí"}
                </h2>
                <p>
                  {filter === "all" && !query
                    ? "Arrastra tus archivos PDF o selecciona documentos para comenzar."
                    : "Los documentos aparecerán aquí cuando coincidan con este filtro."}
                </p>
                {#if filter === "all" && !query}<button
                    class="secondary"
                    onclick={() => fileInput.click()}
                    >Seleccionar documentos</button
                  ><small
                    >Podrás abrirlos mientras se prepara la búsqueda.</small
                  >{/if}
              </div>{/if}
          </section>
        {/if}
      {/if}
      {#if error}<div class="error" role="alert">
          {error}<button class="quiet" onclick={() => (error = "")}
            >Cerrar</button
          >
        </div>{/if}
      {#if sessionReady}
        <details class="privacy-note">
          <summary>Sobre este espacio y la medición de uso</summary>
          <p>
            {#if accessMode === "account"}
              Tu biblioteca está vinculada a tu cuenta. Cerrar sesión conserva
              los documentos.
            {:else if accessMode === "ephemeral"}
              Espacio temporal de este navegador: caduca en aproximadamente {Math.max(
                1,
                Math.ceil(expiresIn / 3600),
              )} horas. Al caducar se elimina automáticamente; «Terminar y borrar
              espacio» adelanta la eliminación. Borrar cookies impide volver a abrirlo.
              Cerrar la pestaña no adelanta su caducidad.
            {:else}
              Este espacio sin cuenta conserva su política anterior. Los
              archivos existentes no se eliminan automáticamente.
            {/if}
          </p>
          <p>
            Para mejorar SARA registramos acciones, tiempos y errores durante {retentionDays}
            días. No incluimos documentos, nombres de archivo, preguntas, respuestas
            ni direcciones IP en estas métricas.
          </p>
          <button
            class="secondary"
            role="switch"
            aria-checked={measurement}
            disabled={savingPreference || !measurementAvailable}
            onclick={changeMeasurement}
            >{measurement
              ? "Medición de uso activada"
              : "Medición de uso desactivada"}</button
          >
        </details>
      {/if}
      <div class="bottom-note">
        <span>SARA DocReader</span><span
          >Un espacio para leer, conectar y descubrir.</span
        >
      </div>
    </main>
  </div>
</div>
{#if selected && sessionReady}
  <div class="reader-shell" bind:this={readerElement} role="dialog" aria-modal="true" aria-label={`Lector de ${selected.name}`} tabindex="-1" onkeydown={(event) => { if (event.key === "Escape") closeReader(); }}>
    <header class="reader-heading">
      <button
        class="secondary"
        onclick={closeReader}>← Biblioteca</button
      ><strong>{selected.name}</strong><span class={`status ${selected.status}`}
        >{states[selected.status]}</span
      >
    </header>
    <div class="reader-body">
      <section class="reader-document" aria-label="Visor del documento">
        {#key selected.id}<PdfViewer id={selected.id} bind:page />{/key}
      </section>
      <aside class="reader-panel">
        {#if error}<p class="error" role="alert">{error}</p>{/if}
        <div class="tabs">
          <button
            class:active={panel === "info"}
            onclick={() => (panel = "info")}>Documento</button
          ><button
            class:active={panel === "chat"}
            onclick={() => (panel = "chat")}>Preguntar</button
          >
        </div>
        {#if panel === "info"}<span class="eyebrow">DETALLES DEL ARCHIVO</span>
          <h2>{selected.name}</h2>
          <dl>
            <dt>Páginas</dt>
            <dd>{selected.page_count ?? "Por determinar"}</dd>
            <dt>Tamaño</dt>
            <dd>{bytes(selected.size)}</dd>
            <dt>Estado</dt>
            <dd>{states[selected.status]}</dd>
            {#if selected.category}<dt>Categoría sugerida</dt>
              <dd>{selected.category}</dd>{/if}
          </dl>
          {#if selected.summary}<h3>Síntesis del documento</h3>
            <div class="answer">
              <Answer text={selected.summary} />
            </div>{/if}{#if selected.summary_error}<p class="error">
              {selected.summary_error}
            </p>{/if}<button
            class="secondary"
            disabled={selected.status !== "ready" || selected.summary_requested}
            onclick={generateSummary}
            >{selected.summary_requested ? "Preparando síntesis…" : selected.summary
              ? "Actualizar síntesis"
              : "Generar síntesis y categoría"}</button
          >
          <p>
            El original está disponible desde el momento en que lo subes. La
            búsqueda se prepara en segundo plano.
          </p>
          {#if selected.error}<p class="error">
              {selected.error}
            </p>{/if}{#if selected.embedding_error}<p class="notice">
              {selected.embedding_error}
            </p>{/if}{#if selected.status === "failed" || selected.embedding_error}<button
              class="secondary"
              onclick={() => retry(selected!)}>Reintentar procesamiento</button
            >{/if}<a
            class="button secondary"
            href={`/api/documents/${selected.id}/file`}
            target="_blank"
            rel="noreferrer">Abrir original ↗</a
          >
        {:else}<span class="eyebrow">CONSULTA CON FUENTES</span>
          <h2>Pregúntale al documento</h2>
          <p class="context-label">Conversación sobre <strong>{(chatDocument ?? selected).name}</strong></p>
          {#if chatDocument && selected.id !== chatDocument.id}<button class="secondary" onclick={() => { ++navigationRequest; selected = chatDocument; page = 1; }}>Volver al documento de la conversación</button>{/if}
          <p>
            Las respuestas se apoyan en fragmentos del archivo. Revisa siempre
            la fuente.
          </p>
          <details class="extra-documents">
            <summary>Incluir otros documentos ({extraDocs.length})</summary
            >{#each catalog.items.filter((d) => d.id !== (chatDocument ?? selected)?.id && d.status === "ready") as doc}<label
                ><input
                  type="checkbox"
                  value={doc.id}
                  bind:group={extraDocs}
                  onchange={resetChat}
                  disabled={asking || (!extraDocs.includes(doc.id) &&
                    extraDocs.length >= 19)}
                />{doc.name}</label
              >{/each}<small
              >Cambiar los documentos inicia una conversación nueva. Se muestran los documentos cargados de tu biblioteca.</small
            >
            {#if catalog.next}<button class="secondary" disabled={asking} onclick={() => load(true)}>Cargar más documentos</button>{/if}
          </details>
          {#if turns.length || answer}<button class="quiet" disabled={asking} onclick={resetChat}>Nueva conversación</button>{/if}
          {#each turns as turn}<details class="chat-turn"><summary>{turn.question}</summary><div class="answer"><Answer text={turn.answer} sources={turn.sources} onsource={openSource} /></div>{#each turn.sources as source, i}<button class="source" onclick={() => openSource(source)}>Fuente {i + 1} · {source.name} · Página {source.page}</button>{/each}</details>{/each}
          <form
            onsubmit={(e) => {
              e.preventDefault();
              void ask();
            }}
          >
            <label for="question">Tu pregunta</label><textarea
              id="question"
              placeholder={completedAnswer || turns.length ? "Continúa la conversación…" : "¿Qué metodología se utilizó?"}
              bind:value={question}
              maxlength="2000"
              rows="4"></textarea><button
              class="primary"
              disabled={asking ||
                (chatDocument ?? selected).status !== "ready" ||
                !question.trim()}
              >{asking ? "Consultando…" : "Consultar documento →"}</button
            >{#if asking}<button
                type="button"
                class="quiet"
                onclick={() => { chatController?.abort(); chatError = "Consulta detenida. Puedes reformular la pregunta."; }}>Detener</button
              >{/if}
          </form>
          {#if selected.status !== "ready"}<p class="notice">
              Podrás consultar cuando termine el procesamiento.
            </p>{/if}{#if chatError}<p class="error" role="alert">
              {chatError}
            </p>{/if}{#if answer}<h3>{lastQuestion}</h3><div class="answer">
              <Answer text={answer} {sources} onsource={openSource} />
            </div>
            <div class="answer-feedback">
              <span>¿Te resultó útil?</span><button
                class="secondary"
                disabled={feedback !== null || asking}
                onclick={() => rateAnswer("positive")}>Sí</button
              ><button
                class="secondary"
                disabled={feedback !== null || asking}
                onclick={() => rateAnswer("negative")}>No</button
              >{#if feedback}<small>Gracias por tu valoración.</small>{/if}
            </div>{/if}{#if sources.length}<h3>Fuentes consultadas</h3>
            {#each sources as source, i}<button
                class="source"
                onclick={() => openSource(source)}
                ><strong>Fuente {i + 1} · Página {source.page} ↗</strong><span
                  >{source.name}</span
                ><span>{source.text.slice(0, 150)}…</span></button
              >{/each}{/if}{/if}
      </aside>
    </div>
  </div>
{/if}

<style>
  .access-options {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 1.25rem;
    margin: 2rem 0;
  }
  .access-options article {
    border: 1px solid var(--line, #c9c9c9);
    border-radius: 1rem;
    padding: 1.5rem;
  }
  .access-options h2 {
    font-size: 1.25rem;
  }
  .access-options small {
    display: block;
    margin-top: 1rem;
  }
  @media (max-width: 700px) {
    .access-options {
      grid-template-columns: 1fr;
    }
  }
</style>
