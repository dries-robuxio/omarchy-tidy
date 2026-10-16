<p align="center">
  <img src="assets/logo.svg" alt="Tidy" width="128" height="128">
</p>

<h1 align="center">Tidy</h1>

<p align="center">
  <b>Clean up the text you are writing, in any app, with one key.</b><br>
  An <a href="https://omarchy.org">Omarchy</a> plugin. Bring your own AI: Claude Code, Codex, OpenCode,<br>
  Gemini CLI, Kimi, Ollama, OpenRouter, any OpenAI-compatible API, or your own command.
</p>

---

Type fast, don't fix anything, press **Ctrl + `**, and a second later
the whole text field is replaced with a corrected version: typos, misspelled
words, wrong words, doubled words, grammar, punctuation and capitals. Then
press Enter.

```
teh meeting is tomorow, can you send me the the docs bfeore 3?
```
```
The meeting is tomorrow, can you send me the docs before 3?
```

- **Works everywhere:** browsers, chat apps, editors, terminals. No app integration needed.
- **Your words, fixed:** keeps your language (no translating), your tone and slang ("lol",
  "tmrw" stay), your line breaks and lists. It does not rewrite, summarise, make things
  formal or answer what you wrote.
- **Uses what you already pay for:** your Claude or ChatGPT subscription through their CLIs, a
  free local model with Ollama, or any API key you have.
- **Fast:** the AI is kept warm in the background, so Claude Code answers in about a second.
- **Change the job:** one prompt in the config turns Tidy into "make it formal", "translate to
  English" or "shorten this".

## Install

```sh
omarchy plugin add https://github.com/dries-robuxio/omarchy-tidy.git --enable
```

A small setup window opens and asks two things:

