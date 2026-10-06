"""Command line: `earshot ingest <feed_url>`, `earshot worker`, `earshot status`, `earshot eval ...`."""

import argparse
import logging

from earshot.db import apply_schema, connect
from earshot.ingest import ingest_feed
from earshot.transcribe import DEFAULT_MODEL
from earshot.worker import default_worker_id, run_worker


def cmd_ingest(conn, args) -> None:
    result = ingest_feed(conn, args.feed_url, limit=args.limit)
    print(f"{result.new} new episode(s) queued for transcription, {result.skipped} already known.")


def cmd_worker(conn, args) -> None:
    try:
        run_worker(conn, args.id or default_worker_id(), once=args.once, until_empty=args.until_empty)
    except KeyboardInterrupt:
        print("worker stopped")


def cmd_eval_librispeech(args) -> None:
    from earshot.evals import librispeech  # dev-only dependencies, imported on demand

    result = librispeech.run(args.model)
    path = librispeech.save(result)
    print(f"{result['model']}: WER {result['wer']:.2%} on {result['ref_words']} words "
          f"(S={result['substitutions']} D={result['deletions']} I={result['insertions']}), "
          f"correct words inside their utterance {result['timestamps']['inside_true_utterance_rate']:.2%} "
          f"(p95 {result['timestamps']['p95_seconds_outside']:.2f}s, max {result['timestamps']['max_seconds_outside']:.2f}s outside), "
          f"{result['audio_seconds']:.0f}s audio in {result['seconds_elapsed']:.0f}s -> {path}")


def cmd_eval_signals(conn, args) -> None:
    from earshot.evals.signals import summarize
    from earshot.pipeline import get_transcript

    result = summarize(get_transcript(conn, args.episode_id))
    print(f"episode {args.episode_id}: {result['words']} words | compressed runs {result['compressed_run']} | "
          f"backward jumps {result['backward_jump']} | repeated phrases {result['repeated_phrase']} | "
          f"{result['suspects_per_hour']} suspects/hour")
    for s in result["suspects"]:
        print(f"  {s['at']:>7}  {s['kind']:16} ...{s['snippet']}...")


def cmd_eval_podcast_prepare(conn, args) -> None:
    from earshot.evals import podcast

    row = conn.execute("SELECT audio_url FROM episodes WHERE id = %s", (args.episode_id,)).fetchone()
    if row is None:
        raise SystemExit(f"no episode with id {args.episode_id}")
    info = podcast.prepare(args.name, row[0], minutes=args.minutes, local_model=args.local_model)
    print(f"clip: {info['clip_seconds']:.0f}s in {info['folder']}")
    for model, seconds in info["seconds_elapsed"].items():
        print(f"  {model}: transcribed in {seconds:.0f}s")
    state = "created - correct it while listening to clip.flac" if info["reference_created"] else "kept (your edits are safe)"
    print(f"reference.txt {state}")


def cmd_eval_podcast(args) -> None:
    from earshot.evals import podcast

    for r in podcast.score(args.name):
        flag = (f"  [WARNING: reference differs from the draft by only {r['reference_edits_from_draft']} words; "
                "scores measure agreement with the draft model, not accuracy]") if r["reference_is_uncorrected_draft"] else ""
        print(f"{r['model']}: WER {r['wer']:.2%} on {r['ref_words']} words "
              f"(S={r['substitutions']} D={r['deletions']} I={r['insertions']}), "
              f"{r['speed_x_realtime']}x real time{flag}")


def cmd_index(conn, args) -> None:
    from earshot.embed import Embedder, embed_pending
    from earshot.index import index_new_episodes

    built = index_new_episodes(conn)
    print(f"passages built for {len(built)} episode(s): {sum(built.values())} passages")
    embedded = embed_pending(conn, Embedder())
    print(f"embedded {embedded} passage(s)")


