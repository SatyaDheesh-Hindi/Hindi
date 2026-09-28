## Hindi missing-words diagnosis

| setup | llama-cpp-python | round trip whole/pieces/special=True (of 13) | copy exact | words lost in copy | rewrite has 'डिपार्टमेंट/विभाग' | 'और ऑफ जस्टिस' gap | s |
|---|---|---|---|---|---|---|---|
| A unsloth Q4_K_M | 0.3.35 | 11/11/11 | ❌ | डिपार्टमेंट, मुख्यमंत्री, प्रतिनिधियों, पदाधिकारियों, व्यापारियों | ❌ | ❌ yes | 218.8 |
| A unsloth Q4_K_M + HF tokenizer | 0.3.35 | 11/11/11 | ❌ | n/a | ❌ | ✅ no | - |

### A unsloth Q4_K_M — `unsloth/gemma-4-12b-it-GGUF/gemma-4-12b-it-Q4_K_M.gguf`
- copy output: कर्नाटक के डी.के. शिवकुमार ने जन और नौकरशाहों से मुलाकात की, जबकि ऑफ जस्टिस और सोसाइटी के ने से बात की।
- rewrite: **जज ने ट्रंप और DOJ के सेटलमेंट को रद्द किया, 1.8 बिलियन फंड को बताया गैरकानूनी** — सोमवार को अमेरिकी डिस्ट्रिक्ट जज कैथलीन विलियम्स ने डोनाल्ड ट्रंप और ऑफ जस्टिस (DOJ) के बीच हुए एक सेटलमेंट को रद्द कर दिया। जज ने इसे गैरकानूनी सेल्फ-डीलिंग बताया। ट्रंप ने टैक्स रिटर्न लीक होने पर IRS पर 10 बिलियन डॉलर का केस किया था। इसके बाद DOJ ने टैक्सपेयर्स से 1.8 बिलियन डॉलर निकालकर एक एंटी-वेपनिज़ेशन फंड में डालने पर सहमति जताई थी।
- round-trip fail: `मुख्यमंत्री` whole→`` pieces→``
- round-trip fail: `कर्नाटक के मुख्यमंत्री डी.के. शिवकुमार न` whole→`कर्नाटक के डी.के. शिवकुमार ने जन और नौकरशाहों से मुलाकात की, जबकि ऑफ जस्टिस और स` pieces→`कर्नाटक के डी.के. शिवकुमार ने जन और नौकरशाहों से मुलाकात की, जबकि ऑफ जस्टिस और स`
- token IDs llama.cpp == HF: 13/13; HF decodes llama.cpp's IDs correctly: 13/13; HF round trip: 13/13

### A unsloth Q4_K_M — `unsloth/gemma-4-12b-it-GGUF/gemma-4-12b-it-Q4_K_M.gguf`
- copy output: n/a
- rewrite: **n/a** — n/a
- round-trip fail: `मुख्यमंत्री` whole→`` pieces→``
- round-trip fail: `कर्नाटक के मुख्यमंत्री डी.के. शिवकुमार न` whole→`कर्नाटक के डी.के. शिवकुमार ने जन और नौकरशाहों से मुलाकात की, जबकि ऑफ जस्टिस और स` pieces→`कर्नाटक के डी.के. शिवकुमार ने जन और नौकरशाहों से मुलाकात की, जबकि ऑफ जस्टिस और स`
- token IDs llama.cpp == HF: 13/13; HF decodes llama.cpp's IDs correctly: 13/13; HF round trip: 13/13
- ERROR in copy:
```
TypeError: 'str' object cannot be interpreted as an integer
Traceback (most recent call last):
  File "/home/runner/work/Hindi/Hindi/quality/token_check.py", line 36, in step
    fn()
  File "/home/runner/work/Hindi/Hindi/quality/token_check.py", line 89, in copy
    out = ctx["t"]._chat([{"role": "user", "content":
  File "/home/runner/work/Hindi/Hindi/hindi_core.py", line 398, in _chat
    for tok in self.model.generate(ids, temp=temperature, top_p=0.9, top_k=64, repeat_penalty=1.0, reset=True):
  File "/opt/hostedtoolcache/Python/3.10.21/x64/lib/python3.10/site-packages/llama_cpp/llama.py", line 977, in generate
    self.eval(tokens)
  File "/opt/hostedtoolcache/Python/3.10.21/x64/lib/python3.10/site-packages/llama_cpp/llama.py", line 677, in eval
    self._batch.set_batch(
  File "/opt/hostedtoolcache/Python/3.10.21/x64/lib/python3.10/site-packages/llama_cpp/_internals.py", line 511, in set_batch
    self.batch.token[i] = batch[i]
TypeError: 'str' object cannot be interpreted as an integer

```
- ERROR in rewrite:
```
TypeError: 'str' object cannot be interpreted as an integer
Traceback (most recent call last):
  File "/home/runner/work/Hindi/Hindi/quality/token_check.py", line 36, in step
    fn()
  File "/home/runner/work/Hindi/Hindi/quality/token_check.py", line 98, in rewrite
    w = ctx["t"].write_article(ART_T, ART_B)
  File "/home/runner/work/Hindi/Hindi/hindi_core.py", line 434, in write_article
    raw = self._chat(msgs, budget)
  File "/home/runner/work/Hindi/Hindi/hindi_core.py", line 398, in _chat
    for tok in self.model.generate(ids, temp=temperature, top_p=0.9, top_k=64, repeat_penalty=1.0, reset=True):
  File "/opt/hostedtoolcache/Python/3.10.21/x64/lib/python3.10/site-packages/llama_cpp/llama.py", line 977, in generate
    self.eval(tokens)
  File "/opt/hostedtoolcache/Python/3.10.21/x64/lib/python3.10/site-packages/llama_cpp/llama.py", line 677, in eval
    self._batch.set_batch(
  File "/opt/hostedtoolcache/Python/3.10.21/x64/lib/python3.10/site-packages/llama_cpp/_internals.py", line 511, in set_batch
    self.batch.token[i] = batch[i]
TypeError: 'str' object cannot be interpreted as an integer

```
