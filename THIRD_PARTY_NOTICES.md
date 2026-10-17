# Third-party sources

The clarification protocol and prompt adaptations build on
[Sphinteract](https://github.com/ZhaoFuheng/Sphinteract), pinned at commit
`21c6fcd`. The Mini-Interact adapter uses the released interface and evaluator from
[BIRD-Interact](https://github.com/bird-bench/BIRD-Interact).
Their MIT notices are reproduced below.

The original-BIRD execution comparison follows the
[released BIRD evaluator](https://github.com/AlibabaResearch/DAMO-ConvAI/blob/483554eae102996f5ec1f4feab4e78ef29c2a394/bird/llm/src/evaluation.py).
Dataset and source checksums are recorded in `experiment/manifests/`.
External datasets and evaluators must be obtained under their own terms.
Python and frontend dependencies retain their respective licences; their
versions are recorded in the lockfiles.

## BIRD records and replay data

`demonstrator/data/replay.json` includes ten selected BIRD Dev questions, table
definitions and derived SQL result previews, with saved model-generated trajectories
and prompt contexts added by Muhammad Faqih Akmal. Source: the
[BIRD authors' dataset](https://bird-bench.github.io/). The bundle is distributed
under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
Changes comprise selection, JSON packaging, added model records and cached
read-only query previews. Original record and prompt hashes are retained.
Sphinteract prompt portions retain the MIT notice below.

`experiment/results/bird-*.jsonl.gz` contains the complete retained task records
for the two BIRD studies, including public questions, generated SQL, simulated
clarification exchanges and measured outcomes. These derived records are also
distributed under CC BY-SA 4.0 with attribution to BIRD and Muhammad Faqih Akmal.
Compression preserves the original record bytes and their published hashes.

The Mini-Interact files in `experiment/results/` contain measured outcomes,
usage and a numerical event projection, with public identifiers and strata from
the [Mini-Interact authors](https://huggingface.co/datasets/birdsql/mini-interact).
They are distributed under CC BY-SA 4.0. The projection removes question/answer
text, SQL and prompts, and adds derived validation flags. The privately supplied
answer bundle and answer-bearing traces require separate permission.

## Sphinteract and BIRD-Interact: MIT License

Copyright (c) 2024 ZhaoFuheng (Sphinteract)

Copyright (c) 2024 bird_sql (BIRD-Interact)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
