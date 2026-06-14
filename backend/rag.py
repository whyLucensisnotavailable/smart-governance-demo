"""政务问答 RAG：政策文档切片 + 关键词检索 + DeepSeek 生成，回答标注依据来源。

无 key 时命中内置 fallback_qa 预设回答；检索逻辑（切片打分）始终真实运行。
"""
import json
import os
import pathlib
import re

import httpx

DATA_DIR = pathlib.Path(__file__).parent / "data"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"


def _load_corpus() -> dict:
    with open(DATA_DIR / "policies.json", encoding="utf-8") as f:
        return json.load(f)


CORPUS = _load_corpus()

# chunk_id -> (doc 标题, 来源, 文本)
CHUNK_INDEX: dict[str, dict] = {}
for doc in CORPUS["documents"]:
    for ch in doc["chunks"]:
        CHUNK_INDEX[ch["chunk_id"]] = {
            "chunk_id": ch["chunk_id"],
            "doc_title": doc["title"],
            "source": doc["source"],
            "text": ch["text"],
        }


def _tokenize(text: str) -> list[str]:
    # 中文按 2-gram 切，兼顾英文单词
    text = re.sub(r"[^\w一-鿿]", "", text)
    grams = [text[i:i + 2] for i in range(len(text) - 1)]
    return grams or [text]


def retrieve(query: str, top_k: int = 4) -> list[dict]:
    """关键词 2-gram 重叠打分检索，返回最相关的政策切片。"""
    q_grams = set(_tokenize(query))
    scored = []
    for ch in CHUNK_INDEX.values():
        c_grams = set(_tokenize(ch["text"]))
        overlap = len(q_grams & c_grams)
        if overlap > 0:
            scored.append((overlap / (len(q_grams) ** 0.5), ch))
    scored.sort(key=lambda x: -x[0])
    return [ch for _, ch in scored[:top_k]]


def _fallback_answer(query: str) -> dict | None:
    best, best_hits = None, 0
    for qa in CORPUS["fallback_qa"]:
        hits = sum(1 for kw in qa["keywords"] if kw in query)
        if hits > best_hits:
            best, best_hits = qa, hits
    if best is None:
        return None
    return {
        "answer": best["answer"],
        "sources": [CHUNK_INDEX[cid] for cid in best["sources"]],
        "mode": "内置预设（未配置 DeepSeek Key）",
    }


async def answer(query: str) -> dict:
    chunks = retrieve(query)
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()

    if not key:
        fb = _fallback_answer(query)
        if fb:
            fb["retrieved"] = chunks
            return fb
        return {
            "answer": "当前知识库未覆盖该问题，已为您转人工服务（12345热线）。配置 DeepSeek Key 后可回答更多问题。",
            "sources": chunks,
            "retrieved": chunks,
            "mode": "内置预设（未配置 DeepSeek Key）",
        }

    context = "\n\n".join(f"[{c['chunk_id']}] 《{c['doc_title']}》：{c['text']}" for c in chunks)
    messages = [
        {
            "role": "system",
            "content": (
                "你是政务服务智能助手。仅依据提供的政策切片回答问题，"
                "在句末用 [chunk_id] 标注依据来源；切片不足以回答时明确说明并建议转人工，"
                "不得编造政策内容。回答控制在200字以内。"
            ),
        },
        {"role": "user", "content": f"## 政策依据\n{context}\n\n## 市民问题\n{query}"},
    ]
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                DEEPSEEK_URL,
                json={"model": MODEL, "messages": messages, "temperature": 0.2, "max_tokens": 400},
                headers={"Authorization": f"Bearer {key}"},
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
        return {"answer": text, "sources": chunks, "retrieved": chunks, "mode": "DeepSeek 生成"}
    except Exception as e:
        fb = _fallback_answer(query)
        if fb:
            fb["mode"] = f"DeepSeek 调用失败（{type(e).__name__}），已降级为内置预设"
            fb["retrieved"] = chunks
            return fb
        return {
            "answer": f"DeepSeek 调用失败（{type(e).__name__}），且知识库未覆盖该问题，请转人工服务。",
            "sources": chunks,
            "retrieved": chunks,
            "mode": "降级",
        }
