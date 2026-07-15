import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from stet.llm.model_manager import ModelManager
from stet.core.config import ConfigManager

INPUT_TEXT = (
    "Okay we need to cleanup the memory and some info. the solar panel, yeah delete that entirely, "
    "that was only to list my academic university experience which is completely separate and irrelevant to stet. "
    "stet is already listed on gumroad, are you sure the obsidian is autoupdating itself from the md files for "
    "each project? thats supposed to be the case and that shouldve been mentioned somewhere. i have set up "
    "ko-fi but could not connect paypall because its acting weird, maybe you can help me figure it, i cant "
    "use github sponsors because it needs a stripe account and i cant have that. as for the reddit marketing, "
    "you are right, i have done one on Localllm i believe but it got downvoted and people starting threatening "
    "to break my knees with a lead pipe because theyre tired of the spam. as for the product hunt, i was working "
    "on that and i made vector images to use as marketing material, you can take a look at those and recommend "
    "other stuff, maybe help me out in making a readme gif or video because that seems to be popular on product hunt. "
    "i dont know what hacker news or dev to or hjashnode are so pls educate me on those, i don't know about the gif "
    "for the readme in github, help me out with that too. as for the palystore, yeah that would be great if you "
    "could build and submit it for review."
)

def test_thresholds():
    cfg = ConfigManager()
    recent = cfg.get("recent_models", [])
    if recent:
        valid_model = next((m for m in recent if m.endswith(".gguf") and "E:/" in m), recent[0])
        cfg.set("model_path", valid_model)
        print(f"Using model: {valid_model}")

    manager = ModelManager(cfg)
    print("\n" + "="*80)
    modes = cfg.get("correction_modes", [])

    for t in [0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0]:
        if len(modes) > 0:
            # Let's find spelling_only mode or update it directly
            for mode in modes:
                if mode.get("name") == "spelling_only":
                    mode["hallucination_threshold"] = t
            cfg.set("correction_modes", modes)

        start = time.time()
        output, chunks = manager.correct_text_patch(INPUT_TEXT, strength="spelling_only")
        elapsed = time.time() - start

        print(f"Threshold: {t:.2f} | Chunks: {chunks} | Time: {elapsed:.2f}s")
        fixed_paypal = "PayPal" in output or "paypal" in output
        fixed_cant = "can't" in output or "cannot" in output
        fixed_its = "it's" in output
        fixed_playstore = "Play Store" in output or "playstore" in output or "Playstore" in output

        print(f"  - PayPal fixed: {fixed_paypal}")
        print(f"  - can't fixed: {fixed_cant}")
        print(f"  - it's fixed: {fixed_its}")
        print(f"  - Play Store fixed: {fixed_playstore}")
        if abs(t - 0.70) < 0.01:
            print(f"  - Full output text:\n{output}\n")
        else:
            print(f"  - Final output snippet:\n    {output[:100]}...\n")

if __name__ == '__main__':
    test_thresholds()
