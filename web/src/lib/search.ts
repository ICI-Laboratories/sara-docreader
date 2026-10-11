/** Plain-text snippets: never interpolate document content as HTML. */
export function excerpt(content: string, query: string, maxLength = 450) {
  const needle = query.trim();
  const found = needle ? content.toLocaleLowerCase().indexOf(needle.toLocaleLowerCase()) : -1;
  const start = found < 0 ? 0 : Math.max(0, found - 100);
  const end = Math.min(content.length, Math.max(start + maxLength, found + needle.length));
  const parts: { text: string; match: boolean }[] = [];
  if (start) parts.push({text: "…", match: false});
  if (found >= 0) {
    parts.push({text: content.slice(start, found), match: false});
    parts.push({text: content.slice(found, found + needle.length), match: true});
    parts.push({text: content.slice(found + needle.length, end), match: false});
  } else parts.push({text: content.slice(start, end), match: false});
  if (end < content.length) parts.push({text: "…", match: false});
  return parts;
}
