# WordListMaker

Generate possible password lists from simple words — supports combining words, masks, case changes, leet substitutions, years/numbers/symbols, length & entropy filtering, streaming to file, and parallel execution (Threads/Processes).

> ⚠️ For ethical use only. This script is intended for authorized security testing (such as evaluating your own password strength or in a lab environment).

---

## Features

- Combine multiple words in different arrangements with multiple joiners.
- Flexible masks (e.g., `{base}{year}{sym}`, `{sym}{camel}{num}`).
- Case transformations: `original, lower, upper, title, invert`.
- Customizable leet substitutions with max expansion limit to avoid combinatorial explosion.
- Add numbers/symbols/years (ranges or `last:N`).
- Filters: min/max length, **Shannon entropy** minimum, blacklist.
- Streaming output to file (supports `.gz`) with max line limit.
- Parallel execution via Threads or Processes.
- Optional progress bar.

---

## Requirements

- Python 3.8+
- (Optional) `tqdm` for progress bar.

Install optional dependencies:
```bash
pip install tqdm
```

---

## Quick Start

```bash
python tool.py -w hello,world -s
```

Combines the words and generates multiple variations, printing them to the console.

Save to a compressed file:
```bash
python tool.py -w hello,world -o out.txt.gz -s
```

---

## Input Methods

- Single argument, comma-separated:
  ```bash
  python tool.py -w john,doe,acme -s
  ```
- From file:
  ```bash
  python tool.py --word-file words.txt -s
  ```
- From STDIN:
  ```bash
  cat words.txt | python tool.py -w - -s
  ```

> The script combines the words into one or more **base strings** using permutations and joiners, then applies masks and transformations.

---

## Key Options

- Display:
  - `-s, --show` Print results to console.
  - `-o, --output PATH` Save results to file (supports `.gz`).
  - `--force` Overwrite output file if it exists.
  - `--progress` Show progress bar (requires `tqdm`).

- Building strings:
  - `-w, --word` Base words (CSV) or `-` to read from STDIN.
  - `--word-file` File containing base words (one per line).
  - `--joiners` Join characters between words (CSV). Example: `"" ,-,_,.` (empty string = no joiner).
  - `--max-permutation-length` Max number of words to combine per candidate (default: all words).

- Masks:
  - `--mask` Can be repeated multiple times. Placeholders:
    - `{base}` Base word after transformations.
    - `{Base}` Capitalized.
    - `{BASE}` Uppercase.
    - `{camel}` CamelCase conversion (preserves non-alphanumeric separators).
    - `{num}` From numbers list.
    - `{sym}` From symbols list.
    - `{year}` From years list.

  Default masks if none specified:
  ```
  {base}{num}{sym}
  {base}{year}{sym}
  {sym}{base}{num}
  {camel}{num}
  {Base}{year}
  {BASE}{sym}{num}
  ```

- Numbers/Symbols/Years:
  - `--numbers "1,12,123,2025,007"` (empty element `""` means none).
  - `--symbols "!,@,#,$"` (empty element allowed).
  - `--years "2010-2015,last:3,2025"` ranges, `last:N`, or single years.

- Case & Leet:
  - `--cases "original,lower,upper,title,invert"`
  - `--leet "a=@,4;s=$,5;e=3;i=1;o=0"` Format: semicolon-separated; each `char=replacementCSV`.
  - `--leet-max-expansions 4` Cap expansions to prevent explosion.

- Filters & Limits:
  - `--min-length 4`, `--max-length 64`
  - `--min-entropy 0.0` Minimum Shannon entropy (0 to disable).
  - `--blacklist path.txt` Exclude passwords found in file.
  - `--max-count N` Stop after generating N results.

- Parallelism & Logging:
  - `-t, --threads 4` Number of workers.
  - `--processes` Use Processes instead of Threads (better for CPU-heavy rules).
  - `--log-level info|debug|warning|error`

---

## Examples

**1) Custom masks and joiners:**
```bash
python tool.py -w red,fox --joiners ",-,_"
  --mask "{base}{year}{sym}" --mask "{sym}{camel}{num}"
  --years "2018-2020,last:2" --numbers "1,12,123" --symbols "!,$"
  -s
```

**2) Length & entropy filter with max count:**
```bash
python tool.py -w brand,name --min-length 8 --max-length 16 --min-entropy 2.5 --max-count 500 -s
```

**3) Use processes for faster heavy workloads:**
```bash
python tool.py -w hello,world,test --processes -t 8 -o out.txt.gz --progress
```

**4) Exclude common passwords (Blacklist):**
```bash
python tool.py -w qwerty,admin --blacklist top1000.txt -s
```

---

## Tips

- More words/joiners/masks and substitutions = more output size.
  Use:
  - `--max-count` to limit,
  - `-o out.txt.gz` for compressed output,
  - `--processes -t N` to speed up,
  - `--progress` to monitor.

- Be cautious with memory: output is streamed, so prefer file output for large runs.

---

## CamelCase Output

- `{camel}` attempts to capitalize alphanumeric parts while keeping non-alphanumeric separators (like `_`, `-`, `.`):
  - `foo_bar` → `Foo_Bar`
  - `hello-world` → `Hello-World`

---

## Limits & Considerations

- Over-aggressive leet mappings can cause huge output. Adjust `--leet-max-expansions`.
- `--min-entropy` is a simple Shannon estimate and may not fully reflect real-world password strength.
- Output order may vary depending on word order, joiners, and parameters.

---

## License

Insert your license here (MIT/Apache-2.0/GPL…).

---

## Disclaimer

Use this script only for educational/testing purposes with explicit permission. Unauthorized use may violate laws.
