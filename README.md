# Stet

<p align="center">
  <img src="logo.png" alt="Stet Logo" width="128" height="128">
</p>

<p align="center">
  <strong>Stet</strong> is a local, privacy-first AI autocorrect and text rewriting tool. Select text in any application, press a global hotkey, and instantly correct or rewrite it. Runs entirely offline on your machine — no API keys, cloud dependencies, or data leaks.
</p>

---

## ✨ Features

- **Local & Private**: Powered by `llama.cpp` — all inference runs entirely on your local hardware. No text ever leaves your machine.
- **4 Correction Strengths**:
  - **Spelling Only**: Fixes typos, spelling errors, and grammar without changing your tone or style.
  - **Full Correction**: Fixes grammar, punctuation, phrasing, and syntax.
  - **Rewrite & Polish**: Overhauls sentence structure for better flow, clarity, and vocabulary.
  - **Custom Patch**: Lets you apply custom rules or instructions to the selected text.
- **Smart Selection Capture**: Directly reads the highlighted text in the active window (supports console, text editors, IDEs, and browser windows).
- **Interactive Correction Window**: Edit the results, run custom prompt templates, or chat directly with the AI to refine your text.
- **Custom System Prompts**: Override standard correction rules to stream token-by-token corrections using your own system prompts.
- **System Tray Management**: Easily load or unload model weights from memory, toggle settings, and monitor model status in the background.

---

## ⌨️ Keyboard Shortcuts

- **`F9`**: Open the Correction and Chat Window.
- **`F10`**: Instantly correct selected text silently in the background and paste it back automatically.
- **`Ctrl+Enter`** (in popup): Accept the corrected text and paste it back into your original app.
- **`Escape`** (in popup): Close the popup and revert the text.

---

## 📥 Installation

### Option 1: Standalone Setup (Recommended)
1. Download `StetSetup.exe` from the latest release.
2. Run the installer to set up desktop/start-menu shortcuts.
3. Follow the built-in wizard prompts to automatically download the recommended LLM model.

### Option 2: Portable ZIP
1. Download and extract the latest `Stet_*.zip`.
2. Double-click `download_model.bat` to download the recommended model weights (~1.8 GB).
3. Run `run.bat` or `Stet.exe` to launch the application.

---

## 💻 System Requirements

- **OS**: Windows 10+ (64-bit).
- **GPU**: NVIDIA Graphics Card with CUDA 12.4 support (recommended for fast acceleration).
- **Model weights**: Gemma 4 E2B GGUF or similar model.

---

*Stet is open-source software distributed under the GNU GPL v3 license.*