def _clock(seconds: float) -> str:
    return f"{int(seconds // 3600)}:{int(seconds % 3600 // 60):02d}:{int(seconds % 60):02d}" if seconds >= 3600         else f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def cmd_search(conn, args) -> None:
    from earshot.embed import Embedder
    from earshot.search import search

    hits = search(conn, args.query, embedder=Embedder(), mode=args.mode, k=args.k)
    if not hits:
        print("no results")
    for rank, h in enumerate(hits, 1):
        print(f"{rank}. [{_clock(h.start)}] {h.title[:70]}  (score {h.score:.3f})")
        print(f"   {h.text[:220]}...")


def cmd_status(conn, args) -> None:
    episodes = conn.execute("SELECT count(*) FROM episodes").fetchone()[0]
    print(f"episodes: {episodes}")
    passages, embedded = conn.execute(
        "SELECT count(*), count(embedding) FROM passages").fetchone()
    print(f"passages: {passages} ({embedded} embedded)")
    for status, count in conn.execute(
        "SELECT status, count(*) FROM jobs GROUP BY status ORDER BY status"
    ):
        print(f"jobs {status}: {count}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="earshot", description="Ask podcasts questions.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="fetch an RSS feed and queue new episodes")
    p_ingest.add_argument("feed_url")
    p_ingest.add_argument("--limit", type=int, default=20, help="newest N episodes (default 20)")
    p_ingest.set_defaults(func=cmd_ingest)

    p_worker = sub.add_parser("worker", help="transcribe queued episodes (Ctrl+C to stop)")
    p_worker.add_argument("--once", action="store_true", help="process at most one job, then exit")
    p_worker.add_argument("--until-empty", action="store_true",
                          help="exit when no work is left (waits out rate-limit deferrals)")
    p_worker.add_argument("--id", help="worker name (default: hostname-pid)")
    p_worker.set_defaults(func=cmd_worker)

    p_status = sub.add_parser("status", help="show episode and job counts")
    p_status.set_defaults(func=cmd_status)

    p_index = sub.add_parser("index", help="build passages + embeddings for transcribed episodes")
    p_index.set_defaults(func=cmd_index)

    p_search = sub.add_parser("search", help="search transcripts; prints timestamped hits")
    p_search.add_argument("query")
    p_search.add_argument("--mode", default="hybrid+rerank",
                          choices=["keyword", "vector", "hybrid", "hybrid+rerank"])
    p_search.add_argument("-k", type=int, default=5)
    p_search.set_defaults(func=cmd_search)

    p_eval = sub.add_parser("eval", help="quality evaluations (dev dependencies)")
    eval_sub = p_eval.add_subparsers(dest="eval_name", required=True)
    p_libri = eval_sub.add_parser("librispeech", help="WER + timestamp accuracy on LibriSpeech")
    p_libri.add_argument("--model", default=DEFAULT_MODEL)
    p_libri.set_defaults(func=cmd_eval_librispeech, needs_db=False)
    p_signals = eval_sub.add_parser("signals", help="hallucination signals for a transcribed episode")
    p_signals.add_argument("episode_id", type=int)
    p_signals.set_defaults(func=cmd_eval_signals)
    p_prep = eval_sub.add_parser("podcast-prepare", help="cut a fixed clip + transcribe it with each model")
    p_prep.add_argument("name")
    p_prep.add_argument("--episode-id", type=int, required=True)
    p_prep.add_argument("--minutes", type=float, default=10)
    p_prep.add_argument("--local-model", default="small.en",
                        help="neutral draft model; use base.en if RAM is tight (small.en commits ~2.5 GB)")
    p_prep.set_defaults(func=cmd_eval_podcast_prepare)
    p_pod = eval_sub.add_parser("podcast", help="WER of each model vs your corrected reference")
    p_pod.add_argument("name")
    p_pod.set_defaults(func=cmd_eval_podcast, needs_db=False)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx logs full request URLs at INFO; feed/audio URLs can carry tracking IDs
    # or access tokens (private feeds), so keep them out of the logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not getattr(args, "needs_db", True):
        args.func(args)
        return
    with connect(autocommit=True) as conn:
        apply_schema(conn)  # idempotent, so safe on every run
        args.func(conn, args)
