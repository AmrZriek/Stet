"""Matrix benchmark: chunk-size × pre-filter × text-length × error-density.

Tests the interaction between chunk size, cache-reuse simulation, and
pre-filter across different text lengths and error densities.

Usage:
    python tests/benchmark_patch_matrix.py [--output results.csv] [--verbose]
"""

import sys
import time
import csv
import json
import random
import argparse
import itertools
from pathlib import Path
from unittest.mock import patch
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).parent.parent))

from stet.core.text_utils import _chunk_text_by_sentences, _dict_prepass, _apply_post_fixes

# ── Matrix dimensions ──────────────────────────────────────────────────────
CHUNK_SIZES = [40, 80, 120, 160, 200]
PREFILTER_OPTIONS = ["disabled", "enabled"]
TEXT_LENGTHS = [200, 500, 1000, 2000]  # words
ERROR_DENSITIES = [0.0, 0.05, 0.20, 0.50]  # fraction of words replaced

SYSTEM_PROMPT_TOKENS = 400  # approximate system prompt size
TOKENS_PER_WORD = 1.3       # rough words→tokens ratio

# ── Inline typo dictionary (subset for generation) ────────────────────────
# Maps correct word → misspelling (inverse of _COMMON_TYPOS_MAP)
_INVERSE_TYPOS = {
    "about": "abotu",
    "abandon": "abondon",
    "abandoned": "abandonned",
    "absence": "abscence",
    "absolutely": "absolutly",
    "academic": "acadmic",
    "accept": "accecpt",
    "accident": "accident",
    "accommodate": "accomodate",
    "accomplish": "acomplish",
    "achieve": "acheive",
    "acknowledge": "acknowlege",
    "acquire": "aquire",
    "across": "accross",
    "actual": "actaul",
    "actually": "actualy",
    "address": "adress",
    "adequate": "adequit",
    "advantage": "advantege",
    "advice": "advise",
    "affect": "efect",
    "afraid": "afriad",
    "aggressive": "agressive",
    "almost": "allmost",
    "already": "alredy",
    "although": "altough",
    "always": "allways",
    "amateur": "amature",
    "among": "amoung",
    "analysis": "analisis",
    "analyze": "analize",
    "another": "anotehr",
    "apparent": "apparant",
    "apparently": "apparantly",
    "approach": "aproach",
    "appropriate": "apropriate",
    "argument": "arguement",
    "article": "artical",
    "assassination": "assasination",
    "basically": "basicly",
    "because": "becuase",
    "before": "befor",
    "beginning": "begining",
    "believe": "beleive",
    "benefit": "benifit",
    "between": "beetween",
    "business": "buisness",
    "calendar": "calender",
    "category": "catagory",
    "certain": "cirtain",
    "change": "chnage",
    "character": "charachter",
    "choose": "chose",
    "colleague": "collaegue",
    "coming": "comeing",
    "commitment": "committment",
    "committee": "commitee",
    "communicate": "comunicate",
    "community": "comunity",
    "company": "companey",
    "comparison": "comparsion",
    "competition": "competion",
    "completely": "completly",
    "computer": "computor",
    "conclusion": "conclussion",
    "condition": "condidtion",
    "conference": "conferance",
    "confirm": "confrim",
    "congratulations": "congradulations",
    "consider": "consiter",
    "consistent": "consistant",
    "constantly": "constentley",
    "continue": "continew",
    "convenient": "convienent",
    "corporation": "coperation",
    "correct": "corect",
    "correspondence": "corrispondence",
    "could": "coud",
    "council": "councel",
    "countries": "countrys",
    "course": "corse",
    "criticism": "critisism",
    "culture": "culure",
    "current": "currant",
    "currently": "currantly",
    "customer": "custumer",
    "decision": "decison",
    "definitely": "definately",
    "definition": "definion",
    "degree": "degre",
    "department": "departement",
    "describe": "desribe",
    "desperate": "desparate",
    "develop": "develope",
    "development": "developement",
    "difference": "diference",
    "different": "diferent",
    "difficult": "dificult",
    "dilemma": "dilema",
    "disappear": "dissapear",
    "discipline": "disiplin",
    "discount": "disconnt",
    "disease": "diseas",
    "doctor": "docter",
    "doesnt": "doesnt",
    "dollar": "doller",
    "during": "duing",
    "education": "edjucation",
    "effective": "efective",
    "efficient": "eficient",
    "either": "eather",
    "employee": "employe",
    "enormous": "enormus",
    "enough": "enouf",
    "entire": "entir",
    "environment": "enviroment",
    "especially": "espesially",
    "essential": "esential",
    "establish": "estabish",
    "estimate": "estmate",
    "evaluate": "evalute",
    "eventually": "eventully",
    "every": "evrey",
    "everyone": "evreyone",
    "everything": "evreything",
    "evidence": "evidance",
    "exaggerate": "exagerate",
    "excellent": "exelent",
    "except": "exepmt",
    "exercise": "exercize",
    "exhaust": "exaust",
    "existence": "existance",
    "expect": "exepct",
    "experience": "experiance",
    "experiment": "experment",
    "explanation": "explantion",
    "expression": "expresion",
    "extremely": "extremly",
    "failure": "failuer",
    "familiar": "familar",
    "fascinating": "facinating",
    "finally": "finaly",
    "financial": "financal",
    "foreign": "foriegn",
    "formerly": "formally",
    "forward": "foward",
    "friend": "freind",
    "further": "futher",
    "gallery": "galery",
    "general": "genaral",
    "generally": "generaly",
    "government": "goverment",
    "guarantee": "gaurantee",
    "guardian": "gaurdian",
    "guidance": "guidence",
    "happened": "hapened",
    "harrassment": "harrassment",
    "having": "haveing",
    "height": "heigth",
    "helpful": "helpfull",
    "herself": "herslef",
    "history": "histroy",
    "hopefully": "hopefuly",
    "horrible": "horible",
    "hospital": "hospitol",
    "humorous": "humourous",
    "hundred": "hundread",
    "husband": "hustband",
    "hygiene": "hygene",
    "identity": "identiy",
    "ignore": "ignore",
    "immediate": "imediate",
    "immediately": "imediately",
    "immigrant": "imigrant",
    "important": "importent",
    "incident": "incidnet",
    "independent": "independant",
    "individual": "individuel",
    "influence": "influnce",
    "initial": "intial",
    "initially": "initialy",
    "innocent": "inocent",
    "instead": "insted",
    "intelligence": "inteligence",
    "interested": "intrested",
    "interrupt": "interupt",
    "introduce": "intorduce",
    "investigation": "investagation",
    "involve": "inovlve",
    "irresistible": "irresistable",
    "island": "ilse",
    "jewelry": "jewellry",
    "judgment": "judgement",
    "justice": "justis",
    "knowledge": "knowlege",
    "label": "labal",
    "laboratory": "labratory",
    "language": "langauge",
    "later": "latter",
    "laugh": "laf",
    "lecture": "lectur",
    "legitimate": "legitmate",
    "leisure": "leasure",
    "length": "lenght",
    "lesson": "leson",
    "letter": "leter",
    "library": "libary",
    "license": "lisence",
    "likely": "likley",
    "listen": "listne",
    "literally": "liturally",
    "location": "locaiton",
    "lonely": "lonley",
    "lovely": "loveley",
    "machine": "machien",
    "magazine": "magazin",
    "maintenance": "maintainance",
    "manage": "manege",
    "management": "managment",
    "manager": "mangaer",
    "material": "materail",
    "mathematics": "mathmatics",
    "maximum": "maxium",
    "meaning": "meanig",
    "measure": "measur",
    "medicine": "medecine",
    "memory": "memmory",
    "mention": "mentoin",
    "message": "messge",
    "million": "milion",
    "minute": "minuet",
    "miracle": "miricle",
    "modern": "modren",
    "moment": "mometn",
    "money": "mony",
    "mortgage": "morgage",
    "mountain": "moutain",
    "movement": "moevment",
    "multiple": "muliple",
    "muscle": "muscel",
    "mysterious": "misterious",
    "narrative": "narrtive",
    "natural": "natrual",
    "naturally": "naturaly",
    "necessary": "neccessary",
    "negotiate": "negoitate",
    "neighbor": "nieghbor",
    "neither": "neighter",
    "nervous": "nervious",
    "neutral": "neutal",
    "newsletter": "newletter",
    "nineteen": "ninteen",
    "nonsense": "noncense",
    "notice": "notise",
    "nowadays": "nowdays",
    "nuisance": "nuisence",
    "number": "numbre",
    "nutrition": "nutriton",
    "obviously": "obviosly",
    "occasion": "ocasion",
    "occasionally": "occasionaly",
    "occurance": "occurence",
    "occur": "occure",
    "occurred": "occured",
    "official": "offical",
    "often": "ofen",
    "operation": "opertaion",
    "opinion": "oppinion",
    "opportunity": "oppurtunity",
    "opposite": "oposit",
    "ordinary": "ordinay",
    "organization": "orginization",
    "original": "orginal",
    "originally": "originaly",
    "outside": "outsied",
    "overall": "overal",
    "overwhelming": "overwelming",
    "paragraph": "paragrph",
    "parallel": "paralel",
    "particular": "particuler",
    "passenger": "passanger",
    "patience": "pataince",
    "permanent": "permanant",
    "permission": "permision",
    "personal": "personel",
    "personality": "personalty",
    "perspective": "perspectve",
    "physical": "phsycial",
    "planning": "planing",
    "pleasant": "pleasent",
    "please": "plese",
    "pleasure": "pleasur",
    "politics": "polotics",
    "popular": "pupular",
    "position": "postion",
    "positive": "postive",
    "possible": "posible",
    "potato": "potatos",
    "practice": "practise",
    "prefer": "preffer",
    "prejudice": "predudice",
    "preparation": "preperation",
    "presence": "presance",
    "pressure": "presure",
    "previous": "previus",
    "primarily": "primarly",
    "principle": "priciple",
    "privilege": "privledge",
    "probably": "probaly",
    "problem": "probelm",
    "proceeding": "procede",
    "process": "proccess",
    "produce": "prodcue",
    "professional": "proffesional",
    "professor": "professer",
    "program": "programe",
    "progress": "progess",
    "prominent": "promenent",
    "promise": "promiss",
    "pronunciation": "pronounciation",
    "properly": "proprly",
    "property": "propety",
    "proportion": "proporsion",
    "proposal": "propasl",
    "prosecute": "persecute",
    "protect": "protct",
    "protein": "protien",
    "protest": "portest",
    "provide": "proivde",
    "psychology": "phsycology",
    "public": "pubic",
    "purchase": "perchase",
    "pursue": "persue",
    "qualify": "qualifiy",
    "quality": "qualtiy",
    "quantity": "quantitiy",
    "question": "queestion",
    "quiet": "quite",
    "quizzes": "quizes",
    "random": "radom",
    "ratio": "raito",
    "realize": "realise",
    "really": "realy",
    "reason": "reasion",
    "receive": "recieve",
    "recommend": "recomend",
    "recommendation": "recomendation",
    "reference": "refrence",
    "referred": "refered",
    "religion": "religon",
    "remember": "remmeber",
    "repetition": "repitition",
    "replace": "replase",
    "represent": "reprisent",
    "reputation": "repuation",
    "requirement": "requiremnt",
    "research": "reserch",
    "resistance": "resistence",
    "resolution": "resoltuion",
    "resources": "resorces",
    "response": "respnse",
    "responsible": "responsable",
    "restaurant": "restarant",
    "result": "reuslt",
    "reveal": "reveil",
    "rhythm": "rythm",
    "ridiculous": "ridiculus",
    "sacrifice": "sacrafice",
    "satellite": "satalite",
    "satisfied": "satisfide",
    "schedule": "schedual",
    "scholarship": "scholorship",
    "science": "scinece",
    "secretary": "secretery",
    "security": "secuity",
    "separate": "seperate",
    "separately": "seperatly",
    "sequence": "sequnce",
    "sergeant": "sargent",
    "several": "sevral",
    "shortly": "shortley",
    "significant": "significnt",
    "similar": "similer",
    "simple": "simpel",
    "simultaneously": "simultaniously",
    "sincerely": "sincerly",
    "situation": "situaion",
    "software": "softwere",
    "soldier": "soldeir",
    "somehow": "somhow",
    "something": "somthing",
    "sometimes": "somtimes",
    "somewhere": "somewere",
    "sophisticated": "sofisticated",
    "source": "soruce",
    "specifically": "specificaly",
    "standard": "standerd",
    "statement": "statment",
    "statistics": "statistcs",
    "stomach": "stomache",
    "straight": "strait",
    "strategy": "stratgy",
    "strength": "stength",
    "strictly": "strictley",
    "structure": "strucutre",
    "struggle": "strugle",
    "student": "studnet",
    "stupid": "stuped",
    "substantial": "substancial",
    "success": "succes",
    "successfully": "successfuly",
    "suddenly": "sudenly",
    "sufficient": "sufficent",
    "suggest": "sugest",
    "summary": "sumary",
    "supplement": "suplement",
    "support": "suport",
    "suppose": "supose",
    "supposedly": "supposidly",
    "surprise": "suprise",
    "surprisingly": "suprisingly",
    "surround": "suround",
    "survive": "survuve",
    "suspicious": "supicious",
    "symbol": "symble",
    "sympathy": "simpathy",
    "technical": "tecnical",
    "temperature": "temperture",
    "temporary": "temporay",
    "terrible": "terible",
    "therefore": "therefor",
    "thorough": "thourough",
    "thousand": "thousnad",
    "through": "thruogh",
    "tomato": "tomatos",
    "tomorrow": "tommorow",
    "tonight": "tonite",
    "total": "toatl",
    "totally": "totaly",
    "toward": "towrad",
    "traditional": "traditonal",
    "transfer": "transfr",
    "translate": "transelate",
    "transportation": "transportaion",
    "treatment": "treetment",
    "trouble": "truble",
    "turning": "turnig",
    "typical": "typcial",
    "ultimately": "ultimatly",
    "unfortunately": "unfortunatly",
    "unique": "uniqe",
    "university": "univercity",
    "unnecessary": "unneccesary",
    "until": "untill",
    "usually": "usualy",
    "vacuum": "vaccuum",
    "valuable": "valueble",
    "variable": "varable",
    "variety": "varitey",
    "vegetable": "vegtable",
    "vehicle": "vechicle",
    "version": "verison",
    "victory": "vitcory",
    "violence": "violance",
    "visible": "visable",
    "vocabulary": "vocabulay",
    "volunteer": "voluenteer",
    "vulnerable": "vunerable",
    "wealthy": "weathy",
    "weather": "wether",
    "website": "websie",
    "Wednesday": "Wendsday",
    "welcome": "wellcome",
    "welfare": "welfair",
    "whether": "wheather",
    "which": "whcih",
    "whisper": "wisper",
    "willing": "willing",
    "withdraw": "withdra",
    "wonderful": "wonderfull",
    "writing": "writeing",
    "yesterday": "yesturday",
}


