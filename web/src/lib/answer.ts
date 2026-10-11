/** Inline emphasis and verified citations; all remaining text stays literal. */
export type AnswerPart = { text: string; kind: 'text' | 'strong' | 'em' | 'code' | 'source'; index?: number };
export function answerParts(text: string, sourceCount: number): AnswerPart[] {
  const parts: AnswerPart[] = [];
  const expression = /\*\*(?!\s)([^*\n]*\S)\*\*|\*(?!\s)([^*\n]*\S)\*|`([^`\n]+)`|\[Fuente\s+(\d+)\]/gi;
  let start = 0;
  for (const match of text.matchAll(expression)) {
    const position = match.index!;
    if (position > start) parts.push({kind:'text', text:text.slice(start, position)});
    if (match[1]) parts.push({kind:'strong', text:match[1]});
    else if (match[2]) parts.push({kind:'em', text:match[2]});
    else if (match[3]) parts.push({kind:'code', text:match[3]});
    else {
      const index = Number(match[4]) - 1;
      parts.push(index >= 0 && index < sourceCount
        ? {kind:'source', text:match[0], index}
        : {kind:'text', text:match[0]});
    }
    start = position + match[0].length;
  }
  if (start < text.length) parts.push({kind:'text', text:text.slice(start)});
  return parts;
}