1. **Which key** you want (Ctrl + `, Ctrl + <, Super + Ctrl + Y, or any key you type). Keys
   that something else already uses are refused, so Tidy never takes over one of
   your shortcuts.
2. **Which AI** fixes your text, from the ones it finds on your machine (Claude
   Code, Codex, OpenCode, Gemini CLI, Kimi, Ollama) or an API you name.

It then tries a sample sentence so you see it work, and saves your choices.
Nothing is bound until you finish the setup, and nothing in your Hyprland config
is edited. Run it again any time with
`~/.config/omarchy/plugins/io.github.dries-robuxio.tidy/bin/tidy setup`.

## Use it

| Key (default) | What it does |
| --- | --- |
| **Ctrl + `** | Select all the text in the field you're typing in, tidy it, replace it |
| **Ctrl + Shift + `** | Tidy only the text you selected |

Whatever key you choose, the same key with Shift tidies only the selection.

A small notification says *Tidying…* while it works. If you switch to another
window before it's done, Tidy doesn't paste into the wrong place: the result
waits on your clipboard instead. In a terminal, Tidy works on the selection
(Ctrl+A there means "start of line").

**Ctrl + `** is one-handed on purpose: left little finger on Ctrl, ring finger
on the key left of 1. Tidy binds that key by position, so on AZERTY it is
Ctrl + ², on German Ctrl + ^, the same spot. (It takes over VS Code's Ctrl + `
terminal toggle while Tidy runs; pick another key in the setup if you need it.)
On keyboards with a `< >` key next to left Shift (AZERTY, German, UK, ...)
**Ctrl + <** is just as quick.

## Configure

The setup writes `~/.config/tidy/config.toml` (a commented copy of
[`config.example.toml`](config.example.toml)). Edit it by hand for anything else;
changes apply within two seconds.

```toml
backend = "claude"         # claude | codex | opencode | gemini | kimi | openai | command
model = ""                 # empty = a fast default for that backend
keys = "CTRL + code:49"     # Ctrl + the key left of 1
selection_keys = "CTRL + SHIFT + code:49"
notify = true
# prompt = """Make the text formal. Output only the rewritten text."""
```

Try a setup without touching a text field:

```sh
~/.config/omarchy/plugins/io.github.dries-robuxio.tidy/bin/tidy text "this is is a tset"
~/.config/omarchy/plugins/io.github.dries-robuxio.tidy/bin/tidy status
```

### Backends

| `backend` | Uses | Default model | Speed (measured) | Tools |
| --- | --- | --- | --- | --- |
| `claude` | [Claude Code](https://docs.claude.com/en/docs/claude-code), your Claude subscription | `haiku` | **~1 s** (kept warm) | all off |
| `codex` | [Codex CLI](https://github.com/openai/codex), your ChatGPT plan | `gpt-6-luna` | ~3–4 s | shell and apps off, read-only sandbox |
| `opencode` | [OpenCode](https://opencode.ai), any provider you set up there | set `model`, e.g. `openrouter/z-ai/glm-5.3-flash` | 5–13 s (slow start) | all denied |
| `gemini` | [Gemini CLI](https://github.com/google-gemini/gemini-cli), your Google login | `gemini-2.5-flash-lite` | *experimental, untested* | denied by policy |
| `kimi` | [Kimi CLI](https://github.com/MoonshotAI/kimi-cli) | Kimi's default | *experimental, untested* | none given; loads AGENTS.md and saves sessions |
| `openai` | any OpenAI-compatible API | set it | fastest: one HTTP call | none |
| `command` | your own program | — | — | up to you |

Agent CLIs carry their own startup time; only `claude` is kept warm so far.
OpenCode's free built-in model refuses requests from other programs, so set a
`model` from a provider you configured in OpenCode.

**Any OpenAI-compatible API** (`backend = "openai"`) is the fastest option and
works with local models:

```toml
backend = "openai"
[openai]
url = "http://localhost:11434/v1"   # Ollama; or OpenRouter, OpenAI, Groq, LM Studio, ...
model = "llama3.2:3b"
api_key_file = ""                   # e.g. "~/.config/tidy/key" (chmod 600) if the API needs a key
```

**Your own command** (`backend = "command"`) gets the text on stdin and the
prompt in `$TIDY_PROMPT`, and prints the cleaned text:

```toml
backend = "command"
[command]
cmd = ["llm", "-m", "gpt-4o-mini", "-s", "Fix the grammar. Output only the text."]
```

## How it works

- The plugin is a headless Omarchy **service**: `Service.qml` keeps `bin/tidy serve` running
  while the plugin is enabled.
- The worker registers the two keys with Hyprland at runtime (and again after every Hyprland
  config reload), only if no other binding uses them and only after the setup was saved; it
  removes only its own bindings, keeps the backend warm, and answers key presses over a Unix socket in
  `$XDG_RUNTIME_DIR`. Claude Code is kept as one pre-started process; each request uses it
  once, so no earlier text stays in its context.
- A key press sends Ctrl+A / Ctrl+C to the focused window through Hyprland (the way Omarchy's
  universal copy does), cleans the text and pastes it back with Ctrl+V.
- The text is sent between `<text>` tags with an instruction not to follow anything inside
  it, so a message that says "ignore your instructions" just gets its spelling fixed.
- Agent CLIs run with their tools switched off where the CLI allows it, in an empty temporary
  directory, without your project files or MCP servers.

**Privacy:** your text goes to the backend you chose and nowhere else. With Ollama
it never leaves your machine. The cleaned text stays on your clipboard afterwards.

**Requirements:** Omarchy (Hyprland + omarchy-shell), Python 3.11+, `wl-clipboard` and
`libnotify`, all part of Omarchy, plus the CLI or API of the backend you pick.

## Remove

```sh
omarchy plugin remove io.github.dries-robuxio.tidy
```

The keys disappear with it. Your `~/.config/tidy/` stays; delete it if you like.

## Development

```sh
python3 -m unittest discover tests
omarchy plugin validate .
```

## License

MIT
