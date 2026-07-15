"""Stress-test and benchmark LLM models for instruction following and performance.

Imports test samples from scripts.stress_test_samples and runs them against a specific
model or all recent models. Tracks generated tokens, prompt tokens, latency, and TPS.
Provides outputs for manual inspection.
"""

from __future__ import annotations

import os
import sys
import time
import json
import struct
import argparse
import requests
import psutil
from pathlib import Path

# Adjust sys.path to import stet modules
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stet.core.config import ConfigManager
from stet.llm.model_manager import ModelManager
from stet.constants import DEFAULT_TEMPLATES
from scripts.stress_test_samples import TEST_SAMPLES

# -- Baseline Model Path --
BASELINE_MODEL = r"E:\AI\LLM\lmstudio-community\gemma-4-12B-it-QAT-GGUF\gemma-4-12B-it-QAT-Q4_0.gguf"

# -- Intercept responses to extract token count usage --
original_post = requests.Session.post

current_run_tokens = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "api_elapsed": 0.0,
}

def intercepted_post(self, url, *args, **kwargs):
    # Skip counting for raw manual requests (like streaming) since we count them separately
    if kwargs.get("headers", {}).get("X-Test-Stream") == "true":
        return original_post(self, url, *args, **kwargs)
        
    start = time.perf_counter()
    resp = original_post(self, url, *args, **kwargs)
    elapsed = time.perf_counter() - start
    
    try:
        if resp.ok:
            data = resp.json()
            if "usage" in data:
                usage = data["usage"]
                current_run_tokens["prompt_tokens"] += usage.get("prompt_tokens", 0)
                current_run_tokens["completion_tokens"] += usage.get("completion_tokens", 0)
                current_run_tokens["api_elapsed"] += elapsed
    except Exception:
        pass
    return resp

requests.Session.post = intercepted_post


def kill_existing_servers():
    """Ensure any running llama-server.exe is terminated to free port 8080."""
    print("Terminating existing llama-server instances...")
    count = 0
    for proc in psutil.process_iter(["name"]):
        if proc.info["name"] == "llama-server.exe":
            try:
                proc.kill()
                count += 1
            except Exception:
                pass
    if count:
        print(f"Killed {count} llama-server process(es).")
        time.sleep(1.5)


