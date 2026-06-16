"""Run a small live correction matrix across correction strengths.

The default mode attempts to use the local backend. Use --offline to print the
matrix without loading a model, which keeps the CLI safe in environments where
the llama.cpp server or model files are unavailable.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


STRENGTHS = ("conservative", "smart_fix", "aggressive")

SAMPLES = (
    # ── Preservation tests ───────────────────────────────────────────────
    {
        "id": "repeated_word_intentional",
        "input": "This is very very important.",
        "notes": "Intentional repeated word should be preserved.",
        "expected": {
            "conservative": "This is very very important.",
            "smart_fix": "This is very very important.",
            "aggressive": "This is very very important.",
        },
    },
    {
        "id": "repeated_sentence_intentional",
        "input": "Stop. Stop.",
        "notes": "Intentional repeated sentence should be preserved.",
        "expected": {
            "conservative": "Stop. Stop.",
            "smart_fix": "Stop. Stop.",
            "aggressive": "Stop. Stop.",
        },
    },
    {
        "id": "repeated_triple",
        "input": "No no no. That is wrong wrong wrong.",
        "notes": "Triple repetition — must survive all modes.",
        "expected": {
            "conservative": "No no no. That is wrong wrong wrong.",
            "smart_fix": "No no no. That is wrong wrong wrong.",
            "aggressive": "No no no. That is wrong wrong wrong.",
        },
    },
    {
        "id": "all_caps_acronyms",
        "input": "The NASA report was sent to the CEO via PDF.",
        "notes": "ALL-CAPS and acronyms must survive all modes.",
        "expected": {
            "conservative": "The NASA report was sent to the CEO via PDF.",
            "smart_fix": "The NASA report was sent to the CEO via PDF.",
            "aggressive": "The NASA report was sent to the CEO via PDF.",
        },
    },
    {
        "id": "numbers_dates_values",
        "input": "The budget is $4.2 million. Revenue grew 15% year-over-year. The deadline is 2026-06-30.",
        "notes": "Numbers, currency, percentages, dates must never change.",
        "expected": {
            "conservative": "The budget is $4.2 million. Revenue grew 15% year-over-year. The deadline is 2026-06-30.",
            "smart_fix": "The budget is $4.2 million. Revenue grew 15% year-over-year. The deadline is 2026-06-30.",
            "aggressive": "The budget is $4.2 million. Revenue grew 15% year-over-year. The deadline is 2026-06-30.",
        },
    },
    {
        "id": "urls_and_code",
        "input": "See https://example.com/api/v2 for docs. Use `pip install foo` to install.",
        "notes": "URLs and code spans must survive verbatim.",
        "expected": {
            "conservative": "See https://example.com/api/v2 for docs. Use `pip install foo` to install.",
            "smart_fix": "See https://example.com/api/v2 for docs. Use `pip install foo` to install.",
            "aggressive": "See https://example.com/api/v2 for docs. Use `pip install foo` to install.",
        },
    },
    {
        "id": "already_clean",
        "input": "The project received the update. The results are correct.",
        "notes": "Already clean text — should pass through unchanged in all modes.",
        "expected": {
            "conservative": "The project received the update. The results are correct.",
            "smart_fix": "The project received the update. The results are correct.",
            "aggressive": "The project received the update. The results are correct.",
        },
    },
    {
        "id": "title_case_preserved",
        "input": "The Quick Brown Fox Jumps Over The Lazy Dog.",
        "notes": "Intentional Title Case — should be preserved.",
        "expected": {
            "conservative": "The Quick Brown Fox Jumps Over The Lazy Dog.",
            "smart_fix": "The Quick Brown Fox Jumps Over The Lazy Dog.",
            "aggressive": "The Quick Brown Fox Jumps Over The Lazy Dog.",
        },
    },

    # ── Spelling-only typos ──────────────────────────────────────────────
    {
        "id": "simple_typos",
        "input": "Teh project recieved teh update.",
        "notes": "Basic typo correction.",
        "expected": {
            "conservative": "The project received the update.",
            "smart_fix": "The project received the update.",
            "aggressive": "The project received the update.",
        },
    },
    {
        "id": "multiple_typos_one_sentence",
        "input": "The quik brown fx jumps ovr the lzy dog.",
        "notes": "Multiple typos in one sentence.",
        "expected": {
            "conservative": "The quick brown fox jumps over the lazy dog.",
            "smart_fix": "The quick brown fox jumps over the lazy dog.",
            "aggressive": "The quick brown fox jumps over the lazy dog.",
        },
    },
    {
        "id": "double_letter_typos",
        "input": "The comittee adressed the ocurrence imediately.",
        "notes": "Double-letter errors (committee, addressed, occurrence, immediately).",
        "expected": {
            "conservative": "The committee addressed the occurrence immediately.",
            "smart_fix": "The committee addressed the occurrence immediately.",
            "aggressive": "The committee addressed the occurrence immediately.",
        },
    },
    {
        "id": "ie_ei_confusion",
        "input": "We recieve the foriegn achievment report on Wenesday.",
        "notes": "ie/ei confusion and silent-letter errors.",
        "expected": {
            "conservative": "We receive the foreign achievement report on Wednesday.",
            "smart_fix": "We receive the foreign achievement report on Wednesday.",
            "aggressive": "We receive the foreign achievement report on Wednesday.",
        },
    },
    {
        "id": "phonetic_typos",
        "input": "The definate neccessary enviroment is independant.",
        "notes": "Phonetic misspellings (definite, necessary, environment, independent).",
        "expected": {
            "conservative": "The definite necessary environment is independent.",
            "smart_fix": "The definite necessary environment is independent.",
            "aggressive": "The definite necessary environment is independent.",
        },
    },

    # ── Grammar errors (mode-dependent) ──────────────────────────────────
    {
        "id": "grammar_subject_verb",
        "input": "Him and me was late becuase the traffic.",
        "notes": "Grammar + spelling — modes 1 & 2 fix grammar, mode 0 only spelling.",
        "expected": {
            "conservative": "Him and me was late because the traffic.",
            "smart_fix": "He and I were late because of the traffic.",
            "aggressive": "He and I were late because of the traffic.",
        },
    },
    {
        "id": "grammar_dont_doesnt",
        "input": "The team dont have enough time to finish the project.",
        "notes": "Contraction error — mode 0 preserves, modes 1 & 2 fix.",
        "expected": {
            "conservative": "The team dont have enough time to finish the project.",
            "smart_fix": "The team don't have enough time to finish the project.",
            "aggressive": "The team doesn't have enough time to finish the project.",
        },
    },
    {
        "id": "grammar_its_it_s",
        "input": "The cat licked it's paw and then it's other paw.",
        "notes": "its/it's confusion — mode 0 preserves, modes 1 & 2 fix.",
        "expected": {
            "conservative": "The cat licked it's paw and then it's other paw.",
            "smart_fix": "The cat licked its paw and then its other paw.",
            "aggressive": "The cat licked its paw and then its other paw.",
        },
    },
    {
        "id": "grammar_there_their",
        "input": "Their going to the store and there picking up there stuff.",
        "notes": "there/their/they're — mode 0 preserves, modes 1 & 2 fix.",
        "expected": {
            "conservative": "Their going to the store and there picking up there stuff.",
            "smart_fix": "They're going to the store and they're picking up their stuff.",
            "aggressive": "They're going to the store and they're picking up their stuff.",
        },
    },
    {
        "id": "grammar_run_on_sentence",
        "input": "I went to the store I bought milk I came home.",
        "notes": "Run-on sentence — mode 0 preserves, mode 2 may fix punctuation.",
    },

    # ── Casual / informal text ───────────────────────────────────────────
    {
        "id": "casual_slang",
        "input": "hey bro wut up lol. gonna grab some food brb.",
        "notes": "Casual slang — mode 0 fixes only misspellings, mode 2 may polish.",
    },
    {
        "id": "casual_missing_capitalization",
        "input": "i dont know if its gonna work.",
        "notes": "Mode-specific capitalization and contraction behavior.",
        "expected": {
            "conservative": "i dont know if its gonna work.",
            "smart_fix": "I don't know if it's gonna work.",
            "aggressive": "I don't know if it's gonna work.",
        },
    },
    {
        "id": "casual_chat_style",
        "input": "omg thats so cool tbh. idk how they did it but its amazing lol.",
        "notes": "Chat-style abbreviations — should be preserved in mode 0, may be expanded in mode 2.",
    },

    # ── Multi-paragraph / structure ──────────────────────────────────────
    {
        "id": "multi_paragraph",
        "input": "First paragraph with a teh typo.\n\nSecond paragraph with teh error.\n\nThird paragraph is clean.",
        "notes": "Multi-paragraph — should preserve double-newline structure.",
        "expected": {
            "conservative": "First paragraph with a the typo.\n\nSecond paragraph with the error.\n\nThird paragraph is clean.",
            "smart_fix": "First paragraph with a the typo.\n\nSecond paragraph with the error.\n\nThird paragraph is clean.",
            "aggressive": "First paragraph with a the typo.\n\nSecond paragraph with the error.\n\nThird paragraph is clean.",
        },
    },
    {
        "id": "list_with_newlines",
        "input": "Shopping list:\n- Applse\n- Banannas\n- Ornges\n- Mangos",
        "notes": "Single-newline list — should preserve line breaks.",
        "expected": {
            "conservative": "Shopping list:\n- Apples\n- Bananas\n- Oranges\n- Mangos",
            "smart_fix": "Shopping list:\n- Apples\n- Bananas\n- Oranges\n- Mangos",
            "aggressive": "Shopping list:\n- Apples\n- Bananas\n- Oranges\n- Mangos",
        },
    },
    {
        "id": "mixed_paragraphs_and_list",
        "input": "Here are the items we need:\n\n- Applse for the pie\n- Banannas for smoothie\n- Ornges for juice\n\nPlease buy them tomorow.",
        "notes": "Mixed paragraphs and list — structure + typos.",
        "expected": {
            "conservative": "Here are the items we need:\n\n- Apples for the pie\n- Bananas for smoothie\n- Oranges for juice\n\nPlease buy them tomorrow.",
            "smart_fix": "Here are the items we need:\n\n- Apples for the pie\n- Bananas for smoothie\n- Oranges for juice\n\nPlease buy them tomorrow.",
            "aggressive": "Here are the items we need:\n\n- Apples for the pie\n- Bananas for smoothie\n- Oranges for juice\n\nPlease buy them tomorrow.",
        },
    },

    # ── Longer passages ──────────────────────────────────────────────────
    {
        "id": "medium_passage_typos",
        "input": (
            "The quik brown fx jumps ovr the lzy dog. She sells sea shells by teh sea shore. "
            "How much wood would a woodchuck chuck if a woodchuck could chuck wood? "
            "Peter Piper picked a peck of pickled peppers. A stitch in time saves nine. "
            "All that glitters is not gold. Actions speak louder then words. "
            "The early bird catches teh worm. Better late then never."
        ),
        "notes": "Medium passage with scattered typos — tests chunking.",
        "expected": {
            "conservative": (
                "The quick brown fox jumps over the lazy dog. She sells sea shells by the sea shore. "
                "How much wood would a woodchuck chuck if a woodchuck could chuck wood? "
                "Peter Piper picked a peck of pickled peppers. A stitch in time saves nine. "
                "All that glitters is not gold. Actions speak louder than words. "
                "The early bird catches the worm. Better late than never."
            ),
        },
    },
    {
        "id": "long_passage_mixed_errors",
        "input": (
            "The definate neccessary enviroment for this projcet is a quiet office with good "
            "lighting. The comittee adressed the ocurrence imediately after the meeting. "
            "We recieve the foriegn achievment report on Wenesday afternoon. "
            "Him and me was late becuase the traffic was terrible today. "
            "The team dont have enough time to finish the project by the deadlne. "
            "The budget is $4.2 million and revenue grew 15% year-over-year."
        ),
        "notes": "Long passage mixing spelling + grammar errors + values.",
    },
    {
        "id": "paragraph_per_sentence",
        "input": (
            "Teh first sentence has a typo.\n\n"
            "The secnd sentence also has one.\n\n"
            "The third sentence is perfct.\n\n"
            "The fourth sentence recieved a fix."
        ),
        "notes": "Each paragraph is one sentence with a typo — tests per-chunk processing.",
        "expected": {
            "conservative": (
                "The first sentence has a typo.\n\n"
                "The second sentence also has one.\n\n"
                "The third sentence is perfect.\n\n"
                "The fourth sentence received a fix."
            ),
        },
    },

    # ── Edge cases ───────────────────────────────────────────────────────
    {
        "id": "single_word",
        "input": "recieved",
        "notes": "Single word input — should be corrected.",
        "expected": {
            "conservative": "received",
            "smart_fix": "received",
            "aggressive": "received",
        },
    },
    {
        "id": "single_short_sentence_clean",
        "input": "Hello.",
        "notes": "Minimal clean input — should pass through unchanged.",
        "expected": {
            "conservative": "Hello.",
            "smart_fix": "Hello.",
            "aggressive": "Hello.",
        },
    },
    {
        "id": "empty_like_whitespace",
        "input": "   ",
        "notes": "Whitespace-only — should return as-is or empty.",
    },
    {
        "id": "technical_with_code",
        "input": "Run `git commit -m 'fix'` to committ the chanes. Then push to teh main branch.",
        "notes": "Code blocks must survive; surrounding typos should be fixed.",
        "expected": {
            "conservative": "Run `git commit -m 'fix'` to commit the changes. Then push to the main branch.",
            "smart_fix": "Run `git commit -m 'fix'` to commit the changes. Then push to the main branch.",
            "aggressive": "Run `git commit -m 'fix'` to commit the changes. Then push to the main branch.",
        },
    },
    {
        "id": "mixed_all_caps_typos",
        "input": "The NASA satelite recieved a signal from teh ISS satelite.",
        "notes": "ALL-CAPS acronyms + typos — acronyms survive, typos fixed.",
        "expected": {
            "conservative": "The NASA satellite received a signal from the ISS satellite.",
            "smart_fix": "The NASA satellite received a signal from the ISS satellite.",
            "aggressive": "The NASA satellite received a signal from the ISS satellite.",
        },
    },
    {
        "id": "email_like",
        "input": "Hi John,\n\nI wanted to follow up on teh proposal we discused last week. Can you send me the upadted version?\n\nThanks,\nSarah",
        "notes": "Email-like structure with greeting/sign-off — should preserve structure.",
        "expected": {
            "conservative": "Hi John,\n\nI wanted to follow up on the proposal we discussed last week. Can you send me the updated version?\n\nThanks,\nSarah",
            "smart_fix": "Hi John,\n\nI wanted to follow up on the proposal we discussed last week. Can you send me the updated version?\n\nThanks,\nSarah",
            "aggressive": "Hi John,\n\nI wanted to follow up on the proposal we discussed last week. Can you send me the updated version?\n\nThanks,\nSarah",
        },
    },
    {
        "id": "bullet_points_preserved",
        "input": "Key findings:\n* Revenue incresed by 20%\n* Custmer satisfaction is high\n* Teh new product launch was succesful",
        "notes": "Bullet points with asterisks — structure must survive.",
        "expected": {
            "conservative": "Key findings:\n* Revenue increased by 20%\n* Customer satisfaction is high\n* The new product launch was successful",
            "smart_fix": "Key findings:\n* Revenue increased by 20%\n* Customer satisfaction is high\n* The new product launch was successful",
            "aggressive": "Key findings:\n* Revenue increased by 20%\n* Customer satisfaction is high\n* The new product launch was successful",
        },
    },

    # ── Full-stop preservation ────────────────────────────────────────────
    {
        "id": "fullstop_simple",
        "input": "Teh project recieved the update",
        "notes": "Missing full stop in input — output MUST end with period.",
        "expected": {
            "conservative": "The project received the update",
            "smart_fix": "The project received the update.",
            "aggressive": "The project received the update.",
        },
    },
    {
        "id": "fullstop_multi_sentence",
        "input": "Teh first thing. Teh secnd thing. Teh thrd thing",
        "notes": "Last sentence missing full stop — output must preserve/add it.",
        "expected": {
            "conservative": "The first thing. The second thing. The third thing",
            "smart_fix": "The first thing. The second thing. The third thing.",
            "aggressive": "The first thing. The second thing. The third thing.",
        },
    },
    {
        "id": "fullstop_question",
        "input": "is teh meeting tomorow",
        "notes": "Question without question mark — mode 2 may add it.",
    },
    {
        "id": "fullstop_exclamation",
        "input": "thats amzing",
        "notes": "Exclamation without mark — mode 2 may add it.",
    },

    # ── Spelling - Transposed letters ─────────────────────────────────
    {
        "id": "transposed_teh",
        "input": "I went to teh store yesterday.",
        "notes": "Transposed letters: teh -> the.",
        "expected": {
            "conservative": "I went to the store yesterday.",
            "smart_fix": "I went to the store yesterday.",
            "aggressive": "I went to the store yesterday.",
        },
    },
    {
        "id": "transposed_hte",
        "input": "Hte cat sat on hte mat.",
        "notes": "Transposed letters: hte -> the.",
        "expected": {
            "conservative": "The cat sat on the mat.",
            "smart_fix": "The cat sat on the mat.",
            "aggressive": "The cat sat on the mat.",
        },
    },
    {
        "id": "transposed_taht",
        "input": "I think taht we should go now.",
        "notes": "Transposed letters: taht -> that.",
        "expected": {
            "conservative": "I think that we should go now.",
            "smart_fix": "I think that we should go now.",
            "aggressive": "I think that we should go now.",
        },
    },
    {
        "id": "transposed_adn",
        "input": "She bought bread adn butter.",
        "notes": "Transposed letters: adn -> and.",
        "expected": {
            "conservative": "She bought bread and butter.",
            "smart_fix": "She bought bread and butter.",
            "aggressive": "She bought bread and butter.",
        },
    },
    {
        "id": "transposed_ot",
        "input": "I want ot go to the park.",
        "notes": "Transposed letters: ot -> to.",
        "expected": {
            "conservative": "I want to go to the park.",
            "smart_fix": "I want to go to the park.",
            "aggressive": "I want to go to the park.",
        },
    },
    {
        "id": "transposed_fro",
        "input": "He came fro the office.",
        "notes": "Transposed letters: fro -> from.",
        "expected": {
            "conservative": "He came from the office.",
            "smart_fix": "He came from the office.",
            "aggressive": "He came from the office.",
        },
    },
    {
        "id": "transposed_yoru",
        "input": "Is this yoru bag or mine?",
        "notes": "Transposed letters: yoru -> your.",
        "expected": {
            "conservative": "Is this your bag or mine?",
            "smart_fix": "Is this your bag or mine?",
            "aggressive": "Is this your bag or mine?",
        },
    },
    {
        "id": "transposed_whcih",
        "input": "Whcih way should we go?",
        "notes": "Transposed letters: whcih -> which.",
        "expected": {
            "conservative": "Which way should we go?",
            "smart_fix": "Which way should we go?",
            "aggressive": "Which way should we go?",
        },
    },
    {
        "id": "transposed_mroe",
        "input": "I need mroe time to finish.",
        "notes": "Transposed letters: mroe -> more.",
        "expected": {
            "conservative": "I need more time to finish.",
            "smart_fix": "I need more time to finish.",
            "aggressive": "I need more time to finish.",
        },
    },

    # ── Spelling - Missing letters ────────────────────────────────────
    {
        "id": "missing_recieve",
        "input": "I did not recieve your email.",
        "notes": "Missing letter: recieve -> receive.",
        "expected": {
            "conservative": "I did not receive your email.",
            "smart_fix": "I did not receive your email.",
            "aggressive": "I did not receive your email.",
        },
    },
    {
        "id": "missing_occured",
        "input": "The accident occured at midnight.",
        "notes": "Missing letter: occured -> occurred.",
        "expected": {
            "conservative": "The accident occurred at midnight.",
            "smart_fix": "The accident occurred at midnight.",
            "aggressive": "The accident occurred at midnight.",
        },
    },
    {
        "id": "missing_definately",
        "input": "I definately want to go to the party.",
        "notes": "Missing letter: definately -> definitely.",
        "expected": {
            "conservative": "I definitely want to go to the party.",
            "smart_fix": "I definitely want to go to the party.",
            "aggressive": "I definitely want to go to the party.",
        },
    },
    {
        "id": "missing_seperate",
        "input": "Keep the two piles seperate.",
        "notes": "Missing letter: seperate -> separate.",
        "expected": {
            "conservative": "Keep the two piles separate.",
            "smart_fix": "Keep the two piles separate.",
            "aggressive": "Keep the two piles separate.",
        },
    },
    {
        "id": "missing_goverment",
        "input": "The goverment passed a new law.",
        "notes": "Missing letter: goverment -> government.",
        "expected": {
            "conservative": "The government passed a new law.",
            "smart_fix": "The government passed a new law.",
            "aggressive": "The government passed a new law.",
        },
    },
    {
        "id": "missing_mispell",
        "input": "Do not mispell their names.",
        "notes": "Missing letter: mispell -> misspell.",
        "expected": {
            "conservative": "Do not misspell their names.",
            "smart_fix": "Do not misspell their names.",
            "aggressive": "Do not misspell their names.",
        },
    },
    {
        "id": "missing_untill",
        "input": "Wait untill I get there.",
        "notes": "Missing letter: untill -> until.",
        "expected": {
            "conservative": "Wait until I get there.",
            "smart_fix": "Wait until I get there.",
            "aggressive": "Wait until I get there.",
        },
    },
    {
        "id": "missing_becuase",
        "input": "I left becuase I was tired.",
        "notes": "Missing letter: becuase -> because.",
        "expected": {
            "conservative": "I left because I was tired.",
            "smart_fix": "I left because I was tired.",
            "aggressive": "I left because I was tired.",
        },
    },
    {
        "id": "missing_embarass",
        "input": "I did not mean to embarass you.",
        "notes": "Missing letter: embarass -> embarrass.",
        "expected": {
            "conservative": "I did not mean to embarrass you.",
            "smart_fix": "I did not mean to embarrass you.",
            "aggressive": "I did not mean to embarrass you.",
        },
    },
    {
        "id": "missing_ocassion",
        "input": "This is a rare ocassion.",
        "notes": "Missing letter: ocassion -> occasion.",
        "expected": {
            "conservative": "This is a rare occasion.",
            "smart_fix": "This is a rare occasion.",
            "aggressive": "This is a rare occasion.",
        },
    },

    # ── Spelling - Extra letters ──────────────────────────────────────
    {
        "id": "extra_becuase",
        "input": "I was late becuase of traffic.",
        "notes": "Extra letter: becuase -> because.",
        "expected": {
            "conservative": "I was late because of traffic.",
            "smart_fix": "I was late because of traffic.",
            "aggressive": "I was late because of traffic.",
        },
    },
    {
        "id": "extra_untill",
        "input": "I will wait untill tomorrow.",
        "notes": "Extra letter: untill -> until.",
        "expected": {
            "conservative": "I will wait until tomorrow.",
            "smart_fix": "I will wait until tomorrow.",
            "aggressive": "I will wait until tomorrow.",
        },
    },
    {
        "id": "extra_alot",
        "input": "I have alot of work to do.",
        "notes": "Extra word: alot -> a lot.",
        "expected": {
            "conservative": "I have a lot of work to do.",
            "smart_fix": "I have a lot of work to do.",
            "aggressive": "I have a lot of work to do.",
        },
    },
    {
        "id": "extra_thier",
        "input": "Thier house is very big.",
        "notes": "Extra letter: thier -> their.",
        "expected": {
            "conservative": "Their house is very big.",
            "smart_fix": "Their house is very big.",
            "aggressive": "Their house is very big.",
        },
    },
    {
        "id": "extra_wich",
        "input": "I do not know wich one to pick.",
        "notes": "Extra letter: wich -> which.",
        "expected": {
            "conservative": "I do not know which one to pick.",
            "smart_fix": "I do not know which one to pick.",
            "aggressive": "I do not know which one to pick.",
        },
    },
    {
        "id": "extra_wierd",
        "input": "That was a wierd experience.",
        "notes": "Extra letter: wierd -> weird.",
        "expected": {
            "conservative": "That was a weird experience.",
            "smart_fix": "That was a weird experience.",
            "aggressive": "That was a weird experience.",
        },
    },

    # ── Spelling - Homophone confusion ────────────────────────────────
    {
        "id": "homophone_your_youre",
        "input": "Your going to love this. Your the best.",
        "notes": "Homophone: your/you're — conservative preserves, smart_fix/aggressive fixes.",
        "expected": {
            "conservative": "Your going to love this. Your the best.",
            "smart_fix": "You're going to love this. You're the best.",
            "aggressive": "You're going to love this. You're the best.",
        },
    },
    {
        "id": "homophone_then_than",
        "input": "She is taller then him. I would rather stay then go.",
        "notes": "Homophone: then/than — conservative preserves, smart_fix/aggressive fixes.",
        "expected": {
            "conservative": "She is taller then him. I would rather stay then go.",
            "smart_fix": "She is taller than him. I would rather stay than go.",
            "aggressive": "She is taller than him. I would rather stay than go.",
        },
    },
    {
        "id": "homophone_loose_lose",
        "input": "I do not want to loose this game. My shoe is lose.",
        "notes": "Homophone: loose/lose — conservative preserves, smart_fix/aggressive fixes.",
        "expected": {
            "conservative": "I do not want to loose this game. My shoe is lose.",
            "smart_fix": "I do not want to lose this game. My shoe is loose.",
            "aggressive": "I do not want to lose this game. My shoe is loose.",
        },
    },
    {
        "id": "homophone_weather_whether",
        "input": "I do not know weather to go or stay.",
        "notes": "Homophone: weather/whether — conservative preserves, smart_fix/aggressive fixes.",
        "expected": {
            "conservative": "I do not know weather to go or stay.",
            "smart_fix": "I do not know whether to go or stay.",
            "aggressive": "I do not know whether to go or stay.",
        },
    },
    {
        "id": "homophone_affect_effect",
        "input": "The weather will effect our plans. This will have a big affect on us.",
        "notes": "Homophone: affect/effect — context dependent, no expected.",
    },
    {
        "id": "homophone_compliment_complement",
        "input": "The wine complements the meal. She gave me a nice complement.",
        "notes": "Homophone: compliment/complement — may already be correct.",
    },
    {
        "id": "homophone_too_two",
        "input": "I want to go to. There are to options.",
        "notes": "Homophone: too/to/two — context dependent, no expected.",
    },
    {
        "id": "homophone_know_no",
        "input": "I do no what you mean. Say know more.",
        "notes": "Homophone: know/no — context dependent, no expected.",
    },
    {
        "id": "homophone_accept_except",
        "input": "I will accept all of them exept that one.",
        "notes": "Homophone: accept/except + typo — no expected.",
    },
    {
        "id": "homophone_principal_principle",
        "input": "The principal of the matter is clear. The school principal spoke.",
        "notes": "Homophone: principal/principle — context dependent, no expected.",
    },
    {
        "id": "homophone_stationary_stationery",
        "input": "The car was stationery. I bought some stationary supplies.",
        "notes": "Homophone: stationary/stationery — context dependent, no expected.",
    },
    {
        "id": "homophone_desert_dessert",
        "input": "I want desert after dinner. The sahara is a big desert.",
        "notes": "Homophone: desert/dessert — context dependent, no expected.",
    },

    # ── Spelling - Double-letter errors ───────────────────────────────
    {
        "id": "double_occurrence",
        "input": "The ocurrence was rare. It only hapened once.",
        "notes": "Double-letter: ocurrence -> occurrence, hapened -> happened.",
        "expected": {
            "conservative": "The occurrence was rare. It only happened once.",
            "smart_fix": "The occurrence was rare. It only happened once.",
            "aggressive": "The occurrence was rare. It only happened once.",
        },
    },
    {
        "id": "double_necessary",
        "input": "It is neccessary to bring the neccesary documents.",
        "notes": "Double-letter: neccessary -> necessary, neccesary -> necessary.",
        "expected": {
            "conservative": "It is necessary to bring the necessary documents.",
            "smart_fix": "It is necessary to bring the necessary documents.",
            "aggressive": "It is necessary to bring the necessary documents.",
        },
    },
    {
        "id": "double_accommodate",
        "input": "We can accomodate up to ten guests.",
        "notes": "Double-letter: accomodate -> accommodate.",
        "expected": {
            "conservative": "We can accommodate up to ten guests.",
            "smart_fix": "We can accommodate up to ten guests.",
            "aggressive": "We can accommodate up to ten guests.",
        },
    },
    {
        "id": "double_embarrass",
        "input": "I do not want to embrase anyone.",
        "notes": "Double-letter: embrase -> embarrass.",
        "expected": {
            "conservative": "I do not want to embarrass anyone.",
            "smart_fix": "I do not want to embarrass anyone.",
            "aggressive": "I do not want to embarrass anyone.",
        },
    },
    {
        "id": "double_millennium",
        "input": "The milennium celebration was huge.",
        "notes": "Double-letter: milennium -> millennium.",
        "expected": {
            "conservative": "The millennium celebration was huge.",
            "smart_fix": "The millennium celebration was huge.",
            "aggressive": "The millennium celebration was huge.",
        },
    },
    {
        "id": "double_recommend",
        "input": "I reccommend this book to everyone.",
        "notes": "Double-letter: reccommend -> recommend.",
        "expected": {
            "conservative": "I recommend this book to everyone.",
            "smart_fix": "I recommend this book to everyone.",
            "aggressive": "I recommend this book to everyone.",
        },
    },
    {
        "id": "double_apparently",
        "input": "Aparently the meeting was cancelled.",
        "notes": "Double-letter: aparently -> apparently.",
        "expected": {
            "conservative": "Apparently the meeting was cancelled.",
            "smart_fix": "Apparently the meeting was cancelled.",
            "aggressive": "Apparently the meeting was cancelled.",
        },
    },
    {
        "id": "double_disappoint",
        "input": "I am dissapointed with the results.",
        "notes": "Double-letter: dissapointed -> disappointed.",
        "expected": {
            "conservative": "I am disappointed with the results.",
            "smart_fix": "I am disappointed with the results.",
            "aggressive": "I am disappointed with the results.",
        },
    },
    {
        "id": "double_rhythm",
        "input": "The rythm of the music was catchy.",
        "notes": "Double-letter: rythm -> rhythm.",
        "expected": {
            "conservative": "The rhythm of the music was catchy.",
            "smart_fix": "The rhythm of the music was catchy.",
            "aggressive": "The rhythm of the music was catchy.",
        },
    },
    {
        "id": "double_succesful",
        "input": "The project was very succesful.",
        "notes": "Double-letter: succesful -> successful.",
        "expected": {
            "conservative": "The project was very successful.",
            "smart_fix": "The project was very successful.",
            "aggressive": "The project was very successful.",
        },
    },

    # ── Spelling - ie/ei confusion ────────────────────────────────────
    {
        "id": "ieei_receive",
        "input": "Please recieve the package at the door.",
        "notes": "ie/ei: recieve -> receive.",
        "expected": {
            "conservative": "Please receive the package at the door.",
            "smart_fix": "Please receive the package at the door.",
            "aggressive": "Please receive the package at the door.",
        },
    },
    {
        "id": "ieei_foreign",
        "input": "She is a foriegn exchange student.",
        "notes": "ie/ei: foriegn -> foreign.",
        "expected": {
            "conservative": "She is a foreign exchange student.",
            "smart_fix": "She is a foreign exchange student.",
            "aggressive": "She is a foreign exchange student.",
        },
    },
    {
        "id": "ieei_achieve",
        "input": "She will achive great things.",
        "notes": "ie/ei: achive -> achieve.",
        "expected": {
            "conservative": "She will achieve great things.",
            "smart_fix": "She will achieve great things.",
            "aggressive": "She will achieve great things.",
        },
    },
    {
        "id": "ieei_weird",
        "input": "That was a wierd dream.",
        "notes": "ie/ei: wierd -> weird.",
        "expected": {
            "conservative": "That was a weird dream.",
            "smart_fix": "That was a weird dream.",
            "aggressive": "That was a weird dream.",
        },
    },
    {
        "id": "ieei_seize",
        "input": "The police will sieze the evidence.",
        "notes": "ie/ei: sieze -> seize.",
        "expected": {
            "conservative": "The police will seize the evidence.",
            "smart_fix": "The police will seize the evidence.",
            "aggressive": "The police will seize the evidence.",
        },
    },
    {
        "id": "ieei_height",
        "input": "The hieght of the building is impressive.",
        "notes": "ie/ei: hieght -> height.",
        "expected": {
            "conservative": "The height of the building is impressive.",
            "smart_fix": "The height of the building is impressive.",
            "aggressive": "The height of the building is impressive.",
        },
    },
    {
        "id": "ieei_caffeine",
        "input": "There is too much caffiene in this drink.",
        "notes": "ie/ei: caffiene -> caffeine.",
        "expected": {
            "conservative": "There is too much caffeine in this drink.",
            "smart_fix": "There is too much caffeine in this drink.",
            "aggressive": "There is too much caffeine in this drink.",
        },
    },

    # ── Spelling - Silent letters ─────────────────────────────────────
    {
        "id": "silent_knowledge",
        "input": "He has a good knowlege of history.",
        "notes": "Silent letter: knowlege -> knowledge.",
        "expected": {
            "conservative": "He has a good knowledge of history.",
            "smart_fix": "He has a good knowledge of history.",
            "aggressive": "He has a good knowledge of history.",
        },
    },
    {
        "id": "silent_psychology",
        "input": "She studied psycology in college.",
        "notes": "Silent letter: psycology -> psychology.",
        "expected": {
            "conservative": "She studied psychology in college.",
            "smart_fix": "She studied psychology in college.",
            "aggressive": "She studied psychology in college.",
        },
    },
    {
        "id": "silent_pneumonia",
        "input": "He was diagnosed with numonia.",
        "notes": "Silent letter: numonia -> pneumonia.",
        "expected": {
            "conservative": "He was diagnosed with pneumonia.",
            "smart_fix": "He was diagnosed with pneumonia.",
            "aggressive": "He was diagnosed with pneumonia.",
        },
    },
    {
        "id": "silent_receipt",
        "input": "Please keep the receit for your records.",
        "notes": "Silent letter: receit -> receipt.",
        "expected": {
            "conservative": "Please keep the receipt for your records.",
            "smart_fix": "Please keep the receipt for your records.",
            "aggressive": "Please keep the receipt for your records.",
        },
    },
    {
        "id": "silent_doubt",
        "input": "I have no dout about it.",
        "notes": "Silent letter: dout -> doubt.",
        "expected": {
            "conservative": "I have no doubt about it.",
            "smart_fix": "I have no doubt about it.",
            "aggressive": "I have no doubt about it.",
        },
    },
    {
        "id": "silent_column",
        "input": "The last colum needs to be filled.",
        "notes": "Silent letter: colum -> column.",
        "expected": {
            "conservative": "The last column needs to be filled.",
            "smart_fix": "The last column needs to be filled.",
            "aggressive": "The last column needs to be filled.",
        },
    },

    # ── Spelling - Autocorrect-style errors ───────────────────────────
    {
        "id": "autocorrect_and_adn",
        "input": "Bread adn milk are on the list.",
        "notes": "Autocorrect-style: adn -> and.",
        "expected": {
            "conservative": "Bread and milk are on the list.",
            "smart_fix": "Bread and milk are on the list.",
            "aggressive": "Bread and milk are on the list.",
        },
    },
    {
        "id": "autocorrect_thsi",
        "input": "Thsi is the right answer.",
        "notes": "Autocorrect-style: thsi -> this.",
        "expected": {
            "conservative": "This is the right answer.",
            "smart_fix": "This is the right answer.",
            "aggressive": "This is the right answer.",
        },
    },
    {
        "id": "autocorrect_tbat",
        "input": "I said tbat already.",
        "notes": "Autocorrect-style: tbat -> that.",
        "expected": {
            "conservative": "I said that already.",
            "smart_fix": "I said that already.",
            "aggressive": "I said that already.",
        },
    },
    {
        "id": "autocorrect_whag",
        "input": "Whag are you doing?",
        "notes": "Autocorrect-style: whag -> what.",
        "expected": {
            "conservative": "What are you doing?",
            "smart_fix": "What are you doing?",
            "aggressive": "What are you doing?",
        },
    },
    {
        "id": "autocorrect_wnat",
        "input": "I do not wnat to go.",
        "notes": "Autocorrect-style: wnat -> want.",
        "expected": {
            "conservative": "I do not want to go.",
            "smart_fix": "I do not want to go.",
            "aggressive": "I do not want to go.",
        },
    },
    {
        "id": "autocorrect_becasue",
        "input": "I stayed home becasue of rain.",
        "notes": "Autocorrect-style: becasue -> because.",
        "expected": {
            "conservative": "I stayed home because of rain.",
            "smart_fix": "I stayed home because of rain.",
            "aggressive": "I stayed home because of rain.",
        },
    },

    # ── Spelling - British vs American (preserve) ─────────────────────
    {
        "id": "british_colour",
        "input": "The colour of the wall is blue.",
        "notes": "British spelling: colour — preserved in all modes.",
        "expected": {
            "conservative": "The colour of the wall is blue.",
            "smart_fix": "The colour of the wall is blue.",
            "aggressive": "The colour of the wall is blue.",
        },
    },
    {
        "id": "british_favour",
        "input": "Can you do me a favour?",
        "notes": "British spelling: favour — preserved in all modes.",
        "expected": {
            "conservative": "Can you do me a favour?",
            "smart_fix": "Can you do me a favour?",
            "aggressive": "Can you do me a favour?",
        },
    },
    {
        "id": "british_organise",
        "input": "We need to organise the files.",
        "notes": "British spelling: organise — preserved in all modes.",
        "expected": {
            "conservative": "We need to organise the files.",
            "smart_fix": "We need to organise the files.",
            "aggressive": "We need to organise the files.",
        },
    },
    {
        "id": "british_centre",
        "input": "The town centre is nearby.",
        "notes": "British spelling: centre — preserved in all modes.",
        "expected": {
            "conservative": "The town centre is nearby.",
            "smart_fix": "The town centre is nearby.",
            "aggressive": "The town centre is nearby.",
        },
    },
    {
        "id": "british_defence",
        "input": "The defence team argued well.",
        "notes": "British spelling: defence — preserved in all modes.",
        "expected": {
            "conservative": "The defence team argued well.",
            "smart_fix": "The defence team argued well.",
            "aggressive": "The defence team argued well.",
        },
    },
    {
        "id": "british_analyse",
        "input": "Please analyse the data carefully.",
        "notes": "British spelling: analyse — preserved in all modes.",
        "expected": {
            "conservative": "Please analyse the data carefully.",
            "smart_fix": "Please analyse the data carefully.",
            "aggressive": "Please analyse the data carefully.",
        },
    },
    {
        "id": "british_realise",
        "input": "I did not realise the time.",
        "notes": "British spelling: realise — preserved in all modes.",
        "expected": {
            "conservative": "I did not realise the time.",
            "smart_fix": "I did not realise the time.",
            "aggressive": "I did not realise the time.",
        },
    },
    {
        "id": "british_programme",
        "input": "The television programme starts at eight.",
        "notes": "British spelling: programme — preserved in all modes.",
        "expected": {
            "conservative": "The television programme starts at eight.",
            "smart_fix": "The television programme starts at eight.",
            "aggressive": "The television programme starts at eight.",
        },
    },

    # ── Grammar - Subject-verb agreement ──────────────────────────────
    {
        "id": "grammar_he_dont",
        "input": "He don't know the answer.",
        "notes": "Subject-verb: don't -> doesn't — conservative preserves.",
        "expected": {
            "conservative": "He don't know the answer.",
            "smart_fix": "He doesn't know the answer.",
            "aggressive": "He doesn't know the answer.",
        },
    },
    {
        "id": "grammar_they_was",
        "input": "They was at the store when I called.",
        "notes": "Subject-verb: was -> were — conservative preserves.",
        "expected": {
            "conservative": "They was at the store when I called.",
            "smart_fix": "They were at the store when I called.",
            "aggressive": "They were at the store when I called.",
        },
    },
    {
        "id": "grammar_each_of_them_are",
        "input": "Each of them are going to the party.",
        "notes": "Subject-verb: are -> is — conservative preserves.",
        "expected": {
            "conservative": "Each of them are going to the party.",
            "smart_fix": "Each of them is going to the party.",
            "aggressive": "Each of them is going to the party.",
        },
    },
    {
        "id": "grammar_neither_nor",
        "input": "Neither the teacher nor the students was ready.",
        "notes": "Subject-verb: was -> were — conservative preserves.",
        "expected": {
            "conservative": "Neither the teacher nor the students was ready.",
            "smart_fix": "Neither the teacher nor the students were ready.",
            "aggressive": "Neither the teacher nor the students were ready.",
        },
    },
    {
        "id": "grammar_everyone_are",
        "input": "Everyone are invited to the meeting.",
        "notes": "Subject-verb: are -> is — conservative preserves.",
        "expected": {
            "conservative": "Everyone are invited to the meeting.",
            "smart_fix": "Everyone is invited to the meeting.",
            "aggressive": "Everyone is invited to the meeting.",
        },
    },
    {
        "id": "grammar_doesnt_have",
        "input": "The dogs doesnt have any food.",
        "notes": "Subject-verb: doesnt -> don't — conservative preserves.",
        "expected": {
            "conservative": "The dogs doesnt have any food.",
            "smart_fix": "The dogs don't have any food.",
            "aggressive": "The dogs don't have any food.",
        },
    },

    # ── Grammar - Wrong tense ─────────────────────────────────────────
    {
        "id": "grammar_I_seen",
        "input": "I seen the movie last night.",
        "notes": "Wrong tense: seen -> saw — conservative preserves.",
        "expected": {
            "conservative": "I seen the movie last night.",
            "smart_fix": "I saw the movie last night.",
            "aggressive": "I saw the movie last night.",
        },
    },
    {
        "id": "grammar_he_runned",
        "input": "He runned to the store as fast as he could.",
        "notes": "Wrong tense: runned -> ran — conservative preserves.",
        "expected": {
            "conservative": "He runned to the store as fast as he could.",
            "smart_fix": "He ran to the store as fast as he could.",
            "aggressive": "He ran to the store as fast as he could.",
        },
    },
    {
        "id": "grammar_I_gone",
        "input": "I gone to the market this morning.",
        "notes": "Wrong tense: gone -> went — conservative preserves.",
        "expected": {
            "conservative": "I gone to the market this morning.",
            "smart_fix": "I went to the market this morning.",
            "aggressive": "I went to the market this morning.",
        },
    },
    {
        "id": "grammar_she_brung",
        "input": "She brung her friend to the party.",
        "notes": "Wrong tense: brung -> brought — conservative preserves.",
        "expected": {
            "conservative": "She brung her friend to the party.",
            "smart_fix": "She brought her friend to the party.",
            "aggressive": "She brought her friend to the party.",
        },
    },
    {
        "id": "grammar_have_wrote",
        "input": "I have wrote three emails today.",
        "notes": "Wrong tense: wrote -> written — conservative preserves.",
        "expected": {
            "conservative": "I have wrote three emails today.",
            "smart_fix": "I have written three emails today.",
            "aggressive": "I have written three emails today.",
        },
    },
    {
        "id": "grammar_did_went",
        "input": "Did you went to the store?",
        "notes": "Wrong tense: went -> go — conservative preserves.",
        "expected": {
            "conservative": "Did you went to the store?",
            "smart_fix": "Did you go to the store?",
            "aggressive": "Did you go to the store?",
        },
    },

    # ── Grammar - Missing articles ────────────────────────────────────
    {
        "id": "grammar_missing_article_store",
        "input": "I went to store to buy milk.",
        "notes": "Missing article: to store -> to the store — conservative preserves.",
        "expected": {
            "conservative": "I went to store to buy milk.",
            "smart_fix": "I went to the store to buy milk.",
            "aggressive": "I went to the store to buy milk.",
        },
    },
    {
        "id": "grammar_missing_article_university",
        "input": "She is student at university.",
        "notes": "Missing articles: is student -> is a student, at university -> at a university.",
        "expected": {
            "conservative": "She is student at university.",
            "smart_fix": "She is a student at a university.",
            "aggressive": "She is a student at a university.",
        },
    },
    {
        "id": "grammar_missing_article_dog",
        "input": "I saw dog in the park. Dog was very friendly.",
        "notes": "Missing articles: saw dog -> saw a dog, Dog -> The dog.",
        "expected": {
            "conservative": "I saw dog in the park. Dog was very friendly.",
            "smart_fix": "I saw a dog in the park. The dog was very friendly.",
            "aggressive": "I saw a dog in the park. The dog was very friendly.",
        },
    },

    # ── Grammar - Double negatives ────────────────────────────────────
    {
        "id": "grammar_double_negative_nothing",
        "input": "I don't have nothing to say.",
        "notes": "Double negative: nothing -> anything — conservative preserves.",
        "expected": {
            "conservative": "I don't have nothing to say.",
            "smart_fix": "I don't have anything to say.",
            "aggressive": "I don't have anything to say.",
        },
    },
    {
        "id": "grammar_double_negative_nowhere",
        "input": "We can't go nowhere without a car.",
        "notes": "Double negative: nowhere -> anywhere — conservative preserves.",
        "expected": {
            "conservative": "We can't go nowhere without a car.",
            "smart_fix": "We can't go anywhere without a car.",
            "aggressive": "We can't go anywhere without a car.",
        },
    },
    {
        "id": "grammar_double_negative_never",
        "input": "I don't never want to see that again.",
        "notes": "Double negative: don't never -> never — conservative preserves.",
        "expected": {
            "conservative": "I don't never want to see that again.",
            "smart_fix": "I never want to see that again.",
            "aggressive": "I never want to see that again.",
        },
    },
    {
        "id": "grammar_double_negative_nobody",
        "input": "I can't find nobody to help me.",
        "notes": "Double negative: nobody -> anybody — conservative preserves.",
        "expected": {
            "conservative": "I can't find nobody to help me.",
            "smart_fix": "I can't find anybody to help me.",
            "aggressive": "I can't find anybody to help me.",
        },
    },

    # ── Grammar - Dangling modifiers ──────────────────────────────────
    {
        "id": "grammar_dangling_walking",
        "input": "Walking down the street, the trees were beautiful.",
        "notes": "Dangling modifier — complex fix, no expected.",
    },
    {
        "id": "grammar_dangling_running",
        "input": "Running to catch the bus, my phone fell out of my pocket.",
        "notes": "Dangling modifier — complex fix, no expected.",
    },
    {
        "id": "grammar_dangling_cooking",
        "input": "After cooking dinner, the kitchen was a mess.",
        "notes": "Dangling modifier — complex fix, no expected.",
    },

    # ── Grammar - Parallelism errors ──────────────────────────────────
    {
        "id": "grammar_parallelism_mixed",
        "input": "She likes swimming, to run, and cycling.",
        "notes": "Parallelism: to run -> running — conservative preserves.",
        "expected": {
            "conservative": "She likes swimming, to run, and cycling.",
            "smart_fix": "She likes swimming, running, and cycling.",
            "aggressive": "She likes swimming, running, and cycling.",
        },
    },
    {
        "id": "grammar_parallelism_lists",
        "input": "The goals are to increase revenue, improving customer satisfaction, and we need to reduce costs.",
        "notes": "Parallelism: complex list — no expected.",
    },
    {
        "id": "grammar_parallelism_to_and_ing",
        "input": "I want to go shopping, eat out, and to see a movie.",
        "notes": "Parallelism: to see -> see — conservative preserves.",
        "expected": {
            "conservative": "I want to go shopping, eat out, and to see a movie.",
            "smart_fix": "I want to go shopping, eat out, and see a movie.",
            "aggressive": "I want to go shopping, eat out, and see a movie.",
        },
    },

    # ── Grammar - Pronoun case errors ─────────────────────────────────
    {
        "id": "grammar_pronoun_between_you_and_I",
        "input": "This is just between you and I.",
        "notes": "Pronoun case: I -> me — conservative preserves.",
        "expected": {
            "conservative": "This is just between you and I.",
            "smart_fix": "This is just between you and me.",
            "aggressive": "This is just between you and me.",
        },
    },
    {
        "id": "grammar_pronoun_him_and_me",
        "input": "Him and me went to the store.",
        "notes": "Pronoun case: Him and me -> He and I — conservative preserves.",
        "expected": {
            "conservative": "Him and me went to the store.",
            "smart_fix": "He and I went to the store.",
            "aggressive": "He and I went to the store.",
        },
    },
    {
        "id": "grammar_pronoun_me_and_her",
        "input": "Me and her are going to the concert.",
        "notes": "Pronoun case: Me and her -> She and I — conservative preserves.",
        "expected": {
            "conservative": "Me and her are going to the concert.",
            "smart_fix": "She and I are going to the concert.",
            "aggressive": "She and I are going to the concert.",
        },
    },
    {
        "id": "grammar_pronoun_us_students",
        "input": "Us students need more time.",
        "notes": "Pronoun case: Us -> We — conservative preserves.",
        "expected": {
            "conservative": "Us students need more time.",
            "smart_fix": "We students need more time.",
            "aggressive": "We students need more time.",
        },
    },
    {
        "id": "grammar_pronoun_whom_is",
        "input": "Whom is coming to dinner?",
        "notes": "Pronoun case: Whom -> Who — conservative preserves.",
        "expected": {
            "conservative": "Whom is coming to dinner?",
            "smart_fix": "Who is coming to dinner?",
            "aggressive": "Who is coming to dinner?",
        },
    },

    # ── Grammar - Who/whom ────────────────────────────────────────────
    {
        "id": "grammar_who_whom_subject",
        "input": "Whom wrote this letter?",
        "notes": "Who/whom: Whom -> Who (subject) — conservative preserves.",
        "expected": {
            "conservative": "Whom wrote this letter?",
            "smart_fix": "Who wrote this letter?",
            "aggressive": "Who wrote this letter?",
        },
    },
    {
        "id": "grammar_who_whom_clause",
        "input": "The person whom called you is my friend.",
        "notes": "Who/whom: whom -> who (subject of clause) — conservative preserves.",
        "expected": {
            "conservative": "The person whom called you is my friend.",
            "smart_fix": "The person who called you is my friend.",
            "aggressive": "The person who called you is my friend.",
        },
    },

    # ── Grammar - Lay/lie ─────────────────────────────────────────────
    {
        "id": "grammar_lay_lie_present",
        "input": "I want to lay down for a nap.",
        "notes": "Lay/lie: lay -> lie (intransitive) — conservative preserves.",
        "expected": {
            "conservative": "I want to lay down for a nap.",
            "smart_fix": "I want to lie down for a nap.",
            "aggressive": "I want to lie down for a nap.",
        },
    },
    {
        "id": "grammar_lay_lie_past",
        "input": "I laid on the couch all afternoon.",
        "notes": "Lay/lie: laid -> lay (past tense of lie) — conservative preserves.",
        "expected": {
            "conservative": "I laid on the couch all afternoon.",
            "smart_fix": "I lay on the couch all afternoon.",
            "aggressive": "I lay on the couch all afternoon.",
        },
    },
    {
        "id": "grammar_lay_lie_transitive",
        "input": "Please lay the book on the table.",
        "notes": "Lay/lie: lay is correct here (transitive) — preserved in all.",
        "expected": {
            "conservative": "Please lay the book on the table.",
            "smart_fix": "Please lay the book on the table.",
            "aggressive": "Please lay the book on the table.",
        },
    },

    # ── Grammar - Fewer/less ──────────────────────────────────────────
    {
        "id": "grammar_fewer_less_countable",
        "input": "There are less people here today.",
        "notes": "Fewer/less: less -> fewer (countable) — conservative preserves.",
        "expected": {
            "conservative": "There are less people here today.",
            "smart_fix": "There are fewer people here today.",
            "aggressive": "There are fewer people here today.",
        },
    },
    {
        "id": "grammar_fewer_less_uncountable",
        "input": "There is less water in the bottle.",
        "notes": "Fewer/less: less is correct (uncountable) — preserved in all.",
        "expected": {
            "conservative": "There is less water in the bottle.",
            "smart_fix": "There is less water in the bottle.",
            "aggressive": "There is less water in the bottle.",
        },
    },
    {
        "id": "grammar_fewer_less_items",
        "input": "I have less items than you.",
        "notes": "Fewer/less: less -> fewer (countable) — conservative preserves.",
        "expected": {
            "conservative": "I have less items than you.",
            "smart_fix": "I have fewer items than you.",
            "aggressive": "I have fewer items than you.",
        },
    },

    # ── Punctuation - Missing commas ──────────────────────────────────
    {
        "id": "punct_comma_however",
        "input": "However I think we should wait.",
        "notes": "Missing comma after however — conservative preserves.",
        "expected": {
            "conservative": "However I think we should wait.",
            "smart_fix": "However, I think we should wait.",
            "aggressive": "However, I think we should wait.",
        },
    },
    {
        "id": "punct_comma_intro_clause",
        "input": "After the meeting we went to lunch.",
        "notes": "Missing comma after intro clause — conservative preserves.",
        "expected": {
            "conservative": "After the meeting we went to lunch.",
            "smart_fix": "After the meeting, we went to lunch.",
            "aggressive": "After the meeting, we went to lunch.",
        },
    },
    {
        "id": "punct_comma_verb_list",
        "input": "I bought apples oranges and bananas.",
        "notes": "Missing commas in list — conservative preserves.",
        "expected": {
            "conservative": "I bought apples oranges and bananas.",
            "smart_fix": "I bought apples, oranges, and bananas.",
            "aggressive": "I bought apples, oranges, and bananas.",
        },
    },
    {
        "id": "punct_comma_direct_address",
        "input": "Let's eat grandma.",
        "notes": "Missing comma in direct address — conservative preserves.",
        "expected": {
            "conservative": "Let's eat grandma.",
            "smart_fix": "Let's eat, grandma.",
            "aggressive": "Let's eat, grandma.",
        },
    },
    {
        "id": "punct_comma_but",
        "input": "I wanted to go but I was too tired.",
        "notes": "Missing comma before but — conservative preserves.",
        "expected": {
            "conservative": "I wanted to go but I was too tired.",
            "smart_fix": "I wanted to go, but I was too tired.",
            "aggressive": "I wanted to go, but I was too tired.",
        },
    },
    {
        "id": "punct_comma_date",
        "input": "On June 15 2026 we will launch.",
        "notes": "Missing commas around date — conservative preserves.",
        "expected": {
            "conservative": "On June 15 2026 we will launch.",
            "smart_fix": "On June 15, 2026, we will launch.",
            "aggressive": "On June 15, 2026, we will launch.",
        },
    },
    {
        "id": "punct_comma_city_state",
        "input": "I live in Portland Oregon and work in Seattle Washington.",
        "notes": "Missing commas in city/state — conservative preserves.",
        "expected": {
            "conservative": "I live in Portland Oregon and work in Seattle Washington.",
            "smart_fix": "I live in Portland, Oregon, and work in Seattle, Washington.",
            "aggressive": "I live in Portland, Oregon, and work in Seattle, Washington.",
        },
    },

    # ── Punctuation - Missing periods ─────────────────────────────────
    {
        "id": "punct_period_missing_end",
        "input": "The meeting is at three",
        "notes": "Missing period at end — conservative preserves, smart_fix/aggressive add.",
        "expected": {
            "conservative": "The meeting is at three",
            "smart_fix": "The meeting is at three.",
            "aggressive": "The meeting is at three.",
        },
    },
    {
        "id": "punct_period_multiple_sentences",
        "input": "I went to the store. Then I came home. After that I ate dinner",
        "notes": "Missing period at end of last sentence.",
        "expected": {
            "conservative": "I went to the store. Then I came home. After that I ate dinner",
            "smart_fix": "I went to the store. Then I came home. After that I ate dinner.",
            "aggressive": "I went to the store. Then I came home. After that I ate dinner.",
        },
    },

    # ── Punctuation - Extra commas ────────────────────────────────────
    {
        "id": "punct_extra_comma",
        "input": "I went to, the store to buy milk.",
        "notes": "Extra comma after 'to' — conservative preserves.",
        "expected": {
            "conservative": "I went to, the store to buy milk.",
            "smart_fix": "I went to the store to buy milk.",
            "aggressive": "I went to the store to buy milk.",
        },
    },

    # ── Punctuation - Missing apostrophes ─────────────────────────────
    {
        "id": "punct_apostrophe_dont",
        "input": "I dont think thats right.",
        "notes": "Missing apostrophes: dont -> don't, thats -> that's.",
        "expected": {
            "conservative": "I dont think thats right.",
            "smart_fix": "I don't think that's right.",
            "aggressive": "I don't think that's right.",
        },
    },
    {
        "id": "punct_apostrophe_cant",
        "input": "I cant believe its already June.",
        "notes": "Missing apostrophes: cant -> can't, its -> it's.",
        "expected": {
            "conservative": "I cant believe its already June.",
            "smart_fix": "I can't believe it's already June.",
            "aggressive": "I can't believe it's already June.",
        },
    },
    {
        "id": "punct_apostrophe_wont",
        "input": "It wont take long, I promise.",
        "notes": "Missing apostrophe: wont -> won't.",
        "expected": {
            "conservative": "It wont take long, I promise.",
            "smart_fix": "It won't take long, I promise.",
            "aggressive": "It won't take long, I promise.",
        },
    },
    {
        "id": "punct_apostrophe_didnt",
        "input": "She didnt know about the meeting.",
        "notes": "Missing apostrophe: didnt -> didn't.",
        "expected": {
            "conservative": "She didnt know about the meeting.",
            "smart_fix": "She didn't know about the meeting.",
            "aggressive": "She didn't know about the meeting.",
        },
    },
    {
        "id": "punct_apostrophe_shouldnt",
        "input": "You shouldnt do that.",
        "notes": "Missing apostrophe: shouldnt -> shouldn't.",
        "expected": {
            "conservative": "You shouldnt do that.",
            "smart_fix": "You shouldn't do that.",
            "aggressive": "You shouldn't do that.",
        },
    },
    {
        "id": "punct_apostrophe_isnt",
        "input": "That isnt what I meant.",
        "notes": "Missing apostrophe: isnt -> isn't.",
        "expected": {
            "conservative": "That isnt what I meant.",
            "smart_fix": "That isn't what I meant.",
            "aggressive": "That isn't what I meant.",
        },
    },
    {
        "id": "punct_apostrophe_wouldnt",
        "input": "I wouldnt go there if I were you.",
        "notes": "Missing apostrophe: wouldnt -> wouldn't.",
        "expected": {
            "conservative": "I wouldnt go there if I were you.",
            "smart_fix": "I wouldn't go there if I were you.",
            "aggressive": "I wouldn't go there if I were you.",
        },
    },
    {
        "id": "punct_apostrophe_arent",
        "input": "They arent coming to the party.",
        "notes": "Missing apostrophe: arent -> aren't.",
        "expected": {
            "conservative": "They arent coming to the party.",
            "smart_fix": "They aren't coming to the party.",
            "aggressive": "They aren't coming to the party.",
        },
    },
    {
        "id": "punct_apostrophe_havent",
        "input": "I havent finished yet.",
        "notes": "Missing apostrophe: havent -> haven't.",
        "expected": {
            "conservative": "I havent finished yet.",
            "smart_fix": "I haven't finished yet.",
            "aggressive": "I haven't finished yet.",
        },
    },
    {
        "id": "punct_apostrophe_wasnt",
        "input": "It wasnt my fault.",
        "notes": "Missing apostrophe: wasnt -> wasn't.",
        "expected": {
            "conservative": "It wasnt my fault.",
            "smart_fix": "It wasn't my fault.",
            "aggressive": "It wasn't my fault.",
        },
    },

    # ── Punctuation - Semicolon/colon ─────────────────────────────────
    {
        "id": "punct_semicolon_correct",
        "input": "I went to the store; milk was what I needed.",
        "notes": "Correct semicolon usage — preserved in all modes.",
        "expected": {
            "conservative": "I went to the store; milk was what I needed.",
            "smart_fix": "I went to the store; milk was what I needed.",
            "aggressive": "I went to the store; milk was what I needed.",
        },
    },
    {
        "id": "punct_colon_correct",
        "input": "I have one goal: to win.",
        "notes": "Correct colon usage — preserved in all modes.",
        "expected": {
            "conservative": "I have one goal: to win.",
            "smart_fix": "I have one goal: to win.",
            "aggressive": "I have one goal: to win.",
        },
    },
    {
        "id": "punct_colon_such_as",
        "input": "I like fruits such as: apples, oranges, and bananas.",
        "notes": "Colon after such as — may be flagged, no expected.",
    },
    {
        "id": "punct_colon_including",
        "input": "The team includes: John, Sarah, and Mike.",
        "notes": "Colon after includes — may be flagged, no expected.",
    },

    # ── Punctuation - which/that ──────────────────────────────────────
    {
        "id": "punct_which_correct",
        "input": "The car that is parked outside is mine.",
        "notes": "Correct that usage — preserved in all modes.",
        "expected": {
            "conservative": "The car that is parked outside is mine.",
            "smart_fix": "The car that is parked outside is mine.",
            "aggressive": "The car that is parked outside is mine.",
        },
    },

    # ── Structure - URLs ──────────────────────────────────────────────
    {
        "id": "struct_url_complex",
        "input": "Visit https://example.com/path?q=1&b=2 for more info.",
        "notes": "Complex URL with query params — preserved in all modes.",
        "expected": {
            "conservative": "Visit https://example.com/path?q=1&b=2 for more info.",
            "smart_fix": "Visit https://example.com/path?q=1&b=2 for more info.",
            "aggressive": "Visit https://example.com/path?q=1&b=2 for more info.",
        },
    },
    {
        "id": "struct_url_with_typo_around",
        "input": "Go to https://example.com/page for teh details.",
        "notes": "URL preserved, surrounding typo fixed.",
        "expected": {
            "conservative": "Go to https://example.com/page for the details.",
            "smart_fix": "Go to https://example.com/page for the details.",
            "aggressive": "Go to https://example.com/page for the details.",
        },
    },
    {
        "id": "struct_url_http",
        "input": "The old site was at http://legacy.example.com/old-path.",
        "notes": "HTTP URL — preserved in all modes.",
        "expected": {
            "conservative": "The old site was at http://legacy.example.com/old-path.",
            "smart_fix": "The old site was at http://legacy.example.com/old-path.",
            "aggressive": "The old site was at http://legacy.example.com/old-path.",
        },
    },
    {
        "id": "struct_url_with_fragment",
        "input": "See https://docs.example.com/api#section-3 for details.",
        "notes": "URL with fragment — preserved in all modes.",
        "expected": {
            "conservative": "See https://docs.example.com/api#section-3 for details.",
            "smart_fix": "See https://docs.example.com/api#section-3 for details.",
            "aggressive": "See https://docs.example.com/api#section-3 for details.",
        },
    },
    {
        "id": "struct_url_with_port",
        "input": "The server runs on http://localhost:8080/api.",
        "notes": "URL with port — preserved in all modes.",
        "expected": {
            "conservative": "The server runs on http://localhost:8080/api.",
            "smart_fix": "The server runs on http://localhost:8080/api.",
            "aggressive": "The server runs on http://localhost:8080/api.",
        },
    },
    {
        "id": "struct_multiple_urls",
        "input": "See https://first.com and http://second.com/path for more.",
        "notes": "Multiple URLs — preserved in all modes.",
        "expected": {
            "conservative": "See https://first.com and http://second.com/path for more.",
            "smart_fix": "See https://first.com and http://second.com/path for more.",
            "aggressive": "See https://first.com and http://second.com/path for more.",
        },
    },

    # ── Structure - Email addresses ───────────────────────────────────
    {
        "id": "struct_email",
        "input": "Contact john.doe@company.com for details.",
        "notes": "Email address — preserved in all modes.",
        "expected": {
            "conservative": "Contact john.doe@company.com for details.",
            "smart_fix": "Contact john.doe@company.com for details.",
            "aggressive": "Contact john.doe@company.com for details.",
        },
    },
    {
        "id": "struct_email_with_typo",
        "input": "Send teh report to jane.smith@example.org by tomorow.",
        "notes": "Email preserved, surrounding typos fixed.",
        "expected": {
            "conservative": "Send the report to jane.smith@example.org by tomorrow.",
            "smart_fix": "Send the report to jane.smith@example.org by tomorrow.",
            "aggressive": "Send the report to jane.smith@example.org by tomorrow.",
        },
    },
    {
        "id": "struct_email_multiple",
        "input": "CC admin@site.com and support@site.com on teh email.",
        "notes": "Multiple emails preserved, typo fixed.",
        "expected": {
            "conservative": "CC admin@site.com and support@site.com on the email.",
            "smart_fix": "CC admin@site.com and support@site.com on the email.",
            "aggressive": "CC admin@site.com and support@site.com on the email.",
        },
    },
    {
        "id": "struct_email_subdomain",
        "input": "Reach me at user@mail.co.uk for info.",
        "notes": "Email with subdomain — preserved in all modes.",
        "expected": {
            "conservative": "Reach me at user@mail.co.uk for info.",
            "smart_fix": "Reach me at user@mail.co.uk for info.",
            "aggressive": "Reach me at user@mail.co.uk for info.",
        },
    },

    # ── Structure - File paths ────────────────────────────────────────
    {
        "id": "struct_filepath_windows",
        "input": "Edit C:\\Users\\file.txt to fix the error.",
        "notes": "Windows file path — preserved in all modes.",
        "expected": {
            "conservative": "Edit C:\\Users\\file.txt to fix the error.",
            "smart_fix": "Edit C:\\Users\\file.txt to fix the error.",
            "aggressive": "Edit C:\\Users\\file.txt to fix the error.",
        },
    },
    {
        "id": "struct_filepath_unix",
        "input": "Check /home/user/documents/report.pdf for the data.",
        "notes": "Unix file path — preserved in all modes.",
        "expected": {
            "conservative": "Check /home/user/documents/report.pdf for the data.",
            "smart_fix": "Check /home/user/documents/report.pdf for the data.",
            "aggressive": "Check /home/user/documents/report.pdf for the data.",
        },
    },
    {
        "id": "struct_filepath_with_typo",
        "input": "Open C:\\Projects\\src\\main.py and fix teh bug.",
        "notes": "File path preserved, typo fixed.",
        "expected": {
            "conservative": "Open C:\\Projects\\src\\main.py and fix the bug.",
            "smart_fix": "Open C:\\Projects\\src\\main.py and fix the bug.",
            "aggressive": "Open C:\\Projects\\src\\main.py and fix the bug.",
        },
    },
    {
        "id": "struct_filepath_relative",
        "input": "Run ./scripts/deploy.sh to deploy.",
        "notes": "Relative file path — preserved in all modes.",
        "expected": {
            "conservative": "Run ./scripts/deploy.sh to deploy.",
            "smart_fix": "Run ./scripts/deploy.sh to deploy.",
            "aggressive": "Run ./scripts/deploy.sh to deploy.",
        },
    },

    # ── Structure - Code snippets ─────────────────────────────────────
    {
        "id": "struct_code_inline",
        "input": "Use `print('hello')` to output text.",
        "notes": "Inline code — preserved in all modes.",
        "expected": {
            "conservative": "Use `print('hello')` to output text.",
            "smart_fix": "Use `print('hello')` to output text.",
            "aggressive": "Use `print('hello')` to output text.",
        },
    },
    {
        "id": "struct_code_multiple",
        "input": "Run `npm install` then `npm run build` to compile.",
        "notes": "Multiple inline code spans — preserved in all modes.",
        "expected": {
            "conservative": "Run `npm install` then `npm run build` to compile.",
            "smart_fix": "Run `npm install` then `npm run build` to compile.",
            "aggressive": "Run `npm install` then `npm run build` to compile.",
        },
    },
    {
        "id": "struct_code_with_surrounding_typos",
        "input": "To instal the dependancies run `pip install -r requirements.txt` in teh project folder.",
        "notes": "Code preserved, surrounding typos fixed. Smart/aggressive may add comma after dependencies.",
        "expected": {
            "conservative": "To install the dependencies run `pip install -r requirements.txt` in the project folder.",
            "smart_fix": "To install the dependencies, run `pip install -r requirements.txt` in the project folder.",
            "aggressive": "To install the dependencies, run `pip install -r requirements.txt` in the project folder.",
        },
    },

    # ── Structure - Quoted strings ────────────────────────────────────
    {
        "id": "struct_quote_preserved",
        "input": "The sign read \"NO ENTRY\" in bold letters.",
        "notes": "Quoted string — preserved in all modes.",
        "expected": {
            "conservative": "The sign read \"NO ENTRY\" in bold letters.",
            "smart_fix": "The sign read \"NO ENTRY\" in bold letters.",
            "aggressive": "The sign read \"NO ENTRY\" in bold letters.",
        },
    },

    # ── Structure - Parenthetical remarks ─────────────────────────────
    {
        "id": "struct_parenthesis",
        "input": "The meeting (which was scheduld for Monday) has been moved.",
        "notes": "Parenthetical with typo — fix typo, preserve parens.",
        "expected": {
            "conservative": "The meeting (which was scheduled for Monday) has been moved.",
            "smart_fix": "The meeting (which was scheduled for Monday) has been moved.",
            "aggressive": "The meeting (which was scheduled for Monday) has been moved.",
        },
    },
    {
        "id": "struct_parenthesis_clean",
        "input": "The results (see table 1) are conclusive.",
        "notes": "Clean parenthetical — preserved in all modes.",
        "expected": {
            "conservative": "The results (see table 1) are conclusive.",
            "smart_fix": "The results (see table 1) are conclusive.",
            "aggressive": "The results (see table 1) are conclusive.",
        },
    },

    # ── Structure - Em dashes and en dashes ───────────────────────────
    {
        "id": "struct_em_dash",
        "input": "The answer\u2014if you can call it that\u2014is unclear.",
        "notes": "Em dash — preserved in all modes.",
        "expected": {
            "conservative": "The answer\u2014if you can call it that\u2014is unclear.",
            "smart_fix": "The answer\u2014if you can call it that\u2014is unclear.",
            "aggressive": "The answer\u2014if you can call it that\u2014is unclear.",
        },
    },
    {
        "id": "struct_en_dash",
        "input": "The meeting is from 2:00\u20133:00 PM.",
        "notes": "En dash — preserved in all modes.",
        "expected": {
            "conservative": "The meeting is from 2:00\u20133:00 PM.",
            "smart_fix": "The meeting is from 2:00\u20133:00 PM.",
            "aggressive": "The meeting is from 2:00\u20133:00 PM.",
        },
    },
    {
        "id": "struct_em_dash_with_typo",
        "input": "The answer\u2014if you can beleive it\u2014is teh right one.",
        "notes": "Em dash preserved, surrounding typos fixed.",
        "expected": {
            "conservative": "The answer\u2014if you can believe it\u2014is the right one.",
            "smart_fix": "The answer\u2014if you can believe it\u2014is the right one.",
            "aggressive": "The answer\u2014if you can believe it\u2014is the right one.",
        },
    },

    # ── Structure - Ellipsis ──────────────────────────────────────────
    {
        "id": "struct_ellipsis",
        "input": "I was thinking... maybe we should wait.",
        "notes": "Ellipsis — preserved in all modes.",
        "expected": {
            "conservative": "I was thinking... maybe we should wait.",
            "smart_fix": "I was thinking... maybe we should wait.",
            "aggressive": "I was thinking... maybe we should wait.",
        },
    },
    {
        "id": "struct_ellipsis_with_typo",
        "input": "I was thinkng... maybe we shoud wait.",
        "notes": "Ellipsis preserved, surrounding typos fixed.",
        "expected": {
            "conservative": "I was thinking... maybe we should wait.",
            "smart_fix": "I was thinking... maybe we should wait.",
            "aggressive": "I was thinking... maybe we should wait.",
        },
    },

    # ── Structure - Brackets ──────────────────────────────────────────
    {
        "id": "struct_brackets_square",
        "input": "The report [see appendix A] contains the details.",
        "notes": "Square brackets — preserved in all modes.",
        "expected": {
            "conservative": "The report [see appendix A] contains the details.",
            "smart_fix": "The report [see appendix A] contains the details.",
            "aggressive": "The report [see appendix A] contains the details.",
        },
    },
    {
        "id": "struct_brackets_with_typo",
        "input": "The report [see apendix A] contians the details.",
        "notes": "Brackets preserved, surrounding typos fixed.",
        "expected": {
            "conservative": "The report [see appendix A] contains the details.",
            "smart_fix": "The report [see appendix A] contains the details.",
            "aggressive": "The report [see appendix A] contains the details.",
        },
    },
    {
        "id": "struct_brackets_nested",
        "input": "See section 2.3 [chapter 2 (part 1)] for details.",
        "notes": "Nested brackets — preserved in all modes.",
        "expected": {
            "conservative": "See section 2.3 [chapter 2 (part 1)] for details.",
            "smart_fix": "See section 2.3 [chapter 2 (part 1)] for details.",
            "aggressive": "See section 2.3 [chapter 2 (part 1)] for details.",
        },
    },

    # ── Structure - Curly quotes ──────────────────────────────────────
    {
        "id": "struct_curly_quotes",
        "input": "She said \u201chello\u201d and left.",
        "notes": "Curly double quotes — preserved in all modes.",
        "expected": {
            "conservative": "She said \u201chello\u201d and left.",
            "smart_fix": "She said \u201chello\u201d and left.",
            "aggressive": "She said \u201chello\u201d and left.",
        },
    },
    {
        "id": "struct_curly_single_quotes",
        "input": "It\u2019s a beautiful day.",
        "notes": "Curly single quote — preserved in all modes.",
        "expected": {
            "conservative": "It\u2019s a beautiful day.",
            "smart_fix": "It\u2019s a beautiful day.",
            "aggressive": "It\u2019s a beautiful day.",
        },
    },
    {
        "id": "struct_straight_quotes",
        "input": "She said \"hello\" and left.",
        "notes": "Straight quotes — preserved in all modes.",
        "expected": {
            "conservative": "She said \"hello\" and left.",
            "smart_fix": "She said \"hello\" and left.",
            "aggressive": "She said \"hello\" and left.",
        },
    },

    # ── Structure - Mixed languages ───────────────────────────────────
    {
        "id": "struct_mixed_latin",
        "input": "The curriculum vitae was impressive.",
        "notes": "Latin phrase — preserved in all modes.",
        "expected": {
            "conservative": "The curriculum vitae was impressive.",
            "smart_fix": "The curriculum vitae was impressive.",
            "aggressive": "The curriculum vitae was impressive.",
        },
    },
    {
        "id": "struct_mixed_german",
        "input": "The zeitgeist of the moment was clear.",
        "notes": "German loanword — preserved in all modes.",
        "expected": {
            "conservative": "The zeitgeist of the moment was clear.",
            "smart_fix": "The zeitgeist of the moment was clear.",
            "aggressive": "The zeitgeist of the moment was clear.",
        },
    },

    # ── Structure - Proper nouns ──────────────────────────────────────
    {
        "id": "struct_proper_names",
        "input": "Barack Obama met with Angela Merkel in Berlin.",
        "notes": "Proper names — preserved in all modes.",
        "expected": {
            "conservative": "Barack Obama met with Angela Merkel in Berlin.",
            "smart_fix": "Barack Obama met with Angela Merkel in Berlin.",
            "aggressive": "Barack Obama met with Angela Merkel in Berlin.",
        },
    },
    {
        "id": "struct_proper_brands",
        "input": "I use Microsoft Word and Google Chrome daily.",
        "notes": "Brand names — preserved in all modes.",
        "expected": {
            "conservative": "I use Microsoft Word and Google Chrome daily.",
            "smart_fix": "I use Microsoft Word and Google Chrome daily.",
            "aggressive": "I use Microsoft Word and Google Chrome daily.",
        },
    },
    {
        "id": "struct_proper_places",
        "input": "I visited New York and San Francisco last year.",
        "notes": "Place names — preserved in all modes.",
        "expected": {
            "conservative": "I visited New York and San Francisco last year.",
            "smart_fix": "I visited New York and San Francisco last year.",
            "aggressive": "I visited New York and San Francisco last year.",
        },
    },
    {
        "id": "struct_proper_with_typo",
        "input": "I met Barak Obama in Washinton DC.",
        "notes": "Proper noun typos fixed. Smart/aggressive may add comma.",
        "expected": {
            "conservative": "I met Barack Obama in Washington DC.",
            "smart_fix": "I met Barack Obama in Washington, DC.",
            "aggressive": "I met Barack Obama in Washington, DC.",
        },
    },
    {
        "id": "struct_proper_unusual_names",
        "input": "Xzavier and Quvenzhane went to the store.",
        "notes": "Unusual proper names — preserved in all modes.",
        "expected": {
            "conservative": "Xzavier and Quvenzhane went to the store.",
            "smart_fix": "Xzavier and Quvenzhane went to the store.",
            "aggressive": "Xzavier and Quvenzhane went to the store.",
        },
    },

    # ── Structure - ALL-CAPS ──────────────────────────────────────────
    {
        "id": "struct_allcaps_emphasis",
        "input": "DO NOT forget to submit the form.",
        "notes": "ALL-CAPS emphasis — preserved in all modes.",
        "expected": {
            "conservative": "DO NOT forget to submit the form.",
            "smart_fix": "DO NOT forget to submit the form.",
            "aggressive": "DO NOT forget to submit the form.",
        },
    },
    {
        "id": "struct_allcaps_word",
        "input": "This is VERY important.",
        "notes": "ALL-CAPS word — preserved in all modes.",
        "expected": {
            "conservative": "This is VERY important.",
            "smart_fix": "This is VERY important.",
            "aggressive": "This is VERY important.",
        },
    },
    {
        "id": "struct_allcaps_with_typo",
        "input": "THIS IS IMPRTANT. DO NOT FORGET.",
        "notes": "ALL-CAPS with typo — fix typo, preserve caps.",
        "expected": {
            "conservative": "THIS IS IMPORTANT. DO NOT FORGET.",
            "smart_fix": "THIS IS IMPORTANT. DO NOT FORGET.",
            "aggressive": "THIS IS IMPORTANT. DO NOT FORGET.",
        },
    },

    # ── Structure - Title Case ────────────────────────────────────────
    {
        "id": "struct_title_case",
        "input": "The Lord of the Rings Is a Great Book.",
        "notes": "Title case — preserved in all modes.",
        "expected": {
            "conservative": "The Lord of the Rings Is a Great Book.",
            "smart_fix": "The Lord of the Rings Is a Great Book.",
            "aggressive": "The Lord of the Rings Is a Great Book.",
        },
    },
    {
        "id": "struct_title_case_with_typo",
        "input": "The Quik Brown Fox Jumps Over Teh Lazy Dog.",
        "notes": "Title case with typos — fix typos, preserve case.",
        "expected": {
            "conservative": "The Quick Brown Fox Jumps Over The Lazy Dog.",
            "smart_fix": "The Quick Brown Fox Jumps Over The Lazy Dog.",
            "aggressive": "The Quick Brown Fox Jumps Over The Lazy Dog.",
        },
    },

    # ── Structure - Multiple spaces ───────────────────────────────────
    {
        "id": "struct_space_before_period",
        "input": "This is a sentence . With a space before the period .",
        "notes": "Space before period — fixed in all modes.",
        "expected": {
            "conservative": "This is a sentence. With a space before the period.",
            "smart_fix": "This is a sentence. With a space before the period.",
            "aggressive": "This is a sentence. With a space before the period.",
        },
    },

    # ── Structure - Tabs ──────────────────────────────────────────────
    {
        "id": "struct_tabs",
        "input": "Column1\tColumn2\tColumn3",
        "notes": "Tab-separated values — preserved in all modes.",
        "expected": {
            "conservative": "Column1\tColumn2\tColumn3",
            "smart_fix": "Column1\tColumn2\tColumn3",
            "aggressive": "Column1\tColumn2\tColumn3",
        },
    },
    {
        "id": "struct_tabs_with_text",
        "input": "Name:\tJohn Doe\nAge:\tThirty\nCity:\tNew York",
        "notes": "Tab-formatted text — preserved in all modes.",
        "expected": {
            "conservative": "Name:\tJohn Doe\nAge:\tThirty\nCity:\tNew York",
            "smart_fix": "Name:\tJohn Doe\nAge:\tThirty\nCity:\tNew York",
            "aggressive": "Name:\tJohn Doe\nAge:\tThirty\nCity:\tNew York",
        },
    },

    # ── Structure - Unicode ───────────────────────────────────────────
    {
        "id": "struct_unicode_em_dash",
        "input": "The answer\u2014is clear.",
        "notes": "Unicode em dash — preserved in all modes.",
        "expected": {
            "conservative": "The answer\u2014is clear.",
            "smart_fix": "The answer\u2014is clear.",
            "aggressive": "The answer\u2014is clear.",
        },
    },
    {
        "id": "struct_unicode_smart_quotes",
        "input": "She said \u2018hello\u2019 and \u201cgoodbye\u201d.",
        "notes": "Unicode smart quotes — preserved in all modes.",
        "expected": {
            "conservative": "She said \u2018hello\u2019 and \u201cgoodbye\u201d.",
            "smart_fix": "She said \u2018hello\u2019 and \u201cgoodbye\u201d.",
            "aggressive": "She said \u2018hello\u2019 and \u201cgoodbye\u201d.",
        },
    },
    {
        "id": "struct_unicode_nbsp",
        "input": "This has\u00a0a non-breaking space.",
        "notes": "Non-breaking space — preserved in all modes.",
        "expected": {
            "conservative": "This has\u00a0a non-breaking space.",
            "smart_fix": "This has\u00a0a non-breaking space.",
            "aggressive": "This has\u00a0a non-breaking space.",
        },
    },
    {
        "id": "struct_unicode_copyright",
        "input": "Copyright \u00a9 2026 Acme Corp. All rights reserved.",
        "notes": "Unicode copyright symbol — preserved in all modes.",
        "expected": {
            "conservative": "Copyright \u00a9 2026 Acme Corp. All rights reserved.",
            "smart_fix": "Copyright \u00a9 2026 Acme Corp. All rights reserved.",
            "aggressive": "Copyright \u00a9 2026 Acme Corp. All rights reserved.",
        },
    },
    {
        "id": "struct_unicode_arrows",
        "input": "Click the \u2192 arrow to proceed. Press \u2190 to go back.",
        "notes": "Unicode arrows — preserved in all modes.",
        "expected": {
            "conservative": "Click the \u2192 arrow to proceed. Press \u2190 to go back.",
            "smart_fix": "Click the \u2192 arrow to proceed. Press \u2190 to go back.",
            "aggressive": "Click the \u2192 arrow to proceed. Press \u2190 to go back.",
        },
    },

    # ── Edge cases - Single character ─────────────────────────────────
    {
        "id": "edge_single_char_a",
        "input": "a",
        "notes": "Single character — preserved in all modes.",
        "expected": {
            "conservative": "a",
            "smart_fix": "a",
            "aggressive": "a",
        },
    },
    {
        "id": "edge_single_char_I",
        "input": "I",
        "notes": "Single character pronoun — preserved in all modes.",
        "expected": {
            "conservative": "I",
            "smart_fix": "I",
            "aggressive": "I",
        },
    },
    {
        "id": "edge_single_char_period",
        "input": ".",
        "notes": "Single punctuation — preserved in all modes.",
        "expected": {
            "conservative": ".",
            "smart_fix": ".",
            "aggressive": ".",
        },
    },

    # ── Edge cases - Two words ────────────────────────────────────────
    {
        "id": "edge_two_words_clean",
        "input": "hello world",
        "notes": "Two clean words — no expected.",
    },
    {
        "id": "edge_two_words_typo",
        "input": "helo wrold",
        "notes": "Two misspelled words — fixed in all modes.",
        "expected": {
            "conservative": "hello world",
            "smart_fix": "hello world",
            "aggressive": "hello world",
        },
    },

    # ── Edge cases - Long sentence ────────────────────────────────────
    {
        "id": "edge_long_sentence",
        "input": (
            "The quick brown fox jumps over the lazy dog near the riverbank "
            "where the tall grass sways gently in the warm afternoon breeze "
            "while the birds sing their melodious songs high above in the "
            "branches of the ancient oak tree that has stood there for over "
            "a hundred years providing shade and shelter to all who pass by."
        ),
        "notes": "100+ word sentence, no typos — no expected.",
    },
    {
        "id": "edge_long_sentence_with_typos",
        "input": (
            "The quik brown fx jumps ovr the lzy dog near teh riverbank "
            "where teh tall grass sways gentally in the warm afternon breeze "
            "while teh birds sing there melodious songs hgh above in teh "
            "brances of the anciet oak tree that has stoof there for ovr "
            "a hundrd years provding shade and sheltar to all who pas by."
        ),
        "notes": "100+ word sentence with many scattered typos — no expected.",
    },

    # ── Edge cases - Only punctuation ─────────────────────────────────
    {
        "id": "edge_only_punctuation",
        "input": "...",
        "notes": "Only ellipsis — preserved in all modes.",
        "expected": {
            "conservative": "...",
            "smart_fix": "...",
            "aggressive": "...",
        },
    },
    {
        "id": "edge_punctuation_mix",
        "input": "!@#$%^&*()",
        "notes": "Mixed punctuation — preserved in all modes.",
        "expected": {
            "conservative": "!@#$%^&*()",
            "smart_fix": "!@#$%^&*()",
            "aggressive": "!@#$%^&*()",
        },
    },
    {
        "id": "edge_question_marks",
        "input": "???",
        "notes": "Multiple question marks — preserved in all modes.",
        "expected": {
            "conservative": "???",
            "smart_fix": "???",
            "aggressive": "???",
        },
    },
    {
        "id": "edge_exclamation_marks",
        "input": "!!!",
        "notes": "Multiple exclamation marks — preserved in all modes.",
        "expected": {
            "conservative": "!!!",
            "smart_fix": "!!!",
            "aggressive": "!!!",
        },
    },

    # ── Edge cases - Already perfect ──────────────────────────────────
    {
        "id": "edge_perfect_paragraph",
        "input": (
            "The meeting is scheduled for Monday at 3 PM. "
            "Please bring your reports and be prepared to discuss the quarterly results."
        ),
        "notes": "Already perfect — preserved in all modes.",
        "expected": {
            "conservative": (
                "The meeting is scheduled for Monday at 3 PM. "
                "Please bring your reports and be prepared to discuss the quarterly results."
            ),
            "smart_fix": (
                "The meeting is scheduled for Monday at 3 PM. "
                "Please bring your reports and be prepared to discuss the quarterly results."
            ),
            "aggressive": (
                "The meeting is scheduled for Monday at 3 PM. "
                "Please bring your reports and be prepared to discuss the quarterly results."
            ),
        },
    },
    {
        "id": "edge_perfect_technical",
        "input": (
            "The API returns a JSON object with a 200 status code. "
            "The response includes the user ID and a timestamp."
        ),
        "notes": "Already perfect technical text — preserved in all modes.",
        "expected": {
            "conservative": (
                "The API returns a JSON object with a 200 status code. "
                "The response includes the user ID and a timestamp."
            ),
            "smart_fix": (
                "The API returns a JSON object with a 200 status code. "
                "The response includes the user ID and a timestamp."
            ),
            "aggressive": (
                "The API returns a JSON object with a 200 status code. "
                "The response includes the user ID and a timestamp."
            ),
        },
    },

    # ── Edge cases - Intentional repetition ───────────────────────────
    {
        "id": "edge_repetition_very",
        "input": "It was very very very cold outside.",
        "notes": "Intentional repetition — preserved in all modes.",
        "expected": {
            "conservative": "It was very very very cold outside.",
            "smart_fix": "It was very very very cold outside.",
            "aggressive": "It was very very very cold outside.",
        },
    },
    {
        "id": "edge_repetition_really",
        "input": "I really really really want this to work.",
        "notes": "Intentional repetition — preserved in all modes.",
        "expected": {
            "conservative": "I really really really want this to work.",
            "smart_fix": "I really really really want this to work.",
            "aggressive": "I really really really want this to work.",
        },
    },
    {
        "id": "edge_repetition_so",
        "input": "This is so so so much better.",
        "notes": "Intentional repetition — preserved in all modes.",
        "expected": {
            "conservative": "This is so so so much better.",
            "smart_fix": "This is so so so much better.",
            "aggressive": "This is so so so much better.",
        },
    },

    # ── Edge cases - Sentence fragments ───────────────────────────────
    {
        "id": "edge_fragment_because",
        "input": "Because reasons.",
        "notes": "Sentence fragment — preserved in all modes.",
        "expected": {
            "conservative": "Because reasons.",
            "smart_fix": "Because reasons.",
            "aggressive": "Because reasons.",
        },
    },
    {
        "id": "edge_fragment_maybe",
        "input": "Maybe. Maybe not.",
        "notes": "Fragments — preserved in all modes.",
        "expected": {
            "conservative": "Maybe. Maybe not.",
            "smart_fix": "Maybe. Maybe not.",
            "aggressive": "Maybe. Maybe not.",
        },
    },
    {
        "id": "edge_fragment_yep",
        "input": "Yep.",
        "notes": "Single-word fragment — preserved in all modes.",
        "expected": {
            "conservative": "Yep.",
            "smart_fix": "Yep.",
            "aggressive": "Yep.",
        },
    },
    {
        "id": "edge_fragment_nope",
        "input": "Nope.",
        "notes": "Single-word fragment — preserved in all modes.",
        "expected": {
            "conservative": "Nope.",
            "smart_fix": "Nope.",
            "aggressive": "Nope.",
        },
    },

    # ── Edge cases - Lists with various markers ───────────────────────
    {
        "id": "edge_list_dash",
        "input": "Items:\n- First\n- Second\n- Third",
        "notes": "Dash list — preserved in all modes.",
        "expected": {
            "conservative": "Items:\n- First\n- Second\n- Third",
            "smart_fix": "Items:\n- First\n- Second\n- Third",
            "aggressive": "Items:\n- First\n- Second\n- Third",
        },
    },
    {
        "id": "edge_list_asterisk",
        "input": "Items:\n* First\n* Second\n* Third",
        "notes": "Asterisk list — preserved in all modes.",
        "expected": {
            "conservative": "Items:\n* First\n* Second\n* Third",
            "smart_fix": "Items:\n* First\n* Second\n* Third",
            "aggressive": "Items:\n* First\n* Second\n* Third",
        },
    },
    {
        "id": "edge_list_numbered",
        "input": "Steps:\n1. First step\n2. Second step\n3. Third step",
        "notes": "Numbered list — preserved in all modes.",
        "expected": {
            "conservative": "Steps:\n1. First step\n2. Second step\n3. Third step",
            "smart_fix": "Steps:\n1. First step\n2. Second step\n3. Third step",
            "aggressive": "Steps:\n1. First step\n2. Second step\n3. Third step",
        },
    },
    {
        "id": "edge_list_lettered",
        "input": "Options:\na) First option\nb) Second option\nc) Third option",
        "notes": "Lettered list — preserved in all modes.",
        "expected": {
            "conservative": "Options:\na) First option\nb) Second option\nc) Third option",
            "smart_fix": "Options:\na) First option\nb) Second option\nc) Third option",
            "aggressive": "Options:\na) First option\nb) Second option\nc) Third option",
        },
    },
    {
        "id": "edge_list_numbered_paren",
        "input": "Items:\n1) First\n2) Second\n3) Third",
        "notes": "Numbered paren list — preserved in all modes.",
        "expected": {
            "conservative": "Items:\n1) First\n2) Second\n3) Third",
            "smart_fix": "Items:\n1) First\n2) Second\n3) Third",
            "aggressive": "Items:\n1) First\n2) Second\n3) Third",
        },
    },
    {
        "id": "edge_list_with_typos",
        "input": "Shoping list:\n- Applse\n- Banannas\n- Ornges\n- Mangose",
        "notes": "List with typos — all typos fixed in all modes.",
        "expected": {
            "conservative": "Shopping list:\n- Apples\n- Bananas\n- Oranges\n- Mangoes",
            "smart_fix": "Shopping list:\n- Apples\n- Bananas\n- Oranges\n- Mangoes",
            "aggressive": "Shopping list:\n- Apples\n- Bananas\n- Oranges\n- Mangoes",
        },
    },

    # ── Edge cases - Headers/markdown ─────────────────────────────────
    {
        "id": "edge_header_markdown",
        "input": "# Heading One\n\nSome text under the heading.",
        "notes": "Markdown heading — preserved in all modes.",
        "expected": {
            "conservative": "# Heading One\n\nSome text under the heading.",
            "smart_fix": "# Heading One\n\nSome text under the heading.",
            "aggressive": "# Heading One\n\nSome text under the heading.",
        },
    },
    {
        "id": "edge_header_markdown_h2",
        "input": "## Subheading\n\nContent goes here.",
        "notes": "Markdown h2 — preserved in all modes.",
        "expected": {
            "conservative": "## Subheading\n\nContent goes here.",
            "smart_fix": "## Subheading\n\nContent goes here.",
            "aggressive": "## Subheading\n\nContent goes here.",
        },
    },

    # ── Edge cases - Blockquotes ──────────────────────────────────────
    {
        "id": "edge_blockquote",
        "input": "> This is a quoted line.",
        "notes": "Blockquote — preserved in all modes.",
        "expected": {
            "conservative": "> This is a quoted line.",
            "smart_fix": "> This is a quoted line.",
            "aggressive": "> This is a quoted line.",
        },
    },
    {
        "id": "edge_blockquote_with_typo",
        "input": "> Teh qoute has a spelking error.",
        "notes": "Blockquote with typos — fix typos, preserve blockquote marker.",
        "expected": {
            "conservative": "> The quote has a spelling error.",
            "smart_fix": "> The quote has a spelling error.",
            "aggressive": "> The quote has a spelling error.",
        },
    },
    {
        "id": "edge_blockquote_multi",
        "input": "> First quoted line.\n> Second quoted line.\n> Third quoted line.",
        "notes": "Multi-line blockquote — preserved in all modes.",
        "expected": {
            "conservative": "> First quoted line.\n> Second quoted line.\n> Third quoted line.",
            "smart_fix": "> First quoted line.\n> Second quoted line.\n> Third quoted line.",
            "aggressive": "> First quoted line.\n> Second quoted line.\n> Third quoted line.",
        },
    },

    # ── Edge cases - Markdown formatting ──────────────────────────────
    {
        "id": "edge_markdown_bold",
        "input": "This is **very** important.",
        "notes": "Markdown bold — preserved in all modes.",
        "expected": {
            "conservative": "This is **very** important.",
            "smart_fix": "This is **very** important.",
            "aggressive": "This is **very** important.",
        },
    },
    {
        "id": "edge_markdown_italic",
        "input": "This is *emphasized* text.",
        "notes": "Markdown italic — preserved in all modes.",
        "expected": {
            "conservative": "This is *emphasized* text.",
            "smart_fix": "This is *emphasized* text.",
            "aggressive": "This is *emphasized* text.",
        },
    },
    {
        "id": "edge_markdown_link",
        "input": "See [this page](https://example.com) for details.",
        "notes": "Markdown link — preserved in all modes.",
        "expected": {
            "conservative": "See [this page](https://example.com) for details.",
            "smart_fix": "See [this page](https://example.com) for details.",
            "aggressive": "See [this page](https://example.com) for details.",
        },
    },
    {
        "id": "edge_markdown_code_block",
        "input": "```\nprint('hello')\n```",
        "notes": "Markdown code block — preserved in all modes.",
        "expected": {
            "conservative": "```\nprint('hello')\n```",
            "smart_fix": "```\nprint('hello')\n```",
            "aggressive": "```\nprint('hello')\n```",
        },
    },
    {
        "id": "edge_markdown_inline_code_with_typo",
        "input": "Use the `lenght` functoin to get the size.",
        "notes": "Inline code preserved, surrounding typo fixed.",
        "expected": {
            "conservative": "Use the `lenght` function to get the size.",
            "smart_fix": "Use the `lenght` function to get the size.",
            "aggressive": "Use the `lenght` function to get the size.",
        },
    },
    {
        "id": "edge_markdown_horizontal_rule",
        "input": "Section one.\n\n---\n\nSection two.",
        "notes": "Markdown horizontal rule — preserved in all modes.",
        "expected": {
            "conservative": "Section one.\n\n---\n\nSection two.",
            "smart_fix": "Section one.\n\n---\n\nSection two.",
            "aggressive": "Section one.\n\n---\n\nSection two.",
        },
    },

    # ── Edge cases - Dates ────────────────────────────────────────────
    {
        "id": "edge_date_iso",
        "input": "The deadline is 2026-06-30.",
        "notes": "ISO date — preserved in all modes.",
        "expected": {
            "conservative": "The deadline is 2026-06-30.",
            "smart_fix": "The deadline is 2026-06-30.",
            "aggressive": "The deadline is 2026-06-30.",
        },
    },
    {
        "id": "edge_date_us",
        "input": "The meeting is on 06/15/2026.",
        "notes": "US date format — preserved in all modes.",
        "expected": {
            "conservative": "The meeting is on 06/15/2026.",
            "smart_fix": "The meeting is on 06/15/2026.",
            "aggressive": "The meeting is on 06/15/2026.",
        },
    },
    {
        "id": "edge_date_written",
        "input": "The event is on June 15, 2026.",
        "notes": "Written date — preserved in all modes.",
        "expected": {
            "conservative": "The event is on June 15, 2026.",
            "smart_fix": "The event is on June 15, 2026.",
            "aggressive": "The event is on June 15, 2026.",
        },
    },
    {
        "id": "edge_date_written_with_typo",
        "input": "The evnt is on June 15 2026.",
        "notes": "Written date with typo and missing comma.",
        "expected": {
            "conservative": "The event is on June 15 2026.",
            "smart_fix": "The event is on June 15, 2026.",
            "aggressive": "The event is on June 15, 2026.",
        },
    },
    {
        "id": "edge_date_time",
        "input": "The call is at 3:30 PM EST.",
        "notes": "Time with timezone — preserved in all modes.",
        "expected": {
            "conservative": "The call is at 3:30 PM EST.",
            "smart_fix": "The call is at 3:30 PM EST.",
            "aggressive": "The call is at 3:30 PM EST.",
        },
    },
    {
        "id": "edge_date_range",
        "input": "The conference runs from June 10\u201312, 2026.",
        "notes": "Date range with en dash — preserved in all modes.",
        "expected": {
            "conservative": "The conference runs from June 10\u201312, 2026.",
            "smart_fix": "The conference runs from June 10\u201312, 2026.",
            "aggressive": "The conference runs from June 10\u201312, 2026.",
        },
    },

    # ── Edge cases - Phone numbers ────────────────────────────────────
    {
        "id": "edge_phone_us",
        "input": "Call me at (555) 123-4567.",
        "notes": "US phone number — preserved in all modes.",
        "expected": {
            "conservative": "Call me at (555) 123-4567.",
            "smart_fix": "Call me at (555) 123-4567.",
            "aggressive": "Call me at (555) 123-4567.",
        },
    },
    {
        "id": "edge_phone_dotted",
        "input": "The number is 555.123.4567.",
        "notes": "Dotted phone number — preserved in all modes.",
        "expected": {
            "conservative": "The number is 555.123.4567.",
            "smart_fix": "The number is 555.123.4567.",
            "aggressive": "The number is 555.123.4567.",
        },
    },
    {
        "id": "edge_phone_international",
        "input": "Dial +1-555-123-4567 for support.",
        "notes": "International phone number — preserved in all modes.",
        "expected": {
            "conservative": "Dial +1-555-123-4567 for support.",
            "smart_fix": "Dial +1-555-123-4567 for support.",
            "aggressive": "Dial +1-555-123-4567 for support.",
        },
    },
    {
        "id": "edge_phone_with_typo",
        "input": "Call teh support line at (555) 123-4567 for hlep.",
        "notes": "Phone preserved, surrounding typos fixed.",
        "expected": {
            "conservative": "Call the support line at (555) 123-4567 for help.",
            "smart_fix": "Call the support line at (555) 123-4567 for help.",
            "aggressive": "Call the support line at (555) 123-4567 for help.",
        },
    },

    # ── Edge cases - Addresses ────────────────────────────────────────
    {
        "id": "edge_address_us",
        "input": "The office is at 123 Main Street, Suite 400, New York, NY 10001.",
        "notes": "US address — preserved in all modes.",
        "expected": {
            "conservative": "The office is at 123 Main Street, Suite 400, New York, NY 10001.",
            "smart_fix": "The office is at 123 Main Street, Suite 400, New York, NY 10001.",
            "aggressive": "The office is at 123 Main Street, Suite 400, New York, NY 10001.",
        },
    },
    {
        "id": "edge_address_with_typo",
        "input": "The offce is at 123 Main Stret, Sute 400, New York, NY 10001.",
        "notes": "Address with typos — fix typos, preserve structure.",
        "expected": {
            "conservative": "The office is at 123 Main Street, Suite 400, New York, NY 10001.",
            "smart_fix": "The office is at 123 Main Street, Suite 400, New York, NY 10001.",
            "aggressive": "The office is at 123 Main Street, Suite 400, New York, NY 10001.",
        },
    },
    {
        "id": "edge_address_po_box",
        "input": "Send mail to P.O. Box 12345, Los Angeles, CA 90001.",
        "notes": "P.O. Box address — preserved in all modes.",
        "expected": {
            "conservative": "Send mail to P.O. Box 12345, Los Angeles, CA 90001.",
            "smart_fix": "Send mail to P.O. Box 12345, Los Angeles, CA 90001.",
            "aggressive": "Send mail to P.O. Box 12345, Los Angeles, CA 90001.",
        },
    },

    # ── Edge cases - Math ─────────────────────────────────────────────
    {
        "id": "edge_math_simple",
        "input": "The result is 2 + 2 = 4.",
        "notes": "Simple math — preserved in all modes.",
        "expected": {
            "conservative": "The result is 2 + 2 = 4.",
            "smart_fix": "The result is 2 + 2 = 4.",
            "aggressive": "The result is 2 + 2 = 4.",
        },
    },
    {
        "id": "edge_math_complex",
        "input": "Solve for x: 3x^2 + 5x - 2 = 0.",
        "notes": "Complex math expression — preserved in all modes.",
        "expected": {
            "conservative": "Solve for x: 3x^2 + 5x - 2 = 0.",
            "smart_fix": "Solve for x: 3x^2 + 5x - 2 = 0.",
            "aggressive": "Solve for x: 3x^2 + 5x - 2 = 0.",
        },
    },
    {
        "id": "edge_math_percentage",
        "input": "The growth rate was 15.5% year-over-year.",
        "notes": "Percentage — preserved in all modes.",
        "expected": {
            "conservative": "The growth rate was 15.5% year-over-year.",
            "smart_fix": "The growth rate was 15.5% year-over-year.",
            "aggressive": "The growth rate was 15.5% year-over-year.",
        },
    },

    # ── Edge cases - Chemical formulas ────────────────────────────────
    {
        "id": "edge_chemical_water",
        "input": "The formula for water is H2O.",
        "notes": "Chemical formula — preserved in all modes.",
        "expected": {
            "conservative": "The formula for water is H2O.",
            "smart_fix": "The formula for water is H2O.",
            "aggressive": "The formula for water is H2O.",
        },
    },
    {
        "id": "edge_chemical_complex",
        "input": "The reaction produces CO2 and H2O as byproducts.",
        "notes": "Chemical formulas — preserved in all modes.",
        "expected": {
            "conservative": "The reaction produces CO2 and H2O as byproducts.",
            "smart_fix": "The reaction produces CO2 and H2O as byproducts.",
            "aggressive": "The reaction produces CO2 and H2O as byproducts.",
        },
    },

    # ── Edge cases - Latin abbreviations ──────────────────────────────
    {
        "id": "edge_latin_ie",
        "input": "The answer, i.e., the correct one, is 42.",
        "notes": "Latin abbreviation i.e. — preserved in all modes.",
        "expected": {
            "conservative": "The answer, i.e., the correct one, is 42.",
            "smart_fix": "The answer, i.e., the correct one, is 42.",
            "aggressive": "The answer, i.e., the correct one, is 42.",
        },
    },
    {
        "id": "edge_latin_eg",
        "input": "Fruits, e.g., apples and oranges, are healthy.",
        "notes": "Latin abbreviation e.g. — preserved in all modes.",
        "expected": {
            "conservative": "Fruits, e.g., apples and oranges, are healthy.",
            "smart_fix": "Fruits, e.g., apples and oranges, are healthy.",
            "aggressive": "Fruits, e.g., apples and oranges, are healthy.",
        },
    },
    {
        "id": "edge_latin_etc",
        "input": "Bring supplies like pens, paper, tape, etc.",
        "notes": "Latin abbreviation etc. — preserved in all modes.",
        "expected": {
            "conservative": "Bring supplies like pens, paper, tape, etc.",
            "smart_fix": "Bring supplies like pens, paper, tape, etc.",
            "aggressive": "Bring supplies like pens, paper, tape, etc.",
        },
    },
    {
        "id": "edge_latin_vs",
        "input": "The debate was apples vs. oranges.",
        "notes": "Latin abbreviation vs. — preserved in all modes.",
        "expected": {
            "conservative": "The debate was apples vs. oranges.",
            "smart_fix": "The debate was apples vs. oranges.",
            "aggressive": "The debate was apples vs. oranges.",
        },
    },
    {
        "id": "edge_latin_et_al",
        "input": "The study was conducted by Smith et al.",
        "notes": "Latin abbreviation et al. — preserved in all modes.",
        "expected": {
            "conservative": "The study was conducted by Smith et al.",
            "smart_fix": "The study was conducted by Smith et al.",
            "aggressive": "The study was conducted by Smith et al.",
        },
    },

    # ── Stress tests ──────────────────────────────────────────────────
    {
        "id": "stress_long_passage",
        "input": (
            "The quik brown fx jumps over the lzay dog near the riverbank "
            "where teh tall grass sways gently in the warm afternoon breeze. "
            "Birds sing their melodious songs hgh above in the brances of "
            "the ancient oak tree. The comittee adressed the ocurrence "
            "imediately after the meeting. We recieve the foriegn achievment "
            "report on Wenesday. The definate neccessary enviroment for this "
            "projcet is a quiet office with good lighting. Him and me was "
            "late becuase the traffic was terrible. The team dont have enough "
            "time to finish the project by the deadlne. The budget is $4.2 "
            "million and revenue grew 15% year-over-year. She is taller then "
            "him and would rather stay then go. I do not want to loose this "
            "game. The goverment passed a new law. Do not mispell their names. "
            "Wait untill I get there. I did not mean to embarass you. This is "
            "a rare ocassion. We can accomodate up to ten guests. I do not "
            "want to embrase anyone. The milennium celebration was huge. "
            "Aparently the meeting was cancelled. I am dissapointed with "
            "the results. The rythm of the music was catchy. The project "
            "was very succesful. Please recieve the package at the door. "
            "She is a foriegn exchange student. She will achive great things. "
            "The police will sieze the evidence. The hieght of the building "
            "is impressive. There is too much caffiene in this drink."
        ),
        "notes": "500+ word passage with scattered typos — stress test, no expected.",
    },
    {
        "id": "stress_multi_paragraph_spacing",
        "input": (
            "First paragraph with a teh typo.\n\n\n"
            "Second paragraph with teh error and some lenght issues.\n\n\n\n"
            "Third paragraph is mostly clean.\n\n"
            "Fourth paragraph has a recieve error and a goverment typo."
        ),
        "notes": "Multiple paragraphs with varying line spacing — stress test, no expected.",
    },
    {
        "id": "stress_mixed_clean_dirty",
        "input": (
            "This paragraph is perfectly clean and has no errors at all.\n\n"
            "Teh quik brown fx jumps ovr the lzy dog in teh park.\n\n"
            "Another clean paragraph with no issues to report.\n\n"
            "The comittee adressed the ocurrence imediately after the meeting.\n\n"
            "Final clean paragraph to end the test."
        ),
        "notes": "Mixed clean and dirty paragraphs — stress test, no expected.",
    },
    {
        "id": "stress_recycled_samples",
        "input": (
            "Teh project recieved teh update.\n\n\n"
            "The quik brown fx jumps ovr the lzy dog.\n\n\n\n"
            "The comittee adressed the ocurrence imediately.\n\n"
            "We recieve the foriegn achievment report on Wenesday."
        ),
        "notes": "Recycled existing samples with extra spacing — stress test, no expected.",
    },
)


@dataclass
class EvalRow:
    sample_id: str
    strength: str
    pass_index: int
    ok: bool
    units: int | None
    elapsed_ms: int | None
    input: str
    output: str | None
    expected: str | None
    error: str | None = None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate Stet corrections across strengths."
    )
    parser.add_argument(
        "--passes",
        type=int,
        default=5,
        help="Passes per sample per strength. Default: 5.",
    )
    parser.add_argument(
        "--strength",
        choices=STRENGTHS,
        action="append",
        help="Strength to evaluate. Repeat to select multiple. Default: all.",
    )
    parser.add_argument(
        "--sample",
        action="append",
        help="Sample id to evaluate. Repeat to select multiple. Default: all.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Dry-run the matrix without loading the model or backend.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON lines instead of a compact text table.",
    )
    return parser


def _selected_samples(sample_ids: list[str] | None) -> list[dict[str, str]]:
    if not sample_ids:
        return list(SAMPLES)

    wanted = set(sample_ids)
    selected = [sample for sample in SAMPLES if sample["id"] in wanted]
    missing = sorted(wanted - {sample["id"] for sample in selected})
    if missing:
        raise SystemExit(f"Unknown sample id(s): {', '.join(missing)}")
    return selected


def _print_row(row: EvalRow, as_json: bool) -> None:
    if as_json:
        print(json.dumps(asdict(row), ensure_ascii=False))
        return

    status = "ok" if row.ok else "fail"
    elapsed = "-" if row.elapsed_ms is None else f"{row.elapsed_ms}ms"
    output = row.output if row.output is not None else row.error
    expected = "" if row.expected is None else f" expected={row.expected!r}"
    print(
        f"{status:4} {row.sample_id:30} {row.strength:12} "
        f"pass={row.pass_index:<2} units={row.units!s:<3} "
        f"time={elapsed:<7} output={output!r}{expected}"
    )


def _expected_output(sample: dict[str, object], strength: str) -> str | None:
    expected = sample.get("expected")
    if expected is None:
        return None
    if isinstance(expected, dict):
        return expected.get(strength)  # type: ignore[return-value]
    return expected  # type: ignore[return-value]


def _run_offline(args: argparse.Namespace) -> int:
    strengths = tuple(args.strength or STRENGTHS)
    samples = _selected_samples(args.sample)
    for sample in samples:
        for strength in strengths:
            for pass_index in range(1, args.passes + 1):
                _print_row(
                    EvalRow(
                        sample_id=sample["id"],
                        strength=strength,
                        pass_index=pass_index,
                        ok=True,
                        units=None,
                        elapsed_ms=None,
                        input=sample["input"],
                        expected=_expected_output(sample, strength),
                        output="[offline] " + sample["input"],
                    ),
                    args.json,
                )
    return 0


def _run_live(args: argparse.Namespace) -> int:
    from stet.core.config import ConfigManager
    from stet.llm.model_manager import ModelManager

    strengths = tuple(args.strength or STRENGTHS)
    samples = _selected_samples(args.sample)
    manager = ModelManager(ConfigManager())
    failures = 0

    for sample in samples:
        for strength in strengths:
            for pass_index in range(1, args.passes + 1):
                started = perf_counter()
                try:
                    output, units = manager.correct_text_patch(
                        sample["input"],
                        strength=strength,
                    )
                    elapsed_ms = int((perf_counter() - started) * 1000)
                    expected = _expected_output(sample, strength)
                    ok = output is not None and (expected is None or output == expected)
                    if not ok:
                        failures += 1
                    _print_row(
                        EvalRow(
                            sample_id=sample["id"],
                            strength=strength,
                            pass_index=pass_index,
                            ok=ok,
                            units=units,
                            elapsed_ms=elapsed_ms,
                            input=sample["input"],
                            expected=expected,
                            output=output,
                            error=(
                                None
                                if ok
                                else "output mismatch"
                                if output is not None
                                else "backend returned no correction"
                            ),
                        ),
                        args.json,
                    )
                except Exception as exc:
                    failures += 1
                    elapsed_ms = int((perf_counter() - started) * 1000)
                    _print_row(
                        EvalRow(
                            sample_id=sample["id"],
                            strength=strength,
                            pass_index=pass_index,
                            ok=False,
                            units=None,
                            elapsed_ms=elapsed_ms,
                            input=sample["input"],
                            expected=_expected_output(sample, strength),
                            output=None,
                            error=f"{type(exc).__name__}: {exc}",
                        ),
                        args.json,
                    )

    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.passes < 1:
        parser.error("--passes must be at least 1")

    if args.offline:
        return _run_offline(args)
    return _run_live(args)


if __name__ == "__main__":
    raise SystemExit(main())
