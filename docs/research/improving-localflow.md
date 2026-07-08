# Improving LocalFlow toward Wispr Flow quality

> **About this file.** The repo had no research-notes convention before this. I
> established `docs/research/` as the home for cited research memos; put future
> ones here. Every factual claim below links to a primary source (official docs,
> source code, model cards, first-party changelogs). Numbers are quoted from the
> exact page they came from. Anything I could not confirm against a primary
> source is marked **[unverified]**.
>
> Scope: research only. `localflow.py` was not modified.
> Hard constraints assumed throughout: RTX 4050 **6 GB VRAM** (Whisper + LLM must
> coexist), **8 GB system RAM**, Windows 11, Python 3.13, fully on-device.

---

## Executive summary — ranked improvements

Ordered by (quality gain ÷ effort) within the 6 GB budget.

### Cheap, high-impact (do these first)
1. **Swap `distil-large-v3` → `distil-large-v3.5` (CT2 build).** Drop-in same
   architecture/size (756 M params, so ~identical VRAM), but lower word error
   rate and slightly faster. Short-form OOD WER **7.08 %** vs distil-v3's
   **7.53 %**, and it is trained on **98,000 h** vs the old **22,000 h**. It even
   edges out `large-v3-turbo` (7.30 %) on short-form. One-line model-name change.
   [distil-large-v3.5 card](https://huggingface.co/distil-whisper/distil-large-v3.5)
2. **Raise the *final* pass to `beam_size=5`, keep live preview at `beam_size=1`.**
   The final transcript is the one that gets pasted, so accuracy matters most
   there; the code currently uses `beam_size=1` for both. faster-whisper's own
   default is 5. [faster-whisper README](https://github.com/SYSTRAN/faster-whisper)
3. **Fix the clipboard-restore race and add an injection fallback.** The fixed
   `time.sleep(1.0)` before restoring the old clipboard is a guessed timing race,
   and `Ctrl+V` silently fails against higher-integrity windows (UIPI). See §4.
   [SendInput / UIPI](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)
4. **Stream the cleanup LLM response** instead of `"stream": false`. Ollama
   streams by default; consuming tokens as they arrive lets the overlay show the
   polished text progressively and lets you start pasting sooner. [Ollama API](https://github.com/ollama/ollama/blob/main/docs/api.md)
5. **Persist the custom dictionary and feed it as both `hotwords` and
   `initial_prompt`.** Today `DICTIONARY` is a hardcoded constant; making it a
   user-editable file that grows is the on-device analog of Wispr's
   auto-learning dictionary. See §1 / §2.

### Medium effort, high differentiation
6. **Context-aware formatting per app** (Wispr's headline feature). Detect the
   foreground app (`GetForegroundWindow` → process name) and switch the cleanup
   system prompt / tone profile. Fully reproducible on-device. See §1.
7. **VAD-driven segment streaming** to replace the "re-transcribe the last 30 s
   every 2 s" loop, which recomputes the same audio repeatedly. Use Silero VAD to
   finalize segments at speech pauses and only transcribe new audio. See §2.
8. **A voice-command / "Command Mode" path**: a second hotkey that treats the
   utterance as an instruction over the currently selected text (copy selection →
   send text+instruction to the LLM → paste result). See §1.

### Expensive / marginal (deprioritize)
- **`large-v3-turbo`**: needs **~6 GB VRAM at fp16 by itself** per OpenAI's
  table — it would crowd out the LLM, and distil-large-v3.5 already matches or
  beats its short-form accuracy while being faster. [OpenAI whisper table](https://github.com/openai/whisper)
- **Batched inference (`BatchedInferencePipeline`)**: 4× faster but raises VRAM
  (large-v3 fp16 batch_size=8 measured at **6090 MB** vs 4525 MB unbatched) —
  directly conflicts with sharing 6 GB with the LLM, and single short dictations
  don't benefit much. [faster-whisper README](https://github.com/SYSTRAN/faster-whisper)
- **Ollama JSON-schema structured output** for cleanup: adds constraint overhead
  for a task whose output is just a paragraph of prose; the existing length-guard
  is a lighter safeguard. See §3.

---

## 1. What Wispr Flow does that LocalFlow doesn't

Sources are Wispr Flow's own site and help center. On-device reproducibility is
my assessment against the hard constraints.

| Wispr feature | What it is (primary source) | On-device in LocalFlow? |
|---|---|---|
| **Context awareness** | "reads your active app and adapts transcription accuracy, style, and formatting automatically — so emails sound like emails and Slack messages sound like Slack messages"; detects names in emails, applies style by app category, smart formatting in Notion. [Context Awareness doc](https://docs.wisprflow.ai/articles/4678293671-feature-context-awareness) | **Yes, partially.** Detect foreground process via Win32 and switch the cleanup prompt/tone. Wispr also sends nearby on-screen text + proper nouns to their server to boost accuracy ([Data Controls](https://wisprflow.ai/data-controls)) — that cloud step is out of scope; the app-detection + tone-switch part is fully local. |
| **Flow Styles (per-app tone)** | Pick a tone per app kind — formal for email, casual for chat — and Flow formats to match. [Flow Styles doc](https://docs.wisprflow.ai/articles/2368263928-how-to-setup-flow-styles) | **Yes.** A dict of `{app → system-prompt variant}` swapped into the Ollama call. |
| **Command Mode (edit/ask by voice)** | "transform text with your voice — rewrite a paragraph, translate content, or ask a question." Edits the highlighted selection ("Make this more concise", "Translate to Polish", "Turn this outline into an essay"); with no selection, "inserts the answer or generated text inline at your cursor." Activated by press-and-hold (Windows: `Ctrl`+`Win`+`Alt`). [Command Mode doc](https://docs.wisprflow.ai/articles/4816967992-how-to-use-command-mode) | **Yes.** Second hotkey → copy current selection to clipboard → send `{selection, spoken instruction}` to qwen2.5 → paste. Same paste path already exists. |
| **Custom dictionary that auto-learns** | "Flow automatically learns your unique words and adds them to your personal dictionary." [wisprflow.ai](https://wisprflow.ai/) | **Yes (heuristically).** Persist `DICTIONARY`/`CORRECTIONS` to a user file; optionally auto-add capitalized OOV tokens the user keeps. Feed as `hotwords`+`initial_prompt` (see §2). True learning-from-corrections is harder [unverified how Wispr does it]. |
| **Snippet library (voice shortcuts)** | "Create voice shortcuts… speak a cue and get the full formatted text." [wisprflow.ai](https://wisprflow.ai/) | **Yes.** A local `{trigger phrase → expansion}` map applied post-cleanup. |
| **Auto language detection / switching** | "Flow automatically detects and transcribes in your language, letting you move between them"; 100+ languages. [Multiple languages doc](https://docs.wisprflow.ai/articles/3191899797-use-flow-with-multiple-languages), [Features](https://wisprflow.ai/features) | **Partial.** Whisper auto-detects language per call, but the current code implicitly targets English (and the CPU fallback is `small.en`, English-only). Removing `.en` fallback and letting Whisper detect enables multilingual; mid-utterance switching is weaker than Wispr's [unverified parity]. |
| **Natural formatting (no "period"/"new line")** | Marketing: rambling speech becomes "clear, perfectly formatted text, without the filler words or typos." [wisprflow.ai](https://wisprflow.ai/) | **Yes — already the design.** LocalFlow's LLM cleanup does exactly this. Explicit spoken commands like "new line" would need Command Mode (above). |
| **Throughput claim** | "4x faster than typing" (220 wpm vs 45 wpm); "90% faster across every app." [wisprflow.ai](https://wisprflow.ai/) | These are throughput, not per-utterance latency. A concrete end-to-end **latency** SLA for Wispr was **[unverified]** — not published on the pages reviewed. |
| **Cross-device sync, iPhone/Android apps** | Native Mac/Windows/iOS/Android with synced dictionary/style. [wisprflow.ai](https://wisprflow.ai/) | Out of scope (single-user local app; sync implies cloud). |

**Highest-leverage gaps to close:** context-aware per-app tone (#6 above) and a
Command Mode (#8). Both are genuinely reproducible on-device and are what makes
Wispr feel "smart" beyond raw dictation.

---

## 2. Transcription accuracy & latency within 6 GB VRAM

### 2a. Model choice
OpenAI's model table (VRAM and relative speed are first-party):

| Model | Params | Required VRAM | Rel. speed |
|---|---|---|---|
| small | 244 M | ~2 GB | ~4× |
| medium | 769 M | ~5 GB | ~2× |
| large | 1550 M | ~10 GB | 1× |
| **turbo** | 809 M | **~6 GB** | ~8× |

Source: [github.com/openai/whisper](https://github.com/openai/whisper). Turbo is
"an optimized version of `large-v3` that offers faster transcription speed with a
minimal degradation in accuracy" and is **not trained for translation**. Its
~6 GB fp16 footprint alone would evict the LLM on a 6 GB card.

**distil-large-v3 (current):** 756 M params, "49 % smaller" than large-v3, ~**6.3×**
relative latency (i.e. faster), short-form WER **9.7 %** vs large-v3 8.4 %,
long-form **10.8 %** vs 11.0 %; **1.3× fewer** repeated 5-gram duplicates and
**2.1 %** lower insertion-error rate — i.e. it hallucinates less.
Source: [huggingface/distil-whisper](https://github.com/huggingface/distil-whisper).

**distil-large-v3.5 (recommended upgrade):** same 756 M params, trained on
**98,000 h** (vs 22,000 h). OOD WER — short-form **7.08 %** (distil-v3 7.53 %,
turbo 7.30 %, large-v3 7.12 %); long-form **11.39 %** (distil-v3 11.6 %, turbo
10.25 %). Relative RTFx **1.46** vs distil-v3 1.44 vs turbo 1.0; "approximately
1.5× faster" than turbo on long-form. "drop-in replacement." Load via CTranslate2:
`WhisperModel("distil-whisper/distil-large-v3.5-ct2", …)`.
Source: [distil-large-v3.5 card](https://huggingface.co/distil-whisper/distil-large-v3.5).

> **Verdict:** distil-large-v3.5 is the best fit — same VRAM as today, better
> accuracy, still leaves room for the LLM. Turbo and batching both push VRAM
> toward the 6 GB ceiling for little or negative net benefit here.

**Quantization headroom.** Current `int8_float16` quantizes embedding/linear
weights to int8 and runs the rest in float16; requires NVIDIA Compute Capability
≥ 7.0 (or 6.1) — the RTX 4050 (Ada, CC 8.9) qualifies.
Source: [CTranslate2 quantization](https://opennmt.net/CTranslate2/quantization.html).
For reference, faster-whisper measured large-v3 at **2926 MB (int8)** vs **4525 MB
(fp16)** on an 8 GB card. [faster-whisper README](https://github.com/SYSTRAN/faster-whisper).

### 2b. Decoding parameters (cheap accuracy wins)
From the faster-whisper `transcribe()` source
([transcribe.py](https://raw.githubusercontent.com/SYSTRAN/faster-whisper/master/faster_whisper/transcribe.py)):

- **`beam_size`** default **5**; LocalFlow uses `1` in *both* the live loop and
  the final pass. Bump the **final** pass to 5 for the pasted output; keep live
  preview at 1 for speed.
- **`hotwords`** — "Hotwords/hint phrases to provide the model with. **Has no
  effect if prefix is not None.**" LocalFlow doesn't set `prefix`, so hotwords do
  apply — but note both `hotwords` and `initial_prompt` bias only the **first
  window**. For dictations longer than one 30 s window, later windows rely on
  `condition_on_previous_text` to carry vocabulary. The regex `CORRECTIONS` map is
  the correct backstop for that. [Exact multi-window hotword behavior: verify
  against source before relying on it — the docstring only guarantees first-window
  effect.]
- **`initial_prompt`** — "prompt for the first window." Passing the dictionary
  here *in addition to* `hotwords` is a common way to strengthen vocabulary bias.
- **`condition_on_previous_text`** default **True**; "disabling may make the text
  inconsistent across windows, but the model becomes less prone to getting stuck
  in a failure loop, such as repetition looping." Current code correctly disables
  it in the *live* loop (avoids loops on partial audio) and leaves it True for the
  final pass (coherence). Keep this split.
- **`word_timestamps`** default **False** — "Extract word-level timestamps using
  the cross-attention pattern and dynamic time warping." Enabling it is the
  primitive you'd need for a future "whisper-to-edit"/undo-by-word feature.
- Failure thresholds you get for free: `no_speech_threshold` 0.6,
  `log_prob_threshold` -1.0, `compression_ratio_threshold` 2.4, temperature
  fallback list `[0.0…1.0]`.

### 2c. VAD and the streaming loop
- **`vad_filter`** default is **False**; LocalFlow correctly sets it True. It uses
  the Silero VAD; faster-whisper v1.0.3 upgraded to **Silero V5**, and v1.1.0 made
  the "VAD filter 3× faster on CPU." [releases](https://github.com/SYSTRAN/faster-whisper/releases).
  The README describes the default as "conservative," removing silence longer than
  ~2 s. Exact `VadOptions` defaults (`min_silence_duration_ms`, `speech_pad_ms`,
  `max_speech_duration_s`, `threshold`) were **[unverified]** — read the
  `VadOptions` class before tuning; tightening `min_silence_duration_ms` typically
  improves dictation segmentation.
- **Silero VAD itself:** "One audio chunk (30+ ms) takes less than 1 ms to be
  processed on a single CPU thread," supports 8 kHz and 16 kHz.
  [snakers4/silero-vad](https://github.com/snakers4/silero-vad). Cheap enough to
  run continuously on the mic stream.
- **The current live-preview design re-transcribes the last 30 s of audio every
  2 s** (`audio[-SAMPLE_RATE*30:]`). That is O(clip length × passes) wasted GPU
  work and causes preview text to shift as the window slides. A better shape:
  run Silero VAD on the incoming stream, and when it detects an utterance-final
  pause, transcribe only the *new* finalized segment and append it — closer to
  how real streaming ASR works, lower latency, no recompute. This is medium
  effort but is the single biggest structural latency win.

---

## 3. Cleanup-LLM quality within the VRAM budget

### 3a. Model fit alongside Whisper
Ollama download sizes (Q4_K_M default) — a proxy for VRAM weight footprint,
before KV cache:

| Model | Download size | Context |
|---|---|---|
| qwen2.5:0.5b | 398 MB | 32K |
| qwen2.5:1.5b | 986 MB | 32K |
| **qwen2.5:3b** | **1.9 GB** | 32K |
| qwen2.5:7b | 4.7 GB | 32K |

Source: [ollama.com/library/qwen2.5](https://ollama.com/library/qwen2.5).
With distil-large-v3.5 (~1.5–2 GB in int8_float16) + qwen2.5:3b (~1.9 GB) + KV
caches + CUDA context, the pair fits in 6 GB — which is why the memory note warns
that larger models (gemma3:4b) get evicted. **Keep qwen2.5:3b as the default.**
qwen2.5:1.5b (986 MB) is the fallback if VRAM pressure appears; it trades cleanup
quality for headroom.

The code already sets `num_ctx=2048` — sensible, since KV-cache VRAM scales with
context and dictations are short. Ollama's default `keep_alive` is **5m**
([API](https://github.com/ollama/ollama/blob/main/docs/api.md)); LocalFlow's `2h`
correctly keeps the model warm, and the `/api/ps` sweep that unloads other models
(`keep_alive: 0`) is the right defensive move on a memory-tight box.

### 3b. Making cleanup more reliable / lower-latency
- **Stream the response.** `/api/chat` and `/api/generate` stream by default;
  the code sets `"stream": false`. Streaming lets you render polished text into
  the overlay token-by-token and reduces *perceived* latency even if total time is
  unchanged. [Ollama API](https://github.com/ollama/ollama/blob/main/docs/api.md).
- **Cap output with `num_predict`.** Cleanup output length ≈ input length; setting
  `num_predict` to a function of input tokens is a harder guard than the current
  post-hoc length check (which discards work after generating it). The `options`
  object supports `num_predict`, `temperature`, `top_k`, `top_p`, `seed`.
  [Ollama API](https://github.com/ollama/ollama/blob/main/docs/api.md).
- **`seed` for reproducibility** when debugging prompt changes. Same source.
- **Structured output is available but probably not worth it here.** Ollama's
  `format` accepts a JSON schema and they recommend `temperature: 0` and adding
  "return as JSON" to the prompt. [structured-outputs blog](https://ollama.com/blog/structured-outputs).
  For prose cleanup this adds schema overhead for no real gain; reserve it for
  Command Mode features that return structured edits (e.g. `{op, text}`).
- **Prompt.** The current `CLEANUP_PROMPT` already follows good practice (explicit
  rules, "output ONLY the cleaned text," no summarizing). Lowering `temperature`
  from 0.1 → 0 would make cleanup more deterministic per Ollama's own guidance.

---

## 4. Paste / injection reliability on Windows

The current path: copy text → `time.sleep(0.05)` → simulate `Ctrl+V` via pynput →
restore old clipboard after a fixed `time.sleep(1.0)`. Primary-source failure
modes:

- **UIPI blocks injection into higher-integrity windows.** `SendInput` (which
  pynput's keyboard controller calls into) "is subject to UIPI. Applications are
  permitted to inject input only into applications that are at an equal or lesser
  integrity level." Worse: "**neither GetLastError nor the return value will
  indicate the failure was caused by UIPI blocking.**" So `Ctrl+V` into an
  elevated/admin window (an admin terminal, Task Manager, some installers) fails
  **silently** unless LocalFlow itself runs elevated.
  [SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput).
- **Injected input can be blocked by another thread.** "If the function returns
  zero, the input was already blocked by another thread." The current code never
  checks a return value (pynput hides it), so a dropped paste is invisible. Same
  source.
- **Keyboard-state races.** "This function does not reset the keyboard's current
  state. Any keys that are already pressed when the function is called might
  interfere… check the keyboard's state with `GetAsyncKeyState`." If the user is
  still physically holding `Ctrl`/`Alt` from the hotkey when the paste fires, the
  synthesized `Ctrl+V` can be corrupted. Same source.
- **The 1 s clipboard-restore is a guessed race.** Restoring the previous
  clipboard after a fixed `time.sleep(1.0)` assumes the target app has finished
  reading the clipboard within 1 s. Slow apps (heavy Electron apps, RDP) may still
  be reading when the restore fires, pasting the *old* content; fast interactions
  where the user copies something else within 1 s get clobbered. There is no
  primary "correct delay" — the robust fix is to not race at all (see below).

**On-device alternatives (primary sources):**
- **Direct Unicode injection via `SendInput` + `KEYEVENTF_UNICODE`**, bypassing the
  clipboard entirely. "If `KEYEVENTF_UNICODE` is specified, `SendInput` sends a
  `WM_KEYDOWN`/`WM_KEYUP`… passing the message to `TranslateMessage` posts a
  `WM_CHAR` message with the Unicode character." `wVk` must be 0 and the character
  goes in `wScan`. Microsoft explicitly frames this as the mechanism for
  "nonkeyboard-input methods—such as handwriting recognition or voice
  recognition—as if it were text input."
  [KEYBDINPUT / KEYEVENTF_UNICODE](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-keybdinput).
  Trade-offs: no clipboard clobber and no `Ctrl+V` interception, but it's slower
  for long text, some apps debounce fast synthetic `WM_CHAR`, and it's still
  subject to the same UIPI integrity limit.

**Recommended paste strategy (robust ordering, no fixed sleeps):**
1. Prefer clipboard `Ctrl+V` (fast, preserves rich behavior), but:
2. Snapshot the old clipboard, set the new text, paste, and **restore on the next
   clipboard-change event or a bounded poll** rather than a blind 1 s timer
   (Win32 clipboard-format listener) — [unverified as the single best pattern, but
   it removes the guessed delay].
3. Ensure hotkey modifiers are released first (`GetAsyncKeyState`) before
   synthesizing `Ctrl+V`.
4. **Fallback to `KEYEVENTF_UNICODE` per-character injection** when the paste
   target rejects `Ctrl+V` (terminals with custom paste, apps that intercept it).
5. Detect elevated foreground windows and surface a clear "can't paste into admin
   window unless LocalFlow runs elevated" message instead of failing silently.

This is item #3 in the ranked list — cheap relative to its reliability payoff,
because silent paste failures are the worst possible UX for a dictation tool.

---

## 5. Architecture / robustness gaps

These block *quality* indirectly by making the pipeline hard to change safely.

- **No tests, pipeline untestable without GPU + Ollama.** `App` constructs the
  `WhisperModel` and talks to Ollama over a module-level `requests.Session` in its
  own methods, so nothing can be exercised without hardware. Extract the pure
  stages — `fix_terms`, `quick_clean`, the length-guard logic in `cleanup_text`,
  the preview line-wrapping — behind small functions (some already are) and inject
  the Whisper/Ollama clients so they can be faked. That makes the CORRECTIONS
  regexes and the "reject rogue LLM output" guard unit-testable with zero GPU.
- **Config is hardcoded constants.** Model names, hotkey, `num_ctx`, timeouts,
  `DICTIONARY`, `CORRECTIONS`, prompts are all literals in the file. A small
  `config.toml`/JSON (with the dictionary persisted, per §1) is the prerequisite
  for the per-app tone profiles and the learning dictionary.
- **No latency/quality metrics surfaced.** The code already times transcription
  and cleanup (`t0…t2`) and logs them, but nothing reaches the user. Surfacing
  "transcribe 0.8 s / clean 1.2 s" in the overlay or tray tooltip would make
  regressions visible and let the user feel the wins from §2–§3.
- **Threading model is ad-hoc.** Multiple daemon threads (`_live_preview`,
  `_process`, `_warm_ollama`, `threading.Timer` for safety-stop and
  clipboard-restore) coordinate through booleans (`recording`, `busy`) plus two
  locks. It works for a single user but has sharp edges: the safety-stop `Timer`
  and the hotkey can both enter `toggle`, and the clipboard-restore `Timer`
  outlives the pipeline. A single owned state machine (idle → recording →
  processing → idle) with explicit transitions would remove the boolean-flag
  races.
- **Error recovery is coarse.** A failed Ollama call falls back to `quick_clean`
  (good), but a mid-dictation Whisper/CUDA fault only breaks the live loop and is
  logged; there's no user-visible "GPU fell over, restart" signal beyond the log
  file. The GPU→CPU fallback happens only at startup, not if CUDA dies later.

None of these are Wispr-parity features, but they are what let you land items
#1–#8 without regressing the app.

---

## Appendix — source list

Primary sources cited above:
- Wispr Flow site & docs: [wisprflow.ai](https://wisprflow.ai/),
  [features](https://wisprflow.ai/features),
  [Context Awareness](https://docs.wisprflow.ai/articles/4678293671-feature-context-awareness),
  [Flow Styles](https://docs.wisprflow.ai/articles/2368263928-how-to-setup-flow-styles),
  [Command Mode](https://docs.wisprflow.ai/articles/4816967992-how-to-use-command-mode),
  [Multiple languages](https://docs.wisprflow.ai/articles/3191899797-use-flow-with-multiple-languages),
  [Data Controls](https://wisprflow.ai/data-controls)
- ASR: [OpenAI Whisper](https://github.com/openai/whisper),
  [faster-whisper README](https://github.com/SYSTRAN/faster-whisper),
  [faster-whisper releases](https://github.com/SYSTRAN/faster-whisper/releases),
  [faster-whisper transcribe.py](https://raw.githubusercontent.com/SYSTRAN/faster-whisper/master/faster_whisper/transcribe.py),
  [distil-whisper repo](https://github.com/huggingface/distil-whisper),
  [distil-large-v3.5 card](https://huggingface.co/distil-whisper/distil-large-v3.5),
  [Silero VAD](https://github.com/snakers4/silero-vad),
  [CTranslate2 quantization](https://opennmt.net/CTranslate2/quantization.html)
- LLM: [Ollama API docs](https://github.com/ollama/ollama/blob/main/docs/api.md),
  [Ollama structured outputs](https://ollama.com/blog/structured-outputs),
  [qwen2.5 on Ollama](https://ollama.com/library/qwen2.5)
- Windows injection: [SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput),
  [KEYBDINPUT / KEYEVENTF_UNICODE](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-keybdinput)