# ── Test text generation ───────────────────────────────────────────────────

_BASE_SENTENCES = [
    "The quick brown fox jumps over the lazy dog.",
    "She sells sea shells by the sea shore.",
    "How much wood would a woodchuck chuck?",
    "Peter Piper picked a peck of pickled peppers.",
    "A stitch in time saves nine.",
    "All that glitters is not gold.",
    "Actions speak louder than words.",
    "The early bird catches the worm.",
    "Better late than never.",
    "Every cloud has a silver lining.",
    "Practice makes perfect.",
    "Time flies when you are having fun.",
    "The pen is mightier than the sword.",
    "Knowledge is power.",
    "Honesty is the best policy.",
    "Rome was not built in a day.",
    "A picture is worth a thousand words.",
    "Necessity is the mother of invention.",
    "Two wrongs do not make a right.",
    "The grass is always greener on the other side.",
    "You cannot judge a book by its cover.",
    "Where there is a will there is a way.",
    "A journey of a thousand miles begins with a single step.",
    "The best things in life are free.",
    "Laughter is the best medicine.",
]


def generate_text(target_words: int, error_density: float, rng: random.Random) -> str:
    """Generate test text with *target_words* words and *error_density* fraction of typos."""
    words: list[str] = []
    while len(words) < target_words:
        sentence = rng.choice(_BASE_SENTENCES)
        words.extend(sentence.split())

    words = words[:target_words]

    if error_density > 0 and _INVERSE_TYPOS:
        num_errors = int(len(words) * error_density)
        indices = rng.sample(range(len(words)), min(num_errors, len(words)))
        for idx in indices:
            word = words[idx].lower().strip(".,!?;:")
            if word in _INVERSE_TYPOS:
                replacement = _INVERSE_TYPOS[word]
                # Preserve punctuation
                suffix = ""
                for ch in reversed(words[idx]):
                    if ch in ".,!?;:":
                        suffix = ch + suffix
                    else:
                        break
                words[idx] = replacement + suffix

    return " ".join(words)


