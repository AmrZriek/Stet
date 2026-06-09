import sys
import time
import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from stet.llm.model_manager import ModelManager
from stet.core.config import ConfigManager

TEXT_SAMPLE = """\
This is a comprehensive test of the new paragraph chunking and prompt evaluation.
It contains mutiple sentences. Some of them have typso that need fixing.

This is the second paragraph. It should be processed as a single chunk because we only split on double newlines now.
If the logic works correctly, this entire paragraph will be sent as one unit. The LLM will evaluate it and hopefully output exactly what we expect.
Since there are no typos in this specific paragraph, we really hope the model outputs [OK] instead of echoing it back.

Here is a third paragraph. We're testing if the streaming mode is slower than the patch mode.
I beleive this will be a good test. It has exactly one typo in this paragraph.

Let's add a list:
- Item one
- Item two
- Item three

That list should also stay together because single newlines don't force a split.
"""

original_post = requests.Session.post

def intercept_post(self, url, **kwargs):
    if "json" in kwargs:
        payload = kwargs["json"]
        messages = payload.get("messages", [])
        print("\n" + "="*40)
        print("LLM REQUEST (PATCH CHUNK)")
        print("="*40)
        for m in messages:
            if m["role"] == "system":
                print(f"[SYSTEM PROMPT SENT TO LLM]:\n{m['content']}")
            elif m["role"] == "user":
                print(f"[USER CONTENT SENT TO LLM]:\n{m['content']}")
                
    response = original_post(self, url, **kwargs)
    
    try:
        data = response.json()
        print("-" * 40)
        print("LLM RAW RESPONSE DATA:")
        import json
        print(json.dumps(data, indent=2))
        print("=" * 40 + "\n")
    except Exception as e:
        print(f"Error parsing response: {e}")
        
    return response


def main():
    cfg = ConfigManager()
    
    # Ensure there's a valid model path by picking one from recent_models
    recent = cfg.get("recent_models", [])
    if recent:
        # Just grab the first valid-looking one
        valid_model = next((m for m in recent if m.endswith(".gguf") and "E:/" in m), recent[0])
        cfg.set("model_path", valid_model)
        print(f"Using model: {valid_model}")
        
    manager = ModelManager(cfg)
    
    print("\n\n" + "#"*50)
    print("LIVE TEST: PATCH MODE")
    print("#"*50)
    
    requests.Session.post = intercept_post
    
    start = time.time()
    patch_output, chunks = manager.correct_text_patch(TEXT_SAMPLE, strength="spelling_only")
    patch_time = time.time() - start
    
    requests.Session.post = original_post
    
    print(f"\nPatch Time: {patch_time:.2f}s")
    print(f"Chunks processed: {chunks}")
    print("\n" + "="*40)
    print("FINAL ASSEMBLED OUTPUT")
    print("="*40)
    print(patch_output)

if __name__ == '__main__':
    main()
