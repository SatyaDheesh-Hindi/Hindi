## Hindi missing-words diagnosis

| setup | llama-cpp-python | round trip whole/pieces (of 13) | copy exact | words lost in copy | rewrite has 'डिपार्टमेंट/विभाग' | 'और ऑफ जस्टिस' gap | s |
|---|---|---|---|---|---|---|---|
| A current (prebuilt wheel, unsloth Q4_K_M) | 0.3.35 | 11/11 | ❌ | डिपार्टमेंट, मुख्यमंत्री, प्रतिनिधियों, पदाधिकारियों, व्यापारियों | ❌ | ❌ yes | 216.4 |
| B latest llama.cpp from source, unsloth Q4_K_M | 0.3.35 | 11/11 | ❌ | डिपार्टमेंट, मुख्यमंत्री, प्रतिनिधियों, पदाधिकारियों, व्यापारियों | ❌ | ❌ yes | 175.5 |
| C prebuilt wheel, ggml-org Q4_K_M | 0.3.35 | 0/0 | ❌ | n/a | ❌ | ✅ no | - |
| D prebuilt wheel, google QAT q4_0 | 0.3.35 | 11/11 | ❌ | डिपार्टमेंट, मुख्यमंत्री, प्रतिनिधियों, पदाधिकारियों, व्यापारियों | ❌ | ❌ yes | 224.9 |

### A current (prebuilt wheel, unsloth Q4_K_M) — `unsloth/gemma-4-12b-it-GGUF/gemma-4-12b-it-Q4_K_M.gguf`
- copy output: कर्नाटक के डी.के. शिवकुमार ने जन और नौकरशाहों से मुलाकात की, जबकि ऑफ जस्टिस और सोसाइटी के ने से बात की।
- rewrite: **जज ने ट्रंप और DOJ के सेटलमेंट को रद्द किया, 1.8 बिलियन फंड को बताया गैरकानूनी** — सोमवार को अमेरिकी डिस्ट्रिक्ट जज कैथलीन विलियम्स ने डोनाल्ड ट्रंप और ऑफ जस्टिस (DOJ) के बीच हुए एक सेटलमेंट को रद्द कर दिया। जज ने इसे गैरकानूनी सेल्फ-डीलिंग बताया। ट्रंप ने टैक्स रिटर्न लीक होने पर IRS पर 10 बिलियन डॉलर का केस किया था। इसके बाद DOJ ने टैक्सपेयर्स से 1.8 बिलियन डॉलर निकालकर एक एंटी-वेपनिज़ेशन फंड में डालने पर सहमति जताई थी।
- round-trip fail: `मुख्यमंत्री` whole→`` pieces→``
- round-trip fail: `कर्नाटक के मुख्यमंत्री डी.के. शिवकुमार न` whole→`कर्नाटक के डी.के. शिवकुमार ने जन और नौकरशाहों से मुलाकात की, जबकि ऑफ जस्टिस और स` pieces→`कर्नाटक के डी.के. शिवकुमार ने जन और नौकरशाहों से मुलाकात की, जबकि ऑफ जस्टिस और स`

### B latest llama.cpp from source, unsloth Q4_K_M — `unsloth/gemma-4-12b-it-GGUF/gemma-4-12b-it-Q4_K_M.gguf`
- copy output: कर्नाटक के डी.के. शिवकुमार ने जन और नौकरशाहों से मुलाकात की, जबकि ऑफ जस्टिस और सोसाइटी के ने से बात की।
- rewrite: **जज ने ट्रंप और DOJ के सेटलमेंट को रद्द किया, 1.8 बिलियन फंड को बताया गैरकानूनी** — सोमवार को US डिस्ट्रिक्ट जज कैथलीन विलियम्स ने डोनाल्ड ट्रंप और ऑफ जस्टिस (DOJ) के बीच हुए एक सेटलमेंट को रद्द कर दिया। उन्होंने इसे गैरकानूनी सेल्फ-डीलिंग बताया। ट्रंप ने टैक्स रिटर्न लीक होने पर IRS पर 10 बिलियन डॉलर का केस किया था। इसके बाद DOJ ने टैक्सपेयर्स से 1.8 बिलियन डॉलर लेकर एक एंटी-वेपनिज़ेशन फंड में डालने पर सहमति जताई थी।
- round-trip fail: `मुख्यमंत्री` whole→`` pieces→``
- round-trip fail: `कर्नाटक के मुख्यमंत्री डी.के. शिवकुमार न` whole→`कर्नाटक के डी.के. शिवकुमार ने जन और नौकरशाहों से मुलाकात की, जबकि ऑफ जस्टिस और स` pieces→`कर्नाटक के डी.के. शिवकुमार ने जन और नौकरशाहों से मुलाकात की, जबकि ऑफ जस्टिस और स`

