import time
import requests
import subprocess
import os
import sys
import psutil
from pathlib import Path

# Need to find the server path
from stet.constants import LLAMA_CPP_DIR, SERVER_EXE
from stet.llm.utils import _find_shipped_llama_server

SERVER_PATH = _find_shipped_llama_server()
if not SERVER_PATH:
    SERVER_PATH = str(LLAMA_CPP_DIR / SERVER_EXE)

TEST_SENTENCES = [
    "Teh quick brown fox jumps over the lazzy dog.",
    "This is a perfectly normal sentence with no typos.",
    "Im going to the store to get sum milk and eggs.",
    "Their are to many people in this room right now.",
    "He didn't went to the party because he was feeling sick.",
    "A very long and convoluted sentence that tries to say a lot of things at once but ultimately fails because it uses too many words and has poor punctuation, grammar and structure.",
    "Its a beautiful day outside.",
    "She said she would call me back later but she never did.",
    "The data shows a clear correlation between the two variables.",
    "I need to finish this report by Friday or my boss will be mad.",
]

def kill_server():
    for proc in psutil.process_iter(['name']):
        if proc.info['name'] == SERVER_EXE:
            proc.kill()
    time.sleep(1)

def run_benchmark(model_path: str, profile_name: str, args: list):
    print(f"\n--- Testing Profile: {profile_name} ---")
    kill_server()
    
    cmd = [str(SERVER_PATH), "--model", model_path, "--port", "8080"] + args
    print("Command:", " ".join(cmd))
    
    # Boot server
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Wait for ready
    ready = False
    for _ in range(60):
        try:
            if requests.get("http://127.0.0.1:8080/health", timeout=1).status_code == 200:
                ready = True
                break
        except requests.exceptions.RequestException:
            pass
        time.sleep(1)
        
    if not ready:
        print("Server failed to boot or timed out.")
        proc.kill()
        return float('inf')

    # Run tests
    total_time = 0
    success = 0
    
    for sentence in TEST_SENTENCES:
        prompt = f"Fix my grammar: {sentence}"
        payload = {
            "prompt": prompt,
            "n_predict": 100,
            "temperature": 0.1,
            "stream": False
        }
        
        start_time = time.time()
        try:
            r = requests.post("http://127.0.0.1:8080/completion", json=payload, timeout=30)
            r.raise_for_status()
            elapsed = time.time() - start_time
            total_time += elapsed
            success += 1
            print(f"  Processed sentence in {elapsed:.2f}s")
        except Exception as e:
            print(f"  Error on sentence: {e}")
            
    proc.kill()
    
    if success == 0:
        return float('inf')
        
    avg_latency = total_time / success
    print(f"Profile '{profile_name}' Average Latency: {avg_latency:.2f}s")
    return avg_latency

def main():
    if len(sys.argv) < 2:
        print("Usage: python benchmark_optimal_settings.py <path_to_gguf_model>")
        sys.exit(1)
        
    model_path = sys.argv[1]
    if not os.path.exists(model_path):
        print(f"Model not found: {model_path}")
        sys.exit(1)
        
    import multiprocessing
    cores = multiprocessing.cpu_count()
    physical_cores = psutil.cpu_count(logical=False) or cores

    profiles = {
        "Profile 1 (Stet Defaults)": [
            "-c", "4096", "-ub", "512", "-b", "2048", "-np", "4", "--flash-attn", "off", "-t", "-1"
        ],
        "Profile 2 (VRAM Safe)": [
            "-c", "4096", "-ub", "512", "-b", "512", "-np", "4", "--flash-attn", "on", "-t", str(physical_cores)
        ],
        "Profile 3 (Pushing Compute)": [
            "-c", "4096", "-ub", "1024", "-b", "1024", "-np", "4", "--flash-attn", "on", "-t", str(physical_cores)
        ],
        "Profile 4 (KV Quantized Parallel)": [
            "-c", "4096", "-ub", "512", "-b", "1024", "-np", "4", "--flash-attn", "on", "-t", str(physical_cores),
            "--cache-type-k", "q8_0", "--cache-type-v", "q8_0"
        ]
    }

    results = {}
    for name, args in profiles.items():
        avg = run_benchmark(model_path, name, args)
        results[name] = avg

    print("\n\n=== FINAL RESULTS ===")
    for name, avg in sorted(results.items(), key=lambda item: item[1]):
        print(f"{name}: {avg:.2f}s avg latency")

if __name__ == "__main__":
    main()