def parse_gguf_block_count(path: str) -> int | None:
    """Read the number of transformer layers from the GGUF file header."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
            if magic != b"GGUF":
                return None
            f.seek(8, 1) # Skip version and tensor count
            kv_count = struct.unpack("<Q", f.read(8))[0]
            
            for _ in range(kv_count):
                key_len_bytes = f.read(8)
                if not key_len_bytes:
                    break
                key_len = struct.unpack("<Q", key_len_bytes)[0]
                key = f.read(key_len).decode("utf-8", errors="ignore")
                
                val_type = struct.unpack("<I", f.read(4))[0]
                
                if val_type == 8: # String
                    val_len = struct.unpack("<Q", f.read(8))[0]
                    val = f.read(val_len).decode("utf-8", errors="ignore")
                elif val_type in (0, 1, 7): # 1 byte
                    val = struct.unpack("<B", f.read(1))[0]
                elif val_type in (2, 3): # 2 bytes
                    val = struct.unpack("<H", f.read(2))[0]
                elif val_type in (4, 5, 6): # 4 bytes
                    val = struct.unpack("<I" if val_type != 6 else "<f", f.read(4))[0]
                elif val_type in (10, 11, 12): # 8 bytes
                    val = struct.unpack("<Q", f.read(8))[0]
                elif val_type == 9: # Array
                    _ = struct.unpack("<I", f.read(4))[0]
                    _ = struct.unpack("<Q", f.read(8))[0]
                    # Skip array header parsing, break out
                    break
                else:
                    break
                    
                if "block_count" in key:
                    return val
    except Exception as e:
        print(f"Error reading block count: {e}")
    return None


def calculate_gpu_layers(model_path: str, target_gpu_gb: float = 5.0) -> int:
    """Calculate n_gpu_layers to fit target_gpu_gb on the GPU."""
    block_count = parse_gguf_block_count(model_path)
    if block_count is None:
        block_count = 48 # Fallback for Gemma-12B
        print(f"Could not read block count for {model_path}. Using fallback: {block_count}")
    
    file_size_bytes = os.path.getsize(model_path)
    file_size_gb = file_size_bytes / (1024**3)
    
    # Non-layer weights overhead (embeddings, heads, vocab) estimated at 1.5 GB
    base_gb = 1.5
    
    if file_size_gb <= target_gpu_gb:
        print(f"Model fits completely in target GPU memory ({file_size_gb:.2f} GB <= {target_gpu_gb:.2f} GB). Offloading all layers.")
        return block_count
    
    layers_gb = file_size_gb - base_gb
    target_layers_gb = target_gpu_gb - base_gb
    
    if target_layers_gb <= 0:
        return 0
        
    fraction = target_layers_gb / layers_gb
    n_gpu_layers = int(round(block_count * fraction))
    n_gpu_layers = min(block_count, max(0, n_gpu_layers))
    
    print(f"Model size: {file_size_gb:.2f} GB | Target GPU: {target_gpu_gb:.2f} GB")
    print(f"Estimated layers offload: {n_gpu_layers} / {block_count} layers (~{fraction*100:.1f}%)")
    return n_gpu_layers


def run_streaming_request(manager: ModelManager, text: str) -> tuple[str, int, int, float]:
    """Execute a raw HTTP request to llama-server with stream=True and parse chunks."""
    # Build messages using the manager's normal wrapping for full_correction
    messages = manager._build_correction_messages(text, None, "full_correction")
    
    # Output budget: ~1.4x word count + 16 headroom
    word_count = len(text.split())
    max_tokens = min(int(word_count * 1.4) + 16, 2048)
    
    payload = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "top_k": 1,
        "stream": True,
        "cache_prompt": True
    }
    
    session = manager._get_session()
    url = manager._chat_url()
    
    start_time = time.perf_counter()
    resp = session.post(url, json=payload, stream=True, timeout=60, headers={"X-Test-Stream": "true"})
    resp.raise_for_status()
    
    full_output = ""
    completion_tokens = 0
    prompt_tokens = 0
    
    for line in resp.iter_lines():
        if line:
            line_str = line.decode('utf-8', errors='ignore').strip()
            if line_str.startswith("data:"):
                data_json = line_str[5:].strip()
                if data_json == "[DONE]":
                     break
                try:
                    parsed = json.loads(data_json)
                    choice = parsed["choices"][0]
                    delta = choice.get("delta", {})
                    content = delta.get("content", "")
                    full_output += content
                    
                    # Accumulate token metrics if provided in chunk
                    usage = parsed.get("usage")
                    if usage:
                        completion_tokens = usage.get("completion_tokens", completion_tokens)
                        prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                except Exception:
                    pass
                    
    elapsed = time.perf_counter() - start_time
    
    # Fallback to estimate if usage metrics are stripped in stream packets
    if completion_tokens == 0:
        completion_tokens = len(full_output.split())  # raw fallback
    
    # Strip markers
    import re
    from stet.core.text_utils import strip_thinking_tokens, strip_meta_commentary
    cleaned = strip_meta_commentary(strip_thinking_tokens(full_output))
    cleaned = re.sub(r"<<<\s*START\s*>>>\s*", "", cleaned, count=1)
    cleaned = re.sub(r"\s*<<<\s*END\s*>>>", "", cleaned, count=1)
    
    return cleaned.strip(), prompt_tokens, completion_tokens, elapsed


def run_model_benchmark(model_path: str, gpu_layers: int, sample_ids: list[str] | None, templates: list[dict], cfg: ConfigManager) -> dict:
    model_name = os.path.basename(model_path)
    print("\n========================================================")
    print(f"EVALUATING MODEL: {model_name} (gpu_layers={gpu_layers})")
    print("========================================================")
    
    # Kill any active llama-server instances to boot a fresh one
    kill_existing_servers()
    
    # Save active model path to restore it later
    original_model = cfg.get("model_path", "")
    original_layers = cfg.get("gpu_layers", 99)
    
    # Set configs for the run
    cfg.set("model_path", model_path)
    cfg.set("gpu_layers", gpu_layers)
    
    manager = ModelManager(cfg)
    
    # Reset counters
    current_run_tokens["prompt_tokens"] = 0
    current_run_tokens["completion_tokens"] = 0
    current_run_tokens["api_elapsed"] = 0.0
    
    # Load model and measure boot time
    boot_start = time.perf_counter()
    loaded = manager.load_model()
    boot_time = time.perf_counter() - boot_start
    
    if not loaded:
        print(f"FAILED to load model {model_name}")
        # Restore configuration
        cfg.set("model_path", original_model)
        cfg.set("gpu_layers", original_layers)
        return {"loaded": False, "reason": "Server boot timed out or failed"}
        
    print(f"Successfully booted model in {boot_time:.2f} seconds.")
    
    # Warmup prompt cache
    manager._warmup_prompt_cache()
    
    model_evals = []
    
    # Filter samples
    samples_to_run = TEST_SAMPLES
    if sample_ids:
        samples_to_run = [s for s in TEST_SAMPLES if s["id"] in sample_ids]
        
    for sample in samples_to_run:
        sample_id = sample["id"]
        print(f"  Running test case: '{sample_id}'")
        
        # 1. Spelling Only (Patch)
        start = time.perf_counter()
        out_spelling, _ = manager.correct_text_patch(sample["input"], strength="spelling_only")
        t_spelling = time.perf_counter() - start
        model_evals.append({
            "sample": sample_id,
            "mode": "spelling_only",
            "elapsed": t_spelling,
            "output": out_spelling
        })
        
        # 2. Full Correction (Patch)
        start = time.perf_counter()
        out_full, _ = manager.correct_text_patch(sample["input"], strength="full_correction")
        t_full = time.perf_counter() - start
        model_evals.append({
            "sample": sample_id,
            "mode": "full_correction",
            "elapsed": t_full,
            "output": out_full
        })
        
        # 3. Rewrite & Polish (Patch)
        start = time.perf_counter()
        out_polish, _ = manager.correct_text_patch(sample["input"], strength="rewrite_polish")
        t_polish = time.perf_counter() - start
        model_evals.append({
            "sample": sample_id,
            "mode": "rewrite_polish",
            "elapsed": t_polish,
            "output": out_polish
        })
        
        # 4. Streaming run (Full Correction)
        try:
            out_stream, pr_tok, gen_tok, t_stream = run_streaming_request(manager, sample["input"])
            # Accumulate streaming metrics manually
            current_run_tokens["prompt_tokens"] += pr_tok
            current_run_tokens["completion_tokens"] += gen_tok
            current_run_tokens["api_elapsed"] += t_stream
        except Exception as e:
            out_stream = f"Streaming failed: {e}"
            t_stream = 0.0
            
        model_evals.append({
            "sample": sample_id,
            "mode": "streaming_full_correction",
            "elapsed": t_stream,
            "output": out_stream
        })
        
        # 5. Run all configured templates dynamically
        for template in templates:
            t_name = template["name"]
            t_prompt = template["prompt"]
            start = time.perf_counter()
            # Select default patch strength based on template category
            strength = "rewrite_polish" if any(x in t_name.lower() for x in ("polish", "professional", "academic", "notes")) else "full_correction"
            
            out_val, _ = manager.correct_text_patch(
                sample["input"],
                strength=strength,
                mode_prompt_override=t_prompt
            )
            t_elapsed = time.perf_counter() - start
            model_evals.append({
                "sample": sample_id,
                "mode": t_name,
                "elapsed": t_elapsed,
                "output": out_val
            })

    # Shut down model
    manager.unload_model()
    kill_existing_servers()
    
    # Restore original settings
    cfg.set("model_path", original_model)
    cfg.set("gpu_layers", original_layers)
    
    total_gen_tokens = current_run_tokens["completion_tokens"]
    total_prompt_tokens = current_run_tokens["prompt_tokens"]
    total_api_elapsed = current_run_tokens["api_elapsed"]
    
    tps = total_gen_tokens / total_api_elapsed if total_api_elapsed > 0 else 0.0
    
    return {
        "loaded": True,
        "boot_time_sec": boot_time,
        "gpu_layers": gpu_layers,
        "completion_tokens": total_gen_tokens,
        "prompt_tokens": total_prompt_tokens,
        "api_time_sec": total_api_elapsed,
        "generation_tps": tps,
        "evals": model_evals
    }


def write_report(results: dict, path: Path, run_samples: list[dict]):
    """Write markdown summary report of the stress test results."""
    md = []
    md.append("# LLM Matrix Stress Test & Performance Report")
    md.append(f"\nGenerated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    md.append("\n## Model Comparison Summary\n")
    md.append("| Model Name | Status | GPU Layers | Boot (s) | Prompt Tok | Gen Tok | Gen Time (s) | TPS |")
    md.append("|---|---|---|---|---|---|---|---|")
    
    for name, r in results.items():
        if not r["loaded"]:
            md.append(f"| {name} | **Failed** | - | - | - | - | - | - |")
        else:
            md.append(
                f"| {name} | Loaded | {r['gpu_layers']} | {r['boot_time_sec']:.1f}s | "
                f"{r['prompt_tokens']} | {r['completion_tokens']} | {r['api_time_sec']:.1f}s | "
                f"**{r['generation_tps']:.2f}** |"
            )
            
    md.append("\n\n## Raw Outputs for Manual Verification\n")
    
    for sample in run_samples:
        md.append(f"## Scenario ID: `{sample['id']}` ({sample['format']})")
        md.append("**Original Input:**")
        md.append("```")
        md.append(sample["input"])
        md.append("```")
        md.append("")
        
        for name, r in results.items():
            if not r["loaded"]:
                continue
            
            md.append(f"### Model: **{name}**")
            
            # Find evals for this sample
            sample_evals = [ev for ev in r["evals"] if ev["sample"] == sample["id"]]
            
            for ev in sample_evals:
                md.append(f"#### Mode: `{ev['mode']}` (Time: {ev['elapsed']:.2f}s)")
                md.append("```")
                md.append(ev["output"] if ev["output"] else "[No Output]")
                md.append("```")
                md.append("")
        
        md.append("---")
        md.append("")
            
    path.write_text("\n".join(md), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Evaluate Stet models on a comprehensive stress-test suite.")
    parser.add_argument("--model", type=str, help="Specific GGUF model path to test. If omitted, runs all recent models.")
    parser.add_argument("--sample", action="append", help="Specific sample IDs to run. If omitted, runs all 25 samples.")
    parser.add_argument("--gpu-layers", type=int, help="Override GPU layers for the tested model(s).")
    parser.add_argument("--target-vram", type=float, default=5.0, help="Target VRAM for baseline model offloading in GB (default: 5.0).")
    parser.add_argument("--report", type=str, default="stress_test_report.md", help="Output path for the markdown report.")
    
    args = parser.parse_args()
    
    cfg = ConfigManager()
    
    # Grab template texts
    templates = cfg.get("custom_templates", []) or DEFAULT_TEMPLATES
        
    results = {}
    
    # Resolve models to test
    if args.model:
        if not os.path.exists(args.model):
            print(f"Error: Model path does not exist: {args.model}")
            sys.exit(1)
        models_to_test = [args.model]
    else:
        recent = list(cfg.get("recent_models", []))
        if BASELINE_MODEL not in recent:
            recent.append(BASELINE_MODEL)
        models_to_test = [m for m in recent if os.path.exists(m)]
        
    # Filter samples
    samples_to_run = TEST_SAMPLES
    if args.sample:
        samples_to_run = [s for s in TEST_SAMPLES if s["id"] in args.sample]
        
    print(f"Preparing to test {len(models_to_test)} model(s)...")
    
    for model_path in models_to_test:
        model_name = os.path.basename(model_path)
        
        # Determine GPU layers
        if args.gpu_layers is not None:
            gpu_layers = args.gpu_layers
        elif model_path == BASELINE_MODEL:
            gpu_layers = calculate_gpu_layers(model_path, target_gpu_gb=args.target_vram)
        else:
            gpu_layers = 99 # Default full offload for smaller models
            
        r = run_model_benchmark(model_path, gpu_layers, args.sample, templates, cfg)
        results[model_name] = r
        
    # Write report
    report_path = Path(args.report)
    write_report(results, report_path, samples_to_run)
    print(f"\nStress test complete! Report written to {report_path.resolve()}")


if __name__ == "__main__":
    main()
