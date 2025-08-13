#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import argparse
import gzip
import itertools
import logging
import math
import os
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

FileName = prog = os.path.basename(sys.argv[0])


def _maybe_tqdm(iterable, enable: bool, total: Optional[int] = None, desc: str = ""):
    if not enable:
        return iterable
    try:
        from tqdm import tqdm  # type: ignore
        return tqdm(iterable, total=total, desc=desc)
    except Exception:
        return iterable


class WordListMaker:
    """
    Generate password candidates by combining provided words, masks, case/leet variations,
    and suffix/prefix components (numbers/symbols/years), with streaming output,
    length & entropy filters, blacklist support, and (optional) parallelism.
    """

    # -------- Argument parsing --------
    def build_parser(self) -> argparse.ArgumentParser:
        DEFAULT_THREADS = 4
        DEFAULT_MIN_LENGTH = 4
        DEFAULT_MAX_LENGTH = 64

        epilog = fr"""
        Examples:
        # Basic: combine two words and print
        python {FileName} -w hello,world -s

        # Save (gz compressed if path ends with .gz)
        python {FileName} -w hello,world -o out.txt.gz

        # Use joiners and custom numbers/symbols
        python {FileName} -w john,doe --joiners "-,_,." --numbers "1,12,123,2025" --symbols "!,$" -s

        # Masks control structure; placeholders: {{base}} {{Base}} {{BASE}} {{camel}} {{num}} {{sym}} {{year}}
        python {FileName} -w red,fox --mask "{{base}}{{year}}{{sym}}" --mask "{{sym}}{{camel}}{{num}}" -s

        # Years as range / 'last:N'
        python {FileName} -w brand,name --years "2010-2015,last:3" -s

        # Filter by length & entropy (Shannon)
        python {FileName} -w hello,world --min-length 8 --max-length 16 --min-entropy 2.5 -s

        # Read words from file / stdin
        python {FileName} --word-file words.txt -s
        cat words.txt | python {FileName} -w - -s

        # Limit output and show progress
        python {FileName} -w a,b,c --max-count 1000 --progress -s

        # Parallel generation using processes (CPU-heavy rules)
        python {FileName} -w company,2025 --processes -t 8 -s
        """

        p = argparse.ArgumentParser(
            prog=os.path.basename(sys.argv[0]),

            formatter_class=argparse.RawDescriptionHelpFormatter,
            description=(
                "Generate password variations from simple words by combining them with masks, "
                "case/leet variants, numbers/symbols/years, and filters. Streams output to file/stdout."
            ),
            epilog=epilog,
        )

        # Inputs
        p.add_argument(
            "-w", "--word",
            help="Base word(s): comma-separated. Use '-' to read from STDIN (one word per line).",
        )
        p.add_argument(
            "--word-file",
            help="File with base words (one per line). Combined with -w if both are provided.",
        )

        # Output & display
        p.add_argument(
            "-o", "--output", help="Output file path. Use .gz to gzip. If omitted, no file is written.")
        p.add_argument("-s", "--show", action="store_true",
                       help="Print generated passwords to stdout.")
        p.add_argument("--force", action="store_true",
                       help="Overwrite output file if it exists without prompting.")
        p.add_argument("--progress", action="store_true",
                       help="Show a progress bar (requires tqdm if available).")

        # Generation controls
        p.add_argument(
            "--joiners",
            default=",-,_,.",
            help='Join characters between words as CSV. Use "" for no joiner, e.g. "",-,_  (default: ",-,_,.")',
        )
        p.add_argument(
            "--cases",
            default="original,lower,upper,title,invert",
            help="Case variants to apply as CSV (any of: original,lower,upper,title,invert).",
        )
        p.add_argument(
            "--numbers",
            default="1,12,123,2025,007",
            help='Numbers to try as CSV. Empty element "" is allowed to mean none.',
        )
        p.add_argument(
            "--symbols",
            default="!,@,#,$",
            help='Symbols to try as CSV. Empty element "" is allowed to mean none.',
        )
        p.add_argument(
            "--years",
            default="",
            help='Years set. CSV of items like "1990-1995", "2020", or "last:5". Empty = none.',
        )

        p.add_argument(
            "--mask",
            action="append",
            default=None,
            help=(
                "Mask/template using placeholders: {base} {Base} {BASE} {camel} {num} {sym} {year}. "
                "May be used multiple times. If omitted, a sensible default set is used."
            ),
        )

        # Permutation sizing
        p.add_argument(
            "--max-permutation-length",
            type=int,
            default=None,
            help="Max number of words to combine per candidate (default: all words).",
        )

        # Leet options
        p.add_argument(
            "--leet",
            default="a=@,4;s=$,5;e=3;i=1;o=0",
            help='Leet map as semicolon-separated items like "a=@,4;s=$,5;e=3;i=1;o=0". Empty to disable.',
        )
        p.add_argument(
            "--leet-max-expansions",
            type=int,
            default=4,
            help="Max number of leet-expanded variants per base to cap combinatorial explosion.",
        )

        # Filters & limits
        p.add_argument("--min-length", type=int,
                       default=DEFAULT_MIN_LENGTH, help="Minimum password length.")
        p.add_argument("--max-length", type=int,
                       default=DEFAULT_MAX_LENGTH, help="Maximum password length.")
        p.add_argument("--min-entropy", type=float, default=0.0,
                       help="Minimum Shannon entropy (0 to disable).")
        p.add_argument(
            "--blacklist", help="Path to blacklist file (one password per line) to exclude.")
        p.add_argument("--max-count", type=int, default=None,
                       help="Stop after generating this many lines.")

        # Parallelism
        p.add_argument("-t", "--threads", type=int,
                       default=DEFAULT_THREADS, help="Number of workers.")
        p.add_argument("--processes", action="store_true",
                       help="Use processes instead of threads.")

        # Logging
        p.add_argument(
            "--log-level",
            default="info",
            choices=["debug", "info", "warning", "error"],
            help="Logging verbosity (default: info).",
        )

        return p

    # -------- Entry point --------
    def __init__(self):
        self.args = self.build_parser().parse_args()
        self._configure_logging()

        # Load & normalize words
        words = self._collect_words()
        if not words:
            logging.error("No words provided. Use -w or --word-file.")
            sys.exit(1)

        words = [self._normalize(w) for w in words if w.strip()]
        # unique while preserving order
        seen: Set[str] = set()
        words = [w for w in words if not (w in seen or seen.add(w))]

        logging.info("Starting words: %s", words)
        if self.args.max_permutation_length:
            logging.info("Max permutation length: %d",
                         self.args.max_permutation_length)

        # Prepare components
        joiners = self._parse_csv_allow_empty(self.args.joiners)
        numbers = self._parse_csv_allow_empty(self.args.numbers)
        symbols = self._parse_csv_allow_empty(self.args.symbols)
        years = self._parse_years(self.args.years)
        cases = self._parse_cases(self.args.cases)
        leet_map = self._parse_leet(self.args.leet)
        masks = self.args.mask or [
            "{base}{num}{sym}",
            "{base}{year}{sym}",
            "{sym}{base}{num}",
            "{camel}{num}",
            "{Base}{year}",
            "{BASE}{sym}{num}",
        ]

        logging.debug("Joiners: %s", joiners)
        logging.debug("Numbers: %s", numbers)
        logging.debug("Symbols: %s", symbols)
        logging.debug("Years: %s", years)
        logging.debug("Cases: %s", cases)
        logging.debug("Masks: %s", masks)
        logging.debug("Leet map: %s", leet_map)

        # Build base combinations (permutations joined by each joiner)
        base_strings = self._make_base_combinations(
            words, joiners, self.args.max_permutation_length)

        # Prepare output
        out_fp = self._open_output(self.args.output, self.args.force)

        # Stream generation
        gen = self._stream_candidates(
            base_strings=base_strings,
            numbers=numbers,
            symbols=symbols,
            years=years,
            cases=cases,
            masks=masks,
            leet_map=leet_map,
            leet_max=self.args.leet_max_expansions,
        )

        # Wrap with progress if requested (unknown total)
        gen_iter = _maybe_tqdm(gen, self.args.progress, desc="Generating")

        # Write/print loop with filters
        count = 0
        blacklist = self._load_blacklist(self.args.blacklist)

        try:
            for pw in gen_iter:
                if not self._passes_filters(pw, self.args.min_length, self.args.max_length, self.args.min_entropy):
                    continue
                if blacklist and pw in blacklist:
                    continue

                if out_fp is not None:
                    out_fp.write((pw + "\n").encode("utf-8"))
                if self.args.show:
                    print(pw)

                count += 1
                if self.args.max_count and count >= self.args.max_count:
                    logging.info("Reached max-count=%d; stopping.",
                                 self.args.max_count)
                    break
        finally:
            if out_fp is not None:
                out_fp.close()

        logging.info("Done. Generated %d line(s).", count)

    # -------- Helpers: I/O & logging --------
    def _configure_logging(self):
        level = getattr(logging, self.args.log_level.upper(), logging.INFO)
        logging.basicConfig(level=level, format="[%(levelname)s] %(message)s")

    def _collect_words(self) -> List[str]:
        words: List[str] = []

        # -w / stdin
        if self.args.word:
            if self.args.word == "-":
                logging.info("Reading words from STDIN...")
                for line in sys.stdin:
                    line = line.strip()
                    if line:
                        words.append(line)
            else:
                words.extend([w.strip()
                             for w in self.args.word.split(",") if w.strip()])

        # --word-file
        if self.args.word_file:
            with open(self.args.word_file, "r", encoding="utf-8") as f:
                file_words = [ln.strip() for ln in f if ln.strip()]
                words.extend(file_words)

        return words

    def _open_output(self, path: Optional[str], force: bool):
        if not path:
            return None
        if os.path.exists(path) and not force:
            logging.error(
                "Output file exists: %s (use --force to overwrite)", path)
            sys.exit(2)

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if path.endswith(".gz"):
            logging.info("Writing gzip: %s", path)
            return gzip.open(path, "wb")
        else:
            logging.info("Writing: %s", path)
            return open(path, "wb")

    def _load_blacklist(self, path: Optional[str]) -> Optional[Set[str]]:
        if not path:
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return {ln.strip() for ln in f if ln.strip()}
        except Exception as e:
            logging.warning("Could not load blacklist '%s': %s", path, e)
            return None

    # -------- Helpers: parsing options --------
    @staticmethod
    def _normalize(s: str) -> str:
        return unicodedata.normalize("NFKC", s.strip())

    @staticmethod
    def _parse_csv_allow_empty(csv: str) -> List[str]:
        if csv is None:
            return [""]
        parts = [p for p in (x.strip()
                             for x in csv.split(",")) if p != "" or p == ""]
        # Preserve explicit empty entries "", but ensure at least [""] if user passed empty string
        return parts if parts else [""]

    @staticmethod
    def _parse_cases(csv: str) -> List[str]:
        valid = {"original", "lower", "upper", "title", "invert"}
        out: List[str] = []
        for token in (t.strip().lower() for t in csv.split(",")):
            if token in valid and token not in out:
                out.append(token)
        return out or ["original"]

    @staticmethod
    def _parse_years(spec: str) -> List[str]:
        if not spec:
            return [""]
        years: Set[int] = set()
        for token in (t.strip() for t in spec.split(",")):
            if not token:
                continue
            if token.startswith("last:"):
                try:
                    n = int(token.split(":", 1)[1])
                    from datetime import datetime

                    this_year = datetime.now().year
                    for y in range(this_year - n + 1, this_year + 1):
                        years.add(y)
                except Exception:
                    continue
            elif "-" in token:
                a, b = token.split("-", 1)
                try:
                    a, b = int(a), int(b)
                    if a > b:
                        a, b = b, a
                    for y in range(a, b + 1):
                        years.add(y)
                except Exception:
                    continue
            else:
                try:
                    years.add(int(token))
                except Exception:
                    continue
        ys = [str(y) for y in sorted(years)]
        return ys if ys else [""]

    @staticmethod
    def _parse_leet(spec: str) -> Dict[str, List[str]]:
        """
        "a=@,4;s=$,5;e=3;i=1;o=0" -> {"a": ["@", "4"], "s": ["$", "5"], ...}
        """
        mapping: Dict[str, List[str]] = {}
        if not spec:
            return mapping
        for chunk in (c for c in spec.split(";") if c.strip()):
            if "=" not in chunk:
                continue
            k, v = chunk.split("=", 1)
            k = k.strip()
            vals = [t for t in (x.strip() for x in v.split(",")) if t]
            if k and vals:
                mapping[k] = vals
        return mapping

    # -------- Base combination building --------
    def _make_base_combinations(
        self, words: List[str], joiners: List[str], max_len: Optional[int]
    ) -> List[str]:
        r_max = max_len or len(words)
        r_max = max(1, min(r_max, len(words)))
        bases: List[str] = []
        for r in range(1, r_max + 1):
            for perm in itertools.permutations(words, r):
                for j in joiners:
                    bases.append(j.join(perm))
        # dedupe preserving order
        seen: Set[str] = set()
        bases = [b for b in bases if not (b in seen or seen.add(b))]
        logging.info("Base combinations: %d", len(bases))
        return bases

    # -------- Case / leet expansions --------
    @staticmethod
    def _apply_case(s: str, mode: str) -> str:
        if mode == "lower":
            return s.lower()
        if mode == "upper":
            return s.upper()
        if mode == "title":
            return s.title()
        if mode == "invert":
            return "".join(ch.lower() if ch.isupper() else ch.upper() for ch in s)
        return s  # original

    def _expand_cases(self, base: str, cases: Sequence[str]) -> List[str]:
        out: List[str] = []
        seen: Set[str] = set()
        for c in cases:
            v = self._apply_case(base, c)
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out

    def _expand_leet(self, s: str, leet_map: Dict[str, List[str]], cap: int) -> List[str]:
        """
        Expand with leet replacements (per character mapping). We cap total variants to avoid explosion.
        Strategy: walk the string; for each char c with mapping, branch to alternatives; prune when exceeding cap.
        """
        if not leet_map:
            return [s]

        variants = [s]
        for i, ch in enumerate(s):
            lower = ch.lower()
            if lower in leet_map:
                new_variants = []
                repls = leet_map[lower]
                for variant in variants:
                    # keep original
                    new_variants.append(variant)
                    # replace this occurrence with each alternative
                    for rep in repls:
                        nv = variant[:i] + rep + variant[i + 1:]
                        new_variants.append(nv)
                # cap
                if len(new_variants) > cap:
                    new_variants = new_variants[:cap]
                variants = new_variants
            if len(variants) >= cap:
                break
        # dedupe
        seen: Set[str] = set()
        out: List[str] = []
        for v in variants:
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out[:cap]

    # -------- Mask application --------
    @staticmethod
    def _to_camel(s: str) -> str:
        parts = [p for p in re_split_keep_delims(s)]
        # Capitalize alnum chunks only
        out = []
        for p in parts:
            if p.isalnum():
                out.append(p[:1].upper() + p[1:].lower())
            else:
                out.append(p)
        return "".join(out)

    def _apply_masks(
        self,
        base: str,
        numbers: Sequence[str],
        symbols: Sequence[str],
        years: Sequence[str],
        masks: Sequence[str],
    ) -> Iterator[str]:
        Base = base[:1].upper() + base[1:] if base else base
        BASE = base.upper()
        camel = self._to_camel(base)

        for mask in masks:
            # choose sources for {num}, {sym}, {year}; empty entries included
            for num in numbers:
                for sym in symbols:
                    for year in years:
                        candidate = (
                            mask.replace("{base}", base)
                            .replace("{Base}", Base)
                            .replace("{BASE}", BASE)
                            .replace("{camel}", camel)
                            .replace("{num}", num)
                            .replace("{sym}", sym)
                            .replace("{year}", year)
                        )
                        yield candidate

    # -------- Streaming generation (with optional parallelism) --------
    def _stream_candidates(
        self,
        base_strings: List[str],
        numbers: Sequence[str],
        symbols: Sequence[str],
        years: Sequence[str],
        cases: Sequence[str],
        masks: Sequence[str],
        leet_map: Dict[str, List[str]],
        leet_max: int,
    ) -> Iterator[str]:
        """
        Stream all candidates. If --threads/processes > 1, bases are split among workers and results yielded.
        """
        workers = max(1, int(self.args.threads or 1))
        if workers == 1:
            yield from self._generate_for_chunk(
                base_strings, numbers, symbols, years, cases, masks, leet_map, leet_max
            )
            return

        # chunk base_strings
        chunks = self._chunk_list(base_strings, workers)
        Executor = ProcessPoolExecutor if self.args.processes else ThreadPoolExecutor

        # Serialize leet_map etc. for processes
        with Executor(max_workers=workers) as ex:
            futures = [
                ex.submit(
                    generate_chunk_static,
                    chunk,
                    numbers,
                    symbols,
                    years,
                    list(cases),
                    list(masks),
                    dict(leet_map),
                    int(leet_max),
                )
                for chunk in chunks
            ]
            for fut in as_completed(futures):
                for pw in fut.result():
                    yield pw

    @staticmethod
    def _chunk_list(items: List[str], n: int) -> List[List[str]]:
        k = max(1, n)
        size = max(1, (len(items) + k - 1) // k)
        return [items[i: i + size] for i in range(0, len(items), size)]

    def _generate_for_chunk(
        self,
        bases: List[str],
        numbers: Sequence[str],
        symbols: Sequence[str],
        years: Sequence[str],
        cases: Sequence[str],
        masks: Sequence[str],
        leet_map: Dict[str, List[str]],
        leet_max: int,
    ) -> Iterator[str]:
        seen: Set[str] = set()
        for base in bases:
            # Case variants
            for cased in self._expand_cases(base, cases):
                # Leet variants (capped)
                for leet in self._expand_leet(cased, leet_map, cap=max(1, leet_max)):
                    for candidate in self._apply_masks(leet, numbers, symbols, years, masks):
                        if candidate not in seen:
                            seen.add(candidate)
                            yield candidate

    # -------- Filters --------
    @staticmethod
    def _passes_filters(s: str, min_len: int, max_len: int, min_entropy: float) -> bool:
        if min_len > max_len:
            min_len, max_len = max_len, min_len
        n = len(s)
        if n < min_len or n > max_len:
            return False
        if min_entropy > 0.0:
            if _shannon_entropy(s) < min_entropy:
                return False
        return True


# -------- Standalone helpers for process targets --------
def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    from collections import Counter

    counts = Counter(s)
    n = len(s)
    ent = 0.0
    for c in counts.values():
        p = c / n
        ent -= p * math.log2(p)
    return ent


def generate_chunk_static(
    bases: List[str],
    numbers: Sequence[str],
    symbols: Sequence[str],
    years: Sequence[str],
    cases: Sequence[str],
    masks: Sequence[str],
    leet_map: Dict[str, List[str]],
    leet_max: int,
) -> List[str]:
    w = WordListMaker  # type alias for methods
    # Minimal reimplementation of needed instance methods (static-friendly)

    def apply_case(s: str, mode: str) -> str:
        if mode == "lower":
            return s.lower()
        if mode == "upper":
            return s.upper()
        if mode == "title":
            return s.title()
        if mode == "invert":
            return "".join(ch.lower() if ch.isupper() else ch.upper() for ch in s)
        return s

    def expand_cases(base: str, cs: Sequence[str]) -> List[str]:
        out: List[str] = []
        seen: Set[str] = set()
        for c in cs:
            v = apply_case(base, c)
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out

    def expand_leet(s: str, leet_map_: Dict[str, List[str]], cap: int) -> List[str]:
        if not leet_map_:
            return [s]
        variants = [s]
        for i, ch in enumerate(s):
            lower = ch.lower()
            if lower in leet_map_:
                new_variants = []
                repls = leet_map_[lower]
                for variant in variants:
                    new_variants.append(variant)
                    for rep in repls:
                        nv = variant[:i] + rep + variant[i + 1:]
                        new_variants.append(nv)
                if len(new_variants) > cap:
                    new_variants = new_variants[:cap]
                variants = new_variants
            if len(variants) >= cap:
                break
        seen: Set[str] = set()
        out: List[str] = []
        for v in variants:
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out[:cap]

    def to_camel(s: str) -> str:
        parts = [p for p in re_split_keep_delims(s)]
        out = []
        for p in parts:
            if p.isalnum():
                out.append(p[:1].upper() + p[1:].lower())
            else:
                out.append(p)
        return "".join(out)

    def apply_masks(base: str) -> Iterator[str]:
        Base = base[:1].upper() + base[1:] if base else base
        BASE = base.upper()
        camel = to_camel(base)
        for mask in masks:
            for num in numbers:
                for sym in symbols:
                    for year in years:
                        yield (
                            mask.replace("{base}", base)
                            .replace("{Base}", Base)
                            .replace("{BASE}", BASE)
                            .replace("{camel}", camel)
                            .replace("{num}", num)
                            .replace("{sym}", sym)
                            .replace("{year}", year)
                        )

    seen_all: Set[str] = set()
    out_all: List[str] = []
    for base in bases:
        for cased in expand_cases(base, cases):
            for leet in expand_leet(cased, leet_map, cap=max(1, leet_max)):
                for cand in apply_masks(leet):
                    if cand not in seen_all:
                        seen_all.add(cand)
                        out_all.append(cand)
    return out_all


# -------- Small regex utility used by camel-case conversion --------
_SPLIT_DELIMS = re.compile(r"([^\w]+)", flags=re.UNICODE)


def re_split_keep_delims(s: str) -> List[str]:
    """
    Split string into alnum and non-alnum chunks, keeping delimiters.
    """
    if not s:
        return [s]
    parts = _SPLIT_DELIMS.split(s)
    return [p for p in parts if p is not None and p != ""]


# -------- Main --------
if __name__ == "__main__":
    WordListMaker()
