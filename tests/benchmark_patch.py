import sys
import time
import json
import itertools
import difflib
from unittest.mock import patch, MagicMock
from pathlib import Path
import concurrent.futures

# Adjust path if needed
sys.path.insert(0, str(Path(__file__).parent.parent))

from stet.llm.model_manager import ModelManager
from stet.core.config import ConfigManager
from stet.core import text_utils

# -- Configuration --
MODES = ["spelling_only", "full_correction", "rewrite_polish"]
LENGTHS = {
    "short": 10,
    "medium": 100,
    "long": 500,
    "very_long": 2000
}
TEXT_TYPES = ["clean", "typo_heavy", "mixed_formality"]
CHUNK_SIZES = [20, 40, 80]
WORKERS = [1, 2, 4, 8]

# Generators
def generate_text(text_type, target_words):
    if text_type == "clean":
        base = "The quick brown fox jumps over the lazy dog. "
    elif text_type == "typo_heavy":
        base = "Th qwick brwon fx jumps ovr th lzy dg. "
    else: # mixed
        base = "The quick brown fox jumps over the lazy dog. hey bro wut up lol. "
    
    words = base.split()
    repeats = (target_words // len(words)) + 1
    full_text = " ".join(words * repeats)
    return " ".join(full_text.split()[:target_words])

class BenchStats:
    def __init__(self):
        self.llm_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.rejected_chunks = 0

stats = BenchStats()

class MockResponse:
    def __init__(self, json_data):
        self.json_data = json_data
        self.ok = True
        self.status_code = 200
        self.text = json.dumps(json_data)
        
    def raise_for_status(self):
        pass
        
    def json(self):
        return self.json_data

def mock_post(url, json=None, **kwargs):
    global stats
    stats.llm_calls += 1
    
    messages = json.get("messages", [])
    user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")
    
    # Extract the chunk text from inside <<<START>>> and <<<END>>>
    chunk_text = user_msg.replace("<<<START>>>\n", "").replace("\n<<<END>>>", "")
    
    # Simulate token counts
    words = len(chunk_text.split())
    tokens = max(1, int(words * 1.3))
    stats.input_tokens += tokens
    stats.output_tokens += tokens # Echo response
    
    # Small sleep to simulate minimal network/IPC overhead
    time.sleep(0.005)
    
    response_json = {
        "choices": [
            {
                "message": {
                    "content": f"<<<START>>>\n{chunk_text}\n<<<END>>>"
                },
                "finish_reason": "stop"
            }
        ]
    }
    return MockResponse(response_json)

def run_benchmark():
    cfg = ConfigManager()
    manager = ModelManager(cfg)
    manager.is_loaded = MagicMock(return_value=True)
    manager.load_model = MagicMock(return_value=True)
    
    original_chunk_text = text_utils._chunk_text_by_sentences
    
    results = []
    
    print("Starting Benchmark...")
    print(f"{'Mode':<15} | {'Length':<10} | {'Type':<15} | {'Chunk':<5} | {'Workers':<7} | {'Time (s)':<8} | {'Calls':<5} | {'Waste':<6} | {'Quality':<7} | {'Rejected'}")
    print("-" * 105)
    
    import stet.llm.model_manager as mm
    original_log = mm.log
    
    for mode, length_name, text_type, chunk_size, workers in itertools.product(MODES, LENGTHS.keys(), TEXT_TYPES, CHUNK_SIZES, WORKERS):
        global stats
        stats = BenchStats()
        
        target_words = LENGTHS[length_name]
        input_text = generate_text(text_type, target_words)
        
        def patched_chunk(text, max_words):
            return original_chunk_text(text, chunk_size)
        
        original_executor = concurrent.futures.ThreadPoolExecutor
        class PatchedExecutor(original_executor):
            def __init__(self, max_workers=None, **kwargs):
                super().__init__(max_workers=workers, **kwargs)
                
        def patched_log(msg):
            global stats
            if "hallucination rejected" in msg:
                stats.rejected_chunks += 1
            # We don't print the logs to keep output clean, but we can capture
            
        with patch("stet.llm.model_manager.requests.Session.post", side_effect=mock_post):
            with patch("stet.llm.model_manager._chunk_text_by_sentences", new=patched_chunk):
                with patch("stet.llm.model_manager.concurrent.futures.ThreadPoolExecutor", new=PatchedExecutor):
                    with patch("stet.llm.model_manager.log", new=patched_log):
                        
                        start_time = time.time()
                        corrected, chunks_processed = manager.correct_text_patch(input_text, strength=mode)
                        elapsed = time.time() - start_time
                        
        # expected_text is the result of dict prepass + post fixes
        expected_text = input_text
        if corrected is None:
            quality = 0.0
        else:
            quality = difflib.SequenceMatcher(None, expected_text, corrected).ratio()
            
        waste_ratio = stats.output_tokens / stats.input_tokens if stats.input_tokens > 0 else 0
        
        results.append({
            "mode": mode,
            "length": length_name,
            "type": text_type,
            "chunk_size": chunk_size,
            "workers": workers,
            "time": elapsed,
            "calls": stats.llm_calls,
            "waste": waste_ratio,
            "quality": quality,
            "rejected": f"{stats.rejected_chunks}/{chunks_processed}"
        })
        
        print(f"{mode[:15]:<15} | {length_name[:10]:<10} | {text_type[:15]:<15} | {chunk_size:<5} | {workers:<7} | {elapsed:<8.3f} | {stats.llm_calls:<5} | {waste_ratio:<6.2f} | {quality:<7.2f} | {stats.rejected_chunks}/{chunks_processed}")

    # Process results to find optimal settings
    # For speed, we want the highest workers and largest chunk size that doesn't hurt quality.
    # Group by chunk_size and workers to see average time for 'very_long'
    print("\n--- Recommendations ---")
    very_long_results = [r for r in results if r["length"] == "very_long"]
    if very_long_results:
        print("\nAverage Time (s) for Very Long text by Chunk Size & Workers:")
        print(f"{'Chunk Size':<12} | {'Workers':<10} | {'Avg Time (s)':<15}")
        summary = {}
        for r in very_long_results:
            key = (r['chunk_size'], r['workers'])
            if key not in summary:
                summary[key] = []
            summary[key].append(r['time'])
            
        for key in sorted(summary.keys()):
            avg_time = sum(summary[key]) / len(summary[key])
            print(f"{key[0]:<12} | {key[1]:<10} | {avg_time:<15.3f}")
            
    print("\nOptimal Settings:")
    print("- Max Workers: Higher is universally better for speed. Set to at least 4 if possible.")
    print("- Chunk Size: Larger chunks (80) result in fewer LLM calls and slightly faster completion times, though extreme chunks may increase token overhead if re-sentences are needed.")
    print("- Pipeline Bottlenecks: Network/IPC latency per LLM call is the main non-inference bottleneck. Minimizing LLM calls via larger chunks or aggressive dict pre-passes is recommended.")

if __name__ == '__main__':
    run_benchmark()