### C prebuilt wheel, ggml-org Q4_K_M — `ggml-org/gemma-4-12B-it-GGUF/gemma-4-12B-it-Q4_K_M.gguf`
- copy output: n/a
- rewrite: **n/a** — n/a
- ERROR in setup:
```
RemoteEntryNotFoundError: 404 Client Error. (Request ID: Root=1-6aba02ce-3e9cc4422f520bb222dd6b60;95ec1f98-974b-4558-b424-2a1a55dbff9e)

Entry Not Found for url: https://huggingface.co/ggml-org/gemma-4-12B-it-GGUF/resolve/main/gemma-4-12B-it-Q4_K_M.gguf.
ckages/huggingface_hub/file_download.py", line 1117, in _hf_hub_download_to_cache_dir
    (url_to_download, etag, commit_hash, expected_size, xet_file_data, head_call_error) = _get_metadata_or_catch_error(
  File "/opt/hostedtoolcache/Python/3.10.21/x64/lib/python3.10/site-packages/huggingface_hub/file_download.py", line 1762, in _get_metadata_or_catch_error
    metadata = get_hf_file_metadata(
  File "/opt/hostedtoolcache/Python/3.10.21/x64/lib/python3.10/site-packages/huggingface_hub/utils/_validators.py", line 89, in _inner_fn
    return fn(*args, **kwargs)
  File "/opt/hostedtoolcache/Python/3.10.21/x64/lib/python3.10/site-packages/huggingface_hub/file_download.py", line 1669, in get_hf_file_metadata
    response = _httpx2_follow_hub_redirects_with_backoff(
  File "/opt/hostedtoolcache/Python/3.10.21/x64/lib/python3.10/site-packages/huggingface_hub/utils/_http.py", line 761, in _httpx2_follow_hub_redirects_with_backoff
    hf_raise_for_status(response)
  File "/opt/hostedtoolcache/Python/3.10.21/x64/lib/python3.10/site-packages/huggingface_hub/utils/_http.py", line 856, in hf_raise_for_status
    raise _format(RemoteEntryNotFoundError, message, response, repo_type=repo_type, repo_id=repo_id) from e
huggingface_hub.errors.RemoteEntryNotFoundError: 404 Client Error. (Request ID: Root=1-6aba02ce-3e9cc4422f520bb222dd6b60;95ec1f98-974b-4558-b424-2a1a55dbff9e)

Entry Not Found for url: https://huggingface.co/ggml-org/gemma-4-12B-it-GGUF/resolve/main/gemma-4-12B-it-Q4_K_M.gguf.

```

### D prebuilt wheel, google QAT q4_0 — `google/gemma-4-12B-it-qat-q4_0-gguf/gemma-4-12b-it-qat-q4_0.gguf`
- copy output: कर्नाटक के डी.के. शिवकुमार ने जन और नौकरशाहों से मुलाकात की, जबकि ऑफ जस्टिस और सोसाइटी के ने से बात की।
- rewrite: **जज ने ट्रंप और DOJ के समझौते को किया रद्द, 1.8 बिलियन डॉलर के फंड को बताया अवैध** — सोमवार को US डिस्ट्रिक्ट जज कैथलीन विलियम्स ने डोनाल्ड ट्रंप और ऑफ जस्टिस (DOJ) के बीच हुए एक समझौते को रद्द कर दिया। उन्होंने इसे अवैध 'सेल्फ-डीलिंग' करार दिया। ट्रंप ने टैक्स रिटर्न लीक होने पर IRS पर 10 बिलियन डॉलर का केस किया था। इसके बाद DOJ ने टैक्सपेयर्स से 1.8 बिलियन डॉलर निकालकर एक एंटी-वेपनिज़ेशन फंड में देने परसहमति जताई थी।
- round-trip fail: `मुख्यमंत्री` whole→`` pieces→``
- round-trip fail: `कर्नाटक के मुख्यमंत्री डी.के. शिवकुमार न` whole→`कर्नाटक के डी.के. शिवकुमार ने जन और नौकरशाहों से मुलाकात की, जबकि ऑफ जस्टिस और स` pieces→`कर्नाटक के डी.के. शिवकुमार ने जन और नौकरशाहों से मुलाकात की, जबकि ऑफ जस्टिस और स`
