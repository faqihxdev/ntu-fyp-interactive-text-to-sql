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
