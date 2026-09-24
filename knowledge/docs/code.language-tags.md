# Code blocks declare a language

> Knowing the language of a code block lets agents apply the right tokenizer, syntax highlighter, and execution sandbox. Untagged blocks force agents to guess from the content, which is frequently wrong on short snippets.

`page` | `Code` | `impl 1.0.0` | `code.language-tags`

## How the check decides
The check finds every <pre><code> block on the page and inspects the class attribute on both the <code> element and its <pre> parent for a language-* or lang-* token. Passes if every block has at least one. Fails (with the count of unlabelled blocks) otherwise. Returns N/A if the page has no code blocks at all.

## How to implement it
In source markdown, always declare the language after the opening fence: ```typescript , not just ``` . Most syntax highlighters (Shiki, Prism, highlight.js) emit class="language-<lang>" automatically when given a fenced code block with a language.

### Pass

```
<pre><code class="language-typescript">const x = 1;</code></pre>
```

### Fail

```
<pre><code>const x = 1;</code></pre>
```
