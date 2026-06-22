import sys
import os
import time
from pathlib import Path
from PyQt6.QtCore import QCoreApplication

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from stet.core.config import ConfigManager
from stet.llm.model_manager import ModelManager
from stet.core.text_utils import looks_like_prose

# Massive sample text (~600 words) with typical spelling, grammar, punctuation mistakes,
# and some complex sentences to test context preservation.
BENCHMARK_TEXT = """
Yesterday, I went to the office by walk because my car had a breakdown. On the way, I saw a couple of people which were arguing very loudly about something unimportant. I think they was talking about a football match that happened the day before. Actually, it was quite funny because they both had completely wrong facts, but neither of them wanted to admit it. If they had checked their mobile phones, they would have found the real score immediately. But people is always like that; they prefer to argue rather than admit they are wrong.

When I finally arrived to the office, my manager was already waiting for me. He was very angry because I was late for the morning meeting. I tried to explain him the situation, but he did not wanted to listen. He said that my lack of punctuality is a recurring issue, which is not true because I am always on time. However, I decided not to argue back because it would only make things worse. Instead, I just apologized and went straight to my desk.

During the afternoon, I had to write a long report about our recent sales performance. The data was very complex and it took me hours to analyze it. I made several errors in the draft, but fortunately my colleague pointed them out before I submitted it. For example, I wrote "recieve" instead of "receive" and "accomodate" instead of "accommodate". These spelling mistakes is very common for me, especially when I am tired or under pressure. I need to be more careful in the future.

After finishing the report, I had a brief meeting with the client. They was very satisfied with our proposals and agreed to sign the contract next week. This is a big win for our company because we have been working on this deal for months. I hope everything goes smoothly and there are no unexpected problems.

In the evening, I returned back home. I was so exhausted that I could not even cook dinner. I just ordered some pizza and watched a movie. It was a long and stressful day, but at least the ending was pleasant. I think I will sleep very well tonight.
"""

def main():
    print("Initializing benchmark...")
    # Initialize Qt Core Application
    qt_app = QCoreApplication(sys.argv)  # noqa: F841

    # Initialize Config Manager
    cfg = ConfigManager()
    
    # Verify model exists
    model_path = cfg.get("model_path", "")
    if not model_path or not os.path.exists(model_path):
        print(f"Error: Model not found at '{model_path}'. Make sure a valid model is configured.")
        return

    print(f"Using model: {Path(model_path).name}")

    # Set custom port to avoid conflict with running app
    cfg.set("server_port", 8089)
    cfg.set("keep_model_loaded", True)

    # Instantiate Model Manager
    manager = ModelManager(cfg)

    # Connect status changed signal to print progress
    manager.status_changed.connect(lambda msg: print(f"[Server Status] {msg}"))

    print("Starting server on port 8089...")
    try:
        manager.load_model()
    except Exception as e:
        print(f"Failed to load model: {e}")
        return

    # Wait for model server to be fully ready
    import requests
    ready = False
    for _ in range(60):
        try:
            if requests.get("http://127.0.0.1:8089/health", timeout=1).status_code == 200:
                ready = True
                break
        except requests.exceptions.RequestException:
            pass
        time.sleep(1)

    if not ready:
        print("Error: Server failed to reach ready state.")
        manager.unload_model()
        return

    print("\nServer is ready. Starting chunk size matrix evaluation...\n")
    
    # Tested chunk sizes
    chunk_sizes = [30, 45, 60, 80, 100, 120, 150]
    results = []

    for size in chunk_sizes:
        print(f"--- Evaluating Chunk Size: {size} words ---")
        cfg.set("patch_chunk_size", size)
        
        # Track timing
        start_time = time.time()
        
        # Run correction
        corrected, units_processed = manager.correct_text_patch(BENCHMARK_TEXT, strength="smart_fix")
        
        elapsed = time.time() - start_time
        
        if corrected is None:
            print("  Failed: correction returned None (likely due to global guard rejection)")
            results.append({
                "size": size,
                "latency": elapsed,
                "words_per_sec": 0,
                "success_rate": 0.0,
                "diff_ratio": 0.0
            })
            continue

        # Evaluate quality / compliance
        # Count original chunks vs successfully corrected ones
        from stet.core.text_utils import _chunk_text_by_sentences
        original_chunks = _chunk_text_by_sentences(BENCHMARK_TEXT, size)
        
        # Count how many chunks were actually modified (prose check + guards passed)
        prose_chunks = 0
        for chunk_text, _ in original_chunks:
            if chunk_text.strip() and looks_like_prose(chunk_text):
                prose_chunks += 1
        
        attempted = prose_chunks
        # success_rate represents how many chunks were processed
        success_rate = (units_processed / attempted) if attempted > 0 else 1.0

        # Calculate character similarity to see quality / editing rate
        import difflib
        o_clean = BENCHMARK_TEXT.replace(" ", "").replace("\n", "").lower()
        c_clean = corrected.replace(" ", "").replace("\n", "").lower()
        sim = difflib.SequenceMatcher(None, o_clean, c_clean).ratio()
        diff_ratio = 1.0 - sim

        words_count = len(BENCHMARK_TEXT.split())
        wps = words_count / elapsed if elapsed > 0 else 0

        print(f"  Completed in {elapsed:.2f}s ({wps:.1f} words/sec)")
        print(f"  Success rate (guards passed): {success_rate * 100:.1f}% ({units_processed}/{attempted} chunks)")
        print(f"  Divergence (edited text ratio): {diff_ratio * 100:.2f}%")

        results.append({
            "size": size,
            "latency": elapsed,
            "words_per_sec": wps,
            "success_rate": success_rate,
            "diff_ratio": diff_ratio
        })

    # Unload server
    print("\nShutting down server...")
    manager.unload_model()

    # Print Final Markdown Table
    print("\n" + "=" * 60)
    print("  BENCHMARK RESULTS")
    print("=" * 60)
    print("| Chunk Size | Latency (s) | Words/Sec | Guard Pass Rate | Divergence (Edited) |")
    print("|------------|-------------|-----------|-----------------|---------------------|")
    for r in results:
        print(f"| {r['size']:<10} | {r['latency']:<11.2f} | {r['words_per_sec']:<9.1f} | {r['success_rate']*100:<14.1f}% | {r['diff_ratio']*100:<18.2f}% |")
    print("=" * 60)

if __name__ == "__main__":
    main()