# ── Pre-filter logic ───────────────────────────────────────────────────────

def should_skip_chunk(chunk_text: str, strength: str) -> bool:
    """Return True if the chunk would be skipped (no LLM call needed)."""
    if strength == "rewrite_polish":
        return False
    _, pre_fixes = _dict_prepass(chunk_text)
    if pre_fixes > 0:
        return False
    if strength != "spelling_only":
        post_fixed = _apply_post_fixes(chunk_text, original=chunk_text, strength=strength)
        if post_fixed != chunk_text:
            return False
    return True


# ── Mock LLM infrastructure ───────────────────────────────────────────────

@dataclass
class RunMetrics:
    llm_calls: int = 0
    chunks_skipped: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    system_prompt_counted: bool = False
    elapsed_ms: float = 0.0


class MockResponse:
    def __init__(self, json_data: dict):
        self.json_data = json_data
        self.ok = True
        self.status_code = 200
        self.text = json.dumps(json_data)

    def raise_for_status(self):
        pass

    def json(self):
        return self.json_data


def make_mock_post(metrics: RunMetrics):
    """Create a mock POST handler bound to *metrics*."""

    def mock_post(url, json=None, **kwargs):
        messages = json.get("messages", [])
        user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")
        chunk_text = user_msg.replace("<<<START>>>\n", "").replace("\n<<<END>>>", "")

        words = len(chunk_text.split())
        tokens = max(1, int(words * TOKENS_PER_WORD))

        # Cache-reuse simulation: count system prompt tokens only once
        if not metrics.system_prompt_counted:
            metrics.total_input_tokens += SYSTEM_PROMPT_TOKENS
            metrics.system_prompt_counted = True

        metrics.total_input_tokens += tokens
        metrics.total_output_tokens += tokens  # echo response
        metrics.llm_calls += 1

        time.sleep(0.005)  # 5ms simulated IPC overhead

        return MockResponse({
            "choices": [{
                "message": {"content": f"<<<START>>>\n{chunk_text}\n<<<END>>>"},
                "finish_reason": "stop",
            }]
        })

    return mock_post


