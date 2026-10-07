function appendInlineMarkdown(parent, text) {
  const tokenPattern = /(\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g;
  let lastIndex = 0;

  for (const match of text.matchAll(tokenPattern)) {
    parent.append(document.createTextNode(text.slice(lastIndex, match.index)));
    const token = match[0];
    if (token.startsWith("**")) {
      const strong = document.createElement("strong");
      strong.textContent = token.slice(2, -2);
      parent.append(strong);
    } else {
      const linkMatch = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      const linkUrl = safeMarkdownUrl(linkMatch[2]);
      if (linkUrl) {
        const link = document.createElement("a");
        link.textContent = linkMatch[1];
        link.href = linkUrl;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        parent.append(link);
      } else {
        parent.append(document.createTextNode(token));
      }
    }
    lastIndex = match.index + token.length;
  }

  parent.append(document.createTextNode(text.slice(lastIndex)));
}

function safeMarkdownUrl(value) {
  try {
    const url = new URL(value, document.baseURI);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

export function renderMarkdown(parent, value) {
  const lines = String(value ?? "").replace(/\r\n?/g, "\n").split("\n");
  let paragraph = [];
  let list = null;

  const flushParagraph = () => {
    if (paragraph.length === 0) return;
    const block = document.createElement("p");
    paragraph.forEach((line, index) => {
      if (index > 0) block.append(document.createElement("br"));
      appendInlineMarkdown(block, line);
    });
    parent.append(block);
    paragraph = [];
  };
  const flushList = () => {
    if (!list) return;
    parent.append(list);
    list = null;
  };

  for (const line of lines) {
    const heading = line.match(/^(#{1,6})\s+(.+?)\s*#*$/);
    const listItem = line.match(/^\s*[*-]\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const element = document.createElement(`h${heading[1].length}`);
      appendInlineMarkdown(element, heading[2]);
      parent.append(element);
    } else if (listItem) {
      flushParagraph();
      list ??= document.createElement("ul");
      list.className = "ai-markdown-list";
      const item = document.createElement("li");
      appendInlineMarkdown(item, listItem[1]);
      list.append(item);
    } else if (!line.trim()) {
      flushParagraph();
      flushList();
    } else {
      flushList();
      paragraph.push(line);
    }
  }

  flushParagraph();
  flushList();
}
