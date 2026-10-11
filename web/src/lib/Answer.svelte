<script lang="ts">
  import { answerParts } from './answer';
  import type { Source } from './api';
  let { text, sources = [], onsource } = $props<{
    text: string; sources?: Source[]; onsource?: (source: Source) => void;
  }>();
  const parts = $derived(answerParts(text, sources.length));
</script>
{#each parts as part}{#if part.kind === 'strong'}<strong>{part.text}</strong>{:else if part.kind === 'em'}<em>{part.text}</em>{:else if part.kind === 'code'}<code>{part.text}</code>{:else if part.kind === 'source' && part.index !== undefined && onsource}<button type="button" class="inline-source" title={`${sources[part.index].name} · página ${sources[part.index].page}`} onclick={() => onsource?.(sources[part.index!])}>{part.text}</button>{:else}{part.text}{/if}{/each}
<style>
  .inline-source { display: inline; min-height: 24px; padding: 2px 4px; border-radius: 4px; color: var(--sara-action); background: var(--sara-action-soft); font-size: inherit; font-weight: 600; line-height: inherit; }
  code { font-size: .9em; overflow-wrap: anywhere; }
</style>