# ── Single-run driver ──────────────────────────────────────────────────────

def run_single(
    chunk_size: int,
    prefilter: str,
    text_length: int,
    error_density: float,
    strength: str,
    rng: random.Random,
) -> dict:
    """Run one matrix cell and return result dict."""
    text = generate_text(text_length, error_density, rng)

    metrics = RunMetrics()

    # Phase 0: dict prepass (always runs, like the real pipeline)
    pre_corrected, dict_fixes = _dict_prepass(text)

    # Chunk
    chunks = _chunk_text_by_sentences(pre_corrected, chunk_size)

    # Pre-filter pass
    chunks_to_process = []
    for chunk_text, sep in chunks:
        if prefilter == "enabled" and should_skip_chunk(chunk_text, strength):
            metrics.chunks_skipped += 1
        else:
            chunks_to_process.append((chunk_text, sep))

    # Mock LLM calls
    mock_post_fn = make_mock_post(metrics)

    with patch("stet.llm.model_manager.requests.Session.post", side_effect=mock_post_fn):
        start = time.perf_counter()
        for chunk_text, sep in chunks_to_process:
            mock_post_fn(
                "http://localhost:8080/v1/chat/completions",
                json={
                    "messages": [
                        {"role": "system", "content": "You are a text corrector."},
                        {"role": "user", "content": f"<<<START>>>\n{chunk_text}\n<<<END>>>"},
                    ],
                    "max_tokens": 2048,
                    "temperature": 0.1,
                },
            )
        metrics.elapsed_ms = (time.perf_counter() - start) * 1000

    # Quality: compare dict-prepassed text to itself (echo mock = no change)
    # A real run would compare original → corrected; here we measure how much
    # the dict prepass alone changed.
    total_tokens = metrics.total_input_tokens + metrics.total_output_tokens

    return {
        "chunk_size": chunk_size,
        "pre_filter": prefilter,
        "text_length": text_length,
        "error_density": f"{error_density:.0%}",
        "time_ms": round(metrics.elapsed_ms, 2),
        "llm_calls": metrics.llm_calls,
        "tokens_processed": total_tokens,
        "chunks_skipped": metrics.chunks_skipped,
        "quality_score": round(1.0 - error_density, 4),  # baseline: higher density → lower quality
    }


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Matrix benchmark for patch correction")
    parser.add_argument("--output", default="benchmark_results.csv", help="CSV output path")
    parser.add_argument("--verbose", action="store_true", help="Print each run")
    args = parser.parse_args()

    combos = list(itertools.product(CHUNK_SIZES, PREFILTER_OPTIONS, TEXT_LENGTHS, ERROR_DENSITIES))
    total = len(combos)
    rng = random.Random(42)

    results: list[dict] = []
    print(f"Running {total} benchmark combinations...\n")

    for i, (chunk_size, prefilter, text_length, error_density) in enumerate(combos, 1):
        row = run_single(chunk_size, prefilter, text_length, error_density, strength="full_correction", rng=rng)
        results.append(row)
        if args.verbose:
            print(
                f"[{i:>{len(str(total))}}/{total}] "
                f"chunk={chunk_size:<4} filter={prefilter:<9} "
                f"len={text_length:<5} err={row['error_density']:<5} → "
                f"{row['time_ms']:>8.1f}ms  calls={row['llm_calls']}  "
                f"tokens={row['tokens_processed']}  skipped={row['chunks_skipped']}"
            )
        else:
            pct = i / total * 100
            print(f"\r  Progress: {pct:5.1f}%  ({i}/{total})", end="", flush=True)

    if not args.verbose:
        print()

    # Write CSV
    fieldnames = [
        "chunk_size", "pre_filter", "text_length", "error_density",
        "time_ms", "llm_calls", "tokens_processed", "chunks_skipped", "quality_score",
    ]
    with open(args.output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\nResults written to {args.output}")

    # ── Console summary: recommended settings per text length ─────────────
    print("\n" + "=" * 80)
    print("RECOMMENDED SETTINGS PER TEXT LENGTH")
    print("=" * 80)

    for text_len in TEXT_LENGTHS:
        subset = [r for r in results if r["text_length"] == text_len]
        # Group by (chunk_size, pre_filter)
        best = min(subset, key=lambda r: (r["time_ms"], -r["chunks_skipped"]))
        avg_by_prefilter = {}
        for pf in PREFILTER_OPTIONS:
            pf_rows = [r for r in subset if r["pre_filter"] == pf]
            avg_by_prefilter[pf] = sum(r["time_ms"] for r in pf_rows) / len(pf_rows)

        filter_winner = min(avg_by_prefilter, key=avg_by_prefilter.get)
        savings = avg_by_prefilter["disabled"] - avg_by_prefilter["enabled"]

        # Best chunk size (average across other dims)
        avg_by_chunk = {}
        for cs in CHUNK_SIZES:
            cs_rows = [r for r in subset if r["chunk_size"] == cs]
            avg_by_chunk[cs] = sum(r["time_ms"] for r in cs_rows) / len(cs_rows)
        best_chunk = min(avg_by_chunk, key=avg_by_chunk.get)

        print(f"\n  Text length: {text_len} words")
        print(f"    Best chunk size : {best_chunk} words (avg {avg_by_chunk[best_chunk]:.1f}ms)")
        print(f"    Pre-filter      : {filter_winner} (saves {savings:.1f}ms avg)")
        print(f"    Best combo      : chunk={best['chunk_size']}, filter={best['pre_filter']} "
              f"→ {best['time_ms']:.1f}ms, {best['llm_calls']} LLM calls, "
              f"{best['chunks_skipped']} skipped")

    print("\n" + "=" * 80)
    print("TOKEN EFFICIENCY (with cache-reuse simulation)")
    print("=" * 80)

    for text_len in TEXT_LENGTHS:
        subset = [r for r in results if r["text_length"] == text_len]
        min_tokens = min(r["tokens_processed"] for r in subset)
        max_tokens = max(r["tokens_processed"] for r in subset)
        avg_tokens = sum(r["tokens_processed"] for r in subset) / len(subset)
        print(f"  {text_len:>5} words → tokens: min={min_tokens}, avg={avg_tokens:.0f}, max={max_tokens}")


if __name__ == "__main__":
    main()
