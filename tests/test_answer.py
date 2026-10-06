"""Answering: the critic keeps verified citations, drops invented ones, removes unsupported
sentences, and refuses rather than answer without evidence. Fake LLM; real DB and search."""

import pytest

from earshot.answer import REFUSAL, answer, keep_supported_sentences
from earshot.embed import embed_pending
from earshot.index import index_new_episodes
from earshot.llm import ChatResult
from search_helpers import TALK, HashEmbedder, OverlapReranker, add_transcript


@pytest.fixture
def corpus(db, make_episode):
    add_transcript(db, make_episode, TALK)
    index_new_episodes(db)
    embed_pending(db, HashEmbedder())


def llm_returning(data):
    calls = []

    def fake(messages, model, max_tokens):
        calls.append(messages)
        return ChatResult(data, 900, 100)

    fake.calls = calls
    return fake


def ask(db, data, question="what do agents need to plan tasks"):
    return answer(db, question, embedder=HashEmbedder(), reranker=OverlapReranker(), llm=llm_returning(data))


def test_verified_citation_is_kept_with_the_exact_quote_time(db, corpus):
    # TALK[1] starts at 60 s; "agents" is its 5th word (index 4) -> 60 + 4 * 0.4 = 61.6 s
    result = ask(db, {"found": True, "answer": "The guest says agents need long term memory [1].",
                      "citations": [{"n": 1, "quote": "agents need long term memory to plan"}]})
    assert result.found and result.text == "The guest says agents need long term memory [1]."
    [c] = result.citations
    assert c.time == pytest.approx(61.6, abs=0.01)
    assert c.title and c.audio_url
    assert result.tokens == 1000


def test_invented_quote_is_rejected_and_its_sentence_removed(db, corpus):
    result = ask(db, {"found": True,
                      "answer": "Agents need memory [1]. They also need a cat [2].",
                      "citations": [{"n": 1, "quote": "agents need long term memory to plan"},
                                    {"n": 2, "quote": "agents also need a pet cat for morale"}]})
    assert result.text == "Agents need memory [1]."
    assert result.removed_sentences == 1
    assert result.rejected[0]["reason"] == "quote not found verbatim in passage"


def test_verbatim_question_is_not_accepted_as_evidence(db, corpus):
    """Regression (real failure): the model cited the host's question, which is verbatim
    but supports nothing."""
    result = ask(db, {"found": True, "answer": "Agents need memory [1].",
                      "citations": [{"n": 1, "quote": "why agents need long term memory?"}]})
    assert result.found is False
    assert result.rejected[0]["reason"] == "quote is a question, not a statement"


def test_answer_with_no_verified_citation_becomes_a_refusal(db, corpus):
    result = ask(db, {"found": True, "answer": "Agents dream of sheep [1].",
                      "citations": [{"n": 1, "quote": "agents dream of electric sheep every night"}]})
    assert (result.found, result.text, result.citations) == (False, REFUSAL, [])


def test_citation_to_a_passage_that_was_not_given_is_rejected(db, corpus):
    result = ask(db, {"found": True, "answer": "Something [9].", "citations": [{"n": 9, "quote": "x y z w v"}]})
    assert result.found is False
    assert result.rejected[0]["reason"] == "no such passage"


def test_model_saying_not_found_is_a_refusal(db, corpus):
    result = ask(db, {"found": False, "answer": "", "citations": []})
    assert (result.found, result.text) == (False, REFUSAL)


def test_malformed_citations_do_not_crash(db, corpus):
    result = ask(db, {"found": True, "answer": "Claim [1].", "citations": "not a list"})
    assert result.found is False


def test_prompt_contains_numbered_passages_with_titles_and_times(db, corpus):
    fake = llm_returning({"found": False})
    answer(db, "memory for agents", embedder=HashEmbedder(), reranker=OverlapReranker(), llm=fake)
    user = fake.calls[0][1]["content"]
    assert "Question: memory for agents" in user
    assert '[1] Episode: "' in user and "(from " in user


def test_no_passages_means_refusal_without_calling_the_llm(db):
    fake = llm_returning({"found": True})
    result = answer(db, "anything", embedder=HashEmbedder(), reranker=OverlapReranker(), llm=fake)
    assert result.text == REFUSAL and fake.calls == []


def llm_sequence(*datas):
    """A fake LLM that answers with each data in turn (first answer, then the repair)."""
    calls = []

    def fake(messages, model, max_tokens):
        calls.append(messages)
        return ChatResult(datas[len(calls) - 1], 900, 100)

    fake.calls = calls
    return fake


BAD_QUOTE = {"found": True, "answer": "Agents need memory [1].",
             "citations": [{"n": 1, "quote": "agents really do need memory?"}]}
GOOD_QUOTE = {"found": True, "answer": "Agents need memory [1].",
              "citations": [{"n": 1, "quote": "agents need long term memory to plan"}]}


def test_repair_pass_recovers_an_answer_whose_quotes_failed(db, corpus):
    fake = llm_sequence(BAD_QUOTE, GOOD_QUOTE)
    result = answer(db, "what do agents need", embedder=HashEmbedder(), reranker=OverlapReranker(), llm=fake)
    assert result.found and result.repaired
    assert len(fake.calls) == 2
    assert "failed verification" in fake.calls[1][-1]["content"]  # told what failed and why
    assert result.tokens == 2000  # both calls counted


def test_repair_is_verified_too_so_it_cannot_sneak_in_a_fake_quote(db, corpus):
    still_fake = {"found": True, "answer": "Agents need memory [1].",
                  "citations": [{"n": 1, "quote": "agents need a nap after lunch every day"}]}
    result = answer(db, "what do agents need", embedder=HashEmbedder(), reranker=OverlapReranker(),
                    llm=llm_sequence(BAD_QUOTE, still_fake))
    assert result.found is False and result.repaired is False


def test_no_repair_call_when_all_citations_verify(db, corpus):
    fake = llm_sequence(GOOD_QUOTE)
    result = answer(db, "what do agents need", embedder=HashEmbedder(), reranker=OverlapReranker(), llm=fake)
    assert result.found and not result.repaired and len(fake.calls) == 1


@pytest.mark.parametrize("text, ok, expected", [
    ("A [1]. B [2].", {1}, "A [1]."),
    ("A [1][2].", {2}, "A [2]."),
    ("A without a marker. B [1].", {1}, "B [1]."),
])
def test_keep_supported_sentences(text, ok, expected):
    assert keep_supported_sentences(text, ok)[0] == expected
